# CLAUDE.md

Operating rules for all work on Vouchcode. These persist across sessions and context
resets. Read this file in full before writing or modifying any code in this repository.

## Project

Vouchcode is a local-first CLI tool that cryptographically tracks AI versus human
authorship at the commit level and requires the committing developer to demonstrate
understanding of AI-generated code before it is sealed into a tamper-evident provenance
ledger.

The authoritative specification is `Documentation/Vouchcode_Research_Documentation.docx`.
Its system architecture, methodology (Section 4), and phase breakdown (Section 5) govern
this implementation. When this file and the research document disagree on a technical
detail, the research document wins and this file should be corrected.

Built for the AI Builders Hackathon 2026 (submission deadline 15 September 2026), and
serving as a portfolio and research artifact. Treat it as a real product. Every module
should be defensible line by line to a judge or a technical interviewer.

## Rule 1: No emojis anywhere

Not in code, comments, docstrings, commit messages, CLI output, terminal prompts, test
names, README, or any documentation file. This is a professional engineering artifact.

## Rule 2: No em dashes anywhere

Not in code comments, CLI output, README, or documentation. Use commas, periods, colons,
or parentheses instead. Check output for em dashes before writing it to a file.

## Rule 3: CLI tone is precise and technical, never cute

Terminal output should read like `git`, `cargo`, or `pytest`, not like a friendly
assistant. No "Oops!", no "All done!", no exclamation marks in output strings. State what
happened, state what failed, state what to do next. Nothing else.

Acceptable: `hook installed: .git/hooks/pre-commit`

Not acceptable: `Great! Your hooks are all set up!`

## Rule 4: No external AI API calls anywhere in Vouchcode's own logic

Comprehension question generation, AST analysis, and scoring must be deterministic and
local, using Python's `ast` module and rule-based extraction. This is a core architectural
constraint from the research document (Section 3.1, "zero external model dependency"), not
a preference. It is what makes the output reproducible and the privacy claim defensible.

If implementing a feature creates pressure to call an LLM API to "make the questions
better", stop and raise it with the user instead of implementing it.

The one exception is Claude Code being used as the development tool. That is separate from
Vouchcode's runtime behavior and must never appear in Vouchcode's dependency list or
execution path.

## Rule 5: Git authorship is Sudais Khalid, with zero AI attribution of any kind

- Before the first commit, run `git config user.name "Sudais Khalid"` and
  `git config user.email "msudaiskhalid.ai@gmail.com"`, and confirm both are set.
- Do not use any commit message format that appends tool attribution. No
  `Co-Authored-By: Claude`, no `Generated with Claude Code`, no equivalent trailer.
- After every commit, run `git log -1 --format=full` and confirm the author line, the
  committer line, and the message body contain no trace of AI attribution before
  considering the task done.
- Commit messages: short, imperative mood, describing what changed and why, scoped to the
  module being worked on. Example: `Add AST-based hunk segmentation for Python diffs`.
- One logical change per commit. Do not bundle unrelated modules into a single commit.

## Rule 6: Use /find-skill before touching unfamiliar libraries

Specifically for the `cryptography` package's Ed25519 signing, GitPython hook internals,
Typer and Rich CLI patterns, and PDF generation with WeasyPrint or ReportLab. Prefer a
verified skill over reconstructing library usage from memory, especially for anything
touching cryptographic signing, where a subtly wrong pattern is expensive to discover
later.

If `/find-skill` returns nothing relevant, say so explicitly and proceed carefully,
calling out any assumption about library behavior that was not verified.

## Rule 7: This file is written before implementation code

CLAUDE.md exists so these rules survive context resets. Keep it current. If a rule changes
or a new constraint is agreed with the user, update this file in the same commit as the
change it governs.

## Rule 8: Phase discipline

Build in the order defined in Section 5 of the research document. Do not create or
implement a later phase's files until the current phase's exit criteria are met and its
tests pass. Scaffold each phase's modules as stubs with clear docstrings before
implementing logic inside them.

| Phase | Focus | Deliverable |
| --- | --- | --- |
| 1 | Foundation | CLI skeleton, hook installation, raw attribution capture into a local JSON ledger. |
| 2 | Segmentation | AST-based diff segmentation and hunk-level attribution tagging. |
| 3 | Comprehension Engine | Deterministic question generation and terminal-based scoring against AST-derived facts. |
| 4 | Provenance Ledger | Hash-chained, Ed25519-signed ledger with tamper detection on verification. |
| 5 | Reporting | Signed JSON and PDF report generation, plus retroactive repository scan mode. |
| 6 | Evaluation and Demonstration | Self-application to an existing repository, demonstration recording, documentation finalization. |

Agreed phase boundary clarifications:

- Phase 1 records commit hash, timestamp, and changed file list, with the attribution
  field set to `"unclassified"`. No stylometry, no AST parsing, no direct-signal
  detection. Phase 1's exit criterion is narrower than full attribution: it proves only
  that the hook fires reliably and the ledger write succeeds on every commit. Introducing
  partial attribution logic in Phase 1 would blur that exit criterion and produce
  throwaway detection code that Phase 2 must replace rather than extend.
- Phase 2 must be tested against a renamed function specifically. Naive AST diffing
  misattributes a rename as a full rewrite, and that is the case most likely to be wrong
  on a first implementation. Two strengths are required, not one: a pure rename, and a
  rename combined with an internal variable rename. The second is where naive approaches
  actually break, in both directions, by reporting either a full rewrite or no change.

Recorded segmentation decisions, so they are not relitigated as bugs:

- Attribution errs toward over-reporting change. A missed rename degrades to
  add-plus-remove, which asks a developer to re-account for code they already understood.
  A false rename links unrelated definitions and lets new code inherit old provenance.
  For a tool whose output is evidence, over-reporting is the tolerable failure, and both
  thresholds in `astdiff.py` are set with that direction in mind.
- `RENAME_SIMILARITY_THRESHOLD` and `MIN_NODES_FOR_SIMILARITY` come from measurement, not
  intuition. Single-expression functions all measure 9 tokens and score 0.78 to 0.89
  against each other regardless of behavior; real functions measure 24 or more and two
  genuinely different ones scored 0.64. Re-measure before changing either value.
- Alpha-renaming covers only names a definition binds. Attribute names, called globals,
  and constant values are never normalized, because calling a different method or
  returning a different constant is a real behavioral change.
- The stylometric path must never return a bare verdict. Confidence is always present,
  capped below certainty, and the path declines outright when the baseline or the hunk is
  too small. It measures divergence from a developer's prior style; it does not detect
  AI, and no claim that it does may be added.

Stop and report to the user at the end of each phase. Do not begin the next phase
automatically.

Recorded comprehension decisions, so they are not relitigated as bugs:

- Merge commits carry `comprehension.status = "excluded_merge"` with a rationale, never a
  null field. Every entry states a comprehension status, including the ones never
  evaluated, because a null reads as an oversight and an explicit status reads as a
  decision.
- A commit made with no terminal is recorded as `skipped_non_interactive`, never as
  passed. A skip does not block the commit; only an actual failure does. Refusing every
  scripted commit would push developers to disable Vouchcode, which protects nothing.
- Answers that cannot be collected are not answers that were wrong. `isatty` is not a
  reliable interactivity check: a hook can inherit a stdin that reports as a terminal and
  then returns end of file immediately. Scoring the resulting empty answers as failures
  refused commits for the tool's blind spot, and the read failing is now the signal.
- A hunk passes only if every question passes. Averaging would let a developer answer the
  easiest question and guess the rest.
- Scoring must separate three cases against the same question: correct, keyword-stuffed
  shallow, and confidently wrong. A stuffed answer usually matches more of the code's
  terms than a correct one, so term overlap alone can never be the grade. Function word
  density alone is not enough either: padding a keyword list with articles defeated it.
  The composite requires a linking word and vocabulary of the answer's own as well.

Recorded ledger and signing decisions, so they are not relitigated as bugs:

- No passphrase on the private key, for this version. A passphrase means prompting on
  every commit or caching the decrypted key, and the second defeats the point while the
  first makes committing intolerable. Section 6.1 already places a compromised local
  machine out of scope, so a passphrase would defend against a threat the model does not
  cover. Revisit only alongside a real agent or keychain integration.
- The private key is written owner read and write only. On Windows `chmod` is a
  documented no-op, not a silent one: the key inherits directory permissions, which is
  weaker, and the test skips rather than asserting loosely so that a pass never implies a
  protection the file does not have.
- Key rotation is out of scope for this version. One key per repository, for its
  lifetime. Rotation raises questions this version does not answer, and answering them
  badly is worse than not offering the feature.
- `ensure_keypair` is idempotent and must stay that way. Regenerating a key on an
  initialized repository would orphan every existing signature, turning a rerun of `init`
  into silent destruction of the provenance record.
- Chain links compare against the predecessor's **recomputed** hash, never its stored
  one. Comparing stored hashes would confine a tamper report to the edited entry and
  leave every following entry looking intact, which defeats the property a hash chain
  exists to provide.
- Signing is best effort at append time. A missing key yields an unsigned entry that
  verification reports as `unsigned`, rather than losing the entry entirely.
- Fingerprints are never stored without a `fingerprint_version` tag recording the
  algorithm, the interpreter's major and minor version, and an `ast_signature` probe
  hash. A missing tag is treated as non-comparable, never as comparable, because an entry
  that makes no claim about its conditions supports no conclusion.
- `unverifiable_version` is its own verification category and must not be folded into
  either `verified` or `tampered`. It exits zero, because the ledger is sound and only
  its fingerprints are non-comparable; exiting non-zero would train readers to ignore it.

Recorded reporting decisions, so they are not relitigated as bugs:

- Every report displays the signing key fingerprint prominently and states, in the
  artifact itself, that a valid signature proves the document is unaltered and does not
  prove who signed it. The notice travels with the report because a verifier reads the
  PDF, not this repository.
- The fingerprint is a real check and a limited one. Do not describe it as solving key
  substitution. A forged report carries a forged key and a fingerprint matching it
  perfectly; the check only works when the verifier obtained a copy of the fingerprint
  independently of the report.
- Fingerprint comparison ignores spacing and case. Failing a verifier over whitespace
  would teach them the check is unreliable rather than that the key is wrong.
- Authorship percentages exclude hunks the AST proved unchanged. Counting a large rename
  in the denominator would dilute the AI share of a mostly generated commit.
- The comprehension pass rate covers evaluated commits only. Merges, skips, and
  retroactive entries are reported by status and never counted as passes. A rate that
  silently counted unevaluated commits as passes would be the most misleading number this
  tool could produce, and an absent rate is reported as absent rather than as zero.
- Retroactive scan entries are marked `capture: retroactive_scan`, use the stylometric
  path only, and never carry a comprehension outcome. No tool signal survives for a commit
  made before the adapter existed, and a developer cannot be meaningfully quizzed months
  later on code sitting in front of them. Scanning never overwrites a live capture.
- The JSON and the PDF render one shared summary module, so the two formats cannot drift
  into reporting different numbers for the same range.

## Rule 9: Tests alongside every feature

Each phase needs at least one test that proves its exit criterion, not merely that the
code runs without raising. A test asserting that no exception was thrown does not prove
an exit criterion.

Example: Phase 2's segmentation must be tested against a diff with a known function
boundary and assert that the hunk splits exactly where expected.

## Rule 10: Deployment setup follows /vibe-ship

Containerization, CI, and deployment configuration are produced through the `/vibe-ship`
skill rather than hand-rolled, so the resulting setup is complete and internally
consistent rather than partial.

## Architecture reference

Five cooperating layers, per Section 3.2 of the research document.

1. Capture: git hooks (pre-commit, post-commit) installed at repository initialization
   detect commit events and, where available, associated AI tool session signals. Absent a
   direct signal, a stylometric heuristic compares naming entropy, comment density, and
   structural regularity against a baseline from the developer's prior commits. Heuristic
   results are always reported with an explicit confidence level, never as a binary
   classification.
2. Segmentation: each captured diff is parsed into an abstract syntax tree and segmented
   into function-level or block-level hunks rather than raw line ranges, so attribution
   aligns with logical units of code.
3. Comprehension: hunks attributed to AI generation are passed to a question generation
   engine that derives targeted, structurally grounded questions from the hunk's control
   flow, and scores terminal-entered responses against facts extracted from the AST.
   Scoring matches key structural terms against extracted facts. It does not use semantic
   similarity to a model-generated reference answer.
4. Ledger: the outcome of each commit, comprising attribution percentages and
   comprehension scores, is appended to a hash-chained, Ed25519-signed local ledger,
   rendering the history tamper-evident.

   Recorded ledger decisions, so they are not relitigated as bugs:

   - `git commit --amend` produces two entries by design, one for the original hash and
     one for the amended hash. An amend creates a new commit rather than modifying one,
     and the original becomes unreachable from any ref. Both entries remain, because
     removing the superseded one would mean deleting from an append-only ledger, which
     is the exact operation Phase 4's hash chain exists to make detectable, and it would
     erase the evidence that an amendment happened. Expect ledger entries for commits
     that git history no longer reaches.
   - A merge commit is recorded with type `merge` and `files` set to null, never to an
     empty list. Null states that no file list was computed; empty would state that the
     merge touched nothing. Computing one would double count content the side branch's
     own entries already record, and deciding whether conflict resolution is authored
     work is a Phase 2 attribution question.
   - Merge classification is by parent count (two or more), not by which hook observed
     the commit, so a hand-resolved merge finished with `git commit` and an automatic
     one from `git merge` are recorded identically.
5. Reporting: on request, the ledger is compiled into a signed JSON artifact and a
   human-readable PDF summary, both independently verifiable offline. Both embed the
   public key required to verify signatures, so a recipient needs neither a Vouchcode
   install nor an account.

## Technical constraints

- Target language for analysis is Python, using the stdlib `ast` module. JavaScript and
  TypeScript via tree-sitter are future work, not initial scope.
- All analysis, signing, and storage occur on the developer's machine. No source code and
  no provenance data is transmitted to a third-party service.
- Vouchcode state lives in `.vouchcode/` at the repository root.
- Installation and initialization reduce to a single command, with no account creation.

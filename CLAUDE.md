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
  on a first implementation.

Stop and report to the user at the end of each phase. Do not begin the next phase
automatically.

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

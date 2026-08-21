# Vouchcode

![Vouchcode](badge.svg)

*This badge is generated locally by running `vouchcode badge` against this repository's
own ledger. It is a self-reported summary, not a certification issued by any service. It
shows the AI-attributed share of changed logic and the comprehension pass rate, says
"comprehension not evaluated" when nothing was evaluated rather than implying otherwise,
and carries its generation date in the SVG title attribute so a stale badge can be dated.
Regenerate it by running the command again. It is not a security guarantee.*

Local-first cryptographic provenance and comprehension verification for AI-assisted
development. Built by [Muhammad Sudais Khalid](https://sudaiskhalid.com).

Vouchcode records which parts of each commit were AI-generated, requires the committing
developer to demonstrate they understand that code before it is sealed, and appends the
result to a hash-chained, signed ledger. Everything runs on the developer's machine. It
makes no network call and consults no language model.

## The problem it addresses

Every AI-provenance tool available today answers one question: who or what wrote this
line. None answer the one that matters for accountability: does the person committing it
understand it?

A developer can accept a generated function without reading it, commit it under their own
name, and pass every attribution check in existence, because attribution checks verify
authorship of text, not comprehension of logic.

**Attribution is necessary but not sufficient. Verifiable comprehension is the missing
second condition.** That is the whole thesis, and the comprehension layer is the part of
this project that does not exist elsewhere.

## Getting started

Follow these six steps in order. Each shows the command and what you should see.

### 1. Check your prerequisites

You need Python 3.10 or newer, and git.

```sh
python --version
git --version
```

```
Python 3.12.3
git version 2.43.0
```

If `python` is not found, try `python3`. If your Python is older than 3.10, install a
newer one before continuing; Vouchcode uses syntax that older versions cannot parse.

### 2. Install Vouchcode

Clone this repository, then install it in editable mode.

```sh
git clone https://github.com/sudais-khalid/VOUCHCODE.git
cd VOUCHCODE
pip install -e ".[dev]"
```

```
Successfully installed vouchcode-0.1.0
```

Confirm it is on your path:

```sh
vouchcode about
```

```
Vouchcode 0.1.0
Local-first cryptographic provenance and comprehension verification for AI-assisted development
author: Muhammad Sudais Khalid (https://sudaiskhalid.com)
repository: https://github.com/sudais-khalid/VOUCHCODE
```

### 3. Initialize a repository

Change into any git repository you want to track, then initialize.

```sh
cd /path/to/your-repository
vouchcode init
```

```
hook installed: .git/hooks/pre-commit
hook installed: .git/hooks/post-commit
hook installed: .git/hooks/post-merge
ledger ready: .vouchcode/ledger.json
initialized: /path/to/your-repository
```

This installs three git hooks, creates the ledger, and generates a local Ed25519 signing
key. Everything lives in `.vouchcode/`, which you should add to your `.gitignore`. Running
`init` again is safe and never replaces an existing key.

### 4. Make a commit

Commit as you normally would.

```sh
git add .
git commit -m "Add record normalizer"
```

If the commit contains code attributed to AI generation, Vouchcode shows you that code and
asks you to account for it before the commit completes:

```
+------------------- records.py: normalize_records -------------------+
| def normalize_records(records, strict):                             |
|     if records is None:                                             |
|         raise ValueError("records required")                        |
|     for key, value in records.items():                              |
|         if value is None and strict:                                |
|             return None                                             |
+-------- attributed ai via tool_signal, confidence 1.00 -------------+

question 1 of 3: When records is None, what does this code do and why?
answer: raises ValueError because it cannot work without records
  correct, score 0.94: answer relates the condition to the outcome the code produces
```

Answer poorly and the commit is refused. `git commit --no-verify` records it as
explicitly unverified rather than blocking you. A commit made with no terminal attached,
such as from a script, is recorded as skipped and never as passed.

Check that the ledger recorded it:

```sh
vouchcode log --limit 3
```

```
commit      type    timestamp                  attribution   files  author
4edeb29b7f  commit  2026-08-20T08:15:48+00:00  mixed             1  Sudais Khalid
```

### 5. Generate a report

```sh
vouchcode report -o out
```

```
json report: out/vouchcode-report.json
pdf report: out/vouchcode-report.pdf
signing key fingerprint: 7D7C BBC8 6885 009B B043 59D0 F26E E03E
publish this fingerprint where a recipient can obtain it independently of this report
```

The JSON is machine-readable and signed. The PDF is what you hand to a supervisor or a
hiring manager. Anyone can check the JSON without installing Vouchcode, using a standard
Ed25519 implementation.

### 6. Run the gate locally, before you touch CI

```sh
vouchcode gate --base-ref main
```

```
vouchcode gate
base ref: main
commits in range: 1
commits found in ledger: 1
minimum confidence: 0.90
comprehension required: yes
gated hunks: 1

FAIL  27f705b8da  generated.py:normalize  ai via tool_signal conf 1.00  comprehension: not_evaluated

result: fail, 1 of 1 gated hunks lack a passing comprehension record
```

Exit code 1 means AI-attributed code in this range has no passing comprehension record.
Exit code 0 means it does, or that there was nothing to gate. Once this behaves the way
you expect locally, copy `.github/workflows/vouchcode-gate.yml` from this repository into
yours to run it on every pull request.

## Signing key fingerprint

This repository's reports are signed with an Ed25519 key whose fingerprint is:

```
7D7C BBC8 6885 009B B043 59D0 F26E E03E
```

This should match the fingerprint shown in any report generated from this repository. It
is also published independently of this repository, so that a verifier can obtain it by a
route the repository's owner does not control.

`reports/vouchcode-self-report.json` is a real report over this repository's own history.
Check it with:

```sh
vouchcode verify-report reports/vouchcode-self-report.json --expect-fingerprint "7D7C BBC8 6885 009B B043 59D0 F26E E03E"
```

The comparison is the point. A fingerprint a verifier only ever sees inside the report it
is meant to authenticate proves nothing, because a forged report carries a forged key and
a fingerprint matching it perfectly. Print your own with `vouchcode key`.

## Commands

| Command | Purpose |
| --- | --- |
| `vouchcode about` | Print the project, author, and repository. |
| `vouchcode init` | Install hooks, create the ledger, generate the signing key. |
| `vouchcode status` | Report hook state and ledger size. Non-zero exit if a hook is inactive. |
| `vouchcode log` | Print ledger entries. `--json` for machine-readable output. |
| `vouchcode key` | Print the signing key fingerprint, for publishing out of band. |
| `vouchcode verify` | Recheck every hash and signature, reporting per entry. |
| `vouchcode report` | Compile a signed JSON report and a PDF summary. |
| `vouchcode verify-report` | Check a report's signature and, with `--expect-fingerprint`, its key. |
| `vouchcode gate` | Fail a build when AI-attributed code lacks a passing comprehension record. |
| `vouchcode badge` | Generate a local status badge SVG from the ledger. |
| `vouchcode scan` | Reconstruct a best-effort ledger from existing history. |
| `vouchcode uninstall` | Remove the hooks. The ledger is retained. |

## How it works

Five layers, each doing one thing.

**Capture.** Git hooks intercept every commit. A `PostToolUse` adapter lets Claude Code
record which lines it generated, into a documented signal format any assistant integration
can write.

**Segmentation.** Each changed Python file is parsed into an abstract syntax tree before
and after the commit, and the two are compared structurally. Attribution is per function,
not per line. A renamed function is recognized as a rename rather than a rewrite, so code
you already accounted for is never sent back through verification. Reformatting and
comment edits produce no hunk at all.

**Comprehension.** Questions are derived from the specific control flow of the hunk in
front of you: what a guard returns, what happens when an iterated collection is empty,
which exception a handler catches. Answers are scored against facts extracted from the
syntax tree, never against a model-generated reference answer, which is what makes the
result reproducible by whoever receives the report.

**Ledger.** Every entry carries the hash of its predecessor and an Ed25519 signature.
Editing any entry breaks that entry and every link after it, and `verify` names the first
point of failure rather than condemning the whole file.

**Reporting.** Signed JSON plus a PDF, both embedding the public key and its fingerprint.

Attribution always states how it was reached, because the three are not interchangeable:

| Source | Confidence | Meaning |
| --- | --- | --- |
| `structural` | 1.0 | The syntax tree proves the logic did not change. Certainty. |
| `tool_signal` | 1.0 | An assistant reported which lines it generated. Evidence. |
| `stylometry` | below 0.75, never 1.0 | The code diverges from your prior style. Inference. |

## Limitations

Stated plainly, because a provenance tool that overstates itself is worse than none.

**Stylometry is probabilistic, and it does not detect AI.** It measures whether code looks
like the rest of the repository. Confidence is always reported, is capped below certainty,
and the path declines to classify at all when the baseline is under twelve prior
definitions or the hunk under twenty syntax nodes. On Vouchcode's own history it produced
a mean confidence of 0.245, which is weak evidence and is reported as such rather than
rounded up.

**The comprehension scorer measures structural consistency, not understanding.** It
separates a correct answer from a keyword-stuffed one and from a confidently wrong one,
and it can in principle be defeated by someone who reads the scoring logic and constructs
an answer to satisfy it. Someone who reasons to the right answer without reading the code
also defeats it. This is partial mitigation, not a solution.

**The gate acts on evidence, not inference.** `vouchcode gate` defaults to a confidence
threshold of 0.9, which covers direct tool signals and structural proof and deliberately
excludes stylometry. On a repository with no assistant integration writing signal files,
the gate will pass almost everything. That is honest rather than useless: it enforces
accountability for code known to be AI-generated, and does not pretend to detect
AI-generated code nobody reported.

**Merge commits are excluded from comprehension scoring** by decision, recorded explicitly
on each entry rather than left as an empty field. A merge introduces no independently
authored logic; the commits it joins carry their own entries.

**Fingerprints are version-tagged, not assumed portable.** `ast.dump` output is not stable
across Python versions, so every entry records the interpreter and a probe hash of how it
serializes the constructs the fingerprinter relies on. Comparing across differing
conditions reports `unverifiable_version`, which is neither tampering nor a clean
verification. A missing tag is treated as non-comparable, never as comparable.

**A signature proves consistency, not identity.** Anyone can generate a key, write a
ledger, and sign a report with it, and that report verifies perfectly. **The fingerprint
comparison is what a verifier actually depends on for trust, not the signature.** A
verifier who only checks the signature has learned that the document is unaltered and
nothing about who produced it.

**Retroactive scans are reconstruction, not observation.** `scan` marks every entry
`capture: retroactive_scan`, uses stylometry alone, and never records a comprehension
outcome. Its baseline is drawn from the current head, which includes code being scored
against it, biasing results toward looking like the developer's own style. Reports count
reconstructed commits separately from observed ones.

**Scope for this version:** Python only, no passphrase on the private key, and no key
rotation.

## Development

```sh
pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
mypy vouchcode
```

Tests drive real git repositories in temporary directories rather than mocking git,
because each phase's exit criterion is a claim about what happens when git actually runs a
hook. CI runs the same four commands across Python 3.10 through 3.13 on Linux and 3.12 on
Windows, with `ruff` and `mypy` pinned so a build's result depends on the diff rather than
on upstream release dates.

`docs/RESEARCH_PAPER.md` describes the system and its evaluation in academic form,
including the adversarial per-phase methodology used to build it. The original design
document is `Documentation/Vouchcode_Research_Documentation.docx`.

## License

MIT.

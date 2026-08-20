# Vouchcode

Local-first cryptographic provenance and comprehension verification for AI-assisted
development.

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

## Install

Requires Python 3.10 or newer, and git.

```sh
pip install -e ".[dev]"
```

## Quickstart

```sh
cd your-repository
vouchcode init
```

`init` installs three git hooks, creates `.vouchcode/ledger.json`, and generates a local
Ed25519 signing key.

Then commit as usual. When a commit contains code attributed to AI generation, Vouchcode
shows you that code and asks you to account for it before the commit completes:

```console
$ git commit -m "Add record normalizer"

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

Answer poorly and the commit is refused, with `git commit --no-verify` available to record
it as explicitly unverified. A commit made with no terminal attached is recorded as
skipped, never as passed.

Then produce and check the portable artifact:

```console
$ vouchcode report -o out
json report: out/vouchcode-report.json
pdf report: out/vouchcode-report.pdf
signing key fingerprint: 0C7C B2E5 0164 3047 B5FA 619C F5F0 858F

$ vouchcode verify
verified: 51
chain intact
```

A recipient checks the report without installing anything:

```console
$ vouchcode verify-report out/vouchcode-report.json --expect-fingerprint "0C7C B2E5 ..."
signature: valid
fingerprint: matches
report verified and signed by the expected key
```

## Commands

| Command | Purpose |
| --- | --- |
| `vouchcode init` | Install hooks, create the ledger, generate the signing key. |
| `vouchcode status` | Report hook state and ledger size. Non-zero exit if a hook is inactive. |
| `vouchcode log` | Print ledger entries. `--json` for machine-readable output. |
| `vouchcode key` | Print the signing key fingerprint, for publishing out of band. |
| `vouchcode verify` | Recheck every hash and signature, reporting per entry. |
| `vouchcode report` | Compile a signed JSON report and a PDF summary. |
| `vouchcode verify-report` | Check a report's signature and, with `--expect-fingerprint`, its key. |
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
comparison is what a verifier actually depends on for trust, not the signature.** Run
`vouchcode key` and publish the result somewhere a recipient can reach independently of
any report you send. A verifier who only checks the signature has learned that the
document is unaltered and nothing about who produced it.

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

The full system design, methodology, threat model, and evaluation strategy are in
`Documentation/Vouchcode_Research_Documentation.docx`.

## License

MIT.

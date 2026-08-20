# Vouchcode

Local-first cryptographic provenance and comprehension verification for AI-assisted
software development.

Vouchcode records which parts of a commit were AI-generated, requires the committing
developer to demonstrate understanding of that code before it is sealed, and appends the
result to a tamper-evident local ledger. It runs entirely on the developer's machine and
makes no call to any external service.

## Why

Attribution tooling answers who or what wrote a line. It does not answer whether the
accountable human understands it. A developer can accept an AI-generated function without
reading it, commit it under their own name, and pass every attribution check that exists
today, because those checks verify authorship of text rather than comprehension of logic.

Vouchcode's position is that attribution is a necessary but insufficient condition for
accountable AI-assisted development, and that verifiable comprehension is the missing
second condition.

## Status

Phase 1 of 6 (Foundation) is complete. The capture hooks and the local ledger work.
Attribution, comprehension verification, cryptographic signing, and reporting are not yet
implemented; every module for those layers is present as a documented stub that raises
NotImplementedError rather than silently returning a wrong answer.

| Phase | Focus | State |
| --- | --- | --- |
| 1 | Foundation: CLI, hook installation, raw capture into a local JSON ledger | complete |
| 2 | Segmentation: AST-based diff segmentation and hunk-level attribution | not started |
| 3 | Comprehension: deterministic question generation and terminal scoring | not started |
| 4 | Ledger: hash chaining and Ed25519 signatures | not started |
| 5 | Reporting: signed JSON and PDF reports, retroactive scan | not started |
| 6 | Evaluation and demonstration | not started |

## Install

Requires Python 3.10 or newer and git.

```sh
pip install -e ".[dev]"
```

## Use

```sh
cd your-repository
vouchcode init
```

`init` installs the capture hooks and creates `.vouchcode/ledger.json`. From that point
every commit appends an entry.

```console
$ vouchcode init
hook installed: .git/hooks/pre-commit
hook installed: .git/hooks/post-commit
hook installed: .git/hooks/post-merge
ledger ready: .vouchcode/ledger.json
initialized: /home/dev/your-repository

$ git commit -m "Add parser"
[main 4edeb29] Add parser

$ vouchcode log
commit      type    timestamp                  attribution   files  author
4edeb29b7f  commit  2026-08-20T08:15:48+00:00  unclassified      1  Sudais Khalid
```

Commands:

| Command | Purpose |
| --- | --- |
| `vouchcode init` | Install the capture hooks and create the ledger. `--force` replaces hooks Vouchcode does not own. |
| `vouchcode status` | Report hook state and ledger size. Exits non-zero if a hook is inactive, so it is usable in CI. |
| `vouchcode log` | Print ledger entries. `--json` for machine-readable output, `--limit N` for the newest N. |
| `vouchcode uninstall` | Remove the managed hooks. The ledger is retained. |

## Ledger format

`.vouchcode/ledger.json` is append-only. Phase 1 writes the commit identity and change
set, and leaves attribution unclassified because no attribution has been attempted yet.

```json
{
  "schema_version": 1,
  "entries": [
    {
      "commit": "ab801c54fb68474370236b74460777e790ed7d69",
      "type": "commit",
      "timestamp": "2026-08-20T08:14:11+00:00",
      "author": { "name": "Sudais Khalid", "email": "msudaiskhalid.ai@gmail.com" },
      "branch": "main",
      "parents": ["09c50f38c7a1b4e0d2f6a8c3b5e7d9f1a2c4e6b8"],
      "files": ["parser.py"],
      "attribution": { "status": "unclassified", "source": null, "confidence": null }
    }
  ]
}
```

A merge commit is recorded with `"type": "merge"` and `"files": null`. Null states that no
file list was computed, where an empty list would state that the merge touched nothing.
Computing one would double count content the side branch's own entries already record, and
deciding whether conflict resolution counts as authored work is a Phase 2 attribution
question.

`git commit --amend` leaves two entries, one for the original hash and one for the amended
hash. An amend creates a new commit rather than modifying one, so the original becomes
unreachable from git history while remaining in the ledger. This is correct for an
append-only ledger: removing the superseded entry would be a deletion from a log whose
whole value is that entries are only ever added, and it would erase the evidence that an
amendment happened.

The ledger is local, per-clone state and is not tracked in git. The portable, shareable
proof is the signed report produced by the reporting layer in Phase 5, not this file.

## Architecture

Five cooperating layers.

| Layer | Package | Responsibility |
| --- | --- | --- |
| Capture | `vouchcode.capture` | Git hooks intercept commits and read the change set. Attribution comes from a direct AI tool signal where one exists, otherwise from a stylometric heuristic reported with an explicit confidence level. |
| Segmentation | `vouchcode.segmentation` | Parses pre- and post-commit sources into ASTs and isolates changed functions and blocks, so attribution aligns with logical units rather than line ranges. |
| Comprehension | `vouchcode.comprehension` | Extracts verifiable structural facts from AI-attributed hunks, derives questions from them, and scores typed answers by matching structural terms against those facts. |
| Ledger | `vouchcode.ledger` | Appends each commit's outcome to a hash-chained, Ed25519-signed local ledger. |
| Reporting | `vouchcode.reporting` | Compiles the ledger into a signed JSON document and a PDF summary, both verifiable offline with the embedded public key. |

Two constraints shape every layer:

Nothing leaves the machine. No source code and no provenance data is transmitted
anywhere.

No external language model participates in Vouchcode's own logic. Question generation and
scoring are derived from the standard library `ast` module and rule-based extraction. This
is what makes a Vouchcode report reproducible by the party receiving it, and it is an
architectural constraint rather than a preference.

## Design decisions

Three hooks. The pre-commit hook records the staged change set while the index still
describes it; the post-commit hook resolves the commit hash that exists only once the
commit is written. Neither alone can observe both facts. Pre-commit is also where Phase 3
will gate a commit on comprehension.

The post-merge hook exists because git does not run the commit hooks for a merge commit it
creates itself, so `git merge --no-ff` would otherwise leave a commit in history with no
ledger entry. A silent gap in a provenance ledger is worse than a coarse record. Merge
classification is by parent count rather than by which hook observed the commit, so a
hand-resolved merge finished with `git commit` and an automatic one are recorded
identically.

Capture never blocks a commit in Phase 1. A provenance tool that rejects a developer's
commit because of a bug in its own recording logic is worse than one that records nothing,
so an internal capture error is reported on stderr and the hook exits zero. Phase 3
introduces a deliberate non-zero exit from pre-commit for a failed comprehension check,
which is a product decision rather than a fault, and the two are kept distinguishable.

Hooks record an absolute interpreter path. Git runs hooks with a reduced environment, and
GUI git clients often run with a PATH that omits the virtual environment Vouchcode was
installed into. Recording the interpreter at install time makes the hook work from any git
frontend.

Foreign hooks are never silently replaced. Every generated hook carries an ownership
marker. A hook without it belongs to the developer or another tool, and `init` refuses to
overwrite it unless `--force` is given.

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
hook.

CI runs the same four commands on every push and pull request, across Python 3.10 through
3.13 on Linux and Python 3.12 on Windows. Windows is in the matrix deliberately: the
capture layer writes hook scripts with forced LF line endings and an absolute interpreter
path because git for Windows runs hooks through sh, and that path needs a real runner to
stay honest. `ruff` and `mypy` are pinned to exact versions in `pyproject.toml` so that a
build's result depends on the diff under review rather than on upstream release dates. A
separate non-blocking job runs `pip-audit` against the dependency tree.

The full system design, methodology, threat model, and evaluation strategy are in
`Documentation/Vouchcode_Research_Documentation.docx`.

## License

MIT.

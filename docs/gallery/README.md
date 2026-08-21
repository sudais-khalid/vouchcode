# Submission gallery

Five images for a submission gallery, in the order a judge should meet them.

| File | Shows |
| --- | --- |
| `01-comprehension-quiz.png` | The comprehension check firing during a commit, with questions derived from the committed function's own control flow and deterministic scores. |
| `02-gate-fail.png` | `vouchcode gate` stopping a build because AI-attributed code has no passing comprehension record. Exit code 1. |
| `03-gate-pass.png` | The same ledger and the same hunk once the record is present. Exit code 0. |
| `04-badge.png` | `badge.svg` at 3x, with its generation date and the note that it is not a third-party attestation. |
| `05-architecture.png` | The five layers, and how attribution states the basis for each claim. |

Every terminal image is rendered from a transcript captured by actually running the
command. None of the text was written to look like output. The badge image is rasterized
from the committed `badge.svg` by reading that file's own geometry and text, so it cannot
show a badge this repository does not contain.

Images 02 and 03 are a pair and should be shown adjacent. The whole point is that the
ledger and the hunk are identical between them, one field differs, and the outcome flips.

Regenerate with `scripts/render_gallery.py` after changing any command output.

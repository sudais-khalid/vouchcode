"""Render Vouchcode submission screenshots from real captured output.

Every terminal image is drawn from a transcript captured by actually running the
command, not from text written to look like output. That matters more than usual here:
the project's argument is that overstated claims are worthless, and a fabricated
screenshot in the submission gallery would contradict it.

Palette matches the demo kit so the gallery, the video, and the deck read as one piece.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ElementTree

from PIL import Image, ImageDraw, ImageFont

REPO = pathlib.Path(__file__).resolve().parents[1]
SHOTS = REPO / "docs" / "gallery" / "transcripts"
OUT = REPO / "docs" / "gallery"
OUT.mkdir(exist_ok=True)

# Palette, shared with the demo kit brief.
BG = (19, 24, 32)
FG = (221, 227, 236)
DIM = (124, 136, 153)
OK = (111, 179, 154)
BAD = (224, 131, 110)
ACCENT = (127, 176, 220)
CHROME = (30, 37, 47)
RULE = (42, 50, 61)

MONO = "C:/Windows/Fonts/consola.ttf"
MONO_BOLD = "C:/Windows/Fonts/consolab.ttf"
SANS = "C:/Windows/Fonts/segoeui.ttf"
SANS_BOLD = "C:/Windows/Fonts/segoeuib.ttf"

SCALE = 2  # Render at 2x so the result stays crisp when a gallery scales it down.


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size * SCALE)


def colour_for(line: str) -> tuple[int, int, int]:
    """Colour a transcript line by what it says, not by position.

    Only the words the tool actually prints drive this, so the image cannot show
    emphasis the real terminal would not.
    """
    stripped = line.strip()
    if stripped.startswith("$"):
        return DIM
    if stripped.startswith("FAIL") or stripped.startswith("result: fail"):
        return BAD
    if stripped.startswith("PASS") or stripped.startswith("result: pass"):
        return OK
    if stripped.startswith("correct,"):
        return OK
    if stripped.startswith(("shallow,", "incorrect,", "unanswered,")):
        return BAD
    if stripped.startswith(("question ", "comprehension:")):
        return ACCENT
    if stripped.startswith("answer:"):
        return FG
    return FG


def render_terminal(
    transcript: pathlib.Path,
    destination: pathlib.Path,
    title: str,
    caption: str,
) -> None:
    """Draw a transcript as a terminal window with a title bar and a caption."""
    lines = transcript.read_text(encoding="utf-8").splitlines()

    body = font(MONO, 13)
    title_font = font(SANS_BOLD, 12)
    caption_font = font(SANS, 11)

    pad = 22 * SCALE
    line_height = int(19 * SCALE)
    chrome_height = 32 * SCALE

    widest = max((body.getlength(line) for line in lines), default=0)
    width = int(widest) + pad * 2
    caption_lines = wrap_caption(caption, caption_font, width - pad * 2)
    caption_block = len(caption_lines) * int(17 * SCALE) + pad

    height = chrome_height + pad + len(lines) * line_height + pad + caption_block

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    # Window chrome. Plain, because a provenance tool is not a consumer app.
    draw.rectangle([0, 0, width, chrome_height], fill=CHROME)
    draw.line([(0, chrome_height), (width, chrome_height)], fill=RULE, width=SCALE)
    draw.text((pad, chrome_height // 2), title, font=title_font, fill=DIM, anchor="lm")

    y = chrome_height + pad
    for line in lines:
        draw.text((pad, y), line, font=body, fill=colour_for(line))
        y += line_height

    y += pad // 2
    draw.line([(pad, y), (width - pad, y)], fill=RULE, width=SCALE)
    y += pad // 2

    for caption_line in caption_lines:
        draw.text((pad, y), caption_line, font=caption_font, fill=DIM)
        y += int(17 * SCALE)

    image.save(destination)
    print(f"{destination.name}  {width // SCALE}x{height // SCALE}")


def wrap_caption(
    text: str, caption_font: ImageFont.FreeTypeFont, limit: int
) -> list[str]:
    """Wrap caption text to the image width."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if caption_font.getlength(candidate) <= limit:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_badge(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Rasterize the real badge.svg by reading its own geometry and text.

    Parsed from the committed file rather than redrawn from memory, so the image cannot
    show a badge the repository does not actually contain.
    """
    tree = ElementTree.fromstring(source.read_text(encoding="utf-8"))
    namespace = {"svg": "http://www.w3.org/2000/svg"}

    # Only the two coloured segment rectangles matter. The file also contains a clipPath
    # rectangle and a full-width gradient overlay, and taking those by index produced a
    # white block over the whole badge on the first attempt.
    group = tree.find("svg:g[@clip-path]", namespace)
    rects = [
        rect
        for rect in group.findall("svg:rect", namespace)
        if str(rect.get("fill", "")).startswith("#")
    ]
    texts = [node.text or "" for node in tree.findall(".//svg:text", namespace)]
    title = (tree.find("svg:title", namespace).text or "").strip()

    label_rect, value_rect = rects[0], rects[1]
    label_width = float(label_rect.get("width", "0"))
    value_width = float(value_rect.get("width", "0"))
    label_fill = label_rect.get("fill", "#555555")
    value_fill = value_rect.get("fill", "#8A8A8A")

    label_text = texts[1] if len(texts) > 1 else "vouchcode"
    value_text = texts[3] if len(texts) > 3 else ""

    zoom = 3 * SCALE
    badge_w = int((label_width + value_width) * zoom)
    badge_h = int(20 * zoom)

    pad = 30 * SCALE
    caption_font = font(SANS, 11)
    heading_font = font(SANS_BOLD, 13)

    caption = (
        "Generated locally by 'vouchcode badge' from this repository's own ledger. "
        "It says 'comprehension not evaluated' rather than implying verification, and "
        "carries its generation date in the SVG title attribute."
    )
    width = max(badge_w + pad * 2, 760 * SCALE)
    caption_lines = wrap_caption(caption, caption_font, width - pad * 2)
    title_lines = wrap_caption(title, font(MONO, 10), width - pad * 2)

    height = (
        pad
        + int(20 * SCALE)
        + pad // 2
        + badge_h
        + pad
        + len(title_lines) * int(15 * SCALE)
        + pad // 2
        + len(caption_lines) * int(17 * SCALE)
        + pad
    )

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    y = pad
    draw.text((pad, y), "badge.svg, shown at 3x", font=heading_font, fill=DIM)
    y += int(20 * SCALE) + pad // 2

    draw.rectangle(
        [pad, y, pad + int(label_width * zoom), y + badge_h], fill=label_fill
    )
    draw.rectangle(
        [pad + int(label_width * zoom), y, pad + badge_w, y + badge_h], fill=value_fill
    )

    # Sized down until the value text fits its segment, so a long value cannot spill
    # outside the badge the way it did before this was checked.
    badge_font = font(MONO, 16)
    while (
        badge_font.getlength(value_text) > value_width * zoom - 8 * SCALE
        and badge_font.size > 8 * SCALE
    ):
        badge_font = ImageFont.truetype(MONO, badge_font.size - SCALE)
    draw.text(
        (pad + int(label_width * zoom / 2), y + badge_h // 2),
        label_text,
        font=badge_font,
        fill=(255, 255, 255),
        anchor="mm",
    )
    draw.text(
        (pad + int(label_width * zoom) + int(value_width * zoom / 2), y + badge_h // 2),
        value_text,
        font=badge_font,
        fill=(255, 255, 255),
        anchor="mm",
    )

    y += badge_h + pad
    mono_small = font(MONO, 10)
    for line in title_lines:
        draw.text((pad, y), line, font=mono_small, fill=DIM)
        y += int(15 * SCALE)

    y += pad // 2
    draw.line(
        [(pad, y - pad // 4), (width - pad, y - pad // 4)], fill=RULE, width=SCALE
    )
    for line in caption_lines:
        draw.text((pad, y), line, font=caption_font, fill=DIM)
        y += int(17 * SCALE)

    image.save(destination)
    print(f"{destination.name}  {width // SCALE}x{height // SCALE}")


LAYERS = [
    (
        "1  Capture",
        "git hooks intercept every commit",
        "Direct tool signal where an assistant reports it. Stylometric fallback "
        "otherwise, always with an explicit confidence.",
        "pre-commit  post-commit  post-merge",
    ),
    (
        "2  Segmentation",
        "attribution per function, not per line",
        "Syntax trees compared before and after. A rename is proven a rename, not "
        "reported as a rewrite.",
        "ast.dump  alpha-renaming  fingerprints",
    ),
    (
        "3  Comprehension",
        "questions derived from this code's control flow",
        "Answers scored against facts extracted from the syntax tree. No language "
        "model, so the score is reproducible.",
        "guards  loops  exception paths",
    ),
    (
        "4  Ledger",
        "hash-chained and Ed25519-signed",
        "Editing any entry breaks it and every link after it. Verification names the "
        "first point of failure.",
        "SHA-256 chain  Ed25519  per-entry status",
    ),
    (
        "5  Reporting",
        "signed JSON and a PDF, verifiable offline",
        "Both embed the public key and display its fingerprint, which is what a "
        "verifier actually checks.",
        "report  verify-report  gate  badge",
    ),
]


def render_architecture(destination: pathlib.Path) -> None:
    """Draw the five-layer architecture.

    Five, not six. Six is the number of development phases; the architecture has five
    layers, and a submission slide claiming otherwise would contradict the README.
    """
    pad = 40 * SCALE
    width = 1000 * SCALE
    row_h = 104 * SCALE
    header_h = 118 * SCALE
    footer_h = 78 * SCALE
    height = header_h + row_h * len(LAYERS) + footer_h

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = font(SANS_BOLD, 26)
    sub_font = font(SANS, 13)
    layer_font = font(SANS_BOLD, 16)
    role_font = font(SANS, 13)
    body_font = font(SANS, 11)
    mono_font = font(MONO, 10)

    draw.text((pad, pad), "Vouchcode architecture", font=title_font, fill=FG)
    draw.text(
        (pad, pad + int(36 * SCALE)),
        "Five cooperating layers. Everything runs locally, with no language model in "
        "the runtime path.",
        font=sub_font,
        fill=DIM,
    )

    y = header_h
    rail_x = pad + int(6 * SCALE)

    for index, (name, role, detail, mechanics) in enumerate(LAYERS):
        # A continuous rail, because the layers form a pipeline rather than a menu.
        draw.line([(rail_x, y), (rail_x, y + row_h)], fill=RULE, width=2 * SCALE)
        draw.ellipse(
            [rail_x - 5 * SCALE, y + 26 * SCALE, rail_x + 5 * SCALE, y + 36 * SCALE],
            fill=ACCENT,
        )

        text_x = pad + int(34 * SCALE)
        draw.text((text_x, y + 18 * SCALE), name, font=layer_font, fill=FG)
        draw.text(
            (text_x + int(190 * SCALE), y + 20 * SCALE),
            role,
            font=role_font,
            fill=ACCENT,
        )

        for offset, line in enumerate(
            wrap_caption(detail, body_font, width - text_x - pad)
        ):
            draw.text(
                (text_x, y + int((46 + offset * 17) * SCALE)),
                line,
                font=body_font,
                fill=DIM,
            )

        draw.text(
            (text_x, y + 82 * SCALE), mechanics, font=mono_font, fill=(90, 102, 118)
        )

        if index < len(LAYERS) - 1:
            draw.line(
                [(pad, y + row_h), (width - pad, y + row_h)], fill=RULE, width=SCALE
            )

        y += row_h

    draw.line(
        [(pad, y + 8 * SCALE), (width - pad, y + 8 * SCALE)], fill=RULE, width=SCALE
    )
    draw.text(
        (pad, y + int(26 * SCALE)),
        "Attribution states how it was reached: structural is proof, tool_signal is "
        "evidence, stylometry is inference capped below 0.75.",
        font=body_font,
        fill=DIM,
    )

    image.save(destination)
    print(f"{destination.name}  {width // SCALE}x{height // SCALE}")


def main() -> None:
    render_terminal(
        SHOTS / "quiz.txt",
        OUT / "01-comprehension-quiz.png",
        "vouchcode: comprehension check during git commit",
        "Real output. Questions are derived from this function's own control flow, and "
        "answers are scored against facts extracted from the syntax tree with no "
        "language model involved.",
    )
    render_terminal(
        SHOTS / "gate_fail.txt",
        OUT / "02-gate-fail.png",
        "vouchcode gate: AI-attributed code with no comprehension record",
        "Real output. The build stops because code reported as AI-generated at full "
        "confidence has not been accounted for. Exit code 1.",
    )
    render_terminal(
        SHOTS / "gate_pass.txt",
        OUT / "03-gate-pass.png",
        "vouchcode gate: the same hunk, once comprehension is on record",
        "Real output. The same ledger and the same hunk, with a passing comprehension "
        "record present. One field changes and the outcome flips. Exit code 0.",
    )
    render_badge(REPO / "badge.svg", OUT / "04-badge.png")
    render_architecture(OUT / "05-architecture.png")


if __name__ == "__main__":
    main()

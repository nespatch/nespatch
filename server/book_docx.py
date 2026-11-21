"""Translate any text PDF into one styled DOCX, pictures kept in reading order.

The key is read from OPENAI_API_KEY. Nothing in this file is specific to one book.

    python server/book_docx.py path/to/file.pdf
"""

import io
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf
from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.shared import Inches, Pt, RGBColor
from openai import OpenAI

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
TARGET = os.environ.get("TARGET_LANG", "Russian")
CAPTION = re.compile(r"^(figure|fig\.|table|chart|photo|map)\s*\d+", re.I)


def clean(text: str) -> str:
    text = text.replace("\u00ad", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def stem(text: str) -> str:
    text = re.sub(r"^\d+\s+", "", text)
    text = re.sub(r"\s+\d+$", "", text)
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().upper()


def repeating_lines(document: pymupdf.Document) -> set[str]:
    counts: Counter[str] = Counter()
    for page in document:
        height = page.rect.height
        for block in page.get_text("blocks"):
            text = stem(clean(block[4]))
            if not text or len(text) > 90:
                continue
            top = block[1] < height * 0.14
            bottom = block[3] > height * 0.88
            if top or bottom:
                counts[text] += 1
    floor = max(4, int(document.page_count * 0.02))
    return {text for text, count in counts.items() if count >= floor}


def collect(path: Path) -> list[dict]:
    document = pymupdf.open(path)
    noise = repeating_lines(document)
    items: list[dict] = []
    for page in document:
        height = page.rect.height
        width = page.rect.width
        rows: list[tuple[float, dict]] = []
        for block in page.get_text("blocks"):
            text = clean(block[4])
            if not text or text.isdigit():
                continue
            center = (block[1] + block[3]) / 2
            if center < height * 0.135 or center > height * 0.90:
                continue
            if len(text) < 90 and stem(text) in noise:
                continue
            rows.append((block[1], {"kind": "text", "text": text}))
        for info in page.get_image_info():
            box = pymupdf.Rect(info["bbox"])
            if box.width < 48 or box.height < 48:
                continue
            if box.width * box.height > width * height * 0.85:
                continue
            pixmap = page.get_pixmap(clip=box, dpi=130)
            rows.append((box.y0, {"kind": "img", "png": pixmap.tobytes("png")}))
        rows.sort(key=lambda row: row[0])
        for _, item in rows:
            if item["kind"] == "img":
                items.append(item)
                continue
            text = item["text"]
            if len(text) == 1 and text.isalpha():
                items.append({"kind": "drop", "text": text})
                continue
            if items and items[-1]["kind"] == "drop":
                letter = items.pop()["text"]
                if text[:1].islower():
                    text = letter + text
                else:
                    text = letter + " " + text
            words = text.split()
            dotted = bool(re.search(r"\.{4,}", text))
            if CAPTION.match(text):
                kind = "cap"
            elif (
                not dotted
                and text.isupper()
                and 1 < len(words) <= 8
                and len(text) < 70
            ):
                kind = "h1"
            elif (
                not dotted
                and len(words) <= 6
                and len(text) < 50
                and text[:1].isupper()
                and not text.endswith((".", ",", ";", ":", "?", "!"))
            ):
                kind = "h2"
            else:
                kind = "p"
            items.append({"kind": kind, "text": text})
    return items


def one_chunk(client: OpenAI, chunk: list[dict]) -> list[str] | None:
    source = "\n".join(
        f"[{index}|{item['kind']}] {item['text']}" for index, item in enumerate(chunk)
    )
    response = client.responses.create(
        model=MODEL,
        max_output_tokens=8000,
        instructions=(
            f"Translate into {TARGET}. Return exactly {len(chunk)} lines. "
            "Keep each [n|kind] tag unchanged. "
            "Keep personal names and product names as written. "
            "Do not add notes. Keep one consistent tone."
        ),
        input=source,
    )
    found = re.findall(r"\[(\d+)\|(\w+)\]\s*(.*)", response.output_text or "")
    if len(found) != len(chunk):
        return None
    ordered = [""] * len(chunk)
    for number, _, line in found:
        index = int(number)
        if index < 0 or index >= len(chunk):
            return None
        ordered[index] = line.strip()
    if any(not line for line in ordered):
        return None
    return ordered


def translate(client: OpenAI, texts: list[dict], progress: Path) -> list[str]:
    done: list[str] = []
    if progress.exists():
        done = json.loads(progress.read_text(encoding="utf-8"))
    step = 8
    while len(done) < len(texts):
        chunk = texts[len(done) : len(done) + step]
        lines = one_chunk(client, chunk)
        if lines is None:
            lines = []
            for item in chunk:
                solo = one_chunk(client, [item])
                if solo is None:
                    raise SystemExit(f"Could not translate item {len(done) + len(lines)}")
                lines.append(solo[0])
        done.extend(lines)
        progress.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        print(f"translated {len(done)}/{len(texts)}", flush=True)
    return done


def write_docx(items: list[dict], translations: list[str], dest: Path) -> None:
    document = Document()
    styles = document.styles
    styles["Normal"].font.name = "Times New Roman"
    styles["Normal"].font.size = Pt(11)
    styles["Normal"].font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
    styles["Normal"].paragraph_format.space_after = Pt(8)
    styles["Normal"].paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    styles["Normal"].paragraph_format.line_spacing = 1.15
    for name, size in (("Heading 1", 16), ("Heading 2", 13)):
        styles[name].font.name = "Times New Roman"
        styles[name].font.size = Pt(size)
        styles[name].font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
        styles[name].font.bold = True
    cursor = 0
    for item in items:
        if item["kind"] == "img":
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(10)
            paragraph.paragraph_format.space_after = Pt(4)
            paragraph.add_run().add_picture(io.BytesIO(item["png"]), width=Inches(5.4))
            continue
        text = translations[cursor]
        cursor += 1
        if item["kind"] == "h1":
            document.add_heading(text, level=1)
        elif item["kind"] == "h2":
            document.add_heading(text, level=2)
        elif item["kind"] == "cap":
            paragraph = document.add_paragraph()
            run = paragraph.add_run(text)
            run.italic = True
            run.font.name = "Times New Roman"
            run.font.size = Pt(10)
        else:
            document.add_paragraph(text)
    document.save(dest)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python server/book_docx.py path/to/file.pdf")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment.")
    source = Path(sys.argv[1])
    items = collect(source)
    texts = [item for item in items if item["kind"] != "img"]
    pictures = sum(1 for item in items if item["kind"] == "img")
    print(f"text {len(texts)} pictures {pictures}", flush=True)
    progress = source.with_suffix(".progress.json")
    client = OpenAI(timeout=180)
    translations = translate(client, texts, progress)
    dest = source.with_suffix(".docx")
    write_docx(items, translations, dest)
    print(f"saved: {dest}")


if __name__ == "__main__":
    main()

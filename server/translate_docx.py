"""Translate a DOCX by replacing text nodes only.

Styles, pictures, and paragraph marks stay in the file. The key is read
from OPENAI_API_KEY.

    python server/translate_docx.py path/to/file.docx
"""

import json
import os
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree
from openai import OpenAI

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
TARGET = os.environ.get("TARGET_LANG", "Russian")
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
WORD = re.compile(r"[A-Za-z\u0400-\u04ff]")


def paragraph_text(paragraph: etree._Element) -> str:
    parts: list[str] = []
    for node in paragraph.xpath(".//w:t", namespaces=NS):
        if node.getparent().tag == f"{{{NS['w']}}}instrText":
            continue
        parts.append(node.text or "")
    return "".join(parts).strip()


def write_back(paragraph: etree._Element, text: str) -> None:
    nodes = [
        node
        for node in paragraph.xpath(".//w:t", namespaces=NS)
        if node.getparent().tag != f"{{{NS['w']}}}instrText"
    ]
    if not nodes:
        return
    nodes[0].text = text
    if nodes[0].get("{http://www.w3.org/XML/1998/namespace}space") is None and (
        text[:1].isspace() or text[-1:].isspace()
    ):
        nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for node in nodes[1:]:
        node.text = ""


def skeleton(root: etree._Element) -> bytes:
    clone = etree.fromstring(etree.tostring(root))
    for node in clone.xpath(".//w:t", namespaces=NS):
        node.getparent().remove(node)
    return etree.tostring(clone)


def one_chunk(client: OpenAI, lines: list[str]) -> list[str] | None:
    source = "\n".join(f"[{index}] {line}" for index, line in enumerate(lines))
    response = client.responses.create(
        model=MODEL,
        reasoning={"effort": "minimal"},
        max_output_tokens=8000,
        instructions=(
            f"Translate into {TARGET}. Return exactly {len(lines)} lines. "
            "Keep each [n] tag. Keep personal names and product names as written. "
            "Do not add notes."
        ),
        input=source,
    )
    found = re.findall(r"\[(\d+)\]\s*(.*)", response.output_text or "")
    if len(found) != len(lines):
        return None
    ordered = [""] * len(lines)
    for number, line in found:
        index = int(number)
        if index < 0 or index >= len(lines):
            return None
        ordered[index] = line.strip()
    if any(not line for line in ordered):
        return None
    return ordered


def translate_lines(client: OpenAI, lines: list[str], progress: Path) -> list[str]:
    done: list[str] = []
    if progress.exists():
        done = json.loads(progress.read_text(encoding="utf-8"))
    step = 8
    while len(done) < len(lines):
        chunk = lines[len(done) : len(done) + step]
        translated = one_chunk(client, chunk)
        if translated is None:
            translated = []
            for line in chunk:
                solo = one_chunk(client, [line])
                if solo is None:
                    raise SystemExit(f"Could not translate line {len(done) + len(translated)}")
                translated.append(solo[0])
        done.extend(translated)
        progress.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        print(f"translated {len(done)}/{len(lines)}", flush=True)
    return done


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python server/translate_docx.py path/to/file.docx")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment.")
    source = Path(sys.argv[1])
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}

    targets = [
        name
        for name in files
        if name.startswith("word/") and name.endswith(".xml")
    ]
    jobs: list[tuple[str, etree._Element]] = []
    trees: dict[str, etree._ElementTree] = {}
    for name in targets:
        root = etree.fromstring(files[name])
        trees[name] = etree.ElementTree(root)
        for paragraph in root.xpath(".//w:p", namespaces=NS):
            text = paragraph_text(paragraph)
            if text and WORD.search(text):
                jobs.append((text, paragraph))

    print(f"paragraphs {len(jobs)}", flush=True)
    before = {name: skeleton(trees[name].getroot()) for name in trees}
    client = OpenAI(timeout=180)
    progress = source.with_suffix(".progress.json")
    translated = translate_lines(client, [text for text, _ in jobs], progress)
    for new_text, (_, paragraph) in zip(translated, jobs):
        write_back(paragraph, new_text)
    dest = source.with_name(f"{source.stem}.ru.docx")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            if name in trees:
                data = etree.tostring(
                    trees[name],
                    xml_declaration=True,
                    encoding="UTF-8",
                    standalone=True,
                )
            archive.writestr(name, data)
    after = {name: skeleton(trees[name].getroot()) for name in trees}
    same = all(before[name] == after[name] for name in trees)
    print(f"style_xml_unchanged: {same}")
    print(f"saved: {dest}")


if __name__ == "__main__":
    main()

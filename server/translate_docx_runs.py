"""Translate each text run in place. Tabs and page numbers stay put.

    python server/translate_docx_runs.py server/samples/111.ten.docx
"""

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
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WORD = re.compile(r"[A-Za-z\u0400-\u04ff]")
PAGE_NUMBER = re.compile(r"^\d+$")


def segments(paragraph: etree._Element) -> list[list[etree._Element]]:
    groups: list[list[etree._Element]] = []
    current: list[etree._Element] = []
    for node in paragraph.iter():
        if node.tag in (f"{W}tab", f"{W}br"):
            if current:
                groups.append(current)
                current = []
            continue
        if node.tag != f"{W}t":
            continue
        if node.getparent() is not None and node.getparent().tag == f"{W}instrText":
            continue
        current.append(node)
    if current:
        groups.append(current)
    return groups


def segment_text(nodes: list[etree._Element]) -> str:
    return "".join(node.text or "" for node in nodes).strip()


def write_back(nodes: list[etree._Element], text: str) -> None:
    nodes[0].text = text
    if text[:1].isspace() or text[-1:].isspace():
        nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for node in nodes[1:]:
        node.text = ""


def skeleton(root: etree._Element) -> bytes:
    clone = etree.fromstring(etree.tostring(root))
    for node in clone.xpath(".//w:t", namespaces=NS):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
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
            "Do not add notes or page numbers."
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


def translate_lines(client: OpenAI, lines: list[str]) -> list[str]:
    done: list[str] = []
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
        print(f"translated {len(done)}/{len(lines)}", flush=True)
    return done


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python server/translate_docx_runs.py path/to/file.docx")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment.")
    source = Path(sys.argv[1])
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}

    trees: dict[str, etree._ElementTree] = {}
    jobs: list[tuple[str, list[etree._Element]]] = []
    for name in files:
        if not (name.startswith("word/") and name.endswith(".xml")):
            continue
        root = etree.fromstring(files[name])
        trees[name] = etree.ElementTree(root)
        for paragraph in root.xpath(".//w:p", namespaces=NS):
            for group in segments(paragraph):
                text = segment_text(group)
                if not text or not WORD.search(text) or PAGE_NUMBER.match(text):
                    continue
                jobs.append((text, group))

    print(f"segments {len(jobs)}", flush=True)
    before = {name: skeleton(tree.getroot()) for name, tree in trees.items()}
    client = OpenAI(timeout=180)
    translated = translate_lines(client, [text for text, _ in jobs])
    for new_text, (_, group) in zip(translated, jobs):
        write_back(group, new_text)

    dest = source.with_name(f"{source.stem}.runs.ru.docx")
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
    after = {name: skeleton(tree.getroot()) for name, tree in trees.items()}
    print(f"style_xml_unchanged: {all(before[name] == after[name] for name in trees)}")
    print(f"saved: {dest}")


if __name__ == "__main__":
    main()

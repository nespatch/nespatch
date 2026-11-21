"""Replace each Word text node in place. Neighboring nodes stay put.

    python server/translate_docx_nodes.py server/samples/bitcoin.docx
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
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WORD = re.compile(r"[A-Za-z\u0400-\u04ff]")
SKIP = re.compile(r"^(https?://|www\.)|\S+@\S+", re.I)


def nodes_of(root: etree._Element) -> list[etree._Element]:
    found = []
    for node in root.xpath(".//w:t", namespaces=NS):
        parent = node.getparent()
        if parent is not None and parent.tag == f"{W}instrText":
            continue
        raw = node.text or ""
        core = raw.strip()
        if core and WORD.search(core) and not SKIP.search(core):
            found.append(node)
    return found


def one_chunk(client: OpenAI, lines: list[str]) -> list[str] | None:
    source = "\n".join(f"[{index}] {line}" for index, line in enumerate(lines))
    response = client.responses.create(
        model=MODEL,
        reasoning={"effort": "minimal"},
        max_output_tokens=4000,
        instructions=(
            f"Translate each line into {TARGET}. Return exactly {len(lines)} lines. "
            "Keep each [n] tag. Keep names as written. "
            "Do not add, remove, or join words beyond that line."
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
    step = 12
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


def skeleton(root: etree._Element) -> bytes:
    clone = etree.fromstring(etree.tostring(root))
    for node in clone.xpath(".//w:t", namespaces=NS):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return etree.tostring(clone)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python server/translate_docx_nodes.py path/to/file.docx")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment.")
    source = Path(sys.argv[1])
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}

    trees: dict[str, etree._ElementTree] = {}
    jobs: list[etree._Element] = []
    for name in files:
        if not (name.startswith("word/") and name.endswith(".xml")):
            continue
        root = etree.fromstring(files[name])
        trees[name] = etree.ElementTree(root)
        jobs.extend(nodes_of(root))

    print(f"nodes {len(jobs)}", flush=True)
    before = {name: skeleton(tree.getroot()) for name, tree in trees.items()}
    cores = [(node.text or "").strip() for node in jobs]
    progress = source.with_suffix(".nodes.progress.json")
    client = OpenAI(timeout=180)
    translated = translate_lines(client, cores, progress)
    for node, new_text in zip(jobs, translated):
        raw = node.text or ""
        lead = raw[: len(raw) - len(raw.lstrip())]
        tail = raw[len(raw.rstrip()) :]
        node.text = f"{lead}{new_text}{tail}"

    dest = source.with_name(f"{source.stem}.nodes.ru.docx")
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

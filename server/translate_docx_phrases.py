"""Translate a phrase, then put the words back into the original nodes.

Tabs and page numbers stay where they are. Each node keeps its own text,
so a paragraph is not emptied.

    python server/translate_docx_phrases.py server/samples/bitcoin.docx
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
PAGE_NUMBER = re.compile(r"^\d+$")


def usable(node: etree._Element) -> bool:
    parent = node.getparent()
    if parent is not None and parent.tag == f"{W}instrText":
        return False
    core = (node.text or "").strip()
    return bool(core) and bool(WORD.search(core)) and not SKIP.search(core) and not PAGE_NUMBER.match(core)


def phrases(paragraph: etree._Element) -> list[list[etree._Element]]:
    groups: list[list[etree._Element]] = []
    current: list[etree._Element] = []

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for node in paragraph.iter():
        if node.tag in (f"{W}tab", f"{W}br"):
            flush()
            continue
        if node.tag != f"{W}t" or not usable(node):
            continue
        current.append(node)
        text = " ".join((item.text or "").strip() for item in current)
        if text.endswith((".", "!", "?")) or len(text.split()) >= 28:
            flush()
    flush()
    return groups


def phrase_text(nodes: list[etree._Element]) -> str:
    return " ".join((node.text or "").strip() for node in nodes)


def distribute(translation: str, nodes: list[etree._Element]) -> list[str]:
    words = translation.split()
    count = len(nodes)
    if not words:
        return [""] * count
    if count == 1 or len(words) == 1:
        return [translation.strip()] + [""] * (count - 1)
    if len(words) <= count:
        return words + [""] * (count - len(words))
    weights = [max(len((node.text or "").strip()), 1) for node in nodes]
    shares = []
    taken = 0
    filled = 0
    total = sum(weights)
    for index, weight in enumerate(weights):
        filled += weight
        target = len(words) if index == count - 1 else round(filled / total * len(words))
        target = min(max(target, taken + 1), len(words) - (count - index - 1))
        shares.append(words[taken:target])
        taken = target
    return [" ".join(share) for share in shares]


def write_group(nodes: list[etree._Element], pieces: list[str]) -> None:
    space = "{http://www.w3.org/XML/1998/namespace}space"
    for index, (node, piece) in enumerate(zip(nodes, pieces)):
        raw = node.text or ""
        lead = raw[: len(raw) - len(raw.lstrip(" "))]
        tail = raw[len(raw.rstrip(" ")) :]
        if piece and index + 1 < len(pieces) and pieces[index + 1]:
            nxt = nodes[index + 1].text or ""
            next_lead = nxt[: len(nxt) - len(nxt.lstrip(" "))]
            if not tail and not next_lead:
                tail = " "
        node.text = f"{lead}{piece}{tail}"
        if node.text[:1] == " " or node.text[-1:] == " ":
            node.set(space, "preserve")


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
            f"Translate each line into {TARGET}. Return exactly {len(lines)} lines. "
            "Keep each [n] tag. Keep personal names and product names as written. "
            "Translate the whole line as one phrase. Do not add notes."
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
    step = 6
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


def translate_docx(source: Path) -> Path:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment.")
    source = Path(source)
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}

    trees: dict[str, etree._ElementTree] = {}
    jobs: list[list[etree._Element]] = []
    for name in files:
        if not (name.startswith("word/") and name.endswith(".xml")):
            continue
        root = etree.fromstring(files[name])
        trees[name] = etree.ElementTree(root)
        for paragraph in root.xpath(".//w:p", namespaces=NS):
            jobs.extend(phrases(paragraph))

    print(f"phrases {len(jobs)}", flush=True)
    before = {name: skeleton(tree.getroot()) for name, tree in trees.items()}
    lines = [" ".join((node.text or "").strip() for node in group) for group in jobs]
    progress = source.with_suffix(".phrases.progress.json")
    client = OpenAI(timeout=180)
    translated = translate_lines(client, lines, progress)
    for group, new_text in zip(jobs, translated):
        write_group(group, distribute(new_text, group))

    dest = source.with_name(f"{source.stem}.phrases.ru.docx")
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
    return dest


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python server/translate_docx_phrases.py path/to/file.docx")
    translate_docx(Path(sys.argv[1]))


def skeleton(root: etree._Element) -> bytes:
    clone = etree.fromstring(etree.tostring(root))
    for node in clone.xpath(".//w:t", namespaces=NS):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return etree.tostring(clone)


if __name__ == "__main__":
    main()

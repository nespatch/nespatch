# Minimal text-PDF trial.
# PDF -> DOCX -> DeepL text API -> same DOCX, with font, size, and bold kept.

import html
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

import tkinter as tk
from tkinter import filedialog, ttk

from docx.oxml.ns import qn
from pdf2docx import Converter

LANGS = [
    ("Spanish", "ES"),
    ("English", "EN"),
    ("German", "DE"),
    ("French", "FR"),
    ("Italian", "IT"),
    ("Portuguese", "PT"),
    ("Dutch", "NL"),
    ("Polish", "PL"),
    ("Russian", "RU"),
    ("Ukrainian", "UK"),
    ("Turkish", "TR"),
    ("Chinese", "ZH"),
    ("Japanese", "JA"),
    ("Korean", "KO"),
]


def load_env():
    here = Path(__file__).resolve()
    for folder in here.parents:
        path = folder / ".env"
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
        return


def deepl_settings():
    load_env()
    key = os.environ.get("DEEPL_AUTH_KEY", "").strip()
    base = os.environ.get("DEEPL_API_URL", "https://api-free.deepl.com").rstrip("/")
    if not key:
        raise RuntimeError("DeepL key is missing.")
    return key, base


def iter_paragraphs(document):
    for paragraph in document.paragraphs:
        yield paragraph
    for table in document.tables:
        yield from iter_table(table)


def iter_table(table):
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                yield paragraph
            for nested in cell.tables:
                yield from iter_table(nested)


def has_drawing(run):
    return bool(run._r.xpath(".//*[local-name()='drawing' or local-name()='pict']"))


def run_format(run):
    return {
        "bold": bool(run.bold),
        "italic": bool(run.italic),
        "name": run.font.name,
        "size": run.font.size,
    }


def markup(paragraph):
    parts = []
    formats = {}
    index = 0
    for run in paragraph.runs:
        if has_drawing(run) or not run.text:
            continue
        formats[index] = run_format(run)
        parts.append(f'<s i="{index}">{html.escape(run.text)}</s>')
        index += 1
    if not parts:
        return None
    return f"<p>{''.join(parts)}</p>", formats


def translate_batch(texts, target, key, base):
    body = json.dumps({
        "text": texts,
        "target_lang": target,
        "tag_handling": "xml",
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/v2/translate",
        data=body,
        headers={
            "Authorization": f"DeepL-Auth-Key {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"DeepL refused the text ({error.code}). {detail}") from error
    return [item["text"] for item in payload["translations"]]


def pieces_from_translation(translated, formats):
    try:
        root = ElementTree.fromstring(f"<root>{translated}</root>")
    except ElementTree.ParseError:
        fallback = next(iter(formats.values()))
        return [(re.sub(r"<[^>]+>", "", translated), fallback)]

    grouped = {key: [] for key in formats}
    loose = []
    for node in root.iter():
        if node.tag != "s":
            continue
        try:
            index = int(node.attrib.get("i", "-1"))
        except ValueError:
            index = -1
        text = "".join(node.itertext())
        if index in grouped:
            grouped[index].append(text)
        elif text:
            loose.append(text)

    pieces = []
    for index in sorted(formats):
        text = "".join(grouped[index])
        if text:
            pieces.append((text, formats[index]))
    if loose:
        pieces.append(("".join(loose), next(iter(formats.values()))))
    if not pieces:
        plain = "".join(root.itertext())
        pieces.append((plain, next(iter(formats.values()))))
    return pieces


def replace_text_runs(paragraph, pieces):
    element = paragraph._p
    for child in list(element):
        if child.tag != qn("w:r"):
            continue
        run_element = child
        text = "".join(node.text or "" for node in run_element.iter(qn("w:t")))
        drawings = run_element.xpath(".//*[local-name()='drawing' or local-name()='pict']")
        if text and not drawings:
            element.remove(child)
    for text, fmt in pieces:
        run = paragraph.add_run(text)
        run.bold = fmt["bold"]
        run.italic = fmt["italic"]
        if fmt["name"]:
            run.font.name = fmt["name"]
        if fmt["size"]:
            run.font.size = fmt["size"]


def convert(pdf_path, docx_path):
    converter = Converter(str(pdf_path))
    try:
        converter.convert(str(docx_path))
    finally:
        converter.close()


def translate_docx(docx_path, target, status):
    from docx import Document

    key, base = deepl_settings()
    document = Document(str(docx_path))
    jobs = []
    for paragraph in iter_paragraphs(document):
        marked = markup(paragraph)
        if marked:
            jobs.append((paragraph, marked[0], marked[1]))
    if not jobs:
        raise RuntimeError("No text found. This PDF may be a scan.")

    status(f"Translating {len(jobs)} paragraphs…")
    for start in range(0, len(jobs), 40):
        chunk = jobs[start:start + 40]
        translated = translate_batch([item[1] for item in chunk], target, key, base)
        for (paragraph, _source, formats), result in zip(chunk, translated):
            replace_text_runs(paragraph, pieces_from_translation(result, formats))
        status(f"Translated {min(start + 40, len(jobs))} of {len(jobs)}…")
    document.save(str(docx_path))


def run_pipeline(pdf_path, target, status):
    pdf_path = Path(pdf_path)
    docx_path = pdf_path.with_name(f"{pdf_path.stem} {target}.docx")
    status("Converting to DOCX…")
    convert(pdf_path, docx_path)
    translate_docx(docx_path, target, status)
    status(f"Saved {docx_path.name}")
    return docx_path


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("nespatch text try")
        self.resizable(False, False)
        self.pdf_path = None

        frame = ttk.Frame(self, padding=16)
        frame.grid()

        ttk.Label(frame, text="Target language").grid(row=0, column=0, sticky="w")
        self.language = ttk.Combobox(
            frame,
            values=[name for name, _code in LANGS],
            state="readonly",
            width=28,
        )
        self.language.current(0)
        self.language.grid(row=1, column=0, sticky="ew", pady=(4, 12))

        self.file_label = ttk.Label(frame, text="No PDF chosen")
        self.file_label.grid(row=2, column=0, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="ew", pady=12)
        ttk.Button(buttons, text="Choose PDF", command=self.choose).grid(row=0, column=0)
        self.translate_button = ttk.Button(buttons, text="Translate", command=self.start, state="disabled")
        self.translate_button.grid(row=0, column=1, padx=(8, 0))

        self.status = ttk.Label(frame, text="", wraplength=360)
        self.status.grid(row=4, column=0, sticky="w")

    def choose(self):
        path = filedialog.askopenfilename(
            title="Choose a text PDF",
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return
        self.pdf_path = path
        self.file_label.configure(text=Path(path).name)
        self.translate_button.configure(state="normal")
        self.set_status("")

    def target_code(self):
        name = self.language.get()
        for label, code in LANGS:
            if label == name:
                return code
        return "ES"

    def set_status(self, text):
        self.status.configure(text=text)

    def start(self):
        if not self.pdf_path:
            return
        self.translate_button.configure(state="disabled")

        def work():
            try:
                run_pipeline(
                    self.pdf_path,
                    self.target_code(),
                    lambda text: self.after(0, self.set_status, text),
                )
            except Exception as error:
                self.after(0, self.set_status, str(error))
            finally:
                self.after(0, lambda: self.translate_button.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()

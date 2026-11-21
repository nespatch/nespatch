"""Send one PDF through a cheap model and download the PDF it builds.

The key is read from OPENAI_API_KEY. Nothing is written into this file.

    $env:OPENAI_API_KEY = "..."
    python server/paper_try.py server/samples/attention.pdf
"""

import os
import sys
import time
from pathlib import Path

from openai import OpenAI

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
TARGET = os.environ.get("TARGET_LANG", "Russian")


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment. Do not put it in this file.")
    if len(sys.argv) < 2:
        sys.exit("Usage: python server/paper_try.py path/to/paper.pdf")

    source = Path(sys.argv[1])
    if not source.is_file():
        sys.exit(f"File not found: {source}")

    client = OpenAI(timeout=900)
    with source.open("rb") as handle:
        uploaded = client.files.create(file=handle, purpose="user_data")
    started = time.time()
    response = client.responses.create(
        model=MODEL,
        tools=[
            {
                "type": "code_interpreter",
                "container": {"type": "auto", "file_ids": [uploaded.id]},
            }
        ],
        tool_choice="required",
        max_output_tokens=64000,
        instructions=(
            "Finish the file in this turn. Do not describe a plan. "
            "Read the PDF inside the python tool, translate it, and save a PDF."
        ),
        input=(
            f"The file {source.name} is already in the python tool container. "
            f"Translate every paragraph into {TARGET}. "
            "Save a new file named translated.pdf. "
            "The title and the body must be in {TARGET}, not in the source language. "
            "Keep headings, figures, captions, tables, and equations readable. "
            "Do not summarize and do not return the original file unchanged."
        ),
    )

    out_dir = source.parent
    saved = []
    containers = []
    for item in response.output:
        kind = getattr(item, "type", None)
        container_id = getattr(item, "container_id", None)
        if container_id and container_id not in containers:
            containers.append(container_id)
        print(f"step: {kind}")
        if kind != "message":
            continue
        for block in item.content:
            for note in getattr(block, "annotations", None) or []:
                if getattr(note, "type", None) != "container_file_citation":
                    continue
                name = Path(note.filename).name or "translated.pdf"
                if not name.lower().endswith(".pdf"):
                    name += ".pdf"
                target = out_dir / f"{source.stem}.{TARGET.lower()}{Path(name).suffix}"
                body = client.containers.files.content.retrieve(
                    note.file_id, container_id=note.container_id
                )
                target.write_bytes(body.read())
                saved.append(target)

    for container_id in containers:
        listing = client.containers.files.list(container_id, limit=100)
        for index, entry in enumerate(listing.data, start=1):
            name = Path(getattr(entry, "path", "") or getattr(entry, "filename", "") or f"{index}.pdf").name
            if not name.lower().endswith(".pdf"):
                continue
            target = out_dir / f"{source.stem}.{index}.{name}"
            body = client.containers.files.content.retrieve(entry.id, container_id=container_id)
            target.write_bytes(body.read())
            print(f"file: {target.name} bytes={target.stat().st_size}")
            saved.append(target)

    elapsed = time.time() - started
    usage = getattr(response, "usage", None)
    print(f"model: {MODEL}")
    print(f"seconds: {elapsed:.0f}")
    text = (response.output_text or "").replace("\n", " ")
    print(f"note: {text[:300]}")
    if usage is not None:
        print(
            "tokens: "
            f"in={getattr(usage, 'input_tokens', '?')} "
            f"out={getattr(usage, 'output_tokens', '?')}"
        )
    if saved:
        for path in saved:
            print(f"saved: {path}")
    else:
        print(response.output_text)
        sys.exit("The model did not return a PDF.")


if __name__ == "__main__":
    main()

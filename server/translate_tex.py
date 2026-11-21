"""Translate prose in a LaTeX paper. Commands, math, and paths stay put.

The key is read from OPENAI_API_KEY.

    python server/translate_tex.py server/samples/arxiv-src/aaai27.tex
"""

import os
import sys
from pathlib import Path

from openai import OpenAI

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
TARGET = os.environ.get("TARGET_LANG", "Russian")


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in the environment. Do not put it in this file.")
    if len(sys.argv) < 2:
        sys.exit("Usage: python server/translate_tex.py path/to/paper.tex")

    source = Path(sys.argv[1])
    original = source.read_text(encoding="utf-8")
    client = OpenAI(timeout=900)
    response = client.responses.create(
        model=MODEL,
        max_output_tokens=64000,
        instructions=(
            "Return only the complete LaTeX file. No markdown fence. "
            f"Translate human prose into {TARGET}: title, authors' affiliations "
            "may stay, abstract, section titles, captions, paragraphs, and "
            "the words inside theorem-like environments. "
            "Do not change commands, math, labels, citations, file names, "
            "or bibliography keys. Keep every \\begin and \\end."
        ),
        input=original,
    )
    text = response.output_text or ""
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    target = source.with_suffix(".ru.tex")
    target.write_text(text, encoding="utf-8")
    usage = getattr(response, "usage", None)
    print(f"model: {MODEL}")
    if usage is not None:
        print(
            "tokens: "
            f"in={getattr(usage, 'input_tokens', '?')} "
            f"out={getattr(usage, 'output_tokens', '?')}"
        )
    print(f"saved: {target}")
    print(f"has_end: {r'\end{document}' in text}")
    print(f"chars: {len(text)}")


if __name__ == "__main__":
    main()

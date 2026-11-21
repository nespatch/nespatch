"""Restore LaTeX command names that the translator rewrote with Cyrillic letters."""

import re
import sys
from pathlib import Path

LOOK = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "А": "A",
        "Е": "E",
        "О": "O",
        "Р": "P",
        "С": "C",
        "п": "p",
        "П": "P",
        "т": "t",
        "Т": "T",
        "к": "k",
        "К": "K",
        "м": "m",
        "М": "M",
        "н": "h",
        "и": "u",
    }
)


def fix_command(match: re.Match[str]) -> str:
    name = match.group(1)
    if any("\u0400" <= ch <= "\u04ff" for ch in name):
        return "\\" + name.translate(LOOK)
    return match.group(0)


def main() -> None:
    path = Path(sys.argv[1])
    original = Path(sys.argv[2]).read_text(encoding="utf-8") if len(sys.argv) > 2 else ""
    known = set(re.findall(r"\\([A-Za-z@]+)", original))
    text = path.read_text(encoding="utf-8")

    def repair(match: re.Match[str]) -> str:
        name = match.group(1)
        fixed = name.translate(LOOK)
        if fixed in known:
            print(f"fix \\{name} -> \\{fixed}")
            return "\\" + fixed
        print(f"keep \\{name}")
        return match.group(0)

    text = re.sub(
        r"\\([A-Za-z\u0400-\u04ff]*[\u0400-\u04ff][A-Za-z\u0400-\u04ff]*)",
        repair,
        text,
    )
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()

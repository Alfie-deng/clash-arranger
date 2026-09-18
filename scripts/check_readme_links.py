#!/usr/bin/env python3
"""Check local markdown links in README and docs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def iter_markdown() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "README.en.md"]
    files.extend(sorted((ROOT / "docs").glob("*.md")))
    files.extend(sorted(ROOT.glob("*.md")))
    return [p for p in files if p.is_file()]


def main() -> int:
    failed = 0
    for path in iter_markdown():
        text = path.read_text(encoding="utf-8")
        for _label, target in LINK.findall(text):
            url = target.split()[0].strip("<>")
            if url.startswith(("http://", "https://", "mailto:", "#")):
                continue
            url = url.split("#", 1)[0]
            if not url:
                continue
            dest = (path.parent / url).resolve()
            if not dest.exists():
                print(f"{path.relative_to(ROOT)} -> missing {url}")
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

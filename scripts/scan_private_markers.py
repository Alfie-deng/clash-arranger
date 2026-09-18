#!/usr/bin/env python3
"""Fail the build on leftover private markers."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
SKIP_FILES = {"scan_private_markers.py"}
ALLOWED_LINE = (
    "Alfie-deng/clash-arranger",
    "github.com/Alfie-deng/clash-arranger",
    "Alfie-deng",
)
NEEDLES = [
    "fivevision-r2s",
    "home-fnos",
    "三毛",
    "XSUS",
    "火烧云",
    "/Users/alfie",
    "10.10.10.",
    "iCloud~ws~stash",
    "19190",
    "19090",
]
IDENTITY = re.compile(r"\b(alfie|Alfie|proxy-seat-autopilot|proxy_seat_autopilot)\b")


def iter_files() -> list[Path]:
    out = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".pyc", ".so"}:
            continue
        if path.name in SKIP_FILES:
            continue
        out.append(path)
    return out


def main() -> int:
    failed = 0
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = str(path.relative_to(ROOT))
        for needle in NEEDLES:
            if needle in text:
                print(f"{rel}: marker {needle!r}")
                failed += 1
        for i, line in enumerate(text.splitlines(), start=1):
            if not IDENTITY.search(line):
                continue
            if any(allow in line for allow in ALLOWED_LINE):
                continue
            print(f"{rel}:{i}: {line.strip()}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

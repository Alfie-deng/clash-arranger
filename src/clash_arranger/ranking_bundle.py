"""Ship canonical ranking modules with a remote request.

A stale or missing permanent copy on a probe host must not change results.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import ModuleType
from typing import Any

_PACKAGE = Path(__file__).resolve().parent
_BUNDLE_FILES = (
    "models.py",
    "config.py",
    "samples.py",
    "policy.py",
    "mixing.py",
    "ranking.py",
)


def module_sources() -> dict[str, str]:
    out = {}
    for name in _BUNDLE_FILES:
        out[name] = (_PACKAGE / name).read_text(encoding="utf-8")
    return out


def module_hashes(sources: dict[str, str] | None = None) -> dict[str, str]:
    sources = sources or module_sources()
    return {
        name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sources.items()
    }


def exec_bundle(
    sources: dict[str, str],
    *,
    entry: str = "ranking",
) -> dict[str, ModuleType]:
    """Execute bundled sources in an isolated namespace (no site-packages copies)."""
    import sys
    import types

    mods: dict[str, ModuleType] = {}
    # Minimal package shell so relative imports resolve.
    pkg = types.ModuleType("clash_arranger")
    pkg.__path__ = []
    pkg.__package__ = "clash_arranger"
    sys.modules["clash_arranger"] = pkg
    for filename, source in sources.items():
        name = filename.removesuffix(".py")
        qual = f"clash_arranger.{name}"
        mod = types.ModuleType(qual)
        mod.__package__ = "clash_arranger"
        mod.__file__ = f"<ranking-bundle/{filename}>"
        sys.modules[qual] = mod
        setattr(pkg, name, mod)
        mods[name] = mod
        exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    if entry not in mods:
        raise KeyError(entry)
    return mods


def rank_with_bundle(
    sources: dict[str, str],
    samples: list[Any],
    cfg: Any,
    window: str,
    now: Any,
) -> Any:
    mods = exec_bundle(sources)
    return mods["ranking"].rank_nodes(samples, cfg, window, now)

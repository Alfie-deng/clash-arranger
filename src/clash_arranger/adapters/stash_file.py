"""Downstream YAML file sync. File success is not app hot-reload confirmation."""

from __future__ import annotations

from pathlib import Path

from . import yaml_group


class DownstreamError(RuntimeError):
    pass


def sync_group(
    path: Path,
    group: str,
    seats: list[str],
    *,
    source_defs: Path | None = None,
) -> dict:
    path = Path(path)
    if not path.is_file():
        raise DownstreamError(f"downstream file missing: {path}")
    try:
        result = yaml_group.apply_group_patch(path, group, seats, source_defs=source_defs)
    except yaml_group.YamlPatchError as exc:
        raise DownstreamError(str(exc)) from exc
    written = yaml_group.parse_group_proxies(path.read_text(encoding="utf-8"), group)
    if written != list(seats):
        raise DownstreamError(f"downstream mismatch {written} != {seats}")
    result["file_seats"] = written
    result["app_runtime"] = "unconfirmed"
    return result


def read_seats(path: Path, group: str) -> list[str]:
    return yaml_group.parse_group_proxies(Path(path).read_text(encoding="utf-8"), group)

"""Text-level proxy-group patches. Never yaml.dump() a whole document."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

import yaml

_NAME_LINE = re.compile(r"^(\s*)-\s+name:\s*(.+?)\s*$")
_PROXIES_KEY = re.compile(r"^(\s*)proxies:\s*(.*)$")
_TOP_KEY = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_BUILTINS = {
    "DIRECT",
    "REJECT",
    "GLOBAL",
    "PASS",
    "COMPATIBLE",
    "REJECT-DROP",
    "REJECT-TIN",
}


class YamlPatchError(RuntimeError):
    """Surgical YAML update was refused."""


def _unquote(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _yaml_scalar(name: str) -> str:
    if not name or any(c in name for c in ":#{}[],&*!|>%@`'\"\\") or name != name.strip():
        return json.dumps(name, ensure_ascii=False)
    return name


def parse_group_proxies(text: str, group_name: str) -> list[str]:
    lines = text.splitlines()
    in_group = False
    group_indent = None
    in_proxies = False
    proxies_indent = None
    item_indent = None
    out: list[str] = []
    for line in lines:
        if not in_group:
            if (
                line.lstrip().startswith("- name:")
                and _unquote(line.split(":", 1)[1]) == group_name
            ):
                in_group = True
                group_indent = len(line) - len(line.lstrip(" "))
            continue
        lead = len(line) - len(line.lstrip(" "))
        stripped = line.lstrip()
        if (
            stripped
            and group_indent is not None
            and lead <= group_indent
            and stripped.startswith("- ")
        ):
            break
        if not in_proxies:
            if stripped.startswith("proxies:"):
                in_proxies = True
                proxies_indent = lead
            continue
        if not stripped:
            if out:
                break
            continue
        if stripped.startswith("- ") and lead >= (proxies_indent or 0):
            if item_indent is None:
                item_indent = lead
            if lead == item_indent:
                out.append(_unquote(stripped[2:]))
                continue
            break
        if item_indent is not None and lead > item_indent:
            continue
        break
    return out


def _group_span(lines: list[str], group_idx: int, group_indent: str) -> int:
    base = len(group_indent)
    j = group_idx + 1
    while j < len(lines):
        raw = lines[j]
        if not raw.strip():
            j += 1
            continue
        lead = len(raw) - len(raw.lstrip(" "))
        stripped = raw.lstrip()
        if lead < base:
            break
        if lead == base and stripped.startswith("- "):
            break
        j += 1
    return j


def replace_group_members(content: str, group_name: str, new_proxies: list[str]) -> str:
    lines = content.splitlines(keepends=True)
    group_idx = None
    group_indent = None
    for i, line in enumerate(lines):
        match = _NAME_LINE.match(line.rstrip("\n"))
        if match and _unquote(match.group(2)) == group_name:
            group_idx = i
            group_indent = match.group(1)
            break
    if group_idx is None:
        raise YamlPatchError(f"group not found: {group_name}")
    body_end = _group_span(lines, group_idx, group_indent or "")
    proxies_key_idx = None
    inline = ""
    for k in range(group_idx + 1, body_end):
        match = _PROXIES_KEY.match(lines[k].rstrip("\n"))
        if match:
            proxies_key_idx = k
            inline = (match.group(2) or "").strip()
            break
    if proxies_key_idx is None:
        raise YamlPatchError(f"group {group_name} has no proxies list")
    if inline:
        raise YamlPatchError(f"group {group_name} uses inline proxies; refuse to rewrite")
    key_line = lines[proxies_key_idx]
    key_indent = len(key_line) - len(key_line.lstrip(" "))
    item_start = proxies_key_idx + 1
    item_end = item_start
    item_indent: int | None = None
    while item_end < body_end:
        raw = lines[item_end]
        if not raw.strip():
            if item_indent is not None:
                break
            item_end += 1
            continue
        lead = len(raw) - len(raw.lstrip(" "))
        stripped = raw.lstrip()
        if stripped.startswith("- ") and lead >= key_indent:
            if item_indent is None:
                item_indent = lead
            if lead == item_indent:
                item_end += 1
                continue
            break
        if item_indent is not None and lead > item_indent:
            item_end += 1
            continue
        break
    if item_indent is None:
        item_indent = key_indent + 2
    pad = " " * item_indent
    new_block = "".join(f"{pad}- {_yaml_scalar(name)}\n" for name in new_proxies)
    return "".join(lines[:item_start]) + new_block + "".join(lines[item_end:])


def verify_syntax(content: str) -> dict:
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise YamlPatchError("document is not a mapping")
    proxy_names = {
        item["name"]
        for item in (data.get("proxies") or [])
        if isinstance(item, dict) and item.get("name")
    }
    group_names = {
        item.get("name")
        for item in (data.get("proxy-groups") or [])
        if isinstance(item, dict) and item.get("name")
    }
    allowed = proxy_names | group_names | _BUILTINS
    for group in data.get("proxy-groups") or []:
        if not isinstance(group, dict):
            continue
        gname = group.get("name", "unknown")
        for member in group.get("proxies") or []:
            if member not in allowed:
                raise YamlPatchError(f"group {gname} references missing {member}")
    return data


def available_proxy_names(content: str) -> set[str]:
    data = yaml.safe_load(content) or {}
    names: set[str] = set()
    for item in data.get("proxies") or []:
        if isinstance(item, dict) and item.get("name"):
            names.add(str(item["name"]))
        elif isinstance(item, str) and item.strip():
            names.add(item.strip())
    return names


def _proxies_body_span(lines: list[str]) -> tuple[int, int]:
    proxies_key = None
    for i, line in enumerate(lines):
        match = _TOP_KEY.match(line.rstrip("\n"))
        if match and match.group(1) == "proxies" and not line.startswith((" ", "\t")):
            proxies_key = i
            break
    if proxies_key is None:
        raise YamlPatchError("missing top-level proxies:")
    body_start = proxies_key + 1
    body_end = body_start
    while body_end < len(lines):
        raw = lines[body_end]
        if not raw.strip():
            body_end += 1
            continue
        if not raw.startswith((" ", "\t")) and _TOP_KEY.match(raw.rstrip("\n")):
            break
        body_end += 1
    return body_start, body_end


def section_text(content: str, key: str) -> str:
    lines = content.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        match = _TOP_KEY.match(line.rstrip("\n"))
        if match and match.group(1) == key and not line.startswith((" ", "\t")):
            start = i
            break
    if start is None:
        return ""
    end = start + 1
    while end < len(lines):
        raw = lines[end]
        if raw.strip() and not raw.startswith((" ", "\t")) and _TOP_KEY.match(raw.rstrip("\n")):
            break
        end += 1
    return "".join(lines[start:end])


def extract_proxy_block(content: str, name: str) -> str | None:
    lines = content.splitlines(keepends=True)
    try:
        body_start, body_end = _proxies_body_span(lines)
    except YamlPatchError:
        return None
    start = None
    item_indent = None
    for i in range(body_start, body_end):
        match = _NAME_LINE.match(lines[i].rstrip("\n"))
        if match and _unquote(match.group(2)) == name:
            start = i
            item_indent = match.group(1)
            break
    if start is None:
        return None
    end = start + 1
    while end < body_end:
        raw = lines[end]
        match = _NAME_LINE.match(raw.rstrip("\n"))
        if match and match.group(1) == item_indent:
            break
        end += 1
    block = "".join(lines[start:end])
    if not block.endswith("\n"):
        block += "\n"
    return block


def names_in_proxies(content: str) -> set[str]:
    lines = content.splitlines(keepends=True)
    body_start, body_end = _proxies_body_span(lines)
    names = set()
    for i in range(body_start, body_end):
        match = _NAME_LINE.match(lines[i].rstrip("\n"))
        if match:
            names.add(_unquote(match.group(2)))
    return names


def inject_missing_proxy_defs(
    needed: Iterable[str],
    target_text: str,
    source_text: str,
) -> tuple[str, list[str]]:
    """Copy only missing top-level proxy blocks. Leave dns/tun/rules/groups alone."""
    needed_names = [n for n in needed if n]
    existing = names_in_proxies(target_text)
    missing = [n for n in needed_names if n not in existing]
    ordered = list(dict.fromkeys(missing))
    if not ordered:
        return target_text, []
    blocks = []
    injected = []
    for name in ordered:
        block = extract_proxy_block(source_text, name)
        if not block:
            continue
        blocks.append(block)
        injected.append(name)
    if not blocks:
        return target_text, []
    lines = target_text.splitlines(keepends=True)
    body_start, body_end = _proxies_body_span(lines)
    prefix = "".join(lines[:body_start])
    body = "".join(lines[body_start:body_end])
    suffix = "".join(lines[body_end:])
    dns_before = section_text(target_text, "dns")
    tun_before = section_text(target_text, "tun")
    rules_before = section_text(target_text, "rules")
    groups_before = section_text(target_text, "proxy-groups")
    if body and not body.endswith("\n"):
        body += "\n"
    new_text = prefix + body + "".join(blocks) + suffix
    if section_text(new_text, "dns") != dns_before:
        raise YamlPatchError("inject would change dns")
    if section_text(new_text, "tun") != tun_before:
        raise YamlPatchError("inject would change tun")
    if section_text(new_text, "rules") != rules_before:
        raise YamlPatchError("inject would change rules")
    if section_text(new_text, "proxy-groups") != groups_before:
        raise YamlPatchError("inject would change proxy-groups")
    return new_text, injected


def write_text(path: Path, content: str, *, snapshot: str | None = None) -> Path:
    path = Path(path)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = path.with_name(f"{path.name}.bak_{ts}")
    tmp = path.with_name(f"{path.name}.tmp_{ts}")
    if path.exists():
        shutil.copy2(path, backup)
    elif snapshot is not None:
        backup.write_text(snapshot, encoding="utf-8")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return backup


def restore_text(path: Path, original: str) -> None:
    tmp = path.with_name(f"{path.name}.rollback.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(original)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def apply_group_patch(
    path: Path,
    group_name: str,
    seats: list[str],
    *,
    source_defs: Path | None = None,
) -> dict:
    original = Path(path).read_text(encoding="utf-8")
    content = original
    injected: list[str] = []
    if source_defs and Path(source_defs).is_file():
        content, injected = inject_missing_proxy_defs(
            seats, content, Path(source_defs).read_text(encoding="utf-8")
        )
    available = available_proxy_names(content)
    missing = [n for n in seats if n not in available]
    if missing:
        raise YamlPatchError(f"candidates missing from proxy pool: {missing}")
    patched = replace_group_members(content, group_name, seats)
    verify_syntax(patched)
    if parse_group_proxies(patched, group_name) != list(seats):
        raise YamlPatchError("patched group members do not match plan")
    if section_text(patched, "dns") != section_text(original, "dns") and not injected:
        # injecting defs must not change dns; group patch also must not
        if section_text(content, "dns") != section_text(patched, "dns"):
            raise YamlPatchError("group patch changed dns")
    if section_text(patched, "rules") != section_text(content, "rules"):
        raise YamlPatchError("group patch changed rules")
    write_text(path, patched, snapshot=original)
    return {"injected": injected, "original": original, "patched": patched}

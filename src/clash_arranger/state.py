"""Locks, status, catch-up, and manual leases."""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import AppConfig, ScheduleConfig
from .models import EXIT_LOCK_BUSY
from .samples import parse_ts

COMMITTED_STATES = {
    "FILE_COMMITTED",
    "APP_RUNTIME_CONFIRMED",
    "RECONCILED_NO_WRITE",
}


class LockBusy(RuntimeError):
    exit_code = EXIT_LOCK_BUSY


def is_weekday(now: dt.datetime) -> bool:
    return now.weekday() < 5


def schedule_active(schedule: ScheduleConfig, now: dt.datetime) -> bool:
    if schedule.kind == "daily":
        return True
    if schedule.kind == "weekdays_only":
        if is_weekday(now):
            return True
        return schedule.weekend_policy != "no_op"
    return True


def weekend_noop(cfg: AppConfig, now: dt.datetime) -> bool:
    return (
        cfg.schedule.kind == "weekdays_only"
        and cfg.schedule.weekend_policy == "no_op"
        and not is_weekday(now)
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def status_path(cfg: AppConfig) -> Path:
    return cfg.state_dir / "status.json"


def lease_path(cfg: AppConfig) -> Path:
    return cfg.state_dir / "manual-lease.json"


def pending_path(cfg: AppConfig) -> Path:
    return cfg.state_dir / "shift-pending.json"


def health_path(cfg: AppConfig) -> Path:
    return cfg.state_dir / "health.json"


def write_status(
    cfg: AppConfig, update: dict[str, Any], *, dry_run: bool = False
) -> dict[str, Any] | None:
    if dry_run:
        return None
    prev = read_json(status_path(cfg))
    merged = {**prev, **update}
    merged["updated_at"] = dt.datetime.now(cfg.tz).isoformat(timespec="seconds")
    _atomic_json(status_path(cfg), merged)
    return merged


def write_health(cfg: AppConfig, payload: dict[str, Any]) -> None:
    _atomic_json(health_path(cfg), payload)


def write_manual_lease(
    cfg: AppConfig,
    *,
    now: dt.datetime,
    ttl_sec: int,
    seats: list[str],
    reason: str,
) -> dict[str, Any]:
    payload = {
        "until": (now + dt.timedelta(seconds=ttl_sec)).isoformat(timespec="seconds"),
        "seats": list(seats),
        "reason": reason,
        "created_at": now.isoformat(timespec="seconds"),
    }
    _atomic_json(lease_path(cfg), payload)
    return payload


def active_manual_lease(cfg: AppConfig, now: dt.datetime) -> dict[str, Any] | None:
    data = read_json(lease_path(cfg))
    until = parse_ts(str(data.get("until") or ""), cfg.tz)
    if until is None:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=now.tzinfo)
    if until <= now:
        return None
    return data


def current_due_window(cfg: AppConfig, now: dt.datetime) -> str | None:
    if weekend_noop(cfg, now):
        return None
    due = None
    for shift in cfg.schedule.shifts:
        start = now.replace(hour=shift.at.hour, minute=shift.at.minute, second=0, microsecond=0)
        if now >= start:
            due = shift.window
    return due


def window_rank(cfg: AppConfig, name: str | None) -> int:
    names = [s.window for s in cfg.schedule.shifts]
    try:
        return names.index(name) if name else -1
    except ValueError:
        return -1


def decide_catch_up(
    cfg: AppConfig,
    now: dt.datetime,
    *,
    health_window: str | None,
    health_updated: str | None,
    pending: dict[str, Any] | None,
    completion_verified: bool = False,
) -> tuple[str | None, str]:
    due = current_due_window(cfg, now)
    if not due:
        return None, "no_due_window"
    due_shift = next(s for s in cfg.schedule.shifts if s.window == due)
    due_at = now.replace(
        hour=due_shift.at.hour, minute=due_shift.at.minute, second=0, microsecond=0
    )
    health_ts = parse_ts(str(health_updated or ""), cfg.tz)
    already = (
        health_window == due
        and health_ts is not None
        and health_ts.date() == now.date()
        and health_ts >= due_at
    )
    if already and completion_verified:
        return None, "already_applied"
    if already:
        return due, "incomplete_current"
    if pending:
        pw = str(pending.get("window") or "")
        if pw == due:
            return due, "pending_due"
        if pw and window_rank(cfg, pw) < window_rank(cfg, due):
            return due, "stale_pending_superseded"
    return due, "missed_current"


def catch_up_completion_verified(
    due: str | None,
    health: dict[str, Any] | None,
    status: dict[str, Any] | None,
    disk: list[str],
    api: list[str],
    downstream: list[str],
    *,
    seat_count: int,
) -> bool:
    health = dict(health or {})
    status = dict(status or {})
    target = list(health.get("target_seats") or [])
    return bool(
        due
        and health.get("window") == due
        and status.get("current_window") == due
        and status.get("transaction_state") in COMMITTED_STATES
        and len(target) == seat_count
        and target == list(disk) == list(api) == list(downstream)
    )


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        if os.name == "nt":
            raise LockBusy("posix lock required")
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy("seat transaction busy") from exc
        yield
    finally:
        fh.close()

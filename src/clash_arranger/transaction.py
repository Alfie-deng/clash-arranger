"""Dry-run-first seat transactions with rollback and four-state success."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .adapters import yaml_group
from .adapters.mihomo import (
    ControllerError,
    MihomoClient,
    is_reload_connection_drop,
    wait_api_seats,
)
from .adapters.stash_file import DownstreamError, read_seats, sync_group
from .config import AppConfig
from .health import ProbeFn, probe_candidates, responsive_nodes, zero_of_all_rounds
from .models import (
    EXIT_DOWNSTREAM_PENDING,
    EXIT_ESCAPE_EXHAUSTED,
    EXIT_OK,
    EXIT_PROBE_REJECT,
    EXIT_ROLLBACK,
    EXIT_RUNTIME_UNVERIFIED,
    SUCCESS_DEFINITION,
    TransactionView,
)
from .state import exclusive_lock, write_status


class TransactionError(RuntimeError):
    def __init__(
        self, message: str, *, exit_code: int = EXIT_ROLLBACK, view: TransactionView | None = None
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.view = view


@dataclass
class ApplyRequest:
    seats: list[str]
    old_seats: list[str]
    apply: bool
    force_manual: bool
    scheduled: bool
    cas_disk: list[str] | None = None
    cas_api: list[str] | None = None
    primary_already_written: bool = False
    expected_token: str | None = None


def _log(view: TransactionView, message: str) -> None:
    view.logs.append(message)


def run_transaction(
    cfg: AppConfig,
    request: ApplyRequest,
    *,
    client: MihomoClient | None = None,
    probe_fn: ProbeFn | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    now: dt.datetime | None = None,
    lock: bool = True,
) -> TransactionView:
    view = TransactionView(state="PREPARED")
    _log(view, SUCCESS_DEFINITION)
    yaml_path = Path(cfg.controller.yaml_path)
    if not request.apply:
        _log(view, "dry-run: no files or controller writes")
        view.state = "DRY_RUN"
        view.reason = "default_dry_run"
        view.disk = (
            yaml_group.parse_group_proxies(
                yaml_path.read_text(encoding="utf-8"), cfg.controller.group
            )
            if yaml_path.is_file()
            else None
        )
        view.exit_code = EXIT_OK
        return view
    if not request.scheduled and not request.force_manual:
        view.state = "REFUSED"
        view.reason = "human apply requires --force-manual"
        view.exit_code = 2
        return view

    def _body() -> TransactionView:
        return _apply_locked(cfg, request, view, client, probe_fn, sleep_fn, now)

    if lock:
        with exclusive_lock(cfg.state_dir / "apply.lock"):
            return _body()
    return _body()


def _apply_locked(
    cfg: AppConfig,
    request: ApplyRequest,
    view: TransactionView,
    client: MihomoClient | None,
    probe_fn: ProbeFn | None,
    sleep_fn: Callable[[float], None] | None,
    now: dt.datetime | None,
) -> TransactionView:
    yaml_path = Path(cfg.controller.yaml_path)
    original = yaml_path.read_text(encoding="utf-8")
    disk_before = yaml_group.parse_group_proxies(original, cfg.controller.group)
    if request.cas_disk is not None and disk_before != list(request.cas_disk):
        view.state = "CAS_ABORT"
        view.reason = f"disk changed {disk_before} != {request.cas_disk}"
        view.exit_code = EXIT_ROLLBACK
        return view
    if client is not None and request.cas_api is not None:
        api_before = client.group_seats()
        if api_before != list(request.cas_api):
            view.state = "CAS_ABORT"
            view.reason = f"api changed {api_before} != {request.cas_api}"
            view.exit_code = EXIT_ROLLBACK
            return view

    if probe_fn is not None:
        pre = probe_candidates(request.seats, probe_fn, rounds=cfg.health.rounds, sleep_fn=sleep_fn)
        if responsive_nodes(request.seats, pre) != list(request.seats):
            dead = zero_of_all_rounds(request.seats, pre)
            view.state = "PRE_PROBE_REJECT"
            view.reason = f"pre-probe dead={dead} results={pre.results}"
            view.exit_code = EXIT_PROBE_REJECT
            view.probe_ok = False
            return view
        _log(view, f"pre-probe pass results={pre.results}")

    wrote_primary = False
    if request.primary_already_written and disk_before == list(request.seats):
        _log(view, "primary already matches plan; skip rewrite")
    else:
        result = yaml_group.apply_group_patch(
            yaml_path,
            cfg.controller.group,
            request.seats,
            source_defs=Path(cfg.downstream.source_proxy_defs)
            if cfg.downstream.source_proxy_defs
            else None,
        )
        wrote_primary = True
        _log(view, f"primary wrote injected={result.get('injected')}")

    disk = yaml_group.parse_group_proxies(
        yaml_path.read_text(encoding="utf-8"), cfg.controller.group
    )
    view.disk = disk
    if disk != list(request.seats):
        yaml_group.restore_text(yaml_path, original)
        view.state = "ROLLED_BACK"
        view.reason = f"disk mismatch {disk}"
        view.exit_code = EXIT_ROLLBACK
        return view

    reload_status = "skipped"
    api = list(disk)
    if client is not None:
        try:
            reload_status = client.reload(str(yaml_path))
        except Exception as exc:
            if is_reload_connection_drop(exc):
                reload_status = "soft_fail_connection_drop"
            else:
                yaml_group.restore_text(yaml_path, original)
                view.state = "ROLLED_BACK"
                view.reason = f"reload failed: {exc}"
                view.exit_code = EXIT_ROLLBACK
                return view
        try:
            api = wait_api_seats(client, request.seats, sleep_fn=sleep_fn)
        except ControllerError as exc:
            if reload_status == "soft_fail_connection_drop":
                view.state = "RELOAD_INCONSISTENT"
                view.reason = f"reload dropped and API mismatch: {exc}"
                yaml_group.restore_text(yaml_path, original)
                view.exit_code = EXIT_ROLLBACK
                return view
            yaml_group.restore_text(yaml_path, original)
            if client is not None:
                try:
                    client.reload(str(yaml_path))
                    wait_api_seats(client, request.old_seats, sleep_fn=sleep_fn)
                except Exception:
                    pass
            view.state = "ROLLED_BACK"
            view.reason = str(exc)
            view.exit_code = EXIT_ROLLBACK
            return view
        if reload_status == "soft_fail_connection_drop":
            if api == list(request.seats) and disk == list(request.seats):
                _log(view, "reload connection drop degraded: disk and API already match")
            else:
                yaml_group.restore_text(yaml_path, original)
                view.state = "ROLLED_BACK"
                view.reason = "reload drop with inconsistent views"
                view.exit_code = EXIT_ROLLBACK
                return view

    view.api = api
    view.reload_status = reload_status

    if probe_fn is not None:
        post = probe_candidates(
            request.seats, probe_fn, rounds=cfg.health.rounds, sleep_fn=sleep_fn
        )
        if responsive_nodes(request.seats, post) != list(request.seats):
            yaml_group.restore_text(yaml_path, original)
            if client is not None:
                try:
                    client.reload(str(yaml_path))
                    wait_api_seats(client, request.old_seats, sleep_fn=sleep_fn)
                except Exception as rollback_exc:
                    view.state = "ROLLBACK_FAILED"
                    view.reason = f"post-probe failed and rollback failed: {rollback_exc}"
                    view.exit_code = EXIT_ROLLBACK
                    return view
            view.state = "ROLLED_BACK"
            view.reason = f"post-apply probe failed results={post.results}"
            view.probe_ok = False
            view.exit_code = EXIT_ROLLBACK
            return view
        view.probe_ok = True
        _log(view, "post-apply probe pass")

    downstream_ok = True
    downstream_seats: list[str] | None = None
    if cfg.downstream.enabled and cfg.downstream.path:
        try:
            sync_group(
                Path(cfg.downstream.path),
                cfg.controller.group,
                request.seats,
                source_defs=Path(cfg.downstream.source_proxy_defs)
                if cfg.downstream.source_proxy_defs
                else None,
            )
            downstream_seats = read_seats(Path(cfg.downstream.path), cfg.controller.group)
        except DownstreamError as exc:
            downstream_ok = False
            view.state = "DOWNSTREAM_PENDING"
            view.reason = str(exc)
            view.downstream = None
            view.exit_code = EXIT_DOWNSTREAM_PENDING
            write_status(
                cfg,
                {
                    "transaction_state": "DOWNSTREAM_PENDING",
                    "target_seats": request.seats,
                    "primary_written": True,
                    "last_error": str(exc),
                },
            )
            _log(view, "primary written; downstream failed; not a full success")
            return view
        if cfg.downstream.confirm_app_runtime:
            view.app_runtime = "unconfirmed"
            _log(view, "downstream file committed; app runtime unconfirmed")
        else:
            view.app_runtime = "not_required"
    else:
        downstream_seats = list(request.seats)

    view.downstream = downstream_seats
    four_ok = (
        view.disk == list(request.seats)
        and view.api == list(request.seats)
        and (downstream_seats == list(request.seats) if cfg.downstream.enabled else True)
        and (view.probe_ok is not False)
        and downstream_ok
    )
    if four_ok:
        view.state = "FILE_COMMITTED"
        view.reason = "disk==api==downstream and post-apply probe pass"
        view.exit_code = EXIT_OK
        if view.app_runtime == "unconfirmed":
            view.exit_code = EXIT_RUNTIME_UNVERIFIED
            view.state = "FILE_COMMITTED"
            _log(view, "FILE_COMMITTED with APP_RUNTIME_UNCONFIRMED")
    else:
        view.state = "FAILED"
        view.reason = "views not aligned"
        view.exit_code = EXIT_ROLLBACK
    write_status(
        cfg,
        {
            "transaction_state": view.state,
            "target_seats": request.seats,
            "primary_written": wrote_primary or request.primary_already_written,
            "last_error": ""
            if view.exit_code in {EXIT_OK, EXIT_RUNTIME_UNVERIFIED}
            else view.reason,
        },
    )
    _log(view, f"wrote_primary={wrote_primary}")
    return view


def attempt_escapes(
    cfg: AppConfig,
    sets: list[list[str]],
    old_seats: list[str],
    *,
    client: MihomoClient | None,
    probe_fn: ProbeFn,
    scheduled: bool,
    force_manual: bool,
) -> TransactionView:
    last = TransactionView(state="HEALTH_ESCAPE_EXHAUSTED", exit_code=EXIT_ESCAPE_EXHAUSTED)
    tried: list[list[str]] = []
    for seats in sets:
        if seats in tried or seats == old_seats:
            continue
        tried.append(list(seats))
        view = run_transaction(
            cfg,
            ApplyRequest(
                seats=seats,
                old_seats=old_seats,
                apply=True,
                force_manual=force_manual,
                scheduled=scheduled,
            ),
            client=client,
            probe_fn=probe_fn,
            lock=False,
        )
        if view.exit_code in {EXIT_OK, EXIT_RUNTIME_UNVERIFIED, EXIT_DOWNSTREAM_PENDING}:
            return view
        last = view
        last.state = "HEALTH_ESCAPE_EXHAUSTED" if view.exit_code != EXIT_OK else view.state
        last.exit_code = (
            EXIT_ESCAPE_EXHAUSTED
            if view.exit_code not in {EXIT_OK, EXIT_RUNTIME_UNVERIFIED}
            else view.exit_code
        )
    last.exit_code = EXIT_ESCAPE_EXHAUSTED
    last.state = "HEALTH_ESCAPE_EXHAUSTED"
    last.reason = last.reason or "all escape sets failed"
    return last

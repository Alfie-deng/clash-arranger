"""Command line. Default is dry-run; live writes need --apply."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .adapters.mihomo import MihomoClient
from .config import ConfigError, load_config, load_secret
from .health import (
    choose_escape_seats,
    evaluate_sustained_speed_promotion,
    probe_candidates,
    rewrite_cooled_down,
    tick_isolation_recovery,
    zero_of_all_rounds,
)
from .models import EXIT_OK, EXIT_USAGE, RANKING_POLICY, RANKING_POLICY_ZH, SUCCESS_DEFINITION
from .ranking import rank_nodes, score_window_meta
from .samples import fixture_mode_samples, load_jsonl, lookback
from .state import (
    LockBusy,
    active_manual_lease,
    decide_catch_up,
    health_path,
    read_json,
    status_path,
    weekend_noop,
    write_health,
)
from .transaction import ApplyRequest, attempt_escapes, run_transaction


def _parse_now(raw: str | None, tz) -> dt.datetime:
    if not raw:
        return dt.datetime.now(tz)
    stamp = dt.datetime.fromisoformat(raw)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=tz)
    return stamp.astimezone(tz)


COMMANDS = ("doctor", "sample", "rank", "plan", "apply", "watch", "status")


def _normalize_argv(argv: list[str]) -> list[str]:
    """Allow `cmd --config x` and `--config x cmd`."""
    command = None
    flags: list[str] = []
    for item in argv:
        if command is None and item in COMMANDS:
            command = item
            continue
        flags.append(item)
    if command is None:
        return argv
    return [*flags, command]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clash-arranger",
        description="Clash node arranger for ordered Mihomo/Clash fallback groups.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", required=False, help="Profile YAML")
    parser.add_argument("--samples", help="JSONL samples (defaults to config samples_path)")
    parser.add_argument("--window", help="Score window name")
    parser.add_argument("--now", help="ISO timestamp override")
    parser.add_argument("--apply", action="store_true", help="Really write. Default is dry-run.")
    parser.add_argument("--force-manual", action="store_true", help="Allow a human write.")
    parser.add_argument(
        "--scheduled", action="store_true", help="Mark this run as a scheduler shift."
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("doctor", "Parse config and fixtures. No live writes."),
        ("sample", "Validate or record fixture-mode samples."),
        ("rank", "Compute a frozen-window ranking."),
        ("plan", "Rank + mix + optional pre-probe. Always dry-run."),
        ("apply", "Write seats. Still dry-run unless --apply."),
        ("watch", "Health watch, promotion, and escape."),
        ("status", "Show state files."),
    ):
        sub.add_parser(name, help=help_text)
    return parser


def _cfg_from(args: argparse.Namespace):
    if not args.config:
        raise ConfigError("--config is required")
    return load_config(args.config)


def _samples(args: argparse.Namespace, cfg):
    path = Path(args.samples) if args.samples else cfg.samples_path
    return load_jsonl(path, tz=cfg.tz, profile=cfg.profile)


def _emit(args: argparse.Namespace, payload: dict[str, Any], text: str) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(text)


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _cfg_from(args)
    samples = fixture_mode_samples(cfg)
    lines = [
        f"profile={cfg.profile}",
        f"timezone={cfg.timezone}",
        f"schedule={cfg.schedule.kind} weekend={cfg.schedule.weekend_policy}",
        f"windows={', '.join(cfg.windows)}",
        f"seat_count={cfg.mix.seat_count}",
        f"samples={cfg.samples_path} n={len(samples)}",
        f"controller={cfg.controller.adapter} {cfg.controller.base_url} group={cfg.controller.group}",
        "default_mode=dry-run",
        f"policy={RANKING_POLICY_ZH}",
        "doctor does not touch a live controller",
    ]
    _emit(args, {"ok": True, "lines": lines, "sample_count": len(samples)}, "\n".join(lines))
    return EXIT_OK


def cmd_sample(args: argparse.Namespace) -> int:
    cfg = _cfg_from(args)
    samples = fixture_mode_samples(cfg) if not args.samples else _samples(args, cfg)
    valid = [s for s in samples if s.sample_valid and s.usable_mbps is not None]
    failed = [s for s in samples if not s.sample_valid]
    text = (
        f"schema=1 total={len(samples)} valid_throughput={len(valid)} "
        f"failed_or_invalid={len(failed)} (failed rows are not 0 Mbps)"
    )
    _emit(
        args,
        {
            "total": len(samples),
            "valid": len(valid),
            "failed": [
                {"node_id": s.node_id, "error_reason": s.error_reason, "ts": s.ts.isoformat()}
                for s in failed
            ],
        },
        text,
    )
    return EXIT_OK


def _rank(args, cfg):
    now = _parse_now(args.now, cfg.tz)
    window = args.window or (
        cfg.schedule.shifts[0].window if cfg.schedule.shifts else next(iter(cfg.windows))
    )
    result = rank_nodes(_samples(args, cfg), cfg, window, now)
    return now, result


def cmd_rank(args: argparse.Namespace) -> int:
    cfg = _cfg_from(args)
    now, result = _rank(args, cfg)
    meta = score_window_meta(cfg.window(result.window), now, cfg.tz)
    payload = {
        "window": result.window,
        "timezone": result.timezone,
        "score_window": meta,
        "tag_as_of": result.tag_as_of.isoformat(),
        "seats": result.seats,
        "ranked": result.ranked,
        "mix_reason": result.mix_reason,
        "filter_log": result.filter_log,
        "ranking_policy": RANKING_POLICY,
        "ranking_policy_zh": RANKING_POLICY_ZH,
        "scores": {k: result.stats[k].median_mbps for k in result.seats if k in result.stats},
        "tags": {k: result.stats[k].tag for k in result.stats},
        "confidence": {k: result.stats[k].confidence for k in result.stats},
    }
    text = (
        f"window={result.window} tz={result.timezone} [{meta['start']}, {meta['end']})\n"
        f"tag_as_of={result.tag_as_of.isoformat()}\n"
        f"seats={result.seats}\n"
        f"mix={result.mix_reason}\n"
        f"policy={RANKING_POLICY_ZH}\n"
        "delay is not a speed signal"
    )
    _emit(args, payload, text)
    return EXIT_OK


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _cfg_from(args)
    now = _parse_now(args.now, cfg.tz)
    if weekend_noop(cfg, now):
        _emit(
            args,
            {"noop": True, "reason": "weekend_no_op"},
            "WEEKEND_IDLE no controller/probe/file access",
        )
        return EXIT_OK
    return cmd_rank(args)


def cmd_apply(args: argparse.Namespace) -> int:
    cfg = _cfg_from(args)
    now = _parse_now(args.now, cfg.tz)
    if weekend_noop(cfg, now):
        _emit(
            args,
            {"noop": True, "reason": "weekend_no_op"},
            "WEEKEND_IDLE skipped before controller access",
        )
        return EXIT_OK
    _now, result = _rank(args, cfg)
    yaml_path = Path(cfg.controller.yaml_path)
    from .adapters.yaml_group import parse_group_proxies

    old = (
        parse_group_proxies(yaml_path.read_text(encoding="utf-8"), cfg.controller.group)
        if yaml_path.is_file()
        else []
    )
    client = None
    probe_fn = None
    if args.apply:
        secret = load_secret(cfg.controller)
        client = MihomoClient(cfg.controller, secret=secret)
        probe_fn = lambda name: client.probe_delay(
            name, url=cfg.health.url, timeout_ms=cfg.health.timeout_ms
        )
    view = run_transaction(
        cfg,
        ApplyRequest(
            seats=result.seats,
            old_seats=old,
            apply=bool(args.apply),
            force_manual=bool(args.force_manual),
            scheduled=bool(args.scheduled),
            cas_disk=old if old else None,
        ),
        client=client,
        probe_fn=probe_fn,
        now=now,
    )
    payload = {
        "state": view.state,
        "reason": view.reason,
        "seats": result.seats,
        "disk": view.disk,
        "api": view.api,
        "downstream": view.downstream,
        "probe_ok": view.probe_ok,
        "exit_code": view.exit_code,
        "logs": view.logs,
    }
    _emit(args, payload, f"{view.state} {view.reason} seats={result.seats} rc={view.exit_code}")
    return view.exit_code


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = _cfg_from(args)
    now = _parse_now(args.now, cfg.tz)
    if weekend_noop(cfg, now):
        _emit(
            args,
            {"noop": True, "reason": "weekend_no_op"},
            "WEEKEND_IDLE health/escape/promotion disabled",
        )
        return EXIT_OK
    lease = active_manual_lease(cfg, now)
    health = read_json(health_path(cfg))
    expected = list(health.get("target_seats") or [])
    ranked = list(health.get("ranked_pool") or [])
    isolated = dict(health.get("isolated") or {})
    if not expected:
        _emit(args, {"skip": True, "reason": "no_committed_state"}, "watch: no committed seats")
        return EXIT_OK

    client = None
    probe_fn: Any
    if args.apply:
        client = MihomoClient(cfg.controller, secret=load_secret(cfg.controller))
        probe_fn = lambda name: client.probe_delay(
            name, url=cfg.health.url, timeout_ms=cfg.health.timeout_ms
        )
    else:
        probe_fn = lambda _name: 50
        _emit(
            args,
            {"dry_run": True},
            "watch dry-run uses synthetic live probes; pass --apply for controller",
        )

    result = probe_candidates(expected, probe_fn, rounds=cfg.health.rounds)
    dead = zero_of_all_rounds(expected, result)
    isolated = tick_isolation_recovery(
        isolated, [n for n in expected if n not in dead], now, cfg.health
    )
    mass = len(dead) >= cfg.health.mass_dead
    if lease and not mass:
        _emit(
            args,
            {"lease": True, "observe_only": True, "dead": dead},
            f"manual lease active; observe only (mass failure still escapes) dead={dead}",
        )
        return EXIT_OK

    if mass:
        sets = []
        pool = list(dict.fromkeys(ranked + expected))
        for offset in range(cfg.health.escape_max_sets):
            rotated = pool[offset:] + pool[:offset]
            try:
                escape = choose_escape_seats(
                    rotated, expected, result, isolated=isolated, now=now, cfg=cfg
                )
            except RuntimeError:
                continue
            if escape not in sets:
                sets.append(escape)
        view = attempt_escapes(
            cfg,
            sets,
            expected,
            client=client,
            probe_fn=probe_fn,
            scheduled=True,
            force_manual=bool(args.force_manual),
        )
        _emit(
            args,
            {"escape": view.state, "reason": view.reason, "exit_code": view.exit_code},
            f"{view.state} {view.reason}",
        )
        return view.exit_code

    samples = lookback(_samples(args, cfg), now, cfg.promotion.lookback_min)
    candidate, evidence = evaluate_sustained_speed_promotion(expected, samples, cfg.promotion)
    last_rewrite = None
    if health.get("last_rewrite"):
        last_rewrite = dt.datetime.fromisoformat(str(health["last_rewrite"]))
        if last_rewrite.tzinfo is None:
            last_rewrite = last_rewrite.replace(tzinfo=cfg.tz)
    if candidate and rewrite_cooled_down(last_rewrite, now, cfg.health):
        promoted = [candidate] + [n for n in expected if n != candidate]
        if args.apply:
            view = run_transaction(
                cfg,
                ApplyRequest(
                    seats=promoted,
                    old_seats=expected,
                    apply=True,
                    force_manual=bool(args.force_manual),
                    scheduled=True,
                ),
                client=client,
                probe_fn=probe_fn,
                now=now,
            )
            _emit(
                args,
                {"promotion": view.state, "evidence": evidence, "exit_code": view.exit_code},
                f"{view.state} {evidence}",
            )
            return view.exit_code
        _emit(
            args,
            {"promotion_ready": promoted, "evidence": evidence},
            f"SPEED_PROMOTION_READY {promoted}",
        )
        return EXIT_OK

    write_health(
        cfg,
        {
            **health,
            "isolated": isolated,
            "last_dead": dead,
            "updated": now.isoformat(timespec="seconds"),
        },
    )
    _emit(args, {"dead": dead, "isolated": isolated}, f"watch ok dead={dead}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _cfg_from(args)
    now = _parse_now(args.now, cfg.tz)
    status = read_json(status_path(cfg))
    health = read_json(health_path(cfg))
    lease = active_manual_lease(cfg, now)
    due, reason = decide_catch_up(
        cfg,
        now,
        health_window=health.get("window"),
        health_updated=health.get("updated"),
        pending=read_json(cfg.state_dir / "shift-pending.json"),
        completion_verified=False,
    )
    payload = {
        "due_window": due,
        "catch_up": reason,
        "status": status,
        "health": health,
        "lease": lease,
        "weekend_noop": weekend_noop(cfg, now),
        "success_definition": SUCCESS_DEFINITION,
    }
    _emit(args, payload, json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(argv if argv is not None else sys.argv[1:]))
    commands = {
        "doctor": cmd_doctor,
        "sample": cmd_sample,
        "rank": cmd_rank,
        "plan": cmd_plan,
        "apply": cmd_apply,
        "watch": cmd_watch,
        "status": cmd_status,
    }
    try:
        return commands[args.command](args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except LockBusy as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except FileNotFoundError as exc:
        print(f"missing file: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())

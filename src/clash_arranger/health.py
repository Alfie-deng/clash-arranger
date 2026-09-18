"""Live delay probes, isolation, escape sets, and sustained-speed promotion.

Controller delay is liveness only. It is never treated as throughput.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from .config import AppConfig, HealthConfig, PromotionConfig
from .models import ProbeResult, Sample
from .samples import parse_ts

ProbeFn = Callable[[str], int | None]


def probe_candidates(
    names: list[str],
    probe_fn: ProbeFn,
    *,
    rounds: int = 2,
    sleep_fn: Callable[[float], None] | None = None,
) -> ProbeResult:
    ordered = list(dict.fromkeys(n for n in names if n))
    out: dict[str, list[int | None]] = {n: [] for n in ordered}
    for round_no in range(1, max(1, rounds) + 1):
        for name in ordered:
            out[name].append(probe_fn(name))
        if sleep_fn and round_no < rounds:
            sleep_fn(1.0)
    return ProbeResult(out)


def live_nodes(names: list[str], result: ProbeResult) -> list[str]:
    return [n for n in names if result.all_live(n)]


def responsive_nodes(names: list[str], result: ProbeResult) -> list[str]:
    """One timeout is jitter. Two-round total failure is dead."""
    return [n for n in names if result.any_live(n)]


def zero_of_all_rounds(names: list[str], result: ProbeResult) -> list[str]:
    return [n for n in names if result.dead(n)]


def isolated_blocked(
    isolated: dict[str, Any], name: str, now: dt.datetime, cfg: HealthConfig
) -> bool:
    rec = (isolated or {}).get(name) or {}
    until = _parse_iso(rec.get("until"), now)
    if until and until > now:
        return True
    if rec and int(rec.get("pass_streak") or 0) < cfg.recovery_passes:
        if until is None or until <= now:
            return True
    return False


def mark_isolated(
    isolated: dict[str, Any], names: list[str], now: dt.datetime, cfg: HealthConfig
) -> dict[str, Any]:
    out = dict(isolated or {})
    until = (now + dt.timedelta(seconds=cfg.isolate_sec)).isoformat(timespec="seconds")
    for name in names:
        prev = dict(out.get(name) or {})
        prev.update({"until": until, "pass_streak": 0})
        out[name] = prev
    return out


def tick_isolation_recovery(
    isolated: dict[str, Any],
    live: list[str],
    now: dt.datetime,
    cfg: HealthConfig,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    live_set = set(live)
    for name, rec in (isolated or {}).items():
        cur = dict(rec)
        until = _parse_iso(cur.get("until"), now)
        if name in live_set and (until is None or until <= now):
            cur["pass_streak"] = int(cur.get("pass_streak") or 0) + 1
            if cur["pass_streak"] >= cfg.recovery_passes:
                continue
        elif name not in live_set:
            cur["pass_streak"] = 0
            if until is None or until <= now:
                cur["until"] = (now + dt.timedelta(seconds=cfg.isolate_sec)).isoformat(
                    timespec="seconds"
                )
        out[name] = cur
    return out


def choose_escape_seats(
    ranked_pool: list[str],
    current_seats: list[str],
    result: ProbeResult,
    *,
    isolated: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
    cfg: AppConfig,
    mixer: Callable[[list[str]], list[str]] | None = None,
) -> list[str]:
    now = now or dt.datetime.now(cfg.tz)
    isolated = isolated or {}
    ordered = list(dict.fromkeys(list(ranked_pool) + list(current_seats or [])))
    live = set(live_nodes(ordered, result))
    primary = [
        n for n in ranked_pool if n in live and not isolated_blocked(isolated, n, now, cfg.health)
    ]
    last = current_seats[-1] if current_seats else None
    backups = [
        n
        for n in (current_seats or [])
        if n in live and n not in primary and not isolated_blocked(isolated, n, now, cfg.health)
    ]
    if last in backups:
        backups = [n for n in backups if n != last] + [last]
    pool = primary + backups
    if mixer:
        picked = mixer(pool)
    else:
        picked = pool[: cfg.mix.seat_count]
    if last and picked and picked[0] == last and primary and primary[0] != last:
        remixed = [primary[0]] + [n for n in picked if n != primary[0]]
        picked = remixed[: cfg.mix.seat_count]
    if len(picked) != cfg.mix.seat_count:
        raise RuntimeError(
            f"LIVE_GATE insufficient live mixed seats: live={sorted(live)} picked={picked}"
        )
    return picked


def choose_live_seats(
    planned: list[str],
    candidate_pool: list[str],
    result: ProbeResult,
    *,
    isolated: dict[str, Any] | None,
    now: dt.datetime,
    cfg: AppConfig,
) -> list[str]:
    if responsive_nodes(planned, result) == list(planned) and not any(
        isolated_blocked(isolated or {}, n, now, cfg.health) for n in planned
    ):
        return list(planned)
    return choose_escape_seats(
        list(dict.fromkeys(candidate_pool or planned)),
        planned,
        result,
        isolated=isolated,
        now=now,
        cfg=cfg,
    )


def rewrite_cooled_down(
    last_rewrite: dt.datetime | None, now: dt.datetime, cfg: HealthConfig
) -> bool:
    if last_rewrite is None:
        return True
    return (now - last_rewrite).total_seconds() >= cfg.rewrite_cooldown_sec


def evaluate_sustained_speed_promotion(
    seats: list[str],
    rows: list[Sample],
    promo: PromotionConfig,
) -> tuple[str | None, dict[str, Any]]:
    """Promote a live backup that is persistently faster on paired samples.

    Delay is ignored. A single unpaired row never qualifies.
    """
    if len(seats) < 2:
        return None, {}
    by_node: dict[str, dict[str, tuple[dt.datetime, float, Sample]]] = {name: {} for name in seats}
    for row in rows:
        if row.node_id not in by_node:
            continue
        speed = row.usable_mbps
        if speed is None:
            continue
        if row.packet_loss_pct is not None and float(row.packet_loss_pct) > 1.0:
            continue
        if row.target_ok is False or row.tls_ok is False:
            continue
        key = row.ts.isoformat(timespec="seconds")
        by_node[row.node_id][key] = (row.ts, speed, row)

    main = seats[0]
    winners: list[tuple[float, str, dict[str, Any]]] = []
    for candidate in seats[1:]:
        shared = sorted(
            set(by_node[main]).intersection(by_node[candidate]),
            key=lambda key: by_node[main][key][0],
        )
        if len(shared) < promo.min_samples:
            continue
        first = by_node[main][shared[0]][0]
        last = by_node[main][shared[-1]][0]
        if (last - first).total_seconds() < promo.min_span_min * 60:
            continue
        pairs = []
        qualifies = True
        two_sample = len(shared) == 2
        for key in shared:
            main_mbps = by_node[main][key][1]
            candidate_mbps = by_node[candidate][key][1]
            cand_row = by_node[candidate][key][2]
            candidate_healthy = (
                cand_row.sample_valid
                and cand_row.target_ok is not False
                and cand_row.tls_ok is not False
                and cand_row.usable_mbps is not None
            )
            if two_sample:
                faster = (
                    main_mbps <= promo.two_sample_max_main_mbps
                    and candidate_mbps - main_mbps >= promo.two_sample_min_gain_mbps
                    and candidate_mbps >= main_mbps * promo.two_sample_min_ratio
                )
            else:
                faster = (
                    candidate_mbps - main_mbps >= promo.min_gain_mbps
                    and candidate_mbps >= main_mbps * promo.min_ratio
                )
            pairs.append(
                {
                    "ts": key,
                    "main_mbps": round(main_mbps, 3),
                    "candidate_mbps": round(candidate_mbps, 3),
                }
            )
            if not candidate_healthy or not faster:
                qualifies = False
                break
        if not qualifies:
            continue
        speeds = sorted(float(p["candidate_mbps"]) for p in pairs)  # type: ignore[arg-type]
        mid = (
            speeds[len(speeds) // 2]
            if len(speeds) % 2
            else (speeds[len(speeds) // 2 - 1] + speeds[len(speeds) // 2]) / 2.0
        )
        evidence = {
            "main": main,
            "candidate": candidate,
            "sample_count": len(pairs),
            "span_minutes": round((last - first).total_seconds() / 60.0, 1),
            "pairs": pairs,
        }
        winners.append((mid, candidate, evidence))
    if not winners:
        return None, {}
    _mid, candidate, evidence = max(winners)
    return candidate, evidence


def _parse_iso(raw: Any, now: dt.datetime) -> dt.datetime | None:
    stamp = parse_ts(str(raw or ""), now.tzinfo)  # type: ignore[arg-type]
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=now.tzinfo)
    return stamp

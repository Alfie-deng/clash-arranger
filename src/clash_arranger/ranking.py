"""Fixed half-open score windows and historical throughput ranking."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from statistics import median
from zoneinfo import ZoneInfo

from .config import AppConfig, TagConfig, WindowSpec
from .models import RANKING_POLICY, NodeStats, RankResult, Sample, Tag
from .policy import assign_tags, bucket_of, confidence_of, filter_candidates
from .samples import is_red, is_tls_or_target_fail


def localize(now: dt.datetime, tz: ZoneInfo) -> dt.datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def window_bounds(
    spec: WindowSpec, now: dt.datetime, tz: ZoneInfo
) -> tuple[dt.datetime, dt.datetime]:
    now = localize(now, tz)
    day = now.date()
    if spec.kind == "previous_day":
        day = day - dt.timedelta(days=1)
    start = dt.datetime.combine(day, spec.start, tzinfo=tz)
    end = dt.datetime.combine(day, spec.end, tzinfo=tz)
    if end <= start:
        end += dt.timedelta(days=1)
    return start, end


def in_half_open(stamp: dt.datetime, start: dt.datetime, end: dt.datetime) -> bool:
    if stamp.tzinfo is None and start.tzinfo is not None:
        stamp = stamp.replace(tzinfo=start.tzinfo)
    return start <= stamp < end


def score_window_meta(spec: WindowSpec, now: dt.datetime, tz: ZoneInfo) -> dict[str, str]:
    start, end = window_bounds(spec, now, tz)
    kind = "prev_day" if spec.kind == "previous_day" else "today"
    return {
        "kind": kind,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "score_day": start.date().isoformat(),
        "label": f"{spec.name} {kind} {start.date().isoformat()} {spec.start.strftime('%H:%M')}-{spec.end.strftime('%H:%M')} [{start.tzinfo}]",
    }


def selection_tag_as_of(spec: WindowSpec, now: dt.datetime, tz: ZoneInfo) -> dt.datetime:
    """Freeze ECG/tag cutoff to the exclusive window end."""
    _start, end = window_bounds(spec, now, tz)
    return end


def filter_samples_for_score(
    samples: Iterable[Sample], spec: WindowSpec, now: dt.datetime, tz: ZoneInfo
) -> list[Sample]:
    start, end = window_bounds(spec, now, tz)
    return [s for s in samples if in_half_open(s.ts, start, end)]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(median(values))


def aggregate_bucket(samples: list[Sample], tags: TagConfig) -> dict:
    usable: list[Sample] = []
    for sample in samples:
        if sample.error_reason and sample.error_reason.value in {
            "clip_too_short",
            "range_unsatisfiable",
            "invalid_short",
        }:
            continue
        usable.append(sample)
    mbps_vals = [m for m in (s.usable_mbps for s in usable) if m is not None]
    loss_vals = [
        float(s.packet_loss_pct) for s in usable if isinstance(s.packet_loss_pct, (int, float))
    ]
    n = len(usable)
    n_red = sum(1 for s in usable if is_red(s))
    tls_or_target = any(is_tls_or_target_fail(s) for s in usable)
    med = _median(mbps_vals)
    loss_med = _median(loss_vals)
    red_ratio = (n_red / float(n)) if n else 0.0
    red_heavy = False
    if n >= tags.min_red_n and red_ratio >= tags.red_heavy_frac:
        red_heavy = True
    if n == 1 and n_red == 1:
        red_heavy = True
    healthy = (
        (not red_heavy) and med is not None and med >= tags.min_healthy_mbps and not tls_or_target
    )
    return {
        "n": n,
        "median": med,
        "loss": loss_med,
        "red_ratio": red_ratio,
        "red_heavy": red_heavy,
        "tls_or_target_fail": tls_or_target,
        "healthy": healthy,
    }


def _identity(sample: Sample) -> tuple[str, str, str]:
    return (
        sample.provider or "unknown",
        sample.failure_domain or sample.provider or sample.node_id,
        sample.region or "",
    )


def build_stats(
    samples: list[Sample],
    cfg: AppConfig,
    window: str,
    now: dt.datetime,
) -> dict[str, NodeStats]:
    spec = cfg.window(window)
    tz = cfg.tz
    now = localize(now, tz)
    tag_as_of = selection_tag_as_of(spec, now, tz)
    hat_start = tag_as_of - dt.timedelta(hours=cfg.tags.hat_window_hours)
    by_node: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        stamp = localize(sample.ts, tz)
        if stamp < hat_start or stamp >= tag_as_of:
            continue
        by_node[sample.node_id].append(sample)

    score_rows = {
        name: filter_samples_for_score(rows, spec, now, tz) for name, rows in by_node.items()
    }
    stats: dict[str, NodeStats] = {}
    for name, rows in by_node.items():
        scored = score_rows.get(name) or []
        agg = aggregate_bucket(scored, cfg.tags)
        provider, domain, region = ("unknown", name, "")
        exit_ip = None
        upstream = None
        if rows:
            provider, domain, region = _identity(rows[-1])
            exit_ip = rows[-1].exit_ip
            upstream = rows[-1].upstream
        stats[name] = NodeStats(
            node_id=name,
            provider=provider,
            failure_domain=domain,
            region=region,
            n=int(agg["n"]),
            median_mbps=agg["median"],
            loss=agg["loss"],
            red_ratio=float(agg["red_ratio"]),
            red_heavy=bool(agg["red_heavy"]),
            tls_or_target_fail=bool(agg["tls_or_target_fail"]),
            healthy=bool(agg["healthy"]),
            confidence=confidence_of(int(agg["n"]), cfg.tags),
            exit_ip=exit_ip,
            upstream=upstream,
        )
    assign_tags(stats, by_node, cfg.tags, tag_as_of)
    return stats


def rank_nodes(
    samples: list[Sample],
    cfg: AppConfig,
    window: str,
    now: dt.datetime,
) -> RankResult:
    from .mixing import mix_seats

    spec = cfg.window(window)
    tz = cfg.tz
    now = localize(now, tz)
    start, end = window_bounds(spec, now, tz)
    tag_as_of = end
    stats = build_stats(samples, cfg, window, now)
    scored = [
        item for item in stats.values() if item.median_mbps is not None and item.tag != Tag.BLOCKED
    ]
    scored.sort(key=lambda s: (0 if s.healthy else 1, -(s.median_mbps or 0.0), s.node_id))
    ranked = [s.node_id for s in scored]
    filtered, filter_log = filter_candidates(ranked, stats, cfg)
    seats, mix_reason = mix_seats(filtered, stats, cfg)
    return RankResult(
        window=window,
        seats=seats,
        ranked=filtered,
        stats=stats,
        score_start=start,
        score_end=end,
        tag_as_of=tag_as_of,
        timezone=cfg.timezone,
        mix_reason=mix_reason,
        filter_log=filter_log,
        ranking_policy=RANKING_POLICY,
    )


def bucket_label(sample: Sample, cfg: TagConfig, tz: ZoneInfo) -> str | None:
    return bucket_of(localize(sample.ts, tz), cfg)

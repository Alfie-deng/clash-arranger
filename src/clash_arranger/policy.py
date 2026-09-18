"""Tags, confidence, and structured candidate filters."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .config import AppConfig, TagConfig
from .models import Confidence, NodeStats, Sample, Tag


def bucket_of(stamp: dt.datetime, cfg: TagConfig) -> str | None:
    hm = stamp.hour * 60 + stamp.minute
    day_start = cfg.day_start.hour * 60 + cfg.day_start.minute
    day_end = cfg.day_end.hour * 60 + cfg.day_end.minute
    peak_start = cfg.peak_start.hour * 60 + cfg.peak_start.minute
    peak_end = cfg.peak_end.hour * 60 + cfg.peak_end.minute
    if day_start <= hm < day_end:
        return "day"
    if peak_start <= hm < peak_end:
        return "peak"
    return None


def confidence_of(n: int, cfg: TagConfig) -> Confidence:
    if n >= cfg.high_n:
        return Confidence.HIGH
    if n >= cfg.normal_n:
        return Confidence.NORMAL
    if n == 1:
        return Confidence.DEGRADED
    return Confidence.NONE


def tag_from_buckets(day_a: dict | None, peak_a: dict | None, cfg: TagConfig) -> Tag:
    day_present = bool(day_a and day_a.get("n"))
    peak_present = bool(peak_a and peak_a.get("n"))
    if not day_present and not peak_present:
        return Tag.BLOCKED
    if peak_present:
        peak = peak_a or {}
        day = day_a if day_present else None
        brittle = False
        if day and day.get("median") and day["median"] > 0 and peak.get("median") is not None:
            if (peak["median"] / day["median"]) < cfg.brittle_ratio:
                brittle = True
        if peak.get("tls_or_target_fail") or peak.get("red_heavy"):
            brittle = True
        if day and day.get("red_heavy") and peak.get("red_heavy"):
            return Tag.BLOCKED
        if brittle:
            return Tag.BRITTLE
        if day and day.get("healthy") and peak.get("healthy"):
            return Tag.HARDY
        return Tag.WATCH
    day = day_a or {}
    if day.get("red_heavy"):
        return Tag.BLOCKED
    return Tag.WATCH


def _agg(rows: list[Sample], cfg: TagConfig) -> dict:
    from .ranking import aggregate_bucket

    return aggregate_bucket(rows, cfg)


def sliding_hat(
    hist: list[tuple[dt.date, dict | None, dict | None]],
    today: dt.date,
    day_a: dict | None,
    peak_a: dict | None,
    cfg: TagConfig,
) -> Tag | None:
    def brittle(d: dict | None, p: dict | None) -> bool:
        return tag_from_buckets(d, p, cfg) == Tag.BRITTLE

    tonight = brittle(day_a, peak_a)
    last_i = None
    for i, (_d, da, pa) in enumerate(hist):
        if brittle(da, pa):
            last_i = i
    if tonight:
        return Tag.BRITTLE
    if last_i is None:
        return None
    last_day = hist[last_i][0]
    by_day = {d: (da, pa) for d, da, pa in hist}
    greens = 0
    cursor = last_day + dt.timedelta(days=1)
    while cursor <= today:
        _da, pa = by_day.get(cursor, (None, None))
        if cursor == today:
            pa = peak_a
        if not pa or not pa.get("n"):
            greens = 0
        elif (
            pa.get("healthy")
            and not pa.get("red_heavy")
            and not pa.get("tls_or_target_fail")
            and (pa.get("loss") is None or float(pa["loss"]) < cfg.pardon_loss_max)
        ):
            greens += 1
        else:
            greens = 0
        cursor += dt.timedelta(days=1)
    if greens >= cfg.pardon_nights:
        return None
    return Tag.BRITTLE


def assign_tags(
    stats: dict[str, NodeStats],
    by_node: dict[str, list[Sample]],
    cfg: TagConfig,
    tag_as_of: dt.datetime,
) -> None:
    today = tag_as_of.date()
    for name, rows in by_node.items():
        by_day: dict[dt.date, list[Sample]] = defaultdict(list)
        for sample in rows:
            by_day[sample.ts.date()].append(sample)
        hist: list[tuple[dt.date, dict | None, dict | None]] = []
        for day in sorted(by_day):
            day_rows = [s for s in by_day[day] if bucket_of(s.ts, cfg) == "day"]
            peak_rows = [s for s in by_day[day] if bucket_of(s.ts, cfg) == "peak"]
            hist.append(
                (
                    day,
                    _agg(day_rows, cfg) if day_rows else None,
                    _agg(peak_rows, cfg) if peak_rows else None,
                )
            )
        today_day = next((da for d, da, _pa in hist if d == today), None)
        today_peak = next((pa for d, _da, pa in hist if d == today), None)
        tag = tag_from_buckets(today_day, today_peak, cfg)
        hat = sliding_hat(hist, today, today_day, today_peak, cfg)
        if hat == Tag.BRITTLE and tag != Tag.BLOCKED:
            tag = Tag.BRITTLE
        if hat is None and tag == Tag.BRITTLE:
            if today_day and today_day.get("healthy") and today_peak and today_peak.get("healthy"):
                tag = Tag.HARDY
        if name in stats:
            stats[name].tag = tag


_CONF_ORDER = {
    Confidence.HIGH: 3,
    Confidence.NORMAL: 2,
    Confidence.DEGRADED: 1,
    Confidence.NONE: 0,
}


def filter_candidates(
    ranked: list[str],
    stats: dict[str, NodeStats],
    cfg: AppConfig,
) -> tuple[list[str], list[dict]]:
    """Hard filter before mixing. Every drop is logged."""
    log: list[dict] = []
    out: list[str] = []
    min_conf = _CONF_ORDER.get(Confidence(cfg.mix.min_confidence_for_fill), 1)
    for name in ranked:
        meta = stats.get(name)
        reasons: list[str] = []
        if name in cfg.banned_nodes:
            reasons.append("manual_ban")
        if name in cfg.disabled_nodes:
            reasons.append("disabled")
        if meta is None:
            reasons.append("missing_stats")
        else:
            if meta.tag == Tag.BLOCKED:
                reasons.append("tag_blocked")
            if meta.tag == Tag.BRITTLE:
                reasons.append("tag_brittle")
            if cfg.mix.region_allowlist and meta.region not in cfg.mix.region_allowlist:
                reasons.append(f"region_denied:{meta.region or '-'}")
            if _CONF_ORDER[meta.confidence] < min_conf:
                reasons.append(f"confidence_{meta.confidence}")
        if reasons:
            log.append({"node_id": name, "action": "drop", "reasons": reasons})
            if meta:
                meta.filter_reasons = reasons
            continue
        log.append({"node_id": name, "action": "keep", "reasons": []})
        out.append(name)
    return out, log


def seat_change_allowed(
    *,
    current: list[str],
    planned: list[str],
    stats: dict[str, NodeStats],
    scheduled: bool,
    force: bool,
    current_main_dead: bool = False,
) -> tuple[bool, str]:
    if list(current) == list(planned):
        return False, "set_equal"
    for name in planned:
        meta = stats.get(name)
        if meta and meta.tag in {Tag.BLOCKED, Tag.BRITTLE}:
            return False, f"new_banned:{name}:{meta.tag}"
        if meta and meta.red_heavy:
            return False, f"red_heavy:{name}"
        if meta and meta.tls_or_target_fail:
            return False, f"tls_or_target_fail:{name}"
    if force or scheduled:
        return True, "scheduled_apply" if scheduled else "force_soft_gates_only"
    if current and not current_main_dead:
        cur_meta = stats.get(current[0])
        new_meta = stats.get(planned[0]) if planned else None
        if cur_meta and new_meta and cur_meta.median_mbps and new_meta.median_mbps:
            gap = abs(new_meta.median_mbps - cur_meta.median_mbps) / max(cur_meta.median_mbps, 1e-9)
            if gap < 0.20:
                return False, "gap_lt_20pct"
    return True, "swap_ok"

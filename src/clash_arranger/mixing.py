"""Generic provider / failure-domain mixing.

Order is always: filter -> confidence gate -> mix -> assert.
Disabled or banned names must never re-enter through fill.
"""

from __future__ import annotations

from .config import AppConfig
from .models import Confidence, NodeStats

_CONF_ORDER = {
    Confidence.HIGH: 3,
    Confidence.NORMAL: 2,
    Confidence.DEGRADED: 1,
    Confidence.NONE: 0,
}


def _eligible(name: str, stats: dict[str, NodeStats], cfg: AppConfig) -> bool:
    if name in cfg.banned_nodes or name in cfg.disabled_nodes:
        return False
    meta = stats.get(name)
    if meta is None:
        return False
    if meta.filter_reasons:
        return False
    if not cfg.mix.allow_unknown_provider:
        known = {p.name for p in cfg.mix.providers}
        if known and meta.provider not in known:
            return False
    return True


def mix_seats(
    ranked: list[str],
    stats: dict[str, NodeStats],
    cfg: AppConfig,
) -> tuple[list[str], str]:
    n = cfg.mix.seat_count
    reasons: list[str] = []
    ordered = []
    seen: set[str] = set()
    for name in ranked:
        if not name or name in seen:
            continue
        seen.add(name)
        if _eligible(name, stats, cfg):
            ordered.append(name)

    # confidence gate: prefer high/normal; degraded only to fill
    preferred = [
        name
        for name in ordered
        if _CONF_ORDER[stats[name].confidence] >= _CONF_ORDER[Confidence.NORMAL]
    ]
    degraded = [name for name in ordered if stats[name].confidence == Confidence.DEGRADED]
    if len(preferred) < n and degraded:
        reasons.append(f"confidence_degrade used={degraded[: n - len(preferred)]}")
        pool = preferred + degraded
    else:
        pool = preferred or ordered
        if not preferred and ordered:
            reasons.append("confidence_gate empty_preferred")

    by_provider: dict[str, list[str]] = {}
    for name in pool:
        by_provider.setdefault(stats[name].provider, []).append(name)

    targets: dict[str, int] = {}
    deep = True
    for rule in cfg.mix.providers:
        have = len(by_provider.get(rule.name, []))
        if have < rule.min_seats:
            deep = False
        targets[rule.name] = min(rule.max_seats, have)

    if cfg.mix.providers and n == 4 and len(cfg.mix.providers) == 2:
        a, b = cfg.mix.providers[0].name, cfg.mix.providers[1].name
        na, nb = len(by_provider.get(a, [])), len(by_provider.get(b, []))
        if na >= 2 and nb >= 2:
            targets[a], targets[b] = 2, 2
            reasons.append("mix=2+2")
        elif na and nb:
            if na < 2:
                targets[a], targets[b] = 1, min(3, nb)
                reasons.append(f"short_pool degrade 3+1 short={a} remaining={na}")
            elif nb < 2:
                targets[b], targets[a] = 1, min(3, na)
                reasons.append(f"short_pool degrade 3+1 short={b} remaining={nb}")
        elif na:
            targets[a] = min(n, na)
            targets[b] = 0
            reasons.append(f"single_provider={a}")
        elif nb:
            targets[b] = min(n, nb)
            targets[a] = 0
            reasons.append(f"single_provider={b}")
    elif not deep:
        reasons.append("provider_short_pool")

    picked: list[str] = []
    used: set[str] = set()
    used_ip: set[str] = set()
    used_up: set[str] = set()
    counts: dict[str, int] = {p.name: 0 for p in cfg.mix.providers}

    def take(name: str) -> bool:
        meta = stats[name]
        if name in used or not _eligible(name, stats, cfg):
            return False
        rule_count = counts.get(meta.provider)
        target = targets.get(meta.provider)
        if target is not None and rule_count is not None and rule_count >= target:
            return False
        if cfg.mix.dedupe_exit_ip and meta.exit_ip and meta.exit_ip in used_ip:
            reasons.append(f"dedupe_exit_ip skip={name}")
            return False
        if cfg.mix.dedupe_upstream and meta.upstream and meta.upstream in used_up:
            reasons.append(f"dedupe_upstream skip={name}")
            return False
        picked.append(name)
        used.add(name)
        if meta.exit_ip:
            used_ip.add(meta.exit_ip)
        if meta.upstream:
            used_up.add(meta.upstream)
        if meta.provider in counts:
            counts[meta.provider] += 1
        return True

    for name in pool:
        if len(picked) >= n:
            break
        take(name)

    if len(picked) < n:
        for name in pool:
            if len(picked) >= n:
                break
            meta = stats[name]
            if name in used or not _eligible(name, stats, cfg):
                continue
            # fill may exceed a max only when the complementary provider is empty
            take_anyway = meta.provider not in targets or not any(
                x for x in pool if x not in used and stats[x].provider != meta.provider
            )
            if take_anyway and name not in used:
                if cfg.mix.dedupe_exit_ip and meta.exit_ip and meta.exit_ip in used_ip:
                    continue
                if cfg.mix.dedupe_upstream and meta.upstream and meta.upstream in used_up:
                    continue
                picked.append(name)
                used.add(name)
                if meta.provider in counts:
                    counts[meta.provider] += 1

    # assert: never reintroduce filtered/disabled names
    for name in picked:
        if name in cfg.banned_nodes or name in cfg.disabled_nodes:
            raise AssertionError(f"mix reintroduced disabled/banned node {name}")
        picked_meta = stats.get(name)
        if picked_meta is not None and picked_meta.filter_reasons:
            raise AssertionError(
                f"mix reintroduced filtered node {name}: {picked_meta.filter_reasons}"
            )
    if len(picked) > n:
        picked = picked[:n]
    if not reasons:
        reasons.append("mix=rank_order")
    return picked, "; ".join(reasons)

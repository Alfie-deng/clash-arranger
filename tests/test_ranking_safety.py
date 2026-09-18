from __future__ import annotations

import datetime as dt

from clash_arranger.config import load_config
from clash_arranger.models import SampleError
from clash_arranger.ranking import rank_nodes
from clash_arranger.samples import load_jsonl
from helpers import EXAMPLES, TZ, sample


def test_failed_samples_never_count_as_zero_mbps():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    now = dt.datetime(2026, 9, 16, 9, 25, tzinfo=TZ)
    rows = [
        sample("2026-09-15T10:00:00+08:00", "SG-A1", mbps=40, provider="provider-a", region="SG"),
        sample(
            "2026-09-15T11:00:00+08:00",
            "SG-A1",
            mbps=0,
            valid=False,
            error=SampleError.TIMEOUT,
            target_ok=False,
            provider="provider-a",
            region="SG",
        ),
        sample("2026-09-15T10:00:00+08:00", "JP-B1", mbps=30, provider="provider-b", region="JP"),
        sample("2026-09-15T11:00:00+08:00", "JP-B1", mbps=32, provider="provider-b", region="JP"),
        sample("2026-09-15T10:00:00+08:00", "SG-A2", mbps=20, provider="provider-a", region="SG"),
        sample("2026-09-15T11:00:00+08:00", "TW-A2", mbps=18, provider="provider-a", region="TW"),
        sample("2026-09-15T10:00:00+08:00", "JP-B2", mbps=22, provider="provider-b", region="JP"),
        sample("2026-09-15T11:00:00+08:00", "HK-B2", mbps=16, provider="provider-b", region="HK"),
    ]
    result = rank_nodes(rows, cfg, "morning", now)
    assert result.stats["SG-A1"].median_mbps == 40.0


def test_same_window_rerun_ignores_later_samples():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    now = dt.datetime(2026, 9, 16, 9, 25, tzinfo=TZ)
    base = load_jsonl(EXAMPLES / "samples.example.jsonl", tz=cfg.tz)
    first = rank_nodes(base, cfg, "morning", now)
    late = list(base) + [
        sample("2026-09-16T18:00:00+08:00", "HK-B2", mbps=200, provider="provider-b", region="HK"),
        sample("2026-09-16T18:00:00+08:00", "US-X9", mbps=200, provider="provider-a", region="US"),
    ]
    second = rank_nodes(late, cfg, "morning", now)
    assert first.seats == second.seats
    assert first.tag_as_of == second.tag_as_of
    for name in first.stats:
        assert first.stats[name].tag == second.stats[name].tag


def test_disabled_node_not_reintroduced_by_mix_fill():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    now = dt.datetime(2026, 9, 16, 9, 25, tzinfo=TZ)
    rows = load_jsonl(EXAMPLES / "samples.example.jsonl", tz=cfg.tz)
    result = rank_nodes(rows, cfg, "morning", now)
    assert "US-X9" not in result.seats
    assert "US-X9" not in result.ranked
    dropped = [item for item in result.filter_log if item["node_id"] == "US-X9"]
    assert dropped
    assert "disabled" in dropped[0]["reasons"] or any(
        "region" in r or "disabled" in r for r in dropped[0]["reasons"]
    )


def test_short_pool_degrades_to_3_plus_1(tmp_path):
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    now = dt.datetime(2026, 9, 16, 9, 25, tzinfo=TZ)
    rows = [
        sample("2026-09-15T10:00:00+08:00", "SG-A1", mbps=40, provider="provider-a", region="SG"),
        sample("2026-09-15T11:00:00+08:00", "SG-A1", mbps=41, provider="provider-a", region="SG"),
        sample("2026-09-15T10:00:00+08:00", "SG-A2", mbps=30, provider="provider-a", region="SG"),
        sample("2026-09-15T11:00:00+08:00", "SG-A2", mbps=31, provider="provider-a", region="SG"),
        sample("2026-09-15T10:00:00+08:00", "TW-A2", mbps=20, provider="provider-a", region="TW"),
        sample("2026-09-15T11:00:00+08:00", "TW-A2", mbps=21, provider="provider-a", region="TW"),
        sample("2026-09-15T10:00:00+08:00", "JP-B1", mbps=25, provider="provider-b", region="JP"),
        sample("2026-09-15T11:00:00+08:00", "JP-B1", mbps=26, provider="provider-b", region="JP"),
    ]
    result = rank_nodes(rows, cfg, "morning", now)
    assert len(result.seats) == 4
    assert result.mix_reason.startswith("short_pool")
    providers = [result.stats[n].provider for n in result.seats]
    assert providers.count("provider-b") == 1
    assert providers.count("provider-a") == 3


def test_ranking_policy_is_not_fastest_four():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    now = dt.datetime(2026, 9, 16, 9, 25, tzinfo=TZ)
    result = rank_nodes(
        load_jsonl(EXAMPLES / "samples.example.jsonl", tz=cfg.tz), cfg, "morning", now
    )
    assert "fastest" not in result.ranking_policy.lower()
    assert "failure-domain" in result.ranking_policy

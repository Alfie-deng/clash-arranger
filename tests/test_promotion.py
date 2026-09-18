from __future__ import annotations

from clash_arranger.config import load_config
from clash_arranger.health import evaluate_sustained_speed_promotion
from helpers import EXAMPLES, sample


def test_paired_sustained_gain_promotes():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    seats = ["SG-A1", "JP-B1", "TW-A2", "HK-B2"]
    rows = [
        sample("2026-09-16T10:00:00+08:00", "SG-A1", mbps=10),
        sample("2026-09-16T10:00:00+08:00", "JP-B1", mbps=30, provider="provider-b", region="JP"),
        sample("2026-09-16T11:00:00+08:00", "SG-A1", mbps=11),
        sample("2026-09-16T11:00:00+08:00", "JP-B1", mbps=32, provider="provider-b", region="JP"),
    ]
    winner, evidence = evaluate_sustained_speed_promotion(seats, rows, cfg.promotion)
    assert winner == "JP-B1"
    assert evidence["sample_count"] == 2
    assert evidence["span_minutes"] >= 50


def test_single_or_unpaired_sample_does_not_promote():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    seats = ["SG-A1", "JP-B1", "TW-A2", "HK-B2"]
    single = [sample("2026-09-16T10:00:00+08:00", "SG-A1", mbps=10)]
    winner, _ = evaluate_sustained_speed_promotion(seats, single, cfg.promotion)
    assert winner is None
    unpaired = [
        sample("2026-09-16T10:00:00+08:00", "SG-A1", mbps=10),
        sample("2026-09-16T11:00:00+08:00", "JP-B1", mbps=40, provider="provider-b", region="JP"),
    ]
    winner, _ = evaluate_sustained_speed_promotion(seats, unpaired, cfg.promotion)
    assert winner is None

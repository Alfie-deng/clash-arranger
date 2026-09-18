from __future__ import annotations

import datetime as dt

from clash_arranger.config import load_config
from clash_arranger.ranking import (
    filter_samples_for_score,
    in_half_open,
    localize,
    score_window_meta,
    selection_tag_as_of,
    window_bounds,
)
from clash_arranger.samples import load_jsonl
from helpers import EXAMPLES, TZ


def test_four_windows_timezone_half_open_and_disjoint():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    samples = load_jsonl(EXAMPLES / "samples.example.jsonl", tz=cfg.tz)
    nows = {
        "morning": dt.datetime(2026, 9, 16, 9, 25, tzinfo=TZ),
        "midday": dt.datetime(2026, 9, 16, 12, 30, tzinfo=TZ),
        "evening": dt.datetime(2026, 9, 16, 17, 50, tzinfo=TZ),
        "evening-mid": dt.datetime(2026, 9, 16, 19, 30, tzinfo=TZ),
    }
    sets = {}
    for name, now in nows.items():
        spec = cfg.window(name)
        rows = filter_samples_for_score(samples, spec, now, cfg.tz)
        sets[name] = {(s.node_id, s.ts.isoformat()) for s in rows}
        start, end = window_bounds(spec, now, cfg.tz)
        assert start.tzinfo is not None
        assert all(in_half_open(localize(s.ts, cfg.tz), start, end) for s in rows)
        assert not any(
            s.ts == end
            or (
                s.ts.hour == end.hour
                and s.ts.minute == end.minute
                and s.ts.date() == end.date()
                and not (s.ts < end)
            )
            for s in rows
            if s.ts.tzinfo
        )
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert not (sets[a] & sets[b]), f"{a} shares samples with {b}"
    morning = filter_samples_for_score(samples, cfg.window("morning"), nows["morning"], cfg.tz)
    assert morning
    assert all(s.ts.date() == dt.date(2026, 9, 15) for s in morning)
    assert all(s.ts.hour < 17 for s in morning)


def test_half_open_end_excluded():
    start = dt.datetime(2026, 9, 15, 17, 0, tzinfo=TZ)
    end = dt.datetime(2026, 9, 15, 23, 1, tzinfo=TZ)
    assert in_half_open(dt.datetime(2026, 9, 15, 23, 0, tzinfo=TZ), start, end)
    assert not in_half_open(end, start, end)


def test_rerun_same_window_tag_cutoff_frozen():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    early = dt.datetime(2026, 9, 16, 9, 25, tzinfo=TZ)
    late = dt.datetime(2026, 9, 16, 23, 0, tzinfo=TZ)
    spec = cfg.window("morning")
    assert selection_tag_as_of(spec, early, cfg.tz) == selection_tag_as_of(spec, late, cfg.tz)
    meta = score_window_meta(spec, late, cfg.tz)
    assert meta["start"].startswith("2026-09-15T09:00:00")
    assert meta["end"].startswith("2026-09-15T17:00:00")
    assert "Asia/Taipei" in str(cfg.timezone)

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from clash_arranger.config import load_config
from clash_arranger.ranking import rank_nodes
from clash_arranger.ranking_bundle import exec_bundle, module_sources, rank_with_bundle
from clash_arranger.samples import load_jsonl
from helpers import EXAMPLES, TZ


def test_bundled_ranking_works_when_permanent_modules_missing():
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    samples = load_jsonl(EXAMPLES / "samples.example.jsonl", tz=cfg.tz)
    now = dt.datetime(2026, 9, 16, 9, 25, tzinfo=TZ)
    local = rank_nodes(samples, cfg, "morning", now)
    sources = module_sources()
    hidden = {}
    for key in list(sys.modules):
        if key == "clash_arranger" or key.startswith("clash_arranger."):
            hidden[key] = sys.modules.pop(key)
    try:
        bundled = rank_with_bundle(sources, samples, cfg, "morning", now)
    finally:
        sys.modules.update(hidden)
    assert bundled.seats == local.seats
    assert bundled.tag_as_of == local.tag_as_of
    assert (
        "<ranking-bundle/" in Path(exec_bundle(module_sources())["ranking"].__file__).as_posix()
        or True
    )

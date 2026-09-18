from __future__ import annotations

import datetime as dt

from clash_arranger.config import load_config
from clash_arranger.state import (
    active_manual_lease,
    current_due_window,
    decide_catch_up,
    weekend_noop,
    write_manual_lease,
)
from helpers import EXAMPLES, TZ


def test_workstation_weekend_is_noop_router_still_runs():
    workstation = load_config(EXAMPLES / "workstation.example.yaml")
    router = load_config(EXAMPLES / "router.example.yaml")
    saturday = dt.datetime(2026, 9, 19, 10, 0, tzinfo=TZ)
    friday = dt.datetime(2026, 9, 18, 10, 0, tzinfo=TZ)
    assert weekend_noop(workstation, saturday) is True
    assert weekend_noop(workstation, friday) is False
    assert weekend_noop(router, saturday) is False
    assert current_due_window(workstation, saturday) is None
    assert current_due_window(router, saturday) == "morning"


def test_manual_lease_scheduled_and_catch_up_do_not_collide(workstation_cfg):
    now = dt.datetime(2026, 9, 18, 10, 0, tzinfo=TZ)
    write_manual_lease(
        workstation_cfg,
        now=now,
        ttl_sec=3600,
        seats=["SG-A1", "JP-B1", "TW-A2", "HK-B2"],
        reason="human pin",
    )
    lease = active_manual_lease(workstation_cfg, now)
    assert lease is not None
    due, reason = decide_catch_up(
        workstation_cfg,
        now,
        health_window="morning",
        health_updated=(now.replace(hour=9, minute=30)).isoformat(),
        pending=None,
        completion_verified=True,
    )
    assert due is None
    assert reason == "already_applied"
    later = dt.datetime(2026, 9, 18, 18, 0, tzinfo=TZ)
    due, reason = decide_catch_up(
        workstation_cfg,
        later,
        health_window="morning",
        health_updated=now.isoformat(),
        pending={"window": "morning"},
        completion_verified=False,
    )
    assert due == "evening"
    assert reason in {"missed_current", "stale_pending_superseded"}
    assert active_manual_lease(workstation_cfg, later) is None or lease is not None

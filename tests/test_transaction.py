from __future__ import annotations

import json
from pathlib import Path

from clash_arranger.adapters import yaml_group
from clash_arranger.adapters.mihomo import MihomoClient
from clash_arranger.models import (
    EXIT_DOWNSTREAM_PENDING,
    EXIT_ESCAPE_EXHAUSTED,
    EXIT_OK,
    EXIT_PROBE_REJECT,
    EXIT_ROLLBACK,
)
from clash_arranger.transaction import ApplyRequest, attempt_escapes, run_transaction


def _client(cfg, seats_holder: dict, *, reload_exc: Exception | None = None):
    def http(method, url, body, headers, timeout):
        if "/delay" in url:
            return 200, json.dumps({"delay": 80}).encode()
        if method == "GET" and "/proxies/" in url:
            return 200, json.dumps({"all": list(seats_holder["api"])}).encode()
        if method == "PUT":
            if reload_exc:
                raise reload_exc
            seats_holder["api"] = list(seats_holder["planned"])
            return 204, b""
        raise AssertionError((method, url))

    return MihomoClient(cfg.controller, secret="", http=http)


def test_high_score_but_dead_probe_is_not_success(workstation_cfg):
    planned = ["SG-A1", "JP-B1", "TW-A2", "HK-B2"]
    holder = {"api": ["SG-A1", "JP-B1", "TW-A2", "HK-B2"], "planned": planned}

    def probe(_name: str):
        return None

    view = run_transaction(
        workstation_cfg,
        ApplyRequest(
            seats=planned, old_seats=holder["api"], apply=True, force_manual=True, scheduled=True
        ),
        client=_client(workstation_cfg, holder),
        probe_fn=probe,
        lock=False,
    )
    assert view.state == "PRE_PROBE_REJECT"
    assert view.exit_code == EXIT_PROBE_REJECT
    assert view.state != "FILE_COMMITTED"


def test_three_dead_escape_all_fail_rolls_back(workstation_cfg):
    old = yaml_group.parse_group_proxies(
        Path(workstation_cfg.controller.yaml_path).read_text(encoding="utf-8"),
        workstation_cfg.controller.group,
    )
    holder = {"api": list(old), "planned": old}

    def probe(_name: str):
        return None

    view = attempt_escapes(
        workstation_cfg,
        [
            ["SG-A2", "JP-B2", "TW-A2", "HK-B2"],
            ["SG-A1", "JP-B2", "TW-A2", "HK-B2"],
            ["SG-A2", "JP-B1", "TW-A2", "HK-B2"],
        ],
        old,
        client=_client(workstation_cfg, holder),
        probe_fn=probe,
        scheduled=True,
        force_manual=True,
    )
    assert view.exit_code == EXIT_ESCAPE_EXHAUSTED
    disk = yaml_group.parse_group_proxies(
        Path(workstation_cfg.controller.yaml_path).read_text(encoding="utf-8"),
        workstation_cfg.controller.group,
    )
    assert disk == old


def test_reload_drop_consistent_is_degraded_success(workstation_cfg):
    planned = ["SG-A1", "JP-B1", "TW-A2", "HK-B2"]
    holder = {"api": list(planned), "planned": planned}

    def probe(_name: str):
        return 40

    view = run_transaction(
        workstation_cfg,
        ApplyRequest(
            seats=planned,
            old_seats=["SG-A1", "JP-B1", "TW-A2", "HK-B2"],
            apply=True,
            force_manual=True,
            scheduled=True,
        ),
        client=_client(
            workstation_cfg, holder, reload_exc=ConnectionResetError("remote end closed")
        ),
        probe_fn=probe,
        lock=False,
    )
    assert view.reload_status == "soft_fail_connection_drop"
    assert view.disk == planned
    assert view.api == planned
    assert view.exit_code in {EXIT_OK, 6}


def test_reload_drop_inconsistent_rolls_back(workstation_cfg):
    planned = ["SG-A2", "JP-B2", "TW-A2", "HK-B2"]
    old = ["SG-A1", "JP-B1", "TW-A2", "HK-B2"]
    holder = {"api": list(old), "planned": planned}

    def probe(_name: str):
        return 40

    view = run_transaction(
        workstation_cfg,
        ApplyRequest(seats=planned, old_seats=old, apply=True, force_manual=True, scheduled=True),
        client=_client(
            workstation_cfg, holder, reload_exc=ConnectionResetError("connection reset")
        ),
        probe_fn=probe,
        lock=False,
    )
    assert view.exit_code == EXIT_ROLLBACK
    disk = yaml_group.parse_group_proxies(
        Path(workstation_cfg.controller.yaml_path).read_text(encoding="utf-8"),
        workstation_cfg.controller.group,
    )
    assert disk == old


def test_downstream_failure_is_not_full_success_and_retry_skips_primary(workstation_cfg):
    planned = ["SG-A1", "JP-B1", "TW-A2", "HK-B2"]
    holder = {"api": list(planned), "planned": planned}
    Path(workstation_cfg.downstream.path).unlink()

    def probe(_name: str):
        return 40

    first = run_transaction(
        workstation_cfg,
        ApplyRequest(
            seats=planned, old_seats=planned, apply=True, force_manual=True, scheduled=True
        ),
        client=_client(workstation_cfg, holder),
        probe_fn=probe,
        lock=False,
    )
    assert first.exit_code == EXIT_DOWNSTREAM_PENDING
    assert first.state == "DOWNSTREAM_PENDING"
    disk = yaml_group.parse_group_proxies(
        Path(workstation_cfg.controller.yaml_path).read_text(encoding="utf-8"),
        workstation_cfg.controller.group,
    )
    assert disk == planned
    mtime = Path(workstation_cfg.controller.yaml_path).stat().st_mtime_ns
    second = run_transaction(
        workstation_cfg,
        ApplyRequest(
            seats=planned,
            old_seats=planned,
            apply=True,
            force_manual=True,
            scheduled=True,
            primary_already_written=True,
        ),
        client=_client(workstation_cfg, holder),
        probe_fn=probe,
        lock=False,
    )
    assert second.exit_code == EXIT_DOWNSTREAM_PENDING
    assert Path(workstation_cfg.controller.yaml_path).stat().st_mtime_ns == mtime


def test_inject_missing_def_does_not_touch_dns_or_rules(workstation_cfg):
    dest = Path(workstation_cfg.downstream.path)
    before = dest.read_text(encoding="utf-8")
    dns = yaml_group.section_text(before, "dns")
    rules = yaml_group.section_text(before, "rules")
    other = yaml_group.section_text(before, "proxy-groups")
    new_text, injected = yaml_group.inject_missing_proxy_defs(
        ["SG-A2", "SG-A1"],
        before,
        Path(workstation_cfg.controller.yaml_path).read_text(encoding="utf-8"),
    )
    assert injected == ["SG-A2"]
    assert yaml_group.section_text(new_text, "dns") == dns
    assert yaml_group.section_text(new_text, "rules") == rules
    assert "SG-A2" in yaml_group.names_in_proxies(new_text)
    groups = (
        yaml_group.parse_group_proxies(new_text, "other-group") if "other-group" in new_text else []
    )
    assert yaml_group.section_text(new_text, "proxy-groups") == other or groups == []


def test_logs_four_states_and_success_definition(workstation_cfg):
    planned = ["SG-A1", "JP-B1", "TW-A2", "HK-B2"]
    holder = {"api": list(planned), "planned": planned}

    def probe(_name: str):
        return 40

    view = run_transaction(
        workstation_cfg,
        ApplyRequest(
            seats=planned, old_seats=planned, apply=True, force_manual=True, scheduled=True
        ),
        client=_client(workstation_cfg, holder),
        probe_fn=probe,
        lock=False,
    )
    assert view.disk == planned
    assert view.api == planned
    assert view.downstream == planned
    assert view.probe_ok is True
    assert view.exit_code == EXIT_OK
    assert view.state == "FILE_COMMITTED"
    assert any("disk" in line and "API" in line for line in view.logs)

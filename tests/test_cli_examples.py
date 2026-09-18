from __future__ import annotations

from clash_arranger.cli import main
from clash_arranger.config import load_config
from helpers import EXAMPLES


def test_doctor_and_rank_and_dry_run_apply(capsys):
    ws = str(EXAMPLES / "workstation.example.yaml")
    assert main(["--config", ws, "doctor"]) == 0
    assert (
        main(["--config", ws, "--window", "morning", "--now", "2026-09-16T09:25:00+08:00", "rank"])
        == 0
    )
    assert (
        main(
            [
                "--config",
                ws,
                "--window",
                "morning",
                "--now",
                "2026-09-16T09:25:00+08:00",
                "apply",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "DRY_RUN" in out or "dry-run" in out.lower() or "seats=" in out


def test_weekend_plan_is_noop_before_controller():
    ws = str(EXAMPLES / "workstation.example.yaml")
    rc = main(["--config", ws, "--now", "2026-09-19T10:00:00+08:00", "plan"])
    assert rc == 0


def test_router_example_parses():
    cfg = load_config(EXAMPLES / "router.example.yaml")
    assert cfg.schedule.kind == "daily"
    assert set(cfg.windows) == {"morning", "evening"}

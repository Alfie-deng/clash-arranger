from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from clash_arranger.config import load_config
from helpers import EXAMPLES


@pytest.fixture
def workstation_cfg(tmp_path: Path):
    cfg = load_config(EXAMPLES / "workstation.example.yaml")
    cfg.state_dir = tmp_path / "state"
    cfg.state_dir.mkdir()
    dest_yaml = tmp_path / "primary.yaml"
    dest_down = tmp_path / "downstream.yaml"
    shutil.copy2(EXAMPLES / "workstation-config.example.yaml", dest_yaml)
    shutil.copy2(EXAMPLES / "downstream-config.example.yaml", dest_down)
    object.__setattr__(cfg.controller, "yaml_path", str(dest_yaml))
    object.__setattr__(cfg.downstream, "path", str(dest_down))
    object.__setattr__(cfg.downstream, "source_proxy_defs", str(dest_yaml))
    return cfg


@pytest.fixture
def router_cfg():
    return load_config(EXAMPLES / "router.example.yaml")

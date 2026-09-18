"""Load and validate profile configuration. Paths and secrets stay external."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

SCHEMA_VERSION = 1


class ConfigError(ValueError):
    """Invalid or incomplete profile configuration."""


def _as_time(value: str) -> time:
    parts = str(value).strip().split(":")
    if len(parts) != 2:
        raise ConfigError(f"time must be HH:MM, got {value!r}")
    return time(int(parts[0]), int(parts[1]))


def _req(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing required key {key!r}")
    return data[key]


@dataclass(slots=True)
class WindowSpec:
    name: str
    kind: str
    start: time
    end: time


@dataclass(slots=True)
class ShiftSpec:
    name: str
    at: time
    window: str


@dataclass(slots=True)
class ProviderRule:
    name: str
    min_seats: int
    max_seats: int


@dataclass(slots=True)
class MixConfig:
    seat_count: int
    providers: list[ProviderRule]
    allow_unknown_provider: bool
    region_allowlist: list[str]
    dedupe_exit_ip: bool
    dedupe_upstream: bool
    min_confidence_for_fill: str


@dataclass(slots=True)
class HealthConfig:
    url: str
    timeout_ms: int
    rounds: int
    fail_threshold: int
    unhealthy_cycles: int
    mass_dead: int
    isolate_sec: int
    rewrite_cooldown_sec: int
    recovery_passes: int
    escape_max_sets: int


@dataclass(slots=True)
class PromotionConfig:
    min_samples: int
    min_span_min: int
    min_gain_mbps: float
    min_ratio: float
    two_sample_max_main_mbps: float
    two_sample_min_gain_mbps: float
    two_sample_min_ratio: float
    cooldown_sec: int
    lookback_min: int


@dataclass(slots=True)
class TagConfig:
    min_healthy_mbps: float
    red_heavy_frac: float
    min_red_n: int
    brittle_ratio: float
    hat_window_hours: int
    pardon_nights: int
    pardon_loss_max: float
    high_n: int
    normal_n: int
    day_start: time
    day_end: time
    peak_start: time
    peak_end: time


@dataclass(slots=True)
class SamplingConfig:
    targets: list[str]
    concurrency: int
    timeout_sec: float
    interval_sec: int
    active_start: time | None
    active_end: time | None
    regions: list[str]


@dataclass(slots=True)
class ControllerConfig:
    adapter: str
    base_url: str
    group: str
    secret_env: str | None
    secret_file: str | None
    secret_keychain: str | None
    yaml_path: str
    reload: bool


@dataclass(slots=True)
class DownstreamConfig:
    enabled: bool
    adapter: str
    path: str
    source_proxy_defs: str | None
    confirm_app_runtime: bool


@dataclass(slots=True)
class OpenClashConfig:
    enabled: bool
    host: str
    user: str
    source_path: str
    runtime_path: str
    group: str


@dataclass(slots=True)
class ScheduleConfig:
    kind: str
    weekend_policy: str
    shifts: list[ShiftSpec]


@dataclass(slots=True)
class AppConfig:
    profile: str
    timezone: str
    tz: ZoneInfo
    schedule: ScheduleConfig
    windows: dict[str, WindowSpec]
    mix: MixConfig
    health: HealthConfig
    promotion: PromotionConfig
    tags: TagConfig
    sampling: SamplingConfig
    controller: ControllerConfig
    downstream: DownstreamConfig
    openclash: OpenClashConfig
    state_dir: Path
    samples_path: Path
    banned_nodes: set[str]
    disabled_nodes: set[str]
    raw: dict[str, Any] = field(default_factory=dict)

    def window(self, name: str) -> WindowSpec:
        if name not in self.windows:
            raise ConfigError(f"unknown window {name!r}")
        return self.windows[name]


def load_yaml(path: Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")
    return data


def load_config(path: Path | str) -> AppConfig:
    path = Path(path)
    data = load_yaml(path)
    return parse_config(data, source_dir=path.parent)


def parse_config(data: dict[str, Any], *, source_dir: Path | None = None) -> AppConfig:
    source_dir = source_dir or Path(".")
    profile = str(_req(data, "profile"))
    timezone = str(_req(data, "timezone"))
    try:
        tz = ZoneInfo(timezone)
    except Exception as exc:
        raise ConfigError(f"invalid timezone {timezone!r}") from exc

    windows: dict[str, WindowSpec] = {}
    for name, spec in dict(_req(data, "windows")).items():
        if not isinstance(spec, dict):
            raise ConfigError(f"window {name} must be a mapping")
        kind = str(spec.get("kind") or "today")
        if kind not in {"today", "previous_day"}:
            raise ConfigError(f"window {name} kind must be today|previous_day")
        windows[str(name)] = WindowSpec(
            name=str(name),
            kind=kind,
            start=_as_time(str(_req(spec, "start"))),
            end=_as_time(str(_req(spec, "end"))),
        )

    sched = dict(_req(data, "schedule"))
    shifts = []
    for item in sched.get("shifts") or []:
        shifts.append(
            ShiftSpec(
                name=str(item["name"]),
                at=_as_time(str(item["at"])),
                window=str(item["window"]),
            )
        )
    schedule = ScheduleConfig(
        kind=str(sched.get("kind") or "weekdays_only"),
        weekend_policy=str(sched.get("weekend_policy") or "no_op"),
        shifts=shifts,
    )
    if schedule.kind not in {"weekdays_only", "daily"}:
        raise ConfigError("schedule.kind must be weekdays_only|daily")

    mix_raw = dict(_req(data, "mix"))
    providers = []
    for name, rule in dict(mix_raw.get("providers") or {}).items():
        providers.append(
            ProviderRule(
                name=str(name),
                min_seats=int(rule.get("min", 0)),
                max_seats=int(rule.get("max", mix_raw.get("seat_count", 4))),
            )
        )
    mix = MixConfig(
        seat_count=int(mix_raw.get("seat_count", 4)),
        providers=providers,
        allow_unknown_provider=bool(mix_raw.get("allow_unknown_provider", False)),
        region_allowlist=[str(x) for x in (mix_raw.get("region_allowlist") or [])],
        dedupe_exit_ip=bool(mix_raw.get("dedupe_exit_ip", True)),
        dedupe_upstream=bool(mix_raw.get("dedupe_upstream", True)),
        min_confidence_for_fill=str(mix_raw.get("min_confidence_for_fill", "degraded")),
    )

    health_raw = dict(data.get("health") or {})
    health = HealthConfig(
        url=str(health_raw.get("url") or "https://www.gstatic.com/generate_204"),
        timeout_ms=int(health_raw.get("timeout_ms", 5000)),
        rounds=int(health_raw.get("rounds", 2)),
        fail_threshold=int(health_raw.get("fail_threshold", 2)),
        unhealthy_cycles=int(health_raw.get("unhealthy_cycles", 2)),
        mass_dead=int(health_raw.get("mass_dead", 3)),
        isolate_sec=int(health_raw.get("isolate_sec", 900)),
        rewrite_cooldown_sec=int(health_raw.get("rewrite_cooldown_sec", 600)),
        recovery_passes=int(health_raw.get("recovery_passes", 2)),
        escape_max_sets=int(health_raw.get("escape_max_sets", 3)),
    )

    promo_raw = dict(data.get("promotion") or {})
    promotion = PromotionConfig(
        min_samples=int(promo_raw.get("min_samples", 2)),
        min_span_min=int(promo_raw.get("min_span_min", 50)),
        min_gain_mbps=float(promo_raw.get("min_gain_mbps", 8.0)),
        min_ratio=float(promo_raw.get("min_ratio", 1.35)),
        two_sample_max_main_mbps=float(promo_raw.get("two_sample_max_main_mbps", 15.0)),
        two_sample_min_gain_mbps=float(promo_raw.get("two_sample_min_gain_mbps", 12.0)),
        two_sample_min_ratio=float(promo_raw.get("two_sample_min_ratio", 1.75)),
        cooldown_sec=int(promo_raw.get("cooldown_sec", 3600)),
        lookback_min=int(promo_raw.get("lookback_min", 130)),
    )

    tag_raw = dict(data.get("tags") or {})
    tags = TagConfig(
        min_healthy_mbps=float(tag_raw.get("min_healthy_mbps", 8.0)),
        red_heavy_frac=float(tag_raw.get("red_heavy_frac", 0.5)),
        min_red_n=int(tag_raw.get("min_red_n", 2)),
        brittle_ratio=float(tag_raw.get("brittle_ratio", 0.4)),
        hat_window_hours=int(tag_raw.get("hat_window_hours", 48)),
        pardon_nights=int(tag_raw.get("pardon_nights", 2)),
        pardon_loss_max=float(tag_raw.get("pardon_loss_max", 5.0)),
        high_n=int(tag_raw.get("high_n", 3)),
        normal_n=int(tag_raw.get("normal_n", 2)),
        day_start=_as_time(str(tag_raw.get("day_start", "09:00"))),
        day_end=_as_time(str(tag_raw.get("day_end", "17:00"))),
        peak_start=_as_time(str(tag_raw.get("peak_start", "17:00"))),
        peak_end=_as_time(str(tag_raw.get("peak_end", "23:01"))),
    )

    samp_raw = dict(data.get("sampling") or {})
    active = samp_raw.get("active_hours") or [None, None]
    sampling = SamplingConfig(
        targets=[str(x) for x in (samp_raw.get("targets") or [])],
        concurrency=int(samp_raw.get("concurrency", 2)),
        timeout_sec=float(samp_raw.get("timeout_sec", 20)),
        interval_sec=int(samp_raw.get("interval_sec", 600)),
        active_start=_as_time(str(active[0])) if active and active[0] else None,
        active_end=_as_time(str(active[1])) if active and len(active) > 1 and active[1] else None,
        regions=[str(x) for x in (samp_raw.get("regions") or [])],
    )

    ctl_raw = dict(data.get("controller") or {})
    controller = ControllerConfig(
        adapter=str(ctl_raw.get("adapter") or "mihomo-local"),
        base_url=str(ctl_raw.get("base_url") or "http://127.0.0.1:9090"),
        group=str(ctl_raw.get("group") or "surf-fallback"),
        secret_env=ctl_raw.get("secret_env"),
        secret_file=ctl_raw.get("secret_file"),
        secret_keychain=ctl_raw.get("secret_keychain"),
        yaml_path=str(ctl_raw.get("yaml_path") or "examples/workstation-config.example.yaml"),
        reload=bool(ctl_raw.get("reload", True)),
    )

    down_raw = dict(data.get("downstream") or {})
    downstream = DownstreamConfig(
        enabled=bool(down_raw.get("enabled", False)),
        adapter=str(down_raw.get("adapter") or "stash-file"),
        path=str(down_raw.get("path") or ""),
        source_proxy_defs=down_raw.get("source_proxy_defs"),
        confirm_app_runtime=bool(down_raw.get("confirm_app_runtime", False)),
    )

    oc_raw = dict(data.get("openclash") or {})
    openclash = OpenClashConfig(
        enabled=bool(oc_raw.get("enabled", False)),
        host=str(oc_raw.get("host") or "router-host.example.test"),
        user=str(oc_raw.get("user") or "root"),
        source_path=str(oc_raw.get("source_path") or "/etc/openclash/config/config.yaml"),
        runtime_path=str(oc_raw.get("runtime_path") or "/etc/openclash/config.yaml"),
        group=str(oc_raw.get("group") or controller.group),
    )

    state_dir = Path(data.get("state_dir") or "state")
    if not state_dir.is_absolute():
        state_dir = (source_dir / state_dir).resolve()
    samples_path = Path(data.get("samples_path") or "examples/samples.example.jsonl")
    if not samples_path.is_absolute():
        samples_path = source_dir / samples_path

    return AppConfig(
        profile=profile,
        timezone=timezone,
        tz=tz,
        schedule=schedule,
        windows=windows,
        mix=mix,
        health=health,
        promotion=promotion,
        tags=tags,
        sampling=sampling,
        controller=controller,
        downstream=downstream,
        openclash=openclash,
        state_dir=state_dir,
        samples_path=samples_path,
        banned_nodes=set(str(x) for x in (data.get("banned_nodes") or [])),
        disabled_nodes=set(str(x) for x in (data.get("disabled_nodes") or [])),
        raw=data,
    )


def load_secret(cfg: ControllerConfig) -> str:
    if cfg.secret_env:
        value = os.environ.get(str(cfg.secret_env), "")
        if value:
            return value
    if cfg.secret_file:
        path = Path(cfg.secret_file)
        if not path.is_file():
            raise ConfigError(f"secret file missing: {path}")
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise ConfigError(f"secret file must be mode 0600, got {oct(mode)}")
        return path.read_text(encoding="utf-8").strip()
    if cfg.secret_keychain:
        import subprocess

        proc = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                str(cfg.secret_keychain),
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ConfigError("keychain secret not found")
        return proc.stdout.strip()
    return os.environ.get("MIHOMO_SECRET", "")


def assert_secret_file_mode(path: Path) -> None:
    mode = path.stat().st_mode
    if stat.S_IMODE(mode) != 0o600:
        raise ConfigError(f"{path} must be 0600")

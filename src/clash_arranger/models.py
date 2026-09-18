"""Shared enums, sample records, and exit codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_PROBE_REJECT = 3
EXIT_DOWNSTREAM_PENDING = 4
EXIT_LOCK_BUSY = 5
EXIT_RUNTIME_UNVERIFIED = 6
EXIT_ROLLBACK = 7
EXIT_ESCAPE_EXHAUSTED = 8

RANKING_POLICY = "Front of historical throughput under health, confidence, region, and failure-domain constraints."
RANKING_POLICY_ZH = "健康、置信度、地区和故障域约束下的历史吞吐前排"

SUCCESS_DEFINITION = (
    "FILE_COMMITTED when source/disk == runtime/API == downstream file "
    "AND post-apply probe passes. APP_RUNTIME_CONFIRMED is extra and never required."
)


class Tag(StrEnum):
    BLOCKED = "blocked"
    BRITTLE = "brittle"
    HARDY = "hardy"
    WATCH = "watch"


class Confidence(StrEnum):
    HIGH = "high"
    NORMAL = "normal"
    DEGRADED = "degraded"
    NONE = "none"


class SampleError(StrEnum):
    TIMEOUT = "timeout"
    TLS_FAILED = "tls_failed"
    TARGET_FAILED = "target_failed"
    CLIP_TOO_SHORT = "clip_too_short"
    RANGE_UNSATISFIABLE = "range_unsatisfiable"
    INVALID_SHORT = "invalid_short"
    INSUFFICIENT_SAMPLES = "insufficient_samples"


INVALID_SAMPLE_ERRORS = frozenset(
    {
        SampleError.CLIP_TOO_SHORT,
        SampleError.RANGE_UNSATISFIABLE,
        SampleError.INVALID_SHORT,
    }
)

RED_SAMPLE_ERRORS = frozenset(
    {
        SampleError.TIMEOUT,
        SampleError.TLS_FAILED,
        SampleError.TARGET_FAILED,
    }
)

TAG_DISPLAY_ZH = {
    Tag.BLOCKED: "不可用",
    Tag.BRITTLE: "脆弱",
    Tag.HARDY: "稳健",
    Tag.WATCH: "观察",
}

TAG_DISPLAY_EN = {
    Tag.BLOCKED: "blocked",
    Tag.BRITTLE: "brittle",
    Tag.HARDY: "hardy",
    Tag.WATCH: "watch",
}


@dataclass(slots=True)
class Sample:
    schema_version: int
    ts: datetime
    profile: str
    site: str
    node_id: str
    provider: str
    failure_domain: str
    region: str
    mbps: float | None
    packet_loss_pct: float | None
    target_ok: bool | None
    tls_ok: bool | None
    http_status: int | None
    sample_valid: bool
    error_reason: SampleError | None
    exit_ip: str | None = None
    upstream: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def usable_mbps(self) -> float | None:
        """Failed or invalid rows never masquerade as 0 Mbps."""
        if not self.sample_valid:
            return None
        if self.error_reason in INVALID_SAMPLE_ERRORS:
            return None
        if self.mbps is None:
            return None
        if self.mbps <= 0:
            return None
        return float(self.mbps)


@dataclass(slots=True)
class NodeStats:
    node_id: str
    provider: str
    failure_domain: str
    region: str
    n: int
    median_mbps: float | None
    loss: float | None
    red_ratio: float
    red_heavy: bool
    tls_or_target_fail: bool
    healthy: bool
    confidence: Confidence
    tag: Tag = Tag.WATCH
    exit_ip: str | None = None
    upstream: str | None = None
    filter_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RankResult:
    window: str
    seats: list[str]
    ranked: list[str]
    stats: dict[str, NodeStats]
    score_start: datetime
    score_end: datetime
    tag_as_of: datetime
    timezone: str
    mix_reason: str
    filter_log: list[dict[str, Any]]
    ranking_policy: str = RANKING_POLICY


@dataclass(slots=True)
class ProbeRound:
    delay_ms: int | None


@dataclass(slots=True)
class ProbeResult:
    results: dict[str, list[int | None]]

    def samples(self, name: str) -> list[int | None]:
        return list(self.results.get(name) or [])

    def all_live(self, name: str) -> bool:
        samples = self.samples(name)
        return bool(samples) and all(isinstance(x, int) and x > 0 for x in samples)

    def any_live(self, name: str) -> bool:
        return any(isinstance(x, int) and x > 0 for x in self.samples(name))

    def dead(self, name: str) -> bool:
        return not self.any_live(name)


@dataclass(slots=True)
class TransactionView:
    disk: list[str] | None = None
    api: list[str] | None = None
    downstream: list[str] | None = None
    probe_ok: bool | None = None
    reload_status: str | None = None
    app_runtime: str | None = None
    state: str = "PREPARED"
    reason: str = ""
    exit_code: int = EXIT_OK
    logs: list[str] = field(default_factory=list)

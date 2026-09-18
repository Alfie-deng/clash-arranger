"""Versioned JSONL samples. Failed rows never become 0 Mbps."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import AppConfig, SamplingConfig
from .models import INVALID_SAMPLE_ERRORS, RED_SAMPLE_ERRORS, Sample, SampleError

CURRENT_SCHEMA = 1


class SampleError_(ValueError):
    """Malformed sample row."""


def parse_ts(raw: str, tz: ZoneInfo | None = None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=tz)
    return stamp


def _error_reason(raw: Any) -> SampleError | None:
    if raw in (None, "", False):
        return None
    try:
        return SampleError(str(raw))
    except ValueError:
        return None


def parse_sample(obj: dict[str, Any], *, default_tz: ZoneInfo | None = None) -> Sample:
    version = int(obj.get("schema_version") or 0)
    if version != CURRENT_SCHEMA:
        raise SampleError_(f"unsupported schema_version={version}")
    ts = parse_ts(str(obj.get("ts") or obj.get("ts_local") or ""), default_tz)
    if ts is None:
        raise SampleError_("sample missing timestamp")
    node_id = str(obj.get("node_id") or obj.get("node_name") or "")
    if not node_id:
        raise SampleError_("sample missing node_id")
    reason = _error_reason(obj.get("error_reason") or obj.get("surf_red_reason"))
    sample_valid = obj.get("sample_valid")
    if sample_valid is None:
        sample_valid = (
            reason not in INVALID_SAMPLE_ERRORS and reason is not SampleError.INSUFFICIENT_SAMPLES
        )
        if obj.get("mbps") in (0, 0.0) and reason in RED_SAMPLE_ERRORS:
            sample_valid = False
    mbps = obj.get("mbps")
    try:
        mbps_f = float(mbps) if mbps is not None else None
    except (TypeError, ValueError):
        mbps_f = None
    loss = obj.get("packet_loss_pct")
    try:
        loss_f = float(loss) if loss is not None else None
    except (TypeError, ValueError):
        loss_f = None
    target_ok = obj.get("target_ok")
    if target_ok is None and "reach_x" in obj:
        target_ok = obj.get("reach_x")
    tls_ok = obj.get("tls_ok")
    if reason is SampleError.TLS_FAILED:
        tls_ok = False
    if reason is SampleError.TARGET_FAILED:
        target_ok = False
    return Sample(
        schema_version=version,
        ts=ts,
        profile=str(obj.get("profile") or ""),
        site=str(obj.get("site") or ""),
        node_id=node_id,
        provider=str(obj.get("provider") or "unknown"),
        failure_domain=str(obj.get("failure_domain") or obj.get("provider") or node_id),
        region=str(obj.get("region") or ""),
        mbps=mbps_f,
        packet_loss_pct=loss_f,
        target_ok=None if target_ok is None else bool(target_ok),
        tls_ok=None if tls_ok is None else bool(tls_ok),
        http_status=int(obj["http_status"]) if obj.get("http_status") is not None else None,
        sample_valid=bool(sample_valid),
        error_reason=reason,
        exit_ip=str(obj["exit_ip"]) if obj.get("exit_ip") else None,
        upstream=str(obj["upstream"]) if obj.get("upstream") else None,
        raw=obj,
    )


def load_jsonl(
    path: Path | str,
    *,
    tz: ZoneInfo | None = None,
    profile: str | None = None,
    site: str | None = None,
) -> list[Sample]:
    rows: list[Sample] = []
    text = Path(path).read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
            sample = parse_sample(obj, default_tz=tz)
        except (json.JSONDecodeError, SampleError_, ValueError) as exc:
            raise SampleError_(f"{path}:{line_no}: {exc}") from exc
        if profile and sample.profile and sample.profile != profile:
            continue
        if site and sample.site and sample.site != site:
            continue
        rows.append(sample)
    return rows


def dump_sample(sample: Sample) -> dict[str, Any]:
    return {
        "schema_version": sample.schema_version,
        "ts": sample.ts.isoformat(timespec="seconds"),
        "profile": sample.profile,
        "site": sample.site,
        "node_id": sample.node_id,
        "provider": sample.provider,
        "failure_domain": sample.failure_domain,
        "region": sample.region,
        "mbps": sample.mbps,
        "packet_loss_pct": sample.packet_loss_pct,
        "target_ok": sample.target_ok,
        "tls_ok": sample.tls_ok,
        "http_status": sample.http_status,
        "sample_valid": sample.sample_valid,
        "error_reason": sample.error_reason.value if sample.error_reason else None,
        "exit_ip": sample.exit_ip,
        "upstream": sample.upstream,
    }


def write_jsonl(path: Path, samples: Iterable[Sample]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for sample in samples:
            fh.write(json.dumps(dump_sample(sample), ensure_ascii=False) + "\n")


def is_red(sample: Sample) -> bool:
    if sample.error_reason in INVALID_SAMPLE_ERRORS:
        return False
    if not sample.sample_valid and sample.error_reason in RED_SAMPLE_ERRORS:
        return True
    if sample.target_ok is False:
        return True
    if sample.tls_ok is False:
        return True
    if sample.error_reason in RED_SAMPLE_ERRORS:
        return True
    return False


def is_tls_or_target_fail(sample: Sample) -> bool:
    if sample.tls_ok is False or sample.target_ok is False:
        return True
    return sample.error_reason in {SampleError.TLS_FAILED, SampleError.TARGET_FAILED}


def in_active_hours(ts: datetime, sampling: SamplingConfig) -> bool:
    if sampling.active_start is None or sampling.active_end is None:
        return True
    hm = ts.hour * 60 + ts.minute
    start = sampling.active_start.hour * 60 + sampling.active_start.minute
    end = sampling.active_end.hour * 60 + sampling.active_end.minute
    return start <= hm < end


def make_sample(
    *,
    ts: datetime,
    node_id: str,
    provider: str,
    region: str,
    mbps: float | None,
    profile: str = "workstation",
    site: str = "workstation-probe",
    failure_domain: str | None = None,
    packet_loss_pct: float | None = 0.0,
    target_ok: bool = True,
    tls_ok: bool = True,
    http_status: int | None = 206,
    sample_valid: bool = True,
    error_reason: SampleError | None = None,
    exit_ip: str | None = None,
    upstream: str | None = None,
) -> Sample:
    return Sample(
        schema_version=CURRENT_SCHEMA,
        ts=ts,
        profile=profile,
        site=site,
        node_id=node_id,
        provider=provider,
        failure_domain=failure_domain or provider,
        region=region,
        mbps=mbps,
        packet_loss_pct=packet_loss_pct,
        target_ok=target_ok,
        tls_ok=tls_ok,
        http_status=http_status,
        sample_valid=sample_valid,
        error_reason=error_reason,
        exit_ip=exit_ip,
        upstream=upstream,
    )


def fixture_mode_samples(cfg: AppConfig) -> list[Sample]:
    """Validate and return committed fixture samples. Never invent live traffic."""
    return load_jsonl(cfg.samples_path, tz=cfg.tz)


def lookback(samples: Iterable[Sample], now: datetime, minutes: int) -> list[Sample]:
    start = now - timedelta(minutes=minutes)
    return [s for s in samples if start <= s.ts < now]

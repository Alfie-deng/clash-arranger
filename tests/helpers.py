from __future__ import annotations

import datetime as dt
from pathlib import Path

from clash_arranger.models import Sample, SampleError
from clash_arranger.samples import CURRENT_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TZ = dt.timezone(dt.timedelta(hours=8), "CST")


def sample(
    ts: str,
    node_id: str,
    *,
    provider: str = "provider-a",
    region: str = "SG",
    mbps: float | None = 20.0,
    valid: bool = True,
    error: SampleError | None = None,
    loss: float | None = 0.1,
    target_ok: bool = True,
    tls_ok: bool = True,
    profile: str = "workstation",
    failure_domain: str | None = None,
    exit_ip: str | None = None,
    upstream: str | None = None,
) -> Sample:
    stamp = dt.datetime.fromisoformat(ts)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TZ)
    return Sample(
        schema_version=CURRENT_SCHEMA,
        ts=stamp,
        profile=profile,
        site="workstation-probe",
        node_id=node_id,
        provider=provider,
        failure_domain=failure_domain or provider,
        region=region,
        mbps=mbps,
        packet_loss_pct=loss,
        target_ok=target_ok,
        tls_ok=tls_ok,
        http_status=206 if valid else None,
        sample_valid=valid,
        error_reason=error,
        exit_ip=exit_ip,
        upstream=upstream,
    )

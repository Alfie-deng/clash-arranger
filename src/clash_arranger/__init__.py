"""Clash 节点编排器 — ordered fallback seats for Mihomo/Clash."""

from .models import (
    EXIT_DOWNSTREAM_PENDING,
    EXIT_ESCAPE_EXHAUSTED,
    EXIT_LOCK_BUSY,
    EXIT_OK,
    EXIT_PROBE_REJECT,
    EXIT_ROLLBACK,
    EXIT_RUNTIME_UNVERIFIED,
    EXIT_USAGE,
    Confidence,
    SampleError,
    Tag,
)

__all__ = [
    "Confidence",
    "SampleError",
    "Tag",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_PROBE_REJECT",
    "EXIT_DOWNSTREAM_PENDING",
    "EXIT_LOCK_BUSY",
    "EXIT_RUNTIME_UNVERIFIED",
    "EXIT_ROLLBACK",
    "EXIT_ESCAPE_EXHAUSTED",
]

__version__ = "0.1.0"

"""Mihomo controller adapter. Transport is injectable for tests."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from ..config import ControllerConfig


class ControllerError(RuntimeError):
    pass


HttpFn = Callable[[str, str, bytes | None, dict[str, str], float], tuple[int, bytes]]


def is_reload_connection_drop(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
        return True
    if isinstance(exc, http.client.RemoteDisconnected):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(
            reason,
            (ConnectionResetError, BrokenPipeError, TimeoutError, http.client.RemoteDisconnected),
        ):
            return True
        msg = str(reason if reason is not None else exc).lower()
    else:
        msg = str(exc).lower()
    needles = (
        "remote end closed",
        "connection reset",
        "broken pipe",
        "remotely closed",
        "connection aborted",
    )
    return any(n in msg for n in needles)


def default_http(
    method: str,
    url: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (200, 204):
            return int(exc.code), exc.read() if exc.fp else b""
        raise


@dataclass
class MihomoClient:
    cfg: ControllerConfig
    secret: str = ""
    http: HttpFn = default_http

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers

    def probe_delay(self, name: str, *, url: str, timeout_ms: int) -> int | None:
        encoded = quote(name, safe="")
        health = quote(url, safe="")
        target = f"{self.cfg.base_url.rstrip('/')}/proxies/{encoded}/delay?url={health}&timeout={timeout_ms}"
        try:
            status, body = self.http(
                "GET", target, None, self._headers(), timeout_ms / 1000.0 + 2.0
            )
        except Exception:
            return None
        if status != 200:
            return None
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return None
        delay = data.get("delay") if isinstance(data, dict) else None
        return int(delay) if isinstance(delay, (int, float)) and delay > 0 else None

    def group_seats(self, group: str | None = None) -> list[str]:
        name = quote(group or self.cfg.group, safe="")
        target = f"{self.cfg.base_url.rstrip('/')}/proxies/{name}"
        status, body = self.http("GET", target, None, self._headers(), 10.0)
        if status != 200:
            raise ControllerError(f"group read failed status={status}")
        data = json.loads(body.decode("utf-8"))
        all_names = data.get("all") if isinstance(data, dict) else None
        if not isinstance(all_names, list):
            raise ControllerError("controller group missing all")
        return [str(x) for x in all_names]

    def reload(self, yaml_path: str) -> str:
        if not self.cfg.reload:
            return "skipped"
        target = f"{self.cfg.base_url.rstrip('/')}/configs?force=true"
        payload = json.dumps({"path": yaml_path}).encode("utf-8")
        try:
            status, _body = self.http("PUT", target, payload, self._headers(), 30.0)
        except Exception as exc:
            if is_reload_connection_drop(exc):
                return "soft_fail_connection_drop"
            raise
        if status in (200, 204):
            return "ok"
        raise ControllerError(f"reload failed status={status}")


def wait_api_seats(
    client: MihomoClient,
    expected: list[str],
    *,
    attempts: int = 8,
    sleep_fn: Callable[[float], None] | None = None,
) -> list[str]:
    last: list[str] | None = None
    last_err = None
    for _ in range(attempts):
        try:
            last = client.group_seats()
            if last == list(expected):
                return last
            last_err = f"mismatch {last} != {expected}"
        except Exception as exc:
            last_err = str(exc)
        if sleep_fn:
            sleep_fn(0.05)
    raise ControllerError(f"API verify failed: {last_err}")

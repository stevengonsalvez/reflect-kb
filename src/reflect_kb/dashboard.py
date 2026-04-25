"""Opt-in dashboard POST client (v4 §Phase 5).

Reads ``[dashboard]`` from ``~/.learnings/config.toml`` (or the
``REFLECT_CONFIG_PATH`` override) and POSTs aggregated stats to
``<endpoint>/v1/ingest`` with a Bearer token. One retry on 5xx.

Spec for the receiving end lives at
``docs/dashboard-endpoint-spec.md``.
"""

from __future__ import annotations

import os
import socket
import sys
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

DEFAULT_CONFIG_PATH = Path.home() / ".learnings" / "config.toml"
INGEST_PATH = "/v1/ingest"
USER_AGENT = "reflect-kb-dashboard-client/1"


@dataclass
class DashboardConfig:
    endpoint: str
    token: str
    client_id: str  # stable per-machine identifier


def _stable_client_id() -> str:
    """Best-effort stable client ID. Hostname is usually enough; fall back to
    a UUID derived from the MAC if hostname is generic.
    """
    host = socket.gethostname() or "unknown"
    return host


def load_config(path: Optional[Path] = None) -> Optional[DashboardConfig]:
    """Load ``[dashboard]`` config. Returns ``None`` if file missing, section
    missing, or required keys missing — caller treats this as "not configured"
    and exits 0.
    """
    cfg_path = path or Path(os.environ.get("REFLECT_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    if not cfg_path.exists():
        return None
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None
    section = data.get("dashboard")
    if not isinstance(section, dict):
        return None
    endpoint = section.get("endpoint")
    token = section.get("token")
    if not (isinstance(endpoint, str) and isinstance(token, str) and endpoint and token):
        return None
    client_id = section.get("client_id") or _stable_client_id()
    return DashboardConfig(
        endpoint=endpoint.rstrip("/"),
        token=token,
        client_id=str(client_id),
    )


def build_payload(stats: dict[str, Any], *, client_id: str) -> dict[str, Any]:
    """Wrap a :class:`StatsReport.to_dict()` in the on-wire envelope.

    The envelope adds idempotency keys and lets the server dedup retries by
    ``run_id`` (see ``docs/dashboard-endpoint-spec.md``).
    """
    return {
        "schema": "reflect-kb.dashboard.ingest/v1",
        "client_id": client_id,
        "run_id": str(uuid.uuid4()),
        "stats": stats,
    }


def post_stats(
    config: DashboardConfig,
    stats: dict[str, Any],
    *,
    client: Optional[httpx.Client] = None,
    timeout: float = 10.0,
) -> httpx.Response:
    """POST aggregated stats. Retries once on 5xx. Returns the final response.

    The caller decides what to do with non-2xx (the CLI surfaces it as an
    error). 4xx is *not* retried — that's a client/config bug, retrying
    won't fix it.
    """
    payload = build_payload(stats, client_id=config.client_id)
    url = f"{config.endpoint}{INGEST_PATH}"
    headers = {
        "Authorization": f"Bearer {config.token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        for attempt in range(2):
            response = http.post(url, json=payload, headers=headers)
            if response.status_code < 500:
                return response
            # 5xx — retry once. Server-side hiccup is usually transient.
            if attempt == 1:
                return response
        # Unreachable, but mypy doesn't know.
        return response  # pragma: no cover
    finally:
        if owns_client:
            http.close()


def sync(
    stats: dict[str, Any],
    *,
    config_path: Optional[Path] = None,
    client: Optional[httpx.Client] = None,
) -> tuple[int, str]:
    """High-level entry: load config, post if configured, return ``(exit_code,
    message)`` for the CLI to print and exit with.

    Exit codes:
        0 — not configured (intentional opt-in) OR successful sync
        1 — configured but POST failed (network or non-2xx response)
    """
    config = load_config(config_path)
    if config is None:
        return 0, "dashboard not configured (add [dashboard] to ~/.learnings/config.toml to enable)"
    try:
        response = post_stats(config, stats, client=client)
    except httpx.HTTPError as exc:
        return 1, f"dashboard POST failed: {exc}"
    if response.status_code >= 400:
        return 1, (
            f"dashboard POST returned HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    return 0, f"dashboard sync ok ({response.status_code} from {config.endpoint})"

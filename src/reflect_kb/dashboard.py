"""Opt-in dashboard POST client (v4 §Phase 5).

Reads ``[dashboard]`` from ``~/.learnings/config.toml`` (or the
``REFLECT_CONFIG_PATH`` override) and POSTs aggregated stats to
``<endpoint>/v1/ingest`` with a Bearer token. One retry on 5xx *or* on a
transport-layer failure (connect/timeout/read).

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


_GENERIC_HOSTNAMES = frozenset({"", "localhost", "unknown", "localhost.localdomain"})


def _stable_client_id() -> str:
    """Best-effort stable client ID. Hostname is usually enough; fall back to
    a hash of the MAC address (via :func:`uuid.getnode`) when the hostname is
    generic (``localhost``, ``unknown``, empty) so two boxes don't share an ID.
    """
    host = (socket.gethostname() or "").strip()
    if host.lower() not in _GENERIC_HOSTNAMES:
        return host
    # uuid.getnode() returns the MAC as a 48-bit int, or a random 48-bit int
    # with the multicast bit set if no NIC is reachable. Either way, hashing +
    # truncating yields a stable, opaque per-machine label that doesn't leak
    # the raw MAC.
    node = uuid.getnode()
    digest = uuid.uuid5(uuid.NAMESPACE_OID, f"reflect-kb-mac:{node:012x}").hex
    return f"machine-{digest[:12]}"


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
    """POST aggregated stats. Retries once on transport errors or 5xx.

    The caller decides what to do with non-2xx (the CLI surfaces it as an
    error). 4xx is *not* retried — that's a client/config bug, retrying
    won't fix it. A transport-layer failure (connect/timeout/read) on the
    first attempt is retried once, matching the 5xx semantics; if the second
    attempt also fails the exception propagates to :func:`sync` which turns
    it into exit code 1.
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
    last_response: Optional[httpx.Response] = None
    try:
        for attempt in range(2):
            try:
                response = http.post(url, json=payload, headers=headers)
            except httpx.TransportError:
                # Connect/timeout/read errors are transient — retry once,
                # then re-raise so sync() reports them with exit 1.
                if attempt == 1:
                    raise
                continue
            last_response = response
            if response.status_code < 500:
                return response
            # 5xx — retry once. Server-side hiccup is usually transient.
            if attempt == 1:
                return response
        # Unreachable in practice (loop either returns or raises), but keep a
        # defensive fallback so the type checker is happy.
        assert last_response is not None  # pragma: no cover
        return last_response  # pragma: no cover
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

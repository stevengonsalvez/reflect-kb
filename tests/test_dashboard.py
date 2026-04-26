"""Tests for the opt-in dashboard sync client (v4 §Phase 5)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from reflect_kb import dashboard


# ----- config loading ---------------------------------------------------------


def test_load_config_returns_none_when_file_missing(tmp_path):
    assert dashboard.load_config(tmp_path / "nope.toml") is None


def test_load_config_returns_none_when_section_missing(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[other]\nfoo = 1\n", encoding="utf-8")
    assert dashboard.load_config(cfg) is None


def test_load_config_returns_none_when_required_keys_missing(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[dashboard]\nendpoint = 'https://example.com'\n", encoding="utf-8")
    assert dashboard.load_config(cfg) is None


def test_load_config_parses_full_section(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[dashboard]\n"
        "endpoint = 'https://team-dash.example.com/'\n"
        "token = 'secret-token'\n"
        "client_id = 'laptop-42'\n",
        encoding="utf-8",
    )
    loaded = dashboard.load_config(cfg)
    assert loaded is not None
    # trailing slash stripped so url joining is unambiguous
    assert loaded.endpoint == "https://team-dash.example.com"
    assert loaded.token == "secret-token"
    assert loaded.client_id == "laptop-42"


def test_load_config_handles_corrupt_toml_gracefully(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[[[ not toml", encoding="utf-8")
    assert dashboard.load_config(cfg) is None


# ----- payload envelope -------------------------------------------------------


def test_build_payload_includes_idempotency_keys():
    payload = dashboard.build_payload({"hit_rate": 0.5}, client_id="host-A")
    assert payload["schema"] == "reflect-kb.dashboard.ingest/v1"
    assert payload["client_id"] == "host-A"
    assert payload["stats"] == {"hit_rate": 0.5}
    # run_id is a UUID hex string — long enough to not collide on retries
    assert len(payload["run_id"]) >= 32


# ----- HTTP behaviour ---------------------------------------------------------


@pytest.fixture
def cfg() -> dashboard.DashboardConfig:
    return dashboard.DashboardConfig(
        endpoint="https://team-dash.example.com",
        token="t-secret",
        client_id="laptop-1",
    )


def test_post_stats_sends_bearer_and_body(cfg):
    with respx.mock(base_url=cfg.endpoint) as mock:
        route = mock.post("/v1/ingest").mock(
            return_value=httpx.Response(202, json={"accepted": True})
        )
        with httpx.Client() as client:
            resp = dashboard.post_stats(cfg, {"hit_rate": 0.7}, client=client)
        assert resp.status_code == 202
        assert route.call_count == 1
        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Bearer t-secret"
        assert sent.headers["content-type"] == "application/json"
        body = sent.read()
        assert b'"hit_rate"' in body  # stats embedded in envelope
        assert b'"client_id"' in body


def test_post_stats_retries_once_on_5xx(cfg):
    with respx.mock(base_url=cfg.endpoint) as mock:
        route = mock.post("/v1/ingest").mock(
            side_effect=[
                httpx.Response(503, text="busy"),
                httpx.Response(202, json={"accepted": True}),
            ]
        )
        with httpx.Client() as client:
            resp = dashboard.post_stats(cfg, {"x": 1}, client=client)
        assert resp.status_code == 202
        assert route.call_count == 2  # one retry exactly


def test_post_stats_does_not_retry_on_4xx(cfg):
    with respx.mock(base_url=cfg.endpoint) as mock:
        route = mock.post("/v1/ingest").mock(return_value=httpx.Response(401))
        with httpx.Client() as client:
            resp = dashboard.post_stats(cfg, {"x": 1}, client=client)
        assert resp.status_code == 401
        assert route.call_count == 1  # auth bug — no point retrying


def test_post_stats_retries_once_on_transport_error(cfg):
    """Transport-layer failures (ConnectError, TimeoutException, …) on the
    first attempt MUST trigger one retry, mirroring the 5xx semantics. Without
    this, a single TCP RST aborts the sync with zero retries.
    """
    with respx.mock(base_url=cfg.endpoint) as mock:
        route = mock.post("/v1/ingest").mock(
            side_effect=[
                httpx.ConnectError("connection refused"),
                httpx.Response(202, json={"accepted": True}),
            ]
        )
        with httpx.Client() as client:
            resp = dashboard.post_stats(cfg, {"x": 1}, client=client)
        assert resp.status_code == 202
        assert route.call_count == 2  # transport error on first call, success on retry


def test_post_stats_propagates_transport_error_after_retry(cfg):
    """Two consecutive transport failures bubble up — sync() converts that
    into exit 1, which is the desired CLI behaviour for genuinely unreachable
    endpoints.
    """
    with respx.mock(base_url=cfg.endpoint) as mock:
        route = mock.post("/v1/ingest").mock(
            side_effect=[
                httpx.ConnectError("first"),
                httpx.ReadTimeout("second"),
            ]
        )
        with httpx.Client() as client, pytest.raises(httpx.TransportError):
            dashboard.post_stats(cfg, {"x": 1}, client=client)
        assert route.call_count == 2  # exactly one retry, no third attempt


# ----- _stable_client_id ------------------------------------------------------


def test_stable_client_id_returns_hostname_when_descriptive(monkeypatch):
    monkeypatch.setattr(dashboard.socket, "gethostname", lambda: "stevies-laptop")
    assert dashboard._stable_client_id() == "stevies-laptop"


@pytest.mark.parametrize("generic", ["localhost", "unknown", "", "  "])
def test_stable_client_id_falls_back_to_mac_for_generic_hostnames(monkeypatch, generic):
    """When the hostname carries no signal, derive a stable opaque label from
    ``uuid.getnode()`` so two boxes with identical generic hostnames don't
    collapse onto one client_id in the dashboard.
    """
    monkeypatch.setattr(dashboard.socket, "gethostname", lambda: generic)
    monkeypatch.setattr(dashboard.uuid, "getnode", lambda: 0xAABBCCDDEEFF)
    cid = dashboard._stable_client_id()
    assert cid.startswith("machine-")
    assert len(cid) == len("machine-") + 12  # 12 hex chars from the digest
    # Stable: same MAC always produces the same id.
    assert cid == dashboard._stable_client_id()


def test_stable_client_id_differs_across_macs(monkeypatch):
    monkeypatch.setattr(dashboard.socket, "gethostname", lambda: "localhost")
    monkeypatch.setattr(dashboard.uuid, "getnode", lambda: 0x111111111111)
    a = dashboard._stable_client_id()
    monkeypatch.setattr(dashboard.uuid, "getnode", lambda: 0x222222222222)
    b = dashboard._stable_client_id()
    assert a != b


# ----- top-level sync() -------------------------------------------------------


def test_sync_returns_zero_when_not_configured(tmp_path):
    code, msg = dashboard.sync({}, config_path=tmp_path / "missing.toml")
    assert code == 0
    assert "not configured" in msg.lower()


def test_sync_success_path(tmp_path, cfg):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"[dashboard]\nendpoint = '{cfg.endpoint}'\ntoken = 't-secret'\nclient_id = '{cfg.client_id}'\n",
        encoding="utf-8",
    )
    with respx.mock(base_url=cfg.endpoint) as mock:
        mock.post("/v1/ingest").mock(return_value=httpx.Response(202, json={"accepted": True}))
        with httpx.Client() as client:
            code, msg = dashboard.sync({"x": 1}, config_path=cfg_path, client=client)
    assert code == 0
    assert "ok" in msg.lower()


def test_sync_reports_http_error(tmp_path, cfg):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"[dashboard]\nendpoint = '{cfg.endpoint}'\ntoken = 't-secret'\nclient_id = '{cfg.client_id}'\n",
        encoding="utf-8",
    )
    with respx.mock(base_url=cfg.endpoint) as mock:
        mock.post("/v1/ingest").mock(return_value=httpx.Response(500, text="kaboom"))
        with httpx.Client() as client:
            code, msg = dashboard.sync({"x": 1}, config_path=cfg_path, client=client)
    assert code == 1
    assert "500" in msg


def test_sync_reports_transport_error_after_retry(tmp_path, cfg):
    """Two transport failures in a row → exit 1 with a useful message. This
    exercises the full chain post_stats → httpx.HTTPError → sync().
    """
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"[dashboard]\nendpoint = '{cfg.endpoint}'\ntoken = 't-secret'\nclient_id = '{cfg.client_id}'\n",
        encoding="utf-8",
    )
    with respx.mock(base_url=cfg.endpoint) as mock:
        mock.post("/v1/ingest").mock(
            side_effect=[httpx.ConnectError("nope"), httpx.ConnectError("still nope")]
        )
        with httpx.Client() as client:
            code, msg = dashboard.sync({"x": 1}, config_path=cfg_path, client=client)
    assert code == 1
    assert "failed" in msg.lower()


# ----- CLI: --window-days forwarding ------------------------------------------


def test_dashboard_sync_cli_forwards_window_days(tmp_path, monkeypatch, cfg):
    """`reflect metrics dashboard sync --window-days N` MUST flow N through to
    metrics_stats.aggregate. Previously the value was hardcoded to 7.
    """
    from click.testing import CliRunner

    from reflect_kb import metrics_stats
    from reflect_kb.cli.metrics_cli import metrics_group

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"[dashboard]\nendpoint = '{cfg.endpoint}'\ntoken = 't-secret'\nclient_id = '{cfg.client_id}'\n",
        encoding="utf-8",
    )
    metrics_jsonl = tmp_path / "metrics.jsonl"
    metrics_jsonl.write_text("", encoding="utf-8")  # empty is fine — aggregate handles it

    captured: dict = {}
    real_aggregate = metrics_stats.aggregate

    def spy_aggregate(path, *, window_days=7, **kwargs):
        captured["path"] = path
        captured["window_days"] = window_days
        return real_aggregate(path, window_days=window_days, **kwargs)

    monkeypatch.setattr(metrics_stats, "aggregate", spy_aggregate)
    # Patch the symbol the CLI module imported, too — Click bound it at import.
    from reflect_kb.cli import metrics_cli as metrics_cli_mod
    monkeypatch.setattr(metrics_cli_mod.metrics_stats, "aggregate", spy_aggregate)

    with respx.mock(base_url=cfg.endpoint) as mock:
        mock.post("/v1/ingest").mock(return_value=httpx.Response(202))
        result = CliRunner().invoke(
            metrics_group,
            [
                "dashboard", "sync",
                "--metrics-path", str(metrics_jsonl),
                "--config-path", str(cfg_path),
                "--window-days", "30",
            ],
        )
    assert result.exit_code == 0, result.output
    assert captured["window_days"] == 30
    assert captured["path"] == metrics_jsonl


def test_dashboard_sync_cli_window_days_defaults_to_seven(tmp_path, monkeypatch, cfg):
    from click.testing import CliRunner

    from reflect_kb import metrics_stats
    from reflect_kb.cli.metrics_cli import metrics_group

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"[dashboard]\nendpoint = '{cfg.endpoint}'\ntoken = 't-secret'\nclient_id = '{cfg.client_id}'\n",
        encoding="utf-8",
    )
    metrics_jsonl = tmp_path / "metrics.jsonl"
    metrics_jsonl.write_text("", encoding="utf-8")

    captured: dict = {}
    real_aggregate = metrics_stats.aggregate

    def spy_aggregate(path, *, window_days=7, **kwargs):
        captured["window_days"] = window_days
        return real_aggregate(path, window_days=window_days, **kwargs)

    from reflect_kb.cli import metrics_cli as metrics_cli_mod
    monkeypatch.setattr(metrics_cli_mod.metrics_stats, "aggregate", spy_aggregate)

    with respx.mock(base_url=cfg.endpoint) as mock:
        mock.post("/v1/ingest").mock(return_value=httpx.Response(202))
        result = CliRunner().invoke(
            metrics_group,
            [
                "dashboard", "sync",
                "--metrics-path", str(metrics_jsonl),
                "--config-path", str(cfg_path),
            ],
        )
    assert result.exit_code == 0, result.output
    assert captured["window_days"] == 7

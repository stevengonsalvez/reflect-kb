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

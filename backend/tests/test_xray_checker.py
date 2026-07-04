"""Tests for api/xray_checker.py — the sampling helper and the account gating on
the checker status route. Docker/network calls are mocked (no real container)."""
import asyncio
import uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.xray_checker as xcapi

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"xc-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_sample_once_records_scraped_proxies(monkeypatch):
    recorded = {}

    async def fake_fetch():
        return [{"stableId": "n1", "online": True}, {"stableId": "n2", "online": False}]

    async def fake_record(proxies):
        recorded["n"] = len(proxies)

    monkeypatch.setattr(xcapi.xc, "fetch_proxies", fake_fetch)
    monkeypatch.setattr(xcapi.metrics_store, "record_samples", fake_record)

    count = asyncio.run(xcapi._sample_once())
    assert count == 2
    assert recorded["n"] == 2


def test_sample_once_returns_zero_when_fetch_fails(monkeypatch):
    async def boom():
        raise RuntimeError("checker unreachable")

    monkeypatch.setattr(xcapi.xc, "fetch_proxies", boom)
    assert asyncio.run(xcapi._sample_once()) == 0


def test_checker_status_requires_auth():
    assert client.get("/api/checker/status").status_code == 401


def test_checker_status_reports_container_state(monkeypatch):
    async def stopped():
        return "stopped"

    monkeypatch.setattr(xcapi.xc, "container_state", stopped)
    r = client.get("/api/checker/status", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["container"] == "stopped"
    assert body["reachable"] is False

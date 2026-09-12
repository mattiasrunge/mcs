"""Envelopes, auth, validation and the system endpoints, through the ASGI app."""

import httpx
import pytest

from tests.conftest import AUTH


async def test_unauthorized_without_key(client):
    r = await client.get("/v2/health", headers={"Authorization": ""})
    assert r.status_code == 401
    assert r.json() == {"ok": False, "error": {"code": "unauthorized", "message": "missing or unknown key"}}


async def test_capabilities_lists_ops_and_roots(client):
    r = await client.get("/v2/capabilities")
    body = r.json()
    assert r.status_code == 200
    assert "faces.detect" in body["ops"] and "media.probe" in body["ops"]
    assert [root["mode"] for root in body["roots"]] == ["ro", "rw"]
    assert body["api"] == "2.0.0"


async def test_validation_error_uses_the_envelope(client):
    r = await client.post("/v2/faces/detect", json={"file": {"path": "/x", "angle": 45}})
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False and body["error"]["code"] == "invalid_request" and body["error"]["permanent"] is True
    assert "angle" in body["error"]["message"]


async def test_unknown_fields_are_refused(client):
    r = await client.post("/v2/text/embed", json={"text": "hi", "bogus": 1})
    assert r.status_code == 400
    assert "bogus" in r.json()["error"]["message"]


async def test_path_outside_roots(client):
    r = await client.post("/v2/faces/detect", json={"file": {"path": "/etc/hostname"}})
    assert r.status_code == 403
    assert r.json()["error"] == {"code": "path_outside_roots", "message": "/etc/hostname: outside the configured roots", "permanent": True}


async def test_health_reports_worker_and_queues(client, fake_worker):
    fake_worker.on("health", lambda req: {"loaded": ["minilm:cuda", "insightface"], "uptime": 5, "rss_mb": 100, "max_rss_mb": 200, "recycles": 1, "caption_fallback": {"reason": "vram_full", "since": 1}})
    r = await client.get("/v2/health")
    body = r.json()
    assert body["ok"] and body["worker"]["running"] is True
    assert body["models"] == {"minilm": {"loaded": True, "device": "cuda"}, "insightface": {"loaded": True}}
    assert body["process"]["recycles"] == 1
    assert body["degraded"] == [{"what": "caption", "reason": "vram_full", "since": 1}]
    assert body["queues"]["models"]["limit"] == 2

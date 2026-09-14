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


async def test_capabilities_carries_the_transcribe_signature(client, fake_worker):
    fake_worker.on("transcribe_signature", lambda req: {"signature": "whisper-large-v3:vad=0.2/400/500,nospeech=0.6,lang=auto,words=0"})
    r = await client.get("/v2/capabilities")
    assert r.json()["signatures"] == {"transcribe": "whisper-large-v3:vad=0.2/400/500,nospeech=0.6,lang=auto,words=0"}
    await fake_worker.stop()
    r = await client.get("/v2/capabilities")
    assert r.json()["signatures"] == {}


def test_parse_smi_reads_the_first_card_and_refuses_a_card_that_does_not_answer():
    from mcs.ops import system

    assert system.parse_smi("37, 8123, 16311, 51\n") == {"util_pct": 37, "mem_used_mb": 8123, "mem_total_mb": 16311, "temp_c": 51}
    assert system.parse_smi("37, 8123, 16311, 51\n0, 10, 16311, 40\n")["util_pct"] == 37
    assert system.parse_smi("[N/A], 8123, 16311, 51\n") is None
    assert system.parse_smi("") is None
    assert system.parse_smi("37, 8123\n") is None


async def test_health_reports_the_whole_card_when_nvidia_smi_answers(client, monkeypatch):
    from mcs.ops import system

    monkeypatch.setattr(system.os.path, "isdir", lambda path: True)
    monkeypatch.setattr(system.os, "listdir", lambda path: ["nvidia0", "nvidiactl"])
    monkeypatch.setattr(system, "_smi_cache", {"at": 0.0, "stats": None})
    monkeypatch.setattr(system, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)

    class Done:
        code = 0
        stdout = b"37, 8123, 16311, 51\n"

    calls = []

    async def fake_run(args, *, timeout, **kwargs):
        calls.append(args)
        return Done()

    monkeypatch.setattr(system, "run", fake_run)
    r = await client.get("/v2/health")
    assert r.json()["gpu"] == {"present": True, "util_pct": 37, "mem_used_mb": 8123, "mem_total_mb": 16311, "temp_c": 51}
    assert calls[0][1:] == ["--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"]

    # A second request within the cache window is answered from the first reading.
    r = await client.get("/v2/health")
    assert r.json()["gpu"]["util_pct"] == 37 and len(calls) == 1


async def test_health_without_nvidia_smi_reports_presence_alone(client, monkeypatch):
    from mcs.ops import system

    monkeypatch.setattr(system, "_smi_cache", {"at": 0.0, "stats": None})
    monkeypatch.setattr(system, "which", lambda name: None)
    r = await client.get("/v2/health")
    assert set(r.json()["gpu"]) == {"present"}

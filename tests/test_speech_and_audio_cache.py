"""Voiceprint spans share one extraction, and the audio cache extracts a file once."""

import asyncio

import pytest

from mcs.tools import audio as audio_tool


@pytest.fixture
def fake_extract(monkeypatch):
    calls = []

    async def extract(path, out, *, timeout, sample_rate=16000, channels=1):
        calls.append(path)
        await asyncio.sleep(0.05)
        if path.endswith("silent.mp4"):
            return False
        with open(out, "wb") as h:
            h.write(b"RIFF" + b"\0" * 100)
        return True

    monkeypatch.setattr(audio_tool.ffmpeg_tool, "extract_audio", extract)
    return calls


async def test_voiceprint_spans_extract_once(client, fake_worker, roots, fake_extract):
    ro, _ = roots
    (ro / "talk.mp4").write_bytes(b"mp4")
    fake_worker.on("embed_voice_segment", lambda req: None if req["end"] - req["start"] < 0.5 else {"embedding": [req["start"]], "dim": 1, "model": "titanet"})
    r = await client.post("/v2/speech/voiceprint", json={"file": {"path": str(ro / "talk.mp4")}, "spans": [{"start": 0, "end": 2}, {"start": 5, "end": 5.2}, {"start": 8, "end": 10}]})
    body = r.json()
    assert body["result"] == {"voiceprints": [{"embedding": [0], "dimension": 1}, None, {"embedding": [8], "dimension": 1}]}
    assert body["meta"]["producer"] == "titanet"
    assert fake_extract == [str(ro / "talk.mp4")]

    # The single form is unchanged, and reuses the cached extraction.
    r = await client.post("/v2/speech/voiceprint", json={"file": {"path": str(ro / "talk.mp4")}, "start": 8, "end": 10})
    assert r.json()["result"] == {"embedding": [8], "dimension": 1}
    assert len(fake_extract) == 1

    r = await client.post("/v2/speech/voiceprint", json={"file": {"path": str(ro / "talk.mp4")}})
    assert r.status_code == 400


async def test_silent_file_is_cached_as_no_audio(client, fake_worker, roots, fake_extract):
    ro, _ = roots
    (ro / "silent.mp4").write_bytes(b"mp4")
    for _ in range(2):
        r = await client.post("/v2/speech/transcribe", json={"file": {"path": str(ro / "silent.mp4")}})
        assert r.json()["result"] == {"speech": False, "audio": False, "text": "", "segments": [], "duration": 0.0}
    assert fake_extract == [str(ro / "silent.mp4")]
    assert fake_worker.requests == []


async def test_concurrent_requests_share_one_extraction(tmp_path, fake_extract):
    cache = audio_tool.AudioCache(str(tmp_path / "scratch"), ttl_seconds=0.2, budget_mb=1)
    await cache.start()
    try:
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x")
        wavs = await asyncio.gather(*(cache.acquire(str(f)) for _ in range(5)))
        assert len(set(wavs)) == 1 and fake_extract == [str(f)]
        for _ in range(5):
            cache.release(str(f))
        assert cache.snapshot()["entries"] == 1
        await asyncio.sleep(0.3)
        cache.sweep()
        assert cache.snapshot()["entries"] == 0
        # A changed file is a new key.
        await cache.acquire(str(f))
        f.write_bytes(b"xy")
        await cache.acquire(str(f))
        assert len(fake_extract) == 3
    finally:
        await cache.stop()

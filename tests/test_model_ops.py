"""The ops that forward to the model worker, against the scripted fake."""

import asyncio
import json

import pytest


async def test_text_embed_round_trip(client, fake_worker):
    fake_worker.on("embed_text", lambda req: [0.1, 0.2, 0.3])
    r = await client.post("/v2/text/embed", json={"text": "hello", "options": {"priority": "interactive"}})
    body = r.json()
    assert r.status_code == 200 and body["ok"]
    assert body["result"] == {"embedding": [0.1, 0.2, 0.3], "dimension": 3}
    assert body["meta"]["producer"].startswith("paraphrase-multilingual")
    assert fake_worker.requests[-1] == {"op": "embed_text", "text": "hello"}


async def test_generate_passes_options_and_deadline(client, fake_worker):
    fake_worker.on("generate", lambda req: {"text": "answer", "model": "qwen"})
    r = await client.post("/v2/text/generate", json={
        "messages": [{"role": "user", "content": "hi"}], "json_only": True, "model": "vlm",
        "options": {"priority": "interactive", "deadline": 1234.5, "require_gpu": True},
    })
    assert r.json()["result"] == {"text": "answer"}
    sent = fake_worker.requests[-1]
    assert sent["interactive"] is True and sent["require_gpu"] is True and sent["expires_at"] == 1234.5
    assert sent["json_only"] is True and sent["model"] == "vlm"


async def test_faces_detect_converts_pixels_to_fractions(client, fake_worker, roots):
    ro, _ = roots
    (ro / "a.jpg").write_bytes(b"jpeg")
    fake_worker.on("detect_faces", lambda req: {
        "frame": {"width": 200, "height": 100},
        "faces": [
            {"index": 0, "box": {"x": 20, "y": 10, "width": 40, "height": 20}, "confidence": 0.9, "embedding": [1.0], "landmarks": [[30, 15]]},
            {"index": 1, "box": {"x": 0, "y": 0, "width": 2, "height": 2}, "confidence": 0.5, "embedding": [2.0]},
        ],
    })
    r = await client.post("/v2/faces/detect", json={"file": {"path": str(ro / "a.jpg"), "angle": 90}, "min_size": 0.05})
    body = r.json()
    assert body["ok"], body
    assert body["result"]["frame"] == {"width": 200, "height": 100}
    assert body["result"]["faces"] == [{
        "box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}, "confidence": 0.9, "embedding": [1.0], "landmarks": [[0.15, 0.15]],
    }]
    assert fake_worker.requests[-1]["angle"] == 90 and fake_worker.requests[-1]["path"] == str(ro / "a.jpg")


async def test_faces_embed_null_when_no_face(client, fake_worker, roots):
    ro, _ = roots
    (ro / "a.jpg").write_bytes(b"jpeg")
    fake_worker.on("embed_face", lambda req: None)
    r = await client.post("/v2/faces/embed", json={"file": {"path": str(ro / "a.jpg")}, "box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}})
    assert r.json() == {"ok": True, "result": None, "meta": r.json()["meta"]}


async def test_worker_errors_are_classified(client, fake_worker, roots):
    ro, _ = roots
    (ro / "a.jpg").write_bytes(b"jpeg")

    class CardBusy(RuntimeError):
        pass

    def busy(req):
        raise CardBusy("cuda oom during caption: out of memory")

    fake_worker.on("caption", busy)
    r = await client.post("/v2/vision/caption", json={"files": [{"path": str(ro / "a.jpg")}], "prompt": "describe"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "model_unavailable" and r.json()["error"]["permanent"] is False
    assert r.headers["retry-after"] == "60"

    def expired(req):
        raise TimeoutError("generate: request expired while waiting for the VLM")

    fake_worker.on("generate", expired)
    r = await client.post("/v2/text/generate", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 504 and r.json()["error"]["code"] == "deadline_exceeded"

    def unreadable(req):
        raise ValueError("could not load image: /x")

    fake_worker.on("caption", unreadable)
    r = await client.post("/v2/vision/caption", json={"files": [{"path": str(ro / "a.jpg")}], "prompt": "describe"})
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported" and r.json()["error"]["permanent"] is True


async def test_worker_down_is_model_unavailable(client, fake_worker):
    await fake_worker.stop()
    r = await client.post("/v2/text/embed", json={"text": "hello"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "model_unavailable"


async def test_caption_requires_one_display_frame(client, fake_worker, roots):
    ro, _ = roots
    (ro / "a.jpg").write_bytes(b"x")
    (ro / "b.jpg").write_bytes(b"x")
    r = await client.post("/v2/vision/caption", json={"files": [{"path": str(ro / "a.jpg"), "angle": 90}, {"path": str(ro / "b.jpg")}], "prompt": "p"})
    assert r.status_code == 400 and "angle" in r.json()["error"]["message"]


async def test_streaming_emits_progress_then_result(client, fake_worker):
    fake_worker.on("generate", lambda req: {"text": "streamed", "model": "m"})
    events = []
    async with client.stream("POST", "/v2/text/generate", json={"messages": [{"role": "user", "content": "x"}]}, headers={"Accept": "text/event-stream"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        buffer = ""
        async for chunk in r.aiter_text():
            buffer += chunk
        for block in buffer.strip().split("\n\n"):
            lines = dict(line.split(": ", 1) for line in block.splitlines())
            events.append((lines["event"], json.loads(lines["data"])))
    assert [e[0] for e in events] == ["progress", "result"]
    assert events[0][1] == {"phase": "generate"}
    assert events[1][1]["ok"] is True and events[1][1]["result"] == {"text": "streamed"}


async def test_streaming_failure_is_an_error_event(client, fake_worker):
    def boom(req):
        raise RuntimeError("something odd")

    fake_worker.on("embed_text", boom)
    async with client.stream("POST", "/v2/text/embed", json={"text": "x"}, headers={"Accept": "text/event-stream"}) as r:
        body = "".join([chunk async for chunk in r.aiter_text()])
    assert r.status_code == 200
    assert "event: error" in body and '"code":"tool_failed"' in body


async def test_queue_depth_answers_busy(client, fake_worker):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(req):
        started.set()
        await release.wait()
        return [1.0]

    fake_worker.on("embed_text", slow)
    # limit 2 running + queue depth 1 waiting; the fourth is refused.
    tasks = [asyncio.create_task(client.post("/v2/text/embed", json={"text": str(i)})) for i in range(3)]
    await asyncio.sleep(0.2)
    refused = await client.post("/v2/text/embed", json={"text": "late"})
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "busy"
    assert refused.headers["retry-after"] == "5"
    release.set()
    results = await asyncio.gather(*tasks)
    assert all(r.status_code == 200 for r in results)

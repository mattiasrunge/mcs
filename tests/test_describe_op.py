"""`vision.describe` against the scripted worker, with the tools stubbed."""

import os

import pytest

from mcs.ops import vision
from mcs.tools import decode as decode_tool


async def test_image_describe_grounds_the_prompt_and_names_the_prompt_version(client, fake_worker, roots):
    ro, _ = roots
    (ro / "a.jpg").write_bytes(b"jpeg")
    fake_worker.on("caption", lambda req: {"captions": ["A man pours wine."], "model": "qwen3-vl-8b-instruct"})
    r = await client.post("/v2/vision/describe", json={
        "file": {"path": str(ro / "a.jpg"), "angle": 90, "mimetype": "image/jpeg"},
        "faces": [{"box": {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}}, {"box": {"x": 0.7, "y": 0.1, "width": 0.1, "height": 0.1}}],
    })
    body = r.json()
    assert r.status_code == 200, body
    assert body["result"]["description"] == "A man pours wine."
    assert body["result"]["grounded_on"] == "faces:2@0.15,0.75"
    assert body["result"]["prompt_version"] == "p4"
    assert body["result"]["stages"] == {"caption": "qwen3-vl-8b-instruct"}
    assert body["meta"]["producer"] == "qwen3-vl-8b-instruct/p4"
    sent = fake_worker.requests[-1]
    assert sent["op"] == "caption" and sent["angle"] == 90 and sent["max_new_tokens"] == 256
    assert sent["prompt"].startswith("Describe this photograph")
    assert sent["prompt"].endswith("positioned from left to right as: left, right. Refer to each by position rather than by name.")


async def test_image_describe_decodes_out_of_process_when_the_reader_cannot(client, fake_worker, roots, monkeypatch):
    ro, _ = roots
    original = ro / "a.raf"
    original.write_bytes(b"raw")
    decoded_path = str(ro / "decoded.jpg")
    (ro / "decoded.jpg").write_bytes(b"jpeg")

    async def fake_decode(path, mimetype, angle, mirror, *, scratch, timeout):
        assert path == str(original) and mimetype == "image/x-fujifilm-raf"
        return decode_tool.Decoded(decoded_path, decode_tool.normalize_angle(angle), mirror, None)

    monkeypatch.setattr(decode_tool, "decode_for_models", fake_decode)

    def caption(req):
        if req["paths"] == [str(original)]:
            raise ValueError(f"could not load image: {original}")
        return {"captions": ["A field."], "model": "vlm"}

    fake_worker.on("caption", caption)
    r = await client.post("/v2/vision/describe", json={"file": {"path": str(original), "angle": 270, "mimetype": "image/x-fujifilm-raf"}})
    assert r.status_code == 200, r.json()
    assert r.json()["result"]["description"] == "A field."
    assert [q["paths"] for q in fake_worker.requests if q["op"] == "caption"] == [[str(original)], [decoded_path]]
    assert fake_worker.requests[-1]["angle"] == 270


async def test_audio_describe_uses_the_given_transcript_or_transcribes_itself(client, fake_worker, roots, monkeypatch):
    ro, _ = roots
    (ro / "a.mp3").write_bytes(b"mp3")

    r = await client.post("/v2/vision/describe", json={"file": {"path": str(ro / "a.mp3"), "mimetype": "audio/mpeg"}, "transcript": "A talk about boats."})
    assert r.json()["result"]["description"] == "A talk about boats."
    assert r.json()["result"]["stages"] == {}

    r = await client.post("/v2/vision/describe", json={"file": {"path": str(ro / "a.mp3"), "mimetype": "audio/mpeg"}, "transcript": ""})
    assert r.json()["result"]["description"] == "Audio with no detected speech"

    async def fake_extract(path, out, *, timeout, sample_rate=16000, channels=1):
        with open(out, "wb") as h:
            h.write(b"RIFF" + b"\0" * 100)
        return True

    from mcs.tools import audio as audio_tool

    monkeypatch.setattr(audio_tool.ffmpeg_tool, "extract_audio", fake_extract)
    fake_worker.on("transcribe", lambda req: {"text": "we sailed to the island and back", "speech": True, "model": "whisper-large-v3", "segments": []})
    fake_worker.on("generate", lambda req: {"text": "A sail to an island.", "model": "qwen3-vl-8b-instruct"})
    r = await client.post("/v2/vision/describe", json={"file": {"path": str(ro / "a.mp3"), "mimetype": "audio/mpeg"}})
    body = r.json()
    assert body["result"]["description"] == "A sail to an island."
    assert body["result"]["stages"] == {"transcribe": "whisper-large-v3", "summary": "qwen3-vl-8b-instruct"}
    assert body["meta"]["producer"] == "whisper-large-v3+qwen3-vl-8b-instruct"
    generate = [q for q in fake_worker.requests if q["op"] == "generate"][-1]
    assert generate["messages"][0]["content"].startswith("You summarize what is said in a home recording.")
    assert generate["messages"][1]["content"] == "we sailed to the island and back"


async def test_video_describe_merges_frames_and_speech(client, fake_worker, roots, monkeypatch):
    ro, _ = roots
    (ro / "v.mp4").write_bytes(b"mp4")

    async def fake_frames(ctx, path, out_dir, *, at, count, strategy, fmt, quality, max_edge, progress):
        frames = []
        for i in range(count):
            p = os.path.join(out_dir, f"frame-{i:03d}.jpg")
            with open(p, "wb") as h:
                h.write(b"jpeg")
            frames.append({"path": p, "t": None})
        return frames

    monkeypatch.setattr(vision, "extract_frames", fake_frames)
    fake_worker.on("caption", lambda req: {"captions": ["People on a beach.", "People on a beach."], "model": "qwen3-vl-8b-instruct"})
    fake_worker.on("generate", lambda req: {"text": "A day at the beach with the children.", "model": "qwen3-vl-8b-instruct"})
    r = await client.post("/v2/vision/describe", json={"file": {"path": str(ro / "v.mp4"), "mimetype": "video/mp4", "angle": 270}, "transcript": "Look at the waves!"})
    body = r.json()
    assert r.status_code == 200, body
    assert body["result"]["description"] == "A day at the beach with the children."
    assert body["result"]["stages"] == {"caption": "qwen3-vl-8b-instruct", "merge": "qwen3-vl-8b-instruct"}
    assert body["meta"]["producer"] == "qwen3-vl-8b-instruct/p4"
    caption = [q for q in fake_worker.requests if q["op"] == "caption"][-1]
    assert len(caption["paths"]) == 4 and caption["angle"] == 270 and caption["prompt"].startswith("Describe this video clip")
    merge = [q for q in fake_worker.requests if q["op"] == "generate"][-1]
    # Two identical keyframe captions collapse to one, under the "Video showing:" lead the
    # multi-caption join has always used.
    assert merge["messages"][1]["content"] == "Seen in the frames: Video showing: People on a beach.\n\nSaid in the clip: Look at the waves!"

    # When the merge fails, the two halves are stapled, which is still a description.
    def boom(req):
        raise RuntimeError("cuda oom during generate")

    fake_worker.on("generate", boom)
    r = await client.post("/v2/vision/describe", json={"file": {"path": str(ro / "v.mp4"), "mimetype": "video/mp4"}, "transcript": "Look at the waves!"})
    assert r.json()["result"]["description"] == "Video showing: People on a beach.\n\nSpoken: Look at the waves!"

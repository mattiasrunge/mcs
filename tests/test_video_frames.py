"""`video.frames` request rules; the extraction itself needs ffmpeg and is stubbed."""

from mcs.ops import video


async def test_frames_need_a_selection_and_a_writable_dir(client, roots, monkeypatch):
    ro, rw = roots
    (ro / "v.mp4").write_bytes(b"mp4")
    r = await client.post("/v2/video/frames", json={"file": {"path": str(ro / "v.mp4")}, "output": {"dir": str(rw / "frames")}})
    assert r.status_code == 400 and "at" in r.json()["error"]["message"]
    r = await client.post("/v2/video/frames", json={"file": {"path": str(ro / "v.mp4")}, "at": [1.0], "output": {"dir": str(ro / "frames")}})
    assert r.status_code == 403

    async def fake(ctx, path, out_dir, *, at, count, strategy, fmt, quality, max_edge, progress):
        assert at == [1.0, 2.5] and out_dir == str(rw / "frames")
        return [{"path": f"{out_dir}/frame-000.jpg", "t": 1.0}, {"path": f"{out_dir}/frame-001.jpg", "t": 2.5}]

    monkeypatch.setattr(video, "extract_frames", fake)
    r = await client.post("/v2/video/frames", json={"file": {"path": str(ro / "v.mp4")}, "at": [1.0, 2.5], "output": {"dir": str(rw / "frames")}})
    body = r.json()
    assert r.status_code == 200, body
    assert body["result"]["frames"] == [{"path": str(rw / "frames" / "frame-000.jpg"), "t": 1.0}, {"path": str(rw / "frames" / "frame-001.jpg"), "t": 2.5}]
    assert (rw / "frames").is_dir()

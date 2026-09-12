"""`video.frames` request rules; the extraction itself needs ffmpeg and is stubbed."""

import os

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


async def test_poster_takes_one_frame_and_fits_it_like_a_photo(client, roots, monkeypatch):
    from mcs.tools import magick

    ro, rw = roots
    (ro / "v.mp4").write_bytes(b"mp4")
    seen = {}

    async def fake_extract(ctx, path, out, *, at, deinterlace):
        seen["at"], seen["deinterlace"], seen["path"] = at, deinterlace, path
        with open(out, "wb") as f:
            f.write(b"jpeg")

    async def fake_render(source, angle, mirror, plans, *, timeout):
        seen["angle"], seen["source"] = angle, os.path.basename(source)
        for plan in plans:
            with open(plan.temp, "wb") as f:
                f.write(b"a")
        return magick.Rendered((240, 320), [(240, 320)])

    monkeypatch.setattr(video, "extract_poster", fake_extract)
    monkeypatch.setattr(magick, "render", fake_render)
    r = await client.post("/v2/video/poster", json={
        "file": {"path": str(ro / "v.mp4"), "angle": 270},
        "at": 0,
        "targets": [{"output": {"path": str(rw / "512x512.avif"), "quality": 58}, "box": {"width": 512, "height": 512}}],
    })
    body = r.json()
    assert r.status_code == 200, body
    assert seen == {"at": 0.0, "deinterlace": False, "path": str(ro / "v.mp4"), "angle": 270, "source": "frame.jpg"}
    assert body["result"] == {"frame": {"width": 240, "height": 320}, "targets": [{"path": str(rw / "512x512.avif"), "width": 240, "height": 320, "bytes": 1}], "t": 0.0}
    assert (rw / "512x512.avif").read_bytes() == b"a"
    assert body["meta"]["producer"].startswith("ffmpeg/")


async def test_poster_past_the_end_is_permanent(client, roots, monkeypatch):
    from mcs.errors import McsError, TOOL_FAILED

    ro, rw = roots
    (ro / "v.mp4").write_bytes(b"mp4")

    async def nothing(ctx, path, out, *, at, deinterlace):
        raise McsError(TOOL_FAILED, f"no frame at {at:g}s", permanent=True)

    monkeypatch.setattr(video, "extract_poster", nothing)
    r = await client.post("/v2/video/poster", json={"file": {"path": str(ro / "v.mp4")}, "at": 5, "targets": [{"output": {"path": str(rw / "x.avif")}, "box": {"width": 10}}]})
    assert r.status_code == 500
    assert r.json()["error"] == {"code": "tool_failed", "message": "no frame at 5s", "permanent": True}
    assert os.listdir(rw) == []


async def test_a_seek_past_the_end_is_permanent_whatever_ffmpeg_exits(client, roots, monkeypatch, tmp_path):
    from mcs.errors import McsError
    from mcs.ops.video import extract_poster
    from mcs.tools import run as run_tool

    ro, rw = roots
    (ro / "v.mp4").write_bytes(b"mp4")

    async def failing_ffmpeg(args, *, timeout, **kw):
        return run_tool.Completed(list(args), 234, b"", b"[out#0/image2 @ 0x1] Nothing was written into output file, because at least one of its streams received no packets.\n")

    monkeypatch.setattr(video, "run", failing_ffmpeg)
    monkeypatch.setattr(video, "require", lambda name: "/usr/bin/ffmpeg")
    ctx = client.app.state.ctx
    try:
        await extract_poster(ctx, str(ro / "v.mp4"), str(tmp_path / "frame.jpg"), at=5.0, deinterlace=False)
    except McsError as exc:
        assert exc.permanent is True and exc.message == "no frame at 5s"
    else:
        raise AssertionError("a seek past the end was not refused")

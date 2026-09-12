"""`video.frames` — frames as images, at given times or spread across the file — and
`video.poster`, one frame fitted into rendition targets like a photo."""

from __future__ import annotations

import os
import shutil
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from ..context import Context
from ..errors import McsError, TOOL_FAILED, invalid
from ..schemas import FileRef, Strict, WithOptions
from ..streaming import Outcome, Progress, run_op
from ..tools import ffmpeg as ffmpeg_tool
from ..tools.run import require, run
from ..tools.versions import producer
from .image import MAX_TARGETS, RenditionTarget, render_targets

# The pixel aspect ratio is the half of the display frame a JPEG cannot carry, so it is baked
# into the frame rather than left to the reader: a captioner given an anamorphic frame
# describes a squeezed picture fluently. Commas inside the expressions are escaped for
# ffmpeg's filtergraph parser.
SQUARE_PIXELS_FILTER = r"scale=w=if(gt(sar\,1)\,trunc(iw*sar/2)*2\,iw):h=if(lt(sar\,1)\,trunc(ih/sar/2)*2\,ih),setsar=1"


class FrameOutput(Strict):
    dir: str
    format: Literal["jpeg", "png"] = "jpeg"
    quality: int = Field(default=90, ge=1, le=100)
    max: int | None = Field(default=None, gt=0)


class FramesRequest(WithOptions):
    file: FileRef
    at: list[float] | None = None
    count: int | None = Field(default=None, ge=1, le=64)
    strategy: Literal["representative", "even"] = "representative"
    output: FrameOutput


async def extract_frames(ctx: Context, path: str, out_dir: str, *, at: list[float] | None, count: int | None, strategy: str, fmt: str, quality: int, max_edge: int | None, progress: Progress) -> list[dict]:
    """Frames in the raw frame (no display-matrix turn applied) with square pixels."""
    timeout = ctx.settings.tool_timeout_seconds
    ffmpeg = require("ffmpeg")
    ext = "jpg" if fmt == "jpeg" else "png"
    # JPEG quality is ffmpeg's 2..31 scale, 2 best; map 1..100 onto it.
    qscale = str(max(2, min(31, round(31 - (quality / 100) * 29))))
    scale = f",scale='min(iw\\,{max_edge})':-2" if max_edge else ""
    frames: list[dict] = []
    if at:
        for index, t in enumerate(at):
            out = os.path.join(out_dir, f"frame-{index:03d}.{ext}")
            args = [ffmpeg, "-y", "-v", "error", "-display_rotation", "0", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", SQUARE_PIXELS_FILTER + scale]
            if fmt == "jpeg":
                args += ["-q:v", qscale]
            done = await run(args + [out], timeout=timeout)
            if done.code == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                frames.append({"path": out, "t": t})
            await progress.emit("frames", fraction=(index + 1) / len(at))
        return frames

    n = count or 4
    if strategy == "representative":
        filt = f"thumbnail=300,setpts=N/TB,{SQUARE_PIXELS_FILTER}{scale}"
    else:
        filt = f"fps=1/{max(1, n)},{SQUARE_PIXELS_FILTER}{scale}"
    pattern = os.path.join(out_dir, f"frame-%03d.{ext}")
    attempts = []
    base = [ffmpeg, "-y", "-v", "error", "-display_rotation", "0"]
    tail = ["-i", path, "-vf", filt, "-frames:v", str(n)] + (["-q:v", qscale] if fmt == "jpeg" else []) + [pattern]
    if ffmpeg_tool.gpu_present():
        attempts.append(base + ["-hwaccel", "cuda"] + tail)
    attempts.append(base + tail)
    last = ""
    for args in attempts:
        done = await run(args, timeout=timeout)
        produced = sorted(f for f in os.listdir(out_dir) if f.startswith("frame-") and f.endswith(f".{ext}"))
        if done.code == 0 and produced:
            return [{"path": os.path.join(out_dir, f), "t": None} for f in produced]
        last = done.tail() or "no frames produced"
    raise McsError(TOOL_FAILED, f"ffmpeg frame extraction failed: {last}")


async def frames(ctx: Context, req: FramesRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    if not req.at and not req.count:
        raise invalid("frames needs `at` (seconds) or `count`")
    out_dir = ctx.roots.output_dir(req.output.dir)
    async with ctx.admission.slot("tools", interactive=req.options.interactive):
        await progress.emit("frames")
        got = await extract_frames(ctx, path, out_dir, at=req.at, count=req.count, strategy=req.strategy, fmt=req.output.format, quality=req.output.quality, max_edge=req.output.max, progress=progress)
    for frame in got:
        frame["path"] = os.path.join(req.output.dir, os.path.basename(frame["path"]))
    return Outcome({"frames": got}, producer=await producer("ffmpeg"))


def register(router: APIRouter, ctx: Context) -> None:
    @router.post("/video/frames")
    async def video_frames(request: Request, body: FramesRequest):
        return await run_op(request, lambda progress: frames(ctx, body, progress))

    register_poster(router, ctx)


class PosterRequest(WithOptions):
    file: FileRef
    at: float = Field(default=1.0, ge=0)
    deinterlace: bool = False
    targets: list[RenditionTarget] = Field(min_length=1, max_length=MAX_TARGETS)


async def extract_poster(ctx: Context, path: str, out: str, *, at: float, deinterlace: bool) -> None:
    """One frame at `at` seconds as a lossless-as-JPEG-gets still, in the raw frame with square pixels.

    `-ss` after `-i`: decoding from the start and dropping frames to the timestamp is
    frame-accurate, and the poster is taken a second in, so there is nothing to skip past.
    `-display_rotation 0` keeps ffmpeg from applying the container's matrix on its own — the
    caller's angle is the only thing that turns pixels, the same rule every op keeps.
    """
    timeout = ctx.settings.tool_timeout_seconds
    ffmpeg = require("ffmpeg")
    filters = ("yadif," if deinterlace else "") + SQUARE_PIXELS_FILTER
    tail = ["-i", path, "-vf", filters, "-ss", f"{at:.3f}", "-vframes", "1", "-qscale:v", "0", "-y", "-f", "image2", "-update", "1", out]
    attempts = []
    if ffmpeg_tool.gpu_present():
        attempts.append([ffmpeg, "-v", "error", "-display_rotation", "0", "-hwaccel", "cuda"] + tail)
    attempts.append([ffmpeg, "-v", "error", "-display_rotation", "0"] + tail)
    last = ""
    for args in attempts:
        done = await run(args, timeout=timeout)
        if done.code == 0:
            break
        last = done.tail() or f"exit {done.code}"
    else:
        raise McsError(TOOL_FAILED, f"ffmpeg frame extraction failed: {last}")
    # A seek past the end exits 0 having written nothing. The same request will do so again.
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise McsError(TOOL_FAILED, f"no frame at {at:g}s", permanent=True)


async def poster(ctx: Context, req: PosterRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    async with ctx.admission.slot("tools", interactive=req.options.interactive):
        scratch = ctx.scratch_dir("poster")
        try:
            await progress.emit("frame")
            frame = os.path.join(scratch, "frame.jpg")
            await extract_poster(ctx, path, frame, at=req.at, deinterlace=req.deinterlace)
            await progress.emit("render")
            # The frame is a JPEG now: the caller's angle applies whole, no reader-applied turn to subtract.
            result = await render_targets(ctx, frame, req.file.angle, req.file.mirror, req.targets)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    result["t"] = req.at
    return Outcome(result, producer=await producer("ffmpeg", "magick"))


def register_poster(router: APIRouter, ctx: Context) -> None:
    @router.post("/video/poster")
    async def video_poster(request: Request, body: PosterRequest):
        return await run_op(request, lambda progress: poster(ctx, body, progress))

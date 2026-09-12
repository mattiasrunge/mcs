"""`fingerprint.compute` — perceptual hashes: pHash for pictures and frames, chromaprint for sound.

Vectors are {-1, 1} floats so cosine similarity approximates normalized Hamming distance
(`1 - 2·hamming/bits`), which is what lets a vector store answer near-duplicate queries.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import shutil
import struct
from typing import Literal

from fastapi import APIRouter, Request

from ..context import Context
from ..errors import McsError, TOOL_FAILED, UNSUPPORTED, invalid
from ..schemas import FileRef, WithOptions
from ..streaming import Outcome, Progress, run_op
from ..tools import ffmpeg as ffmpeg_tool
from ..tools.run import require, run
from ..tools.versions import producer
from .media import kind_of

Kind = Literal["phash-image", "phash-video", "phash-audio"]
DEFAULT_KINDS = {"image": ["phash-image"], "video": ["phash-video"], "audio": ["phash-audio"]}
VIDEO_KEYFRAMES = 8

# Chromaprint stacks FFT frames before it emits anything, so a clip below its window has no
# acoustic fingerprint at all and never will. Measured against fpcalc 1.5.1 on mono 8 kHz sine
# of a known length: ≤2.7 s exits 2 "Empty fingerprint"; 2.8 s answers 1 int32, 3.0 s 3, 3.1 s 4.
# Both floors mean the same thing — nothing to store — and neither is an error.
FPCALC_EMPTY_MARKER = "Empty fingerprint"
FPCALC_MIN_INT32S = 4


class FingerprintRequest(WithOptions):
    file: FileRef
    kinds: list[Kind] | None = None


def hash_to_vector(h) -> list[float]:
    return [1.0 if bit else -1.0 for bit in h.hash.flatten()]


def int32s_to_vector(int32s: list[int], target_dim: int = 128) -> list[float]:
    needed = target_dim // 32
    values = list(int32s[:needed]) + [0] * max(0, needed - len(int32s))
    vector: list[float] = []
    for value in values:
        for byte in struct.pack(">I", value & 0xFFFFFFFF):
            for bit in range(7, -1, -1):
                vector.append(1.0 if (byte >> bit) & 1 else -1.0)
    return vector[:target_dim]


def _entry(kind: str, vector: list[float], label: str) -> dict:
    return {"kind": kind, "dimension": len(vector), "vector": vector, "label": label}


def phash_image(path: str) -> list[dict]:
    import imagehash
    from PIL import ImageFile

    from ..modelworker import image_io

    # Slightly damaged scans decode everywhere but in PIL; take what is there.
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    with image_io.open_rgb(path) as img:
        return [_entry("phash-image", hash_to_vector(imagehash.phash(img)), "primary")]


def _phash_frames(frames: list[str]) -> list[dict]:
    import imagehash
    from PIL import Image

    out = []
    for index, frame in enumerate(frames):
        with Image.open(frame) as img:
            out.append(_entry("phash-video", hash_to_vector(imagehash.phash(img)), f"keyframe-{index}"))
    return out


async def phash_video(ctx: Context, path: str, progress: Progress) -> list[dict]:
    tmp = ctx.scratch_dir("fingerprint")
    try:
        await progress.emit("keyframes")
        frames = await ffmpeg_tool.keyframes(path, tmp, count=VIDEO_KEYFRAMES, timeout=ctx.settings.tool_timeout_seconds)
        await progress.emit("hash")
        return await asyncio.to_thread(_phash_frames, frames)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_fpcalc(stdout: str) -> list[int]:
    for line in stdout.splitlines():
        if line.startswith("FINGERPRINT="):
            return [int(x) for x in line[len("FINGERPRINT="):].split(",") if x]
    return []


async def chromaprint(ctx: Context, path: str) -> list[dict]:
    done = await run([require("fpcalc"), "-raw", path], timeout=ctx.settings.tool_timeout_seconds)
    if done.code != 0:
        if FPCALC_EMPTY_MARKER in done.stderr_text:
            return []
        raise McsError(TOOL_FAILED, f"fpcalc failed: {done.tail()}")
    int32s = parse_fpcalc(done.stdout.decode(errors="replace"))
    if len(int32s) < FPCALC_MIN_INT32S:
        return []
    return [_entry("phash-audio", int32s_to_vector(int32s, 128), "primary")]


async def _mimetype(ctx: Context, req: FingerprintRequest, path: str) -> str | None:
    if req.file.mimetype:
        return req.file.mimetype
    if ctx.exiftool is not None:
        try:
            exif = await ctx.exiftool.extract(path)
            if exif.get("MIMEType"):
                return str(exif["MIMEType"])
        except McsError:
            pass
    return mimetypes.guess_type(path)[0]


async def compute(ctx: Context, req: FingerprintRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    async with ctx.admission.slot("tools", interactive=req.options.interactive):
        kind = kind_of(await _mimetype(ctx, req, path))
        kinds = req.kinds if req.kinds is not None else DEFAULT_KINDS.get(kind)
        if not kinds:
            raise McsError(UNSUPPORTED, f"{req.file.path}: no fingerprint applies to a {kind} file")
        fingerprints: list[dict] = []
        tools = ["ffmpeg"] if "phash-video" in kinds else []
        for wanted in kinds:
            if wanted == "phash-image":
                if kind != "image":
                    raise invalid(f"phash-image needs an image, got {kind}")
                await progress.emit("hash")
                fingerprints += await asyncio.to_thread(phash_image, path)
            elif wanted == "phash-video":
                if kind != "video":
                    raise invalid(f"phash-video needs a video, got {kind}")
                fingerprints += await phash_video(ctx, path, progress)
            elif wanted == "phash-audio":
                await progress.emit("chromaprint")
                fingerprints += await chromaprint(ctx, path)
                tools.append("fpcalc")
    return Outcome({"fingerprints": fingerprints}, producer=await _producer(tools))


async def _producer(tools: list[str]) -> str:
    import importlib.metadata

    try:
        imagehash = importlib.metadata.version("ImageHash")
    except importlib.metadata.PackageNotFoundError:
        imagehash = "?"
    parts = [f"imagehash/{imagehash}"]
    if tools:
        parts.append(await producer(*dict.fromkeys(tools)))
    return "+".join(parts)


def register(router: APIRouter, ctx: Context) -> None:
    @router.post("/fingerprint/compute")
    async def fingerprint_compute(request: Request, body: FingerprintRequest):
        return await run_op(request, lambda progress: compute(ctx, body, progress))

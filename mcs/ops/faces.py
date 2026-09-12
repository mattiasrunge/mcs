"""`faces.detect` and `faces.embed` — InsightFace, boxes as fractions of the display frame."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import Field

from ..context import Context
from ..schemas import Box, FileRef, WithOptions
from ..streaming import Outcome, Progress, run_op
from ._models import model_slot

PRODUCER = "insightface/buffalo_l"


class DetectRequest(WithOptions):
    file: FileRef
    # Smallest face to report, as a fraction of the frame's shorter side.
    min_size: float = Field(default=0.0, ge=0, le=1)


class EmbedRequest(WithOptions):
    file: FileRef
    box: Box


def to_fractions(raw: dict, min_size: float) -> dict:
    """The worker measures in pixels of the display frame; the API speaks in fractions."""
    frame = raw.get("frame") or {}
    width, height = float(frame.get("width") or 0), float(frame.get("height") or 0)
    faces = []
    if width > 0 and height > 0:
        shorter = min(width, height)
        for face in raw.get("faces") or []:
            box = face.get("box") or {}
            bw, bh = float(box.get("width", 0)), float(box.get("height", 0))
            if min_size > 0 and min(bw, bh) < min_size * shorter:
                continue
            entry = {
                "box": {
                    "x": max(0.0, float(box.get("x", 0)) / width),
                    "y": max(0.0, float(box.get("y", 0)) / height),
                    "width": min(1.0, bw / width),
                    "height": min(1.0, bh / height),
                },
                "confidence": float(face.get("confidence", 0)),
                "embedding": face.get("embedding") or [],
            }
            landmarks = face.get("landmarks")
            if landmarks:
                entry["landmarks"] = [[float(x) / width, float(y) / height] for x, y in landmarks]
            faces.append(entry)
    return {"frame": {"width": int(width), "height": int(height)}, "faces": faces}


async def detect(ctx: Context, req: DetectRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    async with model_slot(ctx, req.options):
        await progress.emit("detect")
        raw = await ctx.worker.call(
            "detect_faces",
            {"path": path, "angle": req.file.angle, "mirror": req.file.mirror},
            deadline=req.options.deadline,
        )
    return Outcome(to_fractions(raw, req.min_size), producer=PRODUCER)


async def embed(ctx: Context, req: EmbedRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    async with model_slot(ctx, req.options):
        await progress.emit("embed")
        raw = await ctx.worker.call(
            "embed_face",
            {"path": path, "box": req.box.model_dump(), "angle": req.file.angle, "mirror": req.file.mirror},
            deadline=req.options.deadline,
        )
    if raw is None:
        return Outcome(None, producer=PRODUCER)
    return Outcome({"embedding": raw.get("embedding") or [], "confidence": float(raw.get("confidence", 0))}, producer=PRODUCER)


def register(router: APIRouter, ctx: Context) -> None:
    @router.post("/faces/detect")
    async def faces_detect(request: Request, body: DetectRequest):
        return await run_op(request, lambda progress: detect(ctx, body, progress))

    @router.post("/faces/embed")
    async def faces_embed(request: Request, body: EmbedRequest):
        return await run_op(request, lambda progress: embed(ctx, body, progress))

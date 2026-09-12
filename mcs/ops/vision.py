"""`vision.caption` — the VLM as a primitive. `vision.describe` (the composite) is a later phase."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import Field

from ..context import Context
from ..errors import invalid
from ..schemas import FileRef, WithOptions
from ..streaming import Outcome, Progress, run_op
from ._models import model_slot


class CaptionRequest(WithOptions):
    # A list: frames of one video belong in one conversation with the model. Every file must
    # share the same display frame, because the worker applies one angle to the batch.
    files: list[FileRef] = Field(min_length=1)
    prompt: str = Field(min_length=1)
    max_new_tokens: int = Field(default=128, ge=1, le=2048)


async def caption(ctx: Context, req: CaptionRequest, progress: Progress) -> Outcome:
    paths = [ctx.roots.input(f.path) for f in req.files]
    first = req.files[0]
    if any((f.angle, f.mirror) != (first.angle, first.mirror) for f in req.files):
        raise invalid("every file in one caption request must share angle and mirror")
    async with model_slot(ctx, req.options):
        await progress.emit("caption", message=f"{len(paths)} image(s)")
        raw = await ctx.worker.call(
            "caption",
            {"paths": paths, "prompt": req.prompt, "max_new_tokens": req.max_new_tokens, "angle": first.angle, "mirror": first.mirror},
            deadline=req.options.deadline,
        )
    return Outcome({"captions": raw.get("captions") or []}, producer=str(raw.get("model") or "vlm"))


def register(router: APIRouter, ctx: Context) -> None:
    @router.post("/vision/caption")
    async def vision_caption(request: Request, body: CaptionRequest):
        return await run_op(request, lambda progress: caption(ctx, body, progress))

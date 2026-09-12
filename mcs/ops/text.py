"""`text.embed` and `text.generate`."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from ..context import Context
from ..schemas import Message, WithOptions
from ..streaming import Outcome, Progress, run_op
from ._models import model_slot, worker_payload

EMBED_PRODUCER = "paraphrase-multilingual-MiniLM-L12-v2"


class EmbedRequest(WithOptions):
    text: str


class GenerateRequest(WithOptions):
    messages: list[Message] = Field(min_length=1)
    max_new_tokens: int = Field(default=192, ge=1, le=4096)
    json_only: bool = False
    model: Literal["instruct", "vlm"] = "instruct"


async def embed(ctx: Context, req: EmbedRequest, progress: Progress) -> Outcome:
    async with model_slot(ctx, req.options):
        vector = await ctx.worker.call("embed_text", {"text": req.text}, deadline=req.options.deadline)
    vector = list(vector or [])
    return Outcome({"embedding": vector, "dimension": len(vector)}, producer=EMBED_PRODUCER)


async def generate(ctx: Context, req: GenerateRequest, progress: Progress) -> Outcome:
    async with model_slot(ctx, req.options):
        await progress.emit("generate")
        raw = await ctx.worker.call(
            "generate",
            {
                "messages": [m.model_dump() for m in req.messages],
                "max_new_tokens": req.max_new_tokens,
                "json_only": req.json_only,
                "model": req.model,
                **worker_payload(req.options),
            },
            deadline=req.options.deadline,
        )
    return Outcome({"text": raw.get("text") or ""}, producer=str(raw.get("model") or req.model))


def register(router: APIRouter, ctx: Context) -> None:
    @router.post("/text/embed")
    async def text_embed(request: Request, body: EmbedRequest):
        return await run_op(request, lambda progress: embed(ctx, body, progress))

    @router.post("/text/generate")
    async def text_generate(request: Request, body: GenerateRequest):
        return await run_op(request, lambda progress: generate(ctx, body, progress))

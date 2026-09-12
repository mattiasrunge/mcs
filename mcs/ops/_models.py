"""Shared plumbing for the ops that forward to the model worker."""

from __future__ import annotations

from contextlib import asynccontextmanager

from ..context import Context
from ..schemas import Options


@asynccontextmanager
async def model_slot(ctx: Context, options: Options):
    async with ctx.admission.slot("models", interactive=options.interactive):
        yield


def worker_payload(options: Options) -> dict:
    payload: dict = {}
    if options.interactive:
        payload["interactive"] = True
    if options.require_gpu:
        payload["require_gpu"] = True
    return payload

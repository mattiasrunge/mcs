"""The HTTP front. `create_app()` builds it; `python -m mcs` serves it."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import API_VERSION
from .admission import Admission
from .auth import require_key
from .config import Settings
from .context import Context
from .errors import INTERNAL, INVALID_REQUEST, McsError
from .ops import audio, document, faces, fingerprint, image, media, speech, system, text, video, vision
from .roots import Roots
from .tools.audio import AudioCache
from .tools.exiftool import ExifTool
from .tools.run import which
from .worker import Worker

OP_MODULES = (media, image, video, audio, fingerprint, document, faces, speech, vision, text)


def log(message: str) -> None:
    print(f"mcs: {message}", file=sys.stderr, flush=True)


def create_app(settings: Settings | None = None, *, worker: Worker | None = None, start_worker: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    worker = worker or Worker(settings.worker_script, settings.worker_socket, log=log)
    ctx = Context(
        settings=settings,
        roots=Roots(settings.roots),
        admission=Admission(
            limit_models=settings.limit_models,
            limit_tools=settings.limit_tools,
            queue_depth=settings.queue_depth,
            interactive_reserve=settings.interactive_reserve,
        ),
        worker=worker,
        exiftool=None,
        audio=AudioCache(settings.scratch, ttl_seconds=settings.audio_cache_seconds, budget_mb=settings.audio_cache_mb, timeout=settings.tool_timeout_seconds),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.makedirs(settings.scratch, exist_ok=True)
        if not settings.keys:
            ctx.warnings.append("no keys configured: authentication is off")
            log("no keys configured (MCS_KEYS_FILE / MCS_KEY): authentication is OFF")
        if not settings.roots:
            ctx.warnings.append("no roots configured: every file request will be refused")
            log("MCS_ROOTS is empty: every file request will be refused")
        if which("exiftool"):
            ctx.exiftool = ExifTool(settings.exiftool_workers, settings.tool_timeout_seconds)
        else:
            ctx.warnings.append("exiftool is not installed: media.probe answers from ffprobe only")
        await ctx.audio.start()
        if start_worker:
            await worker.start()
        try:
            yield
        finally:
            if start_worker:
                await worker.stop()
            if ctx.exiftool is not None:
                await ctx.exiftool.stop()
            await ctx.audio.stop()

    app = FastAPI(title="MCS", version=API_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.ctx = ctx

    router = APIRouter(prefix="/v2", dependencies=[Depends(require_key)])
    for module in OP_MODULES:
        module.register(router, ctx)
    system.register(router, ctx)
    app.include_router(router)

    @app.exception_handler(McsError)
    async def mcs_error(_request: Request, exc: McsError):
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
        return JSONResponse(exc.envelope(), status_code=exc.code.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        detail = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ()) if p != 'body')}: {e.get('msg')}" for e in exc.errors())
        return JSONResponse(McsError(INVALID_REQUEST, detail or "invalid request").envelope(), status_code=INVALID_REQUEST.status)

    @app.exception_handler(Exception)
    async def unexpected(_request: Request, exc: Exception):
        log(f"internal error: {type(exc).__name__}: {exc}")
        return JSONResponse(McsError(INTERNAL, f"{type(exc).__name__}: {exc}").envelope(), status_code=INTERNAL.status)

    return app

"""`GET /v2/health` and `GET /v2/capabilities`."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter

from .. import API_VERSION
from ..context import Context
from ..errors import McsError
from ..tools.run import which
from ..tools.versions import tool_version

TOOLS = ("exiftool", "ffprobe", "ffmpeg", "fpcalc", "tesseract", "convert")


def _gpu() -> dict:
    present = bool([d for d in os.listdir("/dev") if d.startswith("nvidia")]) if os.path.isdir("/dev") else False
    return {"present": present}


def _models_from_worker(health: dict | None) -> dict:
    """The worker reports `loaded` as `family[:device…]` keys; group them by family."""
    models: dict[str, dict] = {}
    for key in (health or {}).get("loaded") or []:
        family, _, rest = str(key).partition(":")
        device = rest.split(":", 1)[0] if rest else None
        entry = models.setdefault(family, {"loaded": True})
        if device:
            entry["device"] = device
    return models


def register(router: APIRouter, ctx: Context) -> None:
    @router.get("/health")
    async def health():
        worker = await ctx.worker.health()
        body: dict = {
            "ok": True,
            "api": API_VERSION,
            "uptime": round(time.time() - ctx.started_at, 1),
            "gpu": _gpu(),
            "models": _models_from_worker(worker),
            "worker": {"running": worker is not None, "restarts": ctx.worker.restarts},
            "queues": ctx.admission.snapshot(),
        }
        if worker:
            process: dict = {}
            for src, dst in (("rss_mb", "rss_mb"), ("max_rss_mb", "max_rss_mb"), ("recycles", "recycles"), ("uptime", "uptime")):
                if src in worker:
                    process[dst] = worker[src]
            body["process"] = process
            if "vram_allocated_mb" in worker or "vram_reserved_mb" in worker:
                body["gpu"].update({k: worker[k] for k in ("vram_allocated_mb", "vram_reserved_mb") if k in worker})
            degraded = []
            fallback = worker.get("caption_fallback")
            if fallback:
                degraded.append({"what": "caption", "reason": fallback.get("reason", "card busy"), "since": fallback.get("since")})
            body["degraded"] = degraded
        if ctx.warnings:
            body["warnings"] = list(ctx.warnings)
        return body

    @router.get("/capabilities")
    async def capabilities():
        tools = {name: await tool_version(name) for name in TOOLS if which(name)}
        env = os.environ
        # What a transcript decoded right now would be stamped with: everything that decides
        # `speech.transcribe`'s output, as one string, so a caller can find transcripts made
        # with other settings. Costs no model load; absent while the worker is down.
        signatures: dict = {}
        try:
            signature = await ctx.worker.call("transcribe_signature", {})
            if isinstance(signature, dict) and signature.get("signature"):
                signatures["transcribe"] = signature["signature"]
        except McsError:
            pass
        return {
            "signatures": signatures,
            "api": API_VERSION,
            "ops": [route.path.removeprefix("/v2/").replace("/", ".") for route in router.routes if "POST" in getattr(route, "methods", set())],
            "roots": ctx.roots.describe(),
            "tools": tools,
            "models": {
                "vlm": f"{env.get('MCS_VLM_MODEL') or env.get('CFG_VLM_MODEL') or 'Qwen/Qwen3-VL-8B-Instruct'}@{env.get('MCS_VLM_QUANT') or env.get('CFG_VLM_QUANT') or 'nf4'}",
                "embed": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                "whisper": env.get("MCS_WHISPER_MODEL") or env.get("CFG_WHISPER_MODEL") or "large-v3",
                "faces": "insightface/buffalo_l",
                "instruct": env.get("MCS_INSTRUCT_MODEL") or env.get("CFG_INSTRUCT_MODEL") or "Qwen/Qwen2.5-1.5B-Instruct",
            },
            "limits": {
                "models": ctx.settings.limit_models,
                "tools": ctx.settings.limit_tools,
                "queue_depth": ctx.settings.queue_depth,
                "interactive_reserve": ctx.settings.interactive_reserve,
            },
        }

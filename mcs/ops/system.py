"""`GET /v2/health` and `GET /v2/capabilities`."""

from __future__ import annotations

import asyncio
import os
import time

from fastapi import APIRouter

from .. import API_VERSION
from ..context import Context
from ..errors import McsError
from ..tools import encode
from ..tools.run import run, which
from ..tools.versions import tool_version
from . import transcode

TOOLS = ("exiftool", "ffprobe", "ffmpeg", "fpcalc", "tesseract", "magick")


# The whole card, as `nvidia-smi` reports it: what every process on the card is doing, not what
# this one's torch has allocated. MURRiX's metrics sampler charts this — the card left its
# container with the models, so its dashboard has no other source — and it asks every 15 s;
# `nvidia-smi` costs tens of milliseconds and a fork, so one reading serves every health request
# for a few seconds rather than each request paying for its own.
SMI_QUERY = "utilization.gpu,memory.used,memory.total,temperature.gpu"
SMI_FIELDS = ("util_pct", "mem_used_mb", "mem_total_mb", "temp_c")
SMI_TIMEOUT = 5.0
SMI_CACHE_SECONDS = 5.0
_smi_cache: dict = {"at": 0.0, "stats": None}


def parse_smi(text: str) -> dict | None:
    """The first card of a `--format=csv,noheader,nounits` answer, or None for anything else."""
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < len(SMI_FIELDS):
        return None
    try:
        values = [int(float(part)) for part in parts[: len(SMI_FIELDS)]]
    except ValueError:
        # "[N/A]" or "[Not Supported]" — a card, or a driver, that does not answer this query.
        return None
    return dict(zip(SMI_FIELDS, values))


async def _card_stats() -> dict | None:
    """Utilization, memory and temperature of the first card, or None without `nvidia-smi`."""
    now = time.monotonic()
    if now - _smi_cache["at"] < SMI_CACHE_SECONDS:
        return _smi_cache["stats"]
    stats = None
    smi = which("nvidia-smi")
    if smi:
        try:
            done = await run([smi, f"--query-gpu={SMI_QUERY}", "--format=csv,noheader,nounits"], timeout=SMI_TIMEOUT)
            if done.code == 0:
                stats = parse_smi(done.stdout.decode(errors="replace"))
        except (asyncio.TimeoutError, OSError):
            stats = None
    _smi_cache.update(at=now, stats=stats)
    return stats


async def _gpu() -> dict:
    present = bool([d for d in os.listdir("/dev") if d.startswith("nvidia")]) if os.path.isdir("/dev") else False
    gpu: dict = {"present": present}
    stats = await _card_stats() if present else None
    if stats:
        gpu.update(stats)
    return gpu


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
            "gpu": await _gpu(),
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
        body["audio_cache"] = ctx.audio.snapshot()
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
        from .. import describe as rules

        return {
            "signatures": signatures,
            # What `vision.describe` asks the models, versioned per kind of file: a caption's
            # provenance is `<model>/<version>`, and this is the half a caller cannot learn from
            # the model name. Per kind so a caller re-describes only what a bump covers.
            "prompts": {"describe": dict(rules.PROMPT_VERSIONS)},
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
                "encodes": ctx.settings.limit_encodes,
                "queue_depth": ctx.settings.queue_depth,
                "interactive_reserve": ctx.settings.interactive_reserve,
            },
            # What decides a transcode's fate on this box: the encoder that will run and the
            # CPU-decode budget a refusal is measured against. A caller records the budget's
            # signature beside a refusal, so a raised budget re-opens the gap on its own.
            "encode": {
                "video_encoder": await transcode.gpu_encoder(ctx) or encode.CPU_ENCODER,
                "cpu_budget": encode.Budget(
                    ctx.settings.cpu_encode_max_mb, ctx.settings.cpu_encode_mb_per_1k_frames, ctx.settings.cpu_encode_max_segments
                ).signature,
            },
        }

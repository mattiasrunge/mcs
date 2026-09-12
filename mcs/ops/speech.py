"""`speech.*` — transcription, speaker turns, voiceprints and active-speaker scoring.

Every op here takes any container with sound and extracts the audio itself (16 kHz mono PCM
in scratch, cached for a while — see tools/audio.py); the models never see the caller's file.
A file with no audio stream is a normal answer (`speech: false`, `turns: []`, `null`), not an
error — plenty of any archive is silent phone clips.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, Request
from pydantic import Field

from ..context import Context
from ..errors import invalid
from ..schemas import Box, FileRef, Strict, WithOptions
from ..streaming import Outcome, Progress, run_op
from ._models import model_slot


class Vad(Strict):
    min_silence_ms: int | None = Field(default=None, ge=0)


class TranscribeRequest(WithOptions):
    file: FileRef
    language: str | None = Field(default=None, min_length=2, max_length=3)
    vad: Vad | None = None
    word_timestamps: bool | None = None


class DiarizeRequest(WithOptions):
    file: FileRef


class Span(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class VoiceprintRequest(WithOptions):
    """One span (`start`/`end`) or many (`spans`); the audio is extracted once either way."""

    file: FileRef
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, gt=0)
    spans: list[Span] | None = None


class TrackPoint(Strict):
    t: float = Field(ge=0)
    box: Box


class ActiveSpeakerRequest(WithOptions):
    file: FileRef
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    track: list[TrackPoint]


@asynccontextmanager
async def extracted_audio(ctx: Context, path: str, progress: Progress):
    """The file's sound as a cached WAV, or None when it has none. Held for the block's duration."""
    await progress.emit("extract-audio")
    wav = await ctx.audio.acquire(path)
    try:
        yield wav
    finally:
        ctx.audio.release(path)


def _silent_transcript() -> dict:
    return {"speech": False, "text": "", "segments": [], "duration": 0.0}


async def transcribe(ctx: Context, req: TranscribeRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    async with model_slot(ctx, req.options):
        async with extracted_audio(ctx, path, progress) as wav:
            if wav is None:
                return Outcome(_silent_transcript(), producer="none")
            payload: dict = {"path": wav}
            if req.language:
                payload["language"] = req.language
            if req.vad and req.vad.min_silence_ms is not None:
                payload["vad_min_silence_ms"] = req.vad.min_silence_ms
            if req.word_timestamps is not None:
                payload["word_timestamps"] = req.word_timestamps
            await progress.emit("transcribe")
            raw = await ctx.worker.call("transcribe", payload, deadline=req.options.deadline)
    result = {
        "speech": bool(raw.get("speech")),
        "text": raw.get("text") or "",
        "segments": raw.get("segments") or [],
        "duration": float(raw.get("duration") or 0.0),
    }
    if raw.get("language"):
        result["language"] = raw["language"]
        result["language_probability"] = float(raw.get("language_probability") or 0.0)
    producer = raw.get("signature") or raw.get("model") or "whisper"
    return Outcome(result, producer=str(producer))


async def diarize(ctx: Context, req: DiarizeRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    async with model_slot(ctx, req.options):
        async with extracted_audio(ctx, path, progress) as wav:
            if wav is None:
                return Outcome({"speech": False, "duration": 0.0, "window": 0, "turns": []}, producer="none")
            await progress.emit("diarize")
            raw = await ctx.worker.call("diarize", {"path": wav}, deadline=req.options.deadline)
    turns = [{"start": t["start"], "end": t["end"], "speaker": t.get("localSpeaker") or t.get("speaker")} for t in raw.get("turns") or []]
    result = {"speech": bool(raw.get("speech")), "duration": float(raw.get("duration") or 0.0), "window": raw.get("window"), "turns": turns}
    return Outcome(result, producer=str(raw.get("model") or "sortformer"))


def _voiceprint_of(raw: dict | None) -> dict | None:
    if raw is None:
        return None
    return {"embedding": raw.get("embedding") or [], "dimension": int(raw.get("dim") or len(raw.get("embedding") or []))}


async def voiceprint(ctx: Context, req: VoiceprintRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    if req.spans is None and (req.start is None or req.end is None):
        raise invalid("voiceprint needs start and end, or spans")
    spans = req.spans if req.spans is not None else [Span(start=req.start, end=req.end)]
    producer = "titanet"
    async with model_slot(ctx, req.options):
        async with extracted_audio(ctx, path, progress) as wav:
            results: list[dict | None] = []
            for index, span in enumerate(spans):
                if wav is None:
                    results.append(None)
                    continue
                await progress.emit("voiceprint", fraction=(index + 1) / len(spans))
                raw = await ctx.worker.call("embed_voice_segment", {"path": wav, "start": span.start, "end": span.end}, deadline=req.options.deadline)
                if raw is not None:
                    producer = str(raw.get("model") or producer)
                results.append(_voiceprint_of(raw))
    if req.spans is None:
        return Outcome(results[0], producer=producer)
    return Outcome({"voiceprints": results}, producer=producer)


async def active_speaker(ctx: Context, req: ActiveSpeakerRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    async with model_slot(ctx, req.options):
        async with extracted_audio(ctx, path, progress) as wav:
            if wav is None:
                return Outcome(None, producer="none")
            await progress.emit("score")
            raw = await ctx.worker.call(
                "active_speaker_score",
                {"video": path, "audio": wav, "start": req.start, "end": req.end, "track": [p.model_dump() for p in req.track]},
                deadline=req.options.deadline,
            )
    if raw is None:
        return Outcome(None, producer="lr-asd")
    return Outcome({"score": float(raw.get("score", 0)), "frames": int(raw.get("frames", 0))}, producer="lr-asd")


def register(router: APIRouter, ctx: Context) -> None:
    @router.post("/speech/transcribe")
    async def speech_transcribe(request: Request, body: TranscribeRequest):
        return await run_op(request, lambda progress: transcribe(ctx, body, progress))

    @router.post("/speech/diarize")
    async def speech_diarize(request: Request, body: DiarizeRequest):
        return await run_op(request, lambda progress: diarize(ctx, body, progress))

    @router.post("/speech/voiceprint")
    async def speech_voiceprint(request: Request, body: VoiceprintRequest):
        return await run_op(request, lambda progress: voiceprint(ctx, body, progress))

    @router.post("/speech/active_speaker")
    async def speech_active_speaker(request: Request, body: ActiveSpeakerRequest):
        return await run_op(request, lambda progress: active_speaker(ctx, body, progress))

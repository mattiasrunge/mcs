"""`vision.caption` — the VLM as a primitive — and `vision.describe`, the composite most
callers want: a description of a photo, a video or a recording, in prose."""

from __future__ import annotations

import os
import shutil
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from .. import describe as rules
from ..context import Context
from ..errors import McsError, UNSUPPORTED, invalid
from ..schemas import Box, FileRef, Strict, WithOptions
from ..streaming import Outcome, Progress, run_op
from ..tools import decode as decode_tool
from ._models import model_slot
from .media import kind_of
from .video import extract_frames


class CaptionRequest(WithOptions):
    # A list: frames of one video belong in one conversation with the model. Every file must
    # share the same display frame, because the worker applies one angle to the batch.
    files: list[FileRef] = Field(min_length=1)
    prompt: str = Field(min_length=1)
    max_new_tokens: int = Field(default=128, ge=1, le=2048)


class FaceRef(Strict):
    box: Box


class DescribePrompts(Strict):
    image: str | None = None
    video: str | None = None
    summary: str | None = None


class DescribeRequest(WithOptions):
    file: FileRef
    # Boxes a face detection already found, as fractions of the display frame: they ground the
    # caption in how many people there are and where, never in who.
    faces: list[FaceRef] | None = None
    # What is said, when the caller already has it (a transcript's summary). Without it MCS
    # transcribes and summarizes the file itself.
    transcript: str | None = None
    prompt: DescribePrompts | None = None
    max_new_tokens: int | None = Field(default=None, ge=1, le=2048)


async def caption_paths(ctx: Context, paths: list[str], prompt: str, tokens: int, angle: int, mirror: bool, *, mimetype: str | None, deadline: float | None) -> dict:
    """The worker's caption, with the RAW/HEIF decode fallback when its readers cannot open the file.

    Only a single image falls back — a video's frames are JPEGs this process wrote.
    """
    payload = {"paths": paths, "prompt": prompt, "max_new_tokens": tokens, "angle": angle, "mirror": mirror}
    try:
        return await ctx.worker.call("caption", payload, deadline=deadline)
    except McsError as exc:
        if exc.code is not UNSUPPORTED or len(paths) != 1 or not decode_tool.is_unreadable(exc.message):
            raise
    decoded = await decode_tool.decode_for_models(paths[0], mimetype, angle, mirror, scratch=ctx.settings.scratch, timeout=ctx.settings.tool_timeout_seconds)
    try:
        return await ctx.worker.call("caption", {**payload, "paths": [decoded.path], "angle": decoded.angle, "mirror": decoded.mirror}, deadline=deadline)
    finally:
        decoded.cleanup()


async def caption(ctx: Context, req: CaptionRequest, progress: Progress) -> Outcome:
    paths = [ctx.roots.input(f.path) for f in req.files]
    first = req.files[0]
    if any((f.angle, f.mirror) != (first.angle, first.mirror) for f in req.files):
        raise invalid("every file in one caption request must share angle and mirror")
    async with model_slot(ctx, req.options):
        await progress.emit("caption", message=f"{len(paths)} image(s)")
        raw = await caption_paths(ctx, paths, req.prompt, req.max_new_tokens, first.angle, first.mirror, mimetype=first.mimetype, deadline=req.options.deadline)
    return Outcome({"captions": raw.get("captions") or []}, producer=str(raw.get("model") or "vlm"))


async def _mimetype(ctx: Context, req_file: FileRef, path: str) -> str | None:
    if req_file.mimetype:
        return req_file.mimetype
    if ctx.exiftool is not None:
        try:
            exif = await ctx.exiftool.extract(path)
            if exif.get("MIMEType"):
                return str(exif["MIMEType"])
        except McsError:
            pass
    import mimetypes

    return mimetypes.guess_type(path)[0]


async def _transcript_summary(ctx: Context, path: str, progress: Progress, deadline: float | None) -> tuple[str, str | None, str | None]:
    """(summary text, whisper model, summary model) for a file MCS transcribes itself."""
    wav = await ctx.audio.acquire(path)
    try:
        if wav is None:
            return "", None, None
        await progress.emit("transcribe")
        raw = await ctx.worker.call("transcribe", {"path": wav}, deadline=deadline)
    finally:
        ctx.audio.release(path)
    text = str(raw.get("text") or "").strip()
    whisper = str(raw.get("model") or "whisper")
    if not text:
        return "", whisper, None
    summary, summary_model = await _summarize(ctx, text, None, progress, deadline)
    return summary, whisper, summary_model


async def _summarize(ctx: Context, text: str, system_override: str | None, progress: Progress, deadline: float | None) -> tuple[str, str | None]:
    """One paragraph saying what was said, or the transcript's opening when no model answers."""
    await progress.emit("summarize")
    try:
        raw = await ctx.worker.call(
            "generate",
            {
                "messages": [
                    {"role": "system", "content": system_override or rules.PROMPT_TRANSCRIPT_SUMMARY_SYSTEM},
                    {"role": "user", "content": text[: rules.SUMMARY_INPUT_CHARS]},
                ],
                "max_new_tokens": rules.TOKENS_SUMMARY,
                "json_only": False,
                "model": "vlm",
            },
            deadline=deadline,
        )
        summary = str(raw.get("text") or "").strip()
        if summary:
            return summary, str(raw.get("model") or "vlm")
    except McsError:
        pass
    return text[: rules.SUMMARY_MIN_CHARS], None


async def describe(ctx: Context, req: DescribeRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    mimetype = await _mimetype(ctx, req.file, path)
    kind = kind_of(mimetype)
    prompts = req.prompt or DescribePrompts()
    boxes = [f.box.model_dump() for f in req.faces] if req.faces else []
    deadline = req.options.deadline
    stages: dict = {}

    if kind == "image":
        prompt = (prompts.image or rules.PROMPT_IMAGE) + rules.face_grounding(boxes)
        async with model_slot(ctx, req.options):
            await progress.emit("caption")
            raw = await caption_paths(ctx, [path], prompt, req.max_new_tokens or rules.TOKENS_IMAGE, req.file.angle, req.file.mirror, mimetype=mimetype, deadline=deadline)
        captions = raw.get("captions") or []
        if not captions or not str(captions[0]).strip():
            raise McsError(UNSUPPORTED, f"{req.file.path}: the captioner produced nothing", permanent=False)
        stages["caption"] = str(raw.get("model") or "vlm")
        producer = f"{stages['caption']}/{rules.PROMPT_VERSION}"
        result = {"description": str(captions[0]).strip(), "grounded_on": rules.grounded_on(boxes), "prompt_version": rules.PROMPT_VERSION, "stages": stages}
        return Outcome(result, producer=producer)

    if kind == "audio":
        async with model_slot(ctx, req.options):
            if req.transcript is not None:
                spoken = req.transcript.strip()
            else:
                spoken, whisper, summary_model = await _transcript_summary(ctx, path, progress, deadline)
                if whisper:
                    stages["transcribe"] = whisper
                if summary_model:
                    stages["summary"] = summary_model
        producer = rules.model_name(stages.get("transcribe"), stages.get("summary")) or "transcript"
        result = {"description": spoken or rules.NO_SPEECH, "prompt_version": rules.PROMPT_VERSION, "stages": stages}
        return Outcome(result, producer=producer)

    if kind != "video":
        raise McsError(UNSUPPORTED, f"{req.file.path}: cannot describe a {kind} file")

    tmp = ctx.scratch_dir("describe")
    try:
        async with model_slot(ctx, req.options):
            await progress.emit("keyframes")
            frames = await extract_frames(ctx, path, tmp, at=None, count=rules.VIDEO_KEYFRAMES, strategy="representative", fmt="jpeg", quality=93, max_edge=None, progress=progress)
            await progress.emit("caption", message=f"{len(frames)} keyframes")
            raw = await caption_paths(ctx, [f["path"] for f in frames], prompts.video or rules.PROMPT_VIDEO, req.max_new_tokens or rules.TOKENS_VIDEO, req.file.angle, req.file.mirror, mimetype="image/jpeg", deadline=deadline)
            stages["caption"] = str(raw.get("model") or "vlm")
            seen = rules.join_video_captions([str(c) for c in (raw.get("captions") or [])])

            if req.transcript is not None:
                spoken = req.transcript.strip()
            else:
                spoken, whisper, summary_model = await _transcript_summary(ctx, path, progress, deadline)
                if whisper:
                    stages["transcribe"] = whisper
                if summary_model:
                    stages["summary"] = summary_model

            # One description from both halves, rather than the two stapled together — same
            # model that captioned the frames. A merge that fails leaves the concatenation,
            # which is a correct description, not a precondition for one.
            description = rules.compose_description(seen, spoken)
            await progress.emit("merge")
            try:
                merged = await ctx.worker.call(
                    "generate",
                    {
                        "messages": [
                            {"role": "system", "content": prompts.summary or rules.PROMPT_VIDEO_SUMMARY_SYSTEM},
                            {"role": "user", "content": rules.video_summary_prompt(seen, spoken)},
                        ],
                        "max_new_tokens": req.max_new_tokens or rules.TOKENS_VIDEO,
                        "json_only": False,
                        "model": "vlm",
                    },
                    deadline=deadline,
                )
                if str(merged.get("text") or "").strip():
                    description = str(merged["text"]).strip()
                    stages["merge"] = str(merged.get("model") or "vlm")
            except McsError:
                pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    producer = rules.model_name(f"{stages['caption']}/{rules.PROMPT_VERSION}", stages.get("transcribe"), stages.get("summary"))
    return Outcome({"description": description, "prompt_version": rules.PROMPT_VERSION, "stages": stages}, producer=producer)


def register(router: APIRouter, ctx: Context) -> None:
    @router.post("/vision/caption")
    async def vision_caption(request: Request, body: CaptionRequest):
        return await run_op(request, lambda progress: caption(ctx, body, progress))

    @router.post("/vision/describe")
    async def vision_describe(request: Request, body: DescribeRequest):
        return await run_op(request, lambda progress: describe(ctx, body, progress))

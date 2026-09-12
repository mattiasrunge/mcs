#!/usr/bin/env python3
"""
Model primitives, shared by inference_call.py (one-shot) and model_server.py (resident).

These are model operations only — they take a file path or a string, run one model, and
return its raw output. Anything that knows what a *media file* is (mimetype routing,
keyframe extraction, joining captions with a transcript) belongs to the caller; media
composes these in TypeScript in modules/media/bin/describe-file.

Models come from model_registry, so the same code runs whether they are loaded for this
call and freed (one-shot) or kept resident (server). Heavy imports stay inside functions:
importing this module must not drag in torch.
"""

import os
import sys
import time

import model_registry as registry

# Longest side the captioner sees. Raised from 1024 after measuring the sample it was costing:
# all 45 bake-off images exceeded the old cap, at a median longest side of 4896px, so the median
# image was downscaled 4.78x and a face spanning 3% of the frame arrived ~31px across. No model
# reads an expression off 31 pixels, which made this cap — not the model — a prime suspect for
# the bad facial descriptions that motivated the whole replacement.
#
# Paired with VLM_MAX_PIXELS below: this bounds the raster, that bounds the tiling.
MAX_IMAGE_DIM = int(os.environ.get('CFG_CAPTION_MAX_DIM', '2048'))

# Vision-token budget for the processor's dynamic tiling, expressed the way the processor wants
# it. Qwen3-VL re-tiles whatever it is given, so MAX_IMAGE_DIM alone does not bound prefill and a
# larger raster with an unchanged budget is silently downscaled again inside the processor.
#
# 28 is the patch size, so this reads as "2048 vision tokens". Measured on fry over 45 images:
# doubling it from 1024 cost +0.23GB peak VRAM and no wall-clock at all (4928ms vs 5216ms per
# caption), which is why the higher budget is the default rather than an option.
VLM_MAX_PIXELS = int(os.environ.get('CFG_CAPTION_MAX_PIXELS', str(2048 * 28 * 28)))

VLM = "vlm"
INSIGHTFACE = "insightface"
INSTRUCT = "instruct"
MINILM = "minilm"
WHISPER = "whisper"
SORTFORMER = "sortformer"
TITANET = "titanet"
ASD = "asd"

# Reported as the caption's provenance and written to `describedBy`. Derived from the config
# rather than hardcoded so a model change cannot silently keep claiming the old name — the same
# reason `generate` derives its own.
def _vlm_model_name() -> str:
    return registry.VLM_MODEL.split("/")[-1].lower()


def _whisper_model_name() -> str:
    return f"whisper-{registry.WHISPER_MODEL}"


def transcribe_signature(language: str | None = None, min_silence_ms: int | None = None, word_timestamps: bool | None = None) -> str:
    """Everything that decides what `transcribe` writes, as one string.

    A transcript is stamped with this (`transcribedWith` on the transcript node) and
    `media-scan` compares the stamp against what this returns *now*: a file whose stamp
    differs was decoded with other settings and is re-described. Derived here, on the
    same side that decodes, so the two can never disagree — a literal on the scanning
    side that drifted from what is actually written would mark every file stale forever
    and re-describing would write the same disagreeing value back.

    So every setting that changes the output belongs in here, and nothing else does: the
    model, the VAD, the no-speech ceiling, the language, and whether words are timed.
    Beam size and the conditioning flag are constants in `transcribe`; if either becomes
    configurable it goes in too.
    """
    # Per-request overrides (the HTTP API's `language`, `vad.min_silence_ms`, `word_timestamps`)
    # take part exactly like the configured values do, so a transcript made with them is
    # stamped with what actually ran.
    vad = dict(registry.whisper_vad_parameters())
    if min_silence_ms is not None:
        vad['min_silence_duration_ms'] = int(min_silence_ms)
    lang = registry.WHISPER_LANGUAGE if language is None else language
    words = registry.WHISPER_WORD_TIMESTAMPS if word_timestamps is None else word_timestamps
    return (
        f"{_whisper_model_name()}"
        f":vad={vad['threshold']}/{vad['speech_pad_ms']}/{vad['min_silence_duration_ms']}"
        f",nospeech={registry.WHISPER_NO_SPEECH_PROB_MAX}"
        f",lang={lang or 'auto'}"
        f",words={1 if words else 0}"
    )


def _resize_image(image):
    """Downscale image so the longest side is MAX_IMAGE_DIM.

    LANCZOS, explicitly. The default filter for `Image.resize` is bicubic, which aliases badly
    at the ~2.4x reduction this typically performs and smears exactly the fine facial detail the
    captioner is being asked about.
    """
    from PIL import Image as _Image

    w, h = image.size
    if max(w, h) <= MAX_IMAGE_DIM:
        return image
    scale = MAX_IMAGE_DIM / max(w, h)
    return image.resize((int(w * scale), int(h * scale)), _Image.LANCZOS)


def _normalize_angle(angle) -> int:
    """Quarter turns only; anything else means no rotation."""
    try:
        a = int(round(float(angle))) % 360
    except (TypeError, ValueError):
        return 0
    return a if a in (90, 180, 270) else 0


def _read_upright(cv2, image_path: str, angle, mirror):
    """Read an image into the *display* frame: rotated CCW by `angle`, then flopped.

    The file's own EXIF Orientation is not what decides that — the node's `angle` is, because it
    may be a manual override and the tag may simply be wrong, and every other step rotates by it.
    `cv2.imread` had to be talked out of applying the tag with IMREAD_IGNORE_ORIENTATION; the
    read now goes through `image_io`, which owns that question and reads HEIF and RAW besides.
    """
    # Read through PIL rather than cv2.imread: cv2 has no HEIF or RAW decoder at all. The
    # rotation moves with it — `open_display` is the one place that knows which formats arrive
    # already turned.
    import image_io

    img = image_io.open_display_bgr(image_path, angle, mirror)
    if img is None:
        raise ValueError(f"could not load image: {image_path}")

    return img


def _is_cuda_oom(exc: Exception) -> bool:
    """True if the exception is an out-of-VRAM error (so we can retry on CPU)."""
    import torch
    if isinstance(exc, torch.cuda.OutOfMemoryError):
        return True
    message = str(exc).lower()
    if "out of memory" in message:
        return isinstance(exc, RuntimeError) or "onnxruntime" in message
    # onnxruntime does not raise a torch error and does not say "out of memory";
    # its arena reports "Failed to allocate memory for requested buffer of size N".
    return "failed to allocate memory" in message


def _caption_images(images: list, prompt: str, max_new_tokens: int, device: str) -> list[str]:
    """Caption a batch of PIL images with the VLM on `device`, returning one caption.

    Returns a single-element list even for several images. A video's keyframes are frames of one
    clip, and the model is told so — one instruction over all of them produces a description of
    the clip instead of four sentences about four stills that `joinVideoCaptions` then has to
    de-duplicate. It is also ~4x cheaper, which matters on the step that is already the slowest
    in the pipeline.
    """
    import torch

    processor, model = registry.vlm(device)
    try:
        # Bound the tiling per call rather than at from_pretrained: the processor is cached with
        # the model, and a resolution change should not force a weight reload. If a future
        # transformers moves this attribute the symptom is silent — vision-token counts stop
        # responding to the setting — so it is asserted rather than assumed.
        image_processor = getattr(processor, "image_processor", None)
        if image_processor is None:
            raise RuntimeError("caption: processor has no image_processor to bound tiling on")
        image_processor.max_pixels = VLM_MAX_PIXELS
        image_processor.min_pixels = min(
            getattr(image_processor, "min_pixels", VLM_MAX_PIXELS // 4), VLM_MAX_PIXELS // 4
        )

        content = [{"type": "image", "image": image} for image in images]
        if len(images) > 1:
            content.append({
                "type": "text",
                "text": f"These are {len(images)} frames from the same video clip, in order.",
            })
        content.append({"type": "text", "text": prompt})

        inputs = processor.apply_chat_template(
            [{"role": "user", "content": content}],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device)

        with torch.inference_mode():
            # Greedy. Beam search triples the cost of the most expensive step in the pipeline for
            # no benefit on free-form description, and greedy is reproducible — the same argument
            # `generate` already makes for the instruct family.
            out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

        # Slice the prompt off before decoding. There is no post_process_generation equivalent for
        # a chat model, and decoding the whole sequence hands the instruction back as the caption.
        generated = out[:, inputs["input_ids"].shape[1]:]
        text = processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
    finally:
        # Drop our references here, in the scope that holds them, before asking the registry to
        # reclaim — see model_registry.release().
        del model, processor
        registry.release(f"vlm:{device}")

    return [text]


def _caption_independent_images(samples: list[dict], max_new_tokens: int, device: str) -> list[str]:
    """Generate one caption per independent image in one padded VLM batch.

    This deliberately does not call `_caption_images`: several images there are
    frames of one video and form one conversation. Here every sample retains its
    own prompt and produces its own decoded sequence.
    """
    import torch

    processor, model = registry.vlm(device)
    try:
        image_processor = getattr(processor, "image_processor", None)
        if image_processor is None:
            raise RuntimeError("caption: processor has no image_processor to bound tiling on")
        image_processor.max_pixels = VLM_MAX_PIXELS
        image_processor.min_pixels = min(
            getattr(image_processor, "min_pixels", VLM_MAX_PIXELS // 4),
            VLM_MAX_PIXELS // 4,
        )

        conversations = [
            [{
                "role": "user",
                "content": [
                    {"type": "image", "image": sample["image"]},
                    {"type": "text", "text": sample["prompt"]},
                ],
            }]
            for sample in samples
        ]
        # Decoder-only generation must end every prompt on a real token.
        # Right padding makes shorter requests generate from padding instead.
        processor.tokenizer.padding_side = "left"
        inputs = processor.apply_chat_template(
            conversations,
            tokenize=True,
            add_generation_prompt=True,
            padding=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device)

        with torch.inference_mode():
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )

        generated = out[:, inputs["input_ids"].shape[1]:]
        return [text.strip() for text in processor.batch_decode(generated, skip_special_tokens=True)]
    finally:
        del model, processor
        registry.release(f"vlm:{device}")


def _defer_caption_oom(exc: Exception) -> Exception:
    """Apply the established single-caption OOM policy and return its error."""
    print(
        f"[caption] CUDA OOM ({exc}); deferring this caption rather than degrading. "
        f"Held off for {registry.VLM_BACKOFF_SECONDS:.0f}s before cuda is "
        f"preflighted again; `inference-status` reports it while it lasts.",
        file=sys.stderr,
    )
    registry.evict("vlm:cuda")
    registry.cleanup_torch()
    registry.note_card_busy(f"cuda oom: {str(exc).strip()[:160]}")
    return registry.CardBusy(f"cuda oom during caption: {str(exc).strip()[:160]}")


def _caption_independent_on_card(samples: list[dict], max_new_tokens: int) -> list[str | Exception]:
    """Run an independent-image batch, splitting it back to singles on CUDA OOM."""
    def run():
        device = registry.vlm_device()
        try:
            return _caption_independent_images(samples, max_new_tokens, device)
        except Exception as exc:
            if device != "cuda" or not _is_cuda_oom(exc):
                raise
            if len(samples) == 1:
                return [_defer_caption_oom(exc)]

            # Batch size is the only thing relaxed. Images, prompts, model,
            # precision, token budgets, and generation settings remain exact.
            print(
                f"[caption] CUDA OOM for batch of {len(samples)}; retrying the original "
                "single-image requests",
                file=sys.stderr,
            )
            # The traceback otherwise keeps the failed generate frame (and its
            # CUDA tensors) alive throughout the single-image retries.
            exc.__traceback__ = None
            registry.cleanup_torch()
            outcomes: list[str | Exception] = []
            for sample in samples:
                single_device = device
                try:
                    single_device = registry.vlm_device()
                    outcomes.extend(_caption_independent_images([sample], max_new_tokens, single_device))
                except Exception as single_exc:
                    single_exc.__traceback__ = None
                    if single_device == "cuda" and _is_cuda_oom(single_exc):
                        outcomes.append(_defer_caption_oom(single_exc))
                    else:
                        outcomes.append(single_exc)
            return outcomes

    with registry.family_lock(VLM):
        return registry.run_on_model_thread(VLM, run)


def _caption_on_card(images: list, prompt: str, max_new_tokens: int) -> list[str]:
    """Caption on the GPU, or defer. There is no lower-quality path.

    `registry.vlm_device()` raises `CardBusy` when the card cannot hold the model, and this does
    not catch it: the pipeline step fails and is retried later. That is the deliberate reversal of
    what Florence did — Florence on the CPU was the same answer 5x slower, so degrading was free
    in quality terms, whereas this model in fp32 on the CPU is ~35GB against a 10GB container and
    would take the model server down rather than run slowly.

    A CUDA OOM that the preflight could not see is recorded and re-raised for the same reason: a
    deferred caption costs a retry, a degraded one costs permanently worse text in the search
    index. See the accuracy-over-time rule in work/plans/replace-florence-captioner.md.

    The model work runs on the VLM's own thread — see registry.run_on_model_thread.
    """
    def run():
        device = registry.vlm_device()
        try:
            return _caption_images(images, prompt, max_new_tokens, device)
        except Exception as exc:
            if device == "cuda" and _is_cuda_oom(exc):
                print(
                    f"[caption] CUDA OOM ({exc}); deferring this caption rather than degrading. "
                    f"Held off for {registry.VLM_BACKOFF_SECONDS:.0f}s before cuda is "
                    f"preflighted again; `inference-status` reports it while it lasts.",
                    file=sys.stderr,
                )
                # Drop the resident copy so the retry starts from a clean card.
                registry.evict("vlm:cuda")
                registry.cleanup_torch()
                registry.note_card_busy(f"cuda oom: {str(exc).strip()[:160]}")
                raise registry.CardBusy(f"cuda oom during caption: {str(exc).strip()[:160]}") from exc
            raise

    with registry.family_lock(VLM):
        return registry.run_on_model_thread(VLM, run)


def caption(paths: list[str], prompt: str, max_new_tokens: int, angle=0, mirror=False) -> dict:
    """Caption one or more images with the VLM.

    Takes a list so a video's keyframes are captioned inside a single acquisition of the VLM
    family lock: one call per frame would serialise on the lock anyway and churn the model in
    and out of VRAM between frames. They are also described as one clip rather than four
    stills — see _caption_images.

    `angle`/`mirror` put the model in the same display frame as everything else, instead of
    trusting the EXIF tag: a sideways image describes badly. Video keyframes come out of
    ffmpeg already upright, so they pass 0.
    """
    from PIL import Image

    import image_io

    if not paths:
        raise ValueError("caption: no paths given")

    images = []
    for path in paths:
        if not os.path.exists(path):
            raise FileNotFoundError(f"image not found: {path}")
        # HEIC and RAW included, and the display frame handled in one place — HEIF arrives
        # already rotated where everything else does not. See image_io.
        images.append(_resize_image(image_io.open_display(path, angle, mirror)))

    return {"captions": _caption_on_card(images, prompt, max_new_tokens), "model": _vlm_model_name()}


def caption_batch(requests: list[dict]) -> list[dict]:
    """Caption independent single-image requests with per-request outcomes."""
    import image_io

    replies: list[dict | None] = [None] * len(requests)
    prepared: list[tuple[int, dict]] = []
    max_new_tokens: int | None = None

    for index, request in enumerate(requests):
        try:
            paths = request.get("paths")
            prompt = request.get("prompt")
            tokens = int(request.get("max_new_tokens", 256))
            if not isinstance(paths, list) or len(paths) != 1 or not prompt:
                raise ValueError("caption_batch: each request needs one path and a prompt")
            if max_new_tokens is not None and tokens != max_new_tokens:
                raise ValueError("caption_batch: max_new_tokens must match within a batch")
            path = paths[0]
            if not os.path.exists(path):
                raise FileNotFoundError(f"image not found: {path}")
            image = _resize_image(image_io.open_display(
                path,
                request.get("angle", 0),
                bool(request.get("mirror", False)),
            ))
            max_new_tokens = tokens
            prepared.append((index, {"image": image, "prompt": prompt}))
        except Exception as exc:  # one malformed image must not fail its neighbour
            replies[index] = {"ok": False, "error": str(exc)}

    if prepared:
        try:
            outcomes = _caption_independent_on_card(
                [sample for _, sample in prepared],
                max_new_tokens if max_new_tokens is not None else 256,
            )
            for (index, _sample), outcome in zip(prepared, outcomes):
                if isinstance(outcome, Exception):
                    replies[index] = {"ok": False, "error": str(outcome)}
                else:
                    replies[index] = {
                        "ok": True,
                        "result": {"captions": [outcome], "model": _vlm_model_name()},
                    }
        except Exception as exc:
            for index, _sample in prepared:
                replies[index] = {"ok": False, "error": str(exc)}

    return [reply if reply is not None else {"ok": False, "error": "caption batch item was not processed"} for reply in replies]


def transcribe(file_path: str, language: str | None = None, min_silence_ms: int | None = None, word_timestamps: bool | None = None) -> dict:
    """Transcribe an audio file with faster-whisper, on the GPU when available.

    `speech` is False when nothing was recognised — a normal outcome for the many silent
    phone clips in the archive, and the caller decides what to say about it. `model` names
    the model that actually ran, so the description's provenance can't drift from
    CFG_WHISPER_MODEL.

    `segments` carry their own start and end, in seconds. They are the whole reason this
    returns a structure rather than a string: subtitles, a transcript that follows the
    playhead, and a search hit that knows *when* something was said all need the timings,
    and once joined they cannot be recovered. `text` is still the flat join, because most
    callers only want that.

    The decode arguments are not defaults. See model_registry for why each is set:
    VAD (whisper_vad_parameters) so silence is not decoded into invented speech, tuned so
    that faint and outdoor speech still reaches the decoder; no conditioning on previous text
    so a bad segment cannot drag the rest of the file into a repetition loop; an optional
    language (WHISPER_LANGUAGE) so a noisy first 30 seconds cannot mislabel an entire
    recording; and a ceiling on the decoder's own no-speech score (WHISPER_NO_SPEECH_PROB_MAX)
    that drops the fluent sentences it invents over a window it rated as silence.
    """
    import torch

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"audio not found: {file_path}")

    # The effective decode settings: the request's overrides where given, the configuration
    # otherwise — the same resolution transcribe_signature performs, so stamp and decode agree.
    vad = dict(registry.whisper_vad_parameters())
    if min_silence_ms is not None:
        vad['min_silence_duration_ms'] = int(min_silence_ms)
    lang = registry.WHISPER_LANGUAGE if language is None else language
    words = registry.WHISPER_WORD_TIMESTAMPS if word_timestamps is None else bool(word_timestamps)

    def run():
        use_cuda = torch.cuda.is_available()
        device = "cuda" if use_cuda else "cpu"
        compute_type = "float16" if use_cuda else "int8"
        try:
            model = registry.whisper(device, compute_type)
        except Exception:
            # CTranslate2 can fail to init CUDA (missing cuDNN/cuBLAS or no device).
            device, compute_type = "cpu", "int8"
            model = registry.whisper(device, compute_type)

        try:
            # segments is a generator — consume it before releasing the model.
            segments, info = model.transcribe(
                file_path,
                beam_size=5,
                language=lang or None,
                vad_filter=True,
                vad_parameters=vad,
                condition_on_previous_text=False,
                word_timestamps=words,
            )
            # Empty segments are dropped here rather than downstream: a cue with no text is
            # not a subtitle, and an empty chunk is not worth a vector. So are segments the
            # decoder rated as almost certainly not speech — those are its hallucinations,
            # and they read as sentences, which is exactly why they must not reach search.
            out = []
            for segment in segments:
                text = segment.text.strip()
                if not text:
                    continue
                no_speech = getattr(segment, "no_speech_prob", None)
                if no_speech is not None and no_speech >= registry.WHISPER_NO_SPEECH_PROB_MAX:
                    continue
                entry = {"start": float(segment.start), "end": float(segment.end), "text": text}
                # Only present when WHISPER_WORD_TIMESTAMPS asked for the alignment pass.
                # Carried through because a cue cannot be split at a speaker change without
                # knowing where inside it each word falls — see resolveSpeakers in
                # @media/transcript.
                words = getattr(segment, "words", None) or []
                aligned = [
                    {"start": float(w.start), "end": float(w.end), "word": w.word}
                    for w in words
                    if w.start is not None and w.end is not None and w.word.strip()
                ]
                if aligned:
                    entry["words"] = aligned
                out.append(entry)
            # `info` is only safe to read after the generator is drained — faster-whisper
            # fills in the detected language as it decodes the first window.
            return out, {
                "language": info.language,
                "language_probability": float(info.language_probability or 0.0),
                "duration": float(info.duration or 0.0),
            }
        finally:
            del model
            registry.release(f"whisper:{device}:{compute_type}")

    with registry.family_lock(WHISPER):
        segments, info = registry.run_on_model_thread(WHISPER, run)

    text = " ".join(segment["text"] for segment in segments)
    return {
        "text": text,
        "speech": bool(text),
        "model": _whisper_model_name(),
        "signature": transcribe_signature(lang, min_silence_ms, words),
        "segments": segments,
        **info,
    }


def _parse_turn(line: str) -> dict | None:
    """`"12.480 17.840 speaker_0"` -> `{"start": 12.48, "end": 17.84, "speaker": "speaker_0"}`.

    Sortformer's own output format. Anything that does not parse is dropped rather than
    raised on: one malformed line must not lose a whole recording's diarization.
    """
    parts = line.split()
    if len(parts) < 3:
        return None
    try:
        start, end = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if end <= start:
        return None
    return {"start": start, "end": end, "speaker": parts[2]}


def diarize(file_path: str) -> dict:
    """Find speaker turns in an audio file. Returns turns with timings and a speaker label.

    Takes an already-demuxed 16kHz mono WAV, like `transcribe` — the caller owns ffmpeg.

    **Windowed, and the caller must know it.** Sortformer is not linear in input length, so
    this feeds it registry.DIARIZE_WINDOW_SECONDS at a time rather than the whole file (see
    the note there for the measured OOM that forces it). Two consequences leak into the
    result and are reported rather than hidden:

      - `speaker` is LOCAL TO ITS WINDOW. The label is prefixed with the window index
        (`w0/speaker_0`) precisely so it cannot be mistaken for a file-wide identity: the
        same person in two windows gets two labels. Stitching them is the caller's job, and
        it is done by embedding each turn and clustering — the same similarity vote the
        cross-file identity step already runs.
      - A turn that spans a window boundary comes back as two turns meeting at that
        boundary. Once stitching has agreed they are one speaker, adjacent turns that touch
        can be merged.

    `speech` is False when no turn was found anywhere, a normal outcome for the many silent
    clips in the archive — the same contract `transcribe` uses.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"audio not found: {file_path}")

    window = registry.DIARIZE_WINDOW_SECONDS

    def run():
        import soundfile
        import tempfile

        device = registry.select_device()
        model = registry.sortformer(device)
        try:
            samples, rate = soundfile.read(file_path, dtype="float32", always_2d=False)
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            total = len(samples) / float(rate)

            turns: list[dict] = []
            with tempfile.TemporaryDirectory() as tmp:
                chunk_path = os.path.join(tmp, "window.wav")
                index = 0
                offset = 0.0
                while offset < total:
                    span = min(window, total - offset)
                    # Below this a window holds no usable speech and diarizing it only costs
                    # a model call, so stop rather than emit a degenerate tail.
                    if span < 2.0:
                        break
                    piece = samples[int(offset * rate):int((offset + span) * rate)]
                    soundfile.write(chunk_path, piece, rate)
                    for line in model.diarize(audio=[chunk_path], batch_size=1)[0]:
                        turn = _parse_turn(line)
                        if turn is None:
                            continue
                        turns.append({
                            "start": round(turn["start"] + offset, 3),
                            "end": round(min(turn["end"] + offset, total), 3),
                            "localSpeaker": f"w{index}/{turn['speaker']}",
                        })
                    index += 1
                    offset += span

            # Sortformer groups its turns by speaker, not by time, so the caller would
            # otherwise have to know that. Hand back one timeline.
            turns.sort(key=lambda t: (t["start"], t["end"]))
            return turns, total
        finally:
            del model
            registry.release(f"sortformer:{device}")

    with registry.family_lock(SORTFORMER):
        turns, duration = registry.run_on_model_thread(SORTFORMER, run)

    return {
        "turns": turns,
        "speech": bool(turns),
        "duration": duration,
        "window": window,
        "model": registry.SORTFORMER_MODEL.split("/")[-1],
    }


def active_speaker_score(video_path: str, audio_path: str, start: float, end: float, track: list) -> dict | None:
    """How well one visible face's lip movement matches the sound over a span.

    `track` is that face through time: `[{"t": seconds, "box": {x, y, width, height}}]` with
    the box in fractions of the frame, the same convention every stored faceBox uses. The
    caller owns finding the face and following it; this only scores the pairing.

    Returns `{"score": 0..1, "frames": n}`, where the score is the mean probability that the
    face is speaking. None when there is too little to judge on — a track shorter than a
    handful of frames, or a span with no audio behind it.

    The two streams have to arrive at the rates the model was trained on: video at 25fps and
    MFCC at 100fps, four audio frames per video frame. Both are resampled to that here rather
    than being demanded of the caller, because the frame times come from wherever the caller
    chose to sample and will not naturally land on 25fps.
    """
    try:
        import cv2
        import numpy
        import python_speech_features
        import soundfile
    except ImportError as e:
        raise ImportError(f"Required package not installed: {e}")

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"audio not found: {audio_path}")
    if end <= start or len(track) < 4:
        return None

    # --- visual: the face, cropped the way the model was trained to see it -----------------
    capture = cv2.VideoCapture(video_path)
    try:
        crops = []
        for entry in track:
            at = float(entry.get("t", 0.0))
            box = entry.get("box") or {}
            capture.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            # Fractions to pixels, padded: the model sees a face with some room around it,
            # and a tight crop loses the jaw, which is most of what lip motion is.
            pad = 0.25
            x0 = int(max(0, (float(box.get("x", 0)) - float(box.get("width", 0)) * pad) * width))
            y0 = int(max(0, (float(box.get("y", 0)) - float(box.get("height", 0)) * pad) * height))
            x1 = int(min(width, (float(box.get("x", 0)) + float(box.get("width", 0)) * (1 + pad)) * width))
            y1 = int(min(height, (float(box.get("y", 0)) + float(box.get("height", 0)) * (1 + pad)) * height))
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            face = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
            crops.append(cv2.resize(face, (112, 112)))
    finally:
        capture.release()

    if len(crops) < 4:
        return None

    # 25fps is the rate the visual frontend expects; the caller's sampling is resampled onto
    # it by nearest neighbour rather than interpolated, since these are images.
    frames = max(4, int(round((end - start) * 25)))
    index = numpy.linspace(0, len(crops) - 1, frames).round().astype(int)
    visual = numpy.stack([crops[i] for i in index]).astype(numpy.float32)

    # --- audio: MFCC at four frames per video frame ---------------------------------------
    samples, rate = soundfile.read(audio_path, dtype="float32", always_2d=False)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    piece = samples[int(start * rate):int(end * rate)]
    if len(piece) < rate // 10:
        return None
    mfcc = python_speech_features.mfcc(piece, rate, numcep=13, winlen=0.025, winstep=0.010)

    wanted = frames * 4
    if len(mfcc) < wanted:
        mfcc = numpy.pad(mfcc, ((0, wanted - len(mfcc)), (0, 0)), mode="edge")
    audio = mfcc[:wanted].astype(numpy.float32)

    def run():
        import torch

        device = registry.lr_asd_device()
        model, head = registry.lr_asd(device)
        try:
            with torch.no_grad():
                visual_t = torch.from_numpy(visual).unsqueeze(0).to(device)
                audio_t = torch.from_numpy(audio).unsqueeze(0).to(device)
                outs_av, _outs_v = model(audio_t, visual_t)
                probabilities = torch.softmax(head(outs_av), dim=-1)[:, 1]
                return float(probabilities.mean().item())
        finally:
            del model, head
            registry.release(f"asd:{device}")

    with registry.family_lock(ASD):
        score = registry.run_on_model_thread(ASD, run)

    return {"score": score, "frames": frames}


def embed_voice_segment(file_path: str, start: float, end: float) -> dict | None:
    """Embed one speech turn as a voiceprint. Returns {"embedding": [...], "dim": n} or None.

    The voice analog of `embed_face`: given a span the diarizer already located, produce the
    vector that decides whose voice it is. `dim` is reported rather than assumed so the
    embedding node's `capacity` is written from what the model actually returned and cannot
    drift from the pgvector column if the model is ever changed.

    None when the span is too short to carry a usable voiceprint — the caller's duration gate
    should normally catch that first, but a turn clipped by a window boundary can be shorter
    than it looks.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"audio not found: {file_path}")
    if end <= start:
        return None

    def run():
        import soundfile
        import tempfile

        device = registry.titanet_device()
        model = registry.titanet(device)
        try:
            samples, rate = soundfile.read(file_path, dtype="float32", always_2d=False)
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            piece = samples[int(start * rate):int(end * rate)]
            # A quarter second is below what any speaker model can characterise.
            if len(piece) < rate // 4:
                return None
            with tempfile.TemporaryDirectory() as tmp:
                turn_path = os.path.join(tmp, "turn.wav")
                soundfile.write(turn_path, piece, rate)
                vector = model.get_embedding(turn_path).reshape(-1).tolist()
            return [float(value) for value in vector]
        finally:
            del model
            registry.release(f"titanet:{device}")

    with registry.family_lock(TITANET):
        embedding = registry.run_on_model_thread(TITANET, run)

    if embedding is None:
        return None
    return {
        "embedding": embedding,
        "dim": len(embedding),
        "model": registry.TITANET_MODEL.split("/")[-1],
    }


def _first_json_object(text: str) -> str:
    """The first balanced {...} in `text`, fences and prose stripped.

    A small instruct model prefers to be helpful — a ```json fence, a sentence of preamble,
    a trailing "Let me know if..." — and none of that is worth another round trip to correct.
    Braces inside string literals do not count, so a value like "Hall {of} fame" cannot end
    the object early. Returns "" when there is no balanced object, which the caller treats as
    an unusable answer.
    """
    start = text.find("{")
    if start < 0:
        return ""

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def _generate_on_vlm(
    messages: list,
    max_new_tokens: int,
    expires_at: float | None = None,
    interactive: bool = False,
    require_gpu: bool = False,
) -> dict:
    """Run a text-only chat through the captioning VLM.

    Qwen3-VL is a language model that also takes images, so a message list with no image content
    is an ordinary chat for it. Same greedy decode and same prompt-slicing as `_caption_images`,
    because it is the same model doing the same job with one modality fewer.

    Why a caller would want this rather than the instruct family: the separate CPU model costs
    ~6.2GB of host RAM at fp32. Batch callers already wait behind GPU work, while the interactive
    query parser asks only for two tiny lists and enters the queue ahead of waiting captions.
    On a 16GB box, not retaining the separate model is the difference between fitting under the
    RSS ceiling and not.

    `vlm_device()` raises CardBusy when the card is full, exactly as it does for a caption, and
    that is left to propagate: the caller defers rather than degrading.
    """
    def run():
        import torch

        device = registry.vlm_device()
        if require_gpu and device != "cuda":
            raise RuntimeError("generate: GPU unavailable")
        processor, model = registry.vlm(device)
        try:
            inputs = processor.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
                return_tensors="pt",
            ).to(model.device)

            with torch.inference_mode():
                out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

            generated = out[:, inputs["input_ids"].shape[1]:]
            return processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
        finally:
            del model, processor
            registry.release(f"vlm:{device}")

    with registry.family_lock(VLM).hold(priority=1 if interactive else 0):
        # The browser may have stopped waiting while this request sat behind a caption. Do not
        # generate a response nobody can receive or let stale search clicks delay the crawl.
        if expires_at is not None and time.time() >= float(expires_at):
            raise TimeoutError("generate: request expired while waiting for the VLM")
        completion = registry.run_on_model_thread(VLM, run)

    return {"text": completion, "model": registry.VLM_MODEL}


def generate(
    messages: list,
    max_new_tokens: int = 192,
    json_only: bool = False,
    model: str = "instruct",
    expires_at: float | None = None,
    interactive: bool = False,
    require_gpu: bool = False,
) -> dict:
    """Run a chat message list through a language model.

    `model` selects the family: "instruct" (default) is the optional small CPU model; "vlm"
    is the captioner, which batch callers and tightly bounded interactive extraction can share —
    see `_generate_on_vlm`.

    Greedy (`do_sample=False`) on purpose: this is extraction into a fixed schema, where
    sampling buys nothing and costs reproducibility — a prompt regression has to be
    observable, and a test cannot assert on output that changes per call.

    `json_only` returns just the first balanced JSON object from the completion, so callers
    do not each reimplement fence-stripping. The result is still untrusted text: it is not
    parsed or validated here.
    """
    import torch

    if not messages:
        raise ValueError("generate: messages must not be empty")

    if model == "vlm":
        result = _generate_on_vlm(messages, max_new_tokens, expires_at, interactive, require_gpu)
        if json_only:
            result["text"] = _first_json_object(result["text"])
        return result
    if model != "instruct":
        raise ValueError(f"generate: unknown model {model!r}")

    def run():
        device = registry.instruct_device()
        key = f"{INSTRUCT}:{device}"
        tokenizer, model = registry.instruct(device)
        try:
            prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = tokenizer(prompt, return_tensors="pt").to(device)
            prompt_length = inputs["input_ids"].shape[-1]
            with torch.inference_mode():
                output = model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id,
                )
            return tokenizer.decode(output[0][prompt_length:], skip_special_tokens=True).strip()
        finally:
            del tokenizer, model
            registry.release(key)

    with registry.family_lock(INSTRUCT):
        completion = registry.run_on_model_thread(INSTRUCT, run)

    if json_only:
        completion = _first_json_object(completion)

    return {"text": completion, "model": registry.INSTRUCT_MODEL}


def embed_text(text: str) -> list[float]:
    """Embed text using sentence-transformers (384-dim)."""
    def run():
        device = registry.select_device()
        model = registry.sentence_transformer(device)
        try:
            embedding = model.encode(text, normalize_embeddings=True)
            return embedding.tolist()
        finally:
            del model
            registry.release(f"minilm:{device}")

    with registry.family_lock(MINILM):
        return registry.run_on_model_thread(MINILM, run)


def detect_faces(image_path: str, angle=0, mirror=False) -> dict:
    """Detect faces in an image using InsightFace. Returns boxes + 512-dim embeddings.

    Detection runs on the *display* frame (see `_read_upright`), so `frame` is reported
    alongside the pixel boxes: the caller normalizes against it and stores fractions.
    """
    try:
        import cv2
    except ImportError as e:
        raise ImportError(
            f"Required package not installed: {e}\n"
            "Install with: pip install insightface onnxruntime-gpu opencv-python-headless"
        )

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    img = _read_upright(cv2, image_path, angle, mirror)
    frame_h, frame_w = img.shape[:2]

    def run():
        """Load and run InsightFace, pinned to one thread — see registry.run_on_model_thread."""
        app = registry.insightface()
        family_key = INSIGHTFACE
        try:
            try:
                faces = app.get(img)
            except Exception as exc:
                if not _is_cuda_oom(exc):
                    raise
                # A small card cannot hold the VLM and InsightFace at once.
                # Drop both CUDA sessions and retry on CPU: detection is ~2s of
                # work, so falling back costs throughput but keeps the pipeline
                # correct — failing the step instead loses the file entirely.
                registry.evict("vlm:cuda")
                registry.evict_insightface()
                app = registry.insightface(force_cpu=True)
                family_key = f"{INSIGHTFACE}:cpu"
                faces = app.get(img)

            results = []
            for i, face in enumerate(faces):
                bbox = face.bbox.astype(int).tolist()
                result = {
                    "index": i,
                    "box": {
                        "x": bbox[0],
                        "y": bbox[1],
                        "width": bbox[2] - bbox[0],
                        "height": bbox[3] - bbox[1],
                    },
                    "confidence": float(face.det_score),
                    "embedding": face.embedding.tolist(),  # 512-dim vector
                }
                # Add landmarks if available (5 facial keypoints)
                if face.kps is not None:
                    result["landmarks"] = face.kps.tolist()
                results.append(result)
            return results
        finally:
            del app
            registry.release(family_key)

    with registry.family_lock(INSIGHTFACE):
        results = registry.run_on_model_thread(INSIGHTFACE, run)

    return {"frame": {"width": frame_w, "height": frame_h}, "faces": results}


def embed_face(image_path: str, box: dict, angle=0, mirror=False) -> dict | None:
    """Embed a single already-located face using InsightFace.

    `box` is fractional (0..1) and in the display frame, like every stored faceBox — the
    caller never has to know the image's dimensions. Crops the padded bounding box and runs
    detection on the crop so the returned 512-dim vector is properly aligned (InsightFace
    aligns from its own landmarks). Used to back-fill embeddings for box-only manual tags so
    face recognition has anchors. Returns {"embedding": [...], "confidence": float} or None
    when no face is found inside the box (e.g. a mistaken tag or a face too small to detect).
    """
    try:
        import cv2
    except ImportError as e:
        raise ImportError(
            f"Required package not installed: {e}\n"
            "Install with: pip install insightface onnxruntime-gpu opencv-python-headless"
        )

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    img = _read_upright(cv2, image_path, angle, mirror)

    h, w = img.shape[:2]
    x, y = int(round(float(box["x"]) * w)), int(round(float(box["y"]) * h))
    bw, bh = int(round(float(box["width"]) * w)), int(round(float(box["height"]) * h))
    px, py = max(1, int(bw * 0.3)), max(1, int(bh * 0.3))
    x0, y0 = max(0, x - px), max(0, y - py)
    x1, y1 = min(w, x + bw + px), min(h, y + bh + py)
    if x1 <= x0 or y1 <= y0:
        return None
    crop = img[y0:y1, x0:x1]

    def run():
        """Load and run InsightFace, pinned to one thread — see registry.run_on_model_thread."""
        app = None
        family_key = INSIGHTFACE
        try:
            try:
                # Load + run inside the guard: under the 4GB card contending with a
                # concurrent VLM work, even the InsightFace session *init* can OOM,
                # so fall the whole thing back to CPU rather than failing the tag.
                app = registry.insightface()
                faces = app.get(crop)
            except Exception as exc:
                if not _is_cuda_oom(exc):
                    raise
                registry.evict("vlm:cuda")
                registry.evict_insightface()
                registry.cleanup_torch()
                app = registry.insightface(force_cpu=True)
                family_key = f"{INSIGHTFACE}:cpu"
                faces = app.get(crop)

            if not faces:
                return None
            # The tagged face is the dominant one in the crop.
            face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            return {"embedding": face.embedding.tolist(), "confidence": float(face.det_score)}
        finally:
            if app is not None:
                del app
            registry.release(family_key)

    with registry.family_lock(INSIGHTFACE):
        return registry.run_on_model_thread(INSIGHTFACE, run)

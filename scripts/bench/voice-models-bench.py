#!/usr/bin/env python3
"""voice-models-bench — Phase 1 of work/plans/voice-fingerprint.md.

Measure, on real hardware, what the three new models cost so the pipeline YAML and the GPU
worker kinds can be given honest `vramMb` budgets instead of guesses — the same
"measure before budgeting" discipline that sized INSIGHTFACE_GPU_MEM_LIMIT_BYTES and the
timed-transcripts backfill.

Models under test:
  - Sortformer  (nvidia/diar_sortformer_4spk-v1)              speaker-turn segmentation
  - TitaNet-L   (nvidia/speakerverification_en_titanet_large) 192-dim voiceprint embedding
  - LR-ASD      (/opt/lr-asd, MIT)                             active-speaker (lip-sync) score

For each: cold load time, per-item inference time, and peak *device* VRAM (via
torch.cuda.mem_get_info, so NeMo/onnxruntime allocations torch's own accounting misses are
still counted). TitaNet and LR-ASD are also timed on CPU, because if a segment embeds in
well under a second on CPU they belong on the CPU pool and need no GPU kind at all.

Nothing here is wired into the product. It exists to produce a few numbers per model
(load_s, item_s, vram_mb, realtime factor) and a suggested budget, then be thrown away.

Run it through the Makefile, which mounts the store and passes the GPU:

    make remote-voice-bench HOST=fry MEDIA_ROOT=/home/mattias/m3 \\
        VOICE_BENCH_ARGS="--limit 100 --min-duration 30"

MEDIA_ROOT matters. Point it at the **legacy originals** (`OLD_PATH`, /home/mattias/m3), not
at either deployment's managed file store: those hold 260k/130k files that are almost all
stills, with 6 and 0 audio-bearing containers respectively, and they are content-addressed so
nothing carries an extension. The speech-bearing home video only exists in the legacy tree.
"""

import argparse
import contextlib
import glob
import os
import random
import subprocess
import sys
import tempfile
import time
import traceback

RESULTS: list[dict] = []
FAILURES: list[str] = []


@contextlib.contextmanager
def stage(name: str):
    """Run a block, turning any exception into a recorded failure without aborting the rest.

    Recorded, not just printed: a stage that dies still has to reach the exit code, or a run
    where diarization OOMed reports success and the summary table quietly lacks a row.
    """
    print(f"\n=== {name} ===", flush=True)
    started = time.time()
    try:
        yield
        print(f"--- {name}: ok in {time.time() - started:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001 - one model failing must not stop the others
        FAILURES.append(f"{name}: {exc}")
        print(f"FAIL {name}: {exc}", flush=True)
        traceback.print_exc(limit=4)


def cuda_free_mb() -> float | None:
    """Device-level free VRAM in MiB, or None with no usable GPU."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return free / 1048576
    except Exception:
        return None


class VramProbe:
    """Peak *device* VRAM used between enter and exit, in MiB.

    Device-level rather than torch-level on purpose: NeMo pulls in its own CUDA allocations
    and (through asr) onnxruntime, none of which torch.cuda.memory_allocated can see. This
    samples mem_get_info at enter, and the caller re-checks .peak() after the work.
    """

    def __init__(self):
        self.baseline = None
        self.low_water = None

    def __enter__(self):
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        self.baseline = cuda_free_mb()
        self.low_water = self.baseline
        return self

    def sample(self):
        free = cuda_free_mb()
        if free is not None and (self.low_water is None or free < self.low_water):
            self.low_water = free

    def __exit__(self, *_exc):
        self.sample()
        return False

    def peak_mb(self) -> float | None:
        if self.baseline is None or self.low_water is None:
            return None
        return max(0.0, self.baseline - self.low_water)


def demux_wav(src: str, dst: str, start: float | None = None, dur: float | None = None) -> None:
    """16 kHz mono PCM WAV, the shape every speech model here wants. Mirrors transcribe-file."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-ac", "1", "-ar", "16000", "-vn", "-f", "wav", dst]
    subprocess.run(cmd, check=True, capture_output=True)


def sniff_container(path: str) -> bool:
    """True if the first bytes look like a container that can carry audio.

    The file store is content-addressed — names carry no extension — so discovery has to go
    by content. Magic bytes first because they are one cheap read; an ffprobe per candidate
    over a store that is mostly stills would cost minutes to find the handful of a/v files.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    if len(head) < 12:
        return False
    return (
        head[4:8] == b"ftyp"                       # MP4 / MOV / M4A
        or head[:4] == b"\x1a\x45\xdf\xa3"         # Matroska / WebM
        or (head[:4] == b"RIFF" and head[8:12] == b"AVI ")
        or (head[:4] == b"RIFF" and head[8:12] == b"WAVE")
        or head[:4] == b"OggS"                     # Ogg / Opus
        or head[:3] == b"ID3"                      # MP3 with a tag
        or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")  # bare MP3 frame
        or head[:4] == b"fLaC"
    )


def audio_duration(path: str) -> float | None:
    """Duration of the file's first audio stream in seconds, or None if it has none."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type:format=duration",
             "-of", "default=nw=1:nk=1", path],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if "audio" not in out.stdout:
        return None
    for line in out.stdout.split():
        with contextlib.suppress(ValueError):
            return float(line)
    return None


def collect_media(media_dir: str, limit: int, min_duration: float, seed: int) -> list[tuple[str, float]]:
    """Up to `limit` (path, duration) pairs for files with at least `min_duration` of audio.

    Two things this has to get right, both learned the hard way on the real store:

      - Names are content-addressed, so discovery goes by magic bytes, not extensions.
      - Taking the first N in walk order gets a run of near-identical tiny clips from
        whatever import batch happens to sort first, and the archive is largely short,
        silent phone clips — Sortformer correctly finds no turns in those, which measures
        nothing. So candidates are sampled across the whole store, and anything too short
        to plausibly hold a conversation is skipped.
    """
    candidates: list[str] = []
    scanned = 0
    for root, _dirs, files in os.walk(media_dir):
        for name in files:
            path = os.path.join(root, name)
            scanned += 1
            if sniff_container(path):
                candidates.append(path)

    random.Random(seed).shuffle(candidates)
    kept: list[tuple[str, float]] = []
    probed = 0
    for path in candidates:
        probed += 1
        duration = audio_duration(path)
        if duration is not None and duration >= min_duration:
            kept.append((path, duration))
            if len(kept) >= limit:
                break

    total = sum(d for _p, d in kept)
    print(
        f"scanned {scanned} file(s), {len(candidates)} container(s), probed {probed}, "
        f"kept {len(kept)} with >= {min_duration:.0f}s audio ({total / 60:.1f} min total)"
    )
    return kept


def record(model: str, device: str, load_s: float, item_s: float, vram_mb: float | None,
           rtf: float | None = None, note: str = "") -> None:
    """One measured row. `rtf` is audio-seconds processed per wall-second, where that is the
    meaningful figure (diarization cost scales with how long the recording is, not with how
    many files there are)."""
    per_hour = 3600.0 / item_s if item_s > 0 else 0.0
    RESULTS.append(
        {
            "model": model,
            "device": device,
            "load_s": load_s,
            "item_s": item_s,
            "vram_mb": vram_mb,
            "items_per_hour": per_hour,
            "rtf": rtf,
            "note": note,
        }
    )
    vram_txt = f"{vram_mb:.0f}MB" if vram_mb is not None else "n/a"
    rtf_txt = f" | {rtf:.0f}x realtime" if rtf else ""
    print(
        f"  [{model} @ {device}] load {load_s:.1f}s | item {item_s:.3f}s "
        f"| vram {vram_txt}{rtf_txt} | ~{per_hour:.0f}/h {note}",
        flush=True,
    )


# --- Sortformer ---------------------------------------------------------------------------

def bench_sortformer(media: list[tuple[str, float]], window: float) -> None:
    """Diarize in fixed windows rather than whole files.

    Sortformer is not linear in input length: its own training config declares
    `session_len_sec: 90`, and handed a full recording it tries to allocate proportionally to
    the square of the sequence — MEASURED on fry, one ~12-minute file asked for 18.00 GiB on a
    15.52 GiB card and raised torch.OutOfMemoryError. So the real pipeline has to window long
    audio, and the bench measures what the pipeline will actually do.

    Turn indices are LOCAL TO EACH CALL: `spk0` in one window is not `spk0` in the next, so a
    real implementation stitches windows by embedding each turn with TitaNet and clustering
    within the file. That is the same operation the cross-file identity step already performs,
    so it folds in rather than adding a mechanism.
    """
    from nemo.collections.asr.models import SortformerEncLabelModel  # NeMo 3.0: confirm name
    import torch

    with VramProbe() as probe:
        t0 = time.time()
        model = SortformerEncLabelModel.from_pretrained("nvidia/diar_sortformer_4spk-v1")
        model.eval()
        if torch.cuda.is_available():
            model = model.to("cuda")
        load_s = time.time() - t0
        probe.sample()

        durations: list[float] = []
        audio_seconds = 0.0
        turn_counts: list[int] = []
        windows_done = 0
        oom_files = 0
        with tempfile.TemporaryDirectory() as tmp:
            for i, (src, seconds) in enumerate(media):
                file_turns = 0
                failed = False
                for start in [w * window for w in range(int(seconds // window) + 1)]:
                    span = min(window, seconds - start)
                    if span < 2.0:
                        continue
                    wav = os.path.join(tmp, f"s{i}.wav")
                    try:
                        demux_wav(src, wav, start=start, dur=span)
                    except subprocess.CalledProcessError:
                        continue
                    t1 = time.time()
                    try:
                        out = model.diarize(audio=[wav], batch_size=1)
                    except torch.OutOfMemoryError:
                        torch.cuda.empty_cache()
                        failed = True
                        break
                    durations.append(time.time() - t1)
                    audio_seconds += span
                    windows_done += 1
                    probe.sample()
                    if i == 0 and start == 0:
                        print(f"    first file: {os.path.basename(src)} ({seconds:.1f}s audio, "
                              f"{window:.0f}s windows)")
                        print(f"    diarize() -> {type(out).__name__}, "
                              f"len={len(out) if hasattr(out, '__len__') else '?'}")
                        print(f"    repr (truncated): {repr(out)[:400]}")
                    with contextlib.suppress(Exception):
                        file_turns += len(out[0])
                if failed:
                    oom_files += 1
                else:
                    turn_counts.append(file_turns)

    if not durations:
        raise RuntimeError("no windows diarized")
    total_compute = sum(durations)
    # Per-file cost is what the pipeline pays, so report that rather than per-window.
    item_s = total_compute / max(1, len(turn_counts))
    # How often it found anything is the correctness signal: a model returning no turns for
    # every file would look fast and be useless. Silent clips legitimately yield zero.
    with_speech = sum(1 for c in turn_counts if c > 0)
    avg_turns = (sum(turn_counts) / len(turn_counts)) if turn_counts else float("nan")
    record("sortformer", "cuda" if torch.cuda.is_available() else "cpu", load_s, item_s,
           probe.peak_mb(), rtf=(audio_seconds / total_compute if total_compute else None),
           note=f"(n={len(turn_counts)} files / {windows_done} windows, {with_speech} with "
                f"turns, avg {avg_turns:.1f} turns/file, {oom_files} OOM)")
    del model


# --- TitaNet -----------------------------------------------------------------------------

def bench_titanet(media: list[tuple[str, float]], device: str) -> None:
    from nemo.collections.asr.models import EncDecSpeakerLabelModel  # NeMo 3.0: confirm name
    import torch

    probe_cm = VramProbe() if device == "cuda" else contextlib.nullcontext()
    probe = None
    with probe_cm as maybe_probe:
        probe = maybe_probe
        t0 = time.time()
        model = EncDecSpeakerLabelModel.from_pretrained("nvidia/speakerverification_en_titanet_large")
        model.eval()
        if device == "cuda" and torch.cuda.is_available():
            model = model.to("cuda")
        load_s = time.time() - t0
        if probe:
            probe.sample()

        dims: set[int] = set()
        durations: list[float] = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, (src, _seconds) in enumerate(media):
                # One embedding per ~4s slice — the grain a diarization turn will be.
                slice_wav = os.path.join(tmp, f"t{i}.wav")
                try:
                    demux_wav(src, slice_wav, start=0.0, dur=4.0)
                except subprocess.CalledProcessError:
                    continue
                if os.path.getsize(slice_wav) < 8000:  # < ~0.25s of audio
                    continue
                t1 = time.time()
                emb = model.get_embedding(slice_wav)
                durations.append(time.time() - t1)
                if probe:
                    probe.sample()
                with contextlib.suppress(Exception):
                    dims.add(int(emb.reshape(-1).shape[0]))

    if not durations:
        raise RuntimeError("no slices embedded")
    item_s = sum(durations) / len(durations)
    vram = probe.peak_mb() if probe else None
    record("titanet", device, load_s, item_s, vram,
           note=f"(n={len(durations)}, dim={sorted(dims) or '?'}, per 4s turn)")
    del model


# --- LR-ASD ----------------------------------------------------------------------------

def bench_lrasd(device: str) -> None:
    """Forward-cost only: synthetic face-track + MFCC tensors of the shapes model/Model.py
    expects. Real inputs (InsightFace face crops + python_speech_features MFCC) are Phase 5;
    this just sizes the forward pass and its VRAM so the kind can be budgeted / placed on the
    right pool.
    """
    lr_home = os.environ.get("LR_ASD_HOME", "/opt/lr-asd")
    sys.path.insert(0, lr_home)
    from model.Model import ASD_Model  # noqa: E402
    import torch  # noqa: E402

    probe_cm = VramProbe() if device == "cuda" else contextlib.nullcontext()
    with probe_cm as probe:
        t0 = time.time()
        model = ASD_Model()
        weight_path = os.path.join(lr_home, "weight", "finetuning_TalkSet.model")
        state = torch.load(weight_path, map_location="cpu")
        # Saved from the ASD training wrapper, so keys are prefixed `model.` and also carry
        # loss-head params. Keep only the backbone, drop the prefix.
        backbone = {
            k[len("model."):]: v for k, v in state.items() if k.startswith("model.")
        } or state
        model.load_state_dict(backbone, strict=False)
        model.eval().to(device)
        load_s = time.time() - t0
        if probe:
            probe.sample()

        params_m = sum(p.numel() for p in model.parameters()) / 1e6
        # A 3s track at 25fps: 75 grayscale 112x112 frames, MFCC at 4x that rate, 13 coeffs.
        durations: list[float] = []
        for _ in range(20):
            frames = 75
            visual = torch.rand(1, frames, 112, 112, device=device) * 255
            audio = torch.rand(1, frames * 4, 13, device=device)
            t1 = time.time()
            with torch.no_grad():
                model(audio, visual)
            if device == "cuda":
                torch.cuda.synchronize()
            durations.append(time.time() - t1)
            if probe:
                probe.sample()

    item_s = sum(durations[2:]) / len(durations[2:])  # drop warm-up
    vram = probe.peak_mb() if probe else None
    record("lr-asd", device, load_s, item_s, vram,
           note=f"({params_m:.1f}M params, 3s track)")
    del model


def verify_ops(media: list[tuple[str, float]]) -> None:
    """Exercise the real inference_ops primitives, not the models directly.

    The rest of this script measures the models; this checks the code that will actually call
    them — windowing, turn parsing, the local-speaker labelling and the embedding contract —
    against a real file. It imports from the mounted worktree, so it tests the working tree
    rather than whatever is baked at /app.
    """
    # The registry reads its config at import, so this has to be set first. Forced on rather
    # than left to the deployment's default because the whole point here is to check the
    # word-timing path resolveSpeakers depends on; a synthetic tone does not exercise it
    # (whisper finds no speech in one and returns no segments at all).
    os.environ["CFG_WHISPER_WORD_TIMESTAMPS"] = "1"
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mcs", "modelworker"))
    import inference_ops as ops  # noqa: E402

    src, seconds = media[0]
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "verify.wav")
        demux_wav(src, wav)
        print(f"    file: {os.path.basename(src)} ({seconds:.1f}s)")

        result = ops.diarize(wav)
        turns = result["turns"]
        print(f"    diarize: speech={result['speech']} turns={len(turns)} "
              f"window={result['window']}s model={result['model']}")
        if not turns:
            raise RuntimeError("diarize returned no turns for a file chosen for having speech")

        speakers = sorted({t["localSpeaker"] for t in turns})
        print(f"    speakers: {len(speakers)} distinct label(s), e.g. {speakers[:4]}")
        print(f"    first 3 turns: {turns[:3]}")

        # The op promises one timeline, ordered, despite the model grouping by speaker.
        starts = [t["start"] for t in turns]
        if starts != sorted(starts):
            raise RuntimeError("diarize returned turns out of time order")
        # And promises window-scoped labels, so every one carries its window index.
        if not all(t["localSpeaker"].startswith("w") and "/" in t["localSpeaker"] for t in turns):
            raise RuntimeError(f"localSpeaker is not window-scoped: {speakers[:4]}")
        # Timings must stay inside the recording once window offsets are applied.
        if turns[-1]["end"] > result["duration"] + 1.0:
            raise RuntimeError(
                f"turn ends at {turns[-1]['end']}s beyond the {result['duration']}s file — "
                "window offset is wrong"
            )

        longest = max(turns, key=lambda t: t["end"] - t["start"])
        embedding = ops.embed_voice_segment(wav, longest["start"], longest["end"])
        if embedding is None:
            raise RuntimeError("embed_voice_segment returned None for the longest turn")
        print(f"    embed_voice_segment: dim={embedding['dim']} model={embedding['model']} "
              f"over {longest['end'] - longest['start']:.1f}s")
        if embedding["dim"] != 192:
            raise RuntimeError(f"expected a 192-dim voiceprint, got {embedding['dim']}")

        # A zero-length span must be refused rather than embedded into noise.
        if ops.embed_voice_segment(wav, 5.0, 5.0) is not None:
            raise RuntimeError("embed_voice_segment accepted an empty span")

        # The other half of the speaker join: without per-word timings there is nowhere to
        # cut a cue that straddles a change of speaker. Checked against real speech, since a
        # tone yields no segments and would pass this vacuously.
        transcript = ops.transcribe(wav)
        segments = transcript["segments"]
        print(f"    transcribe: speech={transcript['speech']} segments={len(segments)}")
        if not segments:
            raise RuntimeError("transcribe found no speech in a file chosen for having some")
        worded = [s for s in segments if s.get("words")]
        if not worded:
            raise RuntimeError("word timings requested but no segment carried any")
        sample = worded[0]["words"][0]
        print(f"    words: {len(worded)}/{len(segments)} segments timed, "
              f"e.g. {sample['word']!r} at {sample['start']:.2f}-{sample['end']:.2f}s")
        for segment in worded:
            for word in segment["words"]:
                if word["end"] < word["start"]:
                    raise RuntimeError(f"word ends before it starts: {word}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media-dir", required=True, help="directory of real audio/video to sample")
    parser.add_argument("--limit", type=int, default=100, help="max files to test (default 100)")
    parser.add_argument("--skip-cpu", action="store_true", help="skip the CPU timings for TitaNet / LR-ASD")
    parser.add_argument("--min-duration", type=float, default=30.0,
                        help="skip files with less audio than this, in seconds (default 30); the "
                             "archive is mostly short silent clips, which measure nothing")
    parser.add_argument("--seed", type=int, default=1, help="sampling seed, for a repeatable run")
    parser.add_argument("--ops", action="store_true",
                        help="verify the inference_ops primitives against a real file instead "
                             "of measuring the models")
    parser.add_argument("--diar-window", type=float, default=90.0,
                        help="seconds of audio per diarization call (default 90, matching "
                             "Sortformer's own session_len_sec; whole files OOM the card)")
    args = parser.parse_args()

    media = collect_media(args.media_dir, args.limit, args.min_duration, args.seed)
    if not media:
        print(f"no audio/video found under {args.media_dir}", file=sys.stderr)
        # The store is content-addressed, so a wrong mount looks identical to an empty one.
        # Show what is actually there rather than leaving the caller to guess which it was.
        sample = list(glob.glob(os.path.join(args.media_dir, "*")))[:10]
        print(f"top level holds {len(sample)} shown entr(ies): {sample}", file=sys.stderr)
        return 2
    print(f"sampling {len(media)} file(s) from {args.media_dir}")

    free0 = cuda_free_mb()
    if free0 is not None:
        print(f"GPU free at start: {free0:.0f}MB")
    else:
        print("no GPU visible — CUDA rows will be skipped")

    if args.ops:
        with stage("inference_ops.diarize / embed_voice_segment"):
            verify_ops(media)
        return 1 if FAILURES else 0

    with stage("Sortformer (diarization, cuda)"):
        bench_sortformer(media, args.diar_window)

    with stage("TitaNet (voiceprint, cuda)"):
        if cuda_free_mb() is not None:
            bench_titanet(media, "cuda")
    if not args.skip_cpu:
        with stage("TitaNet (voiceprint, cpu)"):
            bench_titanet(media, "cpu")

    with stage("LR-ASD (active speaker, cuda)"):
        if cuda_free_mb() is not None:
            bench_lrasd("cuda")
    if not args.skip_cpu:
        with stage("LR-ASD (active speaker, cpu)"):
            bench_lrasd("cpu")

    print("\n" + "=" * 90)
    print(f"{'model':<12} {'device':<6} {'load_s':>8} {'item_s':>9} {'vram_mb':>9} "
          f"{'rtf':>7} {'per_hour':>10}  note")
    for r in RESULTS:
        vram_txt = f"{r['vram_mb']:.0f}" if r["vram_mb"] is not None else "-"
        rtf_txt = f"{r['rtf']:.0f}x" if r.get("rtf") else "-"
        print(
            f"{r['model']:<12} {r['device']:<6} {r['load_s']:>8.1f} {r['item_s']:>9.3f} "
            f"{vram_txt:>9} {rtf_txt:>7} {r['items_per_hour']:>10.0f}  {r['note']}"
        )

    print("\nsuggested budgets (device usage + ~30% slack; a CPU-viable model needs no GPU kind):")
    for r in RESULTS:
        if r["device"] != "cuda" or r["vram_mb"] is None:
            continue
        cpu_row = next((x for x in RESULTS if x["model"] == r["model"] and x["device"] == "cpu"), None)
        cpu_hint = ""
        if cpu_row and cpu_row["item_s"] < 1.0:
            cpu_hint = f"  (CPU does {cpu_row['item_s']:.2f}s/item — consider the CPU pool instead)"
        print(f"  {r['model']:<12} vramMb: {r['vram_mb'] * 1.3:.0f}{cpu_hint}")

    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for failure in FAILURES:
            print("  -", failure)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

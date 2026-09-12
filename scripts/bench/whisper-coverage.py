#!/usr/bin/env python3
"""whisper-coverage — why a transcript misses speech that is plainly audible.

A 62-second clip of two people talking came back with nine cues, while the diarizer found
seventeen speech turns in the same audio. Something between the sound and the cues is
dropping speech, and there are three candidates that a transcript alone cannot tell apart:

  - the VAD in front of whisper deciding that speech is not speech (wind, water, a child's
    voice, anything far from the microphone);
  - the language being detected wrongly, so every window is decoded as the wrong language and
    the parts it cannot force into that language come back empty or invented;
  - whisper's own 30-second windowing losing anything, which is the thing people usually
    suspect first and which this exists to rule in or out.

So run the same audio several ways and print what each returns. Differences here are the
answer; agreement means the speech never reached the decoder at all.

    make remote-whisper-coverage HOST=fry2 \\
        WHISPER_COVERAGE_ARGS="--file /files/2R/01M1SJJZ13NMPMP7PH523JZH2R"

That answered the question (it is the VAD), which raised the next one: which VAD setting?
`--sweep` runs a set of candidate settings over many files at once and scores each setting
on the two numbers that matter — words recovered on clips with speech in them, and words
invented on clips without. A setting that recovers speech and also starts inventing it on
silence is not an improvement, and only the silent half of the sample can show that; so
`--silent` marks the files that the current pipeline (and a listener) found nothing in.

    make remote-whisper-coverage HOST=fry2 WHISPER_COVERAGE_ARGS="--sweep \\
        --file /files/2R/… --file /files/4H/… --silent /files/V6/… --silent /files/7M/…"

Word counts are the metric, never the share of the timeline the cues span: a setting that
deletes speech inside a region still gets a cue stretched across the whole region. `kept`
is the count after the pipeline's own no-speech guard (model_registry.WHISPER_NO_SPEECH_PROB_MAX),
i.e. what would actually be written; `--text` prints every decode with its suspect lines
marked, which is how a number is checked against what was said.
"""

import argparse
import os
import subprocess
import sys
import tempfile


def demux(src: str, dst: str) -> bool:
    """16kHz mono WAV of the file's audio; False when it has no audio stream at all.

    Plenty of the archive's clips carry no audio track, and ffmpeg fails to open an output
    with no streams in it. That is not an error for a coverage run — it is the one case
    where "no speech" is certain — so it is reported rather than raised.
    """
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
         "-ac", "1", "-ar", "16000", "-vn", "-f", "wav", dst],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True
    if "does not contain any stream" in result.stderr or "Invalid argument" in result.stderr:
        return False
    raise RuntimeError(f"ffmpeg failed on {src}: {result.stderr.strip()}")


def decode(model, wav: str, **kwargs):
    """One decode, the way the pipeline does it apart from what `kwargs` overrides."""
    segments, info = model.transcribe(wav, beam_size=5, condition_on_previous_text=False, **kwargs)
    return list(segments), info


def run(model, wav: str, label: str, **kwargs) -> None:
    """One decode, reported as coverage rather than as text."""
    segments, info = decode(model, wav, **kwargs)

    spoken = sum(s.end - s.start for s in segments)
    words = sum(len(s.text.split()) for s in segments)
    gaps = []
    previous = 0.0
    for s in segments:
        if s.start - previous > 2.0:
            gaps.append((round(previous, 1), round(s.start, 1)))
        previous = s.end

    print(f"\n--- {label}")
    print(f"    language={info.language} ({info.language_probability:.2f}) "
          f"segments={len(segments)} words={words} speech={spoken:.1f}s of {info.duration:.1f}s "
          f"({100 * spoken / max(info.duration, 0.01):.0f}% covered)")
    if gaps:
        print(f"    silent gaps over 2s: {gaps[:8]}")
    for s in segments[:6]:
        print(f"      {s.start:6.2f}-{s.end:6.2f}  {s.text.strip()[:88]}")
    if len(segments) > 6:
        print(f"      … {len(segments) - 6} more")


def diagnose(model, wav: str, language: str, vad: dict) -> None:
    """The four-way split that tells the VAD, the language and the windowing apart."""
    # What the pipeline does today.
    run(model, wav, "as the pipeline runs it (VAD on, language auto)", vad_filter=True, vad_parameters=vad)

    # Is the VAD throwing speech away?
    run(model, wav, "VAD off, language auto", vad_filter=False)

    # Is detection picking the wrong language and decoding into it?
    run(model, wav, f"VAD on, language pinned to {language}", vad_filter=True, language=language, vad_parameters=vad)

    # Both together — the ceiling this audio can give.
    run(model, wav, f"VAD off, language pinned to {language}", vad_filter=False, language=language)


# --- the sweep ---------------------------------------------------------------------------

# A segment whisper itself is unsure of. These are whisper's own signals, not ours:
# `no_speech_prob` is the decoder's belief that the window held no speech at all, and a high
# compression ratio is what a repetition loop looks like once written down. Both thresholds
# are the ones the reference implementation uses to decide to re-decode a window.
NO_SPEECH_PROB = 0.6
COMPRESSION_RATIO = 2.4


def suspect(segment, previous_text: str) -> str:
    """Why a segment is suspect, or an empty string when it is not."""
    text = segment.text.strip()
    if not text:
        return ""
    if segment.no_speech_prob is not None and segment.no_speech_prob > NO_SPEECH_PROB:
        return f"nospeech={segment.no_speech_prob:.2f}"
    if segment.compression_ratio is not None and segment.compression_ratio > COMPRESSION_RATIO:
        return f"compression={segment.compression_ratio:.1f}"
    # A line repeated verbatim is the start of a loop, whatever the decoder thought of it.
    return "repeat" if text == previous_text else ""


def measure(model, wav: str, no_speech_max: float, **kwargs) -> dict:
    """One decode, scored. `kept` is what the pipeline would write after its no-speech guard."""
    segments, info = decode(model, wav, **kwargs)
    words = 0
    suspect_words = 0
    kept = 0
    previous = ""
    lines = []
    for s in segments:
        n = len(s.text.split())
        words += n
        why = suspect(s, previous)
        if why:
            suspect_words += n
        if s.no_speech_prob is None or s.no_speech_prob < no_speech_max:
            kept += n
        previous = s.text.strip()
        # Suspect lines are marked in place, so the text can be read against the verdict.
        lines.append(f"[{why}] {previous}" if why else previous)
    return {
        "segments": len(segments),
        "words": words,
        "suspect": suspect_words,
        "kept": kept,
        "text": " ".join(lines),
        "duration": float(info.duration or 0.0),
    }


# What the pipeline decoded with until 2026-09-11, kept as the row everything is read against.
BEFORE = {"threshold": 0.5, "speech_pad_ms": 400, "min_silence_duration_ms": 500}


def sweep_settings(pipeline: dict) -> list:
    """Candidate VAD settings, each a (label, transcribe kwargs) pair.

    The pipeline's own setting comes first and no VAD at all last — the floor and the
    ceiling every other row is read against. In between, one knob at a time before any
    combination, so a gain can be attributed.
    """
    def vad(base: dict, **overrides):
        params = dict(base)
        params.update(overrides)
        return {"vad_filter": True, "vad_parameters": params}

    short = {"threshold": "thr", "speech_pad_ms": "pad", "min_silence_duration_ms": "silence"}
    describe = ", ".join(f"{short.get(k, k)} {v}" for k, v in pipeline.items())
    return [
        (f"pipeline ({describe})", vad(pipeline)),
        ("before 2026-09-11 (thr 0.5, pad 400, silence 500)", vad(BEFORE)),
        ("before + threshold 0.35", vad(BEFORE, threshold=0.35)),
        ("before + threshold 0.2", vad(BEFORE, threshold=0.2)),
        ("before + pad 800ms", vad(BEFORE, speech_pad_ms=800)),
        ("before + silence 2000ms", vad(BEFORE, min_silence_duration_ms=2000)),
        ("before + threshold 0.35 + pad 800ms", vad(BEFORE, threshold=0.35, speech_pad_ms=800)),
        ("before + threshold 0.35 + silence 2000ms", vad(BEFORE, threshold=0.35, min_silence_duration_ms=2000)),
        ("VAD off", {"vad_filter": False}),
    ]


def sweep(model, files: list, language: str, registry, only: list, show_text: bool) -> None:
    settings = sweep_settings(registry.whisper_vad_parameters())
    if only:
        settings = [(label, kwargs) for label, kwargs in settings if any(o in label for o in only)]
    no_speech_max = registry.WHISPER_NO_SPEECH_PROB_MAX
    common = {"language": language} if language else {}
    # totals[label] = [talking words, talking kept, silent words, silent kept]
    totals = {label: [0, 0, 0, 0] for label, _ in settings}
    width = max(len(label) for label, _ in settings)

    with tempfile.TemporaryDirectory() as tmp:
        for index, (path, silent) in enumerate(files):
            wav = os.path.join(tmp, f"{index}.wav")
            print(f"\n=== {'silent' if silent else 'talking'}  {path}", flush=True)
            if not demux(path, wav):
                print("    no audio stream; nothing any setting could decode", flush=True)
                continue
            print(f"    {'setting':<{width}}  segments  words  suspect  kept")
            for label, kwargs in settings:
                result = measure(model, wav, no_speech_max, **common, **kwargs)
                row = totals[label]
                offset = 2 if silent else 0
                row[offset] += result["words"]
                row[offset + 1] += result["kept"]
                print(f"    {label:<{width}}  {result['segments']:8d}  {result['words']:5d}  {result['suspect']:7d}  {result['kept']:4d}", flush=True)
                if show_text and result["text"]:
                    print(f"        {result['text'][:600]}")
            os.remove(wav)

    talking = sum(1 for _, silent in files if not silent)
    silent = len(files) - talking
    print(f"\n=== totals over {talking} talking and {silent} silent files; kept = after the no-speech guard at {no_speech_max}")
    print(f"    {'setting':<{width}}  talking words (kept)  silent words (kept)")
    for label, _ in settings:
        t, tk, s, sk = totals[label]
        print(f"    {label:<{width}}  {t:13d} ({tk:5d})  {s:12d} ({sk:5d})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action="append", default=[],
                        help="media file with speech in it (container path); repeatable")
    parser.add_argument("--silent", action="append", default=[],
                        help="media file the current pipeline found no speech in; repeatable, --sweep only")
    parser.add_argument("--language", default=None,
                        help="language to pin (diagnosis: the pinned runs, default sv; --sweep: every run, default auto-detect as the pipeline does)")
    parser.add_argument("--sweep", action="store_true",
                        help="score candidate VAD settings over every file instead of the four-way diagnosis")
    parser.add_argument("--text", action="store_true", help="--sweep: also print each decode's text")
    parser.add_argument("--setting", action="append", default=[],
                        help="--sweep: run only settings whose label contains this (repeatable); e.g. --setting pipeline --setting before --setting off")
    args = parser.parse_args()

    files = [(path, False) for path in args.file] + [(path, True) for path in args.silent]
    if not files:
        print("nothing to decode: pass --file (and, with --sweep, --silent)", file=sys.stderr)
        return 2
    for path, _ in files:
        if not os.path.exists(path):
            print(f"no such file: {path}", file=sys.stderr)
            return 2

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mcs", "modelworker"))
    import model_registry as registry  # noqa: E402

    model = registry.whisper("cuda", "float16")

    if args.sweep:
        sweep(model, files, args.language or "", registry, args.setting, args.text)
        return 0

    vad = registry.whisper_vad_parameters()
    with tempfile.TemporaryDirectory() as tmp:
        for path, _ in files:
            wav = os.path.join(tmp, "audio.wav")
            print(f"file: {path}")
            if not demux(path, wav):
                print("    no audio stream; nothing to decode")
                continue
            diagnose(model, wav, args.language or "sv", vad)

    return 0


if __name__ == "__main__":
    sys.exit(main())

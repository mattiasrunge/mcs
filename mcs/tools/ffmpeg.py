"""ffmpeg for the front process: audio extraction and frame grabs.

Transcoding proper arrives in a later phase; this is what the speech and fingerprint ops need
today. A GPU is used for decoding when one is present, and the CPU chain is the fallback for
the quarter of any old archive NVDEC cannot read.
"""

from __future__ import annotations

import glob
import os

from ..errors import McsError, TOOL_FAILED
from .run import require, run


def gpu_present() -> bool:
    return bool(glob.glob("/dev/nvidia[0-9]*"))


async def extract_audio(path: str, out_wav: str, *, timeout: float, sample_rate: int = 16000, channels: int = 1) -> bool:
    """A PCM WAV of the file's sound at `out_wav`. False when the file has no audio stream.

    ffmpeg exits 0 having written nothing for a container without sound, so the size is checked
    rather than the exit code alone.
    """
    done = await run(
        [require("ffmpeg"), "-y", "-v", "error", "-vn", "-i", path, "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", str(channels), out_wav],
        timeout=timeout,
    )
    if done.code != 0:
        text = done.stderr_text.lower()
        if "does not contain any stream" in text or "output file is empty" in text or "no audio" in text:
            return False
        raise McsError(TOOL_FAILED, f"ffmpeg audio extraction failed: {done.tail()}")
    try:
        return os.path.getsize(out_wav) > 44
    except FileNotFoundError:
        return False


async def keyframes(path: str, out_dir: str, *, count: int, timeout: float, pattern: str = "frame-%03d.jpg") -> list[str]:
    """`count` representative frames as JPEGs, in order. The thumbnail filter picks them."""
    target = os.path.join(out_dir, pattern)
    filt = ["-vf", "thumbnail=300,setpts=N/TB", "-frames:v", str(count), "-q:v", "2"]
    attempts: list[list[str]] = []
    ffmpeg = require("ffmpeg")
    if gpu_present():
        attempts.append([ffmpeg, "-y", "-v", "error", "-hwaccel", "cuda", "-i", path, *filt, target])
    attempts.append([ffmpeg, "-y", "-v", "error", "-i", path, *filt, target])
    last = ""
    for args in attempts:
        done = await run(args, timeout=timeout)
        frames = sorted(glob.glob(os.path.join(out_dir, "frame-*.jpg")))
        if done.code == 0 and frames:
            return frames
        last = done.tail() or "no frames produced"
    raise McsError(TOOL_FAILED, f"ffmpeg keyframe extraction failed: {last}")

"""Configuration, from the environment only.

There is deliberately no configuration API: `GET /v2/capabilities` and `GET /v2/health` are how
a caller learns what a given MCS runs, and changing any of this is a restart. Every knob is an
`MCS_*` variable; the model worker's own `CFG_*` variables are derived from them when it is
spawned (see worker.py), so an operator sets one namespace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .roots import Root, parse_roots


@dataclass(frozen=True)
class Settings:
    port: int
    host: str
    keys_file: str
    keys: frozenset[str]
    roots: tuple[Root, ...]
    scratch: str
    worker_socket: str
    worker_script: str
    # Admission: how many requests may be forwarded to the model worker at once (it queues per
    # model family behind that), how many tool-bound requests may run at once, and how many may
    # wait for either before a new one is answered `busy`.
    limit_models: int
    limit_tools: int
    # Transcodes hold a slot for minutes, so they have a lane of their own rather than sitting
    # in the tool lane ahead of every probe and rendition behind them.
    limit_encodes: int
    queue_depth: int
    interactive_reserve: int
    exiftool_workers: int
    tool_timeout_seconds: int
    # A CPU AV1 encode of a long tape runs for hours; the tool timeout would cut it short.
    encode_timeout_seconds: int
    audio_cache_seconds: int
    audio_cache_mb: int
    # The CPU decode's memory footprint, projected before an encode starts: RSS grows with the
    # frames decoded in one process and is never given back (measured at ~170 MB per thousand
    # frames, independent of resolution), so a source over the budget is encoded as windows of
    # this size — one process each — and one whose windows cannot be placed is refused. Zero
    # disables the CPU chain. See mcs/tools/encode.py.
    cpu_encode_max_mb: int
    cpu_encode_mb_per_1k_frames: int
    cpu_encode_max_segments: int
    # Which ffmpeg encoder makes the video renditions: empty picks av1_nvenc when the card and
    # the build offer it and libsvtav1 otherwise; a name pins it (the CPU chain still backs it).
    video_encoder: str
    # NVENC's cq is not libsvtav1's crf: +6 keeps the same `quality` meaning roughly the same
    # bytes on either encoder (measured: cq 38 matched crf 32 within 1%).
    nvenc_cq_offset: int
    nvenc_preset: str

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> "Settings":
        e = dict(os.environ) if env is None else env

        def integer(name: str, default: int) -> int:
            raw = e.get(name, "")
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer, got {raw!r}") from exc

        keys_file = e.get("MCS_KEYS_FILE", "/etc/mcs.keys")
        here = os.path.dirname(os.path.abspath(__file__))
        return Settings(
            port=integer("MCS_PORT", 8181),
            host=e.get("MCS_HOST", "0.0.0.0"),
            keys_file=keys_file,
            keys=load_keys(keys_file, e.get("MCS_KEY")),
            roots=parse_roots(e.get("MCS_ROOTS", "")),
            scratch=e.get("MCS_SCRATCH", "/tmp/mcs"),
            worker_socket=e.get("MCS_WORKER_SOCKET", "/tmp/mcs-model-worker.sock"),
            worker_script=e.get("MCS_WORKER_SCRIPT", os.path.join(here, "modelworker", "model_server.py")),
            limit_models=integer("MCS_LIMIT_MODELS", 8),
            limit_tools=integer("MCS_LIMIT_TOOLS", 4),
            limit_encodes=integer("MCS_LIMIT_ENCODES", 2),
            queue_depth=integer("MCS_QUEUE_DEPTH", 64),
            interactive_reserve=integer("MCS_INTERACTIVE_RESERVE", 1),
            exiftool_workers=integer("MCS_EXIFTOOL_WORKERS", 2),
            tool_timeout_seconds=integer("MCS_TOOL_TIMEOUT", 900),
            encode_timeout_seconds=integer("MCS_ENCODE_TIMEOUT", 6 * 3600),
            audio_cache_seconds=integer("MCS_AUDIO_CACHE_SECONDS", 1800),
            audio_cache_mb=integer("MCS_AUDIO_CACHE_MB", 2048),
            cpu_encode_max_mb=integer("MCS_CPU_ENCODE_MAX_MB", 4000),
            cpu_encode_mb_per_1k_frames=integer("MCS_CPU_ENCODE_MB_PER_1K_FRAMES", 170),
            cpu_encode_max_segments=integer("MCS_CPU_ENCODE_MAX_SEGMENTS", 64),
            video_encoder=e.get("MCS_VIDEO_ENCODER", ""),
            nvenc_cq_offset=integer("MCS_NVENC_CQ_OFFSET", 6),
            nvenc_preset=e.get("MCS_NVENC_PRESET", "p5"),
        )


def load_keys(path: str, inline: str | None) -> frozenset[str]:
    """One key per line, as v1's `mcs.keys`; `MCS_KEY` adds one without a file.

    No file and no key means no authentication at all, which is only right on a private socket
    or a development box — the log says so at startup rather than failing, because a container
    with nothing mounted is still worth being able to probe.
    """
    keys: set[str] = set()
    if inline and inline.strip():
        keys.add(inline.strip())
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    keys.add(line)
    except FileNotFoundError:
        pass
    return frozenset(keys)

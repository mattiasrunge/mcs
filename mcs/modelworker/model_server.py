#!/usr/bin/env python3
"""
Resident model server.

Holds the captioning VLM, InsightFace, MiniLM, whisper and the instruct model loaded across
requests so the media pipeline stops paying model-load cost per file. Measured before this
existed:
~9s of every ~18.5s image was process startup and model loading (describe-file ~7s,
detect-faces ~2.0s to do 117ms of actual work).

Ops are model primitives — one model in, its raw output out. Callers compose them: media's
`describe-file` routes on mimetype, extracts keyframes and joins captions with a transcript
itself, so nothing here needs to know what a media file is.

Protocol: newline-delimited JSON over a unix SOCK_STREAM socket, one request and one
response per connection.

  -> {"op":"caption","paths":["/files/S1/01K..."],"prompt":"<CAPTION>","max_new_tokens":128}
  -> {"op":"transcribe","path":"/tmp/.../audio.wav"}
  -> {"op":"transcribe_signature"}
  -> {"op":"embed_text","text":"..."}
  -> {"op":"generate","messages":[{"role":"user","content":"..."}],"max_new_tokens":192,"json_only":true,"model":"instruct|vlm","interactive":false,"require_gpu":false}
  -> {"op":"diarize","path":"/tmp/.../audio.wav"}
  -> {"op":"embed_voice_segment","path":"/tmp/.../audio.wav","start":12.48,"end":17.84}
  -> {"op":"active_speaker_score","video":"...","audio":"...","start":12.4,"end":17.8,"track":[...]}
  -> {"op":"detect_faces","path":"/files/S1/01K...","angle":270,"mirror":false}
  -> {"op":"embed_face","path":"...","box":{"x":0.1,"y":0.2,"width":0.1,"height":0.1},"angle":270}
  -> {"op":"health"}

`angle`/`mirror` (optional, default 0/false) name the display frame an image op works in —
the file node's rotation, not the EXIF tag's. Face boxes are fractions of that frame.
  <- {"ok":true,"result":...}
  <- {"ok":false,"error":"..."}

Models load lazily on first use and are evicted after CFG_MODEL_IDLE_EVICT seconds
idle, so an audio-free crawl never pays for whisper and VRAM stays bounded on the 4GB
A2000. Inference serialises per family (see model_registry.family_lock).

Families in CFG_MODEL_PINNED (default: minilm) are exempt from eviction and are
preloaded at startup — they sit on the interactive search path, where a reload costs
~24s and is far more noticeable than the VRAM it frees.

The server also holds itself to an RSS ceiling (CFG_MODEL_MAX_RSS, by default a
fraction of the cgroup limit). Crossing it logs the `memstats` line and the `op_counts`
breakdown, then drains and re-execs — see _rss_watchdog for why a leak must not be left
for the OOM killer to find.

A second watchdog covers the other silent failure: captions deferred because the card is full.
Nothing degrades and nothing runs slower - the work simply does not happen, and a backlog that
never drains looks exactly like slow indexing. See _card_busy_watchdog, and registry.vlm_device
for the preflight that mostly avoids it.
"""

import json
import os
import signal
import socket
import socketserver
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mem_diag  # noqa: E402
import model_registry as registry  # noqa: E402

SOCKET_PATH = os.environ.get('CFG_MODEL_SOCKET', '/tmp/murrix-model-server.sock')
EVICT_INTERVAL_SECONDS = 60

# How often the RSS watchdog samples. One read of /proc/self/statm, so the interval is set by
# how fast the ceiling can be crossed rather than by cost: the 2026-08-11 leak ran at ~24GB/h,
# which is ~200MB per 30s — fine against the ~9GB of headroom the default ceiling leaves.
RSS_CHECK_INTERVAL_SECONDS = float(os.environ.get('CFG_MODEL_RSS_INTERVAL', '30'))

# A cross inside this many seconds of startup does not recycle. A ceiling below what the
# pinned models legitimately need would otherwise re-exec forever, each time paying the ~24s
# reload and never getting under the line; the grace period bounds that loop to one restart
# per period and leaves the misconfiguration in the log instead of hiding it in a spin.
RSS_GRACE_SECONDS = float(os.environ.get('CFG_MODEL_RSS_GRACE', '600'))

# How long a recycle waits for in-flight requests before replacing the process anyway. A
# queued VLM caption can legitimately take minutes and is worth waiting for; a wedged
# one is not worth the OOM this exists to avoid.
DRAIN_TIMEOUT_SECONDS = float(os.environ.get('CFG_MODEL_DRAIN_TIMEOUT', '300'))

# How long a crossing waits for the server to go quiet before recycling anyway.
#
# A recycle drops every request still inside `handle`, so one taken mid-request destroys the
# work it was meant to protect. Waiting costs the backstop nothing — a leak is memory still
# held once requests finish, so it is still over the line at the next quiet moment — but the
# wait has to be bounded, because a wedged request must not hold the ceiling open until the
# cgroup OOM-kills the container instead. See _rss_watchdog.
RSS_QUIESCE_TIMEOUT_SECONDS = float(os.environ.get('CFG_MODEL_RSS_QUIESCE', '600'))

# How long the card may stay marked busy before the log says so, and how often it repeats while
# it lasts. A single OOM under a burst recovers on its own and is not worth a line; a busy state
# still set three minutes later is a crawl whose captions are all being deferred, and on a
# multi-week import that is the difference between noticing today and noticing in a week.
VLM_BUSY_ALERT_SECONDS = float(os.environ.get('CFG_VLM_BUSY_ALERT', '180'))
VLM_BUSY_REPEAT_SECONDS = float(os.environ.get('CFG_VLM_BUSY_REPEAT', '900'))

_started_at = time.time()
_requests = 0
_caption_items = 0
_requests_lock = threading.Lock()

# Recycles this lineage of processes has already done, carried across the re-exec in the
# environment. Reading it back hours later from `podman logs` is what separates "the ceiling
# is holding a slow leak at bay" from "nothing leaks and this has never fired".
RECYCLE_COUNT = int(os.environ.get('CFG_MODEL_RECYCLE_COUNT', '0') or 0)

# In-flight request count, for the drain. `socketserver`'s own thread list cannot serve this:
# `_Threads.append` drops daemon threads on the floor, and `daemon_threads = True` is what
# lets a wedged request never hold up shutdown.
_inflight = 0
_inflight_cv = threading.Condition()

# Set while `_preload` is loading the pinned families. A model load is the largest allocation
# this process makes and it happens with no request in flight, so without this the RSS watchdog
# would count the server as quiet at exactly the moment it is doing the most work — and recycle
# into a reload of the very models that were half-loaded.
_preloading = False

# Set when a recycle is decided and cleared by an explicit shutdown, which is what makes a
# SIGTERM cancel one. Handlers keep running to completion either way; `main` acts on it after
# `serve_forever` has returned and the socket is gone.
_recycling = False

# The RSS ceiling in bytes, resolved once in `main` so `health` can report it without
# re-reading the cgroup on every poll. None when there is no ceiling.
_max_rss: int | None = None

# Requests served per op. A total alone cannot say which op a memory series is tracking, and
# the ops differ by orders of magnitude in what they allocate — attributing growth needs the
# breakdown, taken hours after the fact from a `health` poll.
_op_counts: dict[str, int] = {}


def log(message: str) -> None:
    print(f"[model-server] {message}", file=sys.stderr, flush=True)


def _hub_cache_dir() -> str:
    """Where huggingface_hub will look for weights, resolved without importing it.

    Same precedence the library uses, so this is a report rather than a second opinion.
    """
    explicit = os.environ.get('HF_HUB_CACHE') or os.environ.get('HUGGINGFACE_HUB_CACHE')
    if explicit:
        return explicit
    hf_home = os.environ.get('HF_HOME')
    if not hf_home:
        xdg = os.environ.get('XDG_CACHE_HOME') or os.path.join(
            os.path.expanduser('~'), '.cache'
        )
        hf_home = os.path.join(xdg, 'huggingface')
    return os.path.join(hf_home, 'hub')


def _log_model_cache() -> None:
    """Say where the weights will be read from, and whether they are actually there.

    A miss here is not a slow start, it is ~17GB pulled from the Hub inside a pipeline step,
    against the caption client's 600s timeout, on every container recreate. It is also
    completely silent: transformers just downloads. The cache follows $HOME unless HF_HOME
    says otherwise, and $HOME is not the same at image-build time as it is under a MURRiX
    shell — which is exactly how fry2 spent 2026-09-06 re-fetching four baked models.
    """
    cache = _hub_cache_dir()
    # faster-whisper resolves a bare model name to the Systran repo that hosts it, and
    # sentence-transformers prefixes its own org — so both are spelled out as the repo id the
    # cache is actually keyed by, not as the name the config uses.
    wanted = {
        'vlm': registry.VLM_MODEL,
        'instruct': registry.INSTRUCT_MODEL,
        'whisper': registry.WHISPER_MODEL if '/' in registry.WHISPER_MODEL
        else f'Systran/faster-whisper-{registry.WHISPER_MODEL}',
        'minilm': 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
    }
    missing = [
        name for name, repo in wanted.items()
        if not os.path.isdir(os.path.join(cache, f"models--{repo.replace('/', '--')}"))
    ]
    if missing:
        log(f"WARNING hub cache {cache} has no local copy of {', '.join(sorted(missing))} — "
            f"these will be DOWNLOADED on first use, inside whatever request asks for them. "
            f"Set HF_HOME to wherever the image baked them.")
    else:
        log(f"hub cache {cache} (all baked models present)")


def handle(request: dict) -> dict:
    """Dispatch one request. Imports inference_ops lazily so startup stays fast."""
    op = request.get("op")

    if op == "health":
        health = {
            "loaded": registry.loaded(),
            "uptime": round(time.time() - _started_at, 1),
            "requests": _requests,
            "caption_items": _caption_items,
            "op_counts": dict(_op_counts),
            "socket": SOCKET_PATH,
            "recycles": RECYCLE_COUNT,
        }
        # Where the server sits against its own ceiling. One /proc read, and it is the
        # difference between "indexing feels slow" and "it is 300MB from recycling again".
        rss = mem_diag.rss_bytes()
        if rss is not None:
            health["rss_mb"] = round(rss / 1048576)
        if _max_rss:
            health["max_rss_mb"] = round(_max_rss / 1048576)
        # torch's own accounting, which is what distinguishes "the models are big" from
        # "the caching allocator is holding blocks it will never reuse": reserved is what
        # nvidia-smi sees, allocated is what the live tensors actually need. A large gap
        # is cache, and reclaimable. Reported only if torch is already imported, so a
        # faces-only server does not load it just to answer a health check.
        torch = sys.modules.get("torch")
        if torch is not None:
            try:
                if torch.cuda.is_available():
                    health["vram_allocated_mb"] = round(torch.cuda.memory_allocated() / 1048576)
                    health["vram_reserved_mb"] = round(torch.cuda.memory_reserved() / 1048576)
            except Exception:  # noqa: BLE001 - diagnostics must never break health
                pass
        # The card-busy state, named rather than left to be inferred from a missing `vlm:cuda`
        # entry in `loaded`. Present only while it holds, so its absence is the healthy case
        # and any consumer can treat the key itself as the alarm. Plain bookkeeping — no
        # device probe, so a faces-only server still answers without importing torch.
        fallback = registry.card_busy()
        if fallback is not None:
            health["caption_fallback"] = fallback
        return health

    if op == "memstats":
        # Host-RSS diagnostics for the ratchet that OOM-kills this server on fry. Kept out of
        # `health` because it reads /proc/self/maps, which is O(mappings) and pointless on the
        # per-request health path. See mem_diag.py for what the numbers decide.
        return mem_diag.snapshot()

    import inference_ops as ops

    angle, mirror = request.get("angle", 0), bool(request.get("mirror", False))

    if op == "caption":
        paths, prompt = request.get("paths"), request.get("prompt")
        if not paths or not prompt:
            raise ValueError("caption: missing paths or prompt")
        return ops.caption(paths, prompt, int(request.get("max_new_tokens", 256)), angle, mirror)

    if op == "caption_batch":
        requests = request.get("requests")
        if not isinstance(requests, list) or not requests:
            raise ValueError("caption_batch: missing requests")
        return ops.caption_batch(requests)

    if op == "transcribe":
        path = request.get("path")
        if not path:
            raise ValueError("transcribe: missing path")
        # Optional per-request decode settings; absent means the configured value.
        min_silence = request.get("vad_min_silence_ms")
        return ops.transcribe(
            path,
            language=request.get("language"),
            min_silence_ms=int(min_silence) if min_silence is not None else None,
            word_timestamps=request.get("word_timestamps"),
        )

    if op == "transcribe_signature":
        # What a transcript decoded right now would be stamped with; no model is loaded.
        # media-scan asks this once per walk to find transcripts made with other settings.
        return {"signature": ops.transcribe_signature()}

    if op == "embed_text":
        text = request.get("text")
        if text is None:
            raise ValueError("embed_text: missing text")
        return ops.embed_text(text)

    if op == "generate":
        messages = request.get("messages")
        if not messages:
            raise ValueError("generate: missing messages")
        return ops.generate(
            messages,
            int(request.get("max_new_tokens", 192)),
            bool(request.get("json_only", False)),
            str(request.get("model", "instruct")),
            request.get("expires_at"),
            bool(request.get("interactive", False)),
            bool(request.get("require_gpu", False)),
        )

    if op == "diarize":
        path = request.get("path")
        if not path:
            raise ValueError("diarize: missing path")
        return ops.diarize(path)

    if op == "embed_voice_segment":
        path = request.get("path")
        start, end = request.get("start"), request.get("end")
        if not path or start is None or end is None:
            raise ValueError("embed_voice_segment: missing path, start or end")
        return ops.embed_voice_segment(path, float(start), float(end))

    if op == "active_speaker_score":
        video, audio = request.get("video"), request.get("audio")
        start, end, track = request.get("start"), request.get("end"), request.get("track")
        if not video or not audio or start is None or end is None or track is None:
            raise ValueError("active_speaker_score: missing video, audio, start, end or track")
        return ops.active_speaker_score(video, audio, float(start), float(end), track)

    if op == "detect_faces":
        path = request.get("path")
        if not path:
            raise ValueError("detect_faces: missing path")
        return ops.detect_faces(path, angle, mirror)

    if op == "embed_face":
        path, box = request.get("path"), request.get("box")
        if not path or box is None:
            raise ValueError("embed_face: missing path or box")
        return ops.embed_face(path, box, angle, mirror)

    raise ValueError(f"unknown op: {op}")


class Handler(socketserver.StreamRequestHandler):
    # A queued VLM describe can legitimately take minutes; don't time out on it.
    timeout = None

    def handle(self):
        global _requests, _caption_items, _inflight
        try:
            raw = self.rfile.readline()
        except OSError:
            return
        if not raw:
            return

        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._reply({"ok": False, "error": f"malformed request: {exc}"})
            return

        op = request.get("op", "?")
        with _requests_lock:
            _requests += 1
            _op_counts[op] = _op_counts.get(op, 0) + 1
            if op == "caption":
                _caption_items += 1
            elif op == "caption_batch":
                _caption_items += len(request.get("requests") or [])
        # Counted before the work and released in the same `finally` as the trim, so a
        # recycle's drain covers the reclaim too — re-exec'ing between a reply and its
        # `cleanup_torch` would be harmless, but a request still inside the VLM is not.
        with _inflight_cv:
            _inflight += 1

        started = time.time()
        try:
            result = handle(request)
            self._reply({"ok": True, "result": result})
            if op != "health":
                log(f"{op} ok in {round(time.time() - started, 2)}s")
        except Exception as exc:  # noqa: BLE001 - every failure goes back to the client
            # `error_type` is the class name, for the HTTP front to classify the failure by
            # (CardBusy → retry later, FileNotFoundError → not found) without parsing prose.
            self._reply({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
            log(f"{op} failed after {round(time.time() - started, 2)}s: {exc}")
            # The client only ever sees `str(exc)`, and one line is not enough for the
            # failures that matter here. `Input type (c10::Half) and bias type (float)`
            # says a dtype mismatch happened somewhere in the model but not *where*, and
            # since it only appears after hours of uptime there is no second chance to
            # catch it — by the time anyone looks, a restart has cleared the state. The
            # traceback names the module, so print it where it costs nothing to have.
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
        finally:
            # Resident mode never calls release(), so this is the only per-request point to
            # return freed inference memory to the OS. The buffers are native (numpy/torch/
            # onnxruntime): gc frees almost nothing and glibc parks the bytes at each arena's
            # high-water mark, so without this the server's RSS climbs unbounded until the
            # container OOMs. Runs after _reply so the client isn't held up by the trim.
            #
            # `memstats` is exempt alongside `health`: it exists to measure the memory the trim
            # acts on, so trimming on its way out would make every reading a post-trim one and
            # hide the very growth it is there to catch.
            if op not in ("health", "memstats"):
                registry.cleanup_torch()
            with _inflight_cv:
                _inflight -= 1
                _inflight_cv.notify_all()

    def _reply(self, payload: dict) -> None:
        try:
            self.wfile.write((json.dumps(payload) + "\n").encode())
            self.wfile.flush()
        except OSError:
            # Client vanished (pipeline step killed) — nothing to do.
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    # Clients queue on the per-family lock rather than being refused.
    request_queue_size = 64
    allow_reuse_address = True


def _preload(stop: threading.Event) -> None:
    """Load the pinned families up front, off the request path.

    Without this the pin only helps from the *second* search onwards: the first one after a
    restart still pays the full model load. Runs in a thread so a slow load never delays
    binding the socket — clients that arrive first just fall back to in-process loading.

    Each load takes the family lock, which is what stops it racing a request for the same
    family. `_get` loads outside the registry lock on purpose, so without this both threads
    end up inside the loader at once — and transformers is a lazy module, so a concurrent
    first import of it fails with "cannot import name 'AutoModelForCausalLM' from
    'transformers'" rather than merely doing the work twice. Observed on fry: the first
    query-parse after a restart landed inside the instruct preload and got exactly that.
    """
    global _preloading
    _preloading = True
    try:
        _preload_families(stop)
    finally:
        _preloading = False


def _preload_families(stop: threading.Event) -> None:
    """The loop itself, split out only so `_preload` can bracket it with `_preloading`."""
    for family in sorted(registry.PINNED_FAMILIES):
        if stop.is_set():
            return
        try:
            started = time.time()
            # Loading goes on the family's own thread, not this one. A thread that loads a
            # model acquires the same per-thread CUDA state a thread that runs one does, and
            # this thread exits as soon as the preload is done — see run_on_model_thread.
            if family == 'minilm':
                def load():
                    registry.sentence_transformer(registry.select_device())
            elif family == 'instruct':
                def load():
                    registry.instruct(registry.instruct_device())
            else:
                log(f"preload: no loader wired for pinned family '{family}', skipping")
                continue
            with registry.family_lock(family):
                registry.run_on_model_thread(family, load)
            log(f"preloaded {family} in {time.time() - started:.1f}s")
        except Exception as exc:  # noqa: BLE001 - preload is best-effort
            log(f"preload of {family} failed: {exc}")


def _evict_loop(stop: threading.Event) -> None:
    while not stop.wait(EVICT_INTERVAL_SECONDS):
        try:
            evicted = registry.evict_idle()
            if evicted:
                log(f"evicted idle models: {', '.join(evicted)}")

            # Reclaim regardless of whether anything was evicted. torch's caching
            # allocator keeps freed blocks reserved rather than returning them to the
            # driver, and describe-file feeds it a different image size on every call, so
            # the cache grows into a pile of odd-sized blocks it can never reuse. Eviction
            # used to be the only thing that ran empty_cache — and during a crawl the
            # models are in constant use and never go idle, so it never ran at all.
            # Observed on fry: ~2.7GB of resident weights holding 13.5GB of VRAM and
            # climbing, which would have left no room for whisper (5.5GB) on the first
            # video. empty_cache is cheap when there is nothing cached to free.
            registry.cleanup_torch()

            # onnxruntime's CUDA arena never gives memory back, so InsightFace walks the
            # card under sustained face work until a CUDA OOM drops the VLM and InsightFace
            # itself onto the CPU. Recreating its sessions releases every arena; the reload
            # costs ~1-2s against the hundreds of images between resets.
            freed_at = registry.reset_insightface_if_low()
            if freed_at is not None:
                log(f"reset insightface sessions to release onnxruntime arenas "
                    f"(free VRAM was {freed_at:.0f}MB)")
        except Exception as exc:  # noqa: BLE001
            log(f"eviction failed: {exc}")


def _work_requests() -> int:
    """Requests that actually ran a model — `health` and `memstats` allocate nothing."""
    with _requests_lock:
        return sum(n for op, n in _op_counts.items() if op not in ("health", "memstats"))


def _captions_served() -> int:
    with _requests_lock:
        return _caption_items


def _card_busy_watchdog(stop: threading.Event) -> None:
    """Say out loud when captions have been deferred for a while by a full card.

    Modelled on `_rss_watchdog` for the same reason it exists: the failure is expensive,
    entirely silent, and indistinguishable from a crawl that is merely slow. Captions are no
    longer moved to the CPU when the card is full — they are deferred back to the pipeline
    queue — so the symptom is a describe backlog that stops draining while every health probe
    still reports fine. `inference-status` reports it, but it is a hand-typed command with no
    caller, so on its own it only ever tells someone who already suspected. This is what tells
    someone who did not.

    Not an alarm on the first OOM: one under a burst recovers by itself and the backoff in
    `registry.vlm_device` is exactly what makes that cheap. It is a busy state still set
    minutes later that means the card is not coming back on its own.

    The line carries what decides the next move — free VRAM, what is loaded, and the caption
    rate since the deferrals began — because the alternative is what happened last time, which is
    reconstructing all three from `dmesg` afterwards.
    """
    since_captions: int | None = None
    since_at = 0.0
    alerted_at = 0.0

    while not stop.wait(RSS_CHECK_INTERVAL_SECONDS):
        try:
            state = registry.card_busy()

            if state is None:
                if since_captions is not None:
                    log("card-busy cleared: captioning is back on the GPU")
                since_captions, since_at, alerted_at = None, 0.0, 0.0
                continue

            now = time.monotonic()
            if since_captions is None:
                since_captions, since_at = _captions_served(), now

            if state["seconds"] < VLM_BUSY_ALERT_SECONDS:
                continue
            if alerted_at and now - alerted_at < VLM_BUSY_REPEAT_SECONDS:
                continue
            alerted_at = now

            captions = _captions_served() - since_captions
            elapsed = max(now - since_at, 1.0)
            free_mb = registry.free_vram_mb()
            free = f"{free_mb:.0f}MB" if free_mb is not None else "unknown"
            log(f"CAPTIONS DEFERRED: {state['seconds']:.0f}s and {state['events']} event(s) — "
                f"the card cannot hold the VLM, {captions} caption(s) at "
                f"{captions / elapsed * 3600:.0f}/h since. free vram {free}, "
                f"loaded {', '.join(registry.loaded()) or 'none'}, retrying cuda in "
                f"{state['retry_in']:.0f}s. Cause: {state['reason']}")
        except Exception as exc:  # noqa: BLE001 - a broken probe must not kill the server
            log(f"card-busy watchdog failed: {exc}")


def _rss_watchdog(stop: threading.Event, server: "Server", ceiling: int) -> None:
    """Recycle the process when its own RSS crosses `ceiling`.

    Three per-request leaks have taken this server to the container cap (glibc arena
    retention, onnxruntime's CUDA PerThreadContext, torch's `generate` per request thread),
    each found only afterwards from `dmesg`. Every one of them looked identical from outside
    — indexing slows, the box swaps, `python3` is OOM-killed hours later — because the server
    had no notion of its own budget and nothing noticed until the kernel did, by which point
    the whole host had spent hours in direct reclaim.

    So this is a ceiling of the server's own, well under the cgroup's: cross it and the
    process drains and rebuilds itself (~24s for the pinned families) instead of being killed
    mid-request. It is a backstop, not a fix — the loud log is the point, because a recycle
    that keeps happening is the next leak, and the `memstats` line plus `op_counts` are
    exactly the evidence needed to attribute it.

    A crossing is acted on only when the server is quiet, or after
    RSS_QUIESCE_TIMEOUT_SECONDS of waiting for it to become so. Recycling on the sample that
    first goes over the line means recycling in the middle of whatever allocated the bytes,
    which for a model load is the request that most needs to survive.
    """
    warned = False
    quiet_deadline: float | None = None
    while not stop.wait(RSS_CHECK_INTERVAL_SECONDS):
        try:
            rss = mem_diag.rss_bytes()
            if rss is None or rss < ceiling:
                # Back under the line, so whatever was over it was transient and the wait
                # for a quiet moment starts again from scratch the next time.
                quiet_deadline = None
                continue

            uptime = time.time() - _started_at
            served = _work_requests()
            if uptime < RSS_GRACE_SECONDS or served == 0:
                # Over the line with nothing to blame it on: the models alone do not fit
                # under this ceiling, and re-exec'ing would only reload them into the same
                # wall. Say so once and keep serving — the cgroup is still the backstop.
                if not warned:
                    warned = True
                    log(f"WARNING rss {rss / 1048576:.0f}MB is already over the "
                        f"{ceiling / 1048576:.0f}MB ceiling after {uptime:.0f}s and "
                        f"{served} model request(s) — too early to blame a leak, not "
                        f"recycling. Raise CFG_MODEL_MAX_RSS if this is the real "
                        f"footprint.")
                continue

            # Never recycle out from under a request that could account for the growth.
            #
            # Loading the VLM for a caption is itself a multi-GB host spike, so a ceiling the
            # resident models nearly fill is crossed *by* the caption — and a recycle then
            # kills it, the replacement reloads, spikes, and crosses again. Measured on fry2
            # 2026-09-05: two recycles in 17 minutes, every caption dropped at the client's
            # 600s timeout, and not one description written for the whole corpus.
            #
            # A leak is by definition memory still held after requests finish, so deferring
            # to the next quiet moment gives up nothing: if these bytes are a leak, RSS is
            # still over the line when the last request returns. The wait is bounded so a
            # wedged request cannot hold the ceiling open until the cgroup does the killing.
            with _inflight_cv:
                busy = _inflight
            doing = f"{busy} request(s) in flight" if busy else "the pinned models still loading"
            if busy or _preloading:
                if quiet_deadline is None:
                    quiet_deadline = time.time() + RSS_QUIESCE_TIMEOUT_SECONDS
                    log(f"rss {rss / 1048576:.0f}MB over the {ceiling / 1048576:.0f}MB "
                        f"ceiling with {doing} — waiting up to "
                        f"{RSS_QUIESCE_TIMEOUT_SECONDS:.0f}s for the server to go quiet "
                        f"before recycling")
                if time.time() < quiet_deadline:
                    continue
                log(f"still {doing} {RSS_QUIESCE_TIMEOUT_SECONDS:.0f}s after the crossing "
                    f"— recycling anyway")

            _recycle(stop, server, rss, ceiling)
            return
        except Exception as exc:  # noqa: BLE001 - a broken probe must not kill the server
            log(f"rss watchdog failed: {exc}")


def _recycle(stop: threading.Event, server: "Server", rss: int, ceiling: int) -> None:
    """Log the evidence, then ask `main` to drain and re-exec."""
    global _recycling
    _recycling = True

    log(f"RSS CEILING CROSSED: {rss / 1048576:.0f}MB over {ceiling / 1048576:.0f}MB after "
        f"{time.time() - _started_at:.0f}s and {_work_requests()} model request(s) — "
        f"draining and re-execing (recycle #{RECYCLE_COUNT + 1})")
    # Taken before anything reclaims, and in the same shape the periodic log writes, so the
    # crossing lands in the same `grep memstats` series as the hours that led to it.
    try:
        log(f"memstats {mem_diag.format_line(mem_diag.snapshot(), registry.loaded())}")
    except Exception as exc:  # noqa: BLE001
        log(f"memstats at ceiling failed: {exc}")
    # What ran, per op. A total cannot say which op a series is tracking and the ops differ by
    # orders of magnitude in what they allocate; this is what turns "it grew" into "it grew
    # 30MB per caption".
    with _requests_lock:
        counts = " ".join(f"{op}={n}" for op, n in sorted(_op_counts.items()))
    log(f"op_counts {counts or 'none'}")

    stop.set()
    # `shutdown()` blocks until `serve_forever` has returned, so it goes on a thread of its
    # own exactly as the signal handler does, and this one ends here.
    threading.Thread(target=server.shutdown, daemon=True).start()


def _drain() -> None:
    """Wait for in-flight requests, bounded by DRAIN_TIMEOUT_SECONDS.

    By the time this runs the listening socket is closed and its path unlinked, so nothing new
    arrives: a client that connects now gets ENOENT and falls back to in-process
    `inference_call.py`, which is the same contract as the service being stopped. This only
    bounds the wait on requests already inside `handle`. Going anyway after the timeout is no
    worse than the OOM kill it replaces — the client reads a dropped connection as unavailable
    and does the work in-process — and it keeps one wedged request from holding the ceiling
    open indefinitely.
    """
    deadline = time.time() + DRAIN_TIMEOUT_SECONDS
    with _inflight_cv:
        while _inflight and _recycling:
            remaining = deadline - time.time()
            if remaining <= 0:
                log(f"drain timed out with {_inflight} request(s) still running")
                return
            _inflight_cv.wait(min(remaining, 1.0))
    if _recycling:
        log("drained")


def _reexec() -> int:
    """Replace this process with a fresh copy of itself.

    `execv` rather than exiting onto the service's `restartPolicy`: the pid, the service
    instance and its shell all survive, so a recycle every few hours never spends the
    restart budget that exists to give up on a genuinely broken service.

    Every inherited descriptor is marked close-on-exec first. `exec` replaces the address
    space but *not* the file table, and a surviving `/dev/nvidia*` descriptor would keep the
    old CUDA context — and its VRAM — alive in a process that can no longer reach it, which
    is the opposite of the point. Only stdin/stdout/stderr are kept, which is the whole of
    what `external` gives this process.
    """
    import fcntl

    try:
        inherited = [int(name) for name in os.listdir("/proc/self/fd")]
    except OSError:
        inherited = []
    for fd in inherited:
        if fd < 3:
            continue
        try:
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        except OSError:
            pass  # already gone, or not ours to touch

    os.environ["CFG_MODEL_RECYCLE_COUNT"] = str(RECYCLE_COUNT + 1)
    argv = [sys.executable, os.path.abspath(__file__), *sys.argv[1:]]
    log(f"re-exec {' '.join(argv)}")
    sys.stderr.flush()
    try:
        os.execv(argv[0], argv)
    except OSError as exc:
        # Nothing left to serve with — the socket is closed and unlinked. Exit non-zero so
        # the service's on-failure policy starts a new one.
        log(f"re-exec failed: {exc}")
        return 1
    return 0  # unreachable: execv does not return


def main() -> int:
    global _max_rss

    registry.set_resident(True)

    # A socket file left behind by a killed server would make clients hang on connect
    # instead of falling back, so clear it before binding.
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = Server(SOCKET_PATH, Handler)
    # The pipeline runs as a different uid than the server in some setups; the socket
    # carries no secrets beyond what those callers already have.
    os.chmod(SOCKET_PATH, 0o666)

    stop = threading.Event()
    evictor = threading.Thread(target=_evict_loop, args=(stop,), daemon=True)
    evictor.start()
    threading.Thread(target=_preload, args=(stop,), daemon=True).start()
    threading.Thread(target=_card_busy_watchdog, args=(stop,), daemon=True).start()

    # The RSS ratchet takes 6-11h to reach the container cap, so the series has to collect
    # unattended — polling `memstats` by hand only ever samples the healthy first hour.
    memlog_interval = mem_diag.interval_from_env()
    if memlog_interval:
        threading.Thread(
            target=mem_diag.log_loop,
            args=(stop, log, memlog_interval, registry.loaded),
            daemon=True,
        ).start()

    _log_model_cache()

    _max_rss, ceiling_source = mem_diag.max_rss_from_env()
    if _max_rss:
        log(f"rss ceiling {_max_rss / 1048576:.0f}MB ({ceiling_source})")
        threading.Thread(
            target=_rss_watchdog, args=(stop, server, _max_rss), daemon=True
        ).start()
    else:
        log(f"rss ceiling disabled ({ceiling_source})")

    def shutdown(_signum, _frame):
        global _recycling
        log("shutting down")
        # Cancels a recycle in flight, including one already draining: an operator stopping
        # the service must not have it re-exec itself back into existence.
        _recycling = False
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    pinned = ', '.join(sorted(registry.PINNED_FAMILIES)) or 'none'
    recycled = f", recycle #{RECYCLE_COUNT}" if RECYCLE_COUNT else ""
    log(f"listening on {SOCKET_PATH} (idle evict {registry.IDLE_EVICT_SECONDS}s, "
        f"pinned: {pinned}{recycled})")
    try:
        server.serve_forever()
    finally:
        # Closing and unlinking here is what makes the drain safe: from this point a client
        # finds no socket and falls back in-process, so the only requests left are the ones
        # already being served.
        server.server_close()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        log("stopped")

    if _recycling:
        _drain()
        # Re-checked after the drain, not only before it: a SIGTERM that lands while we are
        # waiting cancels the recycle, and an operator stopping the service must not have it
        # replace itself at the last moment.
        if _recycling:
            return _reexec()
        log("recycle cancelled by shutdown")

    return 0


if __name__ == "__main__":
    sys.exit(main())

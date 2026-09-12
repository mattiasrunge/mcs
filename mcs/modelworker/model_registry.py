#!/usr/bin/env python3
"""
Model cache shared by the one-shot CLI scripts and the resident model server.

Two modes, because the memory behaviour has to differ:

  - one-shot (default): `release()` frees a model right after use, exactly as the
    scripts did before. The pipeline runs up to CFG_PIPELINE_MAX_CONCURRENT of these at
    once and Florence-2-large peaks around 5.3GB RSS, so a one-shot process must not
    hold two model families resident at the same time.
  - resident (`set_resident(True)`, used by model_server.py): models stay loaded and
    `release()` is a no-op. Idle families are evicted by `evict_idle()` so an
    audio-free crawl stops paying for whisper and VRAM stays bounded.

Everything imports lazily inside the getters — importing this module must stay cheap
so the socket client path never pays for torch.
"""

import gc
import heapq
import os
import sys
import threading
import time
import warnings
from contextlib import contextmanager

warnings.filterwarnings('ignore')
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('ORT_LOGGING_LEVEL', '3')

# Unload a resident family after this long without a request.
#
# Six hours, not the fifteen minutes it was. Fifteen collided with the GPU worker's kind
# quantum, which is also fifteen minutes (CFG_GPU_KIND_QUANTUM_MS): a full quantum of face work
# leaves the VLM idle for exactly as long as it takes to be evicted, so the first caption after
# every rotation paid a cold load. That load is not the ~36s the batching comment in
# pool-worker assumes — MEASURED on fry2 2026-09-06 it is 8-14 minutes from local disk, against
# 4-6s for the caption itself. Evicting to reclaim VRAM and then spending twelve minutes
# getting it back is a loss in every direction.
#
# The premise it was sized under is also gone. It reads "an idle family squatting on the card
# is the difference between the next batch fitting and not", which was true when the models did
# not co-exist. They do: measured resident together at 6.9GB of a 16GB card
# (insightface + minilm:cuda + vlm:cuda), with recycles 0.
#
# Long enough that a working crawl never evicts, short enough that a machine left alone
# overnight gives the card back. VRAM pressure is handled where it belongs and by measurement
# rather than by a clock — `reset_insightface_if_low` releases onnxruntime's arenas and
# `vlm_device`'s preflight defers a caption when the card is genuinely full.
IDLE_EVICT_SECONDS = float(os.environ.get('CFG_MODEL_IDLE_EVICT', '21600'))

# Families that are never evicted, and are preloaded by the resident server.
#
# MiniLM is the odd one out: it is ~120MB, and it sits on the *interactive* path — every
# search embeds its query with it. Evicting it to save 120MB means the next search pays a
# ~24s reload (measured), which is the difference between search feeling instant and
# feeling broken. The VLM and whisper are batch-path and worth evicting; MiniLM is not.
#
# Natural-language media search now asks the already-resident VLM only for the two short lists
# it cannot derive deterministically (people and places), at the front of the queued crawl work.
# Keeping the 1.5B instruct model pinned on the CPU would spend ~6.2GB of host RAM on no
# production caller. It remains available, unpinned, for experiments and future text callers.
PINNED_FAMILIES = {
    name.strip() for name in os.environ.get('CFG_MODEL_PINNED', 'minilm').split(',') if name.strip()
}

# Which faster-whisper model to load. MUST match the model baked into the image
# (src/Containerfile ARG WHISPER_MODEL) or the first transcription downloads it at runtime,
# inside a pipeline step, with no network guarantee.
#
# Measured on one Swedish home video (fry, RTX 5060 Ti), same clip and beam size. Times are
# inference only; the GB column is total device usage at that point in the run, so it
# accumulates across models rather than being a clean per-model figure:
#   base            ~7s            garbled, plus a hallucinated laughter loop
#   small            8.1s  1.85GB  still dropping most words
#   large-v3-turbo   8.1s  3.33GB  recognisable
#   large-v3        12.6s  5.51GB  recovers whole sentences and first names
# large-v3 is only ~1.6x turbo's time and is not pinned, so it is evicted after an idle spell
# and costs nothing on an audio-free crawl. It is NOT to be downgraded to turbo to free VRAM:
# the project's accuracy-over-time rule forbids trading "recovers whole sentences and first
# names" for "recognisable", and with one model on the card at a time it no longer has to
# share anyway.
WHISPER_MODEL = os.environ.get('CFG_WHISPER_MODEL', 'large-v3')

# Language whisper is told to expect, as an ISO-639-1 code. Empty means auto-detect.
#
# Detection reads only the first 30 seconds, and it decides for the *whole file*. A home
# video that opens on wind noise, a doorbell or a single English loanword is routinely
# detected as Danish or Norwegian, and every following sentence is then decoded as that
# language — the output looks like speech and is entirely wrong, which is worse than
# nothing because it goes into search. Naming the language the archive is mostly in avoids
# that; leave it empty for a mixed corpus where detection is the lesser risk.
WHISPER_LANGUAGE = os.environ.get('CFG_WHISPER_LANGUAGE', '').strip()

# Whether to align each word to its own timestamp, rather than each segment.
#
# Something reads it now. Whisper segments on pauses and the diarizer on voices, so one cue
# can hold the end of one person's sentence and the start of another's; `resolveSpeakers` in
# @media/transcript cuts such a cue at the word nearest the change, and without these timings
# there is nowhere to cut and it has to attribute the whole line to whoever talked most of
# it. The timings ride in the VTT itself as WebVTT timestamp tags, so turning this on changes
# what gets written, not just what is computed.
#
# Still off by default, for two reasons rather than one. It is a second pass over the
# decoder's cross-attention for every segment, whose cost on this corpus has not been
# measured; and it only helps a file that is transcribed *after* it is switched on, since an
# existing VTT carries no words. Both of those land in the same place — the backfill, where
# re-transcription is already the work and the cost can be measured against a real slice
# before committing the archive to it. See work/plans/voice-fingerprint.md.
WHISPER_WORD_TIMESTAMPS = os.environ.get('CFG_WHISPER_WORD_TIMESTAMPS', '') == '1'

# The Silero VAD in front of whisper: what counts as speech, and what is cut away.
#
# The VAD is what stops whisper hallucinating over silence — an archive of phone clips is
# mostly silence, and left to itself the decoder fills it with invented speech and
# repetition loops. But faster-whisper does not merely skip the silence: it cuts every gap
# longer than MIN_SILENCE out of the audio, concatenates what is left into one stream, and
# decodes *that*. So the setting decides what the decoder hears, and a tight one feeds it
# sentences jammed together with no breath between them.
#
# These three were MEASURED together on fry2 2026-09-11 (scripts/whisper-coverage.py --sweep,
# 12 talking clips and 11 with audio the old setting found no speech in; the numbers are in
# work/tasks/done/media-whisper-vad-drops-audible-speech.md). The old setting — threshold 0.5,
# pad 400ms, silence 500ms — was dropping a third of the words on a plainly audible two-person
# conversation, and every single-knob loosening that recovered them also let whisper invent
# ~60 words of fluent nonsense on one near-silent clip. Only the three together recovered the
# speech (+14% words over the talking half, +34% on the clip that raised it) while keeping
# that clip to a line, and that residue is what WHISPER_NO_SPEECH_PROB_MAX below removes.
#
#   threshold   Silero's speech probability a frame needs. Outdoor audio, wind, water and a
#               child's voice a few metres from a phone are exactly what 0.5 rejects.
#   pad         Silence kept on each side of a region, so word onsets are not clipped and the
#               decoder gets a natural edge rather than a cut.
#   silence     How long a gap has to be before it is cut. 2000ms is the library's own
#               default; at 500ms every pause between sentences went too.
#
# Do not lower `silence` or raise `threshold` back "to be safe": measured alone, each of
# those made the hallucination problem *worse*, not better — a short fragment of borderline
# audio is what whisper invents over, and both settings produce more of them.
WHISPER_VAD_THRESHOLD = float(os.environ.get('CFG_WHISPER_VAD_THRESHOLD', '0.2'))
WHISPER_VAD_SPEECH_PAD_MS = int(os.environ.get('CFG_WHISPER_VAD_SPEECH_PAD_MS', '800'))
WHISPER_VAD_MIN_SILENCE_MS = int(os.environ.get('CFG_WHISPER_VAD_MIN_SILENCE_MS', '2000'))


def whisper_vad_parameters() -> dict:
    """The `vad_parameters` the pipeline decodes with; one place so a harness measures the same thing."""
    return {
        'threshold': WHISPER_VAD_THRESHOLD,
        'speech_pad_ms': WHISPER_VAD_SPEECH_PAD_MS,
        'min_silence_duration_ms': WHISPER_VAD_MIN_SILENCE_MS,
    }


# Segments the decoder itself rates this likely to be non-speech are dropped.
#
# Whisper scores every window for "there is no speech here" before decoding it, and the
# reference implementation only acts on that score when the decode was also low-confidence.
# A hallucination is the opposite case: a confident, fluent sentence over a window the model
# rated 0.9 no-speech. In the same measurement, every invented line — the fluent nonsense and
# the "Takk for at du så på!" video-credit class — scored 0.84-0.94 (a few credit lines
# 0.68-0.76), and no genuine line anywhere in the sample scored above 0.61. 0.8 sits in the
# gap on the side that keeps speech; a sung refrain at 0.61 stays, and a stray credit line
# at 0.7 is the accepted residue. Set to 1 to disable.
WHISPER_NO_SPEECH_PROB_MAX = float(os.environ.get('CFG_WHISPER_NO_SPEECH_PROB_MAX', '0.8'))

# Speaker diarization: who is talking, and between which seconds. Same baked-model rule as
# WHISPER_MODEL (src/Containerfile). Sortformer is end-to-end and handles overlapping speech.
SORTFORMER_MODEL = os.environ.get('CFG_SORTFORMER_MODEL', 'nvidia/diar_sortformer_4spk-v1')

# How much audio goes into one diarization call, in seconds.
#
# This is not a tuning knob, it is a hard model limit with a measured number behind it.
# Sortformer's own training config declares `session_len_sec: 90`, and it is not linear in
# input length: handed a whole recording it allocates proportionally to the square of the
# sequence. MEASURED on fry 2026-09-10, one ~12-minute file asked for 18.00 GiB on a 15.52 GiB
# card and raised OutOfMemoryError, while the same corpus windowed at 90s ran 100 files
# without a single failure at 988MB peak and 328x realtime.
#
# Raising this does not buy accuracy; it buys an OOM. The cost of windowing is that speaker
# indices are local to each call, which `diarize` reports rather than hides — see there.
DIARIZE_WINDOW_SECONDS = float(os.environ.get('CFG_DIARIZE_WINDOW_SECONDS', '90'))

# Voiceprint embedding, for matching a speech turn to a person the way a face embedding
# matches a photo to one. Same baked-model rule as WHISPER_MODEL.
TITANET_MODEL = os.environ.get(
    'CFG_TITANET_MODEL', 'nvidia/speakerverification_en_titanet_large',
)

# 'cpu' (default) keeps the voiceprint model off the card; 'auto' puts it on the GPU.
#
# CPU is the intended production setting, and unlike INSTRUCT_DEVICE it is not even a latency
# trade: MEASURED on fry over 100 files, TitaNet embeds a 4s turn in 0.011s on the CPU against
# 0.013s on the card. The model is small enough that host-to-device transfer dominates, so the
# GPU buys nothing and costs a kind, a VRAM budget and a slot in the batch rotation. It is the
# same weights either way.
TITANET_DEVICE = os.environ.get('CFG_TITANET_DEVICE', 'cpu')

# Active speaker detection: whether a visible face is the one making the sound.
#
# Not a Hugging Face model — LR-ASD is vendored into the image as a source tree with its
# weights committed alongside (see src/Containerfile), so this is a directory rather than a
# model id. `finetuning_TalkSet.model` over `pretrain_AVA.model`: AVA is broadcast footage,
# TalkSet is the fine-tune the authors ship for video in the wild, which is what home
# recordings are.
LR_ASD_HOME = os.environ.get('LR_ASD_HOME', '/opt/lr-asd')
LR_ASD_WEIGHTS = os.environ.get('CFG_LR_ASD_WEIGHTS', 'finetuning_TalkSet.model')

# 'auto' (default) puts it on the GPU when there is one, 'cpu' forces it off the card.
#
# Unlike TITANET_DEVICE this defaults to the card, because the measurement went the other
# way: 0.006s per 3s track on the GPU against 0.237s on the CPU, a 40x gap. The model is
# small (0.8M params, 144MB measured) but it is run once per face track per turn, so those
# multiply in a way a voiceprint's one-per-turn does not.
LR_ASD_DEVICE = os.environ.get('CFG_LR_ASD_DEVICE', 'auto')

# Instruct model used to turn a natural-language search query into filters. Same rule as
# WHISPER_MODEL: this MUST match the model baked into the image (src/Containerfile ARG
# INSTRUCT_MODEL) or the first query downloads several GB at runtime, inside an interactive
# request, with no network guarantee.
#
# 1.5B is chosen for what the job actually is: extracting a handful of fields into a fixed
# JSON schema with few-shot examples, not open-ended reasoning. Qwen2.5 is multilingual, which
# matters because the library and its queries are partly Swedish.
INSTRUCT_MODEL = os.environ.get('CFG_INSTRUCT_MODEL', 'Qwen/Qwen2.5-1.5B-Instruct')

# 'auto' (default) puts it on the GPU when there is one, 'cpu' forces it off the card.
#
# 'cpu' is the intended production setting, not a fallback. With one model on the card at a
# time, a GPU-resident instruct model would have to wait for a caption batch to release the
# card — minutes — and no batch quantum is both long enough to amortize a ~36s model load and
# short enough for a search box. On the CPU a greedy decode of well under a hundred tokens is
# a few seconds, never competes with the crawl, and gives search a constant latency.
#
# It is the SAME weights, only slower, which is what makes this acceptable where a smaller
# model would not be. See work/plans/replace-florence-captioner.md.
INSTRUCT_DEVICE = os.environ.get('CFG_INSTRUCT_DEVICE', 'auto')

# Ceiling for InsightFace's onnxruntime CUDA arena, sized by measurement rather than guess.
# 3072 was too small: the arena exhausted mid-inference after ~250 images and failed 121 of
# 400 calls with "Available memory of 5513472 is smaller than requested bytes of 20751872".
# Group photos are the driver — det_size is fixed but the recognition batch scales with the
# number of faces. 6144 leaves room on a 16GB card for MiniLM and the CUDA contexts; the VLM
# and whisper no longer have to fit beside it, since faces and captions are now separate
# batches. See insightface() for why a ceiling is needed at all.
INSIGHTFACE_GPU_MEM_LIMIT_BYTES = int(
    os.environ.get('CFG_INSIGHTFACE_GPU_MEM_MB', '6144')
) * 1024 * 1024

# Free-VRAM floor below which InsightFace's sessions are recreated to release their arenas
# (see reset_insightface_if_low). Kept above VLM_MIN_FREE_MB so that between the two lines the
# answer is to release onnxruntime's arenas and keep going, and only below the lower line is a
# caption deferred — the arena is what fills the card during a crawl, so the reset is usually
# the whole fix.
INSIGHTFACE_RESET_FREE_MB = float(os.environ.get('CFG_INSIGHTFACE_RESET_FREE_MB', '2500'))

# Vision-language model behind image and video captioning, replacing Florence-2-large.
#
# MUST match the model baked into the image (src/Containerfile ARG VLM_MODEL) or the first
# caption downloads ~17GB at runtime, inside a pipeline step, with no network guarantee — the
# same rule as WHISPER_MODEL and INSTRUCT_MODEL.
#
# Chosen by measured bake-off on fry 2026-09-04 over 45 real archive images
# (work/plans/replace-florence-captioner.md). Against Florence-2-large this model takes an
# instruction, which is the whole point: Florence had fixed task tokens and could not be told
# "describe an expression only when it is unmistakable", so it asserted one on every face.
VLM_MODEL = os.environ.get('CFG_VLM_MODEL', 'Qwen/Qwen3-VL-8B-Instruct')

# How the weights are quantized. 'nf4' (default) is 6.43GB measured and was what the bake-off
# ran; 'int8' is ~9-10GB and a milder quantization, reachable only because one model owns the
# card at a time; 'bf16' is ~17.6GB and does not fit at all at 8B.
#
# NOT 'fp8', and not by omission: the official FP8 checkpoints cannot be loaded by transformers
# at all. Measured on fry, transformers 5.16.1 dies in quantizer_finegrained_fp8.update_tp_plan
# with "'NoneType' object has no attribute 'get'". The model card says vLLM or SGLang, and vLLM
# preallocates a fixed VRAM slice that would fight InsightFace's arena.
VLM_QUANT = os.environ.get('CFG_VLM_QUANT', 'nf4')

# Free VRAM the VLM needs before a caption is allowed onto the card.
#
# This is a *generate's* working set, not a load's: with one model resident at a time, the batch
# scheduler decides when the VLM is on the card and it is the only large tenant while it is
# there. Measured peak at the production token budget was 7.03GB against 6.43GB of weights, so
# the floor covers activations plus slack, and stays below INSIGHTFACE_RESET_FREE_MB (2500) so
# that between the two lines the right answer is still to release onnxruntime's arenas rather
# than to give up on the card.
VLM_MIN_FREE_MB = float(os.environ.get('CFG_VLM_MIN_FREE_MB', '1200'))

# How long the card stays marked busy before it is preflighted again.
#
# The backoff exists so pressure costs one decision rather than a failed load per image:
# `torch.cuda.is_available()` reports that a device exists, not that anything is free on it.
# What changed with the VLM is what happens at the end of it — see vlm_device(): captioning is
# deferred back to the pipeline queue, never quietly moved somewhere that produces worse text.
VLM_BACKOFF_SECONDS = float(os.environ.get('CFG_VLM_BACKOFF', '300'))


class CardBusy(RuntimeError):
    """The GPU cannot currently hold the VLM, so the caller should retry later.

    Raised instead of captioning on the CPU. That is a deliberate reversal of what Florence did:
    a 0.77B model on the CPU was "5x slower but the same answer", whereas this model in fp32 on
    the CPU needs roughly 35GB against a 10GB container and would take the model server down.
    Deferring costs a pipeline retry; degrading would cost a permanently worse caption in the
    search index, which the project's accuracy-over-time rule forbids.
    """


def _family_of(key: str) -> str:
    """Cache keys are `family` or `family:device[:compute]` — take the family."""
    return key.split(':', 1)[0]


_resident = False
_cache: dict[str, object] = {}
_last_used: dict[str, float] = {}
_lock = threading.Lock()

class FamilyLock:
    """A per-family lock with stable ordering inside small explicit priorities."""

    def __init__(self):
        self._condition = threading.Condition()
        self._held = False
        self._ticket = 0
        self._waiters: list[tuple[int, int, object]] = []

    def acquire(self, priority: int = 0) -> None:
        token = object()
        with self._condition:
            ticket = self._ticket
            self._ticket += 1
            heapq.heappush(self._waiters, (-priority, ticket, token))
            while self._held or self._waiters[0][2] is not token:
                self._condition.wait()
            heapq.heappop(self._waiters)
            self._held = True

    def release(self) -> None:
        with self._condition:
            if not self._held:
                raise RuntimeError("release unlocked FamilyLock")
            self._held = False
            self._condition.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.release()

    @contextmanager
    def hold(self, priority: int = 0):
        self.acquire(priority)
        try:
            yield self
        finally:
            self.release()


# One inference lock per family. GPU work serialises anyway and the card cannot hold parallel
# VLM generates, so concurrent clients queue rather than thrash VRAM. Interactive work may
# choose who acquires next, but never interrupts a generate already running.
_family_locks: dict[str, FamilyLock] = {}

# One *construction* lock across all families. Model loading is not thread-safe, whatever the
# family: `transformers.from_pretrained` sets the process-global `torch.set_default_dtype` to
# its `torch_dtype` for the whole of model construction and restores it at the end
# (modeling_utils.py, `_set_default_torch_dtype` -> `set_default_dtype(dtype_orig)`), with no
# lock of its own. Two families loading at once therefore corrupt each other's default dtype:
# an instruct or minilm load finishing mid-construction of another family restores the default
# to float32, and every submodule built after that instant is float32 while the caption path
# feeds it half precision -- `Input type (c10::Half) and bias type (float) should be the same`,
# intermittent, surviving in the cache until that copy is evicted. First diagnosed on
# Florence-2; the mechanism is transformers', not that model's, so it outlived it.
#
# The window is real because `_get` loads outside `_lock` and each family has its own thread,
# so a search query can land inside a VLM load. Serialising loads costs a queued moment on a
# cold family and nothing at all on a cache hit; concurrent loads contend on VRAM
# and PCIe anyway. Distinct from `_lock`: `health` and cache lookups never wait on a load.
_load_lock = threading.Lock()

# libc handle for malloc_trim (glibc only). False once we know it is unavailable.
_libc: object = None

# One long-lived inference thread per family. See run_on_model_thread.
_family_executors: dict[str, object] = {}

# Pseudo-family for reclaim work, which belongs to no model but still calls into CUDA.
_MAINTENANCE = "maintenance"

# The caption path's memory of having been pushed onto the CPU. Guarded by `_lock`.
#
# `_vlm_busy_until` is monotonic (a clock change must not extend or cancel a backoff) while
# `_vlm_busy_since` is wall-clock, because it is reported to a human reading `health` hours
# later. `_vlm_busy_events` counts fallbacks rather than captions: two in an hour is a card
# under pressure, forty is a card that never recovers.
_vlm_busy_since: float | None = None
_vlm_busy_until: float = 0.0
_vlm_busy_reason: str = ""
_vlm_busy_events: int = 0


def set_resident(value: bool) -> None:
    """Enable model caching (server mode) or per-call freeing (one-shot mode)."""
    global _resident
    _resident = value


def is_resident() -> bool:
    return _resident


def family_lock(family: str) -> FamilyLock:
    with _lock:
        return _family_locks.setdefault(family, FamilyLock())


def run_on_model_thread(family: str, fn, *args, **kwargs):
    """Run `fn` on that family's one long-lived thread. Resident mode only.

    **No model may ever run on a request thread.** The model server is a
    ThreadingUnixStreamServer, so it hands every request its own OS thread, and both CUDA
    stacks under us attach per-thread state to whatever thread touches them and never release
    it when that thread exits. `malloc_trim` cannot touch any of it: the bytes are still
    *allocated* (glibc `uordblks`), not freed-and-retained (`fordblks`). That distinction is
    the first thing to measure next time.

    Measured on fry, glibc in-use per request, same work either way:

        detect_faces (onnxruntime CUDA)   same thread  +0.00 MB   new thread  +2.20 MB
        caption      (Florence, torch)    same thread  +0.00 MB   new thread  +29.86 MB
        embed_text   (MiniLM, torch)      same thread  +0.00 MB   new thread   +0.00 MB

    onnxruntime's CUDA provider builds a PerThreadContext (cuBLAS/cuDNN handles, a stream,
    host staging buffers) per thread. The torch side is not the same mechanism and is much
    larger: a bare matmul (+0.01 MB) and a bare cuDNN conv (+0.05 MB) barely register, and a
    plain transformer forward like MiniLM not at all — it is the beam-search `generate` path
    that carries it. `generate` is therefore assumed to leak for *every* model, which is why
    this is applied per family rather than only where it has been measured.

    At the crawl's rate 29.86 MB/caption is ~24 GB/h, which is the whole of the ~18 GB the
    server reached in ~72 minutes before the cgroup OOM killer took it on 2026-08-11.

    Model *loading* has to happen on this thread too, not just inference — the thread that
    builds a session or moves weights onto the card acquires the per-thread state exactly like
    the one that runs it.

    One executor per family rather than one shared: `family_lock` already serialises within a
    family, so a per-family thread costs nothing and never queues behind more than the single
    call that lock admits — while a shared thread would put a face request behind a minutes-long
    caption.

    Only the resident server needs this. `inference_call.py` does one call and exits, so it
    stays on the calling thread rather than paying for a thread it would immediately shut down.
    """
    if not _resident:
        return fn(*args, **kwargs)

    with _lock:
        executor = _family_executors.get(family)
        if executor is None:
            from concurrent.futures import ThreadPoolExecutor
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"model-{family}")
            _family_executors[family] = executor

    return executor.submit(fn, *args, **kwargs).result()


def select_device() -> str:
    """'cuda' when torch sees a usable GPU, else 'cpu'."""
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _malloc_trim() -> None:
    """Hand each glibc arena's freed high-water memory back to the OS. No-op off glibc.

    The heavy inference buffers are native (numpy/torch/onnxruntime), so gc frees almost
    nothing; glibc keeps the freed bytes at each arena's high-water mark and only returns
    them on an explicit trim. Without this, the resident server's RSS only ever grows — up
    to the largest transient working set it ever saw — until it trips the container memory cap
    and OOMs.
    """
    global _libc
    if _libc is None:
        import ctypes
        try:
            _libc = ctypes.CDLL("libc.so.6")
        except OSError:
            _libc = False
    if _libc:
        try:
            _libc.malloc_trim(0)
        except Exception:
            pass


def _empty_cuda_cache() -> None:
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def cleanup_torch() -> None:
    """Reclaim memory after model use: gc, free cached VRAM, and trim glibc arenas.

    Only touches torch if it is already imported, so a faces-only (onnxruntime) server
    never pays to load torch just to reclaim.

    The CUDA part goes on a thread of its own. This runs per request, from the request
    thread, and `empty_cache` is a CUDA call like any other — reclaiming on the throwaway
    thread would attach to it exactly the per-thread state run_on_model_thread exists to
    avoid. gc and the trim are thread-agnostic and stay on the caller.
    """
    gc.collect()
    if sys.modules.get("torch") is not None:
        # A cleanup that came *from* the maintenance thread would submit to the single worker
        # it is already running on and wait on itself, so run it inline there.
        if threading.current_thread().name.startswith(f"model-{_MAINTENANCE}"):
            _empty_cuda_cache()
        else:
            run_on_model_thread(_MAINTENANCE, _empty_cuda_cache)
    _malloc_trim()


def _peek(key: str):
    """Cached model for `key`, touching its idle timer, or None."""
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _last_used[key] = time.time()
        return cached


def _get(key: str, loader):
    """Return a cached model, loading it via `loader()` on miss."""
    cached = _peek(key)
    if cached is not None:
        return cached

    # Load outside the registry lock: loading the VLM takes ~36s from cache (measured) and
    # must not block
    # `health` or another family's lookup. Under `_load_lock` though — model construction
    # across families is not thread-safe, see there. Re-check the cache once it is held: a
    # load we queued behind may have been for this very key, and loading it a second time
    # would build a whole second copy just to throw it away below.
    with _load_lock:
        cached = _peek(key)
        if cached is not None:
            return cached
        model = loader()

        with _lock:
            if _resident:
                _cache[key] = model
                _last_used[key] = time.time()
    return model


def release(key: str) -> None:
    """Drop a family from the cache and reclaim memory; no-op while resident.

    Callers must `del` their own local references to the model *before* calling this,
    in the scope that holds them — deleting them here would only drop this function's
    references while the caller's frame still pins the objects, and the collection
    below would free nothing.
    """
    if _resident:
        return
    with _lock:
        _cache.pop(key, None)
        _last_used.pop(key, None)
    cleanup_torch()


def evict(key: str) -> None:
    """Drop one family from the cache regardless of mode (used on CUDA OOM retry)."""
    with _lock:
        _cache.pop(key, None)
        _last_used.pop(key, None)
    cleanup_torch()


def evict_idle(max_idle: float = IDLE_EVICT_SECONDS) -> list[str]:
    """Unload resident families untouched for `max_idle` seconds. Returns their keys.

    Families in PINNED_FAMILIES are never evicted — see the note there.
    """
    if not _resident:
        return []
    now = time.time()
    with _lock:
        stale = [
            k for k, seen in _last_used.items()
            if now - seen > max_idle and _family_of(k) not in PINNED_FAMILIES
        ]
        for key in stale:
            _cache.pop(key, None)
            _last_used.pop(key, None)
    if stale:
        cleanup_torch()
    return stale


def loaded() -> list[str]:
    with _lock:
        return sorted(_cache)


# --- Model families -------------------------------------------------------------

def vlm(device: str):
    """VLM_MODEL vision-language captioner. Returns (processor, model).

    No `trust_remote_code`: this is a stock transformers architecture, unlike Florence-2 whose
    remote code is also why `transformers` was pinned to 4.49.0 for so long — it breaks from 4.50
    onward (huggingface/transformers#36886, #41622) and cannot coexist with a model that needs a
    modern release.
    """
    key = f"vlm:{device}"

    def load():
        from transformers import AutoProcessor, AutoModelForImageTextToText
        import torch

        kwargs: dict = {}
        if device == "cuda" and VLM_QUANT in ("nf4", "int8"):
            from transformers import BitsAndBytesConfig

            if VLM_QUANT == "nf4":
                kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True,
                )
            else:
                kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
            kwargs["device_map"] = device
        else:
            # bf16 on the card, fp32 on a CPU-only host. No uniform `.to(dtype=...)` cast here,
            # unlike the Florence loader this replaces: a quantized checkpoint must not be swept
            # to one dtype, since that would dequantize it back to full precision and silently
            # undo the reason it fits.
            kwargs["dtype"] = torch.bfloat16 if device == "cuda" else torch.float32
            kwargs["device_map"] = device

        processor = AutoProcessor.from_pretrained(VLM_MODEL)
        model = AutoModelForImageTextToText.from_pretrained(VLM_MODEL, **kwargs).eval()
        return processor, model

    return _get(key, load)


def evict_insightface() -> None:
    """Drop the InsightFace session so the next call re-creates it (CUDA OOM retry)."""
    evict("insightface")
    evict("insightface:cpu")


def free_vram_mb() -> float | None:
    """Free VRAM on the device, or None if torch is not loaded or there is no GPU.

    Device-level rather than torch-level: the whole point is to see memory held by
    onnxruntime and CTranslate2, which torch's own accounting cannot see.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return None
    try:
        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return free / 1048576
    except Exception:  # noqa: BLE001 - diagnostics must never break the caller
        return None


def reset_insightface_if_low() -> float | None:
    """Recreate InsightFace's sessions when the device is short on VRAM.

    onnxruntime's CUDA arena grows and never returns memory, and `gpu_mem_limit` cannot
    bound it usefully here: the limit is per-session and buffalo_l loads five of them, so a
    cap large enough for each is five times too large in aggregate, while a cap small enough
    in aggregate starves one session mid-inference ("Available memory of 5513472 is smaller
    than requested bytes of 20751872", 121 of 400 calls). Measured growth is ~42MB per image
    with torch flat, reaching 15.8GB of a 16.3GB card in 300 images — at which point CUDA
    OOM evicts Florence *and* drops InsightFace onto the CPU provider, and indexing falls to
    roughly a fifth of its rate with the GPU idle.

    Dropping the sessions frees every arena at once; the next call rebuilds them in ~1-2s,
    which amortises to nothing over the hundreds of images between resets. Returns the free
    VRAM that triggered a reset, or None when nothing was done.
    """
    free_mb = free_vram_mb()
    if free_mb is None or free_mb >= INSIGHTFACE_RESET_FREE_MB:
        return None
    # Hold the family lock so no request is mid-inference on a session being dropped.
    with family_lock("insightface"):
        if "insightface" not in loaded() and "insightface:cpu" not in loaded():
            return None
        evict_insightface()
    cleanup_torch()
    return free_mb


def note_card_busy(reason: str) -> None:
    """Record that the card cannot hold the VLM, and hold that view for the backoff.

    Called both by the preflight below, which decides it, and by the OOM handler in
    `inference_ops`, which discovers it the hard way.
    """
    global _vlm_busy_since, _vlm_busy_until, _vlm_busy_reason, _vlm_busy_events
    with _lock:
        if _vlm_busy_since is None:
            _vlm_busy_since = time.time()
        _vlm_busy_until = time.monotonic() + VLM_BACKOFF_SECONDS
        _vlm_busy_reason = reason
        _vlm_busy_events += 1


def clear_card_busy() -> float | None:
    """Forget the busy state. Returns how many seconds it lasted, or None if there was none."""
    global _vlm_busy_since, _vlm_busy_until, _vlm_busy_reason
    with _lock:
        if _vlm_busy_since is None:
            return None
        lasted = time.time() - _vlm_busy_since
        _vlm_busy_since = None
        _vlm_busy_until = 0.0
        _vlm_busy_reason = ""
    return lasted


def card_busy() -> dict | None:
    """The current busy state as reportable fields, or None while captions are running.

    This is what `health` carries and what the model server's watchdog samples — the state has
    to be readable without touching CUDA, so it is plain bookkeeping and never a device probe.
    """
    with _lock:
        if _vlm_busy_since is None:
            return None
        return {
            "since": _vlm_busy_since,
            "seconds": round(time.time() - _vlm_busy_since, 1),
            "retry_in": round(max(0.0, _vlm_busy_until - time.monotonic()), 1),
            "reason": _vlm_busy_reason,
            "events": _vlm_busy_events,
        }


def vlm_device() -> str:
    """Device for the next caption: 'cuda', or 'cpu' only on a host that has no GPU at all.

    `select_device()` is not enough on this path. `torch.cuda.is_available()` reports that a
    device *exists*, not that anything is free on it, so it kept answering 'cuda' while VRAM was
    full — and the OOM handler evicts the resident copy before retrying, so the next caption
    reloaded from cold, OOMed and evicted again. A failed load per image, for as long as the
    pressure lasted.

    A **preflight** turns that exception path into a decision: measure free VRAM before choosing,
    and trigger InsightFace's arena reset from the caption path rather than waiting up to
    EVICT_INTERVAL_SECONDS for the maintenance loop to notice. That reset is usually the whole
    answer — onnxruntime's arenas are what fill the card during a crawl — so it is tried first.

    **What changed from Florence: there is no CPU fallback.** Florence on the CPU was the same
    answer 5x slower, so degrading was free in quality terms. This model is not: fp32 on the CPU
    is ~35GB against a 10GB container, and it would take the model server down rather than run
    slowly. Under pressure this raises `CardBusy` and the pipeline retries later, which costs a
    deferral instead of a permanently worse caption in the search index.

    A CPU-only host — a dev laptop, CI, a one-shot `inference_call.py` — still gets 'cpu'. That
    is not a degradation: it is the same weights, and nothing else can run there anyway.

    Runs on the VLM's own model thread, inside its family lock — `select_device` is the first
    torch CUDA call and must not attach per-thread state to a request thread. Taking the
    InsightFace family lock from under the VLM one is safe: nothing on the face path ever takes
    the VLM lock, so there is no cycle, and the wait is one in-flight detection.
    """
    if select_device() != "cuda":
        return "cpu"

    with _lock:
        backing_off = _vlm_busy_until and time.monotonic() < _vlm_busy_until
    if backing_off:
        raise CardBusy("card marked busy; retrying after the backoff")

    free_mb = free_vram_mb()
    # None means torch is not imported yet, so nothing of ours is on the card and there is
    # nothing to preflight against. Proceed, and let the OOM handler be the backstop it is.
    if free_mb is not None:
        if free_mb < INSIGHTFACE_RESET_FREE_MB and reset_insightface_if_low() is not None:
            after = free_vram_mb()
            if after is not None:
                free_mb = after

        if free_mb < VLM_MIN_FREE_MB:
            note_card_busy(
                f"preflight: {free_mb:.0f}MB free, below the {VLM_MIN_FREE_MB:.0f}MB the VLM "
                f"needs on the card"
            )
            raise CardBusy(
                f"only {free_mb:.0f}MB free, below the {VLM_MIN_FREE_MB:.0f}MB floor"
            )

    clear_card_busy()
    return "cuda"


def insightface(force_cpu: bool = False):
    """InsightFace buffalo_l analyser (512-dim embeddings).

    `force_cpu` pins it to the CPU provider — used after a CUDA OOM, since the
    4GB card cannot hold this alongside Florence-2.
    """
    key = "insightface:cpu" if force_cpu else "insightface"

    def load():
        import sys
        import onnxruntime
        from insightface.app import FaceAnalysis

        # Prefer the GPU execution provider when onnxruntime-gpu finds CUDA, always keep
        # CPU as fallback. ctx_id selects the GPU device (>= 0) or CPU (< 0).
        use_cuda = not force_cpu and 'CUDAExecutionProvider' in onnxruntime.get_available_providers()
        # Bound the CUDA arena. onnxruntime defaults to kNextPowerOfTwo with no limit and
        # never returns the memory, so sustained face detection walks the card: a faces-only
        # probe on fry went 3,149MB -> 15,821MB over 300 images (~42MB each) with torch flat
        # at 1,972MB throughout. At the top it OOMs, which evicts Florence *and* reloads
        # InsightFace on the CPU provider — one arena taking down both models.
        # kSameAsRequested stops the doubling; the cap is a backstop well above the ~1-2GB
        # this model actually needs.
        cuda_opts = {
            'arena_extend_strategy': 'kSameAsRequested',
            'gpu_mem_limit': str(INSIGHTFACE_GPU_MEM_LIMIT_BYTES),
        }
        providers = ([('CUDAExecutionProvider', cuda_opts)] if use_cuda else []) + ['CPUExecutionProvider']
        ctx_id = 0 if use_cuda else -1
        root = os.environ.get('INSIGHTFACE_HOME', os.path.expanduser('~/.insightface'))

        # FaceAnalysis prints "Applied providers..." to stdout, which would corrupt the
        # JSON on the one-shot path.
        original_stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            app = FaceAnalysis(name='buffalo_l', root=root, providers=providers)
            app.prepare(ctx_id=ctx_id, det_size=(640, 640))
        finally:
            sys.stdout.close()
            sys.stdout = original_stdout
        return app

    return _get(key, load)


def sentence_transformer(device: str):
    """paraphrase-multilingual-MiniLM-L12-v2 text embedder (384-dim)."""
    key = f"minilm:{device}"

    def load():
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2", device=device)

    return _get(key, load)


def instruct_device() -> str:
    """Device for the instruct family, honouring INSTRUCT_DEVICE. See the note there."""
    return 'cpu' if INSTRUCT_DEVICE == 'cpu' else select_device()


def instruct(device: str):
    """INSTRUCT_MODEL for text generation. Returns (tokenizer, model)."""
    key = f"instruct:{device}"

    def load():
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch

        dtype = torch.float16 if device == "cuda" else torch.float32
        tokenizer = AutoTokenizer.from_pretrained(INSTRUCT_MODEL)
        model = AutoModelForCausalLM.from_pretrained(INSTRUCT_MODEL, torch_dtype=dtype).to(device)
        model.eval()
        return tokenizer, model

    return _get(key, load)


def whisper(device: str, compute_type: str):
    """faster-whisper model named by WHISPER_MODEL."""
    key = f"whisper:{device}:{compute_type}"

    def load():
        from faster_whisper import WhisperModel
        return WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)

    return _get(key, load)


def sortformer(device: str):
    """Sortformer speaker diarizer named by SORTFORMER_MODEL.

    Same baked-model rule as WHISPER_MODEL: this MUST match src/Containerfile or the first
    diarization pulls the checkpoint at runtime, inside a pipeline step, with no network
    guarantee. End-to-end, and it models overlapping speech rather than assuming one voice
    at a time — which is why the transcript's own whisper segmentation is not reused as a
    speaker boundary.
    """
    key = f"sortformer:{device}"

    def load():
        from nemo.collections.asr.models import SortformerEncLabelModel

        model = SortformerEncLabelModel.from_pretrained(SORTFORMER_MODEL)
        model.eval()
        return model.to(device)

    return _get(key, load)


def titanet(device: str):
    """TitaNet speaker-verification model named by TITANET_MODEL. 192-dim embeddings.

    Measured dim is 192 over 100 real files; the `voice` embedding node's `capacity` is
    written from what the model returns rather than from this comment, so a model change
    cannot silently mismatch the pgvector column.
    """
    key = f"titanet:{device}"

    def load():
        from nemo.collections.asr.models import EncDecSpeakerLabelModel

        model = EncDecSpeakerLabelModel.from_pretrained(TITANET_MODEL)
        model.eval()
        return model.to(device)

    return _get(key, load)


def titanet_device() -> str:
    """Device for the voiceprint family, honouring TITANET_DEVICE. See the note there."""
    return 'cpu' if TITANET_DEVICE == 'cpu' else select_device()


def lr_asd_device() -> str:
    """Device for the active-speaker family, honouring LR_ASD_DEVICE. See the note there."""
    return 'cpu' if LR_ASD_DEVICE == 'cpu' else select_device()


def lr_asd(device: str):
    """LR-ASD active-speaker model and its scoring head. Returns (model, head).

    The head comes back separately because the checkpoint is a *training* state dict: it holds
    the backbone under `model.` and the two-class classifier under `lossAV.FC`, and the
    repo's own `ASD` wrapper that owns both also pulls in an optimizer, a scheduler and pandas
    on import. Loading the two pieces directly keeps inference free of all of that, and the
    score is then `softmax(head(outsAV))[:, 1]` — the same quantity `ASD.evaluate_network`
    reads, without the training machinery around it.
    """
    key = f"asd:{device}"

    def load():
        import sys
        import torch

        if LR_ASD_HOME not in sys.path:
            sys.path.insert(0, LR_ASD_HOME)
        from model.Model import ASD_Model

        weights = os.path.join(LR_ASD_HOME, 'weight', LR_ASD_WEIGHTS)
        state = torch.load(weights, map_location='cpu')

        model = ASD_Model()
        backbone = {k[len('model.'):]: v for k, v in state.items() if k.startswith('model.')}
        # strict=False: the checkpoint carries the loss heads too, which are not this module's.
        model.load_state_dict(backbone or state, strict=False)

        head = torch.nn.Linear(128, 2)
        head.load_state_dict({'weight': state['lossAV.FC.weight'], 'bias': state['lossAV.FC.bias']})

        return model.eval().to(device), head.eval().to(device)

    return _get(key, load)

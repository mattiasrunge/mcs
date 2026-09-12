#!/usr/bin/env python3
"""
Checks for the caption device decision path, with the CUDA side stubbed out.

Run by hand or by pytest (`tests/test_caption_device.py` wraps it) — the worker's own code is
otherwise only exercised end-to-end inside the container, which needs a GPU and real models.

Nothing here imports torch, transformers or onnxruntime. `model_registry` keeps every heavy import
inside its getters precisely so the socket-client path never pays for them, and that is what makes
this runnable on a laptop: the whole point of `vlm_device` is a decision made *before* any of
that is touched, so stubbing `free_vram_mb` is enough to drive every branch of it.

What these pin is the decision table, which is the part with no other coverage. The wiring — that
`vlm_device` runs on the VLM's own thread, under two family locks, without deadlocking — is
verified by a real caption on fry.

THE DECISION CHANGED WITH THE MODEL. Florence-2 fell back to the CPU under pressure: the same
answer roughly 5x slower, so degrading was free in quality terms. The VLM does not — in fp32 on
the CPU it is ~35GB against a 10GB container, and the project's accuracy-over-time rule forbids
answering with worse text anyway. So the "card is full" branch now raises `CardBusy` and the
pipeline retries later, and these checks pin that it raises rather than quietly returning "cpu".
"""

import os
import sys
import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mcs', 'modelworker'))

import model_registry as r  # noqa: E402
import model_server as ms  # noqa: E402
import inference_ops as ops  # noqa: E402

_failures: list[str] = []


def raises_card_busy(what) -> None:
    """Assert the decision defers instead of answering with something worse."""
    try:
        device = r.vlm_device()
    except r.CardBusy:
        print(f"  ok   {what}: raised CardBusy")
        return
    _failures.append(f"{what}: returned {device!r} instead of raising CardBusy")
    print(f"  FAIL {what}: returned {device!r} instead of raising CardBusy")


def eq(what, got, want) -> None:
    if got != want:
        _failures.append(f"{what}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if got == want else 'FAIL'} {what}: {got!r}")


def check_vlm_device() -> None:
    """The preflight and the backoff, branch by branch."""
    free = [None]
    resets: list = []
    evicted: list = []

    r.select_device = lambda: "cuda"
    r.free_vram_mb = lambda: free[0]
    r.evict = lambda key: evicted.append(key)

    def reset_releasing_arenas():
        resets.append(free[0])
        if free[0] is not None and free[0] < r.INSIGHTFACE_RESET_FREE_MB:
            free[0] = 9000.0
            return 1.0
        return None

    r.reset_insightface_if_low = reset_releasing_arenas

    def fresh():
        r._vlm_busy_since = None
        r._vlm_busy_until = 0.0
        r._vlm_busy_events = 0
        resets.clear()
        evicted.clear()

    print("torch not imported -> nothing of ours is on the card, so proceed to cuda")
    fresh()
    free[0] = None
    eq("device", r.vlm_device(), "cuda")
    eq("no fallback recorded", r.card_busy(), None)

    print("plenty free -> cuda, and the arena reset is not disturbed")
    fresh()
    free[0] = 8000.0
    eq("device", r.vlm_device(), "cuda")
    eq("resets attempted", resets, [])

    print("between the two lines -> release the arenas and stay on the GPU")
    fresh()
    free[0] = 2100.0
    eq("device", r.vlm_device(), "cuda")
    eq("reset ran once", len(resets), 1)
    eq("no fallback recorded", r.card_busy(), None)

    print("card full with nothing to release -> defer, and the busy state is remembered")
    fresh()
    free[0] = 400.0
    r.reset_insightface_if_low = lambda: None
    raises_card_busy("device")
    state = r.card_busy()
    eq("events", state["events"], 1)
    eq("reason names the preflight", state["reason"].startswith("preflight:"), True)
    eq("retry is inside the backoff", 0 < state["retry_in"] <= r.VLM_BACKOFF_SECONDS, True)

    print("inside the backoff -> defer without so much as reading free VRAM")
    probes: list = []
    r.free_vram_mb = lambda: (probes.append(1), 9000.0)[1]
    raises_card_busy("device")
    eq("vram probes", probes, [])
    eq("events unchanged", r.card_busy()["events"], 1)

    print("backoff expired, card recovered -> cuda and the busy state clears")
    r._vlm_busy_until = 0.0
    eq("device", r.vlm_device(), "cuda")
    eq("busy state cleared", r.card_busy(), None)
    # Nothing is evicted on recovery any more: there is no CPU copy to drop, because captioning
    # never moved to the CPU in the first place.
    eq("evicted", evicted, [])

    print("backoff expired, card still full -> defer again, backoff renewed, nothing evicted")
    fresh()
    r.free_vram_mb = lambda: free[0]
    free[0] = 400.0
    try:
        r.vlm_device()
    except r.CardBusy:
        pass
    r._vlm_busy_until = 0.0
    raises_card_busy("device")
    eq("events", r.card_busy()["events"], 2)
    eq("evicted", evicted, [])

    print("a host with no GPU still gets cpu — same weights, only slower, so not a degradation")
    fresh()
    r.select_device = lambda: "cpu"
    eq("device", r.vlm_device(), "cpu")
    r.select_device = lambda: "cuda"

    print("note/clear round trip reports how long the busy state lasted")
    fresh()
    r.note_card_busy("cuda oom: out of memory")
    eq("seconds is non-negative", r.card_busy()["seconds"] >= 0, True)
    eq("clear returns a duration", r.clear_card_busy() is not None, True)
    eq("clear on a clean state returns None", r.clear_card_busy(), None)


def check_card_busy_watchdog() -> None:
    """When the watchdog speaks, how often it repeats, and what the line carries."""
    lines: list[str] = []
    ms.log = lambda message: lines.append(message)
    ms.RSS_CHECK_INTERVAL_SECONDS = 0.01
    ms.VLM_BUSY_ALERT_SECONDS = 100
    # Shorter than the tick, so a repeat is due on every pass rather than after 15 minutes.
    ms.VLM_BUSY_REPEAT_SECONDS = 0.001
    r.free_vram_mb = lambda: 180.0
    r.loaded = lambda: ["vlm:cuda", "minilm:cuda"]

    oom = "cuda oom: out of memory"
    script = [
        None,                                                     # healthy
        {"seconds": 30, "retry_in": 270, "events": 1, "reason": oom},   # too new to blame
        {"seconds": 200, "retry_in": 100, "events": 1, "reason": oom},  # alert
        {"seconds": 230, "retry_in": 70, "events": 1, "reason": oom},   # repeat
        None,                                                     # recovered
    ]
    stop = threading.Event()
    ticks = iter(script)

    def next_state():
        # Captions keep landing while the busy state holds, so the rate in the line is real.
        ms._op_counts["caption"] = ms._op_counts.get("caption", 0) + 5
        ms._caption_items += 5
        try:
            return next(ticks)
        except StopIteration:
            stop.set()
            return None

    r.card_busy = next_state
    ms._op_counts["caption"] = 7
    ms._caption_items = 7

    ms._card_busy_watchdog(stop)

    alerts = [line for line in lines if line.startswith("CAPTIONS DEFERRED")]
    cleared = [line for line in lines if line.startswith("card-busy cleared")]

    for line in lines:
        print(f"    {line}")

    eq(
        "only the states past the alert threshold spoke",
        [alert.split("CAPTIONS DEFERRED: ")[1].split(" and ")[0] for alert in alerts],
        ["200s", "230s"],
    )
    eq("recovery logged once", len(cleared), 1)
    eq("carries free vram", "free vram 180MB" in alerts[0], True)
    eq("carries what is loaded", "loaded vlm:cuda, minilm:cuda" in alerts[0], True)
    eq("carries the cause", oom in alerts[0], True)
    eq("carries the rate since the deferrals began", "5 caption(s) at" in alerts[0], True)


def check_caption_batch_oom_split() -> None:
    """A batch OOM retries the unchanged requests singly, never at lower quality."""
    class BatchOom(RuntimeError):
        pass

    saved = {
        "vlm_device": r.vlm_device,
        "family_lock": r.family_lock,
        "run_on_model_thread": r.run_on_model_thread,
        "cleanup_torch": r.cleanup_torch,
        "caption": ops._caption_independent_images,
        "is_oom": ops._is_cuda_oom,
    }
    calls: list[tuple[list[str], int, str]] = []

    try:
        r.vlm_device = lambda: "cuda"
        r.family_lock = lambda _family: nullcontext()
        r.run_on_model_thread = lambda _family, fn: fn()
        r.cleanup_torch = lambda: None
        ops._is_cuda_oom = lambda exc: isinstance(exc, BatchOom)

        def caption(samples, tokens, device):
            calls.append(([sample["prompt"] for sample in samples], tokens, device))
            if len(samples) > 1:
                raise BatchOom("synthetic batch OOM")
            return [f"caption for {samples[0]['prompt']}"]

        ops._caption_independent_images = caption
        samples = [{"image": object(), "prompt": "first"}, {"image": object(), "prompt": "second"}]
        results = ops._caption_independent_on_card(samples, 128)

        eq("batch then original singles", [len(prompts) for prompts, _tokens, _device in calls], [2, 1, 1])
        eq("token budget unchanged", [tokens for _prompts, tokens, _device in calls], [128, 128, 128])
        eq("device unchanged", [device for _prompts, _tokens, device in calls], ["cuda", "cuda", "cuda"])
        eq("request order/result mapping", results, ["caption for first", "caption for second"])
    finally:
        r.vlm_device = saved["vlm_device"]
        r.family_lock = saved["family_lock"]
        r.run_on_model_thread = saved["run_on_model_thread"]
        r.cleanup_torch = saved["cleanup_torch"]
        ops._caption_independent_images = saved["caption"]
        ops._is_cuda_oom = saved["is_oom"]


def check_caption_batch_generation() -> None:
    """Padding and generation preserve each independent prompt and quality setting."""
    saved_torch = sys.modules.get("torch")
    saved_vlm, saved_release = r.vlm, r.release
    calls = {}

    class Inputs(dict):
        def to(self, device):
            calls["device"] = device
            return self

    class Output:
        def __getitem__(self, key):
            calls["slice"] = key[1].start
            return "generated"

    def template(conversations, **kwargs):
        calls["conversations"] = conversations
        calls["template"] = kwargs
        return Inputs(input_ids=SimpleNamespace(shape=(2, 12)))

    def generate(**kwargs):
        calls["generate"] = kwargs
        return Output()

    processor = SimpleNamespace(
        tokenizer=SimpleNamespace(padding_side="right"),
        image_processor=SimpleNamespace(min_pixels=1),
        apply_chat_template=template,
        batch_decode=lambda output, **kwargs: [" first caption ", " second caption "],
    )
    samples = [{"image": object(), "prompt": "first prompt"}, {"image": object(), "prompt": "second prompt"}]
    try:
        sys.modules["torch"] = SimpleNamespace(inference_mode=nullcontext)
        r.vlm = lambda device: (processor, SimpleNamespace(device=device, generate=generate))
        r.release = lambda key: calls.update(released=key)
        result = ops._caption_independent_images(samples, 160, "cuda")
        eq("batch uses left padding", processor.tokenizer.padding_side, "left")
        eq("batch pixel ceiling unchanged", processor.image_processor.max_pixels, ops.VLM_MAX_PIXELS)
        eq("batch token budget unchanged", calls["generate"]["max_new_tokens"], 160)
        eq("batch stays greedy", calls["generate"]["do_sample"], False)
        eq("batch slices the entire padded prompt", calls["slice"], 12)
        eq("batch preserves result order", result, ["first caption", "second caption"])
        for index, sample in enumerate(samples):
            content = calls["conversations"][index][0]["content"]
            eq(f"image {index} unchanged", content[0]["image"] is sample["image"], True)
            eq(f"prompt {index} unchanged", content[1]["text"], sample["prompt"])
        eq("model released", calls["released"], "vlm:cuda")
    finally:
        r.vlm, r.release = saved_vlm, saved_release
        if saved_torch is None:
            sys.modules.pop("torch", None)
        else:
            sys.modules["torch"] = saved_torch


def check_expired_generation() -> None:
    """A search that timed out while queued must not touch the VLM afterward."""
    saved_lock, saved_run = r.family_lock, r.run_on_model_thread
    calls = []
    try:
        r.family_lock = lambda family: r.FamilyLock()
        r.run_on_model_thread = lambda family, fn: calls.append(family)
        try:
            ops._generate_on_vlm([{"role": "user", "content": "Anna"}], 48, 0)
            _failures.append("expired generate: did not raise")
            print("  FAIL expired generate: did not raise")
        except TimeoutError:
            eq("expired generate never reaches model thread", calls, [])
    finally:
        r.family_lock, r.run_on_model_thread = saved_lock, saved_run


def check_family_lock_priority() -> None:
    """Interactive work acquires next, without reordering equal-priority captions."""
    lock = r.FamilyLock()
    order = []
    threads = []
    lock.acquire()

    def start(name, priority):
        thread = threading.Thread(target=lambda: acquire(name, priority))
        thread.start()
        threads.append(thread)
        deadline = time.monotonic() + 1
        while len(lock._waiters) < len(threads) and time.monotonic() < deadline:
            time.sleep(0.001)

    def acquire(name, priority):
        with lock.hold(priority):
            order.append(name)

    start("caption-1", 0)
    start("caption-2", 0)
    start("search", 1)
    lock.release()
    for thread in threads:
        thread.join(timeout=1)

    eq("interactive jumps queued captions", order, ["search", "caption-1", "caption-2"])


def check_required_gpu() -> None:
    """An interactive parse refuses the huge CPU VLM path when CUDA is absent."""
    saved_torch = sys.modules.get("torch")
    saved_device, saved_vlm = r.vlm_device, r.vlm
    saved_thread = r.run_on_model_thread
    loaded = []
    try:
        sys.modules["torch"] = SimpleNamespace()
        r.vlm_device = lambda: "cpu"
        r.vlm = lambda device: loaded.append(device)
        r.run_on_model_thread = lambda family, fn: fn()
        try:
            ops._generate_on_vlm([{"role": "user", "content": "Anna"}], 48, require_gpu=True)
            _failures.append("required GPU: did not raise")
            print("  FAIL required GPU: did not raise")
        except RuntimeError as exc:
            eq("required GPU reports unavailable", str(exc), "generate: GPU unavailable")
            eq("required GPU never loads a CPU VLM", loaded, [])
    finally:
        r.vlm_device, r.vlm = saved_device, saved_vlm
        r.run_on_model_thread = saved_thread
        if saved_torch is None:
            sys.modules.pop("torch", None)
        else:
            sys.modules["torch"] = saved_torch


def main() -> int:
    print("--- family lock priority ---")
    check_family_lock_priority()
    print()
    print("--- required GPU ---")
    check_required_gpu()
    print()
    print("--- expired interactive generation ---")
    check_expired_generation()
    print()
    print("--- caption batch generation ---")
    check_caption_batch_generation()
    print("--- caption batch OOM split ---")
    check_caption_batch_oom_split()
    print()
    print("--- vlm_device ---")
    check_vlm_device()
    print()
    print("--- _card_busy_watchdog ---")
    check_card_busy_watchdog()
    print()

    if _failures:
        print(f"{len(_failures)} FAILED")
        for failure in _failures:
            print(f" - {failure}")
        return 1
    print("all caption-device checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())

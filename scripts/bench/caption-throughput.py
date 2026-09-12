#!/usr/bin/env python3
"""Compare production single-image and microbatch paths on a fixed image sample.

Run inside the media image with the application stopped and read-only originals.
--sample accepts caption-bakeoff-pick.ts output. JSONL stdout retains every
caption/error for human quality review; passing timing gates is NOT quality approval.
No database writes, model changes, or reduced image/token budgets are performed.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcs/modelworker"))


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    sample = json.loads(Path(args.sample).read_text())
    if len(sample) < 2 or len(sample) % 2 or args.rounds < 1:
        parser.error("sample must contain an even number of at least two images; rounds must be positive")

    # Import the actual TypeScript constants, not a copy that can silently drift.
    settings = json.loads(subprocess.check_output([
        "deno", "eval", "import { CAPTION_PROMPT_IMAGE, CAPTION_PROMPT_VERSION, "
        "CAPTION_TOKENS_IMAGE } from './modules/media/lib/describe.ts'; "
        "console.log(JSON.stringify({prompt: CAPTION_PROMPT_IMAGE, "
        "version: CAPTION_PROMPT_VERSION, tokens: CAPTION_TOKENS_IMAGE}));",
    ], cwd=ROOT, text=True))
    import inference_ops as ops
    import model_registry as registry
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("caption throughput requires CUDA")
    registry.set_resident(True)
    requests = [{"paths": [row["hostPath"]], "prompt": settings["prompt"],
                 "max_new_tokens": settings["tokens"], "angle": row.get("angle", 0),
                 "mirror": row.get("mirror", False)} for row in sample]
    manifest = []
    for row in sample:
        with open(row["hostPath"], "rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        manifest.append({"path": row["hostPath"], "sha256": digest})
    emit({"type": "settings", **settings, "model": registry.VLM_MODEL,
          "quant": registry.VLM_QUANT, "max_dim": ops.MAX_IMAGE_DIM,
          "max_pixels": ops.VLM_MAX_PIXELS, "sample": manifest})

    def invoke(group, size):
        if size == 2:
            return ops.caption_batch(group)
        try:
            return [{"ok": True, "result": ops.caption(**group[0])}]
        except Exception as exc:
            return [{"ok": False, "error": str(exc)}]

    # Warm both paths before measuring; model load and kernel initialization are excluded.
    for size in (1, 2):
        replies = invoke(requests[:size], size)
        emit({"type": "warmup", "batch_size": size, "replies": replies})
        if not all(reply.get("ok") for reply in replies):
            raise RuntimeError("caption warmup failed")

    totals = {1: {"seconds": 0, "successes": 0}, 2: {"seconds": 0, "successes": 0}}
    for round_number in range(args.rounds):
        # Reverse order each round to reduce systematic thermal/order bias.
        for size in ((1, 2) if round_number % 2 == 0 else (2, 1)):
            samples = []
            stop = threading.Event()

            def monitor():
                while not stop.is_set():
                    try:
                        line = subprocess.check_output([
                            "nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                            "--format=csv,noheader,nounits", "--id=0",
                        ], text=True, timeout=5).strip()
                        samples.append([float(value) for value in line.split(",")])
                    except Exception as exc:
                        emit({"type": "sampling_error", "error": str(exc)})
                    stop.wait(0.2)

            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            start = time.perf_counter()
            successes = 0
            try:
                for index in range(0, len(requests), size):
                    tick = time.perf_counter()
                    replies = invoke(requests[index:index + size], size)
                    successes += sum(bool(reply.get("ok") and
                                          reply.get("result", {}).get("captions", [""])[0])
                                     for reply in replies)
                    emit({"type": "result", "round": round_number, "batch_size": size,
                          "index": index, "seconds": time.perf_counter() - tick,
                          "replies": replies})
            finally:
                elapsed = time.perf_counter() - start
                stop.set()
                thread.join(timeout=6)
            totals[size]["seconds"] += elapsed
            totals[size]["successes"] += successes
            emit({"type": "block", "round": round_number, "batch_size": size,
                  "seconds": elapsed, "successes": successes, "gpu_samples": samples,
                  "gpu_util_avg": sum(row[0] for row in samples) / len(samples) if samples else None,
                  "vram_peak_percent": max(row[1] / row[2] * 100 for row in samples) if samples else None})
    rates = {size: row["successes"] / row["seconds"] for size, row in totals.items()}
    emit({"type": "summary", "totals": totals, "captions_per_second": rates,
          "batch_two_speedup": rates[2] / rates[1] if rates[1] else None,
          "quality_review": "required; inspect paired outputs against original images"})


if __name__ == "__main__":
    main()

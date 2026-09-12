#!/usr/bin/env python3
"""caption-bakeoff — compare candidate captioners against the incumbent, on real archive images.

    python3 scripts/caption-bakeoff.py --sample sample.json --out caption-bakeoff.html \
        --models 4b-bf16,8b-nf4 --prompts A --dims 1024,2048

THREE AXES, and the third is the one most likely to matter. Model, prompt, and INPUT RESOLUTION:
production downscales every image to 1024px on its longest side before captioning, which leaves a
face in a group photo around 30 pixels wide. See DEFAULT_DIM below. Run at least two dims before
concluding anything about which model is better.

Reads the JSON written by `scripts/caption-bakeoff-pick.ts`, which carries each image's host
path AND the caption Florence-2 produced for it. The incumbent column is therefore free, and
this script never loads Florence — it cannot, because Florence's remote code is broken from
transformers 4.50 onward while every candidate here needs >= 4.57. See the pick script's header.

RUN IT IN A THROWAWAY IMAGE DERIVED FROM murrix, never in the deployed container. fry's host has
no python3-venv (and installing it needs sudo), and deriving from murrix reuses the ~3 GB cu130
torch layer instead of re-downloading it. It also makes phase 1 a real rehearsal of the phase 3
dependency change, on the actual card:

    FROM localhost/murrix:latest
    RUN pip install --no-cache-dir --upgrade "transformers>=4.57" accelerate \
        compressed-tensors bitsandbytes
    ENV HF_HOME=/hf

    podman run --rm --device nvidia.com/gpu=all \
      -v /home/mattias/m/files:/files:ro -v /home/mattias/m/git/MURRiX/old:/old:ro \
      -v /var/tmp/hf-cache:/hf -v /var/tmp/bakeoff-work:/work \
      --entrypoint python3 murrix-bakeoff /work/caption-bakeoff.py --sample /work/sample.json ...

The sample carries CONTAINER paths, and this archive's originals are symlinks into /old/files,
which only resolve inside the container — so both roots must be mounted or every image is a
broken symlink. Upgrading transformers in this image BREAKS Florence-2, which is fine: nothing
here captions with Florence.

ONE MODEL RESIDENT AT A TIME. Candidates are run grouped by model, and each is freed before the
next loads. Holding two would OOM a 16 GB card and, worse, would make the VRAM column meaningless
— and that column is half the reason this script exists. The 8B-vs-4B question is not only
"which captions better", it is "which captions better per second", because a caption that takes
15s head-of-line-blocks an interactive query-parse on the shared family lock.

EVERY CANDIDATE IS WRAPPED. A model that loads and then dies on its first real kernel launch is
the specific failure this stack has hit before on sm_120 (see src/Containerfile's TORCH_CUDA
note). Here that must degrade to one empty column and a recorded error, not to a lost run over
45 images.
"""
import argparse
import base64
import gc
import html
import io
import json
import os
import sys
import time
import traceback

# Production's current value (inference_ops.MAX_IMAGE_DIM), and the baseline column here.
#
# THIS IS PROBABLY THE REAL CAUSE OF THE BAD EXPRESSIONS, more than the model is. Every image is
# downscaled so its longest side is 1024px before it ever reaches the captioner. A 4000px-wide
# group photo where a face spans 3% of the frame arrives with that face ~31px across — no model,
# at any parameter count, reads a smile off 31 pixels. That is why `--dims` is a first-class axis
# of this bake-off and not a tuning detail: swapping Florence for an 8B VLM while still feeding it
# 31px faces would buy far less than the model change appears to promise.
DEFAULT_DIM = 1024

# Vision tokens scale with pixels, so this must track the dim being tested rather than being
# fixed: Qwen3-VL re-tiles whatever it is given, and a max_pixels below the raster silently
# undoes the resolution the column claims to be measuring. 28 is the model's patch size.
def max_pixels_for(dim):
    return dim * 28 * 28

PROMPTS = {
    # A — abstain when unsure. The hypothesis this whole change rests on: a caption that omits
    # an expression is worth more than one that invents it, because this text feeds MiniLM and
    # pgvector, and a wrong emotion actively poisons semantic search where an omission merely
    # fails to help it.
    "A": (
        "Describe this photograph for a family photo archive. Say what is visible: the setting, "
        "the objects, and what the people are doing. Describe a facial expression or emotion only "
        "when it is unmistakable; when it is not, describe the face plainly and say nothing about "
        "the emotion. Do not guess names, ages, relationships or nationalities. Do not speculate "
        "about the occasion, the date or the place unless it is written in the image. Write two to "
        "four plain sentences. Do not begin with \"The image shows\"."
    ),
    # B — always name the emotion. The control. If B beats A on your eye, the abstention
    # hypothesis is wrong and the plan's prompt should change.
    "B": (
        "Describe this photograph for a family photo archive. Say what is visible: the setting, "
        "the objects, and what the people are doing, including their facial expressions and "
        "apparent mood. Do not guess names or relationships. Write two to four plain sentences. "
        "Do not begin with \"The image shows\"."
    ),
    # C — physical description only, no emotion vocabulary at all. Worth a real hearing rather
    # than being dismissed as timid: it is verifiable, and MiniLM still matches a query for
    # "laughing" against "mouth open, head tilted back, eyes creased".
    "C": (
        "Describe this photograph for a family photo archive. Say what is visible: the setting, "
        "the objects, the people and their posture, gaze and the physical configuration of their "
        "faces (for example: mouth open, eyes closed, head tilted back). Do not name or infer any "
        "emotion, mood or feeling. Do not guess names, ages or relationships. Write two to four "
        "plain sentences. Do not begin with \"The image shows\"."
    ),
}

# The candidate set. `quant` picks the loader path, and the three differ in ways that matter:
# bf16 is the no-quantization-risk baseline, fp8 is the intended production format, nf4 is the
# only way an 8B fits at all. Keep the ids here in one place so a column label can never drift
# from the checkpoint that produced it.
MODELS = {
    "4b-bf16": {"id": "Qwen/Qwen3-VL-4B-Instruct", "quant": "bf16"},
    "8b-nf4": {"id": "Qwen/Qwen3-VL-8B-Instruct", "quant": "nf4"},
    "4b-nf4": {"id": "Qwen/Qwen3-VL-4B-Instruct", "quant": "nf4"},
    "qwen35-4b": {"id": "Qwen/Qwen3.5-4B", "quant": "bf16"},
    # int8 only becomes reachable once the card holds ONE model at a time: ~9-10 GB for the 8B,
    # which never fit beside whisper + the InsightFace arena but fits comfortably alone. It is a
    # milder quantization than NF4 and should caption closer to bf16, so under accuracy-over-time
    # it is the candidate to beat.
    "8b-int8": {"id": "Qwen/Qwen3-VL-8B-Instruct", "quant": "int8"},
    # FP8 IS NOT HERE ON PURPOSE. Measured on fry 2026-09-04, transformers 5.16.1 + torch
    # 2.14.0+cu130: loading Qwen/Qwen3-VL-4B-Instruct-FP8 dies in
    # quantizers/quantizer_finegrained_fp8.py update_tp_plan() with
    # "AttributeError: 'NoneType' object has no attribute 'get'" — the tp_plan layer_overrides
    # are None for this checkpoint and the quantizer does not guard it. The model card already
    # says transformers cannot load these weights and to use vLLM/SGLang; this is what that
    # looks like in practice. Consequence for the plan: the ~4.8 GB FP8 option does not exist on
    # this stack, so the choice is 4B at bf16 (~8 GB) or 8B at NF4 (~6 GB) — and the 8B is
    # therefore both the smaller AND the stronger option.
    # "4b-fp8": {"id": "Qwen/Qwen3-VL-4B-Instruct-FP8", "quant": "native"},
}


def load_image(path, dim):
    """Open, orient and downscale one image so its longest side is `dim`."""
    from PIL import Image, ImageOps

    image = Image.open(path)
    # describe-file passes an explicit angle/mirror from the file's stored orientation. Applying
    # the EXIF transform here is the closest standalone equivalent; without it a portrait shot
    # reaches the model on its side and every candidate is judged on a rotated image.
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    if max(image.size) > dim:
        scale = dim / max(image.size)
        # LANCZOS, deliberately. Production calls image.resize() with no filter, which is
        # bicubic — and bicubic downscaling by a factor of four aliases hard, smearing exactly
        # the fine facial detail this whole exercise is about. If a resolution column wins here,
        # part of that win is the filter, and the production fix is both.
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    return image


def thumbnail_uri(image, px=512):
    """A 512px JPEG as a data URI, so the report scp's off fry as a single file."""
    thumb = image.copy()
    thumb.thumbnail((px, px))
    buf = io.BytesIO()
    thumb.save(buf, format="JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def load_model(spec):
    """Load one candidate. Returns (processor, model)."""
    import torch
    from transformers import AutoProcessor, AutoModelForImageTextToText

    kwargs = {"device_map": "cuda:0"}
    if spec["quant"] == "bf16":
        kwargs["dtype"] = torch.bfloat16
    elif spec["quant"] == "int8":
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
    elif spec["quant"] == "nf4":
        from transformers import BitsAndBytesConfig

        # NF4 is the only 4-bit path with working sm_120 kernels in stock transformers — AWQ and
        # GPTQ's Blackwell field reports are vLLM's kernels, not these. It costs roughly 40% of
        # fp16 throughput on Blackwell, which is exactly what the ms/caption column is here to
        # price.
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
    # "native" means the checkpoint carries its own quantization config (compressed-tensors for
    # the FP8 builds). Passing a dtype there would dequantize it back to bf16 and quietly
    # measure the wrong thing.

    processor = AutoProcessor.from_pretrained(spec["id"])
    model = AutoModelForImageTextToText.from_pretrained(spec["id"], **kwargs).eval()
    return processor, model


def set_max_pixels(processor, dim):
    """Retune the processor's tiling budget to the resolution column being run.

    Set on the image_processor rather than passed at from_pretrained, because the resolution is
    an inner axis here and reloading weights per dim would dominate the run. If a future
    transformers moves this attribute, the symptom is a silent no-op — every dim column would
    produce identical vision-token counts, which the tok figures in the report would show.
    """
    px = max_pixels_for(dim)
    if hasattr(processor, "image_processor"):
        processor.image_processor.max_pixels = px
        # min_pixels must stay below max or the processor raises on the first image.
        processor.image_processor.min_pixels = min(getattr(processor.image_processor, "min_pixels", px // 4), px // 4)


def caption(processor, model, image, prompt, max_new_tokens):
    """One caption. Greedy — beams triple the cost of the most expensive step for no benefit on
    free-form description, and greedy is what production should run for reproducibility."""
    import torch

    messages = [{
        "role": "user",
        "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}],
    }]
    inputs = processor.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        return_dict=True, return_tensors="pt",
    ).to(model.device)

    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

    # Slice off the prompt before decoding. There is no post_process_generation equivalent for a
    # chat model, and decoding the whole sequence returns the instruction back as the caption.
    generated = out[:, inputs["input_ids"].shape[1]:]
    text = processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
    return text, int(generated.shape[1])


def free_model(processor, model):
    import torch

    del processor, model
    gc.collect()
    torch.cuda.empty_cache()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="caption-bakeoff-sample.json")
    ap.add_argument("--out", default="caption-bakeoff.html")
    ap.add_argument("--models", default="8b-nf4", help=f"comma-separated from {','.join(MODELS)}")
    ap.add_argument("--prompts", default="A", help=f"comma-separated from {','.join(PROMPTS)}")
    ap.add_argument("--dims", default=str(DEFAULT_DIM), help="comma-separated longest-side pixel sizes, e.g. 1024,2048")
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--limit", type=int, default=0, help="stop after N images (smoke test)")
    args = ap.parse_args()

    model_keys = [m.strip() for m in args.models.split(",") if m.strip()]
    prompt_keys = [p.strip() for p in args.prompts.split(",") if p.strip()]
    dims = [int(d.strip()) for d in args.dims.split(",") if d.strip()]
    for key in model_keys:
        if key not in MODELS:
            sys.exit(f"unknown model {key!r}; known: {', '.join(MODELS)}")
    for key in prompt_keys:
        if key not in PROMPTS:
            sys.exit(f"unknown prompt {key!r}; known: {', '.join(PROMPTS)}")

    with open(args.sample) as fh:
        sample = json.load(fh)
    if args.limit:
        sample = sample[:args.limit]
    if not sample:
        sys.exit("sample is empty")

    import torch

    if not torch.cuda.is_available():
        sys.exit("no CUDA device — this is a throughput measurement, it is meaningless on CPU")

    n_cols = len(model_keys) * len(prompt_keys) * len(dims)
    print(f"{len(sample)} images x {len(model_keys)} models x {len(prompt_keys)} prompts x {len(dims)} dims = {len(sample) * n_cols} captions", flush=True)

    # Decode every image once per resolution, up front. Doing it inside the model loop would put
    # ~45 CPU decodes in each timing loop and make the ms/caption column a decode benchmark.
    by_dim = {}
    for dim in dims:
        by_dim[dim] = [load_image(e["hostPath"], dim) for e in sample]
    # Thumbnails come from the largest decode so the report shows the most detail available.
    thumbs = [thumbnail_uri(im) for im in by_dim[max(dims)]]

    columns = []   # (label, model_key, prompt_key, dim)
    results = {}   # label -> list of {text, ms, tokens} | {error}
    errors = {}

    def label_for(model_key, prompt_key, dim):
        # Keep the dim out of the label when only one is being tested, so the common single-
        # resolution run does not grow a noisy suffix in every column header.
        return f"{model_key}/{prompt_key}" + (f"/{dim}px" if len(dims) > 1 else "")

    for model_key in model_keys:
        spec = MODELS[model_key]
        print(f"\n=== {model_key} ({spec['id']}, {spec['quant']}) ===", flush=True)
        torch.cuda.reset_peak_memory_stats()
        processor = model = None
        try:
            t0 = time.perf_counter()
            processor, model = load_model(spec)
            load_s = time.perf_counter() - t0
            weights_gb = torch.cuda.max_memory_allocated() / 1e9
            print(f"loaded in {load_s:.1f}s, weights {weights_gb:.2f} GB", flush=True)
        except Exception:
            # A candidate that will not load is a result, not a crash. Record it and move on so
            # the other columns still produce a report.
            errors[model_key] = traceback.format_exc()
            print(f"LOAD FAILED:\n{errors[model_key]}", file=sys.stderr, flush=True)
            for prompt_key in prompt_keys:
                for dim in dims:
                    label = label_for(model_key, prompt_key, dim)
                    columns.append((label, model_key, prompt_key, dim))
                    results[label] = [{"error": "load failed"} for _ in sample]
            continue

        try:
            for prompt_key in prompt_keys:
                for dim in dims:
                    label = label_for(model_key, prompt_key, dim)
                    columns.append((label, model_key, prompt_key, dim))
                    results[label] = []
                    set_max_pixels(processor, dim)
                    torch.cuda.reset_peak_memory_stats()
                    for i, image in enumerate(by_dim[dim]):
                        try:
                            t0 = time.perf_counter()
                            text, tokens = caption(processor, model, image, PROMPTS[prompt_key], args.max_new_tokens)
                            ms = (time.perf_counter() - t0) * 1000
                            results[label].append({"text": text, "ms": ms, "tokens": tokens})
                        except Exception as exc:
                            # A single image that OOMs at a high dim is a data point about that
                            # resolution, not a reason to lose the column. Clear the cache so the
                            # next image is not doomed by this one's fragmentation.
                            results[label].append({"error": str(exc)[:400]})
                            torch.cuda.empty_cache()
                            print(f"  [{i}] FAILED: {exc}", file=sys.stderr, flush=True)
                        if (i + 1) % 5 == 0:
                            print(f"  {i + 1}/{len(by_dim[dim])}", flush=True)
                    ok = [r for r in results[label] if "ms" in r]
                    peak_gb = torch.cuda.max_memory_allocated() / 1e9
                    failed = len(results[label]) - len(ok)
                    if ok:
                        mean_ms = sum(r["ms"] for r in ok) / len(ok)
                        mean_tok = sum(r["tokens"] for r in ok) / len(ok)
                        print(f"{label}: {mean_ms:.0f} ms/caption, {mean_tok:.0f} tokens, peak {peak_gb:.2f} GB, {failed} failed", flush=True)
        finally:
            free_model(processor, model)

    write_report(args.out, sample, thumbs, columns, results, errors, args)
    print(f"\nwrote {args.out}")


def write_report(out_path, sample, thumbs, columns, results, errors, args):
    """One self-contained HTML file: thumbnails inline, scoring radios, TSV export.

    The radios matter as much as the captions. Eyeballing 45 rows produces an impression;
    the plan's gate is a count ("beats Florence on >= 30, and on the majority of the
    named-wrong bucket"), and a count needs somewhere to put the clicks.
    """
    labels = [c[0] for c in columns]
    head = "".join(f"<th>{html.escape(l)}</th>" for l in labels)

    rows = []
    for i, entry in enumerate(sample):
        cells = []
        for label in labels:
            r = results[label][i]
            if "error" in r:
                cells.append(f'<td class="err">{html.escape(r["error"])}</td>')
            else:
                meta = f'<div class="meta">{r["ms"]:.0f} ms · {r["tokens"]} tok</div>'
                cells.append(f'<td><label><input type="radio" name="win{i}" value="{html.escape(label)}"> best</label>{meta}<div>{html.escape(r["text"])}</div></td>')
        rows.append(
            f'<tr><td class="img"><img src="{thumbs[i]}" loading="lazy">'
            f'<div class="meta">{html.escape(entry["bucket"])}</div>'
            f'<div class="meta path">{html.escape(entry["vfsPath"])}</div></td>'
            f'<td><label><input type="radio" name="win{i}" value="incumbent"> best</label>'
            f'<div class="meta">{html.escape(entry.get("describedBy") or "?")}</div>'
            f'<div>{html.escape(entry["currentCaption"])}</div></td>'
            + "".join(cells) + "</tr>"
        )

    err_html = ""
    if errors:
        err_html = "<h2>Load failures</h2>" + "".join(
            f"<h3>{html.escape(k)}</h3><pre>{html.escape(v)}</pre>" for k, v in errors.items()
        )

    doc = f"""<!doctype html>
<meta charset="utf-8">
<title>caption bake-off</title>
<style>
  body {{ font: 14px/1.5 system-ui, sans-serif; margin: 2rem; color: #111; background: #fff; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: .5rem; vertical-align: top; text-align: left; }}
  th {{ position: sticky; top: 0; background: #f3f3f3; }}
  td.img {{ width: 300px; }}
  img {{ max-width: 280px; height: auto; display: block; }}
  .meta {{ color: #666; font-size: 12px; }}
  .path {{ word-break: break-all; }}
  .err {{ color: #a00; font-family: ui-monospace, monospace; font-size: 12px; }}
  pre {{ background: #f6f6f6; padding: .75rem; overflow-x: auto; }}
  button {{ font: inherit; padding: .4rem .8rem; }}
  #tally {{ margin: 1rem 0; font-weight: 600; }}
</style>
<h1>caption bake-off</h1>
<p class="meta">{len(sample)} images · max_new_tokens={args.max_new_tokens} · incumbent captions read from <code>metadata/description</code>, not re-run</p>
<p><button onclick="tsv()">copy as TSV</button> <span id="tally"></span></p>
{err_html}
<table>
  <thead><tr><th>image</th><th>incumbent (Florence-2)</th>{head}</tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
<script>
const BUCKETS = {json.dumps([e["bucket"] for e in sample])};
function counts() {{
  const total = {{}}, byBucket = {{}};
  document.querySelectorAll('input[type=radio]:checked').forEach(r => {{
    const i = +r.name.slice(3), b = BUCKETS[i];
    total[r.value] = (total[r.value] || 0) + 1;
    byBucket[b] = byBucket[b] || {{}};
    byBucket[b][r.value] = (byBucket[b][r.value] || 0) + 1;
  }});
  return {{ total, byBucket }};
}}
function refresh() {{
  const {{ total }} = counts();
  const n = Object.values(total).reduce((a, b) => a + b, 0);
  document.getElementById('tally').textContent =
    n + '/' + BUCKETS.length + ' scored — ' +
    Object.entries(total).sort((a, b) => b[1] - a[1]).map(([k, v]) => k + ': ' + v).join('  ');
}}
document.addEventListener('change', refresh);
function tsv() {{
  const {{ total, byBucket }} = counts();
  let out = 'column\\twins\\n';
  for (const [k, v] of Object.entries(total).sort((a, b) => b[1] - a[1])) out += k + '\\t' + v + '\\n';
  out += '\\nbucket\\tcolumn\\twins\\n';
  for (const [b, m] of Object.entries(byBucket))
    for (const [k, v] of Object.entries(m)) out += b + '\\t' + k + '\\t' + v + '\\n';
  navigator.clipboard.writeText(out).then(() => alert('copied\\n\\n' + out));
}}
refresh();
</script>
"""
    with open(out_path, "w") as fh:
        fh.write(doc)


if __name__ == "__main__":
    main()

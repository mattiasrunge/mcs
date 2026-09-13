# MCS v2 — Media Cache Server
#
# Every media tool and every model MURRiX used to bake into its own image, behind one HTTP API.
# See docs/api.md for the contract and README.md for running it.
#
# Build:
#   podman build -t mcs -f Containerfile .
#
# Run (GPU, with the roots a MURRiX deployment shares — identical paths on both sides):
#   podman run --rm --device nvidia.com/gpu=all --network=host \
#     -e MCS_KEY=let-me-in -e MCS_ROOTS=/files:ro,/old:ro,/files-volatile:rw,/files-tmp:rw \
#     -v /srv/files:/files:ro -v /srv/old:/old:ro -v /srv/files-volatile:/files-volatile -v /srv/files-tmp:/files-tmp \
#     mcs
#
# THE LAYER PREFIX BELOW IS BYTE-FOR-BYTE MURRIX'S `src/Containerfile` UP TO `WORKDIR /app`.
# That is deliberate and temporary: podman's build cache is keyed on the parent layer plus the
# instruction, so keeping the same base image and the same instructions lets a build on a host
# that already holds the MURRiX image reuse its ~30 GB of wheels and baked weights instead of
# downloading them again. The Deno base is unused here and costs ~100 MB. Once MURRiX's image
# no longer carries these layers the base can become python:3.12 and the prefix can be edited
# freely — until then, change it in MURRiX first and copy it here, or the cache is lost.
#
# Everything MCS-specific starts at "MCS" below.

FROM docker.io/denoland/deno:debian-2.6.3

# CUDA wheel index for torch, and effectively for the whole image: torch's bundled nvidia-*
# packages are what every other GPU library resolves against at runtime.
#
# cu130 (CUDA 13) is what the GPU stack needs, and getting here took some doing. cu124 has no
# Blackwell kernels at all — torch dies at the first kernel launch on sm_120 with "no kernel
# image is available for execution on the device". Beyond that, the two ML runtimes disagree
# about which CUDA major they want, and BOTH failure modes are silent:
#
#   - onnxruntime-gpu (InsightFace) needs CUDA 13 (libcublasLt.so.13) from 1.24 onward. Given
#     CUDA 12 it drops to CPUExecutionProvider with only a warning. Pinning it back to the
#     last CUDA 12 line (1.23.0) is NOT a way out: that build binds CUDAExecutionProvider
#     happily and then fails on the first inference with cudaErrorNoKernelImageForDevice,
#     because it carries no sm_120 kernels.
#   - CTranslate2 (faster-whisper) links libcublas.so.12 and resolves it lazily at the first
#     decode, so on a pure CUDA 13 stack the model loads fine and only transcription fails
#     with "Library libcublas.so.12 is not found or cannot be loaded".
#
# The resolution is to ship both cuBLAS majors — see nvidia-cublas-cu12 below. The sonames
# differ (.so.12 vs .so.13) so they coexist in the ldconfig cache, and only the CUDA 13 cuDNN
# is installed, so there is no libcudnn.so.9 ambiguity. Verified on an RTX 5060 Ti (sm_120):
# InsightFace runs real inference on CUDA and faster-whisper actually transcribes.
#
# Anything that changes here must be re-checked with `make gpu-check`, which runs real
# inference per model — every failure above passes a load-only smoke test.
#
# Override per host: podman build --build-arg TORCH_CUDA=cu126 (or make container-build TORCH_CUDA=...).
ARG TORCH_CUDA=cu130

# faster-whisper model baked into the image. Must match model_registry.WHISPER_MODEL
# (env CFG_WHISPER_MODEL) or the first transcription downloads it at runtime, inside a
# pipeline step, with no network guarantee. See model_registry for the measured comparison —
# `base` mangles Swedish badly; large-v3 needs 5.5GB VRAM and earns it.
ARG WHISPER_MODEL=large-v3

# libheif-plugin-aomenc is what makes ImageMagick able to WRITE AVIF, which the whole
# derivative pipeline depends on. Debian splits libheif's codecs into plugin packages and
# `imagemagick` pulls in only the decoders (dav1d, libde265), so without this the failure is
# quiet and late: `convert -list format` reports AVIF as `r--` and every encode dies with
# "no encode delegate for this image format `AVIF'". Installing svtenc/rav1e alongside it
# changes nothing — libheif keeps aomenc's priority and the output is byte-identical.
#
# curl + xz-utils are for the ffmpeg fetch below; neither is in the deno base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libimage-exiftool-perl \
    imagemagick \
    libheif-plugin-aomenc \
    ffmpeg \
    libchromaprint-tools \
    curl \
    xz-utils \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    tesseract-ocr \
    tesseract-ocr-swe \
    && rm -rf /var/lib/apt/lists/*

# ffmpeg 9.0.1, from BtbN's static builds, shadowing the distro package on PATH.
#
# The base image cannot supply this: denoland/deno:debian-* is Debian stable (trixie, ffmpeg
# 7.1.x) for every tag, and Debian carries 8.1 in sid and 9.0 only in experimental — so no
# base-image bump gets us here, and apt-pinning experimental would drag newer libs under a
# stable userland.
#
# What 9 buys is transpose_cuda (upstream 2026-03-31, absent from 8.1). It is the last piece
# needed to keep a *rotated* video's whole filter chain on the GPU: with it the chain is
# yadif_cuda,scale_cuda,transpose_cuda -> av1_nvenc with no hwdownload and no CPU hop. See
# modules/media/bin/video-to-video.
#
# This follows upstream's n9.0 release branch, not git master. Pin via the dated release tag,
# whose asset name records the exact release-branch commit; the `latest` tag's `n9.0-latest`
# asset is a moving target. BtbN keeps only month-end dated builds long-term, so prefer one of
# those whenever the branch has one, or return to the distro package once backports ships 9.x.
#
# HOST DRIVER REQUIREMENT: this build wants nvenc API 13.1, i.e. **NVIDIA driver >= 610.00**.
# On an older driver EVERY nvenc encoder fails to open with "Driver does not support the
# required nvenc API version" — measured on fry at 595.84, where av1_nvenc AND h264_nvenc both
# failed here while the distro ffmpeg 7.1.5 encoded av1_nvenc on the same driver without
# complaint. Nothing breaks (video-to-video retries on the CPU chain) but the GPU encode is
# silently unused, so check `make gpu-check` after any driver change.
#
# The distro ffmpeg stays installed and PATH decides which one runs. PATH rather than an
# absolute path in external-commands.json is deliberate: compute_fingerprint.py spawns ffmpeg
# by name through subprocess and external-exec hands children the host PATH, so a single PATH
# entry keeps every caller on the same binary.
ARG FFMPEG_BUILD=autobuild-2026-08-31-13-27
ARG FFMPEG_ASSET=ffmpeg-n9.0.1-11-ge47273f4d9-linux64-gpl-9.0
RUN curl -fsSL "https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_BUILD}/${FFMPEG_ASSET}.tar.xz" \
    | tar -xJ -C /opt \
    && mv "/opt/${FFMPEG_ASSET}" /opt/ffmpeg \
    && rm -f /opt/ffmpeg/bin/ffplay \
    && /opt/ffmpeg/bin/ffmpeg -hide_banner -filters | grep -q transpose_cuda
ENV PATH="/opt/ffmpeg/bin:$PATH"

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# torch and torchvision come from the $TORCH_CUDA wheel index (see the ARG above). These wheels
# bundle their own CUDA runtime + cuDNN 9 as nvidia-* packages, so no CUDA base image is needed —
# only the host driver, exposed via the NVIDIA container toolkit (podman --device nvidia.com/gpu=all).
# onnxruntime-gpu (InsightFace) and CTranslate2 (faster-whisper) reuse those bundled libs at
# runtime via the dynamic linker (registered with ldconfig below). With no GPU device passed,
# everything falls back to CPU automatically — see the Python scripts' device auto-detection.
#
# This MUST stay a separate pip invocation with a single --index-url. pip resolves the highest
# version across all indexes, so with pypi.org as an --extra-index-url PyPI's plain torch — a
# newer CUDA build than this image wants — outranks the pinned one and TORCH_CUDA silently
# does nothing. With no second index to outbid it, the CUDA index's newest matching wheel is
# what gets installed. torchvision comes along here so it matches torch's CUDA build rather
# than being resolved from PyPI as a transitive dependency.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/${TORCH_CUDA} \
    torch \
    torchvision

# Everything else from PyPI. torch/torchvision are already satisfied above, so pip keeps the
# CUDA-index builds instead of resolving its own.
#
# onnxruntime-gpu is pinned so its CUDA major cannot drift away from $TORCH_CUDA: 1.28 wants
# CUDA 13, matching cu130. Moving one means re-checking the other with `make gpu-check`.
#
# nvidia-cublas-cu12 is here solely for CTranslate2 (faster-whisper), which links
# libcublas.so.12 while the rest of the image is CUDA 13. It installs alongside the CUDA 13
# cuBLAS rather than replacing it — different sonames, same directory.
# transformers is pinned to an exact release rather than a floor. It was held at 4.49.0 for
# Florence-2's remote code, which broke from 4.50 onward; that constraint is gone with Florence,
# but an exact pin stays because this is the layer that decides whether every model in the image
# still loads. 5.16.1 is what the change was validated against on fry (real CUDA matmul on
# sm_120, and a 45-image caption run).
#
# sentence-transformers is pinned for a different and sharper reason: MiniLM's output must not
# move. Every description embedding in pgvector came from it, so a change in its pooling or
# normalisation silently invalidates the whole search index without failing anything.
#
# bitsandbytes quantizes the captioning model at load; compressed-tensors and accelerate are its
# loader dependencies. einops and timm are gone with Florence's remote code.
RUN pip install --no-cache-dir \
    insightface \
    onnxruntime-gpu==1.28.0 \
    nvidia-cublas-cu12 \
    opencv-python-headless \
    imagehash \
    Pillow \
    pillow-heif \
    rawpy \
    PyMuPDF \
    python-docx \
    odfpy \
    pytesseract \
    transformers==5.16.1 \
    accelerate \
    bitsandbytes \
    compressed-tensors \
    sentence-transformers==5.7.0 \
    faster-whisper

# insightface and faster-whisper both declare a hard dependency on `onnxruntime` (the CPU
# wheel), so pip installs it alongside onnxruntime-gpu. Both provide the same `onnxruntime`
# module and the CPU wheel wins at import time — CUDAExecutionProvider then never shows up in
# get_available_providers() and InsightFace silently runs on CPU even with the GPU passed
# through. Drop the CPU wheel; onnxruntime-gpu satisfies the same import.
# Expose the torch-bundled NVIDIA libs (cuDNN, cuBLAS, cuda_runtime, ...) to the dynamic
# linker so onnxruntime-gpu and CTranslate2 (faster-whisper) can find them at runtime. torch
# loads its own copies via RPATH, but those two resolve via the system linker. Register the
# nvidia/*/lib dirs through ldconfig (robust against the python minor version in the path).
#
# This MUST precede the onnxruntime check below: that step imports onnxruntime-gpu, which
# resolves libcudart via the system linker. Run it first and the import fails with
# "libcudart.so.13: cannot open shared object file" even though the lib is present.
RUN python3 -c 'import glob, os, nvidia; print("\n".join(sorted({d for base in nvidia.__path__ for d in glob.glob(os.path.join(base, "*", "lib"))})))' \
        > /etc/ld.so.conf.d/nvidia-python.conf \
    && ldconfig

# Both wheels install into the same `onnxruntime` package directory, and each one's RECORD
# lists those shared files — so uninstalling the CPU wheel deletes files onnxruntime-gpu is
# still using, leaving an `onnxruntime` that imports but has no get_available_providers().
# Reinstalling the GPU wheel afterwards puts them back; --no-deps keeps insightface's
# dependency on the CPU wheel from dragging it straight back in.
RUN pip uninstall -y onnxruntime \
    && pip install --no-cache-dir --force-reinstall --no-deps onnxruntime-gpu==1.28.0 \
    && python3 -c "import onnxruntime as ort; ps = ort.get_available_providers(); \
        print('onnxruntime providers:', ps); \
        assert 'CUDAExecutionProvider' in ps, 'CPU wheel still shadows onnxruntime-gpu: %r' % (ps,)"

RUN python3 -c "from insightface.app import FaceAnalysis; \
    app = FaceAnalysis(name='buffalo_l', root='/opt/insightface', providers=['CPUExecutionProvider']); \
    app.prepare(ctx_id=0, det_size=(640, 640))"

ENV INSIGHTFACE_HOME=/opt/insightface

# Where every Hugging Face model — the VLM, whisper, MiniLM and the instruct model — is baked
# and later read from. This MUST precede the four bake layers below, and it is set for exactly
# the reason INSIGHTFACE_HOME above is.
#
# Without it the cache follows $HOME, and $HOME is not the same at build time as at run time.
# The build runs as root and wrote ~20GB into /root/.cache/huggingface; the model server runs
# from a MURRiX shell owned by `admin`, so it looked in /home/admin/.cache/huggingface, found
# nothing, and re-downloaded every model from the Hub — inside a pipeline step, against the
# caption client's 600s timeout, on every container recreate. MEASURED on fry2 2026-09-06:
# `preloaded minilm in 295.8s` for a 120MB model that loads from disk in ~24s, and a caption
# still fetching Qwen3-VL 20 minutes in, with the xet transfer log open in the process's fds.
#
# /opt rather than either home directory: it belongs to neither user, and it is the same shape
# as /opt/insightface, which never had this problem because it was told where to look.
ENV HF_HOME=/opt/huggingface

ENV CFG_WHISPER_MODEL=${WHISPER_MODEL}

# Captioning model. Baked for the same reason as WHISPER_MODEL and INSTRUCT_MODEL: the first
# caption would otherwise pull ~17GB inside a pipeline step with no network guarantee.
#
# The ARG is deliberately NOT here with the others at the top of the file — see the note beside
# INSTRUCT_MODEL below for why an ARG placed high re-runs apt, the torch wheel and every other
# bake. It is declared immediately before its own layer instead.
ARG VLM_MODEL=Qwen/Qwen3-VL-8B-Instruct
ENV CFG_VLM_MODEL=${VLM_MODEL}

# No trust_remote_code, unlike the Florence-2 model this replaces: Qwen3-VL is a stock
# transformers architecture. That is also why `transformers` could finally be unpinned above —
# Florence's remote code breaks from 4.50 onward (huggingface/transformers#36886, #41622), which
# is what held the whole image at 4.49.0.
#
# Weights are fetched unquantized here and quantized at load by bitsandbytes, so this layer is
# ~17GB on disk even though only 6.43GB reaches the card.
RUN python3 -c "from transformers import AutoProcessor, AutoModelForImageTextToText; \
    AutoProcessor.from_pretrained('${VLM_MODEL}'); \
    AutoModelForImageTextToText.from_pretrained('${VLM_MODEL}')"

RUN python3 -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

# Prewarm whisper *and its VAD*. Constructing WhisperModel downloads the weights only; the
# Silero VAD is a separate ONNX asset that faster-whisper fetches on the first transcribe
# asking for it — which in this system is inside a pipeline step, with no network guarantee.
# That is the same failure the WHISPER_MODEL note warns about, one layer down. Running a
# real transcribe here pulls it at build time instead. Over generated silence, so it costs
# nothing: the VAD finds no speech and no decode ever runs.
RUN python3 -c "import os, tempfile, wave; \
    from faster_whisper import WhisperModel; \
    model = WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8'); \
    path = os.path.join(tempfile.mkdtemp(), 'silence.wav'); \
    handle = wave.open(path, 'wb'); \
    handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000); \
    handle.writeframes(b'\x00' * 32000); handle.close(); \
    segments, _info = model.transcribe(path, vad_filter=True, condition_on_previous_text=False); \
    list(segments); \
    print('whisper and vad prewarmed')"

# Instruct model, for turning a natural-language search query into filters. Same rule as
# WHISPER_MODEL: must match model_registry.INSTRUCT_MODEL (env CFG_INSTRUCT_MODEL) or the
# first such query downloads several GB at runtime — here inside an *interactive* request
# rather than a pipeline step, so the wait is in someone's face.
#
# The ARG is declared HERE rather than beside the other build args at the top, because an ARG
# invalidates the build cache for every instruction after it. Declared at the top it re-ran
# apt, the cu130 torch wheel and every other model bake — a ~30 minute full rebuild to change
# one model name. Down here it invalidates only its own layer. WHISPER_MODEL still has that
# problem; it is left alone because moving it would invalidate the cache once more for no
# gain today.
ARG INSTRUCT_MODEL=Qwen/Qwen2.5-1.5B-Instruct
ENV CFG_INSTRUCT_MODEL=${INSTRUCT_MODEL}

# No trust_remote_code here, unlike Florence: Qwen2 is a stock transformers architecture.
RUN python3 -c "from transformers import AutoTokenizer, AutoModelForCausalLM; \
    AutoTokenizer.from_pretrained('${INSTRUCT_MODEL}'); \
    AutoModelForCausalLM.from_pretrained('${INSTRUCT_MODEL}')"

# ── Speaker diarization + voiceprint (NeMo) and active-speaker detection (LR-ASD) ─────────────
#
# For work/plans/voice-fingerprint.md. This block is deliberately LAST among the pip/model
# layers and carries its own risk note, because nemo_toolkit is not one clean library: the
# [asr] extra hard-depends on a training stack — lightning (<=2.4), hydra-core (<=1.3.2),
# omegaconf, webdataset, wandb, torchmetrics, sentencepiece, nv_one_logger_* — layered on top
# of the curated torch(cu130) / transformers==5.16.1 / onnxruntime-gpu==1.28.0 / CTranslate2
# graph above. Accepted deliberately (the alternatives — pyannote's gated weights, or a
# hand-assembled SpeechBrain diarization recipe — were weighed and declined).
#
# Rules this install follows so it cannot disturb the stack above:
#   - SEPARATE pip invocation with NO --index-url / --extra-index-url, so pip keeps the cu130
#     torch already satisfied instead of resolving torch==2.12.0+cu132 from NeMo's `cu13`
#     extra (that extra is opt-in and NOT requested here).
#   - transformers is left unpinned by NeMo, so the exact 5.16.1 from above wins. NeMo 3.0.0
#     is not known to be tested against transformers 5.x — a NeMo import breaking on it is the
#     first thing to bisect.
#   - `make gpu-check` (torch / InsightFace / VLM / MiniLM / whisper must all still load and
#     compute) is mandatory after any change here, plus scripts/voice-models-bench.py for the
#     three new models themselves.
RUN pip install --no-cache-dir \
    "nemo_toolkit[asr]==3.0.0" \
    python_speech_features

# NeMo caches pretrained checkpoints under $HOME by default — the same build-time vs run-time
# HOME split that HF_HOME and INSIGHTFACE_HOME above exist to solve (build runs as root, the
# model server runs from a MURRiX shell owned by `admin`). Point it at /opt, owned by neither.
ENV NEMO_CACHE_DIR=/opt/nemo

# Bake the two checkpoints so the first diarize / voiceprint in a pipeline step does not pull
# them from NGC / the Hub with no network guarantee — the same rule as WHISPER_MODEL et al.
# Sortformer = speaker-turn segmentation (handles overlapped speech); TitaNet-large = the
# 192-dim voiceprint embedding. Both are CC-BY-4.0 and download without a token.
#
# Class names follow the NeMo model cards for nvidia/diar_sortformer_4spk-v1 and
# nvidia/speakerverification_en_titanet_large; if NeMo 3.0 moved them, the bench script is
# where that surfaces first.
RUN python3 -c "from nemo.collections.asr.models import SortformerEncLabelModel, EncDecSpeakerLabelModel; \
    SortformerEncLabelModel.from_pretrained('nvidia/diar_sortformer_4spk-v1'); \
    EncDecSpeakerLabelModel.from_pretrained('nvidia/speakerverification_en_titanet_large')"

# LR-ASD (active speaker detection, IJCV 2025). MIT-licensed; the two ~3.4MB weight files are
# committed in the repo, so there is nothing to download separately and no gate. Fetched as a
# pinned-commit tarball via curl for the same reason ffmpeg is — `git` is not in the image and
# adding it to the apt layer above would invalidate every model bake between here and there.
# Only the core model (model/*.py + the weights) is used at runtime; the repo's own S3FD face
# detector and scenedetect demo path are not — face tracks come from InsightFace instead.
ARG LR_ASD_COMMIT=1b6dcd2d8fc2895683de6508ec6294ec47d388ca
RUN curl -fsSL "https://github.com/Junhua-Liao/LR-ASD/archive/${LR_ASD_COMMIT}.tar.gz" \
    | tar -xz -C /opt \
    && mv "/opt/LR-ASD-${LR_ASD_COMMIT}" /opt/lr-asd \
    && test -f /opt/lr-asd/weight/finetuning_TalkSet.model
ENV LR_ASD_HOME=/opt/lr-asd

# ── MCS ─────────────────────────────────────────────────────────────────────────────────────

# The HTTP front. Pinned floors only; the model stack above is what decides compatibility.
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30"

WORKDIR /opt/mcs
COPY mcs mcs
COPY scripts scripts
COPY docs docs
COPY pyproject.toml README.md ./

# Compile once so the first request does not.
RUN python3 -m compileall -q mcs scripts

# Defaults an operator overrides per deployment. MCS_ROOTS is empty on purpose: a container
# started without naming what it may read refuses every file request instead of serving the
# disk it happens to see.
ENV MCS_PORT=8181
ENV MCS_HOST=0.0.0.0
ENV MCS_ROOTS=""
ENV MCS_SCRATCH=/tmp/mcs
ENV MCS_KEYS_FILE=/etc/mcs.keys
# The model worker's own knobs, in the MCS namespace (worker.py maps MCS_* → CFG_*). These
# repeat the CFG_* values baked above so `capabilities` reports them without consulting both.
ENV MCS_WHISPER_MODEL=${WHISPER_MODEL}
ENV MCS_VLM_MODEL=${VLM_MODEL}
ENV MCS_INSTRUCT_MODEL=${INSTRUCT_MODEL}
# ImageMagick parallelises one `magick` across cores via OpenMP (default: all of them). Left
# alone, every rendition in the tool lane (MCS_LIMIT_TOOLS wide) spawns that many threads and
# oversubscribes the box. Per-image OpenMP scaling is sublinear, so N independent images at one
# thread each beats one image at N threads — and one thread per slot is what lets MCS_LIMIT_TOOLS
# be sized against cores. MURRiX carried this value; it moved with the tool.
ENV MAGICK_THREAD_LIMIT=1
# The transcodes' knobs, at the values MURRiX's video-to-video was measured into (its own
# comments are with `mcs/tools/encode.py`): the CPU decode's projected footprint per window,
# the constant it is projected from, how many windows one encode may become. MCS_VIDEO_ENCODER
# is empty on purpose — av1_nvenc when the card and this ffmpeg offer it, libsvtav1 otherwise.
ENV MCS_CPU_ENCODE_MAX_MB=4000
ENV MCS_CPU_ENCODE_MB_PER_1K_FRAMES=170
ENV MCS_CPU_ENCODE_MAX_SEGMENTS=64

EXPOSE 8181

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8181/v2/health', timeout=4).status == 200 else 1)" || exit 1

CMD ["python3", "-m", "mcs"]

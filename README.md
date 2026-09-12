# MCS v2 — Media Cache Server

A stateless service that answers questions about media bytes and produces media bytes: probing,
renditions, transcodes, fingerprints, face detection, speech, captions and descriptions, text
embeddings and generation, document text. It owns the tools (ffmpeg, ImageMagick, exiftool,
chromaprint, tesseract) and the models (a VLM, InsightFace, MiniLM, whisper, NeMo diarization
and voiceprints, LR-ASD) behind one HTTP API, so a caller never depends on which tool or model
answers. **The contract is [docs/api.md](docs/api.md).**

v1 (2015–2017, the `master` branch) was the same idea for the MURRiX of its day: a keyed
service on identically mounted volumes. v2 keeps that shape and drops the cache — the caller
decides where results live.

## Shape

Two processes in one container. The **front** (`python -m mcs`, FastAPI) validates requests,
spawns the tools, and holds the composition. The **model worker** (`mcs/modelworker/`, the
resident model server) holds the models behind a Unix socket and recycles itself on its RSS
ceiling without taking the listener down; the front supervises and restarts it.

```
POST /v2/<namespace>/<op>     JSON in, JSON out; Accept: text/event-stream for progress
GET  /v2/health               liveness, loaded models, VRAM, degraded fallbacks
GET  /v2/capabilities         ops, roots, tools, models, limits
```

Files are absolute paths under configured **roots** (`MCS_ROOTS=/files:ro,/old:ro,/files-volatile:rw`);
anything outside is refused. Mount the same paths on both sides and nothing is ever copied.

## Run

```bash
make build                         # the image (tens of GB: models are baked in)
make run GPU=1 MCS_KEY=… FILES_PATH=… OLD_PATH=… VOLATILE_PATH=… TMP_PATH=…
make health                        # GET /v2/health
make gpu-check                     # real inference per model, reports the device each bound to
make remote-build HOST=fry         # the same on a host: deploy/hosts/fry.env
```

Configuration is environment only — see `docs/api.md` §6 and `mcs/config.py`. The model
worker's own settings are `MCS_*` too (`MCS_VLM_MODEL`, `MCS_WHISPER_MODEL`, `MCS_MODEL_PINNED`,
…); the front maps them onto the worker's `CFG_*` namespace when it spawns it.

## Develop

```bash
make venv && make test             # the front's tests: no models, no GPU
```

The tests script the model worker's socket protocol (`tests/conftest.py`), so every op is
exercised end to end without torch. `tests/test_probe_tools.py` runs against a real exiftool
when one is installed. `tests/caption_device_check.py` is the worker's own device-decision
table, run by pytest.

## Status

Phase 1 of the split from MURRiX (see MURRiX's `work/plans/mcs-v2.md`): the model primitives,
`media.probe`, `fingerprint.compute` and `document.extract`. Renditions, transcodes and the
`vision.describe` composite follow in later phases; `docs/api.md` describes all of it.

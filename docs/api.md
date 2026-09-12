# MCS v2 API

MCS (Media Cache Server) v2 is a stateless service that answers questions about media bytes and
produces media bytes: probing, renditions, transcodes, fingerprints, face detection, speech,
captions and descriptions, text embeddings and generation, document text extraction. It owns the
tools (ffmpeg, ImageMagick, exiftool, chromaprint, tesseract) and the models (a VLM, InsightFace,
MiniLM, whisper, NeMo diarization and voiceprints, LR-ASD) so that a caller never depends on which
tool or model answers.

This document is the contract. It is written to stand on its own: a caller with a directory of
files and an HTTP client can use every operation here without knowing anything about MURRiX.

Status: **draft for review**, 2026-09-12. Nothing below is implemented yet.

---

## 1. Principles

- **Stateless.** MCS holds no catalog, no ids, no cache of results. Every request carries
  everything the answer depends on; the same request gives the same answer for the same model
  and tool versions, and every answer says which versions produced it.
- **Files by path.** Inputs and outputs are absolute paths on a filesystem MCS can see. MCS reads
  the input where it is and writes the output where it is told, byte-exactly at that path. It
  never chooses names, never writes anywhere it was not asked to, and never cleans up after the
  caller.
- **Explicit parameters.** Anything that changes the answer is a request parameter: the display
  frame of an image, target sizes, prompts, language hints. MCS reads no sidecar, no database, no
  convention.
- **Normalized results with the raw answer attached.** Where a tool's output is the result
  (`media.probe`), MCS returns a normalized structure it will keep stable across tool changes, and
  the tool's raw output beside it under `raw.<tool>` for callers that need more. A caller reading
  `raw.*` accepts that it is bound to that tool.
- **Fine-grained ops, and composites where they make a better API.** `vision.caption` is the
  model; `vision.describe` is the useful thing. Both exist.
- **Consistent shapes.** Every file input looks the same, every long op streams the same events,
  every error has the same envelope.

## 2. Transport

HTTP/1.1, JSON request bodies, JSON responses. All operations are `POST /v2/<namespace>/<op>`
except `GET /v2/health` and `GET /v2/capabilities`.

```
POST /v2/faces/detect
Authorization: Bearer <key>
Content-Type: application/json

{ "file": { "path": "/files/S1/01K…jpg", "angle": 90 } }
```

Every request body may carry an `options` object:

| Field | Type | Meaning |
| --- | --- | --- |
| `priority` | `"interactive"` \| `"batch"` | `interactive` enters a model family's queue ahead of waiting batch work. Default `batch`. |
| `deadline` | number, epoch seconds | Work still queued past this instant is discarded and answered with `deadline_exceeded`. Work already running finishes. |
| `require_gpu` | boolean | Refuse rather than answer from a CPU fallback. |

### 2.1 Responses

Success:

```json
{ "ok": true, "result": { … }, "meta": { "took_ms": 412, "producer": "qwen3-vl-8b/nf4", "api": "2.0.0" } }
```

`meta.producer` names the model or tool that answered, with enough detail to detect a change —
a caller that stores results beside it can tell stale from current. `meta` may carry more
(`device`, `queued_ms`).

Failure (HTTP status 4xx/5xx and the same envelope):

```json
{ "ok": false, "error": { "code": "undecodable", "message": "moov atom not found", "permanent": true } }
```

`permanent: true` means the same request will fail the same way for these bytes — a truncated
container, an unsupported format, a path outside the roots. A caller should not retry it.
`permanent: false` (or absent) is transient: a queue full, a model still loading, a deadline.

### 2.2 Streaming long operations

Any op may be asked to stream by sending `Accept: text/event-stream`. The response is then a
server-sent event stream that ends with exactly one terminal event:

```
event: progress
data: {"phase":"decode","fraction":0.31,"message":"frame 4812/15500"}

event: log
data: {"level":"warn","message":"NVDEC has no decoder for dvvideo, decoding on the CPU"}

event: result
data: {"ok":true,"result":{…},"meta":{…}}
```

or `event: error` with the failure envelope. `fraction` is optional and monotonic when present;
`phase` names what is happening in the op's own vocabulary. Without `text/event-stream` the same
op blocks and returns the terminal envelope as its body — the two forms carry the same result.

**Closing the connection cancels the operation.** A running tool is killed, a queued model request
is discarded, and any output file the op had started writing is removed. That is the whole
cancellation API; there are no job ids.

Ops marked *long* below are the ones where streaming is worth asking for. Nothing stops a caller
streaming a short op.

### 2.3 Authentication

`Authorization: Bearer <key>`. Keys are lines in a keys file the operator points MCS at (as v1's
`mcs.keys`); there is one role. A missing or unknown key is `401 unauthorized`.

### 2.4 Versioning

The path prefix is the major version. `GET /v2/capabilities` reports `api` as a semver string:
minor versions add fields and ops, never remove or reinterpret; a breaking change is `/v3`. A
caller should refuse to run against a major it does not know and may warn on a minor below what
it was written for.

### 2.5 Admission

MCS bounds concurrent work per resource — one queue per model family, a slot count for
tool-bound work — and reports the bounds in `capabilities.limits`. A request that cannot be
queued is `503 busy` with `Retry-After`. Interactive requests get a small reserved share so a
crawl never starves a person waiting.

## 3. Files, roots and frames

### 3.1 Paths and roots

MCS is configured with **roots**: directories it may read (`ro`) or read and write (`rw`).
A request naming a path outside every root, or an output path under a read-only root, is
`403 path_outside_roots` (permanent). Paths are resolved after following symlinks, and the
resolved path must also lie under a root — a symlink that leaves the roots is refused. Roots are
listed in `capabilities.roots`.

An **output path** is written exactly as given. MCS writes through a private temporary file in
the same directory and renames it into place, so a reader never sees a partial file at the
output path; a failed op leaves nothing there. The directory must exist and be writable. The
extension does not choose the format — the request does — but MCS refuses a mismatch it can see
(`.avif` asked to hold `mp4`) as `invalid_request`.

### 3.2 File input

Every operation that reads media takes a `file` object:

```json
{ "path": "/files/S1/01K…", "angle": 90, "mirror": false, "mimetype": "image/heic" }
```

| Field | Type | Meaning |
| --- | --- | --- |
| `path` | string | Absolute path under a root. Required. |
| `angle` | 0 \| 90 \| 180 \| 270 | The turn still owed on these bytes to show them upright — see 3.3. Default 0. |
| `mirror` | boolean | Flip after rotating. Default false. |
| `mimetype` | string | A hint. MCS sniffs when absent, and trusts the sniff over a hint it contradicts. |

### 3.3 The display frame

Images and video frames are handled in the **display frame**: the stored raster rotated
counter-clockwise by `angle`, then flipped horizontally when `mirror` is set. MCS never applies
the file's own EXIF `Orientation` or a container's rotation matrix on its own; the caller states
the turn owed, and that is the only rotation applied. This is what lets a caller override a wrong
tag and be sure every op agrees.

Every coordinate MCS returns or accepts — face boxes, landmarks, crop boxes, tracks — is a
**fraction of the display frame** (`0..1`, origin top-left), so the same numbers apply to the
original and to every rendition of it. Where a result reports the frame it measured
(`frame: {width, height}`), those are the pixel dimensions of the display frame.

For formats whose decoder already applies a stored rotation (HEIF `irot`), MCS accounts for it
internally: the caller still states the *total* turn owed relative to what a naive viewer would
show, and MCS applies only the residual. That rule is what makes `angle` the same number for a
JPEG and a HEIC.

### 3.4 Output specifications

Ops that write files take `output` objects:

```json
{ "path": "/files-volatile/…/512x512.avif", "format": "avif", "quality": 58 }
```

The set of formats and their parameters is per op; `capabilities` lists what this MCS can encode.

## 4. Operations

Namespaces: `media`, `image`, `video`, `audio`, `fingerprint`, `faces`, `speech`, `vision`,
`text`, `document`, `system`.

### 4.1 `media.probe`

What is this file? One call, all tools.

Request: `{ "file": {…}, "raw": ["exiftool", "ffprobe"] }` — `raw` lists which tool outputs to
attach (default: all that ran).

Result:

```json
{
  "mimetype": "video/quicktime",
  "kind": "video",
  "size": 242120443,
  "sha256": "…",
  "container": "mov,mp4,m4a,3gp,3g2,mj2",
  "duration": 61.4,
  "bitrate": 31500000,
  "picture": { "width": 3840, "height": 2160, "codec": "hevc", "rotation": 90, "sar": 1, "fps": 29.97, "interlaced": false },
  "sound": { "codec": "aac", "channels": 2, "sample_rate": 48000 },
  "captured_at": [ { "value": "2025-09-27T15:26:32", "source": "DateTimeOriginal", "zone": "+02:00" } ],
  "gps": { "lat": 59.33, "lon": 18.07, "alt": 12, "dop": 1.2 },
  "device": { "make": "DJI", "model": "FC3582", "serial": "…" },
  "decodable": true,
  "raw": { "exiftool": { … }, "ffprobe": { … } }
}
```

- `kind` is `image | video | audio | document | other`.
- `picture.rotation` is what the *container* says (EXIF Orientation as degrees, or the display
  matrix) — reported, never applied. `picture.width/height` are the stored raster.
- `captured_at` is a list of candidates in the tool's own tag names, ordered by how much the
  tool trusts them; the caller decides. `zone` is present only when the file carries one.
- `decodable: false` comes with `reason` and means no decoder will ever get a frame out of these
  bytes (the container index is missing). It is a property of the file, so any later op on it
  fails `undecodable` with `permanent: true` without trying.
- `sha256` is computed only when asked (`"hash": true`); it is a full read.
- `raw.exiftool` is exiftool's numeric, ungrouped JSON (`-n -j`); `raw.ffprobe` is
  `-show_format -show_streams` JSON. Their tag names belong to those tools.

### 4.2 `image.renditions`

Several sized renditions of one image, from one decode. *long* for large sources.

```json
{
  "file": { "path": "…/IMG_1.CR2", "angle": 270 },
  "targets": [
    { "output": { "path": "…/320x320.avif", "format": "avif", "quality": 55 }, "box": { "width": 320, "height": 320 }, "fit": "cover" },
    { "output": { "path": "…/512x512.avif", "format": "avif", "quality": 58 }, "box": { "width": 512, "height": 512 }, "fit": "contain" },
    { "output": { "path": "…/face.avif",    "format": "avif", "quality": 60 }, "crop": { "x": 0.41, "y": 0.22, "width": 0.09, "height": 0.13 }, "pad": 0.5, "box": { "width": 512, "height": 512 }, "fit": "contain" }
  ]
}
```

- `box` is the bounding box in pixels of the display frame. `fit: contain` fits inside and
  **never upscales** (a small source lands at its own size); `fit: cover` fills the box exactly,
  cropping the centre, and does upscale.
- `crop` (fractions of the display frame) cuts first; `pad` grows the crop by that fraction of
  its own size on each side, clamped to the frame. The result is then fitted like any target.
- Output formats: `avif`, `webp`, `jpeg`, `png`. `quality` is the encoder's own scale.
- Renditions carry **no metadata** (EXIF stripped) and **no orientation tag** — the pixels are
  already upright, and a viewer applying a leftover tag would turn them back.

Result: `{ "frame": {"width", "height"}, "targets": [ { "path", "width", "height", "bytes" } ] }` —
the measured size of each written file, in order.

### 4.3 `image.decode`

The display frame as a plain image, for formats other tools cannot open (RAW, HEIF).

`{ "file": {…}, "output": { "path": "…/decoded.png", "format": "png" | "jpeg", "max": 4096 } }`
→ `{ "frame": {…}, "path" }`. `max` bounds the longer side.

### 4.4 `video.frames`

Frames as images. *long*.

```json
{ "file": {…}, "at": [1.0, 10.5, 40.0], "count": 5, "strategy": "representative",
  "output": { "dir": "/scratch/…", "format": "jpeg", "quality": 90, "max": 1280 } }
```

Either `at` (seconds) or `count` + `strategy` (`representative` picks visually distinct frames
across the file; `even` spaces them). Frames come out in the display frame with square pixels.
Result: `{ "frames": [ { "path", "t", "width", "height" } ] }`.

### 4.5 `video.poster`

One frame, fitted like a rendition target. `{ "file", "at": 1.0, "targets": [ … as 4.2 … ] }` →
as 4.2. A convenience over `video.frames` + `image.renditions` that decodes once.

### 4.6 `video.transcode` *long*

```json
{
  "file": { "path": "…/DJI_0114.MP4", "angle": 0 },
  "output": { "path": "…/1920x1080.mp4", "format": "mp4" },
  "video": { "codec": "av1", "box": { "width": 1920, "height": 1080 }, "quality": 32, "speed": 8, "deinterlace": false },
  "audio": { "codec": "aac", "bitrate": "128k" },
  "clip": { "start": 1.0, "duration": 20.0 },
  "hints": { "source_codec": "hevc" }
}
```

- `video.box` is a bounding box; the picture is fitted inside and never upscaled.
- `video.quality` is on the codec's own scale (AV1: 0–63, lower is better); `speed` likewise.
  MCS maps them onto whichever encoder it uses (hardware or software) so the same request
  produces comparable output on either.
- `audio: null` drops the sound; `clip` cuts before encoding.
- `hints` are optional facts the caller already knows that save a probe.

MCS chooses the decode/filter/encode chain (GPU where present and capable, CPU otherwise) and
falls back on its own; `meta.producer` names what ran (`ffmpeg-9.0.1/av1_nvenc`). Progress events
carry `fraction` by frames. A source MCS refuses to encode on the CPU because it would exceed
MCS's own memory budget is `refused` (permanent for that source and box).

Result: `{ "path", "width", "height", "duration", "bytes", "video_codec", "audio_codec" }`.

### 4.7 `audio.transcode`

`{ "file", "output": { "path": "…/128k-44100.m4a", "format": "m4a" }, "audio": { "codec": "aac", "bitrate": "128k", "sample_rate": 44100 } }`.
`bitrate` and `sample_rate` are **ceilings**: MCS never exceeds the source's own, and keeps mono
for a mono source. Result: `{ "path", "duration", "bytes", "bitrate", "sample_rate", "channels" }`.

### 4.8 `audio.extract`

The sound of any file as PCM, for callers with their own audio models.
`{ "file", "output": { "path": "…/audio.wav", "format": "wav", "sample_rate": 16000, "channels": 1 } }`
→ `{ "path", "duration" }`, or `{ "path": null, "duration": 0 }` when the file has no audio
stream — a normal answer, not an error.

### 4.9 `audio.waveform`

A rendered waveform picture. `{ "file", "targets": [ … as 4.2 … ], "style": { "foreground": "#…", "background": "#…", "aspect": 3 } }`.
`aspect` is the width:height the picture is drawn at for `contain` targets; `cover` targets are
drawn square. Result as 4.2.

### 4.10 `fingerprint.compute`

`{ "file", "kinds": ["phash-image", "phash-video", "phash-audio"] }` — default: every kind that
applies to the file's `kind`. Result:

```json
{ "fingerprints": [ { "kind": "phash-image", "dimension": 64, "vector": [1, -1, …], "label": "primary" } ] }
```

An audio too short to fingerprint yields no `phash-audio` entry rather than an error.

### 4.11 `faces.detect`

`{ "file": {…}, "min_size": 0.02 }` → boxes, landmarks and embeddings in one call:

```json
{ "frame": { "width": 4032, "height": 3024 },
  "faces": [ { "box": { "x": 0.41, "y": 0.22, "width": 0.09, "height": 0.13 }, "confidence": 0.93,
               "landmarks": [[0.44, 0.27], …], "embedding": [ … 512 floats … ] } ] }
```

`min_size` is the smallest face to report, as a fraction of the frame's shorter side.

### 4.12 `faces.embed`

An embedding for a face the caller already located: `{ "file", "box": {…} }` →
`{ "embedding", "confidence" }`, or `result: null` when no face is found inside the box.

### 4.13 `speech.transcribe` *long*

```json
{ "file": {…}, "language": "sv", "vad": { "min_silence_ms": 500 }, "word_timestamps": false }
```

Any container with sound; MCS extracts the audio itself. `language` is an ISO-639-1 hint that
skips detection (detection reads the first 30 s and applies its guess to the whole file).

```json
{ "speech": true, "language": "sv", "language_probability": 0.98, "duration": 61.4,
  "text": "…", "segments": [ { "start": 0.8, "end": 4.1, "text": "…", "words": [ { "start", "end", "word" } ] } ] }
```

`speech: false` comes with an empty `segments` and is a normal answer for a silent clip.

### 4.14 `speech.diarize` *long*

`{ "file" }` → speaker turns:

```json
{ "speech": true, "duration": 61.4, "window": 120,
  "turns": [ { "start": 0.8, "end": 4.1, "speaker": "w0/speaker_0" } ] }
```

The model runs in fixed windows, so `speaker` labels are **local to their window** — the same
person in two windows gets two labels, and a turn crossing a boundary arrives as two turns that
meet there. Turning that into one speaker per file is the caller's job (`speech.voiceprint` on
each turn, then cluster).

### 4.15 `speech.voiceprint`

`{ "file", "start": 12.48, "end": 17.84 }` → `{ "embedding": [ … ], "dimension": 192 }`, or
`result: null` when the span is too short to characterise a voice.

### 4.16 `speech.active_speaker`

Is the face in `track` the one talking between `start` and `end`?
`{ "file", "start", "end", "track": [ { "t": 12.5, "box": {…} }, … ] }` →
`{ "score": 0.81, "frames": 40 }`, or `result: null` when there is too little to judge (a track of
a few frames, a span with no audio). Silence is not evidence of not speaking, so it is no answer
rather than a low score.

### 4.17 `vision.caption`

The VLM, as a primitive: `{ "files": [ {…}, {…} ], "prompt": "…", "max_new_tokens": 128 }` →
`{ "captions": [ "…", "…" ] }`. A list, because frames of one video belong in one conversation
with the model; independent images should be sent as independent requests, which MCS may
microbatch on its own.

### 4.18 `vision.describe` *long*

A description of a photo, a video or a recording, in prose. The composite most callers want.

```json
{ "file": {…},
  "faces": [ { "box": {…} } ],
  "transcript": "…",
  "prompt": { "image": "…", "video": "…", "summary": "…" },
  "max_new_tokens": 128 }
```

- Routes on the file's kind. An image is captioned in the display frame. A video is captioned
  from representative frames and merged with what is said; a recording is described from what is
  said.
- `faces` (optional) grounds the caption in how many people there are and where, without
  naming anyone. `transcript` (optional) is used as given; without it MCS transcribes the file
  itself.
- `prompt` overrides MCS's defaults per stage; the defaults are versioned and named in
  `meta.producer`.

Result: `{ "description": "…", "language": "sv", "grounded_on": { "faces": 2 }, "stages": { "caption": "qwen3-vl-8b/nf4", "transcribe": "faster-whisper/large-v3" } }`.

### 4.19 `text.embed`

`{ "text": "…" }` → `{ "embedding": [ … ], "dimension": 384 }`. Normalized. The model is pinned:
changing it changes every stored vector's meaning, so it is a major-version event.

### 4.20 `text.generate`

`{ "messages": [ { "role": "system" | "user" | "assistant", "content": "…" } ], "max_new_tokens": 192, "json_only": false, "model": "instruct" | "vlm" }`
→ `{ "text": "…" }`. Greedy, so the same messages give the same answer. `json_only` returns the
first balanced JSON object in the completion — the caller still validates what is inside it.

### 4.21 `document.extract`

`{ "file", "ocr": "auto" | "always" | "never", "languages": ["swe", "eng"] }` →
`{ "text": "…", "pages": 5, "method": "pdf-text" | "ocr" | "docx" | "odf" }`. `auto` runs OCR
only when the document carries no usable text layer. A document that cannot be opened is
`unsupported` (permanent).

### 4.22 `system.health` — `GET /v2/health`

Liveness and what is loaded:

```json
{ "ok": true, "api": "2.0.0", "uptime": 86400, "gpu": { "present": true, "name": "…", "vram_mb": 16311, "vram_free_mb": 7900 },
  "models": { "vlm": { "loaded": true, "device": "cuda", "name": "…" }, "whisper": { "loaded": false }, … },
  "process": { "rss_mb": 6200, "max_rss_mb": 12000, "recycles": 0 },
  "degraded": [ { "what": "caption", "reason": "vram_full", "since": 1780000000 } ],
  "queues": { "vlm": { "running": 1, "waiting": 3 } } }
```

`degraded` lists silent fallbacks currently in effect (captioning pushed to the CPU by a full
card) — the things that are otherwise indistinguishable from "slow".

### 4.23 `system.capabilities` — `GET /v2/capabilities`

What this MCS can do, for a caller to check before it relies on it:

```json
{ "api": "2.0.0", "ops": [ "media.probe", … ],
  "roots": [ { "path": "/files", "mode": "ro" }, { "path": "/files-volatile", "mode": "rw" } ],
  "formats": { "image": ["avif", "webp", "jpeg", "png"], "video": ["mp4"], "audio": ["m4a", "wav"] },
  "encoders": { "av1": ["av1_nvenc", "libsvtav1"], "hevc": [] },
  "models": { "vlm": "Qwen/Qwen3-VL-8B-Instruct@nf4", "embed": "paraphrase-multilingual-MiniLM-L12-v2", … },
  "limits": { "vlm": 1, "faces": 2, "tools": 4, "interactive_reserve": 1 } }
```

## 5. Error codes

| Code | HTTP | Permanent | Meaning |
| --- | --- | --- | --- |
| `unauthorized` | 401 | — | Missing or unknown key |
| `invalid_request` | 400 | yes | Malformed body, unknown op, contradictory parameters |
| `path_outside_roots` | 403 | yes | Input or output path not under an allowed root |
| `not_found` | 404 | yes | Input path does not exist |
| `unsupported` | 415 | yes | MCS has no tool or model for this format or kind |
| `undecodable` | 422 | yes | The bytes cannot be opened by any decoder (see `media.probe`) |
| `refused` | 422 | yes | MCS will not do this work within its own budget (a CPU encode too large) |
| `busy` | 503 | no | Admission queue full; `Retry-After` set |
| `deadline_exceeded` | 504 | no | Discarded from the queue past `options.deadline` |
| `model_unavailable` | 503 | no | The model needed is loading or its device is unusable right now |
| `tool_failed` | 500 | no | A tool exited non-zero for a reason MCS could not classify; `message` carries its last lines |
| `internal` | 500 | no | MCS's own fault |

## 6. Configuration (operator)

Environment only. `MCS_KEYS_FILE`, `MCS_ROOTS` (`/files:ro,/old:ro,/files-volatile:rw,/files-tmp:rw`),
`MCS_PORT`, `MCS_SCRATCH`, the model choices and device policies (`MCS_VLM_MODEL`,
`MCS_VLM_QUANT`, `MCS_WHISPER_MODEL`, `MCS_MODEL_PINNED`, `MCS_MODEL_IDLE_EVICT`, `MCS_MAX_RSS`,
…) and the admission limits. Changing any of them is a restart. `capabilities` and `health` are
how a caller learns what a given MCS is running; there is no configuration API.

## 7. Appendix: the MURRiX mapping

Kept here so nothing MURRiX does today is lost in the port. MURRiX resolves a VFS path to its
physical one and sends that; it creates output nodes before the call and syncs them after; it
decides *when* anything runs. Nothing of that is MCS's business.

| MURRiX today | MCS op |
| --- | --- |
| `exif-extract` (exif service, `media-probe.ts`, `video-codec.ts`) | `media.probe` — the readers of `raw.exiftool` are `types/exif.ts`, `exif-date.ts`, `video-codec.ts`, the orientation rules in `exif-extract`; listed so a tool change knows where to look |
| `image-to-image`, `image-ladder.ts`, `generate-derivatives` | `image.renditions` |
| `detect-faces` crop, `face-crop-rebuild.ts`, `regenerate-face-crops` | `image.renditions` with `crop` |
| `decode-image.ts` (RAW/HEIC fallback) | `image.decode` — or nothing, since the model ops decode natively |
| `describe-file` keyframes, `voice-asd.ts` frame grabs | `video.frames` |
| `video-to-image` | `video.poster` |
| `video-to-video`, `video-concat`, `generate-version` | `video.transcode` |
| `audio-to-audio` | `audio.transcode` |
| `demux-audio.ts` | `audio.extract` (only when MURRiX needs the WAV itself; `speech.*` extract on their own) |
| `audio-to-image` | `audio.waveform` |
| `compute-fingerprint`, fingerprint service | `fingerprint.compute` |
| `detect-faces` (model half) | `faces.detect` |
| `face-transfer` / legacy tags | `faces.embed` |
| `transcribe-file` | `speech.transcribe` |
| `voiceprint-file` | `speech.diarize`, `speech.voiceprint`, `speech.active_speaker` |
| `describe-file` | `vision.describe` with `faces` from its face nodes and `transcript` from its transcript node |
| `album-describe`, `query-parse`, video summary | `text.generate` |
| `search-*`, `describe-file`, `index-document` embeddings | `text.embed` |
| `index-document` | `document.extract` |
| `inference-status`, `system-overview` | `system.health` |

Stays in MURRiX: node creation and attrs, `file-sync`, the temp-sibling swap of renditions,
face assignment and person minting, voice stitching and assignment, transcript chunking and VTT,
album summaries, query schemas, pipelines, pools and retries.

## 8. Open questions

- `media.probe`: should `captured_at` interpretation (which tag wins, zone handling) move into
  MCS as a normalized `captured_at_best`, or stay a caller rule? Proposed: stay a caller rule for
  now; MCS reports candidates.
- `image.renditions`: one decode per source is the point — should `targets` be capped, and should
  MCS refuse a `cover` target larger than the source rather than upscaling?
- `vision.describe`: the default prompts are MCS's. Is a caller-visible prompt version in
  `meta.producer` enough for a caller to know when to redo descriptions, or does it need a
  `prompt_version` field of its own?
- Streaming: SSE (`text/event-stream`) or NDJSON? SSE is proposed; both are trivial on both sides.

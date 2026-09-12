#!/usr/bin/env python3
"""gpu-selftest — prove the inference stack really runs on the GPU inside the murrix image.

The Containerfile's build-time check only asserts that CUDAExecutionProvider is *listed*.
That says nothing about whether kernels exist for the host's compute capability: on a
Blackwell card (sm_120) a cu124 torch imports fine, reports cuda available, and then dies
on the first real kernel launch. InsightFace is worse — it silently falls back to CPU and
you only notice via throughput.

So every check here runs an actual computation and inspects what the loaded model/session
ended up on. Exits non-zero on the first hard failure.

Run inside the image with the GPU passed through:

    podman run --rm --device nvidia.com/gpu=all -v "$PWD:/work:ro" \
        --entrypoint python3 murrix /work/scripts/gpu-selftest.py

--allow-cpu-faces downgrades a CPU-bound InsightFace from failure to warning (some
onnxruntime-gpu builds ship no sm_120 kernels; faces still work, just slower).
"""

import argparse
import sys
import traceback

import numpy as np

FAILURES: list[str] = []
WARNINGS: list[str] = []
SKIPPED: list[str] = []
ONLY: list[str] = []


def check(name):
    """Run a check, turning any exception into a recorded failure.

    Honours --only, which exists because the checks are not equal in cost: the VLM alone
    wants 6.43GB, so on a card that is already serving a crawl the whole suite cannot run,
    while verifying one model against the same image can.
    """

    def decorator(fn):
        if ONLY and not any(token.lower() in name.lower() for token in ONLY):
            SKIPPED.append(name)
            return fn
        print(f'\n=== {name} ===')
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a failed check must not stop the rest
            FAILURES.append(f'{name}: {exc}')
            print(f'FAIL {name}: {exc}')
            traceback.print_exc(limit=3)
        return fn

    return decorator


def synthetic_face_image() -> np.ndarray:
    """A crude frontal face on a plain background — enough to exercise the detector.

    Detection is not asserted (a synthetic blob may find zero faces); what matters is that
    the session runs on CUDA without raising.
    """
    import cv2

    img = np.full((480, 480, 3), 200, dtype=np.uint8)
    cv2.ellipse(img, (240, 250), (110, 145), 0, 0, 360, (170, 150, 135), -1)
    cv2.circle(img, (200, 215), 14, (60, 60, 60), -1)
    cv2.circle(img, (280, 215), 14, (60, 60, 60), -1)
    cv2.ellipse(img, (240, 320), (45, 22), 0, 0, 180, (90, 70, 70), -1)
    return img


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-cpu-faces', action='store_true',
                        help='treat an InsightFace CPU fallback as a warning, not a failure')
    parser.add_argument('--only', default='',
                        help='comma-separated substrings of check names to run (e.g. "nemo,lr-asd"); '
                             'the VLM check alone needs 6.43GB, so this is how one model is '
                             'verified on a card that is already busy')
    args = parser.parse_args()
    ONLY.extend(token.strip() for token in args.only.split(',') if token.strip())

    @check('torch / CUDA')
    def _torch():
        import torch

        print('torch:', torch.__version__, '| built for CUDA:', torch.version.cuda)
        if not torch.cuda.is_available():
            raise RuntimeError('torch.cuda.is_available() is False — no device passed through?')

        name = torch.cuda.get_device_name(0)
        major, minor = torch.cuda.get_device_capability(0)
        arch_list = torch.cuda.get_arch_list()
        print(f'device: {name} (sm_{major}{minor})')
        print('kernels built for:', ' '.join(arch_list))

        if f'sm_{major}{minor}' not in arch_list:
            # PTX for a lower arch can JIT forward, so this is not fatal on its own —
            # the matmul below is the real verdict.
            print(f'WARN: no native sm_{major}{minor} cubin; relying on PTX JIT')

        # The actual test: a kernel launch. A wheel without kernels for this arch raises
        # "CUDA error: no kernel image is available for execution on the device" here.
        a = torch.randn(512, 512, device='cuda')
        result = (a @ a.T).sum().item()
        torch.cuda.synchronize()
        if not np.isfinite(result):
            raise RuntimeError(f'cuda matmul produced a non-finite result: {result}')
        print(f'cuda matmul OK (checksum {result:.3f})')
        print(f'VRAM total: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB')

    @check('onnxruntime / InsightFace')
    def _insightface():
        import onnxruntime as ort
        from insightface.app import FaceAnalysis

        providers = ort.get_available_providers()
        print('onnxruntime:', ort.__version__, '| providers:', providers)
        if 'CUDAExecutionProvider' not in providers:
            raise RuntimeError(f'CUDAExecutionProvider missing — CPU wheel shadowing? {providers}')

        app = FaceAnalysis(name='buffalo_l', root='/opt/insightface',
                           providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
        app.prepare(ctx_id=0, det_size=(640, 640))

        # Ask the loaded sessions what they actually bound to, rather than trusting the
        # requested provider list — this is where a silent CPU fallback shows up.
        bound = {name: model.session.get_providers()[0] for name, model in app.models.items()}
        for name, provider in sorted(bound.items()):
            print(f'  {name}: {provider}')

        faces = app.get(synthetic_face_image())
        print(f'detection ran, {len(faces)} face(s) on the synthetic image')

        cpu_only = [n for n, p in bound.items() if p != 'CUDAExecutionProvider']
        if cpu_only:
            msg = f'InsightFace models on CPU: {cpu_only} (no sm_120 kernels in this onnxruntime-gpu build?)'
            if args.allow_cpu_faces:
                WARNINGS.append(msg)
                print('WARN:', msg)
            else:
                raise RuntimeError(msg)

    @check('VLM describe')
    def _vlm():
        import os
        import torch
        from PIL import Image
        from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

        model_id = os.environ.get('CFG_VLM_MODEL', 'Qwen/Qwen3-VL-8B-Instruct')
        quant = os.environ.get('CFG_VLM_QUANT', 'nf4')

        kwargs = {'device_map': 'cuda:0'}
        if quant == 'nf4':
            kwargs['quantization_config'] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
            )
        elif quant == 'int8':
            kwargs['quantization_config'] = BitsAndBytesConfig(load_in_8bit=True)
        else:
            kwargs['dtype'] = torch.bfloat16

        processor = AutoProcessor.from_pretrained(model_id)
        model = AutoModelForImageTextToText.from_pretrained(model_id, **kwargs).eval()

        device = next(model.parameters()).device
        print(f'{model_id} on:', device, '| quant:', quant)
        if device.type != 'cuda':
            raise RuntimeError(f'{model_id} landed on {device}, not cuda')

        # A real generate, not a load. Quantized kernels are exactly the class of thing that
        # imports cleanly, reports cuda, and then dies at the first kernel launch on sm_120 —
        # which is the failure this whole script exists to catch. An FP8 checkpoint, for one,
        # does not even get this far on transformers (see model_registry.VLM_QUANT).
        image = Image.fromarray(synthetic_face_image())
        messages = [{'role': 'user', 'content': [
            {'type': 'image', 'image': image},
            {'type': 'text', 'text': 'Describe this image in one short sentence.'},
        ]}]
        inputs = processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors='pt',
        ).to(model.device)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=32, do_sample=False)
        text = processor.batch_decode(
            out[:, inputs['input_ids'].shape[1]:], skip_special_tokens=True,
        )[0].strip()
        if not text:
            raise RuntimeError(f'{model_id} generated an empty caption')
        print(f'caption: {text!r}')
        print(f'VRAM in use: {torch.cuda.memory_allocated() / 1e9:.2f} GB')

    @check('sentence-transformers embed')
    def _embed():
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2', device='cuda')
        vec = model.encode(['en bild på en katt i solen'])
        print('embedding dim:', vec.shape[-1], '| device:', model.device)
        if str(model.device) == 'cpu':
            raise RuntimeError('sentence-transformers stayed on CPU')

    @check('faster-whisper transcribe')
    def _whisper():
        import subprocess
        import tempfile

        from faster_whisper import WhisperModel

        model = WhisperModel('base', device='cuda', compute_type='float16')
        print('whisper base loaded on cuda')

        # Constructing the model is NOT enough: CTranslate2 resolves cuBLAS/cuDNN through the
        # system linker lazily, on the first inference. A CUDA-13 image (where CTranslate2's
        # libcublas.so.12 is missing) loads the model happily and only then fails with
        # "Library libcublas.so.12 is not found or cannot be loaded". So actually decode.
        with tempfile.TemporaryDirectory() as tmp:
            wav = f'{tmp}/tone.wav'
            subprocess.run(
                ['ffmpeg', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                 '-ar', '16000', '-ac', '1', '-y', wav],
                check=True, capture_output=True,
            )
            # The same decode arguments inference_ops.transcribe uses, because they are
            # what can fail on their own. vad_filter in particular loads a *second* model
            # — the Silero VAD ONNX — which the image prewarms separately; if that asset is
            # missing this is where it shows, rather than inside a pipeline step with no
            # network. A sine tone has no speech, so the VAD is expected to return nothing.
            segments, info = model.transcribe(
                wav,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            list(segments)  # generator — nothing runs until it is consumed

            # And once over speech, to prove segments come back timed. Espeak is not in the
            # image, so the "speech" is a frequency sweep: whisper will hear something and
            # hallucinate words for it, which is fine — what is under test is the shape of
            # what comes back, not the words.
            speech = f'{tmp}/sweep.wav'
            subprocess.run(
                ['ffmpeg', '-f', 'lavfi', '-i', 'sine=frequency=200:duration=3',
                 '-af', 'vibrato=f=8:d=0.9,volume=3', '-ar', '16000', '-ac', '1', '-y', speech],
                check=True, capture_output=True,
            )
            # word_timestamps on, whatever the deployment's default is: the shape of what it
            # returns is what inference_ops.transcribe reads to give resolveSpeakers
            # something to cut a cue on, and a silent change to it would only surface as
            # subtitles that stopped naming the right speaker.
            timed, _timed_info = model.transcribe(
                speech, beam_size=1, condition_on_previous_text=False, word_timestamps=True,
            )
            timed = list(timed)
            for segment in timed:
                if segment.start is None or segment.end is None:
                    raise RuntimeError('whisper returned a segment with no timings')
                if segment.end < segment.start:
                    raise RuntimeError(f'whisper segment ends before it starts: {segment.start} -> {segment.end}')
            print(f'timed segments: {len(timed)}')

            worded = [s for s in timed if getattr(s, 'words', None)]
            if timed and not worded:
                raise RuntimeError('word_timestamps=True returned segments carrying no words')
            for segment in worded:
                for word in segment.words:
                    if word.start is None or word.end is None or not str(word.word):
                        raise RuntimeError(f'whisper returned a malformed word: {word!r}')
            if worded:
                sample = worded[0].words[0]
                print(f'word timings present, e.g. {sample.word!r} at {sample.start:.2f}-{sample.end:.2f}s')

        if not info.language:
            raise RuntimeError('whisper reported no detected language')
        print(f'transcribe ran on cuda (detected language: {info.language})')

    @check('NeMo diarization / voiceprint')
    def _voice():
        import subprocess
        import tempfile

        import torch
        from nemo.collections.asr.models import EncDecSpeakerLabelModel, SortformerEncLabelModel

        # Loading is not the test — NeMo sits on lightning/hydra and on the same torch the
        # rest of the image uses, so what can break is an import or a kernel, not a download.
        diarizer = SortformerEncLabelModel.from_pretrained('nvidia/diar_sortformer_4spk-v1')
        diarizer.eval().to('cuda')
        device = next(diarizer.parameters()).device
        print('sortformer on:', device)
        if device.type != 'cuda':
            raise RuntimeError(f'sortformer landed on {device}, not cuda')

        with tempfile.TemporaryDirectory() as tmp:
            # A frequency sweep, not silence: the VAD in front of the diarizer would drop
            # pure silence and the model would never run a kernel at all, which is exactly
            # the load-only pass this script exists to avoid.
            wav = f'{tmp}/sweep.wav'
            subprocess.run(
                ['ffmpeg', '-f', 'lavfi', '-i', 'sine=frequency=200:duration=8',
                 '-af', 'vibrato=f=8:d=0.9,volume=3', '-ar', '16000', '-ac', '1', '-y', wav],
                check=True, capture_output=True,
            )
            # Shape, not content: a sweep is not speech, so zero turns is a fine answer.
            turns = diarizer.diarize(audio=[wav], batch_size=1)
            if not isinstance(turns, list) or not turns:
                raise RuntimeError(f'diarize returned {turns!r}, expected one entry per input')
            print(f'diarize ran, {len(turns[0])} turn(s) on a synthetic sweep')

            # TitaNet runs on the CPU in production (see model_registry.TITANET_DEVICE), so
            # that is what is checked here — a CUDA-only check would test what never runs.
            embedder = EncDecSpeakerLabelModel.from_pretrained(
                'nvidia/speakerverification_en_titanet_large',
            )
            embedder.eval().to('cpu')
            vector = embedder.get_embedding(wav).reshape(-1)
            print('voiceprint dim:', vector.shape[0], '| device: cpu')
            if vector.shape[0] != 192:
                raise RuntimeError(f'expected a 192-dim voiceprint, got {vector.shape[0]}')
            if not torch.isfinite(vector).all():
                raise RuntimeError('voiceprint contains non-finite values')

    @check('LR-ASD active speaker')
    def _asd():
        import os

        import torch

        lr_home = os.environ.get('LR_ASD_HOME', '/opt/lr-asd')
        weight = os.path.join(lr_home, 'weight', 'finetuning_TalkSet.model')
        if not os.path.exists(weight):
            raise RuntimeError(f'LR-ASD weights missing at {weight}')
        sys.path.insert(0, lr_home)
        from model.Model import ASD_Model

        model = ASD_Model()
        state = torch.load(weight, map_location='cpu')
        backbone = {k[len('model.'):]: v for k, v in state.items() if k.startswith('model.')} or state
        model.load_state_dict(backbone, strict=False)
        model.eval().to('cuda')

        # 3s of video at 25fps: 75 grayscale 112x112 face crops, with MFCC at 4x that rate.
        frames = 75
        visual = torch.rand(1, frames, 112, 112, device='cuda') * 255
        audio = torch.rand(1, frames * 4, 13, device='cuda')
        with torch.no_grad():
            out_av, _out_v = model(audio, visual)
        torch.cuda.synchronize()
        if not torch.isfinite(out_av).all():
            raise RuntimeError('LR-ASD produced non-finite scores')
        print(f'LR-ASD forward OK on cuda, output {tuple(out_av.shape)}')

    print('\n' + '=' * 60)
    if SKIPPED:
        print(f'skipped {len(SKIPPED)} check(s) not matching --only: {", ".join(SKIPPED)}')
    for warning in WARNINGS:
        print('WARN:', warning)
    if FAILURES:
        print(f'FAILED ({len(FAILURES)}):')
        for failure in FAILURES:
            print('  -', failure)
        return 1
    print('all GPU checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

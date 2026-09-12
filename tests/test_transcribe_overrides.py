"""`inference_ops.transcribe` with the whisper model stubbed: the per-request overrides reach the
decode call and the signature, and the configured values apply when a request names none.

This is the test that would have caught the shadowed-local bug the first fry2 transcription
found: the decode call is exercised for real, only the model behind it is fake.
"""

import os
import sys
import types
from contextlib import nullcontext

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcs", "modelworker"))

import inference_ops as ops  # noqa: E402
import model_registry as registry  # noqa: E402


class FakeSegment:
    def __init__(self, start, end, text, words=None):
        self.start, self.end, self.text = start, end, text
        self.no_speech_prob = 0.1
        self.words = words


class FakeModel:
    def __init__(self, calls):
        self.calls = calls

    def transcribe(self, path, **kwargs):
        self.calls.append(kwargs)
        info = types.SimpleNamespace(language="sv", language_probability=0.9, duration=3.0)
        return iter([FakeSegment(0.0, 1.5, "hej"), FakeSegment(1.5, 3.0, "")]), info


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    calls: list[dict] = []
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(registry, "whisper", lambda device, compute_type: FakeModel(calls))
    monkeypatch.setattr(registry, "family_lock", lambda family: nullcontext())
    monkeypatch.setattr(registry, "run_on_model_thread", lambda family, fn: fn())
    monkeypatch.setattr(registry, "release", lambda key: None)
    monkeypatch.setattr(registry, "WHISPER_LANGUAGE", "")
    monkeypatch.setattr(registry, "WHISPER_WORD_TIMESTAMPS", False)
    monkeypatch.setattr(registry, "whisper_vad_parameters", lambda: {"threshold": 0.2, "speech_pad_ms": 800, "min_silence_duration_ms": 2000})
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    return str(audio), calls


def test_defaults_come_from_the_configuration(stubbed):
    path, calls = stubbed
    result = ops.transcribe(path)
    assert calls[0]["language"] is None
    assert calls[0]["vad_parameters"]["min_silence_duration_ms"] == 2000
    assert calls[0]["word_timestamps"] is False
    assert result["speech"] is True and result["text"] == "hej" and len(result["segments"]) == 1
    assert result["language"] == "sv"
    assert result["signature"].endswith(",lang=auto,words=0")
    assert result["signature"] == ops.transcribe_signature()


def test_overrides_reach_the_decode_and_the_signature(stubbed):
    path, calls = stubbed
    result = ops.transcribe(path, language="sv", min_silence_ms=500, word_timestamps=True)
    assert calls[0]["language"] == "sv"
    assert calls[0]["vad_parameters"]["min_silence_duration_ms"] == 500
    assert calls[0]["word_timestamps"] is True
    assert ":vad=0.2/800/500," in result["signature"]
    assert result["signature"].endswith(",lang=sv,words=1")
    # The configured signature is unchanged by a request's overrides.
    assert ops.transcribe_signature().endswith(",lang=auto,words=0")

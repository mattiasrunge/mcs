"""`document.extract` dispatch: what is refused before any tool runs."""

import pytest

from mcs.errors import McsError, UNSUPPORTED
from mcs.ops.document import extract


def test_raw_is_refused_permanently(tmp_path):
    # A DNG used to reach PIL, whose libtiff reader raised a plain OSError — transient by
    # classification, so the caller retried a file that can never yield text.
    f = tmp_path / "shot.dng"
    f.write_bytes(b"II*\x00" + b"\x00" * 64)
    with pytest.raises(McsError) as raised:
        extract(str(f), "image/x-adobe-dng", "auto", "eng")
    assert raised.value.code is UNSUPPORTED
    assert raised.value.permanent is True


def test_unknown_type_is_refused_permanently(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00")
    with pytest.raises(McsError) as raised:
        extract(str(f), "application/octet-stream", "auto", "eng")
    assert raised.value.code is UNSUPPORTED



class _FakeImage:
    format = "PNG"

    def convert(self, _mode):
        return self


def _fake_ocr(monkeypatch, image_to_data):
    """The venv has neither PIL nor pytesseract; extract_image imports both lazily."""
    import sys
    import types

    pil = types.ModuleType("PIL")
    pil.Image = types.SimpleNamespace(open=lambda _path: _FakeImage(), MAX_IMAGE_PIXELS=None)
    pil.ImageFile = types.SimpleNamespace(LOAD_TRUNCATED_IMAGES=False)
    tess = types.ModuleType("pytesseract")
    tess.image_to_data = image_to_data
    tess.Output = types.SimpleNamespace(DICT="dict")
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "pytesseract", tess)


def test_ocr_that_runs_out_of_time_is_no_text(monkeypatch):
    # The orphaned-tesseract case: pytesseract kills the binary at OCR_TIMEOUT_SECONDS and
    # raises. For a photo that is the honest answer — nothing legible — not a failure to
    # retry three times.
    from mcs.ops import document

    seen = {}

    def slow(*args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise RuntimeError("Tesseract process timeout")

    _fake_ocr(monkeypatch, slow)
    assert document.extract_image("/nowhere/wall.jpg", "eng") == ("", 0, "ocr")
    assert seen["timeout"] == document.OCR_TIMEOUT_SECONDS


def test_other_tesseract_errors_still_surface(monkeypatch):
    from mcs.ops import document

    def broken(*args, **kwargs):
        raise RuntimeError("tesseract exploded")

    _fake_ocr(monkeypatch, broken)
    with pytest.raises(RuntimeError):
        document.extract_image("/nowhere/wall.jpg", "eng")

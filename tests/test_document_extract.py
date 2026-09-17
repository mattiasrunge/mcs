"""`document.extract` dispatch: what is refused before any tool runs, and how OCR is bounded."""

import asyncio

import pytest

from mcs.errors import McsError, TOOL_FAILED, UNSUPPORTED
from mcs.ops import document
from mcs.ops.document import extract, parse_tsv
from mcs.tools.run import Completed


async def test_raw_is_refused_permanently(tmp_path):
    # A DNG used to reach PIL, whose libtiff reader raised a plain OSError — transient by
    # classification, so the caller retried a file that can never yield text.
    f = tmp_path / "shot.dng"
    f.write_bytes(b"II*\x00" + b"\x00" * 64)
    with pytest.raises(McsError) as raised:
        await extract(str(f), "image/x-adobe-dng", "auto", "eng")
    assert raised.value.code is UNSUPPORTED
    assert raised.value.permanent is True


async def test_unknown_type_is_refused_permanently(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00")
    with pytest.raises(McsError) as raised:
        await extract(str(f), "application/octet-stream", "auto", "eng")
    assert raised.value.code is UNSUPPORTED


TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t96.5\tHello\n"
    "5\t1\t1\t1\t1\t2\t12\t0\t10\t10\t91\tworld\n"
    "5\t1\t1\t1\t2\t1\t0\t12\t10\t10\t12\tnoise\n"
)


def test_parse_tsv_gives_typed_columns():
    data = parse_tsv(TSV)
    assert data["text"] == ["", "Hello", "world", "noise"]
    assert data["conf"] == [-1.0, 96.5, 91.0, 12.0]
    assert data["line_num"] == [0, 1, 1, 2]
    assert parse_tsv("")["text"] == []


def _tesseract_runs(monkeypatch, outcome):
    """`tools.run.run` as document sees it: `outcome` is a Completed, or an exception to raise."""
    calls = []

    async def fake_run(args, *, timeout, **kwargs):
        calls.append((args, timeout))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(document, "run", fake_run)
    monkeypatch.setattr(document, "require", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(document, "_ocr_source", lambda path: (path, False))
    return calls


async def test_ocr_reads_confident_words(monkeypatch):
    calls = _tesseract_runs(monkeypatch, Completed(["tesseract"], 0, TSV.encode(), b""))
    monkeypatch.setattr(document, "MIN_IMAGE_TEXT_CHARS", 4)
    assert await document.extract_image("/nowhere/sign.jpg", "swe+eng") == ("Hello world", 0, "ocr")
    args, timeout = calls[0]
    assert args[1:] == ["/nowhere/sign.jpg", "stdout", "-l", "swe+eng", "tsv"]
    assert timeout == document.OCR_TIMEOUT_SECONDS


async def test_ocr_that_runs_out_of_time_is_no_text(monkeypatch):
    # The orphaned-tesseract case: `run` kills the process group at OCR_TIMEOUT_SECONDS. For a
    # photo that is the honest answer — nothing legible — not a failure to retry three times.
    _tesseract_runs(monkeypatch, asyncio.TimeoutError())
    assert await document.extract_image("/nowhere/wall.jpg", "eng") == ("", 0, "ocr")


async def test_cancellation_still_reaches_the_caller(monkeypatch):
    # A client that hangs up cancels the op's task; `run` has already killed tesseract, and the
    # cancellation must keep unwinding rather than be swallowed as "no text".
    _tesseract_runs(monkeypatch, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await document.extract_image("/nowhere/wall.jpg", "eng")


async def test_a_failed_run_is_a_tool_failure(monkeypatch):
    _tesseract_runs(monkeypatch, Completed(["tesseract"], 1, b"", b"Error in pixReadStream"))
    with pytest.raises(McsError) as raised:
        await document.extract_image("/nowhere/wall.jpg", "eng")
    assert raised.value.code is TOOL_FAILED
    assert "pixReadStream" in raised.value.message

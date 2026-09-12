"""`document.extract` — the text of a PDF, DOCX, ODT, image (OCR) or plain-text file.

A scanned PDF has no text layer, so `get_text()` returns "" for every page and the document
would be indexed as empty — no error, simply never findable. `ocr: auto` rasterizes any page
that yields (almost) nothing and runs Tesseract on it; `always` OCRs every page; `never` reads
only the text layer.

OCR on a photo answers with pages of plausible-looking garbage rather than with nothing, so an
image's OCR keeps only confident multi-character words and is reported as empty text below a
floor of alphanumerics. Both conditions are needed: noise scores very high on single glyphs.
"""

from __future__ import annotations

import asyncio
import io
import os
from typing import Literal

from fastapi import APIRouter, Request

from ..context import Context
from ..errors import McsError, TOOL_FAILED, UNSUPPORTED
from ..schemas import FileRef, WithOptions
from ..streaming import Outcome, Progress, run_op
from ..tools.versions import tool_version
from .media import kind_of

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ODT = "application/vnd.oasis.opendocument.text"

OCR_DPI = int(os.environ.get("MCS_OCR_DPI", "200"))
MIN_PAGE_TEXT_CHARS = int(os.environ.get("MCS_OCR_MIN_PAGE_CHARS", "32"))
MAX_OCR_PAGES = int(os.environ.get("MCS_OCR_MAX_PAGES", "40"))
MIN_IMAGE_TEXT_CHARS = int(os.environ.get("MCS_OCR_MIN_IMAGE_CHARS", "12"))
MIN_WORD_CONF = float(os.environ.get("MCS_OCR_MIN_WORD_CONF", "60"))
MIN_WORD_CHARS = 2
DEFAULT_LANGUAGES = ("swe", "eng")

# Failures that are a property of the file, not of the run. Named by module.class so this
# costs no imports; deliberately narrow — anything unrecognised stays transient and loud.
PERMANENT_ERRORS = frozenset([
    "PIL.UnidentifiedImageError",
    "pymupdf.FileDataError",
    "pymupdf.EmptyFileError",
    "zipfile.BadZipFile",
])


class DocumentRequest(WithOptions):
    file: FileRef
    ocr: Literal["auto", "always", "never"] = "auto"
    languages: list[str] | None = None


def is_permanent(exc: Exception) -> bool:
    return f"{type(exc).__module__}.{type(exc).__name__}" in PERMANENT_ERRORS


def _ocr_page(page, index: int, lang: str) -> str:
    import fitz
    import pytesseract
    from PIL import Image

    try:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(OCR_DPI / 72, OCR_DPI / 72))
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        return pytesseract.image_to_string(image, lang=lang).strip()
    except Exception:  # noqa: BLE001 - OCR is a bonus, never fatal
        return ""


def extract_pdf(path: str, ocr: str, lang: str) -> tuple[str, int, str]:
    import fitz

    doc = fitz.open(path)
    pages = len(doc)
    parts: list[str] = []
    ocr_pages = 0
    for index, page in enumerate(doc):
        text = page.get_text().strip() if ocr != "always" else ""
        wants_ocr = ocr == "always" or (ocr == "auto" and len(text) < MIN_PAGE_TEXT_CHARS)
        if wants_ocr and ocr_pages < MAX_OCR_PAGES:
            recognised = _ocr_page(page, index, lang)
            if len(recognised) > len(text):
                text = recognised
                ocr_pages += 1
        if text:
            parts.append(text)
    doc.close()
    return "\n\n".join(parts), pages, "ocr" if ocr_pages and ocr_pages * 2 >= max(1, pages) else "pdf-text"


def extract_docx(path: str) -> tuple[str, int, str]:
    from docx import Document

    doc = Document(path)
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n\n".join(parts), 0, "docx"


def extract_odt(path: str) -> tuple[str, int, str]:
    from odf.opendocument import load
    from odf.text import P

    doc = load(path)
    parts = []
    for elem in doc.getElementsByType(P):
        text = "".join(getattr(node, "data", None) or str(node) for node in elem.childNodes)
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts), 0, "odf"


def _confident_text(data: dict) -> str:
    lines: dict[tuple, list[str]] = {}
    for index, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word or float(data["conf"][index]) < MIN_WORD_CONF:
            continue
        if sum(c.isalnum() for c in word) < MIN_WORD_CHARS:
            continue
        key = (data["page_num"][index], data["block_num"][index], data["par_num"][index], data["line_num"][index])
        lines.setdefault(key, []).append(word)
    return "\n".join(" ".join(words) for _, words in sorted(lines.items())).strip()


def extract_image(path: str, lang: str) -> tuple[str, int, str]:
    import pytesseract
    from PIL import Image, ImageFile
    from pytesseract import Output

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    # A high-dpi album scan trips PIL's decompression-bomb guard, which exists for untrusted
    # uploads this service never sees. Bounded rather than disabled so a corrupt header fails.
    Image.MAX_IMAGE_PIXELS = 500_000_000
    image = Image.open(path)
    try:
        data = pytesseract.image_to_data(image, lang=lang, output_type=Output.DICT)
    except TypeError:
        # pytesseract accepts a fixed allowlist of PIL formats (MPO-wrapped JPEGs are not on
        # it); convert() clears .format and it re-encodes as PNG instead.
        data = pytesseract.image_to_data(image.convert("RGB"), lang=lang, output_type=Output.DICT)
    text = _confident_text(data)
    if sum(c.isalnum() for c in text) < MIN_IMAGE_TEXT_CHARS:
        return "", 0, "ocr"
    return text, 0, "ocr"


def extract_plaintext(path: str) -> tuple[str, int, str]:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read().strip(), 0, "text"


def extract(path: str, mimetype: str | None, ocr: str, lang: str) -> tuple[str, int, str]:
    if mimetype == "application/pdf":
        return extract_pdf(path, ocr, lang)
    if mimetype == DOCX:
        return extract_docx(path)
    if mimetype == ODT:
        return extract_odt(path)
    if mimetype and mimetype.startswith("image/"):
        if ocr == "never":
            return "", 0, "ocr"
        return extract_image(path, lang)
    if mimetype and mimetype.startswith("text/"):
        return extract_plaintext(path)
    raise McsError(UNSUPPORTED, f"no text extractor for {mimetype or 'an unknown type'}")


async def run_extract(ctx: Context, req: DocumentRequest, progress: Progress) -> Outcome:
    path = ctx.roots.input(req.file.path)
    mimetype = req.file.mimetype
    lang = "+".join(req.languages or DEFAULT_LANGUAGES)
    async with ctx.admission.slot("tools", interactive=req.options.interactive):
        if not mimetype and ctx.exiftool is not None:
            try:
                mimetype = str((await ctx.exiftool.extract(path)).get("MIMEType") or "")
            except McsError:
                mimetype = None
        if not mimetype:
            import mimetypes

            mimetype = mimetypes.guess_type(path)[0]
        await progress.emit("extract", message=kind_of(mimetype))
        try:
            text, pages, method = await asyncio.to_thread(extract, path, mimetype, req.ocr, lang)
        except McsError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified below
            raise McsError(TOOL_FAILED, str(exc) or type(exc).__name__, permanent=is_permanent(exc)) from exc
    result = {"text": text, "pages": pages, "method": method, "word_count": len(text.split())}
    tess = await tool_version("tesseract") if method == "ocr" else None
    return Outcome(result, producer=f"tesseract/{tess}" if tess else method)


def register(router: APIRouter, ctx: Context) -> None:
    @router.post("/document/extract")
    async def document_extract(request: Request, body: DocumentRequest):
        return await run_op(request, lambda progress: run_extract(ctx, body, progress))

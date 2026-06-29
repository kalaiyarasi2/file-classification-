"""
digital_extractor.py - Text extraction for digital (text-layer) PDFs.

Flow per PDF:
  1. Open with pdfplumber (primary) or PyMuPDF (fallback).
  2. For each of the first `max_pages` pages:
       a. Detect rotation using text-block geometry (height > width -> vertical).
       b. If rotated, apply correction in-memory before reading text.
       c. Extract text from the (corrected) page.
  3. Return combined text + a rotation summary.

This module is only for PDFs that have a real text layer.
For image-only (scanned) PDFs use scanned_extractor.py instead.
"""

from __future__ import annotations

import logging
from pathlib import Path

import re

logger = logging.getLogger(__name__)

# Minimum number of text characters on a page to consider it "digital"
DIGITAL_TEXT_MIN_CHARS = 30

# If more than this fraction of chars are inside (cid:N) sequences, treat as garbage
_CID_GARBAGE_THRESHOLD = 0.20

# Minimum meaningful chars per page - below this, the text layer is too sparse
# (e.g. only barcodes/page-numbers with all actual content in images)
_MIN_CHARS_PER_PAGE = 150


# ── CID garbage detection ─────────────────────────────────────────────────────

def _is_cid_garbage(text: str) -> bool:
    """
    Return True if the extracted text is predominantly unreadable garbage.

    Two patterns are detected:

    1. ``(cid:N)`` sequences - pdfplumber's representation of CID-encoded chars
       when the PDF font has no ToUnicode map.
    2. Non-printable Unicode control characters - PyMuPDF decodes the same
       broken fonts as raw codepoints (Unicode category 'Cc'/'Cs') instead of
       the ``(cid:N)`` text form.

    If either pattern accounts for more than _CID_GARBAGE_THRESHOLD of the
    non-whitespace content the text is considered unreadable and OCR should
    be used instead.
    """
    if not text:
        return False

    import unicodedata

    # Pattern 1: literal (cid:N) tokens (pdfplumber output)
    cid_token_chars = sum(len(m.group()) for m in re.finditer(r'\(cid:\d+\)', text))

    # Pattern 2: non-printable control characters (PyMuPDF output)
    control_chars = sum(
        1 for c in text
        if unicodedata.category(c) in ('Cc', 'Cs', 'Co')  # control / surrogate / private-use
        and c not in ('\n', '\r', '\t')                    # keep normal whitespace
    )

    total_nonspace = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
    if total_nonspace == 0:
        return False

    garbage_chars = cid_token_chars + control_chars
    ratio = garbage_chars / total_nonspace
    logger.debug(
        "[cid-check] Garbage ratio: %.1f%% (cid_tokens=%d, control=%d / total=%d)",
        ratio * 100, cid_token_chars, control_chars, total_nonspace,
    )
    return ratio > _CID_GARBAGE_THRESHOLD


# ── Sparse text detection ───────────────────────────────────────────────────

def _is_too_sparse(text: str, pages_read: int) -> bool:
    """
    Return True when the digital text layer contains too little content to be
    useful, even though it technically has some text (e.g. only barcodes,
    page numbers, or reference IDs).

    PDFs like ACORD insurance forms are sometimes saved with a thin text layer
    (barcodes, dates) while the actual form fields are image-based.  The text
    passes the CID check but is far too short to classify correctly.

    Threshold: fewer than _MIN_CHARS_PER_PAGE printable characters per page.
    """
    if pages_read <= 0:
        return False
    printable = sum(1 for c in text if c.isprintable() and c not in (' ', '\t'))
    chars_per_page = printable / pages_read
    logger.debug(
        "[sparse-check] %.0f printable chars/page (%d chars, %d pages)",
        chars_per_page, printable, pages_read,
    )
    return chars_per_page < _MIN_CHARS_PER_PAGE


# ── Rotation detection (text-block geometry) ──────────────────────────────────

def _detect_rotation_fitz(page) -> int:
    """
    Detect whether a PyMuPDF page needs rotation by analysing text-block shapes.

    If more text blocks are taller than wide the content is likely rotated 90 deg.

    Returns:
        90 if rotation is likely needed, 0 otherwise.
    """
    blocks = page.get_text("blocks")  # list of (x0,y0,x1,y1,text,...)
    vertical = 0
    horizontal = 0

    for block in blocks:
        x0, y0, x1, y1 = block[:4]
        width  = abs(x1 - x0)
        height = abs(y1 - y0)
        if height > width:
            vertical += 1
        else:
            horizontal += 1

    if vertical > horizontal and (vertical + horizontal) > 0:
        logger.debug("Rotation detected: vertical=%d horizontal=%d -> 90 deg", vertical, horizontal)
        return 90
    return 0


# ── pdfplumber extraction (primary) ───────────────────────────────────────────

def _extract_with_pdfplumber(file_path: Path, max_pages: int) -> tuple[str, str]:
    """
    Extract text from a digital PDF using pdfplumber.

    Rotation is applied via PyMuPDF in-memory before pdfplumber reads each page,
    because pdfplumber does not expose a rotate API.

    Returns:
        (combined_text, error_message)
    """
    try:
        import fitz        # type: ignore
        import pdfplumber  # type: ignore
    except ImportError as exc:
        return "", f"Missing dependency: {exc}"

    pages_text: list[str] = []
    rotation_log: list[str] = []

    # Open with both libraries simultaneously
    fitz_doc = fitz.open(str(file_path))

    with pdfplumber.open(str(file_path)) as plumber_doc:
        limit = min(max_pages, len(plumber_doc.pages)) if max_pages else len(plumber_doc.pages)

        for i in range(limit):
            fitz_page   = fitz_doc[i]
            plumber_page = plumber_doc.pages[i]

            # 1. Detect rotation
            angle = _detect_rotation_fitz(fitz_page)

            if angle != 0:
                # Apply rotation on the fitz page so we can log it;
                # pdfplumber reads the underlying PDF directly - re-open
                # a rotated copy in-memory for reliable text extraction.
                fitz_page.set_rotation(angle)
                rotation_log.append(f"page {i+1}: rotated {angle} deg")
                logger.info("[digital] %s page %d -> rotated %d deg", file_path.name, i + 1, angle)

                # Save rotated doc to bytes and re-open with pdfplumber
                rotated_bytes = fitz_doc.tobytes()
                import io
                with pdfplumber.open(io.BytesIO(rotated_bytes)) as rotated_doc:
                    page_text = rotated_doc.pages[i].extract_text() or ""
            else:
                page_text = plumber_page.extract_text() or ""

            pages_text.append(page_text)
            logger.debug("[digital] page %d: %d chars extracted", i + 1, len(page_text))

    fitz_doc.close()

    rotation_summary = (", ".join(rotation_log)) if rotation_log else "no rotation needed"
    combined = "\n".join(pages_text)
    return combined, rotation_summary


# ── PyMuPDF extraction (fallback) ─────────────────────────────────────────────

def _extract_with_fitz(file_path: Path, max_pages: int) -> tuple[str, str]:
    """
    Fallback extractor using PyMuPDF get_text().
    Also applies in-memory rotation correction before extracting.

    Returns:
        (combined_text, error_message)
    """
    try:
        import fitz  # type: ignore
    except ImportError:
        return "", "PyMuPDF (fitz) not installed."

    pages_text: list[str] = []
    rotation_log: list[str] = []

    doc = fitz.open(str(file_path))
    limit = min(max_pages, len(doc)) if max_pages else len(doc)

    for i in range(limit):
        page  = doc[i]
        angle = _detect_rotation_fitz(page)

        if angle != 0:
            page.set_rotation(angle)
            rotation_log.append(f"page {i+1}: rotated {angle} deg")
            logger.info("[digital-fitz] %s page %d -> rotated %d deg", file_path.name, i + 1, angle)

        page_text = page.get_text()
        pages_text.append(page_text)

    doc.close()

    rotation_summary = (", ".join(rotation_log)) if rotation_log else "no rotation needed"
    return "\n".join(pages_text), rotation_summary


# ── Public API ────────────────────────────────────────────────────────────────

def is_digital(file_path: Path, sample_pages: int = 2) -> bool:
    """
    Return True if the PDF has a real text layer (digital PDF).

    Reads the first `sample_pages` pages with PyMuPDF.  If total extracted
    text exceeds DIGITAL_TEXT_MIN_CHARS the PDF is treated as digital.

    Args:
        file_path:    Path to the PDF.
        sample_pages: How many pages to sample.

    Returns:
        True = digital (has text), False = scanned (image-only).
    """
    try:
        import fitz  # type: ignore

        doc   = fitz.open(str(file_path))
        limit = min(sample_pages, len(doc))
        total_chars = sum(len(doc[i].get_text()) for i in range(limit))
        doc.close()
        result = total_chars >= DIGITAL_TEXT_MIN_CHARS
        logger.debug(
            "[type-detect] %s -> %s (%d chars in first %d page(s))",
            file_path.name,
            "DIGITAL" if result else "SCANNED",
            total_chars,
            limit,
        )
        return result
    except Exception as exc:
        logger.warning("Type detection failed for %s: %s - assuming scanned.", file_path.name, exc)
        return False


def extract(file_path: Path, max_pages: int = 3) -> tuple[str, str, str]:
    """
    Extract text from a digital PDF (first `max_pages` pages).

    Rotation is auto-detected and corrected per page before extraction.
    If the extracted text is CID-encoded garbage (broken font ToUnicode map),
    automatically falls back to rostaing-ocr for proper text recognition.

    Args:
        file_path:  Path to a digital PDF.
        max_pages:  Maximum pages to read (default 3).

    Returns:
        ``(text, rotation_info, error)``
        - text:          Combined extracted text.
        - rotation_info: Human-readable rotation log (e.g. "page 1: rotated 90 deg").
        - error:         Non-empty string if a non-fatal problem occurred.
    """
    logger.info("[digital] Extracting: %s (max %d pages)", file_path.name, max_pages)

    # Determine how many pages will actually be read (needed for sparse check)
    try:
        import fitz as _fitz  # type: ignore
        _doc = _fitz.open(str(file_path))
        pages_read = min(max_pages, len(_doc)) if max_pages else len(_doc)
        _doc.close()
    except Exception:
        pages_read = max_pages or 3  # safe fallback

    def _needs_ocr(text: str, source: str) -> bool:
        """Return True and log a warning if text is CID garbage or too sparse."""
        if _is_cid_garbage(text):
            logger.warning(
                "[digital] %s - CID-encoded text detected (%s); falling back to OCR.",
                file_path.name, source,
            )
            return True
        if _is_too_sparse(text, pages_read):
            logger.warning(
                "[digital] %s - text layer too sparse (%d chars / %d pages) (%s); falling back to OCR.",
                file_path.name, len(text.strip()), pages_read, source,
            )
            return True
        return False

    # Primary: pdfplumber
    try:
        text, rotation_info = _extract_with_pdfplumber(file_path, max_pages)
        if text.strip() and not _needs_ocr(text, "pdfplumber"):
            return text, rotation_info, ""
    except Exception as exc:
        logger.warning("[digital] pdfplumber failed: %s - trying PyMuPDF.", exc)

    # Fallback: PyMuPDF
    try:
        text, rotation_info = _extract_with_fitz(file_path, max_pages)
        if text.strip() and not _needs_ocr(text, "PyMuPDF"):
            return text, rotation_info, "pdfplumber failed; used PyMuPDF fallback"
    except Exception as exc:
        logger.error("[digital] Both extractors failed: %s", exc)

    # OCR fallback: rostaing-ocr with auto-rotation for CID-encoded / sparse digital PDFs
    logger.info(
        "[digital] %s - switching to rostaing-ocr with auto-rotation (CID/sparse fallback).", file_path.name
    )
    try:
        from env_loader import get_env_setting
        from scanned_extractor import extract_with_auto_rotation
        
        poppler = get_env_setting("POPPLER_PATH") or None
        ocr_text, ocr_rotation, ocr_error = extract_with_auto_rotation(
            file_path,
            max_pages=max_pages,
            poppler_path=poppler
        )
        if ocr_text.strip():
            return ocr_text, ocr_rotation, f"CID font detected; used rostaing-ocr ({ocr_error or 'success'})"
    except Exception as exc:
        logger.error("[digital] rostaing-ocr fallback also failed: %s", exc)

    return "", "unknown", "All extractors failed (including OCR fallback)."


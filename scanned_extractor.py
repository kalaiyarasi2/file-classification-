"""
scanned_extractor.py - Text extraction for scanned (image-based) PDFs.

Two public entry points:

  extract()                    - original lightweight path (PyMuPDF + OSD only)
  extract_with_auto_rotation() - upgraded path using auto_rotation_ocr pipeline:
                                   pdf2image 300 DPI -> OSD + OpenCV skew ->
                                   validate -> retry -> rostaing-ocr (SchemaOCRExtractor)

The upgraded path gives significantly better results on rotated / slightly tilted
scans because it adds fine skew correction and a validate-then-retry loop on top
of the basic OSD that the original path uses.
"""

from __future__ import annotations

import io
import logging
import re
import tempfile
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# DPI multiplier for rasterisation in the original path (2.0 = 144 dpi)
_ZOOM = 2.0

# Minimum OSD confidence to trust the detected angle (0–100)
_OSD_MIN_CONFIDENCE = 1


# ── Original path helpers ──────────────────────────────────────────────────────

def _detect_rotation_osd(img) -> int:
    """
    Detect the clockwise rotation angle of a PIL Image using pytesseract OSD.

    Returns one of 0, 90, 180, 270 - the degrees to rotate counter-clockwise
    to correct the orientation. Returns 0 if detection fails or confidence is
    below threshold.
    """
    try:
        import pytesseract  # type: ignore

        osd_output = pytesseract.image_to_osd(img, output_type=pytesseract.Output.STRING)
        logger.debug("OSD output:\n%s", osd_output)

        match = re.search(r"Rotate:\s*(\d+)", osd_output)
        if not match:
            return 0

        angle = int(match.group(1))

        conf_match = re.search(r"Orientation confidence:\s*([\d.]+)", osd_output)
        if conf_match:
            confidence = float(conf_match.group(1))
            if confidence < _OSD_MIN_CONFIDENCE:
                logger.debug(
                    "OSD confidence %.1f below threshold - ignoring rotation %d deg",
                    confidence, angle,
                )
                return 0

        if angle in (90, 180, 270):
            logger.debug(
                "OSD detected rotation: %d deg (will rotate %d deg CCW to correct)", angle, angle
            )
            return angle

        return 0

    except Exception as exc:
        logger.debug("OSD rotation detection failed: %s - assuming 0 deg.", exc)
        return 0


def _correct_image_rotation(img, angle: int):
    """Rotate a PIL Image counter-clockwise by *angle* degrees."""
    if angle == 0:
        return img
    return img.rotate(angle, expand=True)


def _rasterise_page(page, zoom: float = _ZOOM):
    """Rasterise a PyMuPDF page to a PIL Image at the given zoom level."""
    from PIL import Image  # type: ignore

    mat = __import__("fitz").Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")


# ── Original extraction path ───────────────────────────────────────────────────

def extract(
    file_path: Path,
    max_pages: int = 3,
    debug_image_folder: Path | None = None,
) -> tuple[str, str, str]:
    """
    Extract text from a scanned PDF using OCR (first `max_pages` pages).

    Original lightweight path - PyMuPDF rasterise + Tesseract OSD + OCR.

    Args:
        file_path:          Path to a scanned (image-based) PDF.
        max_pages:          Maximum number of pages to process (default 3).
        debug_image_folder: If provided, each corrected page image is saved
                            as ``<stem>_page<N>.png`` inside this directory.

    Returns:
        ``(text, rotation_info, error)``
    """
    logger.info("[scanned] Extracting: %s (max %d pages)", file_path.name, max_pages)

    try:
        import fitz         # type: ignore
        import pytesseract  # type: ignore  # still used for OSD rotation detection
    except ImportError as exc:
        return "", "N/A", f"Missing dependency: {exc}. Install PyMuPDF and pytesseract."

    try:
        from schema_ocr import SchemaOCRExtractor  # type: ignore
    except ImportError:
        return "", "N/A", "schema_ocr / rostaing-ocr not installed; OCR unavailable."

    pages_text: list[str] = []
    rotation_log: list[str] = []

    try:
        doc   = fitz.open(str(file_path))
        limit = min(max_pages, len(doc)) if max_pages else len(doc)

        for i in range(limit):
            page = doc[i]

            # Step 1 - rasterise
            img = _rasterise_page(page)

            # Step 2 - detect rotation via OSD
            angle = _detect_rotation_osd(img)

            # Step 3 - correct rotation in-memory
            if angle != 0:
                img = _correct_image_rotation(img, angle)
                rotation_log.append(f"page {i+1}: rotated {angle} deg")
                logger.info(
                    "[scanned] %s page %d -> OSD detected %d deg, corrected.",
                    file_path.name, i + 1, angle,
                )
            else:
                logger.debug("[scanned] %s page %d -> no rotation needed.", file_path.name, i + 1)

            # Step 4 - (optional) save corrected image for visual verification
            if debug_image_folder is not None:
                debug_image_folder.mkdir(parents=True, exist_ok=True)
                img_save_path = debug_image_folder / f"{file_path.stem}_page{i + 1}.png"
                img.save(str(img_save_path))
                logger.debug(
                    "[scanned] %s page %d corrected image saved -> %s",
                    file_path.name, i + 1, img_save_path,
                )

            # Step 5 - OCR via rostaing-ocr
            # Save corrected image to a temp file so SchemaOCRExtractor can read it
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
                tmp_img_path = Path(tmp_img.name)
            try:
                img.save(str(tmp_img_path))
                ocr_extractor = SchemaOCRExtractor(tmp_img_path)
                page_text = ocr_extractor.extract_layout_text(save_debug_output=False)
            finally:
                tmp_img_path.unlink(missing_ok=True)

            pages_text.append(page_text)
            logger.debug("[scanned] page %d: %d chars from OCR", i + 1, len(page_text))

        doc.close()

    except Exception as exc:
        logger.error("[scanned] Failed to process %s: %s", file_path.name, exc, exc_info=True)
        return "", "error", str(exc)

    rotation_summary = (", ".join(rotation_log)) if rotation_log else "no rotation needed"
    combined = "\n".join(pages_text)

    if not combined.strip():
        return "", rotation_summary, "OCR returned empty text - check if rostaing-ocr is installed."

    return combined, rotation_summary, ""


# ── Upgraded extraction path (auto_rotation_ocr) ──────────────────────────────

def extract_with_auto_rotation(
    file_path: Path,
    max_pages: int = 3,
    debug_image_folder: Path | None = None,
    poppler_path: str | None = None,
    dpi: int = 300,
    osd_min_conf: float = 0.3,
    skew_threshold: float = 0.5,
    max_correction_attempts: int = 3,
) -> tuple[str, str, str]:
    """
    Extract text from a scanned PDF using the full auto_rotation_ocr pipeline.

    Per-page flow:
      1. pdf2image renders the page at *dpi* DPI -> JPEG in a temp directory.
      2. auto_rotation_ocr.detect_rotation()  -> OSD coarse angle + confidence.
      3. auto_rotation_ocr.detect_skew()      -> fine skew via OpenCV contours.
      4. auto_rotation_ocr.rotate_image()     -> apply coarse + fine correction.
      5. auto_rotation_ocr.validate_rotation() -> confirm; retry up to
         *max_correction_attempts* times if the page still fails.
      6. pytesseract OCR on the corrected JPEG -> text.
      7. Corrected JPEG copied to *debug_image_folder* if provided.
      8. Temp directory cleaned up.

    Args:
        file_path:               Path to a scanned (image-based) PDF.
        max_pages:               Maximum number of pages to process (default 3).
        debug_image_folder:      If provided, saves corrected page images as
                                 ``<stem>_page<N>.jpg`` for visual verification.
        poppler_path:            Path to Poppler's ``bin`` folder (Windows).
                                 ``None`` -> pdf2image finds it via PATH.
        dpi:                     Render resolution (default 300).
        osd_min_conf:            Minimum Tesseract OSD confidence to trust
                                 coarse rotation (default 8.0).
        skew_threshold:          Minimum skew angle ( deg) worth correcting (0.5).
        max_correction_attempts: Max detect->rotate->validate cycles per page (3).

    Returns:
        ``(text, rotation_info, error)``
        - text:          Combined OCR text from all processed pages.
        - rotation_info: Human-readable rotation log per page.
        - error:         Non-empty string if a non-fatal problem occurred.
    """
    logger.info(
        "[auto-rotation] Extracting: %s (max %d pages, %d DPI)",
        file_path.name, max_pages, dpi,
    )

    # ── Check dependencies ────────────────────────────────────────────
    try:
        from pdf2image import convert_from_path  # type: ignore
        import pytesseract                        # type: ignore  # still needed for OSD
        import auto_rotation_ocr as arc           # type: ignore
        from schema_ocr import SchemaOCRExtractor # type: ignore
    except ImportError as exc:
        logger.warning(
            "[auto-rotation] Missing dependency (%s) - falling back to basic extraction.", exc
        )
        return extract(file_path, max_pages=max_pages, debug_image_folder=debug_image_folder)

    pages_text: list[str] = []
    rotation_log: list[str] = []

    # Use a temp directory for intermediate JPEG files
    tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_pipeline_"))

    try:
        # ── Step 1: Render PDF pages to JPEG images ───────────────────────────
        convert_kwargs: dict = {"dpi": dpi, "output_folder": str(tmp_dir), "fmt": "jpeg"}
        if poppler_path:
            convert_kwargs["poppler_path"] = poppler_path

        pil_images = convert_from_path(str(file_path), **convert_kwargs)
        limit = min(max_pages, len(pil_images)) if max_pages else len(pil_images)

        for i in range(limit):
            page_num = i + 1
            raw_path = tmp_dir / f"page_{page_num:03d}.jpg"

            # Save the PIL image to the temp directory as JPEG (pdf2image may
            # already have done this when output_folder is set, but we ensure
            # a predictable filename here)
            if not raw_path.exists():
                pil_images[i].save(str(raw_path), "JPEG")

            corrected_path = tmp_dir / f"page_{page_num:03d}_corrected.jpg"
            logger.debug("[auto-rotation] Processing page %d: %s", page_num, raw_path)

            # ── Steps 2–5: Detect -> Rotate -> Validate (with retries) ─────────
            attempts_used = 0
            passed        = False
            report: dict  = {}

            for attempt in range(1, max_correction_attempts + 1):
                attempts_used = attempt

                osd_angle, osd_conf = arc.detect_rotation(str(raw_path))
                skew_angle          = arc.detect_skew(str(raw_path))

                logger.debug(
                    "[auto-rotation] page %d attempt %d - OSD=%d deg (conf=%.1f) skew=%.2f deg",
                    page_num, attempt, osd_angle, osd_conf, skew_angle,
                )

                # Dampen skew on retries to avoid over-rotating
                skew_scale   = max(0.0, 1.0 - (attempt - 1) * 0.35)
                applied_skew = skew_angle * skew_scale

                arc.rotate_image(
                    str(raw_path), str(corrected_path),
                    osd_angle=osd_angle,
                    osd_conf=osd_conf,
                    skew_angle=applied_skew,
                    skew_threshold=skew_threshold,
                    osd_min_conf=osd_min_conf,
                )

                passed, report = arc.validate_rotation(
                    str(corrected_path),
                    skew_threshold=skew_threshold,
                    osd_min_conf=osd_min_conf,
                )

                if passed:
                    break

                # No actionable correction left - stop retrying
                if report["osd_angle"] == 0 and abs(report["skew_angle"]) < skew_threshold:
                    logger.debug(
                        "[auto-rotation] page %d - no actionable correction left; stopping.",
                        page_num,
                    )
                    break

            # If still failing after all retries, use the raw page as safe fallback
            if not passed:
                logger.warning(
                    "[auto-rotation] page %d - validation failed after %d attempt(s); "
                    "using raw page as fallback.",
                    page_num, attempts_used,
                )
                import shutil as _shutil
                _shutil.copy2(str(raw_path), str(corrected_path))

            # Build rotation log entry
            osd_applied  = report.get("osd_angle", 0)
            skew_applied = report.get("skew_angle", 0.0)

            if osd_applied or abs(skew_applied) >= skew_threshold:
                rotation_log.append(
                    f"page {page_num}: OSD {osd_applied} deg + skew {skew_applied:.1f} deg"
                )
            else:
                logger.debug("[auto-rotation] page %d -> no correction needed.", page_num)

            # Log a summary for this page
            logger.info(
                "[auto-rotation] %s page %d -> OSD=%d deg skew=%.2f deg attempts=%d %s",
                file_path.name, page_num,
                report.get("osd_angle", 0),
                report.get("skew_angle", 0.0),
                attempts_used,
                "PASS" if passed else "FALLBACK",
            )

            # ── (optional) Save corrected image for visual verification ────────
            if debug_image_folder is not None:
                debug_image_folder.mkdir(parents=True, exist_ok=True)
                dest = debug_image_folder / f"{file_path.stem}_page{page_num}.jpg"
                shutil.copy2(str(corrected_path), str(dest))
                logger.debug(
                    "[auto-rotation] corrected image saved -> %s", dest
                )

            # ── Step 6: OCR via rostaing-ocr on corrected image ────────────────
            ocr_extractor = SchemaOCRExtractor(corrected_path)
            page_text = ocr_extractor.extract_layout_text(save_debug_output=False)

            pages_text.append(page_text)
            logger.debug(
                "[auto-rotation] page %d: %d chars from OCR", page_num, len(page_text)
            )

    except Exception as exc:
        logger.error(
            "[auto-rotation] Failed to process %s: %s", file_path.name, exc, exc_info=True
        )
        return "", "error", str(exc)

    finally:
        # ── Step 7: Clean up temp directory ──────────────────────────────────
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    rotation_summary = (", ".join(rotation_log)) if rotation_log else "no rotation needed"
    combined = "\n".join(pages_text)

    if not combined.strip():
        return "", rotation_summary, "OCR returned empty text - check rostaing-ocr installation."

    return combined, rotation_summary, ""

"""
main.py - Entry point for the AI Document Organizer.

Pipeline (per file):
  1. Scan input directory for PDFs.
  2. Detect if each PDF is digital (text layer) or scanned (image-only).
  3. Route to the correct extractor:
       - digital_extractor  -> pdfplumber + in-memory rotation correction
       - scanned_extractor  -> pytesseract OSD rotation + OCR
  4. First 3 pages of text sent to GPT-4o with category keywords.
  5. GPT returns a score (0-10) per category - highest score wins.
  6. File moved to output/<Category>/ folder.

Usage:
    python main.py --input ./inbox --output ./sorted
    python main.py --input ./inbox --output ./sorted --dry-run

Categories and keywords are loaded from .env (CATEGORY_* lines).
GPT API key is read from OPENAI_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from config import AppConfig
from env_loader import load_categories_from_env, get_env_setting
import digital_extractor
import scanned_extractor
from classifier import DocumentClassifier
from organizer import FileOrganizer
from report import ReportGenerator
from logger import get_logger

logger = get_logger(__name__)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Document Organizer - classify and sort PDFs automatically.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input",   type=Path, help="Input folder containing PDFs.")
    parser.add_argument("--output",  type=Path, help="Output root folder for sorted files.")
    parser.add_argument(
        "--config", type=Path, default=Path("config.json"),
        help="Path to JSON config file (for non-category settings).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Classify without moving files (report only).",
    )
    return parser.parse_args()


# ── Per-file processing ───────────────────────────────────────────────────────

def process_pdf(
    file_path: Path,
    pdf_max_pages: int,
    debug_image_folder: Path | None = None,
    poppler_path: str | None = None,
) -> tuple[str, str, str]:
    """
    Route a PDF to the correct extractor and return extracted text.

    Steps:
      1. Detect: digital or scanned?
      2. Extract text (first `pdf_max_pages` pages).
         - Digital PDFs: pdfplumber text layer extraction.
         - Scanned PDFs: full auto_rotation_ocr pipeline
           (pdf2image 300 DPI -> OSD + OpenCV skew -> validate -> retry -> OCR).
         Corrected page images are optionally saved to *debug_image_folder*.

    Returns:
        (text, pdf_type, rotation_info)
        - text:          Extracted plain text.
        - pdf_type:      "digital" or "scanned".
        - rotation_info: Per-page rotation/correction log string.
    """
    # Step 1 - Detect PDF type
    is_digital = digital_extractor.is_digital(file_path)
    pdf_type   = "digital" if is_digital else "scanned"

    logger.info("[%s] Detected as: %s", file_path.name, pdf_type.upper())

    # Step 2 - Extract text with appropriate extractor
    if is_digital:
        text, rotation_info, error = digital_extractor.extract(file_path, max_pages=pdf_max_pages)
    else:
        # Use the upgraded auto-rotation pipeline for scanned PDFs
        text, rotation_info, error = scanned_extractor.extract_with_auto_rotation(
            file_path,
            max_pages=pdf_max_pages,
            debug_image_folder=debug_image_folder,
            poppler_path=poppler_path,
        )

    if error:
        logger.warning("[%s] Extraction warning: %s", file_path.name, error)

    logger.info(
        "[%s] Extracted %d chars | Rotation: %s",
        file_path.name, len(text), rotation_info,
    )

    return text, pdf_type, rotation_info


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    """
    Full pipeline: directory scan -> extract -> classify -> organise -> report.

    Returns:
        Exit code (0 = success, 1 = fatal error).
    """
    args = parse_args()

    # ── Load base config ──────────────────────────────────────────────────────
    try:
        cfg = AppConfig.load(args.config)
    except Exception as exc:
        logger.error("Failed to load config: %s", exc)
        return 1

    # ── Load categories from .env ─────────────────────────────────────────────
    env_categories = load_categories_from_env()
    if env_categories:
        cfg.categories = env_categories
        logger.info(
            "Loaded %d category/categories from .env: %s",
            len(env_categories),
            list(env_categories.keys()),
        )
    elif not cfg.categories:
        logger.error(
            "No categories found. Add CATEGORY_<Name>=kw1,kw2,... to your .env file."
        )
        return 1

    # ── Read settings from .env ───────────────────────────────────────────────
    pdf_max_pages = int(get_env_setting("PDF_MAX_PAGES",        default="3"))
    min_score     = float(get_env_setting("MIN_SCORE_THRESHOLD", default="3"))
    llm_model     = get_env_setting("LLM_MODEL",                 default="gpt-4o")
    poppler_path  = get_env_setting("POPPLER_PATH",              default="") or None

    # ── Resolve folders ───────────────────────────────────────────────────────
    input_folder:  Path = args.input  or cfg.input_folder
    output_folder: Path = args.output or cfg.output_folder

    if not input_folder:
        logger.error("No input folder. Use --input or set input_folder in config.json.")
        return 1
    if not output_folder:
        logger.error("No output folder. Use --output or set output_folder in config.json.")
        return 1

    input_folder  = Path(input_folder)
    output_folder = Path(output_folder)

    if not input_folder.exists():
        logger.error("Input folder does not exist: %s", input_folder)
        return 1

    logger.info("=" * 60)
    logger.info("  AI Document Organizer - LLM Keyword Scoring")
    logger.info("=" * 60)
    logger.info("  Input    : %s", input_folder)
    logger.info("  Output   : %s", output_folder)
    logger.info("  Pages    : first %d page(s) per PDF", pdf_max_pages)
    logger.info("  Min Score: %s / 10", min_score)
    logger.info("  LLM Model: %s", llm_model)
    logger.info("  Poppler  : %s", poppler_path or "(auto / PATH)")
    logger.info("  OCR mode : auto-rotation (pdf2image + OpenCV skew + validation)")
    logger.info("  Dry run  : %s", args.dry_run)
    logger.info("  Categories (%d): %s", len(cfg.categories), list(cfg.categories.keys()))
    logger.info("=" * 60)

    # ── Bootstrap shared components ───────────────────────────────────────────
    classifier = DocumentClassifier(
        categories=cfg.categories,
        threshold=min_score,
        llm_model=llm_model,
        llm_enabled=True,           # always use LLM in this flow
    )
    organizer = FileOrganizer(
        output_folder=output_folder,
        copy_mode=cfg.copy_mode,
        dry_run=args.dry_run,
    )
    reporter = ReportGenerator(output_folder=output_folder)

    # ── Create extracted-text dump directory ──────────────────────────────────
    extracted_text_folder = output_folder / "extracted_text"
    extracted_text_folder.mkdir(parents=True, exist_ok=True)
    logger.info("Extracted text will be saved to: %s", extracted_text_folder)

    # ── Create rotated-pages image directory ──────────────────────────────────
    rotated_pages_folder = output_folder / "rotated_pages"
    rotated_pages_folder.mkdir(parents=True, exist_ok=True)
    logger.info("Rotated page images will be saved to: %s", rotated_pages_folder)

    # ── Collect PDF files ─────────────────────────────────────────────────────
    all_files = [
        f for f in input_folder.rglob("*")
        if f.is_file() and f.suffix.lower() == ".pdf"
    ]

    if not all_files:
        logger.warning("No PDF files found in: %s", input_folder)
        return 0

    logger.info("Found %d PDF(s) to process.", len(all_files))

    # ── Process each file ─────────────────────────────────────────────────────
    results: list[dict] = []

    try:
        from tqdm import tqdm  # type: ignore
        iterator = tqdm(all_files, desc="Processing", unit="file")
    except ImportError:
        iterator = all_files  # type: ignore[assignment]

    for file_path in iterator:
        start_time = time.perf_counter()

        record: dict = {
            "file_name":          file_path.name,
            "original_path":      str(file_path),
            "pdf_type":           "",        # "digital" or "scanned"
            "rotation_applied":   "",        # per-page rotation summary
            "category":           "Others",
            "llm_score":          0.0,
            "destination_folder": "",
            "processing_time":    0.0,
            "error":              "",
        }

        try:
            # ── STEP 1+2: Detect type -> Rotate if needed -> Extract text ──────
            text, pdf_type, rotation_info = process_pdf(
                file_path, pdf_max_pages,
                debug_image_folder=rotated_pages_folder,
                poppler_path=poppler_path,
            )
            record["pdf_type"]         = pdf_type
            record["rotation_applied"] = rotation_info

            # ── Save extracted text for verification ──────────────────────────
            txt_filename = file_path.stem + ".txt"
            txt_path     = extracted_text_folder / txt_filename
            txt_header   = (
                f"File      : {file_path.name}\n"
                f"PDF Type  : {pdf_type}\n"
                f"Rotation  : {rotation_info}\n"
                f"Chars     : {len(text)}\n"
                f"{'=' * 60}\n\n"
            )
            txt_path.write_text(txt_header + text, encoding="utf-8")
            logger.debug("[%s] Extracted text saved -> %s", file_path.name, txt_path)

            if not text.strip():
                logger.warning("[%s] Empty text after extraction - classifying as Others.", file_path.name)
                record["error"] = "Empty text extracted"
            else:
                # ── STEP 3: LLM keyword scoring ───────────────────────────────
                category, score = classifier.classify(text)
                record["category"]  = category
                record["llm_score"] = round(score * 10, 2)  # store as 0-10

                logger.info(
                    "[%s] -> %s | score=%.1f/10 | type=%s | rotation=%s | chars=%d",
                    file_path.name, category, score * 10,
                    pdf_type, rotation_info, len(text),
                )

                # ── STEP 4: Move to output/<Category>/ ────────────────────────
                dest = organizer.place(file_path, category)
                record["destination_folder"] = str(dest)

        except Exception as exc:
            logger.error("[%s] Unhandled error: %s", file_path.name, exc, exc_info=True)
            record["error"] = str(exc)

        finally:
            record["processing_time"] = round(time.perf_counter() - start_time, 4)
            results.append(record)

    # ── Report ────────────────────────────────────────────────────────────────
    reporter.save(results)
    logger.info(
        "Done. Processed %d file(s). Report saved to: %s",
        len(results), output_folder,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

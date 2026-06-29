"""
file_classifier.py - AI Document Classifier & Organiser (single-file module).

This file consolidates the following modules into one importable, standalone Python file:
    logger.py, utils.py, env_loader.py, config.py,
    auto_rotation_ocr.py, schema_ocr.py, digital_extractor.py,
    scanned_extractor.py, classifier.py, organizer.py, report.py, main.py

─── CLI usage ───────────────────────────────────────────────────────────────────
    python file_classifier.py --input ./inbox --output ./sorted
    python file_classifier.py --input ./inbox --output ./sorted --dry-run

─── Module / POC usage ──────────────────────────────────────────────────────────
    from file_classifier import (
        load_categories_from_env, get_env_setting,
        DocumentClassifier, FileOrganizer, ReportGenerator,
        is_digital, extract, extract_scanned, extract_with_auto_rotation,
    )

    categories = load_categories_from_env()
    classifier = DocumentClassifier(categories=categories, llm_model="gpt-4o")
    category, score = classifier.classify(some_text)

Pipeline (per file):
    1. Scan input directory for PDFs.
    2. Detect if each PDF is digital (text layer) or scanned (image-only).
    3. Route to the correct extractor:
         - digital extraction  -> pdfplumber + in-memory rotation correction
         - scanned extraction  -> pdf2image 300 DPI -> OSD + OpenCV skew -> validate -> retry -> OCR
    4. First N pages of text sent to GPT-4o with category keywords.
    5. GPT returns a score (0-10) per category - highest score wins.
    6. File moved to output/<Category>/ folder.

Categories and keywords are loaded from .env (CATEGORY_* lines).
GPT API key is read from OPENAI_API_KEY in .env.
"""

from __future__ import annotations

# ── stdlib imports ─────────────────────────────────────────────────────────────
import argparse
import csv
import hashlib
import io
import json
import logging
import logging.handlers
import os
import re
import shutil
import string
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Public API ─────────────────────────────────────────────────────────────────
__all__ = [
    # Logging
    "get_logger",
    # Utilities
    "normalise_text", "truncate", "file_md5", "safe_filename", "human_size",
    # Config / Env
    "AppConfig", "load_categories_from_env", "get_env_setting",
    # Auto-rotation OCR pipeline
    "pdf_to_images", "detect_rotation", "detect_skew",
    "rotate_image", "validate_rotation",
    "images_to_pdf", "run_pipeline", "run_pipeline_preserve_layout",
    # OCR extractor
    "SchemaOCRExtractor",
    # PDF extractors
    "is_digital", "extract", "extract_scanned", "extract_with_auto_rotation",
    # Classifier / Organizer / Report
    "DocumentClassifier", "FileOrganizer", "ReportGenerator",
    # Top-level pipeline runner
    "run_pipeline_full",
]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 - LOGGING
# (originally: logger.py)
# ══════════════════════════════════════════════════════════════════════════════

_LOGGING_CONFIGURED = False
_LOG_DIR = Path("logs")


def _configure_root_logger(log_level: str = "INFO") -> None:
    """
    Attach console and rotating-file handlers to the root logger.

    Safe to call multiple times (idempotent).
    """
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    _LOGGING_CONFIGURED = True

    # Configure stdout/stderr to be encoding-tolerant on Windows consoles
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="backslashreplace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(errors="backslashreplace")
        except Exception:
            pass

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Handlers control their own effective level

    # Suppress verbose third-party debug logging
    logging.getLogger("pdfminer").setLevel(logging.WARNING)
    logging.getLogger("pdfplumber").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level, logging.INFO))
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # ── File handler (rotating, max 5 MB × 5 backups) ─────────────────────
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOG_DIR / "document_organizer.log"

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


# Configure on import so any caller is ready immediately.
_configure_root_logger()


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger, ensuring root configuration has been applied.

    Args:
        name: Usually ``__name__`` from the calling module.

    Returns:
        Configured :class:`logging.Logger` instance.
    """
    _configure_root_logger()
    return logging.getLogger(name)


_logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 - UTILITIES
# (originally: utils.py)
# ══════════════════════════════════════════════════════════════════════════════

def normalise_text(text: str) -> str:
    """
    Lowercase, strip punctuation, and collapse whitespace.

    Args:
        text: Raw string from a document extractor.

    Returns:
        Clean, normalised string.

    Examples:
        >>> normalise_text("Hello, World!  This is a  test.")
        'hello world  this is a  test'
    """
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text: str, max_chars: int = 500) -> str:
    """
    Truncate *text* to *max_chars*, appending '…' if cut.

    Args:
        text: Source string.
        max_chars: Maximum character count in the returned string.

    Returns:
        Possibly-truncated string.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def file_md5(path: Path, chunk_size: int = 65_536) -> str:
    """
    Compute the MD5 hex digest of a file without reading it fully into memory.

    Useful for duplicate detection.

    Args:
        path: File to hash.
        chunk_size: Number of bytes per read chunk.

    Returns:
        Lowercase hex MD5 string.
    """
    hasher = hashlib.md5()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def safe_filename(name: str) -> str:
    """
    Strip characters that are illegal in common filesystems from *name*.

    Args:
        name: Proposed filename (without directory component).

    Returns:
        Sanitised filename.
    """
    sanitised = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    sanitised = re.sub(r"_+", "_", sanitised).strip("_")
    return sanitised or "unnamed"


def human_size(byte_count: int) -> str:
    """
    Format *byte_count* as a human-readable string (e.g. "1.23 MB").

    Args:
        byte_count: Number of bytes.

    Returns:
        Human-readable size string.
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if byte_count < 1024:
            return f"{byte_count:.2f} {unit}"
        byte_count /= 1024  # type: ignore[assignment]
    return f"{byte_count:.2f} PB"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 - ENVIRONMENT LOADER + APP CONFIG
# (originally: env_loader.py + config.py)
# ══════════════════════════════════════════════════════════════════════════════

_logger_env = get_logger("file_classifier.env_loader")

# Prefix that marks a line as a category definition
_CATEGORY_PREFIX = "CATEGORY_"


def _load_dotenv(env_path: Path) -> dict[str, str]:
    """
    Parse a .env file into a plain dict without external dependencies.

    Rules:
    - Lines starting with '#' are comments.
    - Empty lines are skipped.
    - KEY=VALUE  (values may be quoted with ' or ").
    - Inline comments after '#' are stripped.
    """
    env: dict[str, str] = {}

    if not env_path.exists():
        _logger_env.warning(".env file not found at: %s", env_path)
        return env

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        # Must contain '='
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip inline comments (handles  KEY=val  # comment)
        value = re.sub(r"\s+#.*$", "", value)

        # Strip surrounding quotes
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        env[key] = value

    return env


def load_categories_from_env(
    env_path: Path | None = None,
) -> dict[str, list[str]]:
    """
    Parse all ``CATEGORY_<Name>=kw1,kw2,...`` lines from the .env file.

    The category name is the part after ``CATEGORY_``.
    Keywords are split on commas and whitespace-stripped.

    Args:
        env_path: Path to the .env file. Defaults to ``.env`` in the
                  same directory as this module.

    Returns:
        Mapping of ``{category_name: [keyword, ...]}``.
        Returns an empty dict and logs a warning if no categories found.

    Example .env lines::

        CATEGORY_Invoice=invoice number,bill to,amount due
        CATEGORY_Bank Statement=beginning balance,ending balance

    Produces::

        {
            "Invoice": ["invoice number", "bill to", "amount due"],
            "Bank Statement": ["beginning balance", "ending balance"],
        }
    """
    if env_path is None:
        env_path = Path(__file__).parent / ".env"

    raw = _load_dotenv(env_path)

    # Also merge in real environment variables (os.environ overrides .env)
    for key, value in os.environ.items():
        raw[key] = value

    categories: dict[str, list[str]] = {}

    for key, value in raw.items():
        if not key.startswith(_CATEGORY_PREFIX):
            continue

        category_name = key[len(_CATEGORY_PREFIX):]  # strip "CATEGORY_"
        if not category_name:
            continue

        keywords = [kw.strip() for kw in value.split(",") if kw.strip()]
        if not keywords:
            _logger_env.warning("Category '%s' has no keywords - skipping.", category_name)
            continue

        categories[category_name] = keywords
        _logger_env.debug(
            "Loaded category '%s' with %d keyword(s): %s",
            category_name,
            len(keywords),
            keywords,
        )

    if not categories:
        _logger_env.warning(
            "No CATEGORY_* entries found in .env. "
            "All documents will be classified as 'Others'."
        )

    _logger_env.info("Loaded %d category/categories from .env.", len(categories))
    return categories


def get_env_setting(key: str, default: str = "") -> str:
    """
    Return a scalar setting value from the environment or .env file.

    Checks ``os.environ`` first (so real env vars always win), then
    falls back to the .env file, then to *default*.

    Args:
        key: Environment variable name (e.g. ``"OPENAI_API_KEY"``).
        default: Value to return when the key is not found anywhere.

    Returns:
        The setting value as a string.
    """
    if key in os.environ:
        return os.environ[key]

    env_path = Path(__file__).parent / ".env"
    raw = _load_dotenv(env_path)
    return raw.get(key, default)


# ── AppConfig ──────────────────────────────────────────────────────────────────

@dataclass
class AppConfig:
    """
    Strongly-typed container for all application settings.

    Attributes:
        input_folder: Source directory containing raw documents.
        output_folder: Destination root for categorised sub-folders.
        categories: Mapping of category name -> list of trigger keywords.
        ocr_enabled: Whether to run Tesseract OCR on images / scanned PDFs.
        llm_enabled: Whether to call an LLM for low-confidence documents.
        confidence_threshold: Minimum normalised score to accept a category.
        copy_mode: Copy files instead of moving them when True.
        log_level: Python logging level string (DEBUG / INFO / WARNING …).
    """

    input_folder: Optional[Path] = None
    output_folder: Optional[Path] = None
    categories: dict[str, list[str]] = field(default_factory=dict)
    ocr_enabled: bool = True
    llm_enabled: bool = False
    confidence_threshold: float = 0.10
    copy_mode: bool = False
    log_level: str = "INFO"

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, config_path: Path) -> "AppConfig":
        """
        Load configuration from a JSON file.

        The JSON structure is flexible:

        {
            "input_folder": "/path/to/input",
            "output_folder": "/path/to/output",
            "ocr_enabled": true,
            "llm_enabled": false,
            "confidence_threshold": 0.10,
            "copy_mode": false,
            "log_level": "INFO",
            "categories": {
                "Insurance Loss Run": ["loss run", "loss history"],
                "Bank Statement": ["beginning balance", "ending balance"]
            }
        }

        The top-level keys that are not recognised settings are treated as
        category definitions for backward compatibility with the simpler
        flat format (where each key IS a category).

        Args:
            config_path: Path to the JSON configuration file.

        Returns:
            Populated AppConfig instance.

        Raises:
            FileNotFoundError: When the config file is missing.
            ValueError: When the JSON is malformed.
        """
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        try:
            raw: dict = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in {config_path}: {exc}") from exc

        # Reserved setting keys - everything else is treated as a category
        SETTINGS_KEYS = {
            "input_folder",
            "output_folder",
            "ocr_enabled",
            "llm_enabled",
            "confidence_threshold",
            "copy_mode",
            "log_level",
            "categories",
        }

        # Explicit categories block takes precedence
        categories: dict[str, list[str]] = raw.get("categories", {})

        # Fall back: treat unknown top-level keys as category definitions
        if not categories:
            categories = {
                k: v
                for k, v in raw.items()
                if k not in SETTINGS_KEYS and isinstance(v, list)
            }

        input_folder = raw.get("input_folder")
        output_folder = raw.get("output_folder")

        instance = cls(
            input_folder=Path(input_folder) if input_folder else None,
            output_folder=Path(output_folder) if output_folder else None,
            categories=categories,
            ocr_enabled=bool(raw.get("ocr_enabled", True)),
            llm_enabled=bool(raw.get("llm_enabled", False)),
            confidence_threshold=float(raw.get("confidence_threshold", 0.10)),
            copy_mode=bool(raw.get("copy_mode", False)),
            log_level=str(raw.get("log_level", "INFO")).upper(),
        )

        # Apply log level from config
        logging.getLogger().setLevel(getattr(logging, instance.log_level, logging.INFO))
        return instance


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 - AUTO-ROTATION OCR PIPELINE
# (originally: auto_rotation_ocr.py - print() calls converted to logger)
# ══════════════════════════════════════════════════════════════════════════════

_logger_arc = get_logger("file_classifier.auto_rotation_ocr")


# ── 1. PDF -> IMAGES ────────────────────────────────────────────────────────────

def pdf_to_images(pdf_path, output_dir="pipeline/raw", dpi=300):
    """Convert each PDF page to a JPEG image."""
    from pdf2image import convert_from_path  # type: ignore

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    os.makedirs(output_dir, exist_ok=True)
    images = convert_from_path(pdf_path, dpi=dpi)

    saved_paths = []
    for i, image in enumerate(images):
        output_path = os.path.join(output_dir, f"page_{i+1:03d}.jpg")
        image.save(output_path, "JPEG")
        saved_paths.append(output_path)
        _logger_arc.debug("[1] Saved raw page: %s", output_path)

    return saved_paths


# ── 2. ROTATION DETECTION ──────────────────────────────────────────────────────

def detect_rotation(image_path):
    """
    Detect required rotation using Tesseract OSD.
    Returns (angle, confidence) where angle is degrees to rotate (0/90/180/270).
    """
    from PIL import Image   # type: ignore
    import pytesseract      # type: ignore

    with Image.open(image_path) as img:
        try:
            osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
            return int(osd["rotate"]), float(osd["orientation_conf"])
        except pytesseract.TesseractError as e:
            _logger_arc.debug("[2] OSD failed for %s: %s", image_path, e)
            return 0, 0.0


def detect_skew(image_path):
    """
    Detect fine-grained skew angle using OpenCV contour analysis.
    Returns angle in degrees (typically -45 deg to 45 deg).
    """
    import cv2      # type: ignore
    import numpy as np  # type: ignore

    img = cv2.imread(image_path)
    if img is None:
        return 0.0

    gray  = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur  = cv2.GaussianBlur(gray, (9, 9), 0)
    thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
    dilate = cv2.dilate(thresh, kernel, iterations=5)

    contours, _ = cv2.findContours(dilate, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0

    largest = max(contours, key=cv2.contourArea)
    angle = cv2.minAreaRect(largest)[-1]

    # Normalize OpenCV rectangle angle to a deskew range around 0.
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90

    return -angle  # Positive = clockwise correction needed


# ── 3. ROTATE ──────────────────────────────────────────────────────────────────

def rotate_image(image_path, output_path,
                 osd_angle=0, osd_conf=0.0, skew_angle=0.0,
                 osd_conf_threshold=20.0, skew_threshold=0.5,
                 osd_min_conf=8.0):
    """
    Apply coarse OSD rotation then fine skew correction.

    Args:
        osd_angle:          Coarse rotation in degrees (0/90/180/270).
        osd_conf:           Tesseract confidence for OSD.
        skew_angle:         Fine skew angle from contour analysis.
        osd_conf_threshold: Preferred OSD confidence to trust coarse rotation.
        skew_threshold:     Minimum skew angle ( deg) to bother correcting.
        osd_min_conf:       Minimum confidence floor to allow OSD correction.

    Returns:
        dict with keys: osd_applied, skew_applied, final_angle
    """
    import cv2              # type: ignore
    from PIL import Image   # type: ignore

    with Image.open(image_path) as img:
        result = {"osd_applied": False, "skew_applied": False, "final_angle": 0.0}

        # Step A: coarse OSD correction (90 deg increments)
        if osd_angle != 0 and osd_conf >= osd_min_conf:
            img = img.rotate(-osd_angle, expand=True)
            result["osd_applied"] = True
            result["final_angle"] += osd_angle
            _logger_arc.info(
                "[3] OSD rotation applied: %d deg (conf=%.1f, floor=%.1f)",
                osd_angle, osd_conf, osd_min_conf,
            )

        corrected_pil = img.copy()

    # Step B: fine skew correction via OpenCV
    if abs(skew_angle) >= skew_threshold:
        corrected_pil.save(output_path, "JPEG")

        cv_img = cv2.imread(output_path)
        (h, w)  = cv_img.shape[:2]
        M       = cv2.getRotationMatrix2D((w // 2, h // 2), skew_angle, 1.0)
        rotated = cv2.warpAffine(cv_img, M, (w, h),
                                 flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
        cv2.imwrite(output_path, rotated)
        result["skew_applied"] = True
        result["final_angle"] += skew_angle
        _logger_arc.info("[3] Skew correction applied: %.2f deg", skew_angle)
    else:
        corrected_pil.save(output_path, "JPEG")

    if not result["osd_applied"] and not result["skew_applied"]:
        _logger_arc.debug("[3] No correction needed.")

    return result


# ── 4. VALIDATION ──────────────────────────────────────────────────────────────

def validate_rotation(image_path, skew_threshold=0.5, osd_conf_threshold=20.0, osd_min_conf=8.0):
    """
    Re-run detection on the corrected image to confirm alignment.

    Returns:
        (passed: bool, report: dict)
    """
    osd_angle, osd_conf = detect_rotation(image_path)
    skew_angle          = detect_skew(image_path)

    osd_ok = (osd_angle == 0) or (osd_conf < osd_min_conf)

    # Adaptive skew tolerance
    effective_skew_threshold = skew_threshold
    if osd_angle == 0 and osd_conf >= osd_min_conf:
        effective_skew_threshold = max(skew_threshold, 2.0)

    skew_ok = abs(skew_angle) < effective_skew_threshold
    passed = osd_ok and skew_ok

    report = {
        "passed":    passed,
        "osd_angle": osd_angle,
        "osd_conf":  osd_conf,
        "skew_angle": round(skew_angle, 2),
    }
    status = "PASS" if passed else "FAIL"
    _logger_arc.info(
        "[4] Validation %s | OSD=%d deg (conf=%.1f) | skew=%.2f deg (limit=%.2f)",
        status, osd_angle, osd_conf, skew_angle, effective_skew_threshold,
    )
    return passed, report


# ── 5. IMAGES -> PDF ────────────────────────────────────────────────────────────

def images_to_pdf(image_paths, output_pdf="output.pdf", resize_to=None, pdf_resolution=300.0):
    """Combine corrected images into a single multi-page PDF."""
    from PIL import Image  # type: ignore

    images = []
    for path in image_paths:
        if not os.path.exists(path):
            _logger_arc.warning("[5] Skipped missing: %s", path)
            continue
        img = Image.open(path).convert("RGB")
        if resize_to:
            img = img.resize(resize_to, Image.Resampling.LANCZOS)
        images.append(img)

    if not images:
        _logger_arc.warning("[5] No valid images - PDF not created.")
        return None

    images[0].save(
        output_pdf,
        save_all=True,
        append_images=images[1:],
        quality=95,
        resolution=pdf_resolution,
    )
    _logger_arc.info("[5] PDF saved: %s (%d pages)", output_pdf, len(images))
    return output_pdf


# ── FULL PIPELINE ──────────────────────────────────────────────────────────────

def run_pipeline(pdf_path,
                 work_dir="pipeline",
                 output_pdf="corrected.pdf",
                 dpi=300,
                 osd_conf_threshold=20.0,
                 osd_min_conf=8.0,
                 skew_threshold=0.5,
                 max_correction_attempts=3,
                 reprocess_failed_pages=True):
    """
    Full pipeline: PDF -> raw images -> detect rotation -> rotate -> validate -> PDF

    Args:
        pdf_path:           Input PDF.
        work_dir:           Scratch folder for intermediate images.
        output_pdf:         Final output PDF path.
        dpi:                Render DPI for PDF -> image conversion.
        osd_conf_threshold: Preferred Tesseract OSD confidence (for reporting/tuning).
        osd_min_conf:       Minimum OSD confidence floor used by dynamic logic.
        skew_threshold:     Minimum skew angle ( deg) to apply fine correction.
        max_correction_attempts:
                            Max detect-rotate-validate cycles per page.
        reprocess_failed_pages:
                            If True, do one final safe reprocess for pages
                            that still fail after normal attempts.

    Returns:
        Path to corrected PDF, plus a per-page report list.
    """
    raw_dir       = os.path.join(work_dir, "raw")
    corrected_dir = os.path.join(work_dir, "corrected")
    os.makedirs(corrected_dir, exist_ok=True)

    # 1. PDF -> images
    raw_paths = pdf_to_images(pdf_path, output_dir=raw_dir, dpi=dpi)

    corrected_paths = []
    page_reports    = []

    for raw_path in raw_paths:
        page_name      = os.path.basename(raw_path)
        corrected_path = os.path.join(corrected_dir, page_name)

        _logger_arc.info("\n-- Page: %s ----------------------", page_name)

        attempts_used = 0
        passed = False
        report = {}

        # 2-4. Detect -> Rotate -> Validate (dynamic attempts)
        for attempt in range(1, max_correction_attempts + 1):
            attempts_used = attempt
            _logger_arc.debug("[2] Attempt %d/%d", attempt, max_correction_attempts)

            osd_angle, osd_conf = detect_rotation(raw_path)
            skew_angle = detect_skew(raw_path)
            _logger_arc.debug(
                "[2] OSD=%d deg (conf=%.1f) | skew=%.2f deg",
                osd_angle, osd_conf, skew_angle,
            )

            # On retries, apply a gentler skew angle
            skew_scale = max(0.0, 1.0 - (attempt - 1) * 0.35)
            applied_skew = skew_angle * skew_scale

            rotate_image(
                raw_path, corrected_path,
                osd_angle=osd_angle, osd_conf=osd_conf,
                skew_angle=applied_skew,
                osd_conf_threshold=osd_conf_threshold,
                skew_threshold=skew_threshold,
                osd_min_conf=osd_min_conf,
            )

            passed, report = validate_rotation(
                corrected_path,
                skew_threshold=skew_threshold,
                osd_conf_threshold=osd_conf_threshold,
                osd_min_conf=osd_min_conf,
            )
            if passed:
                break

            if report["osd_angle"] == 0 and abs(report["skew_angle"]) < skew_threshold:
                _logger_arc.debug(
                    "[4] Validation failed but no actionable correction left; stopping retries."
                )
                break

            _logger_arc.info("[4] Retrying correction for %s...", page_name)

        if not passed and reprocess_failed_pages:
            _logger_arc.info("[4] Reprocessing %s with safe fallback (raw page).", page_name)
            shutil.copy2(raw_path, corrected_path)
            passed, report = validate_rotation(
                corrected_path,
                skew_threshold=skew_threshold,
                osd_conf_threshold=osd_conf_threshold,
                osd_min_conf=osd_min_conf,
            )
            report["reprocessed"] = True
        else:
            report["reprocessed"] = False

        report["page"] = page_name
        report["attempts"] = attempts_used
        report["retried"] = attempts_used > 1
        page_reports.append(report)
        corrected_paths.append(corrected_path)

    # 5. Images -> PDF
    _logger_arc.info("\n-- Building output PDF --------------------")
    result_pdf = images_to_pdf(
        corrected_paths,
        output_pdf=output_pdf,
        pdf_resolution=float(dpi),
    )

    # Summary
    _logger_arc.info("\n-- Pipeline Summary -----------------------")
    for r in page_reports:
        flag = "! " if r["retried"] else "+ "
        _logger_arc.info(
            "  %s%s | OSD=%s deg skew=%s deg | %s",
            flag, r["page"], r["osd_angle"], r["skew_angle"],
            "PASS" if r["passed"] else "FAIL",
        )

    return result_pdf, page_reports


def run_pipeline_preserve_layout(pdf_path,
                                 work_dir="pipeline",
                                 output_pdf="corrected.pdf",
                                 dpi=200,
                                 osd_min_conf=8.0):
    """
    Detect page orientation from rendered images, but rotate original PDF pages.
    This preserves the original page geometry/layout and avoids image-rebuild sizing artifacts.
    """
    from pypdf import PdfReader, PdfWriter  # type: ignore

    raw_dir = os.path.join(work_dir, "raw")
    raw_paths = pdf_to_images(pdf_path, output_dir=raw_dir, dpi=dpi)

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    page_reports = []

    for idx, (raw_path, page) in enumerate(zip(raw_paths, reader.pages), start=1):
        page_name = os.path.basename(raw_path)
        _logger_arc.info("\n-- Page: %s ----------------------", page_name)

        osd_angle, osd_conf = detect_rotation(raw_path)
        skew_angle = detect_skew(raw_path)
        rotate_angle = osd_angle if (osd_angle != 0 and osd_conf >= osd_min_conf) else 0

        _logger_arc.info(
            "[2] OSD=%d deg (conf=%.1f) | skew=%.2f deg | apply_rotate=%d deg",
            osd_angle, osd_conf, skew_angle, rotate_angle,
        )

        if rotate_angle:
            page.rotate(rotate_angle)

        writer.add_page(page)
        page_reports.append({
            "page": page_name,
            "page_index": idx,
            "osd_angle": osd_angle,
            "osd_conf": round(osd_conf, 2),
            "skew_angle": round(skew_angle, 2),
            "applied_rotate": rotate_angle,
            "passed": True,
        })

    with open(output_pdf, "wb") as f:
        writer.write(f)

    _logger_arc.info("[5] PDF saved: %s (%d pages)", output_pdf, len(page_reports))
    for r in page_reports:
        _logger_arc.info(
            "  + %s | OSD=%s deg (conf=%s) | rotate=%s deg",
            r["page"], r["osd_angle"], r["osd_conf"], r["applied_rotate"],
        )

    return output_pdf, page_reports


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 - SCHEMA OCR EXTRACTOR
# (originally: schema_ocr.py)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import rostaing_ocr as _rostaing_ocr_mod  # type: ignore
    _ROSTAING_AVAILABLE = True
except ImportError:
    _rostaing_ocr_mod = None
    _ROSTAING_AVAILABLE = False
    _logger.warning("rostaing-ocr is not installed. SchemaOCRExtractor OCR methods will fail.")


class SchemaOCRExtractor:
    """
    Extracts structured layout text using rostaing-ocr and maps it to a JSON schema.
    """

    def __init__(self, pdf_path, api_key=None):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"File not found: {self.pdf_path}")

        self.api_key = api_key or get_env_setting("OPENAI_API_KEY")
        self.output_text = ""

        self.ocr_engine = None
        if _ROSTAING_AVAILABLE:
            try:
                self.ocr_engine = _rostaing_ocr_mod.RostaingOCR()
            except AttributeError:
                self.ocr_engine = _rostaing_ocr_mod
        else:
            _logger.warning("rostaing-ocr must be installed. Methods will fail until it's loaded.")

    def extract_layout_text(self, save_debug_output=True):
        """
        Extract the layout-preserved text using rostaing-ocr.
        It uses deep learning to preserve tables and columns natively.
        """
        if not self.ocr_engine:
            raise ImportError("rostaing-ocr is not installed or failed to initialize.")

        def _run_rostaing_extraction(pdf_path_str):
            if hasattr(self.ocr_engine, "ocr_extractor"):
                temp_output = self.pdf_path.with_suffix(".rostaing_temp.txt")
                self.ocr_engine.ocr_extractor(str(self.pdf_path), output_file=str(temp_output))
                if temp_output.exists():
                    with open(temp_output, "r", encoding="utf-8") as temp_f:
                        res_text = temp_f.read()
                    temp_output.unlink()
                    return res_text
                else:
                    return ""
            elif hasattr(self.ocr_engine, "process_document"):
                return self.ocr_engine.process_document(str(self.pdf_path))
            elif hasattr(self.ocr_engine, "extract"):
                return self.ocr_engine.extract(str(self.pdf_path))
            elif hasattr(self.ocr_engine, "ocr"):
                return self.ocr_engine.ocr(str(self.pdf_path))
            else:
                _logger.warning("Could not identify exact extraction method in rostaing_ocr.")
                return "Rostaing OCR extraction completed."

        try:
            self.output_text = _run_rostaing_extraction(str(self.pdf_path))
            _logger.info(
                "[Rostaing OCR] Finished extracting. Text length: %d characters.",
                len(self.output_text),
            )

            if save_debug_output and self.output_text:
                debug_path = self.pdf_path.with_suffix(".rostaing_layout.txt")
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(self.output_text)
                _logger.info("[Rostaing OCR] Structured layout text saved to: %s", debug_path)

            return self.output_text

        except Exception as e:
            _logger.error("[Error] Failed during rostaing-ocr extraction: %s", e)
            raise

    def extract_to_schema(self, schema_format: dict, use_llm=False):
        """
        Maps the highly-structured text layout directly to the requested JSON schema.

        Args:
            schema_format: Dict of keys to extract.
            use_llm: If True, uses a cheap Text LLM (gpt-4o-mini) - no Vision needed
                     because rostaing-ocr preserves the visual structure as text spaces.
                     If False, relies purely on fast regular expressions.
        """
        if not self.output_text:
            self.extract_layout_text()

        if use_llm and self.api_key:
            return self._parse_schema_with_text_llm(schema_format)
        else:
            return self._parse_schema_with_regex(schema_format)

    def _parse_schema_with_text_llm(self, schema_format: dict):
        """
        Passes the structured rostaing-ocr text string to a standard text LLM
        to guarantee perfect JSON output. Significantly cheaper than Vision models.
        """
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            _logger.error("openai package not installed; cannot use LLM schema mapping.")
            return {}

        _logger.info("[Rostaing OCR] Mapping structured text to JSON Schema via Text LLM...")
        client = OpenAI(api_key=self.api_key)

        prompt = f"""
        Extract the requested fields from the structured OCR text below.
        The text layout (tables, columns) has been natively preserved.

        Return ONLY a JSON dictionary exactly matching the keys of this schema:
        {json.dumps(schema_format, indent=2)}

        OCR TEXT:
        {self.output_text[:12000]}
        """

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            data = json.loads(response.choices[0].message.content)
            _logger.info("[Rostaing OCR] Schema mapping completed successfully.")
            return data
        except Exception as e:
            _logger.error("[Error] LLM Schema mapping failed: %s", e)
            return {}

    def _parse_schema_with_regex(self, schema_format: dict):
        """
        Zero-API-cost extraction via regex on the rostaing-ocr structured text.
        Because rostaing-ocr preserves exact spaces, 'Key: Value' pairs are very reliable.
        """
        _logger.info("[Rostaing OCR] Mapping structured text to JSON Schema via Regex...")
        results = {}
        for key in schema_format.keys():
            pattern = re.compile(f"{key}\\s*[:\\|-]?\\s*(.+)", re.IGNORECASE)
            match = pattern.search(self.output_text)
            results[key] = match.group(1).strip() if match else None
        return results


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 - DIGITAL PDF EXTRACTOR
# (originally: digital_extractor.py)
# ══════════════════════════════════════════════════════════════════════════════

_logger_dig = get_logger("file_classifier.digital_extractor")

# Minimum number of text characters on a page to consider it "digital"
DIGITAL_TEXT_MIN_CHARS = 30

# If more than this fraction of chars are inside (cid:N) sequences, treat as garbage
_CID_GARBAGE_THRESHOLD = 0.20

# Minimum meaningful chars per page - below this, the text layer is too sparse
_MIN_CHARS_PER_PAGE = 150


def _is_cid_garbage(text: str) -> bool:
    """
    Return True if the extracted text is predominantly unreadable garbage.

    Two patterns are detected:

    1. ``(cid:N)`` sequences - pdfplumber's representation of CID-encoded chars
       when the PDF font has no ToUnicode map.
    2. Non-printable Unicode control characters - PyMuPDF decodes the same
       broken fonts as raw codepoints instead of the ``(cid:N)`` text form.

    If either pattern accounts for more than _CID_GARBAGE_THRESHOLD of the
    non-whitespace content the text is considered unreadable and OCR should
    be used instead.
    """
    if not text:
        return False

    import unicodedata

    cid_token_chars = sum(len(m.group()) for m in re.finditer(r'\(cid:\d+\)', text))

    control_chars = sum(
        1 for c in text
        if unicodedata.category(c) in ("Cc", "Cs", "Co")
        and c not in ("\n", "\r", "\t")
    )

    total_nonspace = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
    if total_nonspace == 0:
        return False

    garbage_chars = cid_token_chars + control_chars
    ratio = garbage_chars / total_nonspace
    _logger_dig.debug(
        "[cid-check] Garbage ratio: %.1f%% (cid_tokens=%d, control=%d / total=%d)",
        ratio * 100, cid_token_chars, control_chars, total_nonspace,
    )
    return ratio > _CID_GARBAGE_THRESHOLD


def _is_too_sparse(text: str, pages_read: int) -> bool:
    """
    Return True when the digital text layer contains too little content to be
    useful, even though it technically has some text (e.g. only barcodes,
    page numbers, or reference IDs).
    """
    if pages_read <= 0:
        return False
    printable = sum(1 for c in text if c.isprintable() and c not in (" ", "\t"))
    chars_per_page = printable / pages_read
    _logger_dig.debug(
        "[sparse-check] %.0f printable chars/page (%d chars, %d pages)",
        chars_per_page, printable, pages_read,
    )
    return chars_per_page < _MIN_CHARS_PER_PAGE


def _detect_rotation_fitz(page) -> int:
    """
    Detect whether a PyMuPDF page needs rotation by analysing text-block shapes.

    If more text blocks are taller than wide the content is likely rotated 90 deg.

    Returns:
        90 if rotation is likely needed, 0 otherwise.
    """
    blocks = page.get_text("blocks")
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
        _logger_dig.debug(
            "Rotation detected: vertical=%d horizontal=%d -> 90 deg", vertical, horizontal
        )
        return 90
    return 0


def _extract_with_pdfplumber(file_path: Path, max_pages: int) -> tuple[str, str]:
    """
    Extract text from a digital PDF using pdfplumber.

    Rotation is applied via PyMuPDF in-memory before pdfplumber reads each page.

    Returns:
        (combined_text, rotation_summary)
    """
    try:
        import fitz        # type: ignore
        import pdfplumber  # type: ignore
    except ImportError as exc:
        return "", f"Missing dependency: {exc}"

    pages_text: list[str] = []
    rotation_log: list[str] = []

    fitz_doc = fitz.open(str(file_path))

    with pdfplumber.open(str(file_path)) as plumber_doc:
        limit = min(max_pages, len(plumber_doc.pages)) if max_pages else len(plumber_doc.pages)

        for i in range(limit):
            fitz_page    = fitz_doc[i]
            plumber_page = plumber_doc.pages[i]

            angle = _detect_rotation_fitz(fitz_page)

            if angle != 0:
                fitz_page.set_rotation(angle)
                rotation_log.append(f"page {i+1}: rotated {angle} deg")
                _logger_dig.info(
                    "[digital] %s page %d -> rotated %d deg", file_path.name, i + 1, angle
                )

                rotated_bytes = fitz_doc.tobytes()
                with pdfplumber.open(io.BytesIO(rotated_bytes)) as rotated_doc:
                    page_text = rotated_doc.pages[i].extract_text() or ""
            else:
                page_text = plumber_page.extract_text() or ""

            pages_text.append(page_text)
            _logger_dig.debug("[digital] page %d: %d chars extracted", i + 1, len(page_text))

    fitz_doc.close()

    rotation_summary = (", ".join(rotation_log)) if rotation_log else "no rotation needed"
    combined = "\n".join(pages_text)
    return combined, rotation_summary


def _extract_with_fitz(file_path: Path, max_pages: int) -> tuple[str, str]:
    """
    Fallback extractor using PyMuPDF get_text().
    Also applies in-memory rotation correction before extracting.

    Returns:
        (combined_text, rotation_summary)
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
            _logger_dig.info(
                "[digital-fitz] %s page %d -> rotated %d deg", file_path.name, i + 1, angle
            )

        page_text = page.get_text()
        pages_text.append(page_text)

    doc.close()

    rotation_summary = (", ".join(rotation_log)) if rotation_log else "no rotation needed"
    return "\n".join(pages_text), rotation_summary


def is_digital(file_path: Path, sample_pages: int = 2) -> bool:
    """
    Return True if the PDF has a real text layer (digital PDF).

    Reads the first `sample_pages` pages with PyMuPDF. If total extracted
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
        _logger_dig.debug(
            "[type-detect] %s -> %s (%d chars in first %d page(s))",
            file_path.name,
            "DIGITAL" if result else "SCANNED",
            total_chars,
            limit,
        )
        return result
    except Exception as exc:
        _logger_dig.warning(
            "Type detection failed for %s: %s - assuming scanned.", file_path.name, exc
        )
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
    _logger_dig.info("[digital] Extracting: %s (max %d pages)", file_path.name, max_pages)

    try:
        import fitz as _fitz  # type: ignore
        _doc = _fitz.open(str(file_path))
        pages_read = min(max_pages, len(_doc)) if max_pages else len(_doc)
        _doc.close()
    except Exception:
        pages_read = max_pages or 3

    def _needs_ocr(text: str, source: str) -> bool:
        """Return True and log a warning if text is CID garbage or too sparse."""
        if _is_cid_garbage(text):
            _logger_dig.warning(
                "[digital] %s - CID-encoded text detected (%s); falling back to OCR.",
                file_path.name, source,
            )
            return True
        if _is_too_sparse(text, pages_read):
            _logger_dig.warning(
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
        _logger_dig.warning("[digital] pdfplumber failed: %s - trying PyMuPDF.", exc)

    # Fallback: PyMuPDF
    try:
        text, rotation_info = _extract_with_fitz(file_path, max_pages)
        if text.strip() and not _needs_ocr(text, "PyMuPDF"):
            return text, rotation_info, "pdfplumber failed; used PyMuPDF fallback"
    except Exception as exc:
        _logger_dig.error("[digital] Both extractors failed: %s", exc)

    # OCR fallback: rostaing-ocr with auto-rotation for CID-encoded / sparse digital PDFs
    _logger_dig.info(
        "[digital] %s - switching to rostaing-ocr with auto-rotation (CID/sparse fallback).", file_path.name
    )
    try:
        poppler = get_env_setting("POPPLER_PATH") or None
        ocr_text, ocr_rotation, ocr_error = extract_with_auto_rotation(
            file_path,
            max_pages=max_pages,
            poppler_path=poppler
        )
        if ocr_text.strip():
            return ocr_text, ocr_rotation, f"CID font detected; used rostaing-ocr ({ocr_error or 'success'})"
    except Exception as exc:
        _logger_dig.error("[digital] rostaing-ocr fallback also failed: %s", exc)

    return "", "unknown", "All extractors failed (including OCR fallback)."


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 - SCANNED PDF EXTRACTOR
# (originally: scanned_extractor.py)
# NOTE: extract() renamed -> extract_scanned() to avoid clash with Section 7's extract()
# ══════════════════════════════════════════════════════════════════════════════

_logger_scn = get_logger("file_classifier.scanned_extractor")

# DPI multiplier for rasterisation in the original path (2.0 = 144 dpi)
_ZOOM = 2.0

# Minimum OSD confidence to trust the detected angle (0–100)
_OSD_MIN_CONFIDENCE = 1


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
        _logger_scn.debug("OSD output:\n%s", osd_output)

        match = re.search(r"Rotate:\s*(\d+)", osd_output)
        if not match:
            return 0

        angle = int(match.group(1))

        conf_match = re.search(r"Orientation confidence:\s*([\d.]+)", osd_output)
        if conf_match:
            confidence = float(conf_match.group(1))
            if confidence < _OSD_MIN_CONFIDENCE:
                _logger_scn.debug(
                    "OSD confidence %.1f below threshold - ignoring rotation %d deg",
                    confidence, angle,
                )
                return 0

        if angle in (90, 180, 270):
            _logger_scn.debug(
                "OSD detected rotation: %d deg (will rotate %d deg CCW to correct)", angle, angle
            )
            return angle

        return 0

    except Exception as exc:
        _logger_scn.debug("OSD rotation detection failed: %s - assuming 0 deg.", exc)
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


def extract_scanned(
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
    _logger_scn.info("[scanned] Extracting: %s (max %d pages)", file_path.name, max_pages)

    try:
        import fitz         # type: ignore
        import pytesseract  # type: ignore  # still used for OSD rotation detection
    except ImportError as exc:
        return "", "N/A", f"Missing dependency: {exc}. Install PyMuPDF and pytesseract."

    try:
        _schema_extractor_cls = SchemaOCRExtractor
    except NameError:
        return "", "N/A", "schema_ocr / rostaing-ocr not installed; OCR unavailable."

    pages_text: list[str] = []
    rotation_log: list[str] = []

    try:
        doc   = fitz.open(str(file_path))
        limit = min(max_pages, len(doc)) if max_pages else len(doc)

        for i in range(limit):
            page = doc[i]

            img   = _rasterise_page(page)
            angle = _detect_rotation_osd(img)

            if angle != 0:
                img = _correct_image_rotation(img, angle)
                rotation_log.append(f"page {i+1}: rotated {angle} deg")
                _logger_scn.info(
                    "[scanned] %s page %d -> OSD detected %d deg, corrected.",
                    file_path.name, i + 1, angle,
                )
            else:
                _logger_scn.debug(
                    "[scanned] %s page %d -> no rotation needed.", file_path.name, i + 1
                )

            if debug_image_folder is not None:
                debug_image_folder.mkdir(parents=True, exist_ok=True)
                img_save_path = debug_image_folder / f"{file_path.stem}_page{i + 1}.png"
                img.save(str(img_save_path))
                _logger_scn.debug(
                    "[scanned] %s page %d corrected image saved -> %s",
                    file_path.name, i + 1, img_save_path,
                )

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
                tmp_img_path = Path(tmp_img.name)
            try:
                img.save(str(tmp_img_path))
                ocr_extractor = SchemaOCRExtractor(tmp_img_path)
                page_text = ocr_extractor.extract_layout_text(save_debug_output=False)
            finally:
                tmp_img_path.unlink(missing_ok=True)

            pages_text.append(page_text)
            _logger_scn.debug("[scanned] page %d: %d chars from OCR", i + 1, len(page_text))

        doc.close()

    except Exception as exc:
        _logger_scn.error(
            "[scanned] Failed to process %s: %s", file_path.name, exc, exc_info=True
        )
        return "", "error", str(exc)

    rotation_summary = (", ".join(rotation_log)) if rotation_log else "no rotation needed"
    combined = "\n".join(pages_text)

    if not combined.strip():
        return "", rotation_summary, "OCR returned empty text - check if rostaing-ocr is installed."

    return combined, rotation_summary, ""


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
      2. detect_rotation()  -> OSD coarse angle + confidence.
      3. detect_skew()      -> fine skew via OpenCV contours.
      4. rotate_image()     -> apply coarse + fine correction.
      5. validate_rotation() -> confirm; retry up to *max_correction_attempts*
         times if the page still fails.
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
                                 coarse rotation (default 0.3).
        skew_threshold:          Minimum skew angle ( deg) worth correcting (0.5).
        max_correction_attempts: Max detect->rotate->validate cycles per page (3).

    Returns:
        ``(text, rotation_info, error)``
        - text:          Combined OCR text from all processed pages.
        - rotation_info: Human-readable rotation log per page.
        - error:         Non-empty string if a non-fatal problem occurred.
    """
    _logger_scn.info(
        "[auto-rotation] Extracting: %s (max %d pages, %d DPI)",
        file_path.name, max_pages, dpi,
    )

    try:
        from pdf2image import convert_from_path  # type: ignore
        import pytesseract  # type: ignore  # still needed for OSD
    except ImportError as exc:
        _logger_scn.warning(
            "[auto-rotation] Missing dependency (%s) - falling back to basic extraction.", exc
        )
        return extract_scanned(file_path, max_pages=max_pages, debug_image_folder=debug_image_folder)

    pages_text: list[str] = []
    rotation_log: list[str] = []

    tmp_dir = Path(tempfile.mkdtemp(prefix="ocr_pipeline_"))

    try:
        # Step 1: Render PDF pages to JPEG images
        convert_kwargs: dict = {"dpi": dpi, "output_folder": str(tmp_dir), "fmt": "jpeg"}
        if poppler_path:
            convert_kwargs["poppler_path"] = poppler_path

        pil_images = convert_from_path(str(file_path), **convert_kwargs)
        limit = min(max_pages, len(pil_images)) if max_pages else len(pil_images)

        for i in range(limit):
            page_num = i + 1
            raw_path = tmp_dir / f"page_{page_num:03d}.jpg"

            if not raw_path.exists():
                pil_images[i].save(str(raw_path), "JPEG")

            corrected_path = tmp_dir / f"page_{page_num:03d}_corrected.jpg"
            _logger_scn.debug("[auto-rotation] Processing page %d: %s", page_num, raw_path)

            # Steps 2–5: Detect -> Rotate -> Validate (with retries)
            attempts_used = 0
            passed        = False
            report: dict  = {}

            for attempt in range(1, max_correction_attempts + 1):
                attempts_used = attempt

                osd_angle, osd_conf = detect_rotation(str(raw_path))
                skew_angle          = detect_skew(str(raw_path))

                _logger_scn.debug(
                    "[auto-rotation] page %d attempt %d - OSD=%d deg (conf=%.1f) skew=%.2f deg",
                    page_num, attempt, osd_angle, osd_conf, skew_angle,
                )

                # Dampen skew on retries to avoid over-rotating
                skew_scale   = max(0.0, 1.0 - (attempt - 1) * 0.35)
                applied_skew = skew_angle * skew_scale

                rotate_image(
                    str(raw_path), str(corrected_path),
                    osd_angle=osd_angle,
                    osd_conf=osd_conf,
                    skew_angle=applied_skew,
                    skew_threshold=skew_threshold,
                    osd_min_conf=osd_min_conf,
                )

                passed, report = validate_rotation(
                    str(corrected_path),
                    skew_threshold=skew_threshold,
                    osd_min_conf=osd_min_conf,
                )

                if passed:
                    break

                if report["osd_angle"] == 0 and abs(report["skew_angle"]) < skew_threshold:
                    _logger_scn.debug(
                        "[auto-rotation] page %d - no actionable correction left; stopping.",
                        page_num,
                    )
                    break

            if not passed:
                _logger_scn.warning(
                    "[auto-rotation] page %d - validation failed after %d attempt(s); "
                    "using raw page as fallback.",
                    page_num, attempts_used,
                )
                shutil.copy2(str(raw_path), str(corrected_path))

            # Build rotation log entry
            osd_applied  = report.get("osd_angle", 0)
            skew_applied = report.get("skew_angle", 0.0)

            if osd_applied or abs(skew_applied) >= skew_threshold:
                rotation_log.append(
                    f"page {page_num}: OSD {osd_applied} deg + skew {skew_applied:.1f} deg"
                )
            else:
                _logger_scn.debug("[auto-rotation] page %d -> no correction needed.", page_num)

            _logger_scn.info(
                "[auto-rotation] %s page %d -> OSD=%d deg skew=%.2f deg attempts=%d %s",
                file_path.name, page_num,
                report.get("osd_angle", 0),
                report.get("skew_angle", 0.0),
                attempts_used,
                "PASS" if passed else "FALLBACK",
            )

            if debug_image_folder is not None:
                debug_image_folder.mkdir(parents=True, exist_ok=True)
                dest = debug_image_folder / f"{file_path.stem}_page{page_num}.jpg"
                shutil.copy2(str(corrected_path), str(dest))
                _logger_scn.debug("[auto-rotation] corrected image saved -> %s", dest)

            # Step 6: OCR via rostaing-ocr on corrected image
            ocr_extractor = SchemaOCRExtractor(corrected_path)
            page_text = ocr_extractor.extract_layout_text(save_debug_output=False)

            pages_text.append(page_text)
            _logger_scn.debug(
                "[auto-rotation] page %d: %d chars from OCR", page_num, len(page_text)
            )

    except Exception as exc:
        _logger_scn.error(
            "[auto-rotation] Failed to process %s: %s", file_path.name, exc, exc_info=True
        )
        return "", "error", str(exc)

    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    rotation_summary = (", ".join(rotation_log)) if rotation_log else "no rotation needed"
    combined = "\n".join(pages_text)

    if not combined.strip():
        return "", rotation_summary, "OCR returned empty text - check rostaing-ocr installation."

    return combined, rotation_summary, ""


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 - DOCUMENT CLASSIFIER
# (originally: classifier.py)
# ══════════════════════════════════════════════════════════════════════════════

_logger_cls = get_logger("file_classifier.classifier")

# Minimum fuzzy-match ratio (0-100) for the fallback keyword scorer
_FUZZY_RATIO_THRESHOLD = 80


def _normalise_for_classifier(text: str) -> str:
    """Lowercase and strip punctuation, keeping spaces and newlines."""
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


class DocumentClassifier:
    """
    Classifies document text into a predefined category using an LLM scorer.

    The LLM receives the document text and all category keywords, then
    returns a score (0-10) for each category. The highest-scoring category
    wins. Falls back to fuzzy keyword matching if the LLM is unavailable.

    Args:
        categories:  Mapping of ``{category_name: [keyword, ...]}``.
        threshold:   Minimum LLM score (0-10) to accept a category win.
                     If the winner's score is below this, returns "Others".
        llm_model:   LLM model identifier string (e.g. "gpt-4o").
        llm_enabled: Whether to use the LLM scorer. When False, uses only
                     the legacy fuzzy keyword fallback.
    """

    OTHERS = "Others"

    def __init__(
        self,
        categories: dict[str, list[str]],
        threshold: float = 3.0,
        llm_model: str = "gpt-4o",
        llm_enabled: bool = True,
    ) -> None:
        self.categories = categories
        self.threshold = threshold
        self.llm_model = llm_model
        self.llm_enabled = llm_enabled

        self._normalised_keywords: dict[str, list[str]] = {
            cat: [_normalise_for_classifier(kw) for kw in kws]
            for cat, kws in categories.items()
        }

    # ── Public API ─────────────────────────────────────────────────────────────

    def classify(self, text: str) -> tuple[str, float]:
        """
        Assign a category to *text*.

        Returns:
            ``(category_name, score)`` where score is in the range [0.0, 10.0].
            Score is normalised to [0.0, 1.0] for backward compat in reports.
        """
        if not text or not text.strip():
            _logger_cls.debug("Empty text; defaulting to '%s'.", self.OTHERS)
            return self.OTHERS, 0.0

        if self.llm_enabled:
            category, score = self._classify_with_llm(text)
            return category, round(score / 10.0, 4)

        category, score = self._classify_fuzzy(text)
        return category, round(score, 4)

    # ── LLM Scorer (Primary) ───────────────────────────────────────────────────

    def _classify_with_llm(self, text: str) -> tuple[str, float]:
        """
        Ask the LLM to score each category based on keyword presence.

        The LLM receives:
          [1] Extracted document text (first 3 pages, already trimmed by extractor)
          [2] Each category with its full keyword list
          [3] Instruction to score 0-10 per category

        The LLM returns JSON:
          {
            "scores": {
              "Invoice": 7,
              "Insurance Loss Run": 2,
              "Bank Statement": 0
            },
            "winner": "Invoice",
            "reasoning": "Document contains 'invoice number', 'bill to', 'amount due'..."
          }

        Returns:
            (category_name, score_0_to_10)
        """
        try:
            import openai  # type: ignore
        except ImportError:
            _logger_cls.warning("openai package not installed. Falling back to fuzzy scoring.")
            return self._classify_fuzzy(text)

        categories_block = "\n".join(
            f"- {cat}: {', '.join(kws)}"
            for cat, kws in self.categories.items()
        )

        text_snippet = text[:4000]

        prompt = f"""You are a document classifier. Your job is to score how well a document matches each category based on keyword presence.

DOCUMENT TEXT (extracted from first 3 pages):
---
{text_snippet}
---

CATEGORIES AND THEIR KEYWORDS:
{categories_block}

INSTRUCTIONS:
For each category, count how many of its keywords appear in or are semantically present in the document text.
Give each category a score from 0 to 10 (0 = no match, 10 = perfect match).
Then identify the winner (category with the highest score).
If no category scores above {self.threshold}, set winner to "Others".

IMPORTANT: Respond ONLY with valid JSON in this exact format:
{{
  "scores": {{
    "CategoryName1": <integer 0-10>,
    "CategoryName2": <integer 0-10>
  }},
  "winner": "<category name or 'Others'>",
  "reasoning": "<one sentence explaining the top match>"
}}"""

        try:
            api_key = get_env_setting("OPENAI_API_KEY")
            client = openai.OpenAI(api_key=api_key if api_key else None)

            response = client.chat.completions.create(
                model=self.llm_model,
                max_tokens=512,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You are a document classifier. Always respond with valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                timeout=60.0,
            )

            response_text = response.choices[0].message.content.strip()
            response_text = re.sub(r"```[a-z]*\n?", "", response_text).strip("` \n")
            result = json.loads(response_text)

        except json.JSONDecodeError as exc:
            _logger_cls.error("LLM returned invalid JSON: %s - falling back to fuzzy.", exc)
            return self._classify_fuzzy(text)
        except Exception as exc:
            _logger_cls.error("LLM call failed: %s - falling back to fuzzy.", exc)
            return self._classify_fuzzy(text)

        scores: dict[str, float] = {}
        raw_scores = result.get("scores", {})

        for cat_name, score_val in raw_scores.items():
            try:
                scores[cat_name] = float(score_val)
            except (TypeError, ValueError):
                scores[cat_name] = 0.0

        winner = result.get("winner", self.OTHERS)
        reasoning = result.get("reasoning", "")

        if winner not in self.categories and winner != self.OTHERS:
            _logger_cls.warning("LLM returned unknown category '%s'; using 'Others'.", winner)
            winner = self.OTHERS

        if scores:
            best_cat = max(scores, key=lambda c: scores[c])
            best_score = scores[best_cat]
            if best_score < self.threshold:
                winner = self.OTHERS
                best_score = max(scores.values()) if scores else 0.0
            else:
                winner = best_cat
        else:
            best_score = 0.0
            winner = self.OTHERS

        _logger_cls.info(
            "LLM scores: %s | Winner: '%s' (score=%s) | %s",
            scores, winner, best_score, reasoning,
        )
        return winner, best_score

    # ── Fuzzy Fallback (Secondary) ─────────────────────────────────────────────

    def _classify_fuzzy(self, text: str) -> tuple[str, float]:
        """
        Legacy fuzzy keyword scorer used when LLM is unavailable.

        Returns:
            (category_name, score_0_to_1)
        """
        normalised_text = _normalise_for_classifier(text)
        scores = self._score_all(normalised_text)

        if not scores:
            return self.OTHERS, 0.0

        best_category = max(scores, key=lambda c: scores[c])
        best_score = scores[best_category]

        _logger_cls.debug("Fuzzy scores: %s", scores)

        fuzzy_threshold = self.threshold / 10.0
        if best_score >= fuzzy_threshold:
            return best_category, best_score * 10.0

        return self.OTHERS, best_score * 10.0

    def _score_all(self, normalised_text: str) -> dict[str, float]:
        """Compute a normalised confidence score (0-1) for every category."""
        scores: dict[str, float] = {}
        tokens = normalised_text.split()

        for category, keywords in self._normalised_keywords.items():
            if not keywords:
                scores[category] = 0.0
                continue

            raw_score = 0.0
            for keyword in keywords:
                match_score = self._score_keyword(keyword, normalised_text, tokens)
                raw_score += match_score

            scores[category] = raw_score / len(keywords)

        return scores

    def _score_keyword(
        self, keyword: str, normalised_text: str, tokens: list[str]
    ) -> float:
        """Return a [0, 1] score indicating how well *keyword* matches the text."""
        if keyword in normalised_text:
            return 1.0

        keyword_tokens = keyword.split()
        if len(keyword_tokens) == 1:
            return self._fuzzy_token_score(keyword, tokens)

        window_size = len(keyword_tokens)
        best = 0.0
        for i in range(max(1, len(tokens) - window_size + 1)):
            window = " ".join(tokens[i : i + window_size])
            score = self._fuzzy_ratio(keyword, window)
            if score > best:
                best = score
            if best == 1.0:
                break
        return best

    @staticmethod
    def _fuzzy_token_score(keyword: str, tokens: list[str]) -> float:
        """Best fuzzy ratio between *keyword* and any single token."""
        try:
            from rapidfuzz import fuzz  # type: ignore

            best = 0.0
            threshold = _FUZZY_RATIO_THRESHOLD / 100
            for token in tokens:
                ratio = fuzz.ratio(keyword, token) / 100
                if ratio > best:
                    best = ratio
                if best >= threshold:
                    return best
            return best if best >= threshold else 0.0
        except ImportError:
            return 0.0

    @staticmethod
    def _fuzzy_ratio(a: str, b: str) -> float:
        """Return normalised fuzzy ratio (0-1) between two strings."""
        try:
            from rapidfuzz import fuzz  # type: ignore

            ratio = fuzz.partial_ratio(a, b) / 100
            return ratio if ratio >= _FUZZY_RATIO_THRESHOLD / 100 else 0.0
        except ImportError:
            return 1.0 if a == b else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 - FILE ORGANIZER
# (originally: organizer.py)
# ══════════════════════════════════════════════════════════════════════════════

_logger_org = get_logger("file_classifier.organizer")


class FileOrganizer:
    """
    Places files into categorised sub-folders under *output_folder*.

    Args:
        output_folder: Root directory for organised output.
        copy_mode: When True, copy files; when False (default), move them.
        dry_run: When True, log what *would* happen without touching files.
    """

    def __init__(
        self,
        output_folder: Path,
        copy_mode: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.output_folder = output_folder
        self.copy_mode = copy_mode
        self.dry_run = dry_run

        if not dry_run:
            output_folder.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def place(self, source: Path, category: str) -> Path:
        """
        Move or copy *source* into the sub-folder named *category*.

        Args:
            source: Absolute path to the source file.
            category: Destination category name (folder will be created).

        Returns:
            The final destination path (even in dry-run mode the *intended*
            path is returned so the report can record it).
        """
        dest_dir  = self.output_folder / category
        dest_path = self._resolve_destination(dest_dir, source.name)

        action = "copy" if self.copy_mode else "move"
        _logger_org.debug("[%s] %s -> %s", action.upper(), source, dest_path)

        if self.dry_run:
            _logger_org.info("[DRY-RUN] Would %s %s -> %s", action, source, dest_path)
            return dest_path

        dest_dir.mkdir(parents=True, exist_ok=True)

        if self.copy_mode:
            shutil.copy2(str(source), str(dest_path))
        else:
            shutil.move(str(source), str(dest_path))

        return dest_path

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_destination(dest_dir: Path, filename: str) -> Path:
        """
        Return a destination path that does not collide with existing files.

        If ``dest_dir/filename`` already exists, append ``_1``, ``_2`` … to
        the stem until a free slot is found.

        Args:
            dest_dir: Target directory (may not yet exist).
            filename: Desired file name.

        Returns:
            A non-colliding ``Path`` inside *dest_dir*.
        """
        candidate = dest_dir / filename
        if not candidate.exists():
            return candidate

        stem = Path(filename).stem
        suffix = Path(filename).suffix
        counter = 1
        while True:
            candidate = dest_dir / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 11 - REPORT GENERATOR
# (originally: report.py)
# ══════════════════════════════════════════════════════════════════════════════

_logger_rep = get_logger("file_classifier.report")

_REPORT_FIELDS = [
    "file_name",
    "original_path",
    "destination_folder",
    "category",
    "pdf_type",
    "confidence",
    "processing_time",
    "error",
]


class ReportGenerator:
    """
    Saves a classification run summary to CSV.

    Args:
        output_folder: Directory where the report file is written.
    """

    REPORT_FILENAME = "classification_report.csv"

    def __init__(self, output_folder: Path) -> None:
        self.output_folder = output_folder

    # ── Public API ─────────────────────────────────────────────────────────────

    def save(self, results: list[dict]) -> Path:
        """
        Write *results* to a CSV file.

        Args:
            results: List of per-document result dictionaries.
                     Each dict should have the keys defined in ``_REPORT_FIELDS``;
                     missing keys are written as empty strings.

        Returns:
            Path to the written report file.
        """
        self.output_folder.mkdir(parents=True, exist_ok=True)
        report_path = self.output_folder / self.REPORT_FILENAME

        file_exists = report_path.exists()

        with report_path.open("a" if file_exists else "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_REPORT_FIELDS, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            for row in results:
                safe_row = {field: row.get(field, "") for field in _REPORT_FIELDS}
                # Fallback mapping from llm_score if confidence is missing or blank
                if not safe_row["confidence"] and "llm_score" in row:
                    try:
                        safe_row["confidence"] = f"{float(row['llm_score']) / 10.0:.4f}"
                    except Exception:
                        pass
                writer.writerow(safe_row)

        total = len(results)
        errors = sum(1 for r in results if r.get("error"))
        categories: dict[str, int] = {}
        for r in results:
            cat = r.get("category", "Others")
            categories[cat] = categories.get(cat, 0) + 1

        _logger_rep.info("Report written: %s", report_path)
        _logger_rep.info(
            "Summary - total=%d, errors=%d, categories=%s", total, errors, categories
        )

        return report_path


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 12 - PIPELINE ORCHESTRATION & CLI ENTRY POINT
# (originally: main.py)
# ══════════════════════════════════════════════════════════════════════════════

_logger_main = get_logger("file_classifier.main")


def _process_pdf(
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
    is_dig = is_digital(file_path)
    pdf_type = "digital" if is_dig else "scanned"

    _logger_main.info("[%s] Detected as: %s", file_path.name, pdf_type.upper())

    if is_dig:
        text, rotation_info, error = extract(file_path, max_pages=pdf_max_pages)
    else:
        text, rotation_info, error = extract_with_auto_rotation(
            file_path,
            max_pages=pdf_max_pages,
            debug_image_folder=debug_image_folder,
            poppler_path=poppler_path,
        )

    if error:
        _logger_main.warning("[%s] Extraction warning: %s", file_path.name, error)

    _logger_main.info(
        "[%s] Extracted %d chars | Rotation: %s",
        file_path.name, len(text), rotation_info,
    )

    return text, pdf_type, rotation_info


def run_pipeline_full(
    input_folder: Path,
    output_folder: Path,
    categories: dict[str, list[str]],
    pdf_max_pages: int = 3,
    min_score: float = 3.0,
    llm_model: str = "gpt-4o",
    copy_mode: bool = False,
    dry_run: bool = False,
    poppler_path: str | None = None,
) -> list[dict]:
    """
    Full pipeline: directory scan -> extract -> classify -> organise -> report.

    This is the main programmatic entry point for using this module in other POCs.

    Args:
        input_folder:  Directory containing input PDFs.
        output_folder: Root directory for classified output sub-folders.
        categories:    Mapping of ``{category_name: [keyword, ...]}``.
        pdf_max_pages: Number of pages to read per PDF for classification.
        min_score:     Minimum LLM score (0-10) to accept a category win.
        llm_model:     OpenAI model identifier (e.g. "gpt-4o").
        copy_mode:     Copy files instead of moving when True.
        dry_run:       Log only - do not move/copy files or write report.
        poppler_path:  Path to Poppler bin folder (Windows). None = use PATH.

    Returns:
        List of per-document result dicts (same schema as the CSV report).
    """
    classifier = DocumentClassifier(
        categories=categories,
        threshold=min_score,
        llm_model=llm_model,
        llm_enabled=True,
    )
    organizer = FileOrganizer(
        output_folder=output_folder,
        copy_mode=copy_mode,
        dry_run=dry_run,
    )
    reporter = ReportGenerator(output_folder=output_folder)

    extracted_text_folder = output_folder / "extracted_text"
    extracted_text_folder.mkdir(parents=True, exist_ok=True)

    rotated_pages_folder = output_folder / "rotated_pages"
    rotated_pages_folder.mkdir(parents=True, exist_ok=True)

    all_files = [
        f for f in input_folder.rglob("*")
        if f.is_file() and f.suffix.lower() == ".pdf"
    ]

    if not all_files:
        _logger_main.warning("No PDF files found in: %s", input_folder)
        return []

    _logger_main.info("Found %d PDF(s) to process.", len(all_files))

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
            "pdf_type":           "",
            "rotation_applied":   "",
            "category":           "Others",
            "llm_score":          0.0,
            "destination_folder": "",
            "processing_time":    0.0,
            "error":              "",
        }

        try:
            text, pdf_type, rotation_info = _process_pdf(
                file_path, pdf_max_pages,
                debug_image_folder=rotated_pages_folder,
                poppler_path=poppler_path,
            )
            record["pdf_type"]         = pdf_type
            record["rotation_applied"] = rotation_info

            # Save extracted text for verification
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
            _logger_main.debug(
                "[%s] Extracted text saved -> %s", file_path.name, txt_path
            )

            if not text.strip():
                _logger_main.warning(
                    "[%s] Empty text after extraction - classifying as Others.", file_path.name
                )
                record["error"] = "Empty text extracted"
            else:
                category, score = classifier.classify(text)
                record["category"]  = category
                record["llm_score"] = round(score * 10, 2)

                _logger_main.info(
                    "[%s] -> %s | score=%.1f/10 | type=%s | rotation=%s | chars=%d",
                    file_path.name, category, score * 10,
                    pdf_type, rotation_info, len(text),
                )

                dest = organizer.place(file_path, category)
                record["destination_folder"] = str(dest)

        except Exception as exc:
            _logger_main.error(
                "[%s] Unhandled error: %s", file_path.name, exc, exc_info=True
            )
            record["error"] = str(exc)

        finally:
            record["processing_time"] = round(time.perf_counter() - start_time, 4)
            results.append(record)

    if not dry_run:
        reporter.save(results)

    _logger_main.info(
        "Done. Processed %d file(s). Report saved to: %s",
        len(results), output_folder,
    )
    return results


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Document Classifier - classify and sort PDFs automatically.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input",  type=Path, help="Input folder containing PDFs.")
    parser.add_argument("--output", type=Path, help="Output root folder for sorted files.")
    parser.add_argument(
        "--config", type=Path, default=Path("config.json"),
        help="Path to JSON config file (for non-category settings).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Classify without moving files (report only).",
    )
    return parser.parse_args()


def main() -> int:
    """
    Full pipeline: directory scan -> extract -> classify -> organise -> report.

    Returns:
        Exit code (0 = success, 1 = fatal error).
    """
    args = _parse_args()

    # ── Load base config ──────────────────────────────────────────────────────
    try:
        cfg = AppConfig.load(args.config)
    except Exception as exc:
        _logger_main.error("Failed to load config: %s", exc)
        return 1

    # ── Load categories from .env ─────────────────────────────────────────────
    env_categories = load_categories_from_env()
    if env_categories:
        cfg.categories = env_categories
        _logger_main.info(
            "Loaded %d category/categories from .env: %s",
            len(env_categories),
            list(env_categories.keys()),
        )
    elif not cfg.categories:
        _logger_main.error(
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
        _logger_main.error("No input folder. Use --input or set input_folder in config.json.")
        return 1
    if not output_folder:
        _logger_main.error("No output folder. Use --output or set output_folder in config.json.")
        return 1

    input_folder  = Path(input_folder)
    output_folder = Path(output_folder)

    if not input_folder.exists():
        _logger_main.error("Input folder does not exist: %s", input_folder)
        return 1

    _logger_main.info("=" * 60)
    _logger_main.info("  AI Document Classifier - LLM Keyword Scoring")
    _logger_main.info("=" * 60)
    _logger_main.info("  Input    : %s", input_folder)
    _logger_main.info("  Output   : %s", output_folder)
    _logger_main.info("  Pages    : first %d page(s) per PDF", pdf_max_pages)
    _logger_main.info("  Min Score: %s / 10", min_score)
    _logger_main.info("  LLM Model: %s", llm_model)
    _logger_main.info("  Poppler  : %s", poppler_path or "(auto / PATH)")
    _logger_main.info("  OCR mode : auto-rotation (pdf2image + OpenCV skew + validation)")
    _logger_main.info("  Dry run  : %s", args.dry_run)
    _logger_main.info("  Categories (%d): %s", len(cfg.categories), list(cfg.categories.keys()))
    _logger_main.info("=" * 60)

    run_pipeline_full(
        input_folder=input_folder,
        output_folder=output_folder,
        categories=cfg.categories,
        pdf_max_pages=pdf_max_pages,
        min_score=min_score,
        llm_model=llm_model,
        copy_mode=cfg.copy_mode,
        dry_run=args.dry_run,
        poppler_path=poppler_path,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())

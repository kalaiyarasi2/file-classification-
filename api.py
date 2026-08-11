"""
api.py - FastAPI REST API for the AI File Classifier Agent.

Swagger UI is available at:  http://127.0.0.1:8000/docs
ReDoc UI is available at:    http://127.0.0.1:8000/redoc

Run with (recommended to avoid watching virtual env / node modules / output directories):
    python api.py
Or via uvicorn CLI:
    uvicorn api:app --reload --host 0.0.0.0 --port 8000 --reload-exclude "venv/*" --reload-exclude "frontend/*" --reload-exclude "logs/*" --reload-exclude "output/*" --reload-exclude "temp_uploads/*" --reload-exclude ".sessions/*"
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import traceback
from pathlib import Path
from typing import Annotated, Optional

from fastapi import (
    FastAPI, File, Form, HTTPException, Query,
    UploadFile, BackgroundTasks, status, Request
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

# ── Import the single-file module ────────────────────────────────────────────
from file_classifier import (
    load_categories_from_env,
    get_env_setting,
    AppConfig,
    is_digital,
    extract,
    extract_scanned,
    extract_with_auto_rotation,
    DocumentClassifier,
    FileOrganizer,
    ReportGenerator,
    run_pipeline_full,
    get_logger,
    safe_filename,
)

# ── Google Drive modules (no API / no OAuth required) ────────────────────────
from google_drive_access import GoogleDriveAccess
from drive_connector import DriveClassifierConnector

# ── OneDrive modules (no API required) ───────────────────────────────────────
from onedrive_access import OneDriveAccess
from onedrive_connector import OneDriveClassifierConnector

logger = get_logger("file_classifier.api")

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="📄 AI File Classifier Agent",
    description="""
## AI-Powered PDF Document Classifier & Organiser

This API exposes the full pipeline of the **File Classifier Agent** as REST endpoints.

### Core Capabilities
- **PDF Type Detection** - Identify whether a PDF has a real text layer (digital) or is image-only (scanned)
- **Text Extraction** - Extract text from digital PDFs (pdfplumber + PyMuPDF) or scanned PDFs (rostaing-OCR + auto-rotation)
- **LLM Classification** - Score each document against configured category keywords using GPT-4o
- **File Organisation** - Move/copy PDFs into categorised output folders
- **Batch Pipeline** - Process an entire directory of PDFs end-to-end

### Authentication
All endpoints read `OPENAI_API_KEY` from the `.env` file or environment variable.

### Configuration
Categories and keywords are loaded from `CATEGORY_*` entries in the `.env` file.
""",
    version="1.0.0",
    contact={
        "name": "File Classifier Agent",
        "url": "https://github.com/your-org/file-classifier-agent",
    },
    license_info={
        "name": "MIT",
    },
    openapi_tags=[
        {"name": "🏥 Health",       "description": "Server health and readiness checks."},
        {"name": "⚙️ Config",       "description": "Read current configuration and categories."},
        {"name": "🔍 Detection",    "description": "Detect whether a PDF is digital or scanned."},
        {"name": "📝 Extraction",   "description": "Extract text from a PDF file."},
        {"name": "🏷️ Classification", "description": "Classify document text or a PDF against configured categories."},
        {"name": "📁 Organisation", "description": "Organise (move/copy) a file into its category folder."},
        {"name": "🚀 Pipeline",     "description": "Run the full end-to-end classification pipeline on a folder."},
        {"name": "📊 Report",       "description": "Retrieve the last classification report."},
        {"name": "☁️ Google Drive",  "description": "Check Google Drive connection and run classification directly from a mounted Drive folder."},
        {"name": "☁️ OneDrive",      "description": "Check OneDrive connection and run classification directly from a synchronized OneDrive folder."},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Google Drive OAuth & Cloud API Routes ─────────────────────────────────────
from google_oauth import router as google_oauth_router
app.include_router(google_oauth_router)

# ── OneDrive OAuth & Cloud API Routes ─────────────────────────────────────────
from onedrive_oauth import router as onedrive_oauth_router
app.include_router(onedrive_oauth_router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def read_root():
    html_file = Path(__file__).parent / "index.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return RedirectResponse(url="http://localhost:8080/")


@app.get("/select-folder")
def select_folder():
    """
    Opens a native Windows folder selection dialog via PowerShell.
    Returns the absolute path, or null if cancelled/unsupported.
    """
    import subprocess
    import sys

    # Only supported on Windows hosts
    if sys.platform != "win32":
        return {"path": None}

    ps_code = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.ShowNewFolderButton = $true; "
        "$dialog.Description = 'Select Folder'; "
        "$form = New-Object System.Windows.Forms.Form; "
        "$form.TopMost = $true; "
        "$result = $dialog.ShowDialog($form); "
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { "
        "  Write-Output $dialog.SelectedPath "
        "}"
    )

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
            capture_output=True,
            text=True,
            check=True,
            creationflags=0x08000000
        )
        path = proc.stdout.strip()
        return {"path": path if path else None}
    except Exception as e:
        logger.error("Error selecting folder via PowerShell: %s", e)
        # Fallback to tkinter
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory()
            root.destroy()
            return {"path": path if path else None}
        except Exception as tk_err:
            logger.error("Tkinter fallback folder selection failed: %s", tk_err)
            return {"path": None}


@app.get("/list-directories", tags=["🚀 Pipeline"], summary="List subdirectories under a given path")
def list_directories(path: Optional[str] = None):
    """
    Lists subdirectories under the given absolute path to support a web-based folder picker.
    If no path is provided, starts at the current working directory.
    """
    import os
    from pathlib import Path

    if not path or path.strip() == "":
        # Default to current working directory
        target_path = Path.cwd().resolve()
    else:
        target_path = Path(path).resolve()

    if not target_path.exists() or not target_path.is_dir():
        # Fallback to CWD
        target_path = Path.cwd().resolve()

    subdirs = []
    parent_path = str(target_path.parent) if target_path.parent != target_path else None

    # Get drive letters on Windows if at root level or listing drives
    drives = []
    if os.name == "nt":
        import string
        from ctypes import windll
        try:
            bitmask = windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(f"{letter}:\\")
                bitmask >>= 1
        except Exception as drive_err:
            logger.warning("Could not list drive letters: %s", drive_err)

    try:
        for entry in os.scandir(target_path):
            try:
                if entry.is_dir() and not entry.name.startswith('.'):
                    subdirs.append({
                        "name": entry.name,
                        "path": str(Path(entry.path).resolve())
                    })
            except Exception:
                pass
        subdirs.sort(key=lambda x: x["name"].lower())
    except Exception as e:
        logger.error("Error listing directories under %s: %s", target_path, e)
        raise HTTPException(status_code=500, detail=f"Could not list directories: {str(e)}")

    return {
        "current_path": str(target_path),
        "parent_path": parent_path,
        "subdirectories": subdirs,
        "drives": drives
    }



# ══════════════════════════════════════════════════════════════════════════════
# Pydantic response / request models
# ══════════════════════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: str = Field(..., example="ok")
    version: str = Field(..., example="1.0.0")
    categories_loaded: int = Field(..., example=5)
    llm_model: str = Field(..., example="gpt-4o")
    openai_key_configured: bool = Field(..., example=True)


class CategoryInfo(BaseModel):
    name: str = Field(..., example="INSURANCE_CLAIMS")
    keyword_count: int = Field(..., example=20)
    keywords: list[str] = Field(..., example=["loss run", "claim number"])


class ConfigResponse(BaseModel):
    llm_model: str = Field(..., example="gpt-4o")
    min_score_threshold: float = Field(..., example=7.0)
    pdf_max_pages: int = Field(..., example=3)
    poppler_path: Optional[str] = Field(None, example="C:\\poppler\\bin")
    categories: list[CategoryInfo]


class DetectResponse(BaseModel):
    filename: str = Field(..., example="invoice_2024.pdf")
    pdf_type: str = Field(..., example="digital", description="`digital` or `scanned`")
    is_digital: bool = Field(..., example=True)
    detection_time_sec: float = Field(..., example=0.312)


class ExtractResponse(BaseModel):
    filename: str = Field(..., example="invoice_2024.pdf")
    pdf_type: str = Field(..., example="digital")
    char_count: int = Field(..., example=3842)
    rotation_info: str = Field(..., example="no rotation needed")
    text_preview: str = Field(..., example="Invoice Number: 10042\nBill To: Acme Corp\n...")
    text_full: str = Field(..., example="Full extracted text of the PDF...")
    error: str = Field("", example="")
    extraction_time_sec: float = Field(..., example=1.23)


class ClassifyTextRequest(BaseModel):
    text: str = Field(
        ...,
        min_length=10,
        example="Invoice Number: 10042\nBill To: Acme Corp\nAmount Due: $1,200.00",
        description="Raw document text to classify against all configured categories.",
    )
    llm_model: Optional[str] = Field(
        None,
        example="gpt-4o",
        description="Override the default LLM model from `.env`.",
    )
    threshold: Optional[float] = Field(
        None,
        ge=0, le=10,
        example=7.0,
        description="Minimum score (0-10) to assign a category. Defaults to MIN_SCORE_THRESHOLD.",
    )


class ScoreDetail(BaseModel):
    category: str = Field(..., example="INVOICE")
    score: float = Field(..., example=8.5)


class ClassifyResponse(BaseModel):
    filename: Optional[str] = Field(None, example="invoice_2024.pdf")
    category: str = Field(..., example="INVOICE")
    confidence_score: float = Field(..., example=0.85, description="Score normalised to [0.0, 1.0]")
    llm_score_0_10: float = Field(..., example=8.5, description="Raw LLM score 0–10")
    pdf_type: Optional[str] = Field(None, example="digital")
    rotation_info: Optional[str] = Field(None, example="no rotation needed")
    classification_time_sec: float = Field(..., example=2.14)
    error: str = Field("", example="")
    extracted_text: str = Field("", description="The full extracted text of the document.")


class OrganiseRequest(BaseModel):
    source_path: str = Field(
        ...,
        example="C:/Users/Intern/input/invoice_2024.pdf",
        description="Absolute path to the source PDF file.",
    )
    category: str = Field(
        ...,
        example="INVOICE",
        description="Target category folder name.",
    )
    output_folder: str = Field(
        ...,
        example="C:/Users/Intern/output",
        description="Root output directory. The file is placed in `<output_folder>/<category>/`.",
    )
    copy_mode: bool = Field(
        False,
        description="Copy the file instead of moving it.",
    )
    dry_run: bool = Field(
        False,
        description="Simulate the operation without touching the filesystem.",
    )


class OrganiseResponse(BaseModel):
    source_path: str
    destination_path: str
    category: str
    action: str = Field(..., example="move")
    dry_run: bool


class PipelineRequest(BaseModel):
    input_folder: str = Field(
        ...,
        example="C:/Users/Intern/input",
        description="Absolute path to the input folder containing PDFs.",
    )
    output_folder: str = Field(
        ...,
        example="C:/Users/Intern/output",
        description="Absolute path to the root output folder.",
    )
    pdf_max_pages: int = Field(3, ge=1, le=20, description="Pages to read per PDF.")
    min_score: float = Field(7.0, ge=0, le=10, description="Min LLM score to accept a category.")
    llm_model: str = Field("gpt-4o", description="OpenAI model to use for classification.")
    copy_mode: bool = Field(False, description="Copy files instead of moving them.")
    dry_run: bool = Field(False, description="Classify without moving files.")


class PipelineResultItem(BaseModel):
    file_name: str
    original_path: str
    pdf_type: str
    category: str
    llm_score: float
    destination_folder: str
    rotation_applied: str
    processing_time: float
    error: str


class PipelineResponse(BaseModel):
    total_files: int
    successful: int
    failed: int
    categories_found: dict[str, int]
    total_time_sec: float
    results: list[PipelineResultItem]


class ReportRow(BaseModel):
    file_name: str
    original_path: str
    destination_folder: str
    category: str
    pdf_type: Optional[str] = ""
    confidence: str
    processing_time: str
    error: str


class ReportResponse(BaseModel):
    report_path: str
    total_rows: int
    rows: list[ReportRow]


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_categories() -> dict[str, list[str]]:
    return load_categories_from_env()


def _get_classifier(
    llm_model: str | None = None,
    threshold: float | None = None,
    active_categories: list[str] | None = None,
) -> DocumentClassifier:
    categories = _get_categories()
    if active_categories is not None:
        categories = {k: v for k, v in categories.items() if k in active_categories}
    model = llm_model or get_env_setting("LLM_MODEL", "gpt-4o")
    thresh = threshold if threshold is not None else float(get_env_setting("MIN_SCORE_THRESHOLD", "3"))
    return DocumentClassifier(categories=categories, threshold=thresh, llm_model=model, llm_enabled=True)


def _save_upload_to_temp(upload: UploadFile) -> Path:
    """Save an UploadFile to a temp file and return its path."""
    suffix = Path(upload.filename or "upload.pdf").suffix or ".pdf"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        shutil.copyfileobj(upload.file, tmp)
    finally:
        tmp.close()
    return Path(tmp.name)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 - HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/health",
    tags=["🏥 Health"],
    summary="Server health check",
    response_model=HealthResponse,
)
def health_check() -> HealthResponse:
    """
    Returns server status, number of loaded categories, configured LLM model,
    and whether the OpenAI API key is set.
    """
    categories = _get_categories()
    api_key = get_env_setting("OPENAI_API_KEY")
    return HealthResponse(
        status="ok",
        version="1.0.0",
        categories_loaded=len(categories),
        llm_model=get_env_setting("LLM_MODEL", "gpt-4o"),
        openai_key_configured=bool(api_key),
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 - CONFIG
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/config",
    tags=["⚙️ Config"],
    summary="Get current configuration",
    response_model=ConfigResponse,
)
def get_config() -> ConfigResponse:
    """
    Returns the current configuration loaded from `.env`:
    - LLM model
    - Score threshold
    - Max pages per PDF
    - Poppler path
    - All configured categories with their keywords
    """
    categories = _get_categories()
    cat_list = [
        CategoryInfo(
            name=name,
            keyword_count=len(kws),
            keywords=kws,
        )
        for name, kws in categories.items()
    ]
    return ConfigResponse(
        llm_model=get_env_setting("LLM_MODEL", "gpt-4o"),
        min_score_threshold=float(get_env_setting("MIN_SCORE_THRESHOLD", "3")),
        pdf_max_pages=int(get_env_setting("PDF_MAX_PAGES", "3")),
        poppler_path=get_env_setting("POPPLER_PATH") or None,
        categories=cat_list,
    )


@app.get(
    "/config/categories",
    tags=["⚙️ Config"],
    summary="List all categories and keywords",
    response_model=list[CategoryInfo],
)
def list_categories() -> list[CategoryInfo]:
    """
    Returns a list of all configured document categories with their keyword lists.
    Categories are loaded from `CATEGORY_*` entries in the `.env` file.
    """
    categories = _get_categories()
    return [
        CategoryInfo(name=name, keyword_count=len(kws), keywords=kws)
        for name, kws in categories.items()
    ]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 - DETECTION
# ══════════════════════════════════════════════════════════════════════════════

@app.post(
    "/detect",
    tags=["🔍 Detection"],
    summary="Detect PDF type (digital vs scanned)",
    response_model=DetectResponse,
)
async def detect_pdf_type(
    file: Annotated[UploadFile, File(description="PDF file to inspect (max 50 MB).")],
) -> DetectResponse:
    """
    Uploads a PDF and determines whether it has a real text layer (**digital**)
    or is image-only (**scanned**).

    Uses PyMuPDF to sample the first 2 pages. If the total extracted characters
    exceed the threshold, the PDF is classified as digital.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    tmp_path = _save_upload_to_temp(file)
    t0 = time.perf_counter()
    try:
        result = is_digital(tmp_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)

    return DetectResponse(
        filename=file.filename or "upload.pdf",
        pdf_type="digital" if result else "scanned",
        is_digital=result,
        detection_time_sec=round(time.perf_counter() - t0, 4),
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 - TEXT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

@app.post(
    "/extract",
    tags=["📝 Extraction"],
    summary="Extract text from a PDF",
    response_model=ExtractResponse,
)
async def extract_text(
    file: Annotated[UploadFile, File(description="PDF file to extract text from.")],
    max_pages: Annotated[int, Query(ge=1, le=20, description="Maximum pages to read.")] = 3,
    force_ocr: Annotated[bool, Query(description="Force OCR even for digital PDFs.")] = False,
    use_auto_rotation: Annotated[bool, Query(description="Use advanced auto-rotation OCR pipeline for scanned PDFs.")] = True,
) -> ExtractResponse:
    """
    Uploads a PDF and extracts its text content.

    **Flow:**
    1. Detect whether the PDF is digital or scanned.
    2. Route to the appropriate extractor:
       - **Digital** -> pdfplumber (primary) -> PyMuPDF (fallback) -> rostaing-OCR (CID fallback)
       - **Scanned** -> pdf2image 300 DPI -> OSD + OpenCV skew -> validate -> retry -> rostaing-OCR

    Returns the full extracted text along with rotation correction details.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    tmp_path = _save_upload_to_temp(file)
    t0 = time.perf_counter()
    error_msg = ""

    try:
        dig = is_digital(tmp_path) and not force_ocr
        pdf_type = "digital" if dig else "scanned"

        if dig:
            text, rotation_info, error_msg = extract(tmp_path, max_pages=max_pages)
        elif use_auto_rotation:
            poppler = get_env_setting("POPPLER_PATH") or None
            text, rotation_info, error_msg = extract_with_auto_rotation(
                tmp_path, max_pages=max_pages, poppler_path=poppler
            )
        else:
            text, rotation_info, error_msg = extract_scanned(tmp_path, max_pages=max_pages)

    except Exception as exc:
        logger.error("Extraction failed for %s: %s", file.filename, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)

    return ExtractResponse(
        filename=file.filename or "upload.pdf",
        pdf_type=pdf_type,
        char_count=len(text),
        rotation_info=rotation_info,
        text_preview=text[:500] + ("…" if len(text) > 500 else ""),
        text_full=text,
        error=error_msg,
        extraction_time_sec=round(time.perf_counter() - t0, 4),
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 - CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

@app.post(
    "/classify/text",
    tags=["🏷️ Classification"],
    summary="Classify raw text",
    response_model=ClassifyResponse,
)
def classify_text(body: ClassifyTextRequest) -> ClassifyResponse:
    """
    Classifies a raw text string against all configured categories.

    **Algorithm:**
    1. Build an LLM prompt containing the text + all category keywords.
    2. GPT scores each category 0–10 based on keyword presence.
    3. The highest-scoring category wins (if it meets the threshold).
    4. Falls back to fuzzy keyword matching if the LLM call fails.

    Useful for testing classification without uploading a PDF.
    """
    t0 = time.perf_counter()
    try:
        classifier = _get_classifier(body.llm_model, body.threshold)
        category, score = classifier.classify(body.text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return ClassifyResponse(
        category=category,
        confidence_score=round(score, 4),
        llm_score_0_10=round(score * 10, 2),
        classification_time_sec=round(time.perf_counter() - t0, 4),
        extracted_text=body.text,
    )


@app.post(
    "/classify/pdf",
    tags=["🏷️ Classification"],
    summary="Upload a PDF and classify it",
    response_model=ClassifyResponse,
)
async def classify_pdf(
    file: Annotated[UploadFile, File(description="PDF file to classify.")],
    max_pages: Annotated[int, Query(ge=1, le=20)] = 3,
    llm_model: Annotated[Optional[str], Query(description="Override LLM model.")] = None,
    threshold: Annotated[Optional[float], Query(ge=0, le=10, description="Override min score.")] = None,
    force_ocr: Annotated[bool, Query(description="Force OCR even for digital PDFs.")] = False,
    categories: Annotated[Optional[str], Query(description="Comma-separated list of active categories.")] = None,
) -> ClassifyResponse:
    """
    Uploads a PDF, extracts its text, classifies it, and saves the outputs to the server.

    **Full flow:**
    1. Detect PDF type (digital vs scanned).
    2. Extract text (with rotation correction and debug image output).
    3. Send text to the LLM for keyword scoring.
    4. Save the PDF, extracted text, and rotated images on the server.
    5. Return the winning category, confidence score, and text.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    import uuid
    original_name = file.filename or "upload.pdf"
    safe_name = safe_filename(original_name)
    
    # Unique subdirectory to handle concurrent requests safely
    temp_dir = Path("temp_uploads") / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = temp_dir / safe_name

    # Save uploaded file bytes
    with tmp_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    t0 = time.perf_counter()
    error_msg = ""
    output_folder = Path("output")

    try:
        dig = is_digital(tmp_path) and not force_ocr
        pdf_type = "digital" if dig else "scanned"

        # Create output folders on server
        extracted_text_folder = output_folder / "extracted_text"
        extracted_text_folder.mkdir(parents=True, exist_ok=True)
        
        rotated_pages_folder = output_folder / "rotated_pages"

        if dig:
            text, rotation_info, error_msg = extract(tmp_path, max_pages=max_pages)
        else:
            poppler = get_env_setting("POPPLER_PATH") or None
            text, rotation_info, error_msg = extract_with_auto_rotation(
                tmp_path,
                max_pages=max_pages,
                debug_image_folder=rotated_pages_folder,
                poppler_path=poppler
            )

        category = "Others"
        score = 0.0

        if text.strip():
            active_cats = [c.strip() for c in categories.split(",")] if categories else None
            classifier = _get_classifier(llm_model, threshold, active_categories=active_cats)
            category, score = classifier.classify(text)

        # ── Save Processed PDF File on Server ──
        organizer = FileOrganizer(output_folder=output_folder, copy_mode=True)
        dest_path = organizer.place(tmp_path, category)

        # ── Save Extracted Text on Server ──
        txt_filename = tmp_path.stem + ".txt"
        txt_path = extracted_text_folder / txt_filename
        txt_header = (
            f"File      : {original_name}\n"
            f"PDF Type  : {pdf_type}\n"
            f"Rotation  : {rotation_info}\n"
            f"Chars     : {len(text)}\n"
            f"{'=' * 60}\n\n"
        )
        txt_path.write_text(txt_header + text, encoding="utf-8")

    except Exception as exc:
        logger.error("classify_pdf failed for %s: %s", original_name, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        # Clean up temporary upload folder
        shutil.rmtree(str(temp_dir), ignore_errors=True)

    return ClassifyResponse(
        filename=original_name,
        category=category,
        confidence_score=round(score, 4),
        llm_score_0_10=round(score * 10, 2),
        pdf_type=pdf_type,
        rotation_info=rotation_info,
        classification_time_sec=round(time.perf_counter() - t0, 4),
        error=error_msg or ("Empty text extracted" if not text.strip() else ""),
        extracted_text=text,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 - FILE ORGANISATION
# ══════════════════════════════════════════════════════════════════════════════

@app.post(
    "/organise",
    tags=["📁 Organisation"],
    summary="Move or copy a file into its category folder",
    response_model=OrganiseResponse,
)
def organise_file(body: OrganiseRequest) -> OrganiseResponse:
    """
    Moves (or copies) a PDF from `source_path` into
    `<output_folder>/<category>/`.

    - If the destination file already exists, a numeric suffix is appended.
    - Use `dry_run=true` to preview the operation without moving any files.
    - Use `copy_mode=true` to copy instead of move.
    """
    source = Path(body.source_path)
    if not source.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Source file not found: {body.source_path}",
        )

    try:
        organizer = FileOrganizer(
            output_folder=Path(body.output_folder),
            copy_mode=body.copy_mode,
            dry_run=body.dry_run,
        )
        dest = organizer.place(source, body.category)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return OrganiseResponse(
        source_path=str(source),
        destination_path=str(dest),
        category=body.category,
        action="copy" if body.copy_mode else "move",
        dry_run=body.dry_run,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 - FULL PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

@app.post(
    "/pipeline/run",
    tags=["🚀 Pipeline"],
    summary="Run the full classification pipeline on a folder",
    response_model=PipelineResponse,
)
def run_pipeline_endpoint(body: PipelineRequest) -> PipelineResponse:
    """
    Runs the **complete end-to-end pipeline** on every PDF inside `input_folder`:

    1. **Detect** - Digital or scanned?
    2. **Extract** - Text extraction with rotation correction.
    3. **Classify** - LLM keyword scoring (GPT-4o).
    4. **Organise** - Move/copy into `<output_folder>/<Category>/`.
    5. **Report** - Write `classification_report.csv` to `output_folder`.

    > ⚠️ This endpoint can take several minutes for large batches.
    > Consider running with `dry_run=true` first to validate the setup.
    """
    input_folder  = Path(body.input_folder)
    output_folder = Path(body.output_folder)

    if not input_folder.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Input folder not found: {body.input_folder}",
        )

    categories = _get_categories()
    if not categories:
        raise HTTPException(
            status_code=422,
            detail="No categories configured. Add CATEGORY_* entries to your .env file.",
        )

    poppler = get_env_setting("POPPLER_PATH") or None
    t0 = time.perf_counter()

    try:
        results = run_pipeline_full(
            input_folder=input_folder,
            output_folder=output_folder,
            categories=categories,
            pdf_max_pages=body.pdf_max_pages,
            min_score=body.min_score,
            llm_model=body.llm_model,
            copy_mode=body.copy_mode,
            dry_run=body.dry_run,
            poppler_path=poppler,
        )
    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    total_time = round(time.perf_counter() - t0, 2)

    # Aggregate category counts
    cat_counts: dict[str, int] = {}
    for r in results:
        cat = r.get("category", "Others")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    failed = sum(1 for r in results if r.get("error"))
    items = [
        PipelineResultItem(
            file_name=r.get("file_name", ""),
            original_path=r.get("original_path", ""),
            pdf_type=r.get("pdf_type", ""),
            category=r.get("category", "Others"),
            llm_score=float(r.get("llm_score", 0)),
            destination_folder=r.get("destination_folder", ""),
            rotation_applied=r.get("rotation_applied", ""),
            processing_time=float(r.get("processing_time", 0)),
            error=r.get("error", ""),
        )
        for r in results
    ]

    return PipelineResponse(
        total_files=len(results),
        successful=len(results) - failed,
        failed=failed,
        categories_found=cat_counts,
        total_time_sec=total_time,
        results=items,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 - REPORT
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/report",
    tags=["📊 Report"],
    summary="Read the classification report CSV",
    response_model=ReportResponse,
)
def get_report(
    output_folder: Annotated[str, Query(
        description="Absolute path to the output folder containing `classification_report.csv`.",
        example="C:/Users/Intern/output",
    )],
) -> ReportResponse:
    """
    Reads and returns the `classification_report.csv` produced by the pipeline.

    The CSV contains one row per processed PDF with columns:
    `file_name`, `original_path`, `destination_folder`, `category`,
    `confidence`, `processing_time`, `error`.
    """
    import csv as _csv

    report_path = Path(output_folder) / "classification_report.csv"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report not found at: {report_path}. Run the pipeline first.",
        )

    rows: list[ReportRow] = []
    with report_path.open(encoding="utf-8") as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            rows.append(ReportRow(
                file_name=row.get("file_name", ""),
                original_path=row.get("original_path", ""),
                destination_folder=row.get("destination_folder", ""),
                category=row.get("category", ""),
                pdf_type=row.get("pdf_type", ""),
                confidence=row.get("confidence", ""),
                processing_time=row.get("processing_time", ""),
                error=row.get("error", ""),
            ))

    return ReportResponse(
        report_path=str(report_path),
        total_rows=len(rows),
        rows=rows,
    )


@app.get(
    "/report/download",
    tags=["📊 Report"],
    summary="Download the classification report CSV file",
    response_class=FileResponse,
)
def download_report(
    output_folder: Annotated[str, Query(
        description="Absolute path to the output folder.",
        example="C:/Users/Intern/output",
    )],
):
    """
    Returns the `classification_report.csv` as a downloadable file attachment.
    """
    report_path = Path(output_folder) / "classification_report.csv"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report not found at: {report_path}",
        )
    return FileResponse(
        path=str(report_path),
        filename="classification_report.csv",
        media_type="text/csv",
    )


class AddCategoryRequest(BaseModel):
    name: str = Field(..., example="UTILITY_BILLS")
    keywords: list[str] = Field(..., example=["electricity", "water", "gas"])


@app.post(
    "/config/categories",
    tags=["⚙️ Config"],
    summary="Add a new category with keywords permanently to config",
)
def add_category(body: AddCategoryRequest):
    import re
    clean_name = body.name.strip().upper().replace(" ", "_").replace("-", "_")
    if not clean_name:
        raise HTTPException(status_code=400, detail="Category name cannot be empty.")
    if not re.match(r"^[A-Z0-9_]+$", clean_name):
        raise HTTPException(
            status_code=400,
            detail="Category name must be alphanumeric and contain only letters, numbers, and underscores.",
        )
    
    clean_kws = [kw.strip() for kw in body.keywords if kw.strip()]
    if not clean_kws:
        raise HTTPException(
            status_code=400,
            detail="At least one valid keyword must be provided.",
        )
    
    env_path = Path(__file__).parent / ".env"
    
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    
    key_to_find = f"CATEGORY_{clean_name}"
    exists_idx = -1
    for idx, line in enumerate(lines):
        if line.strip().startswith(key_to_find + "="):
            exists_idx = idx
            break
            
    new_line = f"{key_to_find}={','.join(clean_kws)}"
    
    if exists_idx != -1:
        lines[exists_idx] = new_line
    else:
        lines.append(new_line)
        
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    
    logger.info("Category '%s' saved dynamically to .env file.", clean_name)
    
    return {"status": "success", "category": clean_name, "keywords": clean_kws}


class SaveReportRequest(BaseModel):
    results: list[dict]


@app.post(
    "/report/save",
    tags=["📊 Report"],
    summary="Save a batch run classification report CSV on the server",
)
def save_report(body: SaveReportRequest):
    try:
        from file_classifier import ReportGenerator
        reporter = ReportGenerator(output_folder=Path("output"))
        report_path = reporter.save(body.results)
        return {"status": "success", "report_path": str(report_path), "total_rows": len(body.results)}
    except Exception as exc:
        logger.error("Failed to save report on server: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 - GOOGLE DRIVE
# ══════════════════════════════════════════════════════════════════════════════

class DriveStatusResponse(BaseModel):
    connected:       bool  = Field(..., description="True if the Drive mount is accessible.")
    drive_root:      str   = Field(..., description="Configured or auto-detected Drive root path.")
    drive_input:     str   = Field(..., description="Input folder the classifier reads PDFs from.")
    drive_output:    str   = Field(..., description="Output folder sorted files are written to.")
    pdf_count:       int   = Field(..., description="Number of PDF files found in the input folder.")
    pdf_files:       list  = Field(..., description="List of PDF filenames found.")
    input_ok:        bool
    output_ok:       bool


class DriveClassifyRequest(BaseModel):
    drive_input_folder:  Optional[str] = Field(
        None, example="G:/My Drive/uploads",
        description="Drive input folder. Defaults to GOOGLE_DRIVE_ROOT in .env.",
    )
    drive_output_folder: Optional[str] = Field(
        None, example="G:/My Drive/sorted",
        description="Drive output folder. Defaults to GOOGLE_DRIVE_OUTPUT in .env.",
    )
    copy_mode:   bool  = Field(True,  description="Keep originals in Drive when True.")
    dry_run:     bool  = Field(False, description="Classify without writing files.")
    pdf_max_pages: int = Field(3,     ge=1, le=20, description="Pages to read per PDF.")
    min_score: float   = Field(7.0,   ge=0, le=10, description="Min LLM score to accept a category.")
    llm_model: Optional[str] = Field(None, description="OpenAI model override.")


@app.get(
    "/drive/status",
    tags=["☁️ Google Drive"],
    summary="Check Google Drive connection and list available PDFs",
    response_model=DriveStatusResponse,
)
def drive_status(
    input_folder: Optional[str] = Query(
        None,
        description="Drive input folder path. Defaults to GOOGLE_DRIVE_ROOT in .env.",
    )
) -> DriveStatusResponse:
    """
    Verifies that the locally mounted Google Drive folder is accessible
    and counts the PDF files waiting to be classified.

    No API key or OAuth required — uses the drive letter mounted by
    'Google Drive for Desktop'.
    """
    drive_input = input_folder or get_env_setting("GOOGLE_DRIVE_ROOT")
    drive_output = get_env_setting("GOOGLE_DRIVE_OUTPUT")

    try:
        connector = DriveClassifierConnector(
            drive_input_folder=drive_input or None,
            drive_output_folder=drive_output or None,
            dry_run=True,
        )
        status = connector.validate()

        gda = GoogleDriveAccess(drive_root=connector.drive_input)
        pdf_names = [Path(p).name for p in status["pdf_files"]]

        return DriveStatusResponse(
            connected=status["input_ok"],
            drive_root=str(connector.drive_input.parent) if connector.drive_input else "",
            drive_input=str(connector.drive_input),
            drive_output=str(connector.drive_output),
            pdf_count=status["pdf_count"],
            pdf_files=pdf_names,
            input_ok=status["input_ok"],
            output_ok=status["output_ok"],
        )
    except Exception as exc:
        logger.error("drive_status error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/drive/classify",
    tags=["☁️ Google Drive"],
    summary="Classify all PDFs in a Google Drive folder and write results back to Drive",
)
def drive_classify(body: DriveClassifyRequest):
    """
    Runs the full AI classification pipeline on every PDF found in the
    Google Drive input folder, then copies the sorted files back to the
    Drive output folder (organised by category).

    Workflow:
    1. Read PDFs from **drive_input_folder** (mounted Drive path)
    2. Copy to a local temp directory for reliable OCR / pdfplumber processing
    3. Run the classifier (GPT-4o scoring)
    4. Write sorted files to **drive_output_folder/{Category}/**
    5. Optionally delete originals from the input folder (copy_mode=False)

    No API key or OAuth required for Drive access — only the OpenAI key
    (already in .env) is needed for classification.
    """
    try:
        connector = DriveClassifierConnector(
            drive_input_folder=body.drive_input_folder or None,
            drive_output_folder=body.drive_output_folder or None,
            copy_mode=body.copy_mode,
            dry_run=body.dry_run,
            pdf_max_pages=body.pdf_max_pages,
            min_score=body.min_score,
            llm_model=body.llm_model,
        )
        result = connector.run()
        return JSONResponse(content=result)
    except Exception as exc:
        logger.error("drive_classify error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 - ONEDRIVE
# ══════════════════════════════════════════════════════════════════════════════

class OneDriveStatusResponse(BaseModel):
    connected:       bool  = Field(..., description="True if the OneDrive root is accessible.")
    onedrive_root:   str   = Field(..., description="Configured or auto-detected OneDrive root path.")
    onedrive_input:  str   = Field(..., description="Input folder the classifier reads PDFs from.")
    onedrive_output: str   = Field(..., description="Output folder sorted files are written to.")
    pdf_count:       int   = Field(..., description="Number of PDF files found in the input folder.")
    pdf_files:       list  = Field(..., description="List of PDF filenames found.")
    input_ok:        bool
    output_ok:       bool


class OneDriveClassifyRequest(BaseModel):
    onedrive_input_folder:  Optional[str] = Field(
        None, example="C:/Users/Intern/OneDrive/uploads",
        description="OneDrive input folder. Defaults to ONEDRIVE_ROOT in .env or auto-detected.",
    )
    onedrive_output_folder: Optional[str] = Field(
        None, example="C:/Users/Intern/OneDrive/sorted",
        description="OneDrive output folder. Defaults to ONEDRIVE_OUTPUT in .env or auto-detected.",
    )
    copy_mode:   bool  = Field(True,  description="Keep originals in OneDrive when True.")
    dry_run:     bool  = Field(False, description="Classify without writing files.")
    pdf_max_pages: int = Field(3,     ge=1, le=20, description="Pages to read per PDF.")
    min_score: float   = Field(7.0,   ge=0, le=10, description="Min LLM score to accept a category.")
    llm_model: Optional[str] = Field(None, description="OpenAI model override.")


@app.get(
    "/onedrive/status",
    tags=["☁️ OneDrive"],
    summary="Check OneDrive connection and list available PDFs",
    response_model=OneDriveStatusResponse,
)
def onedrive_status(
    input_folder: Optional[str] = Query(
        None,
        description="OneDrive input folder path. Defaults to ONEDRIVE_ROOT in .env.",
    )
) -> OneDriveStatusResponse:
    """
    Verifies that the locally synchronized OneDrive folder is accessible
    and counts the PDF files waiting to be classified.
    """
    onedrive_input = input_folder or get_env_setting("ONEDRIVE_ROOT")
    onedrive_output = get_env_setting("ONEDRIVE_OUTPUT")

    try:
        connector = OneDriveClassifierConnector(
            drive_input_folder=onedrive_input or None,
            drive_output_folder=onedrive_output or None,
            dry_run=True,
        )
        status = connector.validate()

        oda = OneDriveAccess(drive_root=connector.drive_input)
        pdf_names = [Path(p).name for p in status["pdf_files"]]

        return OneDriveStatusResponse(
            connected=status["input_ok"],
            onedrive_root=str(connector.drive_input.parent) if connector.drive_input else "",
            onedrive_input=str(connector.drive_input),
            onedrive_output=str(connector.drive_output),
            pdf_count=status["pdf_count"],
            pdf_files=pdf_names,
            input_ok=status["input_ok"],
            output_ok=status["output_ok"],
        )
    except Exception as exc:
        logger.error("onedrive_status error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/onedrive/classify",
    tags=["☁️ OneDrive"],
    summary="Classify all PDFs in a OneDrive folder and write results back to OneDrive",
)
def onedrive_classify(body: OneDriveClassifyRequest):
    """
    Runs the full AI classification pipeline on every PDF found in the
    OneDrive input folder, then copies the sorted files back to the
    OneDrive output folder (organised by category).
    """
    try:
        connector = OneDriveClassifierConnector(
            drive_input_folder=body.onedrive_input_folder or None,
            drive_output_folder=body.onedrive_output_folder or None,
            copy_mode=body.copy_mode,
            dry_run=body.dry_run,
            pdf_max_pages=body.pdf_max_pages,
            min_score=body.min_score,
            llm_model=body.llm_model,
        )
        result = connector.run()
        return JSONResponse(content=result)
    except Exception as exc:
        logger.error("onedrive_classify error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 11 - BACKGROUND AUTOMATION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

import subprocess
import json
import sys
from onedrive_oauth import get_valid_token as od_get_token
from google_oauth import get_credentials_from_cookie as google_get_creds

STATE_FILE = Path(__file__).parent / ".sessions" / "pipeline_process.json"

def is_pid_running(pid: int) -> bool:
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=True,
            creationflags=0x08000000
        )
        return str(pid) in proc.stdout
    except Exception:
        return False

def stop_pid(pid: int):
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            check=True,
            creationflags=0x08000000
        )
    except Exception as e:
        logger.warning("Failed to kill process %d: %s", pid, e)

@app.get("/api/automation/status", tags=["🚀 Pipeline"])
def get_automation_status(request: Request):
    logger.info("automation/status cookies: %s", request.cookies)
    outlook_conn = bool(od_get_token(request))
    gmail_conn = bool(google_get_creds(request))
    
    running = False
    active_provider = None
    pid = None
    started_at = None
    
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            pid = data.get("pid")
            if pid and is_pid_running(pid):
                running = True
                active_provider = data.get("provider")
                started_at = data.get("started_at")
        except Exception:
            pass
            
    return {
        "outlook_connected": outlook_conn,
        "gmail_connected": gmail_conn,
        "running": running,
        "active_provider": active_provider,
        "pid": pid,
        "started_at": started_at
    }

class AutomationStartRequest(BaseModel):
    provider: str

@app.post("/api/automation/start", tags=["🚀 Pipeline"])
def start_automation(body: AutomationStartRequest, request: Request):
    outlook_conn = bool(od_get_token(request))
    gmail_conn = bool(google_get_creds(request))
    
    if body.provider == "outlook" and not outlook_conn:
        raise HTTPException(status_code=400, detail="outlook_not_connected")
    elif body.provider == "gmail" and not gmail_conn:
        raise HTTPException(status_code=400, detail="gmail_not_connected")
        
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            old_pid = data.get("pid")
            if old_pid and is_pid_running(old_pid):
                stop_pid(old_pid)
        except Exception:
            pass
            
    workspace_dir = Path(__file__).resolve().parent.parent
    python_exe = sys.executable
    start_script = workspace_dir / "start_flow.py"
    
    if not start_script.exists():
        raise HTTPException(status_code=500, detail="Orchestration scripts missing in workspace.")
        
    try:
        proc = subprocess.Popen(
            [str(python_exe), str(start_script), "--provider", body.provider, "--interval", "60"],
            cwd=str(workspace_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 | 0x00000008
        )
        
        state = {
            "pid": proc.pid,
            "provider": body.provider,
            "started_at": time.time()
        }
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as fh:
            json.dump(state, fh)
            
        logger.info("Background automation started successfully for %s (PID=%d)", body.provider, proc.pid)
        return {"status": "running", "pid": proc.pid, "provider": body.provider}
    except Exception as exc:
        logger.error("Failed to spawn background automation: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Spawn failed: {str(exc)}")

@app.post("/api/automation/stop", tags=["🚀 Pipeline"])
def stop_automation():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as fh:
                data = json.load(fh)
            pid = data.get("pid")
            if pid and is_pid_running(pid):
                stop_pid(pid)
                logger.info("Background automation process PID=%d stopped.", pid)
        except Exception as e:
            logger.warning("Error reading state file to stop process: %s", e)
            
        try:
            STATE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
            
    return {"status": "stopped"}

@app.get("/api/automation/logs", tags=["🚀 Pipeline"])
def get_automation_logs(lines: int = 50):
    log_file = Path(__file__).resolve().parent.parent / "logs" / "document_organizer.log"
    if not log_file.exists():
        return {"logs": ["Log file does not exist yet."]}
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.readlines()
        last_lines = content[-lines:] if len(content) > lines else content
        return {"logs": [line.strip() for line in last_lines]}
    except Exception as e:
        return {"logs": [f"Error reading logs: {str(e)}"]}

# ══════════════════════════════════════════════════════════════════════════════
# Dev entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=[
            "logs/**/*",
            "output/**/*",
            "temp_uploads/**/*",
            ".sessions/**/*",
            "venv/**/*",
            "frontend/**/*",
            ".pytest_cache/**/*",
        ]
    )

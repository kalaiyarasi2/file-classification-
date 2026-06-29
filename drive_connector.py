"""
drive_connector.py - Connects Google Drive (locally mounted) to the File Classifier Pipeline.

This module bridges GoogleDriveAccess with the existing run_pipeline_full() function.
No API. No OAuth. Just the mounted drive path.

Usage (standalone):
    python drive_connector.py --input "G:\\My Drive\\uploads" --output "G:\\My Drive\\sorted"

Usage (from code):
    from drive_connector import DriveClassifierConnector

    conn = DriveClassifierConnector(
        drive_input_folder="G:\\My Drive\\uploads",
        drive_output_folder="G:\\My Drive\\sorted",
    )
    results = conn.run()
"""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
import time
import logging
from pathlib import Path
from typing import Optional

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
_log = logging.getLogger("drive_connector")


# ── Internal helpers ─────────────────────────────────────────────────────────

def _read_env_key(key: str, default: str = "") -> str:
    """Read a key from .env or os.environ without external libraries."""
    if key in os.environ:
        return os.environ[key]
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return default


# ── Main connector class ──────────────────────────────────────────────────────

class DriveClassifierConnector:
    """
    Connects a locally mounted Google Drive folder to the file classifier pipeline.

    Workflow:
        1. Read PDFs from drive_input_folder (mounted Google Drive path)
        2. Copy them to a local temp folder  (so OCR / pdfplumber work reliably)
        3. Run the existing run_pipeline_full() classifier on the temp folder
        4. Copy the classified output files back into drive_output_folder/<Category>/
        5. Return a summary of all results

    Args:
        drive_input_folder:  Path inside the mounted Drive to read PDFs from.
                             Defaults to GOOGLE_DRIVE_ROOT from .env.
        drive_output_folder: Path inside the mounted Drive to write sorted results.
                             Defaults to GOOGLE_DRIVE_OUTPUT from .env (or <input>/sorted).
        local_work_dir:      Local temp directory for processing.
                             Defaults to a system temp folder (auto-cleaned after run).
        copy_mode:           If True, keep originals in Drive. If False, move (delete from input).
        pdf_max_pages:       Pages to read per PDF for classification.
        min_score:           Minimum LLM score (0-10) to assign a category.
        llm_model:           OpenAI model to use.
        dry_run:             Classify without actually moving any files.
    """

    def __init__(
        self,
        drive_input_folder: "str | Path | None" = None,
        drive_output_folder: "str | Path | None" = None,
        local_work_dir: "str | Path | None" = None,
        copy_mode: bool = True,
        pdf_max_pages: int = 3,
        min_score: float = 7.0,
        llm_model: "str | None" = None,
        dry_run: bool = False,
    ):
        # ── Resolve drive input path ──────────────────────────────────────
        if drive_input_folder:
            self.drive_input = Path(drive_input_folder)
        else:
            env_root = _read_env_key("GOOGLE_DRIVE_ROOT")
            if not env_root:
                raise ValueError(
                    "drive_input_folder not provided and GOOGLE_DRIVE_ROOT is not set in .env"
                )
            self.drive_input = Path(env_root)

        # ── Resolve drive output path ─────────────────────────────────────
        if drive_output_folder:
            self.drive_output = Path(drive_output_folder)
        else:
            env_out = _read_env_key("GOOGLE_DRIVE_OUTPUT")
            self.drive_output = Path(env_out) if env_out else self.drive_input.parent / "sorted"

        # ── Other settings ────────────────────────────────────────────────
        self.local_work_dir = Path(local_work_dir) if local_work_dir else None
        self.copy_mode      = copy_mode
        self.pdf_max_pages  = pdf_max_pages
        self.min_score      = min_score
        self.dry_run        = dry_run

        # Sanitize llm_model: reject Swagger placeholder 'string' or blank
        _default_model = _read_env_key("LLM_MODEL", "gpt-4o")
        _requested_model = llm_model or _default_model
        if not _requested_model or _requested_model.strip().lower() in ("string", "", "null", "none"):
            _requested_model = _default_model
        self.llm_model = _requested_model

        _log.info("DriveClassifierConnector initialized")
        _log.info("  Input  : %s", self.drive_input)
        _log.info("  Output : %s", self.drive_output)
        _log.info("  Model  : %s | Score >= %.1f | Pages: %d | Copy: %s | DryRun: %s",
                  self.llm_model, self.min_score, self.pdf_max_pages,
                  self.copy_mode, self.dry_run)

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self) -> dict:
        """
        Check that the Drive input folder is reachable and contains PDFs.

        Returns:
            dict with keys: input_ok, output_ok, pdf_count, pdf_files
        """
        input_ok  = self.drive_input.exists() and self.drive_input.is_dir()
        pdf_files = []

        if input_ok:
            pdf_files = sorted(self.drive_input.glob("*.pdf"))
            _log.info("Drive input accessible: %s  (%d PDF(s) found)", self.drive_input, len(pdf_files))
        else:
            _log.error("Drive input NOT accessible: %s", self.drive_input)

        output_ok = True
        if not self.drive_output.exists():
            if not self.dry_run:
                try:
                    self.drive_output.mkdir(parents=True, exist_ok=True)
                    _log.info("Created Drive output folder: %s", self.drive_output)
                except Exception as exc:
                    _log.error("Cannot create Drive output: %s", exc)
                    output_ok = False
        else:
            _log.info("Drive output folder ready: %s", self.drive_output)

        return {
            "input_ok":  input_ok,
            "output_ok": output_ok,
            "pdf_count": len(pdf_files),
            "pdf_files": [str(f) for f in pdf_files],
        }

    # ── Core pipeline ─────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Execute the full classify-and-sort pipeline on the Drive input folder.

        Steps:
            1. Validate Drive paths.
            2. Copy PDFs from Drive -> local temp folder.
            3. Run the classifier pipeline on the local temp folder.
            4. Copy classified files from local output -> Drive output folder.
            5. Optionally delete originals from Drive input (if copy_mode=False).

        Returns:
            Result summary dict.
        """
        start = time.time()
        status = self.validate()

        if not status["input_ok"]:
            return {
                "success": False,
                "error": f"Drive input folder not accessible: {self.drive_input}",
                "results": [],
            }

        pdf_files = [Path(p) for p in status["pdf_files"]]
        if not pdf_files:
            return {
                "success": True,
                "message": "No PDF files found in Drive input folder.",
                "drive_input":  str(self.drive_input),
                "drive_output": str(self.drive_output),
                "results": [],
            }

        # ── Step 2: copy PDFs from Drive -> local temp ────────────────────
        use_temp = self.local_work_dir is None
        local_dir = Path(tempfile.mkdtemp(prefix="drive_classifier_")) if use_temp else self.local_work_dir
        local_input  = local_dir / "input"
        local_output = local_dir / "output"
        local_input.mkdir(parents=True, exist_ok=True)
        local_output.mkdir(parents=True, exist_ok=True)

        _log.info("Copying %d PDF(s) from Drive -> local temp: %s", len(pdf_files), local_input)
        for pdf in pdf_files:
            dest = local_input / pdf.name
            shutil.copy2(pdf, dest)
            _log.info("  Copied: %s", pdf.name)

        # ── Step 3: run classifier on local folder ────────────────────────
        try:
            from file_classifier import (
                run_pipeline_full,
                load_categories_from_env,
                get_env_setting,
            )

            # Load categories from .env (required positional arg to run_pipeline_full)
            categories = load_categories_from_env()
            if not categories:
                _log.warning("No categories loaded from .env — files will be classified as 'Others'.")

            _log.info("Running classifier pipeline on: %s  (model=%s, categories=%d)",
                      local_input, self.llm_model, len(categories))
            pipeline_results = run_pipeline_full(
                input_folder=local_input,
                output_folder=local_output,
                categories=categories,          # <-- required arg now passed correctly
                pdf_max_pages=self.pdf_max_pages,
                min_score=self.min_score,
                llm_model=self.llm_model,
                copy_mode=True,      # Always copy inside local; Drive sync handles the rest
                dry_run=self.dry_run,
            )
        except Exception as exc:
            _log.error("Pipeline failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc), "results": []}

        # ── Step 4: copy classified output -> Drive output ────────────────
        copied_to_drive = []
        if not self.dry_run:
            _log.info("Syncing classified output -> Drive: %s", self.drive_output)
            for category_folder in local_output.iterdir():
                if not category_folder.is_dir():
                    continue
                drive_cat_folder = self.drive_output / category_folder.name
                drive_cat_folder.mkdir(parents=True, exist_ok=True)

                for pdf in sorted(category_folder.glob("*.pdf")):
                    drive_dest = drive_cat_folder / pdf.name
                    shutil.copy2(pdf, drive_dest)
                    _log.info("  -> Drive: %s/%s", category_folder.name, pdf.name)
                    copied_to_drive.append(str(drive_dest))

        # ── Step 5: delete originals from Drive input if move mode ────────
        if not self.copy_mode and not self.dry_run:
            for pdf in pdf_files:
                try:
                    pdf.unlink()
                    _log.info("  Removed original from Drive input: %s", pdf.name)
                except Exception as exc:
                    _log.warning("  Could not remove %s: %s", pdf.name, exc)

        # ── Cleanup local temp ────────────────────────────────────────────
        if use_temp:
            try:
                shutil.rmtree(local_dir)
                _log.debug("Cleaned up temp folder: %s", local_dir)
            except Exception:
                pass

        elapsed = round(time.time() - start, 2)
        _log.info("Done in %.2fs. Files written to Drive: %d", elapsed, len(copied_to_drive))

        return {
            "success":          True,
            "drive_input":      str(self.drive_input),
            "drive_output":     str(self.drive_output),
            "pdfs_processed":   len(pdf_files),
            "files_on_drive":   copied_to_drive,
            "dry_run":          self.dry_run,
            "total_time_sec":   elapsed,
            "pipeline_results": pipeline_results if isinstance(pipeline_results, list) else [],
        }


# ── Convenience function ─────────────────────────────────────────────────────

def classify_drive_folder(
    drive_input_folder: "str | None" = None,
    drive_output_folder: "str | None" = None,
    copy_mode: bool = True,
    dry_run: bool = False,
    **kwargs,
) -> dict:
    """
    One-liner helper: classify all PDFs in a Drive folder and write results back.

    Args:
        drive_input_folder:  Source Drive folder (or GOOGLE_DRIVE_ROOT from .env).
        drive_output_folder: Output Drive folder (or GOOGLE_DRIVE_OUTPUT from .env).
        copy_mode:           Keep originals in Drive when True.
        dry_run:             Simulate without writing files.
        **kwargs:            Forwarded to DriveClassifierConnector (pdf_max_pages, min_score, etc.)

    Returns:
        Result summary dict from DriveClassifierConnector.run().

    Example:
        from drive_connector import classify_drive_folder

        result = classify_drive_folder(
            drive_input_folder="G:\\My Drive\\uploads",
            drive_output_folder="G:\\My Drive\\sorted",
        )
        print(result)
    """
    connector = DriveClassifierConnector(
        drive_input_folder=drive_input_folder,
        drive_output_folder=drive_output_folder,
        copy_mode=copy_mode,
        dry_run=dry_run,
        **kwargs,
    )
    return connector.run()


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json

    parser = argparse.ArgumentParser(
        description="Classify PDFs from a Google Drive folder and write results back to Drive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use paths from .env (GOOGLE_DRIVE_ROOT / GOOGLE_DRIVE_OUTPUT)
  python drive_connector.py

  # Specify folders explicitly
  python drive_connector.py --input "G:\\My Drive\\uploads" --output "G:\\My Drive\\sorted"

  # Dry-run (classify only, don't move files)
  python drive_connector.py --dry-run

  # Move files (delete originals from input after sorting)
  python drive_connector.py --move
""",
    )
    parser.add_argument("--input",  default=None, metavar="DRIVE_PATH",
                        help="Drive input folder. Defaults to GOOGLE_DRIVE_ROOT in .env.")
    parser.add_argument("--output", default=None, metavar="DRIVE_PATH",
                        help="Drive output folder. Defaults to GOOGLE_DRIVE_OUTPUT in .env.")
    parser.add_argument("--move",   action="store_true",
                        help="Move files (delete originals). Default is copy.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify without touching any files.")
    parser.add_argument("--pages",  type=int, default=3, metavar="N",
                        help="Pages to read per PDF (default: 3).")
    parser.add_argument("--score",  type=float, default=7.0, metavar="SCORE",
                        help="Minimum LLM score to accept a category (default: 7.0).")
    parser.add_argument("--model",  default=None, metavar="MODEL",
                        help="OpenAI model (default: from .env or gpt-4o).")
    parser.add_argument("--validate-only", action="store_true",
                        help="Check Drive access and PDF count, then exit.")
    args = parser.parse_args()

    connector = DriveClassifierConnector(
        drive_input_folder=args.input,
        drive_output_folder=args.output,
        copy_mode=not args.move,
        dry_run=args.dry_run,
        pdf_max_pages=args.pages,
        min_score=args.score,
        llm_model=args.model,
    )

    if args.validate_only:
        result = connector.validate()
        print("\n" + "=" * 55)
        print("  Drive Validation Result")
        print("=" * 55)
        print(f"  Input  accessible : {result['input_ok']}")
        print(f"  Output accessible : {result['output_ok']}")
        print(f"  PDFs found        : {result['pdf_count']}")
        if result["pdf_files"]:
            print("\n  Files:")
            for f in result["pdf_files"]:
                print(f"    {Path(f).name}")
        print("=" * 55)
    else:
        result = connector.run()
        print("\n" + "=" * 55)
        print("  Drive Classifier - Run Complete")
        print("=" * 55)
        print(json.dumps(result, indent=2, default=str))
        print("=" * 55)

"""
connected.py - Connector/Bridge module between Outlook Email Agent and File Classifier.

This script runs the Outlook Email Agent to fetch unread email attachments,
saves them to the designated input directory (local, Google Drive, or OneDrive),
and then executes the respective file classification pipeline.

Usage:
    # 1. Local Folder Input Pipeline
    python connected.py --mode folder --input ./inbox --output ./sorted

    # 2. Google Drive (mounted) Pipeline
    python connected.py --mode drive --input "G:\\My Drive\\uploads" --output "G:\\My Drive\\sorted"

    # 3. OneDrive (mounted) Pipeline
    python connected.py --mode onedrive --input "C:\\Users\\Intern\\OneDrive\\uploads" --output "C:\\Users\\Intern\\OneDrive\\sorted"
"""

from __future__ import annotations

import os
import sys
import base64
import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List, Set

# Setup path imports for Outlook_Agent
CURRENT_DIR = Path(__file__).parent.resolve()
OUTLOOK_AGENT_DIR = CURRENT_DIR.parent / "Outlook_Agent"
if str(OUTLOOK_AGENT_DIR) not in sys.path:
    sys.path.append(str(OUTLOOK_AGENT_DIR))

# Import classification modules from file-classification
from file_classifier import run_pipeline_full, load_categories_from_env, get_env_setting
from drive_connector import DriveClassifierConnector
from onedrive_connector import OneDriveClassifierConnector

try:
    from outlook_agent_module import OutlookAgentModule, _mark_read, _load_processed_ids, _save_processed_ids
except ImportError:
    # Fallback to manual functions if internal functions cannot be directly imported
    OutlookAgentModule = None
    _mark_read = None
    _load_processed_ids = None
    _save_processed_ids = None

# Configure logging
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("connected_connector")


def get_outlook_agent() -> OutlookAgentModule | None:
    """Instantiate and return the OutlookAgentModule."""
    if OutlookAgentModule is None:
        logger.error("OutlookAgentModule class could not be found or imported.")
        return None
    try:
        return OutlookAgentModule()
    except Exception as exc:
        logger.error("Failed to initialize OutlookAgentModule: %s", exc)
        return None


def sync_outlook_attachments(dest_folder: Path, email_filter: str = "all") -> int:
    """
    Fetches unread emails from Outlook, downloads PDF attachments,
    and writes them to the target destination folder.
    """
    logger.info("Checking Outlook unread emails for PDF attachments...")
    agent = get_outlook_agent()
    if not agent:
        logger.error("Outlook agent not available. Skipping attachment sync.")
        return 0

    dest_folder.mkdir(parents=True, exist_ok=True)
    downloaded_count = 0

    try:
        # Obtain MS Graph token
        token = agent.get_access_token()
        
        # Load previously processed email IDs to avoid double-processing
        processed_ids_file = agent.processed_ids_file
        if _load_processed_ids:
            processed_ids = _load_processed_ids(processed_ids_file)
        else:
            processed_ids = set()

        # Fetch emails
        emails = agent.fetch_unread_emails()
        if not emails:
            logger.info("No new unread emails with attachments found.")
            return 0

        for email in emails:
            email_id = email["id"]
            attachments = email.get("attachments", [])
            
            if not attachments:
                if _mark_read:
                    _mark_read(token, email_id)
                processed_ids.add(email_id)
                continue

            email_has_pdfs = False
            for att in attachments:
                filename = att["filename"]
                if not filename.lower().endswith(".pdf"):
                    continue

                content_bytes = base64.b64decode(att["content_bytes"])
                dest_file = dest_folder / filename
                
                # Save PDF locally (works for local directory, Google Drive mount, and OneDrive mount)
                dest_file.write_bytes(content_bytes)
                logger.info("Saved Outlook PDF attachment: %s", dest_file.name)
                downloaded_count += 1
                email_has_pdfs = True

            # Mark email as read and add to processed list
            if _mark_read:
                _mark_read(token, email_id)
            processed_ids.add(email_id)

        # Save processed IDs
        if _save_processed_ids:
            _save_processed_ids(processed_ids, processed_ids_file)

        logger.info("Outlook attachment sync completed. Synced %d PDF(s).", downloaded_count)
        return downloaded_count

    except Exception as exc:
        logger.error("Error during Outlook attachment sync: %s", exc, exc_info=True)
        return 0


def run_pipeline(
    mode: str,
    input_folder: str | Path,
    output_folder: str | Path,
    email_filter: str = "all",
    pdf_max_pages: int = 3,
    min_score: float = 7.0,
    llm_model: str = "gpt-4o",
    copy_mode: bool = True,
) -> Dict[str, Any]:
    """
    Runs the Outlook Agent sync followed by the requested classification pipeline.
    """
    input_path = Path(input_folder)
    output_path = Path(output_folder)

    # 1. Sync Outlook attachments first
    logger.info("=" * 60)
    logger.info("STAGE 1: Syncing attachments from Outlook to input folder")
    logger.info("=" * 60)
    sync_outlook_attachments(input_path, email_filter)

    # 2. Run the specified classification pipeline
    logger.info("=" * 60)
    logger.info("STAGE 2: Starting classification pipeline [%s]", mode.upper())
    logger.info("=" * 60)

    if mode == "folder":
        # Local Folder classification
        categories = load_categories_from_env()
        poppler = get_env_setting("POPPLER_PATH") or None
        results = run_pipeline_full(
            input_folder=input_path,
            output_folder=output_path,
            categories=categories,
            pdf_max_pages=pdf_max_pages,
            min_score=min_score,
            llm_model=llm_model,
            copy_mode=copy_mode,
            dry_run=False,
            poppler_path=poppler,
        )
        return {"mode": mode, "processed_count": len(results), "results": results}

    elif mode == "drive":
        # Google Drive (mounted) classification
        connector = DriveClassifierConnector(
            drive_input_folder=input_path,
            drive_output_folder=output_path,
            copy_mode=copy_mode,
            pdf_max_pages=pdf_max_pages,
            min_score=min_score,
            llm_model=llm_model,
            dry_run=False,
        )
        results = connector.run()
        return {"mode": mode, "results": results}

    elif mode == "onedrive":
        # OneDrive (mounted) classification
        connector = OneDriveClassifierConnector(
            drive_input_folder=input_path,
            drive_output_folder=output_path,
            copy_mode=copy_mode,
            pdf_max_pages=pdf_max_pages,
            min_score=min_score,
            llm_model=llm_model,
            dry_run=False,
        )
        results = connector.run()
        return {"mode": mode, "results": results}

    else:
        raise ValueError(f"Invalid mode specified: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Connected Pipeline: Outlook Sync + PDF Classifier & Organiser"
    )
    parser.add_argument(
        "--mode",
        choices=["folder", "drive", "onedrive"],
        required=True,
        help="Classification mode: folder (local), drive (Google Drive mount), onedrive (OneDrive sync folder)",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the input directory (where new PDFs are placed/synced)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the sorted output root folder",
    )
    parser.add_argument(
        "--filter",
        default="all",
        help="Email subject/body filter for the Outlook Agent",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=3,
        help="Number of pages to read from each PDF for classification",
    )
    parser.add_argument(
        "--score",
        type=float,
        default=7.0,
        help="Minimum threshold score (0-10) to accept category match",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="LLM model to use for classification",
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move original files instead of copying them (default: copy)",
    )

    args = parser.parse_args()

    try:
        summary = run_pipeline(
            mode=args.mode,
            input_folder=args.input,
            output_folder=args.output,
            email_filter=args.filter,
            pdf_max_pages=args.pages,
            min_score=args.score,
            llm_model=args.model,
            copy_mode=not args.move,
        )
        logger.info("Connected pipeline run completed successfully.")
        return 0
    except Exception as exc:
        logger.error("Connected pipeline run failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

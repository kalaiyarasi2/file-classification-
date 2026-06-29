"""
report.py - Generates a CSV report summarising the classification run.

The report is written to ``<output_folder>/classification_report.csv`` and
contains one row per processed document.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_FIELDS = [
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

    # ── Public API ─────────────────────────────────────────────────────────

    def save(self, results: list[dict]) -> Path:
        """
        Write *results* to a CSV file.

        Args:
            results: List of per-document result dictionaries produced by
                     ``main.py``.  Each dict should have the keys defined
                     in ``_FIELDS``; missing keys are written as empty strings.

        Returns:
            Path to the written report file.
        """
        self.output_folder.mkdir(parents=True, exist_ok=True)
        report_path = self.output_folder / self.REPORT_FILENAME

        file_exists = report_path.exists()

        with report_path.open("a" if file_exists else "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_FIELDS, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            for row in results:
                # Ensure every field is present, fall back to empty string
                safe_row = {field: row.get(field, "") for field in _FIELDS}
                # Fallback mapping from llm_score if confidence is missing or blank
                if not safe_row["confidence"] and "llm_score" in row:
                    try:
                        safe_row["confidence"] = f"{float(row['llm_score']) / 10.0:.4f}"
                    except Exception:
                        pass
                writer.writerow(safe_row)

        # ── Summary stats ──────────────────────────────────────────────────
        total = len(results)
        errors = sum(1 for r in results if r.get("error"))
        categories: dict[str, int] = {}
        for r in results:
            cat = r.get("category", "Others")
            categories[cat] = categories.get(cat, 0) + 1

        logger.info("Report written: %s", report_path)
        logger.info("Summary - total=%d, errors=%d, categories=%s", total, errors, categories)

        return report_path

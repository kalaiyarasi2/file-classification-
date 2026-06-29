"""
config.py - Application configuration loader.

Reads settings from a JSON file and provides typed access throughout the app.
All keys are optional; sensible defaults are applied for everything except
input/output folders (which can also be provided via CLI flags).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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

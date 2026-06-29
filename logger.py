"""
logger.py - Centralised logging configuration.

Sets up a root logger that writes to both the console (INFO+) and a
rotating file handler (DEBUG+). Call ``get_logger(__name__)`` in every
module instead of ``logging.getLogger(__name__)`` to ensure the handlers
are always configured before first use.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_CONFIGURED = False
_LOG_DIR = Path("logs")


def _configure_root_logger(log_level: str = "INFO") -> None:
    """
    Attach console and rotating-file handlers to the root logger.

    Safe to call multiple times (idempotent).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

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



# Configure on import so any module calling get_logger() is ready.
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

"""
utils.py - Shared utility functions used across the document organizer.

Keeping these here (rather than inlining in individual modules) makes it
easy to unit-test common operations in isolation and avoids duplication.
"""

from __future__ import annotations

import hashlib
import re
import string
from pathlib import Path


# ── Text helpers ───────────────────────────────────────────────────────────

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


# ── File helpers ───────────────────────────────────────────────────────────

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
    # Replace filesystem-unsafe characters with underscores
    sanitised = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Collapse multiple underscores
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

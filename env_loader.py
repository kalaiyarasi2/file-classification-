"""
env_loader.py - Load category keywords and settings from the .env file.

Reads all CATEGORY_<Name>=kw1,kw2,... lines from the .env file and
converts them into the categories dict used by DocumentClassifier.

Also exposes helper functions to read scalar settings (API key,
model name, thresholds, etc.) without requiring python-dotenv.

Usage
-----
    from env_loader import load_categories_from_env, get_env_setting

    categories = load_categories_from_env()
    # {"Invoice": ["invoice number", "bill to", ...], ...}

    api_key   = get_env_setting("ANTHROPIC_API_KEY")
    threshold = get_env_setting("MIN_SCORE_THRESHOLD", default="3")
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

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
        logger.warning(".env file not found at: %s", env_path)
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
        # Only strip if the '#' is preceded by whitespace
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
        # Default: look for .env next to this file
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
            logger.warning("Category '%s' has no keywords - skipping.", category_name)
            continue

        categories[category_name] = keywords
        logger.debug(
            "Loaded category '%s' with %d keyword(s): %s",
            category_name,
            len(keywords),
            keywords,
        )

    if not categories:
        logger.warning(
            "No CATEGORY_* entries found in .env. "
            "All documents will be classified as 'Others'."
        )

    logger.info("Loaded %d category/categories from .env.", len(categories))
    return categories


def get_env_setting(key: str, default: str = "") -> str:
    """
    Return a scalar setting value from the environment or .env file.

    Checks ``os.environ`` first (so real env vars always win), then
    falls back to the .env file, then to *default*.

    Args:
        key: Environment variable name (e.g. ``"ANTHROPIC_API_KEY"``).
        default: Value to return when the key is not found anywhere.

    Returns:
        The setting value as a string.
    """
    # Real environment variable wins
    if key in os.environ:
        return os.environ[key]

    # Fall back to .env file
    env_path = Path(__file__).parent / ".env"
    raw = _load_dotenv(env_path)
    return raw.get(key, default)

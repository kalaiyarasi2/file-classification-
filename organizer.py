"""
organizer.py - Moves or copies classified files into categorised output folders.

Responsibilities
----------------
* Create the output folder tree on demand (including "Others/").
* Move or copy each file into the appropriate sub-folder.
* Handle filename collisions by appending a numeric suffix.
* Support a *dry-run* mode that logs actions without touching the filesystem.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


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

    # ── Public API ─────────────────────────────────────────────────────────

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
        dest_dir = self.output_folder / category
        dest_path = self._resolve_destination(dest_dir, source.name)

        action = "copy" if self.copy_mode else "move"
        logger.debug("[%s] %s -> %s", action.upper(), source, dest_path)

        if self.dry_run:
            logger.info("[DRY-RUN] Would %s %s -> %s", action, source, dest_path)
            return dest_path

        dest_dir.mkdir(parents=True, exist_ok=True)

        if self.copy_mode:
            shutil.copy2(str(source), str(dest_path))
        else:
            shutil.move(str(source), str(dest_path))

        return dest_path

    # ── Helpers ────────────────────────────────────────────────────────────

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

"""
google_drive_access.py - Google Drive Folder Access Module (No API Required)

This module accesses your Google Drive folders using the locally mounted path
provided by 'Google Drive for Desktop' (formerly Backup and Sync).

How it works:
    Google Drive for Desktop mounts your entire Drive as a local drive letter
    on Windows (e.g. G:\\My Drive\\ or D:\\Google Drive\\). This module simply
    treats that path as a normal local directory — NO API keys, NO OAuth needed.

Pre-requisites:
    1. Install 'Google Drive for Desktop' from:
       https://www.google.com/drive/download/
    2. Sign in and let it sync/mount your Drive.
    3. Note the drive letter it uses (commonly G: or a drive of your choice).
    4. Set GOOGLE_DRIVE_ROOT in your .env or pass it directly.

Usage:
    # Auto-detect the drive root from .env
    gd = GoogleDriveAccess()

    # Or pass the path explicitly
    gd = GoogleDriveAccess(drive_root="G:\\\\My Drive")

    # List top-level folders
    gd.list_folders()

    # Browse a specific sub-folder
    gd.list_files("G:\\\\My Drive\\\\Documents\\\\Invoices")

    # Copy files from Drive -> local (or local -> Drive)
    gd.copy_files_from_drive("G:\\\\My Drive\\\\Inbox", "./local_inbox")
    gd.copy_files_to_drive("./sorted", "G:\\\\My Drive\\\\Sorted")
"""

from __future__ import annotations

import os
import shutil
import string
import logging
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
_log = logging.getLogger("google_drive_access")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_env_key(key: str, default: str = "") -> str:
    """Read a single key from .env or os.environ (no external lib required)."""
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


def _detect_google_drive_root() -> Optional[Path]:
    """
    Scan all Windows drive letters for a mounted Google Drive folder.

    Google Drive for Desktop typically creates:
        G:\\My Drive
        or another letter chosen by the user.

    Returns the first match, or None if not found.
    """
    candidate_subfolders = ["My Drive", "Google Drive", "GoogleDrive"]

    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        for sub in candidate_subfolders:
            candidate = drive / sub
            if candidate.exists() and candidate.is_dir():
                _log.info("Auto-detected Google Drive root: %s", candidate)
                return candidate

    return None


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class GoogleDriveAccess:
    """
    Filesystem-based access to a mounted Google Drive folder.

    No API, no credentials — just a path on disk provided by
    'Google Drive for Desktop'.

    Args:
        drive_root: Full path to the mounted Google Drive root directory.
                    If None, reads ``GOOGLE_DRIVE_ROOT`` from .env, then
                    tries to auto-detect the mount point.
    """

    def __init__(self, drive_root: "str | Path | None" = None):
        # Priority: explicit arg > .env > auto-detect
        if drive_root:
            self.root = Path(drive_root)
        else:
            env_val = _read_env_key("GOOGLE_DRIVE_ROOT")
            if env_val:
                self.root = Path(env_val)
            else:
                detected = _detect_google_drive_root()
                self.root = detected  # type: ignore[assignment]

        if self.root:
            self._validate()
        else:
            _log.warning(
                "Google Drive root not found. Install 'Google Drive for Desktop' "
                "and sign in, OR set GOOGLE_DRIVE_ROOT=G:\\My Drive in your .env file."
            )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> bool:
        """Confirm the configured root path exists and is a directory."""
        if not self.root.exists():
            _log.error(
                "Path does not exist: %s  "
                "(Is 'Google Drive for Desktop' running and synced?)",
                self.root,
            )
            return False
        if not self.root.is_dir():
            _log.error("Path is not a directory: %s", self.root)
            return False
        _log.info("Google Drive root is accessible: %s", self.root)
        return True

    @property
    def is_available(self) -> bool:
        """True when the Drive mount point exists and is a readable directory."""
        return self.root is not None and self.root.exists() and self.root.is_dir()

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def get_folder(self, *parts: str) -> Path:
        """
        Build and return a path relative to the Drive root.

        Example:
            gd.get_folder("Documents", "Invoices")
            # -> G:\\My Drive\\Documents\\Invoices
        """
        if not self.root:
            raise RuntimeError("Google Drive root is not set or not available.")
        return self.root.joinpath(*parts)

    def list_folders(
        self,
        path: "str | Path | None" = None,
        depth: int = 1,
    ) -> list:
        """
        List sub-folders inside *path* (defaults to the drive root).

        Args:
            path:  Directory to inspect. Defaults to ``self.root``.
            depth: Recursion depth (1 = immediate children only).

        Returns:
            Sorted list of :class:`pathlib.Path` objects.
        """
        base = Path(path) if path else self.root
        if not base or not base.exists():
            _log.error("Folder not found: %s", base)
            return []

        folders: list = []
        self._collect_folders(base, folders, current_depth=1, max_depth=depth)

        _log.info("Found %d folder(s) under: %s", len(folders), base)
        for f in folders:
            _log.info("  [DIR]  %s", f)
        return folders

    def _collect_folders(
        self,
        base: Path,
        acc: list,
        current_depth: int,
        max_depth: int,
    ) -> None:
        try:
            for item in sorted(base.iterdir()):
                if item.is_dir():
                    acc.append(item)
                    if current_depth < max_depth:
                        self._collect_folders(item, acc, current_depth + 1, max_depth)
        except PermissionError as exc:
            _log.warning("Permission denied reading folder %s: %s", base, exc)

    def list_files(
        self,
        path: "str | Path | None" = None,
        extensions: "list[str] | None" = None,
        recursive: bool = False,
    ) -> list:
        """
        List files inside *path*.

        Args:
            path:       Directory to scan. Defaults to ``self.root``.
            extensions: Optional filter, e.g. ``['.pdf', '.docx']``.
            recursive:  Recurse into sub-folders when True.

        Returns:
            Sorted list of matching :class:`pathlib.Path` objects.
        """
        base = Path(path) if path else self.root
        if not base or not base.exists():
            _log.error("Folder not found: %s", base)
            return []

        # Normalise extensions to lowercase with leading dot
        exts = set()
        for e in (extensions or []):
            exts.add(e.lower() if e.startswith(".") else f".{e.lower()}")

        pattern = "**/*" if recursive else "*"
        files = [
            f for f in sorted(base.glob(pattern))
            if f.is_file() and (not exts or f.suffix.lower() in exts)
        ]

        _log.info("Found %d file(s) in: %s", len(files), base)
        for f in files:
            size_kb = f.stat().st_size / 1024
            _log.info("  [FILE] %-50s  %.1f KB", f.name, size_kb)
        return files

    # ------------------------------------------------------------------
    # Copy utilities (Drive <-> local)
    # ------------------------------------------------------------------

    def copy_files_from_drive(
        self,
        source_drive_folder: "str | Path",
        local_dest: "str | Path",
        extensions: "list[str] | None" = None,
        overwrite: bool = False,
    ) -> list:
        """
        Copy files from a Drive folder into a local directory.

        Args:
            source_drive_folder: Path inside the mounted Drive.
            local_dest:          Local destination directory.
            extensions:          File-type filter (e.g. ``['.pdf']``).
            overwrite:           Replace existing local files when True.

        Returns:
            List of copied local :class:`pathlib.Path` objects.
        """
        src = Path(source_drive_folder)
        dst = Path(local_dest)
        dst.mkdir(parents=True, exist_ok=True)

        files = self.list_files(src, extensions=extensions)
        copied = []

        for f in files:
            target = dst / f.name
            if target.exists() and not overwrite:
                _log.debug("Skipped (already exists locally): %s", target.name)
                continue
            shutil.copy2(f, target)
            _log.info("Downloaded  Drive -> Local : %s", f.name)
            copied.append(target)

        _log.info("Copied %d file(s) from Drive to local: %s", len(copied), dst)
        return copied

    def copy_files_to_drive(
        self,
        local_source: "str | Path",
        dest_drive_folder: "str | Path",
        extensions: "list[str] | None" = None,
        overwrite: bool = False,
    ) -> list:
        """
        Copy files from a local directory into a Drive folder.

        Args:
            local_source:      Local directory to read from.
            dest_drive_folder: Destination inside the mounted Drive.
            extensions:        File-type filter (e.g. ``['.pdf']``).
            overwrite:         Replace existing Drive files when True.

        Returns:
            List of :class:`pathlib.Path` objects written to Drive.
        """
        src = Path(local_source)
        dst = Path(dest_drive_folder)

        if not dst.exists():
            dst.mkdir(parents=True, exist_ok=True)
            _log.info("Created Drive folder: %s", dst)

        exts = set()
        for e in (extensions or []):
            exts.add(e.lower() if e.startswith(".") else f".{e.lower()}")

        files = [
            f for f in sorted(src.iterdir())
            if f.is_file() and (not exts or f.suffix.lower() in exts)
        ]
        copied = []

        for f in files:
            target = dst / f.name
            if target.exists() and not overwrite:
                _log.debug("Skipped (already exists in Drive): %s", target.name)
                continue
            shutil.copy2(f, target)
            _log.info("Uploaded  Local -> Drive : %s", f.name)
            copied.append(target)

        _log.info("Copied %d file(s) to Drive folder: %s", len(copied), dst)
        return copied

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def info(self) -> dict:
        """
        Return a summary of the Drive mount.

        Returns:
            Dict with keys: root, available, total_files, total_folders.
        """
        if not self.is_available:
            return {"root": str(self.root), "available": False}

        all_items = list(self.root.rglob("*"))
        total_files   = sum(1 for i in all_items if i.is_file())
        total_folders = sum(1 for i in all_items if i.is_dir())

        result = {
            "root":          str(self.root),
            "available":     True,
            "total_files":   total_files,
            "total_folders": total_folders,
        }
        _log.info("Drive info: %s", result)
        return result

    def __repr__(self) -> str:
        status = "available" if self.is_available else "not available"
        return f"<GoogleDriveAccess root={self.root!r} [{status}]>"


# ---------------------------------------------------------------------------
# CLI entry-point  (python google_drive_access.py --help)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Browse a mounted Google Drive folder (no API required).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect drive root (reads from .env or scans drive letters)
  python google_drive_access.py

  # Specify the drive root explicitly
  python google_drive_access.py --root "G:\\My Drive"

  # Browse a sub-folder and filter to PDFs only
  python google_drive_access.py --root "G:\\My Drive" --folder "Invoices" --ext pdf

  # List 2 levels deep of folders
  python google_drive_access.py --depth 2
""",
    )
    parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help='Mounted Drive path, e.g. "G:\\\\My Drive". '
             "Defaults to GOOGLE_DRIVE_ROOT in .env or auto-detect.",
    )
    parser.add_argument(
        "--folder",
        default=None,
        metavar="SUBFOLDER",
        help="Sub-folder inside the Drive root to inspect.",
    )
    parser.add_argument(
        "--ext",
        nargs="*",
        default=None,
        metavar="EXT",
        help="Filter files by extension, e.g. --ext pdf docx",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        metavar="N",
        help="Folder listing depth (default: 1).",
    )
    args = parser.parse_args()

    gd = GoogleDriveAccess(drive_root=args.root)

    sep = "=" * 60
    print(f"\n{sep}")
    print("  Google Drive Access Module")
    print(sep)
    print(f"  Status : {'Connected' if gd.is_available else 'NOT available'}")
    print(f"  Root   : {gd.root}")
    print(f"{sep}\n")

    if not gd.is_available:
        print("SETUP STEPS:")
        print("  1. Download 'Google Drive for Desktop':")
        print("     https://www.google.com/drive/download/")
        print("  2. Sign in and let it finish syncing.")
        print("  3. Note the drive letter it assigned (e.g. G:).")
        print("  4. Add this line to your .env file:")
        print("     GOOGLE_DRIVE_ROOT=G:\\My Drive")
        print()
        raise SystemExit(1)

    target = gd.get_folder(args.folder) if args.folder else gd.root

    print(f"Folders under: {target}")
    print("-" * 50)
    folders = gd.list_folders(target, depth=args.depth)
    if not folders:
        print("  (no sub-folders found)")

    print(f"\nFiles under: {target}")
    print("-" * 50)
    files = gd.list_files(target, extensions=args.ext)
    if not files:
        print("  (no files found)")

    print(f"\nDone. Found {len(folders)} folder(s) and {len(files)} file(s).\n")

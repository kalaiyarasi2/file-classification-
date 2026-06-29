"""
onedrive_access.py - OneDrive Folder Access Module

This module accesses your OneDrive folders using:
1. Filesystem-based Access (Primary): Interacts with the local OneDrive folder 
   synchronized by the OneDrive Desktop app (typical Windows paths, e.g., C:\\Users\\<user>\\OneDrive).
   NO API keys, NO registration required.
2. Microsoft Graph API-based Access (Secondary/API): Interacts with Microsoft Graph API 
   using OAuth and MSAL (Microsoft Authentication Library). Requires client registration.

Usage:
    # Filesystem-based access (automatically detects OneDrive path)
    od = OneDriveAccess()
    
    # Check if available
    if od.is_available:
        # List folders
        od.list_folders()
        
        # List PDF files
        od.list_files(extensions=['.pdf'])
"""

from __future__ import annotations

import os
import shutil
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------------------------
# Logger Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
_log = logging.getLogger("onedrive_access")

# ---------------------------------------------------------------------------
# MS Graph API Dependencies (Conditional)
# ---------------------------------------------------------------------------
try:
    import msal
    import requests
    HAS_API_DEPS = True
except ImportError:
    HAS_API_DEPS = False


# ---------------------------------------------------------------------------
# Internal Helpers
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


def _detect_onedrive_root() -> Optional[Path]:
    """
    Scan environment variables and common Windows paths for OneDrive.
    Prioritizes commercial/organization folders (usually named "OneDrive - <Org>")
    over default personal ones.
    """
    # 1. Scan default paths under USERPROFILE first to detect and prioritize
    #    commercial or active OneDrive folders.
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        up_path = Path(user_profile)
        try:
            candidates = []
            for item in up_path.iterdir():
                if item.is_dir() and item.name.lower().startswith("onedrive"):
                    candidates.append(item)
            
            if candidates:
                # Sort: items with " - " in their name come first (commercial/business),
                # followed by longer folder names.
                candidates.sort(key=lambda p: (" - " in p.name, len(p.name)), reverse=True)
                selected = candidates[0]
                _log.info("Auto-detected OneDrive root via USERPROFILE scan: %s", selected)
                return selected
        except Exception as exc:
            _log.debug("Failed to list user profile folder: %s", exc)

    # 2. Try environment variables as a fallback
    for env_key in ["ONEDRIVECOMMERCIAL", "ONEDRIVECONSUMER", "ONEDRIVE"]:
        env_val = os.environ.get(env_key)
        if env_val:
            candidate = Path(env_val)
            if candidate.exists() and candidate.is_dir():
                _log.info("Auto-detected OneDrive root via env var %s: %s", env_key, candidate)
                return candidate

    return None


# ===========================================================================
# 1. Filesystem-based Access (Drop-in parallel to GoogleDriveAccess)
# ===========================================================================
class OneDriveAccess:
    """
    Filesystem-based access to the locally mounted/synchronized OneDrive folder.
    
    No API, no credentials — just a path on disk provided by the
    OneDrive Desktop client.
    
    Args:
        drive_root: Full path to the local OneDrive root directory.
                    If None, reads ``ONEDRIVE_ROOT`` from .env, then
                    tries to auto-detect the OneDrive sync path.
    """

    def __init__(self, drive_root: str | Path | None = None):
        # Priority: explicit arg > .env > auto-detect
        if drive_root:
            self.root = Path(drive_root)
        else:
            env_val = _read_env_key("ONEDRIVE_ROOT")
            if env_val:
                self.root = Path(env_val)
            else:
                detected = _detect_onedrive_root()
                self.root = detected  # type: ignore[assignment]

        if self.root:
            self._validate()
        else:
            _log.warning(
                "OneDrive root not found. Ensure the OneDrive client is running, "
                "or set ONEDRIVE_ROOT=C:\\Users\\<user>\\OneDrive in your .env file."
            )

    def _validate(self) -> bool:
        """Confirm the configured root path exists and is a directory."""
        if not self.root.exists():
            _log.error(
                "Path does not exist: %s  "
                "(Is OneDrive running and synchronised?)",
                self.root,
            )
            return False
        if not self.root.is_dir():
            _log.error("Path is not a directory: %s", self.root)
            return False
        _log.info("OneDrive root is accessible: %s", self.root)
        return True

    @property
    def is_available(self) -> bool:
        """True when the OneDrive folder path exists and is a readable directory."""
        return self.root is not None and self.root.exists() and self.root.is_dir()

    def get_folder(self, *parts: str) -> Path:
        """
        Build and return a path relative to the OneDrive root.

        Example:
            od.get_folder("Documents", "Invoices")
            # -> C:\\Users\\<user>\\OneDrive\\Documents\\Invoices
        """
        if not self.root:
            raise RuntimeError("OneDrive root is not set or not available.")
        return self.root.joinpath(*parts)

    def list_folders(
        self,
        path: str | Path | None = None,
        depth: int = 1,
    ) -> list[Path]:
        """
        List sub-folders inside *path* (defaults to the OneDrive root).

        Args:
            path:  Directory to inspect. Defaults to ``self.root``.
            depth: Recursion depth (1 = immediate children only).

        Returns:
            Sorted list of pathlib.Path objects.
        """
        base = Path(path) if path else self.root
        if not base or not base.exists():
            _log.error("Folder not found: %s", base)
            return []

        folders: list[Path] = []
        self._collect_folders(base, folders, current_depth=1, max_depth=depth)

        _log.info("Found %d folder(s) under: %s", len(folders), base)
        for f in folders:
            _log.info("  [DIR]  %s", f)
        return folders

    def _collect_folders(
        self,
        base: Path,
        acc: list[Path],
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
        path: str | Path | None = None,
        extensions: list[str] | None = None,
        recursive: bool = False,
    ) -> list[Path]:
        """
        List files inside *path*.

        Args:
            path:       Directory to scan. Defaults to ``self.root``.
            extensions: Optional filter, e.g. ``['.pdf', '.docx']``.
            recursive:  Recurse into sub-folders when True.

        Returns:
            Sorted list of matching pathlib.Path objects.
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

    def copy_files_from_drive(
        self,
        source_drive_folder: str | Path,
        local_dest: str | Path,
        extensions: list[str] | None = None,
        overwrite: bool = False,
    ) -> list[Path]:
        """
        Copy files from a OneDrive folder into a local directory.

        Args:
            source_drive_folder: Path inside OneDrive.
            local_dest:          Local destination directory.
            extensions:          File-type filter (e.g. ``['.pdf']``).
            overwrite:           Replace existing local files when True.

        Returns:
            List of copied local pathlib.Path objects.
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
            _log.info("Downloaded OneDrive -> Local : %s", f.name)
            copied.append(target)

        _log.info("Copied %d file(s) from OneDrive to local: %s", len(copied), dst)
        return copied

    def copy_files_to_drive(
        self,
        local_source: str | Path,
        dest_drive_folder: str | Path,
        extensions: list[str] | None = None,
        overwrite: bool = False,
    ) -> list[Path]:
        """
        Copy files from a local directory into a OneDrive folder.

        Args:
            local_source:      Local directory to read from.
            dest_drive_folder: Destination inside OneDrive.
            extensions:        File-type filter (e.g. ``['.pdf']``).
            overwrite:         Replace existing OneDrive files when True.

        Returns:
            List of pathlib.Path objects written to OneDrive.
        """
        src = Path(local_source)
        dst = Path(dest_drive_folder)

        if not dst.exists():
            dst.mkdir(parents=True, exist_ok=True)
            _log.info("Created OneDrive folder: %s", dst)

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
                _log.debug("Skipped (already exists in OneDrive): %s", target.name)
                continue
            shutil.copy2(f, target)
            _log.info("Uploaded  Local -> OneDrive : %s", f.name)
            copied.append(target)

        _log.info("Copied %d file(s) to OneDrive folder: %s", len(copied), dst)
        return copied

    def info(self) -> dict[str, Any]:
        """
        Return a summary of the OneDrive folder.

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
        _log.info("OneDrive info: %s", result)
        return result

    def __repr__(self) -> str:
        status = "available" if self.is_available else "not available"
        return f"<OneDriveAccess root={self.root!r} [{status}]>"


# ===========================================================================
# 2. Microsoft Graph API-based Access (OAuth Client Template)
# ===========================================================================
class OneDriveAPIAccess:
    """
    Microsoft Graph API Client for OneDrive cloud access.
    
    This class interfaces directly with the Microsoft Graph cloud endpoint using
    MSAL (Microsoft Authentication Library) and requests.
    
    To configure, supply your client details from Azure App Registration.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str = "common",
        scopes: list[str] | None = None,
    ):
        self.client_id = client_id or _read_env_key("MICROSOFT_CLIENT_ID")
        self.client_secret = client_secret or _read_env_key("MICROSOFT_CLIENT_SECRET")
        self.tenant_id = tenant_id or _read_env_key("MICROSOFT_TENANT_ID", "common")
        self.scopes = scopes or ["Files.ReadWrite", "Files.ReadWrite.All", "User.Read"]
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        
        self.access_token: Optional[str] = None
        self._msal_app: Optional[msal.ConfidentialClientApplication] = None
        
        if not HAS_API_DEPS:
            _log.warning(
                "Microsoft Graph API dependencies missing. "
                "To use OneDriveAPIAccess, run: pip install msal requests"
            )

    def init_client(self) -> bool:
        """Initialize the MSAL client. Returns True if successful."""
        if not HAS_API_DEPS:
            raise ImportError("Required libraries ('msal' or 'requests') are not installed.")
        
        if not self.client_id or not self.client_secret:
            _log.error("Missing MICROSOFT_CLIENT_ID or MICROSOFT_CLIENT_SECRET.")
            return False

        try:
            self._msal_app = msal.ConfidentialClientApplication(
                self.client_id,
                authority=self.authority,
                client_credential=self.client_secret
            )
            return True
        except Exception as e:
            _log.error("Failed to initialize MSAL application: %s", e)
            return False

    def acquire_token_by_client_credential(self) -> bool:
        """Acquire a token representing the application daemon itself (Application Permissions)."""
        if not self._msal_app:
            if not self.init_client():
                return False
        
        # Scopes for client credentials must be the default resource scope
        scopes = ["https://graph.microsoft.com/.default"]
        try:
            result = self._msal_app.acquire_token_for_client(scopes=scopes) # type: ignore[union-attr]
            if "access_token" in result:
                self.access_token = result["access_token"]
                _log.info("Successfully acquired app-only access token.")
                return True
            else:
                _log.error("Failed to acquire token: %s", result.get("error_description"))
                return False
        except Exception as e:
            _log.error("Exception during token acquisition: %s", e)
            return False

    def get_auth_url(self, redirect_uri: str, state: str | None = None) -> str:
        """
        Generate the user authentication URL (Delegated Permissions).
        Redirect user here to sign in and authorize the app.
        """
        if not self._msal_app:
            self.init_client()
        return self._msal_app.get_authorization_request_url( # type: ignore[union-attr]
            self.scopes,
            redirect_uri=redirect_uri,
            state=state
        )

    def acquire_token_by_auth_code(self, auth_code: str, redirect_uri: str) -> bool:
        """Exchange the authorization code for an access token (Delegated Permissions)."""
        if not self._msal_app:
            self.init_client()
        try:
            result = self._msal_app.acquire_token_by_authorization_code( # type: ignore[union-attr]
                auth_code,
                scopes=self.scopes,
                redirect_uri=redirect_uri
            )
            if "access_token" in result:
                self.access_token = result["access_token"]
                _log.info("Successfully acquired user delegated access token.")
                return True
            else:
                _log.error("Failed to acquire token: %s", result.get("error_description"))
                return False
        except Exception as e:
            _log.error("Exception during authorization code exchange: %s", e)
            return False

    def list_files(self, folder_path: str = "") -> list[dict[str, Any]]:
        """
        List items in a specific OneDrive folder using Microsoft Graph.
        
        Args:
            folder_path: Relative path in OneDrive (e.g. 'Documents/Invoices').
                         Empty string defaults to the drive root.
        """
        if not self.access_token:
            _log.error("No access token available. Authenticate first.")
            return []

        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        # Build API endpoint
        if folder_path.strip("/") == "":
            endpoint = "https://graph.microsoft.com/v1.0/me/drive/root/children"
        else:
            endpoint = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder_path}:/children"

        try:
            res = requests.get(endpoint, headers=headers)
            if res.status_code == 200:
                data = res.json()
                items = data.get("value", [])
                _log.info("Found %d items in OneDrive cloud folder: %s", len(items), folder_path)
                return items
            else:
                _log.error("Failed to list items. Status: %d, Response: %s", res.status_code, res.text)
                return []
        except Exception as e:
            _log.error("Exception listing cloud files: %s", e)
            return []


# ---------------------------------------------------------------------------
# CLI Entry-point (python onedrive_access.py --help)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Browse a OneDrive folder (no API required by default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect OneDrive root (scans env vars or user profile folders)
  python onedrive_access.py

  # Specify the OneDrive root explicitly
  python onedrive_access.py --root "C:\\Users\\Intern\\OneDrive"

  # Browse a sub-folder and filter to PDFs only
  python onedrive_access.py --folder "Documents" --ext pdf
""",
    )
    parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="Mounted/local OneDrive path. Defaults to ONEDRIVE_ROOT in .env or auto-detect.",
    )
    parser.add_argument(
        "--folder",
        default=None,
        metavar="SUBFOLDER",
        help="Sub-folder inside OneDrive root to inspect.",
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

    od = OneDriveAccess(drive_root=args.root)

    sep = "=" * 60
    print(f"\n{sep}")
    print("  OneDrive Access Module")
    print(sep)
    print(f"  Status : {'Connected' if od.is_available else 'NOT available'}")
    print(f"  Root   : {od.root}")
    print(f"{sep}\n")

    if not od.is_available:
        print("SETUP STEPS:")
        print("  1. Sign in to your OneDrive desktop client.")
        print("  2. Ensure it is synced.")
        print("  3. Set ONEDRIVE_ROOT in your .env file if auto-detection failed.")
        print()
        raise SystemExit(1)

    target = od.get_folder(args.folder) if args.folder else od.root

    print(f"Folders under: {target}")
    print("-" * 50)
    folders = od.list_folders(target, depth=args.depth)
    if not folders:
        print("  (no sub-folders found)")

    print(f"\nFiles under: {target}")
    print("-" * 50)
    files = od.list_files(target, extensions=args.ext)
    if not files:
        print("  (no files found)")

    print(f"\nDone. Found {len(folders)} folder(s) and {len(files)} file(s).\n")

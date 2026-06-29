import os
import json
import time
import tempfile
import shutil
import io
import logging
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
import msal
from fastapi import APIRouter, Request, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field

# Project modules
from file_classifier import run_pipeline_full, load_categories_from_env, get_logger

# Define router
router = APIRouter()
logger = get_logger("file_classifier.onedrive_oauth")

# Scopes needed for accessing files and folders in OneDrive and user profile
SCOPES = [
    "Files.ReadWrite",
    "Files.ReadWrite.All",
    "User.Read"
]

# --------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------
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


def is_oauth_configured() -> bool:
    """Check if Microsoft Client ID and Secret are configured."""
    client_id = _read_env_key("MICROSOFT_CLIENT_ID")
    client_secret = _read_env_key("MICROSOFT_CLIENT_SECRET")
    return bool(client_id and client_secret)


def get_onedrive_redirect_uri(request: Request) -> str:
    """Gets the redirect URI for the OneDrive callback, prioritizing environment configuration."""
    env_uri = _read_env_key("ONEDRIVE_REDIRECT_URI")
    if env_uri:
        return env_uri
    # Construct callback dynamically, normalizing to localhost for cookie consistency
    url = str(request.url_for("onedrive_callback"))
    if request.headers.get("x-forwarded-proto") == "https":
        url = url.replace("http://", "https://")
    # Normalize 127.0.0.1 → localhost to match Azure app registration and avoid cookie domain issues
    url = url.replace("://127.0.0.1", "://localhost")
    return url


# --------------------------------------------------------------------------
# Server-side session storage (avoids 4KB cookie size limit)
# --------------------------------------------------------------------------
SESSION_DIR = Path(__file__).parent / ".sessions"
SESSION_DIR.mkdir(exist_ok=True)

def _od_session_path(session_id: str) -> Path:
    """Return the file path for a given OneDrive session ID."""
    safe_id = "".join(c for c in session_id if c.isalnum() or c in ('-', '_'))
    return SESSION_DIR / f"onedrive_{safe_id}.json"

def _od_save_session(session_id: str, data: dict):
    """Save credential data to a server-side session file."""
    _od_session_path(session_id).write_text(json.dumps(data), encoding="utf-8")

def _od_load_session(session_id: str) -> Optional[dict]:
    """Load credential data from a server-side session file."""
    path = _od_session_path(session_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def _od_delete_session(session_id: str):
    """Delete a server-side session file."""
    path = _od_session_path(session_id)
    if path.exists():
        path.unlink(missing_ok=True)


def get_valid_token(request: Request) -> Optional[str]:
    """Helper to retrieve and refresh access token from server-side session."""
    session_id = request.cookies.get("onedrive_session_id")
    if not session_id:
        return None
    data = _od_load_session(session_id)
    if not data:
        return None
    try:
        access_token = data.get("access_token")
        expires_at = data.get("expires_at", 0)

        # If token is expired or about to expire in 60 seconds, refresh it
        if expires_at < time.time() + 60:
            refresh_token = data.get("refresh_token")
            if refresh_token:
                logger.info("OneDrive access token expired. Attempting to refresh...")
                client_id = _read_env_key("MICROSOFT_CLIENT_ID")
                client_secret = _read_env_key("MICROSOFT_CLIENT_SECRET")
                tenant_id = _read_env_key("MICROSOFT_TENANT_ID", "common")
                authority = f"https://login.microsoftonline.com/{tenant_id}"

                app = msal.ConfidentialClientApplication(
                    client_id,
                    authority=authority,
                    client_credential=client_secret
                )
                result = app.acquire_token_by_refresh_token(refresh_token, scopes=SCOPES)
                if "access_token" in result:
                    logger.info("OneDrive access token successfully refreshed.")
                    # Update the session with new token
                    data["access_token"] = result["access_token"]
                    data["expires_at"] = time.time() + result.get("expires_in", 3600)
                    if result.get("refresh_token"):
                        data["refresh_token"] = result["refresh_token"]
                    _od_save_session(session_id, data)
                    return result["access_token"]
                else:
                    logger.warning("Failed to refresh OneDrive token: %s", result.get("error_description"))
        return access_token
    except Exception as e:
        logger.error("Failed to parse OneDrive credentials from session: %s", e)
        return None


# --------------------------------------------------------------------------
# OAuth Endpoints
# --------------------------------------------------------------------------
@router.get("/onedrive/check-setup")
def check_setup():
    """Verify if the Microsoft OAuth credentials are configured on the server."""
    configured = is_oauth_configured()
    return {
        "status": "success",
        "oauth_configured": configured,
        "message": "Microsoft OAuth is configured. You can sign in." if configured else "Missing MICROSOFT_CLIENT_ID or MICROSOFT_CLIENT_SECRET in .env."
    }


@router.get("/onedrive/login", include_in_schema=True)
def onedrive_login(request: Request):
    """Initiates the Microsoft OAuth flow by redirecting to Microsoft accounts page."""
    if not is_oauth_configured():
        raise HTTPException(
            status_code=500,
            detail="Microsoft OAuth credentials are not configured in your .env file."
        )

    client_id = _read_env_key("MICROSOFT_CLIENT_ID")
    client_secret = _read_env_key("MICROSOFT_CLIENT_SECRET")
    tenant_id = _read_env_key("MICROSOFT_TENANT_ID", "common")
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    callback_url = get_onedrive_redirect_uri(request)

    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret
    )

    # Determine referer, mapping root or port 8000 to the frontend dev server at port 8080/onedrive
    referer = request.headers.get("referer") or "http://localhost:8080/onedrive"
    if "8000" in referer or referer == "/":
        referer = "http://localhost:8080/onedrive"

    # Generate a CSRF token and package it with the redirect URI in the state parameter
    import base64
    import secrets
    csrf_token = secrets.token_urlsafe(16)
    state_payload = {
        "csrf": csrf_token,
        "redirect": referer
    }
    state_json = json.dumps(state_payload)
    state_param = base64.urlsafe_b64encode(state_json.encode("utf-8")).decode("utf-8")

    authorization_url = app.get_authorization_request_url(
        SCOPES,
        redirect_uri=callback_url,
        state=state_param
    )

    response = RedirectResponse(url=authorization_url)
    response.set_cookie(
        key="onedrive_oauth_state",
        value=csrf_token,
        httponly=True,
        max_age=600,
        samesite="lax"
    )
    return response


@router.get("/onedrive/callback", include_in_schema=True)
def onedrive_callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Callback receiver that Microsoft redirects to after auth success."""
    if error:
        raise HTTPException(status_code=400, detail=f"Microsoft OAuth Error: {error}")

    saved_state = request.cookies.get("onedrive_oauth_state")
    redirect_target = "http://localhost:8080/onedrive"
    
    if state:
        try:
            import base64
            state_json = base64.urlsafe_b64decode(state.encode("utf-8")).decode("utf-8")
            state_payload = json.loads(state_json)
            if state_payload.get("csrf") != saved_state:
                logger.warning("OneDrive OAuth state/CSRF mismatch. Saved: %s, Received: %s", saved_state, state_payload.get("csrf"))
            redirect_target = state_payload.get("redirect", redirect_target)
        except Exception as e:
            logger.error("Failed to parse OneDrive OAuth state: %s", e)
            if state == saved_state:
                redirect_target = "http://localhost:8080/onedrive"

    client_id = _read_env_key("MICROSOFT_CLIENT_ID")
    client_secret = _read_env_key("MICROSOFT_CLIENT_SECRET")
    tenant_id = _read_env_key("MICROSOFT_TENANT_ID", "common")
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    callback_url = get_onedrive_redirect_uri(request)

    try:
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=authority,
            client_credential=client_secret
        )
        result = app.acquire_token_by_authorization_code(
            code,
            scopes=SCOPES,
            redirect_uri=callback_url
        )

        if "access_token" not in result:
            raise ValueError(result.get("error_description", "Unknown error during token exchange"))

        creds_data = {
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token"),
            "expires_at": time.time() + result.get("expires_in", 3600)
        }

        # Save credentials server-side and store only a session ID in cookie
        session_id = uuid.uuid4().hex
        _od_save_session(session_id, creds_data)
        logger.info("OneDrive OAuth: credentials saved to server-side session (id=%s..)", session_id[:8])

        # Success! Redirect back to the dashboard UI
        response = RedirectResponse(url=redirect_target)
        
        # Set small session ID cookie (well under 4KB limit)
        response.set_cookie(
            key="onedrive_session_id",
            value=session_id,
            httponly=True,
            max_age=3600 * 24 * 7, # valid for 7 days
            samesite="lax"
        )
        # Clear OAuth state cookie and old credential cookie
        response.delete_cookie("onedrive_oauth_state")
        response.delete_cookie("onedrive_credentials")  # clean up old cookie if present
        return response
    except Exception as e:
        logger.error("OneDrive OAuth flow token fetching failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch authorization token: {str(e)}")


@router.get("/onedrive/logout")
def onedrive_logout(request: Request):
    """Logs out by clearing OneDrive session."""
    session_id = request.cookies.get("onedrive_session_id")
    if session_id:
        _od_delete_session(session_id)
    response = RedirectResponse(url="/")
    response.delete_cookie("onedrive_session_id")
    response.delete_cookie("onedrive_credentials")  # clean up old cookie if present
    return response


@router.get("/onedrive/profile")
def onedrive_profile(request: Request):
    """Retrieve details of the currently signed-in OneDrive account."""
    token = get_valid_token(request)
    if not token:
        return {"authenticated": False, "email": None, "name": None}

    try:
        headers = {"Authorization": f"Bearer {token}"}
        profile_res = requests.get("https://graph.microsoft.com/v1.0/me", headers=headers)
        
        if profile_res.status_code == 200:
            user_info = profile_res.json()
            return {
                "authenticated": True,
                "email": user_info.get("mail") or user_info.get("userPrincipalName"),
                "name": user_info.get("displayName"),
                "picture": None # Fetching profile picture requires extra API call, we'll keep it simple for now
            }
        else:
            raise ValueError(f"HTTP {profile_res.status_code}: {profile_res.text}")
    except Exception as e:
        logger.error("Failed to query Microsoft profile: %s", e)
        session_id = request.cookies.get("onedrive_session_id")
        if session_id:
            _od_delete_session(session_id)
        response = JSONResponse(content={"authenticated": False, "email": None, "name": None, "error": str(e)})
        response.delete_cookie("onedrive_session_id")
        return response


# --------------------------------------------------------------------------
# API Endpoints for listing Folders and Running Classification
# --------------------------------------------------------------------------
@router.get("/onedrive/folders")
def list_folders(request: Request, parent_id: Optional[str] = Query("root")):
    """List OneDrive folders inside a parent folder (default: root)."""
    token = get_valid_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="User is not signed in to Microsoft.")

    try:
        headers = {"Authorization": f"Bearer {token}"}
        
        # Build API endpoint
        if parent_id == "root":
            url = "https://graph.microsoft.com/v1.0/me/drive/root/children"
        else:
            url = f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}/children"

        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            raise ValueError(f"Microsoft API returned {res.status_code}: {res.text}")

        items = res.json().get("value", [])
        
        # Filter for folders only
        folders = []
        for item in items:
            if "folder" in item:
                folders.append({
                    "id": item.get("id"),
                    "name": item.get("name")
                })
        
        # Fetch parent name if not root
        parent_name = "Root"
        if parent_id != "root":
            try:
                parent_res = requests.get(f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}", headers=headers)
                if parent_res.status_code == 200:
                    parent_name = parent_res.json().get("name", "Folder")
            except Exception:
                pass

        return {
            "status": "success",
            "parent_id": parent_id,
            "parent_name": parent_name,
            "folders": folders
        }
    except Exception as e:
        logger.error("Failed to list OneDrive folders: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class OneDriveDriveClassifyRequest(BaseModel):
    onedrive_input_folder_id: str = Field(..., description="The ID of the input folder in OneDrive containing PDFs")
    onedrive_output_folder_id: str = Field(..., description="The ID of the parent folder in OneDrive where 'sorted' results will be created")
    copy_mode: bool = Field(True, description="Keep original PDFs in the input folder when True")
    dry_run: bool = Field(False, description="Classify files without uploading sorted outputs back to OneDrive")
    pdf_max_pages: int = Field(3, ge=1, le=20)
    min_score: float = Field(7.0, ge=0.0, le=10.0)
    llm_model: Optional[str] = Field("gpt-4o")
    categories: Optional[List[str]] = Field(None, description="Active categories to classify against. None = use all configured categories")
    max_files: Optional[int] = Field(None, description="Limit on number of files to process")


@router.post("/onedrive/drive/classify")
async def cloud_onedrive_classify(request: Request, body: OneDriveDriveClassifyRequest):
    """
    Downloads PDFs from a OneDrive cloud folder, runs classification on the server,
    and uploads the results organized into folders back to OneDrive.
    """
    token = get_valid_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="User is not signed in to Microsoft.")

    try:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Verify source folder exists and list PDFs inside it
        logger.info("Listing PDFs inside OneDrive cloud folder: %s", body.onedrive_input_folder_id)
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{body.onedrive_input_folder_id}/children"
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            raise ValueError(f"Microsoft API returned {res.status_code}: {res.text}")

        items = res.json().get("value", [])
        pdf_files = []
        for item in items:
            if "file" in item and item.get("name", "").lower().endswith(".pdf"):
                pdf_files.append({
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "download_url": item.get("@microsoft.graph.downloadUrl")
                })

        if not pdf_files:
            return {
                "success": True,
                "message": "No PDF files found in the specified OneDrive input folder.",
                "pdfs_processed": 0,
                "pipeline_results": []
            }

        # Apply max_files limit if set in the request
        if body.max_files is not None and body.max_files > 0:
            pdf_files = pdf_files[:body.max_files]
            logger.info("Applying limit: processing first %d file(s) from OneDrive", len(pdf_files))

        logger.info("Found %d PDF file(s) to process in OneDrive.", len(pdf_files))

        # 2. Setup local temp workspace on server
        temp_root = Path(tempfile.mkdtemp(prefix="onedrive_cloud_classifier_"))
        temp_input = temp_root / "input"
        temp_output = temp_root / "output"
        temp_input.mkdir(parents=True, exist_ok=True)
        temp_output.mkdir(parents=True, exist_ok=True)

        # 3. Download PDFs from OneDrive to local temp input directory
        downloaded_count = 0
        for item in pdf_files:
            file_id = item["id"]
            file_name = item["name"]
            download_url = item["download_url"]
            dest_path = temp_input / file_name

            if not download_url:
                logger.warning("No download URL found for file %s (ID: %s)", file_name, file_id)
                continue

            logger.info("Downloading file from OneDrive: %s", file_name)
            try:
                request_dl = requests.get(download_url)
                if request_dl.status_code == 200:
                    dest_path.write_bytes(request_dl.content)
                    downloaded_count += 1
                else:
                    logger.error("Failed to download file %s: HTTP %d", file_name, request_dl.status_code)
            except Exception as dl_err:
                logger.error("Failed to download file %s: %s", file_name, dl_err)

        if downloaded_count == 0:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise HTTPException(status_code=500, detail="Failed to download any PDFs from OneDrive.")

        # 4. Load Categories
        categories = load_categories_from_env()
        # Filter categories to only keep the active ones checked in the frontend UI
        if body.categories is not None:
            categories = {k: v for k, v in categories.items() if k in body.categories}
            logger.info("Active categories filtered to: %s", list(categories.keys()))

        # 5. Run the Local Classifier Pipeline
        model = body.llm_model
        if not model or model.strip().lower() in ("string", "", "null", "none"):
            model = "gpt-4o"

        logger.info("Running local classification pipeline on %d downloaded files", downloaded_count)
        pipeline_results = run_pipeline_full(
            input_folder=temp_input,
            output_folder=temp_output,
            categories=categories,
            pdf_max_pages=body.pdf_max_pages,
            min_score=body.min_score,
            llm_model=model,
            copy_mode=True,  # Always copy locally inside server
            dry_run=body.dry_run
        )

        # 6. Upload categorized outputs back to OneDrive (if not dry_run)
        uploaded_files = []
        if not body.dry_run:
            logger.info("Uploading categorized files back to OneDrive under parent: %s", body.onedrive_output_folder_id)
            
            # Check if "sorted" folder already exists in output folder
            check_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{body.onedrive_output_folder_id}/children"
            check_res = requests.get(check_url, headers=headers)
            sorted_folder_id = None
            if check_res.status_code == 200:
                for item in check_res.json().get("value", []):
                    if item.get("name") == "sorted" and "folder" in item:
                        sorted_folder_id = item.get("id")
                        break
            
            if sorted_folder_id:
                logger.info("Reusing existing 'sorted' folder: %s", sorted_folder_id)
            else:
                # Create "sorted" folder
                create_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{body.onedrive_output_folder_id}/children"
                create_body = {
                    "name": "sorted",
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "fail"
                }
                create_res = requests.post(create_url, headers=headers, json=create_body)
                if create_res.status_code in (200, 201):
                    sorted_folder_id = create_res.json().get("id")
                    logger.info("Created new 'sorted' folder in OneDrive: %s", sorted_folder_id)
                else:
                    raise ValueError(f"Failed to create 'sorted' folder in OneDrive: {create_res.text}")

            # Upload subfolders and files
            for cat_folder in temp_output.iterdir():
                if not cat_folder.is_dir():
                    continue
                
                # Exclude internal temporary dirs if any
                if cat_folder.name in ("extracted_text", "rotated_pages"):
                    continue

                cat_name = cat_folder.name
                
                # Check if category subfolder already exists inside "sorted"
                cat_check_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{sorted_folder_id}/children"
                cat_check_res = requests.get(cat_check_url, headers=headers)
                cat_folder_id = None
                if cat_check_res.status_code == 200:
                    for item in cat_check_res.json().get("value", []):
                        if item.get("name") == cat_name and "folder" in item:
                            cat_folder_id = item.get("id")
                            break
                
                if not cat_folder_id:
                    # Create category subfolder
                    create_cat_body = {
                        "name": cat_name,
                        "folder": {},
                        "@microsoft.graph.conflictBehavior": "fail"
                    }
                    create_cat_res = requests.post(f"https://graph.microsoft.com/v1.0/me/drive/items/{sorted_folder_id}/children", headers=headers, json=create_cat_body)
                    if create_cat_res.status_code in (200, 201):
                        cat_folder_id = create_cat_res.json().get("id")
                    else:
                        logger.error("Failed to create category folder %s in OneDrive: %s", cat_name, create_cat_res.text)
                        continue

                # Upload each PDF in this category
                for pdf_file in cat_folder.glob("*.pdf"):
                    logger.info("Uploading %s to OneDrive category %s", pdf_file.name, cat_name)
                    upload_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{cat_folder_id}:/{pdf_file.name}:/content"
                    pdf_data = pdf_file.read_bytes()
                    
                    upload_res = requests.put(
                        upload_url,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/pdf"
                        },
                        data=pdf_data
                    )
                    
                    if upload_res.status_code in (200, 201):
                        uploaded_files.append({
                            "file_name": pdf_file.name,
                            "category": cat_name,
                            "destination": f"sorted/{cat_name}/{pdf_file.name}"
                        })
                    else:
                        logger.error("Failed to upload %s to OneDrive: %s", pdf_file.name, upload_res.text)

            # 7. Delete original files from OneDrive (if copy_mode is False)
            if not body.copy_mode:
                logger.info("Deleting original files from OneDrive input folder since copy_mode = False")
                for item in pdf_files:
                    file_id = item["id"]
                    try:
                        logger.info("Deleting original file ID: %s from OneDrive", file_id)
                        del_res = requests.delete(f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}", headers=headers)
                        if del_res.status_code != 204:
                            logger.error("Failed to delete original file ID %s. Status code: %d", file_id, del_res.status_code)
                    except Exception as del_err:
                        logger.error("Failed to delete original file ID %s: %s", file_id, del_err)

        # 8. Clean up local server temp folders
        shutil.rmtree(temp_root, ignore_errors=True)

        return {
            "success": True,
            "pdfs_processed": len(pdf_files),
            "dry_run": body.dry_run,
            "uploaded": uploaded_files,
            "pipeline_results": pipeline_results
        }
        
    except Exception as exc:
        logger.error("OneDrive Cloud classification pipeline failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

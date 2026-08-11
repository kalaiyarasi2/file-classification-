import os
import json
import tempfile
import shutil
import io
import base64
import secrets
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

# Suppress OAuthlib's Warning exception for scope changes (e.g. Google adding 'openid' automatically)
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"


from fastapi import APIRouter, Request, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field

# Google libraries
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# Project modules
from file_classifier import run_pipeline_full, load_categories_from_env, get_logger

# Define router
router = APIRouter()
logger = get_logger("file_classifier.google_oauth")

CLIENT_SECRET_FILE = Path(__file__).parent.parent / "client_secret.json"

# Google Scopes needed for accessing files and folders in Google Drive and user profile
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",  # To list and download PDFs
    "https://www.googleapis.com/auth/drive",           # Full access to allow deleting/moving user-created files
    "https://www.googleapis.com/auth/userinfo.profile",# To get user name and picture
    "https://www.googleapis.com/auth/userinfo.email",  # To get user email address
]



# --------------------------------------------------------------------------
# Server-side session storage (avoids 4KB cookie size limit)
# --------------------------------------------------------------------------
WORKSPACE_DIR = Path(__file__).parent.parent
SESSION_DIR = WORKSPACE_DIR / ".sessions"
SESSION_DIR.mkdir(exist_ok=True)

def _session_path(session_id: str) -> Path:
    """Return the file path for a given session ID."""
    # Sanitize to prevent directory traversal
    safe_id = "".join(c for c in session_id if c.isalnum() or c in ('-', '_'))
    return SESSION_DIR / f"google_{safe_id}.json"

def _save_session(session_id: str, data: dict):
    """Save credential data to a server-side session file."""
    _session_path(session_id).write_text(json.dumps(data), encoding="utf-8")

def _load_session(session_id: str) -> Optional[dict]:
    """Load credential data from a server-side session file."""
    path = _session_path(session_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def _delete_session(session_id: str):
    """Delete a server-side session file."""
    path = _session_path(session_id)
    if path.exists():
        path.unlink(missing_ok=True)

# --------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------
def get_credentials_from_cookie(request: Request) -> Optional[Credentials]:
    """Helper to retrieve Google Credentials from server-side session."""
    session_id = request.cookies.get("google_session_id")
    if not session_id:
        return None
    data = _load_session(session_id)
    if not data:
        return None
    try:
        return Credentials.from_authorized_user_info(data, SCOPES)
    except Exception as e:
        logger.error("Failed to parse credentials from session: %s", e)
        return None

def is_oauth_configured() -> bool:
    """Check if client_secret.json exists."""
    return CLIENT_SECRET_FILE.exists()

def get_redirect_uri(request: Request) -> str:
    """Gets the redirect URI, matching the current host and scheme if possible."""
    scheme = request.headers.get("x-forwarded-proto") or request.headers.get("x-forwarded-scheme") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    
    # IIS URL Rewrite drops the original host and sets it to 127.0.0.1:9000. 
    # Force the production domain if we detect this internal proxy address.
    if "127.0.0.1:9000" in host or "localhost:9000" in host:
        host = "app.drive360.ai"
        scheme = "https"
        
    if host and "localhost" not in host and "127.0.0.1" not in host:
        scheme = "https"

    url = str(request.url_for("google_oauth2callback"))
    if scheme == "https" and url.startswith("http://"):
        url = url.replace("http://", "https://", 1)

    try:
        if CLIENT_SECRET_FILE.exists():
            with open(CLIENT_SECRET_FILE, "r") as f:
                data = json.load(f)
            uris = data.get("web", {}).get("redirect_uris", [])
            
            # 1. Exact match
            if url in uris:
                return url

            # 2. Match host and scheme
            for uri in uris:
                if host and host in uri and uri.startswith(scheme + "://"):
                    return uri

            # 3. Match host regardless of scheme
            for uri in uris:
                if host and host in uri:
                    return uri
                    
            if uris:
                return uris[0]
    except Exception as e:
        logger.error("Failed to read redirect_uris from client_secret.json: %s", e)
        
    return url


# --------------------------------------------------------------------------
# OAuth Endpoints
# --------------------------------------------------------------------------
@router.get("/google/check-setup")
def check_setup():
    """Verify if the OAuth client secret is configured on the server."""
    configured = is_oauth_configured()
    return {
        "status": "success",
        "oauth_configured": configured,
        "message": "OAuth is configured. You can sign in." if configured else "Missing client_secret.json on server. Please check the setup instructions."
    }

@router.get("/google/login", include_in_schema=True)
def google_login(request: Request, redirect_to_ui: bool = Query(True)):
    """Initiates the OAuth flow by redirecting the user to Google login page."""
    if not is_oauth_configured():
        raise HTTPException(
            status_code=500,
            detail="Google OAuth client_secret.json is not configured on the server. Please place it in the root folder."
        )

    # Use the registered redirect URI from client_secret.json to avoid mismatch
    callback_url = get_redirect_uri(request)

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE),
        scopes=SCOPES,
        redirect_uri=callback_url,
        autogenerate_code_verifier=False
    )

    # Determine referer, mapping root or port 8000 to the frontend dev server at port 8080/drive
    referer = request.headers.get("referer") or "http://localhost:8080/drive"
    if "8000" in referer or referer == "/":
        referer = "http://localhost:8080/drive"

    # Generate a CSRF token and package it with the redirect URI in the state parameter
    csrf_token = secrets.token_urlsafe(16)
    state_payload = {
        "csrf": csrf_token,
        "redirect": referer
    }
    state_json = json.dumps(state_payload)
    state_param = base64.urlsafe_b64encode(state_json.encode("utf-8")).decode("utf-8")

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state_param
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta http-equiv="refresh" content="0; url={authorization_url}">
    </head>
    <body>
        <script>window.location.href = "{authorization_url}";</script>
        Redirecting to Google...
    </body>
    </html>
    """
    response = HTMLResponse(content=html_content)
    # Store CSRF state in cookie for verification
    response.set_cookie(
        key="google_oauth_state",
        value=csrf_token,
        httponly=True,
        max_age=600,
        samesite="lax",
        secure=True,
        path="/"
    )
    return response


@router.get("/google/oauth2callback", include_in_schema=True)
def google_oauth2callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Callback receiver that Google redirects to after auth success."""
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth Error: {error}")

    # Decode target redirect URL and CSRF token from the state parameter
    csrf_token = None
    redirect_uri = "http://localhost:8080/drive"
    if state:
        try:
            state_data = json.loads(base64.urlsafe_b64decode(state.encode("utf-8")).decode("utf-8"))
            csrf_token = state_data.get("csrf")
            redirect_uri = state_data.get("redirect") or "http://localhost:8080/drive"
        except Exception as e:
            logger.error("Failed to parse state parameter: %s", e)

    saved_state = request.cookies.get("google_oauth_state")
    if not saved_state or saved_state != csrf_token:
        logger.warning("OAuth state mismatch. Saved: %s, Received: %s", saved_state, csrf_token)
        # Continue anyway for development/flexible callbacks

    # Use the registered redirect URI to match the token exchange redirect_uri
    callback_url = get_redirect_uri(request)

    try:
        flow = Flow.from_client_secrets_file(
            str(CLIENT_SECRET_FILE),
            scopes=SCOPES,
            redirect_uri=callback_url,
            autogenerate_code_verifier=False
        )
        flow.fetch_token(code=code)
        
        credentials = flow.credentials
        creds_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes
        }
        
        # Save credentials server-side and store only a session ID in cookie
        session_id = uuid.uuid4().hex
        _save_session(session_id, creds_data)
        logger.info("Google OAuth: credentials saved to server-side session (id=%s..)", session_id[:8])
        
        # Success! Redirect back to the dashboard UI
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="0; url={redirect_uri}">
        </head>
        <body>
            <script>window.location.href = "{redirect_uri}";</script>
            Redirecting...
        </body>
        </html>
        """
        response = HTMLResponse(content=html_content)
        
        # Set small session ID cookie (well under 4KB limit)
        response.set_cookie(
            key="google_session_id",
            value=session_id,
            httponly=True,
            max_age=3600 * 24 * 7, # valid for 7 days
            samesite="lax",
            secure=True,
            path="/"
        )
        # Clear OAuth cookies
        response.delete_cookie("google_oauth_state", path="/")
        response.delete_cookie("google_drive_credentials", path="/")  # clean up old cookie if present
        return response
    except Exception as e:
        logger.error("OAuth flow token fetching failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch authorization token: {str(e)}")


@router.get("/google/logout")
def google_logout(request: Request):
    """Logs out by clearing Google OAuth session."""
    session_id = request.cookies.get("google_session_id")
    if session_id:
        _delete_session(session_id)
    referer = request.headers.get("referer") or "/"
    response = RedirectResponse(url=referer)
    response.delete_cookie("google_session_id", path="/")
    response.delete_cookie("google_drive_credentials", path="/")  # clean up old cookie if present
    return response

@router.get("/google/profile")
def google_profile(request: Request):
    """Retrieve details of the currently signed-in Google account."""
    creds = get_credentials_from_cookie(request)
    if not creds:
        return {"authenticated": False, "email": None, "name": None}

    try:
        # Request profile info from Google API
        service = build("oauth2", "v2", credentials=creds)
        user_info = service.userinfo().get().execute()
        return {
            "authenticated": True,
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "picture": user_info.get("picture")
        }
    except Exception as e:
        # Token might be expired/invalid, clear session
        logger.error("Failed to query user profile: %s", e)
        session_id = request.cookies.get("google_session_id")
        if session_id:
            _delete_session(session_id)
        response = JSONResponse(content={"authenticated": False, "email": None, "name": None, "error": str(e)})
        response.delete_cookie("google_session_id", path="/")
        return response

# --------------------------------------------------------------------------
# API Endpoints for listing Folders and Running Classification
# --------------------------------------------------------------------------
@router.get("/google/drive/folders")
def list_folders(request: Request, parent_id: Optional[str] = Query("root")):
    """List Google Drive folders inside a parent folder (default: root)."""
    creds = get_credentials_from_cookie(request)
    if not creds:
        raise HTTPException(status_code=401, detail="User is not signed in to Google.")

    try:
        service = build("drive", "v3", credentials=creds)
        
        # Build query to list folders only
        query = f"mimeType = 'application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed = false"
        results = service.files().list(
            q=query,
            pageSize=100,
            fields="nextPageToken, files(id, name)",
            orderBy="name"
        ).execute()
        
        folders = results.get("files", [])
        
        # Also get parent info if not root
        parent_name = "Root"
        if parent_id != "root":
            try:
                parent_meta = service.files().get(fileId=parent_id, fields="name, parents").execute()
                parent_name = parent_meta.get("name")
            except Exception:
                pass
                
        return {
            "status": "success",
            "parent_id": parent_id,
            "parent_name": parent_name,
            "folders": folders
        }
    except Exception as e:
        logger.error("Failed to list Google Drive folders: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class GoogleDriveClassifyRequest(BaseModel):
    drive_input_folder_id: str = Field(..., description="The ID of the input folder in Google Drive containing PDFs")
    drive_output_folder_id: str = Field(..., description="The ID of the parent folder in Google Drive where 'sorted' results will be created")
    copy_mode: bool = Field(True, description="Keep original PDFs in the input folder when True")
    dry_run: bool = Field(False, description="Classify files without uploading sorted outputs back to Google Drive")
    pdf_max_pages: int = Field(3, ge=1, le=20)
    min_score: float = Field(7.0, ge=0.0, le=10.0)
    llm_model: Optional[str] = Field("gpt-4o")
    categories: Optional[List[str]] = Field(None, description="Active categories to classify against. None = use all configured categories")
    max_files: Optional[int] = Field(None, description="Limit on number of files to process")
    poc_engine: Optional[str] = Field(None, description="The selected POC engine (e.g. AUTO, INSURANCE, etc.)")

@router.post("/google/drive/classify")
async def cloud_drive_classify(request: Request, body: GoogleDriveClassifyRequest):
    """
    Downloads PDFs from a Google Drive folder, runs classification on the server,
    and uploads the results organized into folders back to Google Drive.
    """
    creds = get_credentials_from_cookie(request)
    if not creds:
        raise HTTPException(status_code=401, detail="User is not signed in to Google.")

    try:
        service = build("drive", "v3", credentials=creds)

        # 1. Verify source folder exists and list PDFs inside it
        logger.info("Listing PDFs inside Google Drive folder: %s", body.drive_input_folder_id)
        query = f"mimeType = 'application/pdf' and '{body.drive_input_folder_id}' in parents and trashed = false"
        file_results = service.files().list(
            q=query,
            pageSize=100,
            fields="files(id, name)"
        ).execute()

        pdf_files = file_results.get("files", [])
        if not pdf_files:
            return {
                "success": True,
                "message": "No PDF files found in the specified input folder.",
                "pdfs_processed": 0,
                "pipeline_results": []
            }

        # Apply max_files limit if set in the request
        if body.max_files is not None and body.max_files > 0:
            pdf_files = pdf_files[:body.max_files]
            logger.info("Applying limit: processing first %d file(s) from Google Drive", len(pdf_files))

        logger.info("Found %d PDF file(s) to process.", len(pdf_files))

        # 2. Setup local temp workspace on server
        temp_root = Path(tempfile.mkdtemp(prefix="cloud_classifier_"))
        temp_input = temp_root / "input"
        temp_output = temp_root / "output"
        temp_input.mkdir(parents=True, exist_ok=True)
        temp_output.mkdir(parents=True, exist_ok=True)

        # 3. Download PDFs from Google Drive to local temp input directory
        downloaded_count = 0
        for item in pdf_files:
            file_id = item["id"]
            file_name = item["name"]
            dest_path = temp_input / file_name

            logger.info("Downloading file from Drive: %s (ID: %s)", file_name, file_id)
            try:
                request_dl = service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request_dl)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                
                # Write to disk
                dest_path.write_bytes(fh.getvalue())
                downloaded_count += 1
            except Exception as dl_err:
                logger.error("Failed to download file %s: %s", file_name, dl_err)

        if downloaded_count == 0:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise HTTPException(status_code=500, detail="Failed to download any PDFs from Google Drive.")

        # 4. Load Categories
        categories = load_categories_from_env()
        # Filter categories to only keep the active ones checked in the frontend UI
        if body.categories is not None:
            categories = {k: v for k, v in categories.items() if k in body.categories}
        elif body.poc_engine and body.poc_engine != "AUTO":
            engine_map = {
                "INSURANCE": ["INSURANCE_CLAIMS"],
                "WORK_COMP": ["WORK_COMPENSATION"],
                "BANK_STATEMENT": ["BANK_STATEMENT"],
                "VENDOR_INVOICE": ["VENDOR_INVOICE"],
                "PAYROLL": ["PAYROLL"],
                "SBC": ["PARITY_SETUP"],
                "RENEWAL": ["RENEWAL_PROCESS"],
                "RE": ["RESOURCING_EDGE"],
                "RPVE": ["RPVE"],
                "INVOICE": ["INVOICE"]
            }
            target_cats = engine_map.get(body.poc_engine)
            if target_cats:
                categories = {k: v for k, v in categories.items() if k in target_cats}

        logger.info("Active categories filtered to: %s", list(categories.keys()))

        # 5. Run the Local Classifier Pipeline
        # Sanitize LLM model
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


        if body.poc_engine == "FULL_PIPELINE" and not body.dry_run:
            import httpx
            import asyncio
            import json
            
            ENGINE_MAP = {
                "INVOICE": "/api/gpu/api/extract",
                "VENDOR_INVOICE": "/api/gpu/api/extract",
                "PAYROLL": "/api/payroll/process-pdf",
                "PARITY_SETUP": "/api/parity/api/extract",
                "RESOURCING_EDGE": "/api/resourcing/api/process-pdf",
                "WORK_COMPENSATION": "/api/gpu/api/extract",
                "BANK_STATEMENT": "/api/gpu/api/extract",
                "RPVE": "/api/rpve/api/extract",
                "RENEWAL_PROCESS": "/api/renewal/api/process",
                "INSURANCE_CLAIMS": "/api/gpu/api/extract"
            }
            
            async def process_file(result_entry):
                cat = result_entry.get("category", "")
                if cat in ENGINE_MAP:
                    endpoint = f"http://127.0.0.1:9000{ENGINE_MAP[cat]}"
                    file_path = result_entry.get("destination_folder")
                    if file_path and os.path.exists(file_path):
                        try:
                            logger.info(f"FULL_PIPELINE routing {file_path} to {endpoint}")
                            async with httpx.AsyncClient(timeout=300.0) as client:
                                with open(file_path, "rb") as f:
                                    client_files = {"file": (os.path.basename(file_path), f, "application/pdf")}
                                    resp = await client.post(endpoint, files=client_files)
                                    resp.raise_for_status()
                                    
                                    json_data = resp.json()
                                    
                                    # Parse URLs
                                    excel_url = None
                                    json_url = None
                                    if isinstance(json_data, dict) and "results" in json_data and isinstance(json_data["results"], list):
                                        if len(json_data["results"]) > 0:
                                            first_result = json_data["results"][0]
                                            if isinstance(first_result, dict):
                                                excel_url = first_result.get("excel_url")
                                                json_url = first_result.get("json_url")
                                    elif isinstance(json_data, dict):
                                        excel_url = json_data.get("excel") or json_data.get("excel_url")
                                        json_url = json_data.get("json") or json_data.get("json_url")
                                    
                                    base, _ = os.path.splitext(file_path)
                                    
                                    if excel_url or json_url:
                                        if excel_url:
                                            if not excel_url.startswith("http"): excel_url = f"http://127.0.0.1:9000{excel_url}"
                                            try:
                                                ex_resp = await client.get(excel_url)
                                                ex_resp.raise_for_status()
                                                with open(base + "_extracted.xlsx", "wb") as f_ex:
                                                    f_ex.write(ex_resp.content)
                                            except Exception as ex_e:
                                                logger.error(f"Failed to download {excel_url}: {ex_e}")
                                        if json_url:
                                            if not json_url.startswith("http"): json_url = f"http://127.0.0.1:9000{json_url}"
                                            try:
                                                js_resp = await client.get(json_url)
                                                js_resp.raise_for_status()
                                                with open(base + "_extracted.json", "wb") as f_js:
                                                    f_js.write(js_resp.content)
                                            except Exception as js_e:
                                                logger.error(f"Failed to download {json_url}: {js_e}")
                                    else:
                                        json_path = base + "_extraction.json"
                                        with open(json_path, "w") as jf:
                                            import json
                                            json.dump(json_data, jf, indent=2)

                                    result_entry["extraction_result"] = "Success"
                        except Exception as e:
                            logger.error(f"Error processing {file_path} in FULL_PIPELINE: {e}")
                            result_entry["extraction_result"] = f"Error: {e}"

            async def run_routing():
                tasks = [process_file(r) for r in pipeline_results if r.get("category")]
                await asyncio.gather(*tasks)
            
            await run_routing()

        # 6. Upload categorized outputs back to Google Drive (if not dry_run)
        uploaded_files = []
        if not body.dry_run:
            logger.info("Uploading categorized files back to Google Drive under parent: %s", body.drive_output_folder_id)
            
            # Create a "sorted" folder inside the output folder in Google Drive
            sorted_folder_meta = {
                "name": "sorted",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [body.drive_output_folder_id]
            }
            # Check if "sorted" folder already exists
            check_q = f"name = 'sorted' and mimeType = 'application/vnd.google-apps.folder' and '{body.drive_output_folder_id}' in parents and trashed = false"
            check_res = service.files().list(q=check_q, fields="files(id)").execute()
            
            if check_res.get("files"):
                sorted_folder_id = check_res["files"][0]["id"]
                logger.info("Reusing existing 'sorted' folder: %s", sorted_folder_id)
            else:
                sorted_folder_id = service.files().create(body=sorted_folder_meta, fields="id").execute().get("id")
                logger.info("Created new 'sorted' folder in Drive: %s", sorted_folder_id)

            # Upload subfolders and files
            for cat_folder in temp_output.iterdir():
                if not cat_folder.is_dir():
                    continue
                
                # Exclude internal temporary dirs if any
                if cat_folder.name in ("extracted_text", "rotated_pages"):
                    continue

                # Create category folder inside "sorted"
                cat_name = cat_folder.name
                cat_folder_meta = {
                    "name": cat_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [sorted_folder_id]
                }
                
                # Check if category subfolder already exists
                cat_q = f"name = '{cat_name}' and mimeType = 'application/vnd.google-apps.folder' and '{sorted_folder_id}' in parents and trashed = false"
                cat_res = service.files().list(q=cat_q, fields="files(id)").execute()
                if cat_res.get("files"):
                    cat_folder_id = cat_res["files"][0]["id"]
                else:
                    cat_folder_id = service.files().create(body=cat_folder_meta, fields="id").execute().get("id")

                # Upload each PDF in this category
                for item in cat_folder.iterdir():
                      if item.name.startswith("."): continue
                      
                      if item.is_dir():
                          bundle_name = item.name
                          bundle_meta = {
                              "name": bundle_name,
                              "mimeType": "application/vnd.google-apps.folder",
                              "parents": [cat_folder_id]
                          }
                          bundle_folder = service.files().create(body=bundle_meta, fields="id").execute()
                          bundle_id = bundle_folder.get("id")
                          
                          for f in item.iterdir():
                              if not f.is_file() or f.name.startswith("."): continue
                              logger.info("Uploading %s to Drive bundle %s", f.name, bundle_name)
                              f_meta = {"name": f.name, "parents": [bundle_id]}
                              media = MediaFileUpload(str(f), mimetype="application/json" if f.name.endswith(".json") else ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if f.name.endswith(".xlsx") else "application/pdf"))
                              service.files().create(body=f_meta, media_body=media, fields="id").execute()
                              uploaded_files.append({
                                  "file_name": f.name,
                                  "category": cat_name,
                                  "destination": f"sorted/{cat_name}/{bundle_name}/{f.name}"
                              })
                      elif item.is_file():
                          logger.info("Uploading %s to Drive category %s", item.name, cat_name)
                          file_metadata = {
                              "name": item.name,
                              "parents": [cat_folder_id]
                          }
                          media = MediaFileUpload(str(item), mimetype="application/json" if item.name.endswith(".json") else ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if item.name.endswith(".xlsx") else "application/pdf"))
                          service.files().create(
                              body=file_metadata,
                              media_body=media,
                              fields="id"
                          ).execute()
                          
                          uploaded_files.append({
                              "file_name": item.name,
                              "category": cat_name,
                              "destination": f"sorted/{cat_name}/{item.name}"
                          })

            # 7. Delete original files from Google Drive (if copy_mode is False)
            if not body.copy_mode:
                logger.info("Deleting original files from Google Drive input folder since copy_mode = False")
                for item in pdf_files:
                    file_id = item["id"]
                    try:
                        logger.info("Deleting original file ID: %s from Drive", file_id)
                        service.files().delete(fileId=file_id).execute()
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
        logger.error("Cloud classification pipeline failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

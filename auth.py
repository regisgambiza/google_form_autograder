from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError
from logger import log
import os.path
import threading

# ============================================
# UNIFIED SCOPES - Request all at once
# ============================================
ALL_SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me",
]

# Thread-safe credentials cache
_credentials_cache = None
_credentials_lock = threading.Lock()
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"


def _get_credentials():
    """Get credentials with ALL scopes at once. Thread-safe with automatic refresh handling."""
    global _credentials_cache
    
    with _credentials_lock:
        # Return cached credentials if valid
        if _credentials_cache and _credentials_cache.valid:
            return _credentials_cache
        
        creds = None

        # Load token if it exists
        if os.path.exists(TOKEN_FILE):
            log("DEBUG", f"Loading credentials from {TOKEN_FILE}")
            try:
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, ALL_SCOPES)
            except Exception as e:
                log("WARNING", f"Failed to load {TOKEN_FILE}: {e}. Will re-authenticate.")
                creds = None

        # If invalid or missing, refresh or re-auth
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                log("DEBUG", "Refreshing expired credentials...")
                try:
                    creds.refresh(Request())
                    log("DEBUG", "Token refreshed successfully")
                    
                    # Save refreshed token
                    with open(TOKEN_FILE, "w") as token_file:
                        token_file.write(creds.to_json())
                    log("DEBUG", f"Refreshed credentials saved to {TOKEN_FILE}")
                    
                except RefreshError as e:
                    log("ERROR", f"Token refresh failed: {e}. Re-authenticating...")
                    creds = None
                except Exception as e:
                    log("ERROR", f"Unexpected error during refresh: {e}. Re-authenticating...")
                    creds = None
            
            # If refresh failed or no credentials, re-authenticate
            if not creds:
                log("DEBUG", f"Initiating OAuth flow with scopes: {ALL_SCOPES}")
                try:
                    if not os.path.exists(CLIENT_SECRETS_FILE):
                        msg = (
                            f"Missing OAuth client file: '{CLIENT_SECRETS_FILE}'. "
                            "Create an OAuth Desktop client in Google Cloud Console, "
                            "download the JSON, and place it in the project root."
                        )
                        log("ERROR", msg)
                        raise FileNotFoundError(msg)
                    flow = InstalledAppFlow.from_client_secrets_file(
                        CLIENT_SECRETS_FILE,
                        ALL_SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                    with open(TOKEN_FILE, "w") as token_file:
                        token_file.write(creds.to_json())
                    log("DEBUG", f"New credentials saved to {TOKEN_FILE}.")
                except Exception as e:
                    log("ERROR", f"OAuth flow failed: {e}")
                    raise

        # Cache the credentials
        _credentials_cache = creds
        return creds


def has_saved_login() -> bool:
    return os.path.exists(TOKEN_FILE)


def clear_cached_credentials() -> None:
    global _credentials_cache
    with _credentials_lock:
        _credentials_cache = None


def sign_in():
    """Force credential creation/refresh and return authenticated credentials."""
    return _get_credentials()


def sign_out(remove_token: bool = True) -> bool:
    """Clear cached credentials and optionally remove the saved OAuth token."""
    clear_cached_credentials()
    removed = False
    if remove_token and os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
        removed = True
        log("INFO", f"Signed out and removed {TOKEN_FILE}")
    return removed


def get_service():
    """Google Forms API"""
    log("DEBUG", "Setting up Forms API credentials...")
    creds = _get_credentials()
    service = build("forms", "v1", credentials=creds)
    log("DEBUG", "Forms API client ready.")
    return service


def get_drive_service():
    """Google Drive API"""
    log("DEBUG", "Setting up Drive API credentials...")
    creds = _get_credentials()
    service = build("drive", "v3", credentials=creds)
    log("DEBUG", "Drive API client ready.")
    return service


def get_classroom_service():
    """Google Classroom API"""
    log("DEBUG", "Setting up Classroom API credentials...")
    creds = _get_credentials()
    service = build("classroom", "v1", credentials=creds)
    log("DEBUG", "Classroom API client ready.")
    return service

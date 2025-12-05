from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from logger import log
import os.path

def _load_credentials(scopes):
    """Internal helper to load or refresh credentials with given scopes."""
    creds = None

    # Load token if it exists
    if os.path.exists("token.json"):
        log("DEBUG", f"Loading credentials from token.json with scopes: {scopes}")
        creds = Credentials.from_authorized_user_file("token.json", scopes)

    # If invalid or missing, refresh or re-auth
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log("DEBUG", "Refreshing expired credentials...")
            creds.refresh(Request())
        else:
            log("DEBUG", f"Initiating OAuth flow with scopes: {scopes}")
            flow = InstalledAppFlow.from_client_secrets_file(
                "client_secrets.json",
                scopes
            )
            creds = flow.run_local_server(port=0)

            with open("token.json", "w") as token_file:
                token_file.write(creds.to_json())
            log("DEBUG", "New credentials saved to token.json.")

    return creds


def get_service():
    """Google Forms API"""
    log("DEBUG", "Setting up Forms API credentials...")

    SCOPES = [
        "https://www.googleapis.com/auth/forms.body",
        "https://www.googleapis.com/auth/forms.responses.readonly"
    ]

    creds = _load_credentials(SCOPES)
    service = build("forms", "v1", credentials=creds)
    log("DEBUG", "Forms API client ready.")
    return service


def get_drive_service():
    """Google Drive API"""
    log("DEBUG", "Setting up Drive API credentials...")

    SCOPES = [
        "https://www.googleapis.com/auth/drive.metadata.readonly",
    ]

    creds = _load_credentials(SCOPES)
    service = build("drive", "v3", credentials=creds)
    log("DEBUG", "Drive API client ready.")
    return service


def get_classroom_service():
    """Google Classroom API (courses + coursework READ)"""
    log("DEBUG", "Setting up Classroom API credentials...")

    SCOPES = [
        "https://www.googleapis.com/auth/classroom.courses.readonly",
        "https://www.googleapis.com/auth/classroom.coursework.me",   # ★ REQUIRED FIX ★
    ]

    creds = _load_credentials(SCOPES)
    service = build("classroom", "v1", credentials=creds)
    log("DEBUG", "Classroom API client ready.")
    return service

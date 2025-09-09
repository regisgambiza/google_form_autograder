from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from logger import log
import os.path

def get_service():
    log("DEBUG", "Setting up OAuth 2.0 credentials...")
    SCOPES = [
        "https://www.googleapis.com/auth/forms.body",
        "https://www.googleapis.com/auth/forms.responses.readonly"
    ]
    creds = None
    # Load existing token if available
    if os.path.exists("token.json"):
        log("DEBUG", "Loading credentials from token.json...")
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    
    # If no valid credentials, prompt user to log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log("DEBUG", "Refreshing expired credentials...")
            creds.refresh(Request())
        else:
            log("DEBUG", "Initiating OAuth flow for new credentials...")
            flow = InstalledAppFlow.from_client_secrets_file(
                "client_secrets.json",
                SCOPES
            )
            creds = flow.run_local_server(port=0)
            # Save credentials for reuse
            with open("token.json", "w") as token_file:
                token_file.write(creds.to_json())
            log("DEBUG", "Credentials saved to token.json.")
    
    service = build("forms", "v1", credentials=creds)
    log("DEBUG", "Authentication successful, Forms API client ready.")
    return service
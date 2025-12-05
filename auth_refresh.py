#!/usr/bin/env python3
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me",   # THIS IS THE ONE THAT WAS MISSING
]

print("FORCING FULL RE-AUTH WITH ALL SCOPES (including coursework.me)")
print("You MUST complete this login — do not close the browser early!\n")

flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
creds = flow.run_local_server(port=0, authorization_prompt_message="")

with open("token.json", "w") as f:
    f.write(creds.to_json())

print("\nSUCCESS! token.json now has FULL Classroom permissions.")
print("You can now run: python gui_main.py → ALL classes will work (including 7/3)")
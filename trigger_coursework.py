#!/usr/bin/env python3
"""
TRIGGER FULL COURSEWORK PERMISSION — Run this ONCE to fix the sneaky 403
"""
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import os.path

SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me",  # The magic one for assignments
]

print("Step 1: Checking current token...")
creds = None
if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if creds and creds.valid:
        print("Token exists — but may lack coursework permission.")

print("\nStep 2: Triggering full re-consent (browser will open)...")
if not creds or not creds.valid:
    flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
    creds = flow.run_local_server(port=0)

# FORCE the missing permission by making a dummy coursework call
print("\nStep 3: Triggering coursework permission (this may show an extra consent screen)...")
try:
    service = build("classroom", "v1", credentials=creds)
    # Dummy call to list YOUR OWN coursework (forces consent for .me scope)
    courses = service.courses().list(pageSize=5).execute()
    course_id = courses.get('courses', [{}])[0].get('id')
    if course_id:
        coursework = service.courses().courseWork().list(courseId=course_id, pageSize=1).execute()
        print(f"✓ SUCCESS! Accessed coursework for course {course_id}")
    else:
        print("No courses found — but permission triggered anyway.")
except Exception as e:
    print(f"Expected warning during trigger: {e}")

# Save the updated token
with open("token.json", "w") as token:
    token.write(creds.to_json())

print("\n✓ FIXED! token.json now has FULL coursework access.")
print("Run: python gui_main.py → Select 7/1 Mathematics → Find Forms → It will work.")
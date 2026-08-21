#!/usr/bin/env python3
"""Script to add questions from Chapter 9 Functions and formulae quiz to the Google Form."""

import json
import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/forms',
    'https://www.googleapis.com/auth/drive.file'
]

def main():
    # Authenticate
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
    creds = flow.run_local_server(port=0)
    
    # Build service
    service = build('forms', 'v1', credentials=creds)
    
    form_id = '15IUmFla4R6QqyVaOZHHh7tc56S_i14TN2CyUQc2oRtA'
    
    # Define questions from the document
    questions = [
        {
            "title": "1a) Here is a function machine. Complete the mapping diagram for this function machine.",
            "question_type": "text",
            "marks": 2
        },
        {
            "title": "1b) Find the input of the function when the output is 11.",
            "question_type": "text",
            "marks": 2
        },
        {
            "title": "1c) Find an expression for the output when the input is x.",
            "question_type": "text",
            "marks": 1
        },
        {
            "title": "2) Match each mapping diagram to the corresponding function.",
            "question_type": "text",
            "marks": 2
        },
        {
            "title": "3) Complete the function machine.",
            "question_type": "text",
            "marks": 1
        },
        {
            "title": "4) Make n the subject of the formula: y = 180(n - 2). n = ?",
            "question_type": "text",
            "marks": 2
        },
        {
            "title": "5) Tick whether each of the following formulae are correct or incorrect rearrangements.",
            "question_type": "checkbox",
            "options": ["Option 1", "Option 2", "Option 3"],
            "marks": 2
        },
        {
            "title": "6a) The entry charges for a museum are shown below. Write a formula for the total cost, C, for m adults and n children to visit the museum.",
            "question_type": "text",
            "marks": 2
        },
        {
            "title": "6b) The cost, C, for m adults and n children to visit an art gallery is given by C = 6m + n. Is it cheaper for 6 adults and 15 children to visit the museum or the art gallery?",
            "question_type": "text",
            "marks": 2
        }
    ]
    
    # Add questions to the form
    for i, q in enumerate(questions):
        request = {
            "createItem": {
                "item": {
                    "title": q["title"],
                    "questionItem": {
                        "question": {
                            "textQuestion": {}
                        }
                    }
                },
                "location": {
                    "index": 2 + i  # Start from index 2 (index 0, 1 already used)
                }
            }
        }
        
        response = service.forms().batchUpdate(formId=form_id, body={"requests": [request]}).execute()
        print(f"Added question: {q['title'][:50]}...")
    
    print("All questions added successfully!")

if __name__ == '__main__':
    main()
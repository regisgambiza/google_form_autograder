# response_utils.py - FINAL FIXED VERSION (no 'text' variable anywhere)
import json
import os
from datetime import datetime, timezone
from logger import log

# Track grading timestamps to know which submissions are "recent"
GRADING_TIMESTAMPS_FILE = ".grading_timestamps.json"

def get_last_grading_time(form_id):
    """Get the last time this form was graded."""
    try:
        if os.path.exists(GRADING_TIMESTAMPS_FILE):
            with open(GRADING_TIMESTAMPS_FILE, "r") as f:
                data = json.load(f)
                timestamp_str = data.get(form_id)
                if timestamp_str:
                    return datetime.fromisoformat(timestamp_str)
    except Exception as e:
        log("DEBUG", f"Could not load grading timestamp for {form_id}: {e}")
    return None

def save_grading_time(form_id, timestamp):
    """Save the time this form was graded."""
    try:
        data = {}
        if os.path.exists(GRADING_TIMESTAMPS_FILE):
            with open(GRADING_TIMESTAMPS_FILE, "r") as f:
                data = json.load(f)
        data[form_id] = timestamp.isoformat()
        with open(GRADING_TIMESTAMPS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log("WARNING", f"Could not save grading timestamp for {form_id}: {e}")

def get_responses(service, form_id, question_id, grade_recent_only=False):
    """
    Fetch all responses for a given form and extract answers for a specific question_id.
    If grade_recent_only is True, only return answers from submissions after the last grading.
    Returns a list of answer values (strings).
    """
    log("DEBUG", f"Fetching responses for form {form_id} and question {question_id}")
    
    last_grade_time = None
    if grade_recent_only:
        last_grade_time = get_last_grading_time(form_id)
        if last_grade_time:
            log("INFO", f"🔄 RECENT-ONLY MODE: Filtering to submissions after {last_grade_time.isoformat()}")
        else:
            log("INFO", "🔄 RECENT-ONLY MODE: No previous grading found, treating all submissions as recent")
    
    answers = []
    next_page_token = None
    total_responses = 0
    filtered_out = 0
    found_with_answers = 0

    try:
        # Handle pagination to get ALL responses
        while True:
            result = service.forms().responses().list(
                formId=form_id,
                pageToken=next_page_token
            ).execute()

            responses = result.get("responses", [])
            total_responses += len(responses)
            log("DEBUG", f"Fetched {len(responses)} responses (total so far: {total_responses})")

            for resp in responses:
                resp_id = resp.get("responseId", "unknown")
                ans_dict = resp.get("answers", {})
                
                # Check submission time if filtering by recent
                if grade_recent_only and last_grade_time:
                    submission_time_str = resp.get("submitTime")
                    if submission_time_str:
                        try:
                            submission_time = datetime.fromisoformat(submission_time_str.replace("Z", "+00:00"))
                            if submission_time <= last_grade_time:
                                filtered_out += 1
                                continue  # Skip old submissions
                        except Exception as e:
                            log("DEBUG", f"Could not parse submission time {submission_time_str}: {e}")

                if question_id not in ans_dict:
                    continue  # This student didn't answer this question

                q_ans = ans_dict[question_id]

                # Text answers (short/long answer)
                if "textAnswers" in q_ans:
                    for ans in q_ans["textAnswers"].get("answers", []):
                        value = ans.get("value")
                        if value is not None:
                            answers.append(value.strip())
                            found_with_answers += 1

                # Choice answers (multiple choice, checkbox, dropdown)
                if "choiceAnswers" in q_ans:
                    for ans in q_ans["choiceAnswers"].get("answers", []):
                        value = ans.get("value")
                        if value is not None:
                            answers.append(value.strip())
                            found_with_answers += 1

            next_page_token = result.get("nextPageToken")
            if not next_page_token:
                break

        # Log summary with filtering info
        if grade_recent_only and last_grade_time:
            log("INFO", f"✅ FILTERING SUMMARY: Total submissions: {total_responses} | Filtered out (old): {filtered_out} | Recent answers: {found_with_answers} for QID {question_id}")
        else:
            log("INFO", f"Finished: {total_responses} total responses → {found_with_answers} answers for QID {question_id}")
        return answers

    except Exception as e:
        # THIS IS THE ONLY EXCEPT BLOCK — SAFE AND CLEAN
        log("ERROR", f"Failed to fetch responses for form {form_id}, question {question_id}: {str(e)}")
        return []
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
                    dt = datetime.fromisoformat(timestamp_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
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
    If grade_recent_only is True, only return answers from the latest new submission batch.
    Returns a list of answer values (strings).
    """
    log("DEBUG", f"Fetching responses for form {form_id} and question {question_id}")

    last_grade_time = None
    if grade_recent_only:
        last_grade_time = get_last_grading_time(form_id)
        if last_grade_time:
            log("INFO", f"RECENT-ONLY MODE: Filtering to submissions after {last_grade_time.isoformat()}")
        else:
            log("INFO", "RECENT-ONLY MODE: No previous grading found, using latest submission only")

    answers = []
    next_page_token = None
    total_responses = 0
    filtered_out = 0
    found_with_answers = 0
    recent_candidates = []  # list[tuple[response, submission_time]]

    try:
        while True:
            result = service.forms().responses().list(
                formId=form_id,
                pageToken=next_page_token
            ).execute()

            responses = result.get("responses", [])
            total_responses += len(responses)

            for resp in responses:
                ans_dict = resp.get("answers", {})

                if grade_recent_only:
                    submission_time_str = (
                        resp.get("submitTime")
                        or resp.get("lastSubmittedTime")
                        or resp.get("createTime")
                    )
                    if not submission_time_str:
                        filtered_out += 1
                        continue
                    try:
                        submission_time = datetime.fromisoformat(submission_time_str.replace("Z", "+00:00"))
                    except Exception:
                        filtered_out += 1
                        continue

                    if last_grade_time and submission_time <= last_grade_time:
                        filtered_out += 1
                        continue

                    recent_candidates.append((resp, submission_time))
                    continue

                if question_id not in ans_dict:
                    continue

                q_ans = ans_dict[question_id]
                if "textAnswers" in q_ans:
                    for ans in q_ans["textAnswers"].get("answers", []):
                        value = ans.get("value")
                        if value is not None:
                            answers.append(value.strip())
                            found_with_answers += 1
                if "choiceAnswers" in q_ans:
                    for ans in q_ans["choiceAnswers"].get("answers", []):
                        value = ans.get("value")
                        if value is not None:
                            answers.append(value.strip())
                            found_with_answers += 1

            next_page_token = result.get("nextPageToken")
            if not next_page_token:
                break

        if grade_recent_only and recent_candidates:
            latest_time = max(ts for _, ts in recent_candidates)
            latest_responses = [r for r, ts in recent_candidates if ts == latest_time]

            for resp in latest_responses:
                ans_dict = resp.get("answers", {})
                if question_id not in ans_dict:
                    continue
                q_ans = ans_dict[question_id]
                if "textAnswers" in q_ans:
                    for ans in q_ans["textAnswers"].get("answers", []):
                        value = ans.get("value")
                        if value is not None:
                            answers.append(value.strip())
                            found_with_answers += 1
                if "choiceAnswers" in q_ans:
                    for ans in q_ans["choiceAnswers"].get("answers", []):
                        value = ans.get("value")
                        if value is not None:
                            answers.append(value.strip())
                            found_with_answers += 1

            log("INFO", f"RECENT-ONLY STRICT: latest submit time {latest_time.isoformat()} | responses in batch: {len(latest_responses)}")

        if grade_recent_only:
            log("INFO", f"FILTERING SUMMARY: Total submissions: {total_responses} | Filtered out: {filtered_out} | Returned answers: {found_with_answers} for QID {question_id}")
        else:
            log("INFO", f"Finished: {total_responses} total responses -> {found_with_answers} answers for QID {question_id}")

        return answers


    except Exception as e:
        # THIS IS THE ONLY EXCEPT BLOCK — SAFE AND CLEAN
        log("ERROR", f"Failed to fetch responses for form {form_id}, question {question_id}: {str(e)}")
        return []

# response_utils.py - FINAL FIXED VERSION (no 'text' variable anywhere)
from logger import log

def get_responses(service, form_id, question_id):
    """
    Fetch all responses for a given form and extract answers for a specific question_id.
    Returns a list of answer values (strings).
    """
    log("DEBUG", f"Fetching responses for form {form_id} and question {question_id}")
    answers = []
    next_page_token = None
    total_responses = 0
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

        log("INFO", f"Finished: {total_responses} total responses → {found_with_answers} answers for QID {question_id}")
        return answers

    except Exception as e:
        # THIS IS THE ONLY EXCEPT BLOCK — SAFE AND CLEAN
        log("ERROR", f"Failed to fetch responses for form {form_id}, question {question_id}: {str(e)}")
        return []
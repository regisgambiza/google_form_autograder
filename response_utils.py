from logger import log
import json

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
        # Handle pagination to ensure all responses are fetched
        while True:
            # Fetch responses with pagination
            result = service.forms().responses().list(
                formId=form_id,
                pageToken=next_page_token
            ).execute()
            
            responses = result.get("responses", [])
            total_responses += len(responses)
            log("DEBUG", f"Fetched {len(responses)} responses. Total so far: {total_responses}")
            
            for resp in responses:
                resp_id = resp.get("responseId", "unknown")
                ans_dict = resp.get("answers", {})
                
                if not ans_dict:
                    log("DEBUG", f"Response {resp_id}: No answers present.")
                    continue
                
                if question_id not in ans_dict:
                    log("DEBUG", f"Response {resp_id}: Question ID {question_id} not found. Present IDs: {list(ans_dict.keys())}")
                    continue
                
                # Extract answers for the question
                q_ans = ans_dict[question_id]
                
                # Handle text answers
                if "textAnswers" in q_ans:
                    text_answers = q_ans.get("textAnswers", {}).get("answers", [])
                    for ans in text_answers:
                        value = ans.get("value")
                        if value is not None:
                            answers.append(value)
                            log("DEBUG", f"Response {resp_id}: Found text answer '{value}' for QID {question_id}")
                            found_with_answers += 1
                
                # Handle choice answers (multiple choice)
                if "choiceAnswers" in q_ans:
                    choice_answers = q_ans.get("choiceAnswers", {}).get("answers", [])
                    for ans in choice_answers:
                        value = ans.get("value")
                        if value is not None:
                            answers.append(value)
                            log("DEBUG", f"Response {resp_id}: Found choice answer '{value}' for QID {question_id}")
                            found_with_answers += 1
            
            next_page_token = result.get("nextPageToken")
            if not next_page_token:
                break  # Exit loop when no more pages

        log("INFO", f"Processed {total_responses} responses. Found {found_with_answers} answers for QID {question_id}.")
        return answers

    except Exception as e:
        log("ERROR", f"Error fetching or processing responses: {e}")
        return []
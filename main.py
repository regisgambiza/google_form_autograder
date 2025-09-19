import json
import sys
from form_utils import get_form_structure
from response_utils import get_responses
from ai_evaluator import evaluate_answers
from updater import update_correct_answers
from auth import get_service
from logger import log
from feedback import generate_form_feedback
# Import new AI evaluator functions
from ai_evaluator_2 import generate_ai_feedback_report, prepend_ai_report_to_feedback

def extract_form_id(form_url):
    """Extract form ID from a Google Form URL."""
    try:
        if "/d/" in form_url:
            form_id = form_url.split("/d/")[1].split("/")[0]
        elif "/d/e/" in form_url:
            form_id = form_url.split("/d/e/")[1].split("/")[0]
        else:
            raise ValueError("URL does not contain '/d/' or '/d/e/'")
        if not form_url.endswith("/viewform"):
            log("WARNING", f"Form URL {form_url} does not end with '/viewform'. Using form ID {form_id}.")
        return form_id
    except IndexError:
        raise ValueError("Invalid URL format: could not extract form ID")
    except Exception as e:
        raise ValueError(f"Error extracting form ID: {str(e)}")

def main():
    log("INFO", "Starting script execution...")
    try:
        with open("forms_to_grade.json") as f:
            forms_data = json.load(f)
        form_urls = list(set(forms_data.get("forms", [])))  # Deduplicate URLs
        if not form_urls:
            log("ERROR", "No forms found in forms_to_grade.json. Exiting.")
            sys.exit(1)
    except FileNotFoundError:
        log("ERROR", "forms_to_grade.json not found in project directory. Exiting.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log("ERROR", f"Failed to parse forms_to_grade.json: {e}. Exiting.")
        sys.exit(1)

    service = get_service()

    for form_url in form_urls:
        try:
            form_id = extract_form_id(form_url)
            log("INFO", f"Processing form ID: {form_id} from URL: {form_url}")


            form_structure = get_form_structure(service, form_id)
            if not form_structure:
                log("ERROR", f"No questions found in form {form_id}. Skipping.")
                continue

            log("INFO", f"Questions found in form {form_id}:")
            for q in form_structure:
                log("DEBUG", f"Q{q['index']} type = {q['type']}, questionId = {q['questionId']}")

            # Separate text and non-text questions
            text_types = {"SHORT_ANSWER", "LONG_ANSWER"}
            text_questions = []
            other_questions = []
            for q in form_structure:
                responses = get_responses(service, form_id, q["questionId"])
                correct_answers = evaluate_answers(q, responses) if q["type"] in text_types else []
                q_data = {
                    "question": q,
                    "responses": responses,
                    "correct_answers": correct_answers
                }
                if q["type"] in text_types:
                    text_questions.append(q_data)
                else:
                    other_questions.append(q_data)

            # Fetch form title for filename
            form_title = None
            try:
                form_data = service.forms().get(formId=form_id).execute()
                form_title = form_data.get("info", {}).get("title", f"feedback_form_{form_id}")
            except Exception as e:
                log("WARNING", f"Could not fetch form title for {form_id}: {e}")
                form_title = f"feedback_form_{form_id}"

            # Generate feedback report for text questions
            report_path = generate_form_feedback(form_id, form_title, text_questions)
            if report_path:
                log("INFO", f"Feedback report for text questions generated at {report_path}")
            else:
                log("ERROR", f"Failed to generate feedback report for text questions in form {form_id}")

            # Debug: Show number and details of non-text questions
            log("DEBUG", f"Found {len(other_questions)} non-text questions for form '{form_title}' (form_id={form_id})")
            for idx, qd in enumerate(other_questions):
                q = qd['question']
                log("DEBUG", f"Non-text Q{idx+1}: index={q.get('index')}, title='{q.get('title')}', type={q.get('type')}, responses={len(qd.get('responses', []))}")

            # Generate AI feedback report for other question types and prepend to main report
            if other_questions and report_path:
                ai_report_path = generate_ai_feedback_report(form_id, form_title, other_questions)
                if ai_report_path:
                    success = prepend_ai_report_to_feedback(report_path, ai_report_path)
                    if success:
                        log("INFO", f"Prepended AI feedback for other question types to {report_path}")
                        # Remove the separate AI report file after prepending
                        import os
                        try:
                            os.remove(ai_report_path)
                            log("DEBUG", f"Deleted temporary AI report file: {ai_report_path}")
                        except Exception as e:
                            log("WARNING", f"Could not delete AI report file {ai_report_path}: {e}")
                    else:
                        log("ERROR", f"Failed to prepend AI feedback for other question types to {report_path}")
                else:
                    log("ERROR", f"Failed to generate AI feedback report for other question types in form {form_id}")

            # Update correct answers for text questions
            form_duplicates = []
            for q_data in text_questions:
                q = q_data["question"]
                correct_answers = q_data["correct_answers"]
                if correct_answers:
                    duplicates = update_correct_answers(service, form_id, q["itemId"], correct_answers, q["index"])
                    if duplicates:
                        form_duplicates.extend(duplicates)
                else:
                    log("INFO", f"No correct answers returned for Q{q['index']} "
                                f"(QID {q['questionId']}), skipping update_correct_answers.")

            # Print the whole list of duplicates for this form
            log("INFO", f"Finished processing form {form_id} successfully.")
            print(f"\n=== Duplicate answers across form {form_id}: {form_duplicates} ===\n")

        except ValueError as e:
            log("ERROR", f"Error processing form {form_url}: {str(e)}. Skipping to next form.")
            continue
        except Exception as e:
            log("ERROR", f"Unexpected error processing form {form_url}: {str(e)}. Skipping to next form.")
            continue

    log("INFO", "All forms processed successfully. Script execution complete.")
    sys.exit(0)

if __name__ == "__main__":
    log("INFO", "Script invoked as main program.")
    main()
import json
import sys
from form_utils import get_form_structure
from response_utils import get_responses
from ai_evaluator import evaluate_answers
from updater import update_correct_answers
from auth import get_service
from logger import log
from feedback import generate_form_feedback

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

            # Fetch form title and grading settings
            form_title = None
            form_data = None
            try:
                form_data = service.forms().get(formId=form_id).execute()
                form_title = form_data.get("info", {}).get("title", f"feedback_form_{form_id}")
            except Exception as e:
                log("WARNING", f"Could not fetch form title for {form_id}: {e}")
                form_title = f"feedback_form_{form_id}"

            # Prepare question data with correct answers for all question types
            text_types = {"SHORT_ANSWER", "LONG_ANSWER"}
            all_questions = []
            for q in form_structure:
                responses = get_responses(service, form_id, q["questionId"])
                correct_answers = []
                if q["type"] in text_types:
                    correct_answers = evaluate_answers(q, responses)
                else:
                    # Fetch correct answers for non-text questions from grading settings
                    try:
                        for item in form_data.get("items", []):
                            if item.get("itemId") == q["itemId"] and "questionItem" in item:
                                question = item["questionItem"].get("question", {})
                                if "grading" in question and "correctAnswers" in question["grading"]:
                                    correct_answers = [ans["value"] for ans in question["grading"]["correctAnswers"].get("answers", [])]
                                break
                    except Exception as e:
                        log("WARNING", f"Could not fetch correct answers for Q{q['index']}: {e}")
                q_data = {
                    "question": q,
                    "responses": responses,
                    "correct_answers": correct_answers
                }
                all_questions.append(q_data)

            # Sort questions by index
            all_questions.sort(key=lambda x: x["question"]["index"])
            log("DEBUG", f"Sorted questions: {[q['question']['index'] for q in all_questions]}")

            # Generate feedback report for all questions
            report_path = generate_form_feedback(form_id, form_title, all_questions)
            if report_path:
                log("INFO", f"Feedback report for all questions generated at {report_path}")
            else:
                log("ERROR", f"Failed to generate feedback report for form {form_id}")

            # Update correct answers for text questions
            form_duplicates = []
            for q_data in all_questions:
                q = q_data["question"]
                correct_answers = q_data["correct_answers"]
                if correct_answers and q["type"] in text_types:
                    duplicates = update_correct_answers(service, form_id, q["itemId"], correct_answers, q["index"])
                    if duplicates:
                        form_duplicates.extend(duplicates)
                else:
                    log("INFO", f"No correct answers returned for Q{q['index']} "
                                f"(QID {q['questionId']}), skipping update_correct_answers.")

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
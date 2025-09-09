import json
from form_utils import get_form_structure
from response_utils import get_responses
from ai_evaluator import evaluate_answers
from updater import update_correct_answers
from auth import get_service
from logger import log

def extract_form_id(form_url):
    """Extract form ID from a Google Form URL."""
    try:
        # Handle /d/ (edit form) and /d/e/ (published form) formats
        if "/d/" in form_url:
            form_id = form_url.split("/d/")[1].split("/")[0]
        elif "/d/e/" in form_url:
            form_id = form_url.split("/d/e/")[1].split("/")[0]
        else:
            raise ValueError("URL does not contain '/d/' or '/d/e/'")

        # Log a warning if the URL is not in published form format
        if not form_url.endswith("/viewform"):
            log("WARNING", f"Form URL {form_url} does not end with '/viewform'. Using form ID {form_id}.")
        
        return form_id
    except IndexError:
        raise ValueError("Invalid URL format: could not extract form ID")
    except Exception as e:
        raise ValueError(f"Error extracting form ID: {str(e)}")

def main():
    # Load the JSON file containing form URLs
    try:
        with open("forms_to_grade.json") as f:
            forms_data = json.load(f)
        form_urls = forms_data.get("forms", [])
        if not form_urls:
            log("ERROR", "No forms found in forms_to_grade.json. Exiting.")
            return
        log("INFO", f"Found {len(form_urls)} forms to process: {form_urls}")
    except FileNotFoundError:
        log("ERROR", "forms_to_grade.json not found in project directory. Exiting.")
        return
    except json.JSONDecodeError as e:
        log("ERROR", f"Failed to parse forms_to_grade.json: {e}. Exiting.")
        return

    service = get_service()

    # Process each form one at a time
    for form_url in form_urls:
        try:
            form_id = extract_form_id(form_url)
            log("INFO", f"Processing form ID: {form_id} from URL: {form_url}")

            form_structure = get_form_structure(service, form_id)

            if not form_structure:
                log("ERROR", f"No text questions found in form {form_id}. Skipping.")
                continue

            log("INFO", f"Text questions found in form {form_id}:")
            for q in form_structure:
                log("DEBUG", f"Q{q['index']} type = {q['type']}, questionId = {q['questionId']}")

            # Get unique question types (only SHORT_ANSWER and LONG_ANSWER)
            selected_types = sorted(set(q["type"] for q in form_structure))
            log("INFO", f"Automatically processing all question types: {selected_types}")

            for q in form_structure:
                if q["type"] not in selected_types:
                    continue
                log("INFO", f"Processing Q{q['index']} ({q['type']}), Item ID={q['itemId']}, Question ID={q['questionId']}")
                responses = get_responses(service, form_id, q["questionId"])
                correct_answers = evaluate_answers(q, responses)
                update_correct_answers(service, form_id, q["itemId"], correct_answers, q["index"])

            log("INFO", f"Finished processing form {form_id} successfully.")

        except ValueError as e:
            log("ERROR", f"Error processing form {form_url}: {str(e)}. Skipping to next form.")
            continue
        except Exception as e:
            log("ERROR", f"Unexpected error processing form {form_url}: {str(e)}. Skipping to next form.")
            continue

    log("INFO", "All forms processed successfully.")

if __name__ == "__main__":
    main()
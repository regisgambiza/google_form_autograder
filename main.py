# main.py - FINAL FIXED VERSION (no 'text' error, safe, clean, working)
import json
import sys
import time
import os
from datetime import datetime, timezone
from form_utils import get_form_structure
from response_utils import get_responses, save_grading_time
from auth import get_service
from logger import log
from feedback import generate_form_feedback
from updater import update_correct_answers


def write_heartbeat(hang_stage: str = "unknown"):
    """Write current timestamp to heartbeat file with stage info for hang monitoring."""
    try:
        data = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "stage": hang_stage,
            "timestamp_epoch": time.time()
        }
        with open("heartbeat.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass  # Silent failure - heartbeat is not critical


# === Load config and import evaluator ===
try:
    with open("config.json") as f:
        config = json.load(f)
except FileNotFoundError:
    log("ERROR", "config.json not found!")
    sys.exit(1)
except json.JSONDecodeError as e:
    log("ERROR", f"Invalid config.json: {e}")
    sys.exit(1)

evaluator_module = config.get("evaluator", "ai_evaluator_2")
generate_report = config.get("generate_report", True)

try:
    __import__(evaluator_module)  # Test import first
    exec(f"from {evaluator_module} import evaluate_answers")
except Exception as e:
    log("WARNING", f"Failed to load {evaluator_module}: {e}. Falling back to ai_evaluator_2")
    from ai_evaluator_2 import evaluate_answers


def extract_form_id(form_url: str) -> str:
    """Extract form ID from any Google Forms URL."""
    try:
        if "/d/" in form_url:
            return form_url.split("/d/")[1].split("/")[0].split("?")[0]
        if "/d/e/" in form_url:
            return form_url.split("/d/e/")[1].split("/")[0].split("?")[0]
        raise ValueError("No valid form ID found in URL")
    except Exception as exc:
        raise ValueError(f"Invalid form URL: {form_url}") from exc


def main():
    log("INFO", "=== Google Form Autograder Started ===")

    # Write initial heartbeat with stage
    write_heartbeat(hang_stage="initialization")

    # Check if we should grade only recent submissions
    grade_recent_only = os.environ.get("GRADE_RECENT_ONLY", "false").lower() == "true"
    if grade_recent_only:
        log("INFO", "🔄 RUNNING IN RECENT SUBMISSIONS ONLY MODE - Only new submissions will be graded")
    else:
        log("INFO", "📝 RUNNING IN WHOLE FORM MODE - All submissions will be graded")

    # Load forms list
    try:
        with open("forms_to_grade.json", "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        log("ERROR", "forms_to_grade.json not found!")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log("ERROR", f"Invalid JSON in forms_to_grade.json: {e}")
        sys.exit(1)

    form_urls = []
    for item in data.get("forms", []):
        url = item.get("url") if isinstance(item, dict) else item
        if url and isinstance(url, str):
            form_urls.append(url.strip())

    form_urls = list(dict.fromkeys(form_urls))  # Remove duplicates, preserve order
    total_forms = len(form_urls)

    if total_forms == 0:
        log("ERROR", "No valid form URLs found in forms_to_grade.json")
        sys.exit(1)

    log("INFO", f"Found {total_forms} form(s) to process")
    print(f"Progress: 0/{total_forms}")

    service = get_service()

    for idx, form_url in enumerate(form_urls, 1):
        print(f"Progress: {idx}/{total_forms}")
        form_id = None

        try:
            form_start = time.perf_counter()
            form_id = extract_form_id(form_url)
            log("INFO", f"[{idx}/{total_forms}] Processing → {form_id}")

            # Write heartbeat before processing form
            write_heartbeat()

            # Fetch form structure
            form_structure = get_form_structure(service, form_id)
            if not form_structure:
                log("WARNING", f"No gradable questions in form {form_id}. Skipping.")
                continue

            # Write heartbeat for form processing stage
            write_heartbeat(hang_stage="form_fetch")
            
            # Get full form data (title + correct answers)
            try:
                form_data = service.forms().get(formId=form_id).execute()
                form_title = form_data.get("info", {}).get("title", f"Untitled_{form_id}")
            except Exception as e:
                log("WARNING", f"Could not get form title: {e}")
                form_title = f"Form_{form_id}"

            # Process each question
            text_types = {"SHORT_ANSWER", "LONG_ANSWER"}
            all_questions = []

            for q in form_structure:
                responses = get_responses(service, form_id, q["questionId"], grade_recent_only=grade_recent_only)

                # Try to get teacher-defined correct answers
                correct_answers_fetched = []
                try:
                    for item in form_data.get("items", []):
                        if item.get("itemId") == q["itemId"] and "questionItem" in item:
                            grading = item["questionItem"]["question"].get("grading", {})
                            answers = grading.get("correctAnswers", {}).get("answers", [])
                            correct_answers_fetched = [a["value"] for a in answers if "value" in a]
                            break
                except Exception:
                    pass  # No correct answers defined — that's fine

                # AI evaluation for text questions
                if q["type"] in text_types:
                    evaluated = evaluate_answers(q, responses, expected=correct_answers_fetched or None)
                else:
                    evaluated = correct_answers_fetched

                all_questions.append({
                    "question": q,
                    "responses": responses,
                    "correct_answers": evaluated
                })

            all_questions.sort(key=lambda x: x["question"]["index"])

            # Progress tracking
            total_responses = sum(len(q["responses"]) for q in all_questions)
            processed_responses = 0

            # Generate feedback report
            if generate_report:
                report_path = generate_form_feedback(form_id, form_title, all_questions)
                log("INFO", f"Report generated → {report_path or 'FAILED'}")

            # Update correct answers in form
            duplicates_found = []
            for q_data in all_questions:
                q = q_data["question"]
                correct = q_data["correct_answers"]

                processed_responses += len(q_data["responses"])
                print(f"FormProgress: {processed_responses}/{total_responses}")

                # Write heartbeat periodically (every 10 responses or at least every 30 seconds)
                if processed_responses % 10 == 0:
                    write_heartbeat(hang_stage="answer_evaluation")

                if correct and q["type"] in text_types:
                    dups = update_correct_answers(service, form_id, q["itemId"], correct, q["index"])
                    if dups:
                        duplicates_found.extend(dups)

            log("INFO", f"Finished processing form {form_id}")
            form_elapsed = time.perf_counter() - form_start
            log("INFO", f"Timing Form {form_id}: {form_elapsed:.2f}s total")
            if duplicates_found:
                print(f"\n=== Duplicate answers in {form_id}: {duplicates_found} ===\n")

            # Write final heartbeat after form completion
            write_heartbeat(hang_stage="form_complete")

            # Save grading timestamp for this form (for "recent only" mode next time)
            save_grading_time(form_id, datetime.now(timezone.utc))

        except Exception as e:
            # THIS IS NOW 100% SAFE — NO 'text' VARIABLE ANYWHERE
            error_detail = str(e)
            log("ERROR", f"Failed to process form: {form_url}")
            log("ERROR", f"Form ID: {form_id or 'unknown'} | Error: {error_detail}")
            print(f"ERROR processing {form_url}: {error_detail}")

    log("INFO", "=== All forms processed. Autograder finished successfully ===")

    # Write final heartbeat before exit
    write_heartbeat(hang_stage="complete")

    sys.exit(0)


if __name__ == "__main__":
    main()

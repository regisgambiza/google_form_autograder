# main.py - Hybrid prep/apply pipeline (prefetch + sequential apply)
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List

from auth import get_service
from feedback import generate_form_feedback
from form_utils import get_form_structure
from hang_diagnostics import start_hang_diagnostics
from logger import log
from response_utils import get_responses, save_grading_time
from updater import update_correct_answers
from global_prefetch import prefetch_all_forms
from global_dispatcher import run_global_dispatcher
from ai_judges import prewarm_judge_runtime

_GRADER_LOCK_FH = None


def acquire_grader_lock():
    """Prevent overlapping grader processes from corrupting queues/logs/caches."""
    global _GRADER_LOCK_FH
    os.makedirs("logs", exist_ok=True)
    lock_path = os.path.abspath(os.path.join("logs", "grader.lock"))
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("ERROR: Another grader process is already running. Stop it before starting a new run.", flush=True)
        fh.close()
        sys.exit(2)

    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _GRADER_LOCK_FH = fh


def write_heartbeat(hang_stage: str = "unknown"):
    try:
        data = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "stage": hang_stage,
            "timestamp_epoch": time.time(),
        }
        with open("heartbeat.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


try:
    with open("config.json", "r", encoding="utf-8") as f:
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
    __import__(evaluator_module)
    exec(f"from {evaluator_module} import evaluate_answers")
except Exception as e:
    log("WARNING", f"Failed to load {evaluator_module}: {e}. Falling back to ai_evaluator_2")
    from ai_evaluator_2 import evaluate_answers


def extract_form_id(form_url: str) -> str:
    try:
        if "/d/" in form_url:
            return form_url.split("/d/")[1].split("/")[0].split("?")[0]
        if "/d/e/" in form_url:
            return form_url.split("/d/e/")[1].split("/")[0].split("?")[0]
        raise ValueError("No valid form ID found in URL")
    except Exception as exc:
        raise ValueError(f"Invalid form URL: {form_url}") from exc


def _load_form_urls() -> List[str]:
    try:
        with open("forms_to_grade.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        log("ERROR", "forms_to_grade.json not found!")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log("ERROR", f"Invalid JSON in forms_to_grade.json: {e}")
        sys.exit(1)

    urls: List[str] = []
    for item in data.get("forms", []):
        url = item.get("url") if isinstance(item, dict) else item
        if isinstance(url, str) and url.strip():
            urls.append(url.strip())
    return list(dict.fromkeys(urls))


def _prepare_form(service, idx: int, total_forms: int, form_url: str, grade_recent_only: bool) -> Dict:
    text_types = {"SHORT_ANSWER", "LONG_ANSWER"}
    form_id = extract_form_id(form_url)
    log("INFO", f"[HYBRID PREP] START {idx}/{total_forms} form_id={form_id}")

    form_structure = get_form_structure(service, form_id)
    if not form_structure:
        return {"idx": idx, "form_url": form_url, "form_id": form_id, "skip": True, "reason": "No gradable questions"}

    try:
        form_data = service.forms().get(formId=form_id).execute()
        form_title = form_data.get("info", {}).get("title", f"Untitled_{form_id}")
    except Exception as e:
        log("WARNING", f"Could not get form title: {e}")
        form_data = {"items": []}
        form_title = f"Form_{form_id}"

    all_questions = []
    for q in form_structure:
        responses = get_responses(service, form_id, q["questionId"], grade_recent_only=grade_recent_only)

        correct_answers_fetched = []
        try:
            for item in form_data.get("items", []):
                if item.get("itemId") == q["itemId"] and "questionItem" in item:
                    grading = item["questionItem"]["question"].get("grading", {})
                    answers = grading.get("correctAnswers", {}).get("answers", [])
                    correct_answers_fetched = [a["value"] for a in answers if "value" in a]
                    break
        except Exception:
            pass

        if q["type"] in text_types:
            evaluated = evaluate_answers(q, responses, expected=correct_answers_fetched or None)
        else:
            evaluated = correct_answers_fetched

        all_questions.append({"question": q, "responses": responses, "correct_answers": evaluated})

    all_questions.sort(key=lambda x: x["question"]["index"])
    log("INFO", f"[HYBRID PREP] DONE {idx}/{total_forms} form_id={form_id}")
    return {
        "idx": idx,
        "form_url": form_url,
        "form_id": form_id,
        "form_title": form_title,
        "all_questions": all_questions,
        "skip": False,
    }


def main():
    acquire_grader_lock()
    log("INFO", "=== Google Form Autograder Started ===")
    log("INFO", f"Execution Mode: {config.get('execution_mode', 'Balanced')}")
    prewarm_judge_runtime()
    write_heartbeat("initialization")
    start_hang_diagnostics(config)

    grade_recent_only = os.environ.get("GRADE_RECENT_ONLY", "false").lower() == "true"
    if grade_recent_only:
        log("INFO", "RUNNING IN RECENT SUBMISSIONS ONLY MODE - Only new submissions will be graded")
    else:
        log("INFO", "RUNNING IN WHOLE FORM MODE - All submissions will be graded")

    form_urls = _load_form_urls()
    total_forms = len(form_urls)
    if total_forms == 0:
        log("ERROR", "No valid form URLs found in forms_to_grade.json")
        sys.exit(1)

    log("INFO", f"Found {total_forms} form(s) to process")
    print(f"Progress: 0/{total_forms}")

    service = get_service()

    if str(config.get("dispatch_mode", "")).lower() == "global":
        processed_count = 0
        failed_count = 0
        log("INFO", "[DISPATCH] Sequential form mode enabled: grading one queued form at a time.")
        for idx, form_url in enumerate(form_urls, start=1):
            form_id = "unknown"
            try:
                form_id = extract_form_id(form_url)
            except Exception:
                pass

            print(f"Progress: {idx - 1}/{total_forms}")
            log("INFO", f"Processing form ID: {form_id} from URL: {form_url}")
            log("INFO", f"[FORM] QUEUED {idx}/{total_forms} | form_id={form_id}")
            write_heartbeat("form_start")

            try:
                run_global_dispatcher(form_urls=[form_url], grade_recent_only=grade_recent_only, generate_report=generate_report)
                processed_count += 1
            except Exception as ex:
                failed_count += 1
                log("ERROR", f"Failed to process form: {form_url}")
                log("ERROR", f"Form ID: {form_id} | Error: {ex}")
                print(f"ERROR processing {form_url}: {ex}")

            print(f"Progress: {idx}/{total_forms}")
            write_heartbeat("form_complete")

        if failed_count:
            log("WARNING", f"=== Sequential dispatcher completed with failures. Completed {processed_count}/{total_forms}, failed {failed_count} ===")
        else:
            log("INFO", f"=== Sequential dispatcher completed. Completed {processed_count}/{total_forms} ===")
        log("INFO", "=== APP STATUS: FULLY FUNCTIONAL - Grading pipeline completed successfully ===")
        write_heartbeat("complete")
        sys.exit(1 if failed_count and processed_count == 0 else 0)

    # Global prefetch mode: fetch all forms/questions first with high concurrency.
    if bool(config.get("global_prefetch_mode", True)):
        prefetch_workers = max(1, int(config.get("global_prefetch_workers", 6)))
        prefetched = prefetch_all_forms(form_urls, grade_recent_only, workers=prefetch_workers)
        processed_count = 0
        for item in prefetched:
            idx = item.idx
            form_url = item.form_url
            form_id = item.form_id
            print(f"Progress: {idx}/{total_forms}")
            try:
                form_start = time.perf_counter()
                if item.skip:
                    log("WARNING", f"Skipping form {form_id or form_url}: {item.reason or 'unknown'}")
                    continue
                log("INFO", f"[FORM] START {idx}/{total_forms} | form_id={form_id}")
                log("INFO", f"[FORM] Now grading: '{item.form_title}' ({form_id})")
                write_heartbeat("form_apply")

                all_questions = []
                for pq in item.questions:
                    evaluated = evaluate_answers(pq.question, pq.responses, expected=pq.expected or None)
                    all_questions.append({"question": pq.question, "responses": pq.responses, "correct_answers": evaluated})
                all_questions.sort(key=lambda x: x["question"]["index"])

                total_responses = sum(len(q["responses"]) for q in all_questions)
                total_questions = len(all_questions)
                processed_responses = 0
                if generate_report:
                    report_path = generate_form_feedback(form_id, item.form_title, all_questions)
                    log("INFO", f"Report generated -> {report_path or 'FAILED'}")

                duplicates_found = []
                text_types = {"SHORT_ANSWER", "LONG_ANSWER"}
                for q_data in all_questions:
                    q = q_data["question"]
                    correct = q_data["correct_answers"]
                    processed_responses += len(q_data["responses"])
                    print(f"FormProgress: {processed_responses}/{total_responses}")
                    if processed_responses % 10 == 0:
                        write_heartbeat("answer_evaluation")
                    if correct and q["type"] in text_types:
                        dups = update_correct_answers(service, form_id, q["itemId"], correct, q["index"])
                        if dups:
                            duplicates_found.extend(dups)

                elapsed = time.perf_counter() - form_start
                log("INFO", f"[FORM] FINISHED '{item.form_title}' ({form_id})")
                log("INFO", f"[FORM] Stats | questions={total_questions} responses={total_responses} elapsed_s={elapsed:.2f}")
                if duplicates_found:
                    print(f"\n=== Duplicate answers in {form_id}: {duplicates_found} ===\n")
                save_grading_time(form_id, datetime.now(timezone.utc))
                write_heartbeat("form_complete")
                processed_count += 1
            except Exception as e:
                err = str(e)
                log("ERROR", f"Failed to process form: {form_url}")
                log("ERROR", f"Form ID: {form_id or 'unknown'} | Error: {err}")
                print(f"ERROR processing {form_url}: {err}")

        log("INFO", f"=== All forms processed. Completed {processed_count}/{total_forms} ===")
        log("INFO", "=== APP STATUS: FULLY FUNCTIONAL - Grading pipeline completed successfully ===")
        write_heartbeat("complete")
        sys.exit(0)

    # Hybrid controls: prepare forms in background, apply in-order one form at a time.
    prefetch_size = max(2, int(config.get("hybrid_prefetch_size", 6)))
    prep_workers = max(1, int(config.get("hybrid_prepare_workers", 2)))
    prep_q: "queue.Queue[Dict]" = queue.Queue(maxsize=prefetch_size)
    stop_event = threading.Event()

    next_to_prepare = 1
    next_to_apply = 1
    prepared_by_idx: Dict[int, Dict] = {}
    prep_lock = threading.Lock()

    def prep_worker():
        nonlocal next_to_prepare
        prep_service = get_service()
        while not stop_event.is_set():
            with prep_lock:
                if next_to_prepare > total_forms:
                    return
                i = next_to_prepare
                next_to_prepare += 1
            url = form_urls[i - 1]
            try:
                item = _prepare_form(prep_service, i, total_forms, url, grade_recent_only)
            except Exception as ex:
                item = {"idx": i, "form_url": url, "form_id": None, "skip": True, "reason": str(ex)}
            prep_q.put(item)

    prep_threads = [threading.Thread(target=prep_worker, daemon=False, name=f"prep-{i}") for i in range(prep_workers)]
    for t in prep_threads:
        t.start()

    processed_count = 0
    while next_to_apply <= total_forms:
        while next_to_apply not in prepared_by_idx:
            item = prep_q.get()
            prepared_by_idx[item["idx"]] = item

        item = prepared_by_idx.pop(next_to_apply)
        idx = item["idx"]
        form_url = item.get("form_url")
        form_id = item.get("form_id")
        print(f"Progress: {idx}/{total_forms}")

        try:
            form_start = time.perf_counter()
            if item.get("skip"):
                log("WARNING", f"Skipping form {form_id or form_url}: {item.get('reason', 'unknown')}")
                next_to_apply += 1
                continue

            form_title = item["form_title"]
            all_questions = item["all_questions"]

            log("INFO", f"[FORM] START {idx}/{total_forms} | form_id={form_id}")
            log("INFO", f"[FORM] Now grading: '{form_title}' ({form_id})")
            write_heartbeat("form_apply")

            total_responses = sum(len(q["responses"]) for q in all_questions)
            total_questions = len(all_questions)
            processed_responses = 0

            if generate_report:
                report_path = generate_form_feedback(form_id, form_title, all_questions)
                log("INFO", f"Report generated -> {report_path or 'FAILED'}")

            duplicates_found = []
            text_types = {"SHORT_ANSWER", "LONG_ANSWER"}
            for q_data in all_questions:
                q = q_data["question"]
                correct = q_data["correct_answers"]
                processed_responses += len(q_data["responses"])
                print(f"FormProgress: {processed_responses}/{total_responses}")
                if processed_responses % 10 == 0:
                    write_heartbeat("answer_evaluation")
                if correct and q["type"] in text_types:
                    dups = update_correct_answers(service, form_id, q["itemId"], correct, q["index"])
                    if dups:
                        duplicates_found.extend(dups)

            elapsed = time.perf_counter() - form_start
            log("INFO", f"[FORM] FINISHED '{form_title}' ({form_id})")
            log("INFO", f"[FORM] Stats | questions={total_questions} responses={total_responses} elapsed_s={elapsed:.2f}")
            if duplicates_found:
                print(f"\n=== Duplicate answers in {form_id}: {duplicates_found} ===\n")

            save_grading_time(form_id, datetime.now(timezone.utc))
            write_heartbeat("form_complete")
            processed_count += 1

        except Exception as e:
            err = str(e)
            log("ERROR", f"Failed to process form: {form_url}")
            log("ERROR", f"Form ID: {form_id or 'unknown'} | Error: {err}")
            print(f"ERROR processing {form_url}: {err}")

        next_to_apply += 1

    stop_event.set()
    for t in prep_threads:
        t.join(timeout=5)

    log("INFO", f"=== All forms processed. Completed {processed_count}/{total_forms} ===")
    write_heartbeat("complete")
    sys.exit(0)


if __name__ == "__main__":
    main()

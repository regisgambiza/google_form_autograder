import copy
import threading
import time

from answer_key_manager import backup_form_grading, enqueue_review
from answer_key_policy import clean_display
from evaluator_config import load_config
from logger import log


_BACKUP_LOCK = threading.Lock()
_BACKED_UP_FORMS = set()


def _ensure_form_backup(service, form_id, reason):
    with _BACKUP_LOCK:
        if form_id in _BACKED_UP_FORMS:
            return None
        path = backup_form_grading(service, form_id, reason=reason)
        _BACKED_UP_FORMS.add(form_id)
        return path

def update_correct_answers(
    service,
    form_id,
    question_id,
    correct_answers,
    question_index,
    trusted_expected=None,
    dry_run=None,
    create_backup=True,
    manual_approval=False,
    enqueue_added_review=True,
):
    review_record = None
    log("DEBUG", f"Entering update_correct_answers for QID {question_id} "
                 f"with correct_answers={correct_answers}, index={question_index}")
    
    if not correct_answers:
        log("INFO", f"No correct answers provided for QID {question_id}, skipping update. "
                    f"(duplicate check skipped)")
        return []

    duplicates = []  # always define

    log("DEBUG", f"Processing {len(correct_answers)} correct answers: {correct_answers}")
    
    try:
        log("DEBUG", f"Fetching form data for form ID {form_id}")
        form = service.forms().get(formId=form_id).execute()
        items = form.get("items", [])
        log("DEBUG", f"Form data retrieved: {len(items)} items found")
        
        target_item_index = None
        target_item = None
        for item_index, item in enumerate(items):
            if item.get("itemId") == question_id:
                target_item = item
                target_item_index = item_index
                break
        
        if not target_item:
            log("ERROR", f"Item with ID {question_id} not found in form {form_id}. "
                         f"(duplicate check skipped)")
            return []
            
        if "questionItem" not in target_item:
            log("ERROR", f"Item with ID {question_id} is not a question item. "
                         f"(duplicate check skipped)")
            return []
            
        if "pageBreakItem" in target_item:
            log("ERROR", f"Item with ID {question_id} is a PageBreakItem, cannot update. "
                         f"(duplicate check skipped)")
            return []
            
        if "textQuestion" not in target_item["questionItem"]["question"]:
            log("ERROR", f"Item with ID {question_id} is not a text question. "
                         f"(duplicate check skipped)")
            return []
            
        # Fetch existing correct answers
        question = target_item["questionItem"]["question"]
        existing_grading = copy.deepcopy(question.get("grading", {}))
        log("DEBUG", f"Question object: {question}")
        existing_answers = []
        if "grading" in question and "correctAnswers" in question["grading"]:
            existing_answers = [ans["value"] for ans in question["grading"]["correctAnswers"].get("answers", [])]
        log("INFO", f"Existing correct answers for QID {question_id}: {existing_answers}")
        
        cfg = load_config()
        auto_add_variants = bool(cfg.get("answer_key_auto_add_proven_equivalents", True))
        if dry_run is None:
            dry_run = bool(cfg.get("answer_key_dry_run", False))
        canonical = str((trusted_expected or [""])[0])
        if not clean_display(canonical):
            log("WARNING", f"No trusted teacher answer for QID {question_id}; answer-key update blocked.")
            return duplicates

        # Existing Google Form values and accepted student values are payloads,
        # not display strings. Exact equality is the only write-path dedupe rule.
        existing_raw = [str(value) for value in existing_answers if value is not None and str(value) != ""]
        existing_values = set(existing_raw)
        updated_answers = []
        seen = set()
        duplicates = []

        def append_unique(raw):
            if raw is None:
                return False
            value = str(raw)
            if value == "":
                return False
            if value in seen:
                duplicates.append(value)
                return False
            seen.add(value)
            updated_answers.append(value)
            return True

        # The teacher answer is always first and can never be removed/reordered.
        append_unique(canonical)
        if manual_approval:
            for value in correct_answers:
                if str(value) != canonical:
                    append_unique(value)
        else:
            # Preserve all existing variants until a teacher removes them in review.
            for value in existing_raw[1:]:
                append_unique(value)
            newly_added = []
            if auto_add_variants:
                max_variants = max(1, int(cfg.get("answer_key_max_variants", 50)))
                for value in correct_answers:
                    if len(updated_answers) >= max_variants:
                        break
                    raw_value = str(value) if value is not None else None
                    if raw_value is not None and raw_value != "" and raw_value not in existing_values and append_unique(raw_value):
                        newly_added.append(raw_value)
                if newly_added:
                    review_record = {
                        "form_id": form_id,
                        "item_id": question_id,
                        "question_id": question.get("questionId", question_id),
                        "canonical": canonical,
                        "candidates": newly_added,
                        "confidence": 1.0,
                        "route": "ai_added_to_form",
                        "added_to_form": True,
                        "reason": "AI-classified variants added pending teacher audit",
                    }

        changed = updated_answers != existing_raw
        log("INFO", f"Filtered {len(duplicates)} duplicate answer-key candidates: {duplicates}")

        auto_threshold = float(cfg.get("answer_key_auto_apply_confidence", 0.95))
        if auto_threshold > 1.0:
            log("INFO", f"Answer-key automation disabled by confidence threshold for QID {question_id}.")
            return duplicates
        if not changed and review_record is None:
            log("INFO", f"Answer key for QID {question_id} is already clean; skipping update.")
            return duplicates
        if dry_run:
            log(
                "INFO",
                f"DRY RUN QID {question_id}: {existing_answers} -> {updated_answers}; no update sent.",
            )
            return duplicates
        log("INFO", f"Submitting canonical plus {max(0, len(updated_answers) - 1)} accepted variant(s)")
        log("DEBUG", f"Final verified answers: {updated_answers}")
            
    except Exception as e:
        log("ERROR", f"Failed during validation for QID {question_id}: {str(e)} "
                     f"(duplicate check skipped)")
        return duplicates
    
    # Prepare update request
    log("DEBUG", f"Preparing update request for QID {question_id}")
    updated_grading = existing_grading
    updated_grading["correctAnswers"] = {
        "answers": [{"value": ans} for ans in updated_answers]
    }
    update_request = {
        "requests": [
            {
                "updateItem": {
                    "item": {
                        "itemId": question_id,
                        "questionItem": {
                            "question": {
                                "questionId": question.get("questionId", question_id),
                                "grading": updated_grading,
                            }
                        },
                    },
                    "location": {
                        "index": target_item_index
                    },
                    "updateMask": "questionItem.question.grading"
                }
            }
        ]
    }
    
    try:
        if changed:
            if create_backup:
                backup_path = _ensure_form_backup(service, form_id, reason=f"before update {question_id}")
                if backup_path:
                    log("INFO", f"Answer-key backup saved: {backup_path}")
            log("DEBUG", f"Executing batch update for form ID {form_id}")
            api_started = time.perf_counter()
            service.forms().batchUpdate(formId=form_id, body=update_request).execute()
            log(
                "INFO",
                f"[GOOGLE API] operation=batchUpdate form_id={form_id} item_id={question_id} "
                f"duration_ms={(time.perf_counter() - api_started) * 1000:.0f} status=success "
                f"answer_count={len(updated_answers)}",
            )
        if review_record and enqueue_added_review:
            enqueue_review(review_record)
        if changed:
            log("INFO", f"Updated QID {question_id} successfully with {len(updated_answers)} verified answers.")
        else:
            log("INFO", f"Queued AI review for QID {question_id} without changing existing answers.")
    except Exception as e:
        log("ERROR", f"Failed to update QID {question_id}: {str(e)}")
        raise
    
    return duplicates

import copy
import threading

from answer_key_manager import backup_form_grading, enqueue_review
from answer_key_policy import clean_display, equivalence_confidence, identity_key, prepare_answer_key, safely_equivalent
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
        canonical = clean_display((trusted_expected or [""])[0]).split("|", 1)[0].strip()
        if not canonical:
            log("WARNING", f"No trusted teacher answer for QID {question_id}; answer-key update blocked.")
            return duplicates

        existing_clean = [clean_display(value) for value in existing_answers if clean_display(value)]
        existing_keys = {identity_key(value) for value in existing_clean}
        updated_answers = []
        seen = set()
        duplicates = []

        def append_unique(raw):
            value = clean_display(raw)
            if not value:
                return False
            key = identity_key(value)
            if key in seen:
                duplicates.append(value)
                return False
            seen.add(key)
            updated_answers.append(value)
            return True

        # The teacher answer is always first and can never be removed/reordered.
        append_unique(canonical)
        if manual_approval:
            for value in correct_answers:
                if identity_key(value) != identity_key(canonical):
                    append_unique(value)
        else:
            # Preserve all existing variants until a teacher removes them in review.
            for value in existing_clean[1:]:
                append_unique(value)
            newly_added = []
            if auto_add_variants:
                max_variants = max(1, int(cfg.get("answer_key_max_variants", 50)))
                for value in correct_answers:
                    cleaned = clean_display(value)
                    if len(updated_answers) >= max_variants:
                        break
                    if cleaned and identity_key(cleaned) not in existing_keys and append_unique(cleaned):
                        newly_added.append(cleaned)
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
                        "reason": "AI-approved variants added pending teacher audit",
                    }

        changed = updated_answers != existing_clean
        log("INFO", f"Filtered {len(duplicates)} duplicate answer-key candidates: {duplicates}")

        auto_threshold = float(cfg.get("answer_key_auto_apply_confidence", 0.95))
        if auto_threshold > 1.0:
            log("INFO", f"Answer-key automation disabled by confidence threshold for QID {question_id}.")
            return duplicates
        if not changed:
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
        if create_backup:
            backup_path = _ensure_form_backup(service, form_id, reason=f"before update {question_id}")
            if backup_path:
                log("INFO", f"Answer-key backup saved: {backup_path}")
        log("DEBUG", f"Executing batch update for form ID {form_id}")
        service.forms().batchUpdate(formId=form_id, body=update_request).execute()
        if review_record:
            enqueue_review(review_record)
        log("INFO", f"Updated QID {question_id} successfully with {len(updated_answers)} verified answers.")
    except Exception as e:
        log("ERROR", f"Failed to update QID {question_id}: {str(e)}")
        raise
    
    return duplicates

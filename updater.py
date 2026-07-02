import copy

from answer_key_policy import prepare_answer_key
from evaluator_config import load_config
from logger import log

def update_correct_answers(
    service,
    form_id,
    question_id,
    correct_answers,
    question_index,
    trusted_expected=None,
):
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
        plan = prepare_answer_key(
            existing_answers,
            correct_answers,
            trusted_expected or [],
            max_variants=int(cfg.get("answer_key_max_variants", 5)),
        )
        duplicates = plan.duplicates
        updated_answers = plan.answers
        if plan.rejected:
            log(
                "WARNING",
                f"Rejected {len(plan.rejected)} unverified answer-key candidates for "
                f"QID {question_id}: {plan.rejected}",
            )
        log("INFO", f"Filtered {len(duplicates)} duplicate answer-key candidates: {duplicates}")

        if not trusted_expected:
            log("WARNING", f"No trusted teacher answer for QID {question_id}; answer-key update blocked.")
            return duplicates
        if not plan.changed:
            log("INFO", f"Answer key for QID {question_id} is already clean; skipping update.")
            return duplicates
        log("INFO", f"Submitting {len(updated_answers)} verified answer-key variants")
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
        log("DEBUG", f"Executing batch update for form ID {form_id}")
        service.forms().batchUpdate(formId=form_id, body=update_request).execute()
        log("INFO", f"Updated QID {question_id} successfully with {len(updated_answers)} verified answers.")
    except Exception as e:
        log("ERROR", f"Failed to update QID {question_id}: {str(e)}")
        raise
    
    return duplicates

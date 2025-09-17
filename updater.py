from logger import log

def update_correct_answers(service, form_id, question_id, correct_answers, question_index):
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
        
        target_item = next((item for item in items if item["itemId"] == question_id), None)
        
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
        log("DEBUG", f"Question object: {question}")
        existing_answers = []
        if "grading" in question and "correctAnswers" in question["grading"]:
            existing_answers = [ans["value"] for ans in question["grading"]["correctAnswers"].get("answers", [])]
        log("INFO", f"Existing correct answers for QID {question_id}: {existing_answers}")
        
        # Check for duplicates
        new_answers = []
        for ans in correct_answers:
            if ans in existing_answers:
                duplicates.append(ans)
            else:
                new_answers.append(ans)

        # DEBUG log with full duplicates list
        log("DEBUG", f"Duplicates {len(duplicates)} {duplicates}")

        log("INFO", f"{len(duplicates)} duplicates found: {duplicates}")
        log("INFO", f"Duplicate answers filtered out: {duplicates}")
        log("INFO", "removing duplicates")
        
        if not new_answers:
            log("INFO", f"All provided answers for QID {question_id} are already in correct answers. "
                        f"Skipping update after duplicate removal.")
            return duplicates
        
        updated_answers = existing_answers + new_answers
        log("INFO", f"now submitting {len(new_answers)} unique answers")
        log("DEBUG", f"New answers to add: {new_answers}, Combined answers: {updated_answers}")
            
    except Exception as e:
        log("ERROR", f"Failed during validation for QID {question_id}: {str(e)} "
                     f"(duplicate check skipped)")
        return duplicates
    
    # Prepare update request
    log("DEBUG", f"Preparing update request for QID {question_id}")
    update_request = {
        "requests": [
            {
                "updateItem": {
                    "item": {
                        "itemId": question_id,
                        "questionItem": {
                            "question": {
                                "questionId": question_id,
                                "grading": {
                                    "correctAnswers": {
                                        "answers": [{"value": ans} for ans in updated_answers]
                                    },
                                    "pointValue": 1,
                                }
                            }
                        },
                    },
                    "location": {
                        "index": question_index - 1  # Convert to 0-based index
                    },
                    "updateMask": "questionItem.question.grading"
                }
            }
        ]
    }
    
    try:
        log("DEBUG", f"Executing batch update for form ID {form_id}")
        service.forms().batchUpdate(formId=form_id, body=update_request).execute()
        log("INFO", f"Updated QID {question_id} successfully with {len(new_answers)} new answers.")
    except Exception as e:
        log("ERROR", f"Failed to update QID {question_id}: {str(e)}")
        raise
    
    return duplicates

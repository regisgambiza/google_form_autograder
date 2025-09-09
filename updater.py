from logger import log

def update_correct_answers(service, form_id, question_id, correct_answers, question_index):
    if not correct_answers:
        log("INFO", f"No correct answers for QID {question_id}, skipping update.")
        return
    
    log("DEBUG", f"Updating QID {question_id} with correct answers {correct_answers} at index {question_index}...")
    
    # Verify that the item is a valid question item
    try:
        form = service.forms().get(formId=form_id).execute()
        items = form.get("items", [])
        target_item = next((item for item in items if item["itemId"] == question_id), None)
        
        if not target_item:
            log("ERROR", f"Item with ID {question_id} not found in form {form_id}.")
            return
            
        if "questionItem" not in target_item:
            log("ERROR", f"Item with ID {question_id} is not a question item.")
            return
            
        if "pageBreakItem" in target_item:
            log("ERROR", f"Item with ID {question_id} is a PageBreakItem, cannot update.")
            return
            
        if "textQuestion" not in target_item["questionItem"]["question"]:
            log("ERROR", f"Item with ID {question_id} is not a text question.")
            return
            
    except Exception as e:
        log("ERROR", f"Failed to verify item ID {question_id}: {str(e)}")
        return
    
    # For text questions, we need to use a different update structure
    update_request = {
        "requests": [
            {
                "updateItem": {
                    "item": {
                        "itemId": question_id,
                        "questionItem": {
                            "question": {
                                "questionId": question_id,  # Add questionId for text questions
                                "grading": {
                                    "correctAnswers": {
                                        "answers": [{"value": ans} for ans in correct_answers]
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
        service.forms().batchUpdate(formId=form_id, body=update_request).execute()
        log("INFO", f"Updated QID {question_id} successfully.")
    except Exception as e:
        log("ERROR", f"Failed to update QID {question_id}: {str(e)}")
        raise
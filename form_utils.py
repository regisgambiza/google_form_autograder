from logger import log

def get_form_structure(service, form_id):
    log("DEBUG", f"Fetching form structure for ID {form_id}...")
    form = service.forms().get(formId=form_id).execute()
    questions = []
    

    for form_index, item in enumerate(form.get("items", [])):
        # Skip items that are page breaks or lack questionItem
        if "pageBreakItem" in item:
            log("DEBUG", f"Skipping page break item: {item.get('title', 'Untitled')} (Item ID: {item.get('itemId', 'Unknown')})")
            continue
        if "questionItem" not in item:
            log("DEBUG", f"Skipping non-question item: {item.get('title', 'Untitled')} (Item ID: {item.get('itemId', 'Unknown')})")
            continue

        # Extract the question object
        question_item = item["questionItem"]
        question = question_item["question"]

        q = {
            "itemId": item["itemId"],
            "questionId": question["questionId"],
            "index": form_index + 1,
            "title": item.get("title", ""),
            "description": item.get("description", "")
        }

        # Detect question type
        if "textQuestion" in question:
            if question["textQuestion"].get("paragraph", False):
                q["type"] = "LONG_ANSWER"
            else:
                q["type"] = "SHORT_ANSWER"
            log("DEBUG", f"Found text question: ID={q['questionId']}, Type={q['type']}, Title={q['title']}, Item ID={q['itemId']}, Form Index={form_index}")
        elif "choiceQuestion" in question:
            cq_type = question["choiceQuestion"].get("type", "RADIO")
            if cq_type == "CHECKBOX":
                q["type"] = "CHECKBOX"
            elif cq_type == "DROP_DOWN":
                q["type"] = "DROPDOWN"
            else:
                q["type"] = "MULTIPLE_CHOICE"
            log("DEBUG", f"Found choice question ({cq_type}): ID={q['questionId']}, Type={q['type']}, Title={q['title']}, Item ID={q['itemId']}, Form Index={form_index}")
        elif "scaleQuestion" in question:
            q["type"] = "SCALE"
            log("DEBUG", f"Found scale question: ID={q['questionId']}, Title={q['title']}, Item ID={q['itemId']}, Form Index={form_index}")
        else:
            q["type"] = "OTHER"
            log("DEBUG", f"Found other question type: ID={q['questionId']}, Title={q['title']}, Item ID={q['itemId']}, Form Index={form_index}")

        questions.append(q)
        
    if not questions:
        log("INFO", "No valid text questions found in the form.")
    
    return questions
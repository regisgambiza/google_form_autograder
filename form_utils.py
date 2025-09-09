from logger import log

def get_form_structure(service, form_id):
    log("DEBUG", f"Fetching form structure for ID {form_id}...")
    form = service.forms().get(formId=form_id).execute()
    questions = []
    
    # Log the full form structure for debugging
    log("DEBUG", f"Full form items: {form.get('items', [])}")
    
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
        
        # Only process text questions (skip multiple choice and other types)
        if "textQuestion" not in question:
            log("DEBUG", f"Skipping non-text question: {item.get('title', 'Untitled')} (Item ID: {item.get('itemId', 'Unknown')})")
            continue
            
        q = {
            "itemId": item["itemId"],
            "questionId": question["questionId"],
            "index": form_index + 1,  # Use actual form index (1-based for logging)
            "title": item.get("title", ""),
            "description": item.get("description", "")
        }
        
        # Determine if it's short or long answer
        if question["textQuestion"].get("paragraph", False):
            q["type"] = "LONG_ANSWER"
        else:
            q["type"] = "SHORT_ANSWER"
            
        questions.append(q)
        
        log("DEBUG", f"Found text question: ID={q['questionId']}, Type={q['type']}, Title={q['title']}, Item ID={q['itemId']}, Form Index={form_index}")
        
    if not questions:
        log("INFO", "No valid text questions found in the form.")
    
    return questions
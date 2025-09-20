import os
import json
from datetime import datetime
from logger import log
import re
import ollama

# Load config for models
with open("config.json") as f:
    config = json.load(f)

MODELS = config["models"]
FEEDBACK_MODEL = MODELS.get("judge", "gpt-oss:20b")
if isinstance(FEEDBACK_MODEL, list):
    FEEDBACK_MODEL = FEEDBACK_MODEL[0]

BATCH_SIZE_LIMIT = 10  # Maximum questions per batch for AI requests

def sanitize_filename(name: str) -> str:
    """Remove illegal characters so the title can be safely used as a filename."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def get_default_feedback(q_data):
    """Generate default feedback with an explanation of how to attempt the question."""
    question = q_data['question']
    q_index = question.get('index', '?')
    q_title = question.get('title', 'Untitled')
    q_type = question.get('type', 'Unknown')
    correct_answers = q_data.get('correct_answers', [])
    responses = q_data.get('responses', [])
    canonical_answer = correct_answers[0] if correct_answers else "Not provided"

    explanation = "No AI-generated explanation available.\n"
    if q_type in ["MULTIPLE_CHOICE", "DROPDOWN"]:
        explanation = (
            "- Read the question carefully.\n"
            "- Look at all the options provided.\n"
            "- Choose the option that best answers the question.\n"
            "- Check your answer to make sure it makes sense."
        )
    elif q_type == "CHECKBOX":
        explanation = (
            "- Read the question carefully.\n"
            "- Select all options that apply to the question.\n"
            "- Make sure you understand what the question is asking.\n"
            "- Review your choices to ensure they are correct."
        )
    elif q_type in ["SHORT_ANSWER", "LONG_ANSWER"]:
        explanation = (
            "- Read the question carefully.\n"
            "- Write a clear and concise answer.\n"
            "- Make sure your answer directly addresses the question.\n"
            "- Check your spelling and grammar."
        )
    else:
        explanation = (
            "- Understand what the question is asking.\n"
            "- Provide a clear and complete response.\n"
            "- Double-check your answer for accuracy."
        )

    response_summary = f"- {len(responses)} responses collected.\n" if responses else "- No responses collected.\n"
    
    return f"""**Correct Answer:** {canonical_answer}
**Explanation / Steps:**
{explanation}
## Feedback
Good effort! Keep practicing to improve your understanding.
## Common Mistakes
- Not reading the question carefully.
- Choosing answers without checking all options.
## Keep Practicing
Try similar questions to get better! Review the correct answer to understand why it is right.
---"""

def generate_question_feedback_batch(questions_batch):
    """
    Generate feedback for a batch of questions using Ollama in a single API call.
    Returns a list of markdown-formatted feedback strings, one per question.
    """
    log("DEBUG", f"Generating feedback for batch of {len(questions_batch)} questions")
    log("DEBUG", f"Question indices in batch: {[q['question'].get('index', '?') for q in questions_batch]}")

    prompt = ""
    for idx, q_data in enumerate(questions_batch):
        question = q_data['question']
        responses = q_data.get('responses', [])
        correct_answers = q_data.get('correct_answers', [])
        q_index = question.get('index', '?')
        q_title = question.get('title', 'Untitled')
        q_type = question.get('type', 'Unknown')
        q_desc = question.get('description', 'N/A')
        correct_display = correct_answers[0] if correct_answers else "Not provided"

        prompt += f"""
Question {idx + 1}/{len(questions_batch)}:
Question Index: {q_index}
Title: {q_title}
Type: {q_type}
Description: {q_desc}
Correct Answer: {correct_display}
Student Responses: {responses}

You are a friendly math teacher for Grade 7-8 Thai learners with basic English skills.
Write feedback in simple English using this markdown format:

**Correct Answer:** {correct_display}
**Explanation / Steps:**
[Explain how to get the answer in short, simple sentences.]
## Feedback
[Write a short, friendly feedback.]
## Common Mistakes
[List common mistakes as bullet points.]
## Keep Practicing
[Give encouragement and a tip.]

---
"""
    prompt += f"""
Return a JSON array with exactly {len(questions_batch)} elements, each containing the markdown feedback for the corresponding question in order.
Example: ["markdown for Q1", "markdown for Q2", ...]
DO NOT include any additional text outside the JSON array.
"""

    try:
        response = ollama.chat(model=FEEDBACK_MODEL, messages=[{"role": "user", "content": prompt}])
        raw_response = response["message"]["content"]
        log("DEBUG", f"Raw AI response: {raw_response}")
        feedback_list = json.loads(raw_response)
        
        if not isinstance(feedback_list, list):
            log("ERROR", "AI response is not a list. Using default feedback.")
            return [get_default_feedback(q_data) for q_data in questions_batch]
        
        if len(feedback_list) != len(questions_batch):
            log("WARNING", f"Expected {len(questions_batch)} feedback entries, got {len(feedback_list)}. Using default feedback for missing entries.")
            feedback_list = feedback_list + [get_default_feedback(q_data) for q_data in questions_batch[len(feedback_list):]]
        
        for i, feedback in enumerate(feedback_list):
            if not isinstance(feedback, str) or not feedback.strip():
                log("WARNING", f"Invalid feedback for question {questions_batch[i]['question'].get('index', '?')}. Using default feedback.")
                feedback_list[i] = get_default_feedback(questions_batch[i])
        
        log("DEBUG", f"Feedback generated for batch of {len(questions_batch)} questions")
        return feedback_list
    except Exception as e:
        log("ERROR", f"Failed to generate batch feedback: {e}. Using default feedback.")
        return [get_default_feedback(q_data) for q_data in questions_batch]

def generate_form_feedback(form_id, form_title, form_questions):
    """
    Generate a feedback report for all questions, sorted by index, in a single file.
    Handles all question types with batched AI requests.
    """
    log("INFO", f"Generating feedback report for form {form_id}")

    feedback_dir = "Feedback"
    if not os.path.exists(feedback_dir):
        os.makedirs(feedback_dir)
        log("INFO", "Created Feedback directory.")

    safe_title = sanitize_filename(form_title)
    report_filename = f"{safe_title}.md"
    report_path = os.path.join(feedback_dir, report_filename)

    content = f"# Feedback for {form_title}\n\n"

    # Sort questions by index
    sorted_questions = sorted(form_questions, key=lambda x: x["question"]["index"])
    log("DEBUG", f"Sorted question indices: {[q['question']['index'] for q in sorted_questions]}")

    # Process questions in batches
    question_batches = [sorted_questions[i:i + BATCH_SIZE_LIMIT] for i in range(0, len(sorted_questions), BATCH_SIZE_LIMIT)]
    log("DEBUG", f"Split {len(sorted_questions)} questions into {len(question_batches)} batches")

    for batch_idx, batch in enumerate(question_batches):
        log("DEBUG", f"Processing batch {batch_idx + 1}/{len(question_batches)} with {len(batch)} questions")
        feedback_list = generate_question_feedback_batch(batch)
        for q_data, feedback in zip(batch, feedback_list):
            question = q_data['question']
            q_index = question.get('index', '?')
            q_title = question.get('title', 'Untitled')
            q_type = question.get('type', 'Unknown')
            correct_answers = q_data.get('correct_answers', [])
            canonical_answer = correct_answers[0] if correct_answers else "Not provided"

            # Add question header once
            content += f"## Question {q_index}: {q_title} ({q_type})\n"
            if question.get('description'):
                content += f"**Description:** {question['description']}\n"
            content += f"{feedback}\n\n"

    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(content)
        log("INFO", f"Feedback report saved to {report_path}")
        return report_path
    except Exception as e:
        log("ERROR", f"Failed to save feedback report for form {form_id}: {e}")
        return None
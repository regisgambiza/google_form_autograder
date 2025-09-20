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

BATCH_SIZE_LIMIT = 1  # Process one question at a time to avoid context confusion and hallucinations

def sanitize_filename(name: str) -> str:
    """Remove illegal characters so the title can be safely used as a filename."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def get_default_feedback(q_data, form_title):
    """Generate default feedback with an explanation of how to attempt the question."""
    question = q_data['question']
    q_index = question.get('index', '?')
    q_title = question.get('title', 'Untitled')
    q_type = question.get('type', 'Unknown')
    correct_answers = q_data.get('correct_answers', [])
    responses = q_data.get('responses', [])
    canonical_answer = correct_answers[0] if correct_answers else "Not provided"

    explanation = "No specific explanation available. Approach the question as follows:\n"
    if q_type in ["MULTIPLE_CHOICE", "DROPDOWN"]:
        explanation += (
            "- Read the question and description carefully.\n"
            "- Review all options.\n"
            "- Select the best match based on the correct answer provided.\n"
            "- Eliminate incorrect options by comparing to the correct one."
        )
    elif q_type == "CHECKBOX":
        explanation += (
            "- Read the question carefully.\n"
            "- Identify all applicable options based on the correct answers.\n"
            "- Select multiple if needed.\n"
            "- Verify against the provided correct answers."
        )
    elif q_type in ["SHORT_ANSWER", "LONG_ANSWER"]:
        explanation += (
            f"- Read the question from '{form_title}'.\n"
            "- Provide a concise response matching the correct answer '{canonical_answer}'.\n"
            "- Use simple language and check for accuracy.\n"
            "- If no correct answer is provided, describe the key concept briefly."
        )
    else:
        explanation += (
            "- Understand the question in the context of 2D/3D shapes.\n"
            "- Refer to the correct answer for guidance.\n"
            "- Explain properties or calculations step-by-step."
        )

    response_summary = f"({len(responses)} responses collected.)" if responses else "(No responses collected.)"
    
    return f"""**Correct Answer:** {canonical_answer} {response_summary}
**Explanation / Steps:**
{explanation}
## Feedback
Good effort on this question from {form_title}! Review the correct answer for better understanding.
## Common Mistakes
- Misinterpreting the question type.
- Not aligning with the provided correct answer.
- Overcomplicating simple concepts in shapes.
## Keep Practicing
Practice similar questions on 2D and 3D shapes to build confidence. Focus on the key properties mentioned.
---"""

def generate_question_feedback_batch(form_title, questions_batch):
    """
    Generate feedback for a batch of questions using Ollama. Now processes one at a time to minimize hallucinations.
    Returns a list of markdown-formatted feedback strings, one per question.
    """
    feedback_list = []
    for idx, q_data in enumerate(questions_batch):
        question = q_data['question']
        responses = q_data.get('responses', [])
        correct_answers = q_data.get('correct_answers', [])
        q_index = question.get('index', '?')
        q_title = question.get('title', 'Untitled')
        q_type = question.get('type', 'Unknown')
        q_desc = question.get('description', 'N/A')
        correct_display = correct_answers[0] if correct_answers else "Not provided"

        # Build individual prompt for this question
        prompt = f"""
You are a friendly math teacher for Grade 7-8 Thai learners with basic English skills. This question is from the form '{form_title}' on 2D and 3D shapes.

IMPORTANT: Base your ENTIRE response STRICTLY on the provided question title, description, correct answer, and student responses. Do NOT use external knowledge, recall other questions, or invent information, calculations, or examples. If the correct answer is 'Not provided', give a general step-by-step approach to solving similar questions without specifics. Repeat: Use ONLY the given details.

Question Index: {q_index}
Title: {q_title}
Type: {q_type}
Description: {q_desc}
Correct Answer: {correct_display}
Student Responses: {responses}

Write feedback in simple English using this EXACT markdown format. Start directly with the sections below:

**Correct Answer:** {correct_display}
**Explanation / Steps:**
[Explain how to arrive at the correct answer using ONLY the provided information. Use short, simple sentences. If no correct answer, describe general steps for this question type in shapes context.]
## Feedback
[Write a short, friendly, encouraging feedback based on the responses and correct answer.]
## Common Mistakes
[List 2-3 common mistakes as bullet points, based ONLY on the provided responses and correct answer.]
## Keep Practicing
[Give 1-2 sentences of encouragement with a simple tip related to the question.]

---
"""
        try:
            response = ollama.chat(model=FEEDBACK_MODEL, messages=[{"role": "user", "content": prompt}])
            raw_response = response["message"]["content"]
            log("DEBUG", f"Raw AI response for Q{q_index}: {raw_response[:200]}...")  # Log first 200 chars
            feedback = raw_response.strip()
            
            # Validate format: should start with **Correct Answer:**
            if not feedback.startswith("**Correct Answer:**"):
                log("WARNING", f"Invalid format for Q{q_index}. Using default feedback.")
                feedback = get_default_feedback(q_data, form_title)
            else:
                # Clean up any extra text
                feedback = re.sub(r'^.*?(\*\*Correct Answer:\*\*.*)', r'\1', feedback, flags=re.DOTALL).strip()
            
            feedback_list.append(feedback)
            log("DEBUG", f"Feedback generated for Q{q_index}")
        except Exception as e:
            log("ERROR", f"Failed to generate feedback for Q{q_index}: {e}. Using default.")
            feedback_list.append(get_default_feedback(q_data, form_title))
    
    return feedback_list

def generate_form_feedback(form_id, form_title, form_questions):
    """
    Generate a feedback report for all questions, sorted by index, in a single file.
    Handles all question types with individual AI requests to prevent hallucinations.
    """
    log("INFO", f"Generating feedback report for form {form_id} - {form_title}")

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

    # Process questions individually (BATCH_SIZE_LIMIT=1)
    question_batches = [sorted_questions[i:i + BATCH_SIZE_LIMIT] for i in range(0, len(sorted_questions), BATCH_SIZE_LIMIT)]
    log("DEBUG", f"Split {len(sorted_questions)} questions into {len(question_batches)} individual calls")

    for batch_idx, batch in enumerate(question_batches):
        log("DEBUG", f"Processing question {batch[0]['question']['index']}")
        feedback_list = generate_question_feedback_batch(form_title, batch)
        for q_data, feedback in zip(batch, feedback_list):
            question = q_data['question']
            q_index = question.get('index', '?')
            q_title = question.get('title', 'Untitled')
            q_type = question.get('type', 'Unknown')

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
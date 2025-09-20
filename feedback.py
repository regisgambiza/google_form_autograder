import os
import json
from datetime import datetime
from logger import log
import re
import ollama

# Load config for models
with open("config.json") as f:
    config = json.load(f)

MODELS = config["models"].get("judge", ["deepseek-r1:32b", "gpt-oss:20b", "gemma2:9b"])

BATCH_SIZE_LIMIT = 1  # Process one question at a time to avoid confusion

def sanitize_filename(name: str) -> str:
    """Remove illegal characters for safe filenames."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def get_default_feedback(q_data, form_title):
    """Generate fallback feedback if models fail."""
    question = q_data['question']
    q_index = question.get('index', '?')
    q_title = question.get('title', 'Untitled')
    q_type = question.get('type', 'Unknown')
    correct_answers = q_data.get('correct_answers', [])
    responses = q_data.get('responses', [])
    canonical_answer = correct_answers[0] if correct_answers else "Not provided"

    explanation = "No specific explanation available. Try this:\n"
    if q_type in ["MULTIPLE_CHOICE", "DROPDOWN"]:
        explanation += (
            "- Read the question carefully.\n"
            "- Check all options.\n"
            "- Pick the one that matches the correct answer.\n"
            "- Rule out wrong options."
        )
    elif q_type == "CHECKBOX":
        explanation += (
            "- Read the question carefully.\n"
            "- Choose all correct options.\n"
            "- Check against the correct answers."
        )
    elif q_type in ["SHORT_ANSWER", "LONG_ANSWER"]:
        explanation += (
            f"- Read the question from '{form_title}'.\n"
            "- Write a clear answer like '{canonical_answer}'.\n"
            "- Use simple words and double-check.\n"
            "- If no correct answer, explain the main idea."
        )
    else:
        explanation += (
            "- Think about 2D/3D shapes.\n"
            "- Use the correct answer as a guide.\n"
            "- Explain steps clearly."
        )

    response_summary = f"({len(responses)} responses)" if responses else "(No responses)"
    
    return f"""**Correct Answer:** {canonical_answer} {response_summary}
**Explanation / Steps:**
{explanation}
## Feedback
Nice try on this question from {form_title}! Look at the correct answer to learn more.
## Common Mistakes
- Misunderstanding the question type.
- Not matching the correct answer.
- Making shapes too complicated.
## Keep Practicing
Practice more 2D/3D shape questions. Focus on their properties.
---"""

def generate_model_discussion(q_data, model, form_title):
    """Get one model's input on the correct answer and steps."""
    question = q_data['question']
    responses = q_data.get('responses', [])
    correct_answers = q_data.get('correct_answers', [])
    q_index = question.get('index', '?')
    q_title = question.get('title', 'Untitled')
    q_type = question.get('type', 'Unknown')
    q_desc = question.get('description', 'N/A')
    correct_display = correct_answers[0] if correct_answers else "Not provided"

    prompt = f"""
You are a math teacher for Grade 7-8 Thai students with basic English. This is from '{form_title}' about 2D/3D shapes.

Use ONLY the given details. Do NOT add outside info or examples.

Question Index: {q_index}
Title: {q_title}
Type: {q_type}
Description: {q_desc}
Correct Answer: {correct_display}
Student Responses: {responses}

Suggest:
- Correct Answer (confirm or fix based on given data)
- Explanation / Steps (short, simple sentences)

Use this format:
**Proposed Correct Answer:** [Your answer]
**Proposed Explanation / Steps:**
[Simple steps to solve.]
---
"""
    try:
        response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        raw_response = response["message"]["content"].strip()
        log("DEBUG", f"Discussion from {model} for Q{q_index}: {raw_response[:200]}...")
        return raw_response
    except Exception as e:
        log("ERROR", f"Failed discussion from {model} for Q{q_index}: {e}")
        return "No response from this model."

def generate_question_feedback_batch(form_title, questions_batch):
    """Generate feedback using model discussions."""
    feedback_list = []
    for q_data in questions_batch:
        q_index = q_data['question'].get('index', '?')

        # Get discussion from all models
        discussions = {model: generate_model_discussion(q_data, model, form_title) for model in MODELS}

        # Combine discussions with the first model
        primary_model = MODELS[0]
        synthesis_prompt = f"""
You are a math teacher for Grade 7-8 Thai students with basic English. Combine these model discussions into one clear feedback report.

Question Details:
- Title: {q_data['question'].get('title', 'Untitled')}
- Type: {q_data['question'].get('type', 'Unknown')}
- Description: {q_data['question'].get('description', 'N/A')}
- Correct Answer: {q_data.get('correct_answers', [])[0] if q_data.get('correct_answers') else "Not provided"}
- Student Responses: {q_data.get('responses', [])}

Discussions:
{'\n\n'.join([f"From {model}: {output}" for model, output in discussions.items()])}

Use the given correct answer first. If models disagree, pick the answer most agree on. Choose the clearest steps. Write in simple English.

Use this format:
**Correct Answer:** [Combined answer]
**Explanation / Steps:**
[Clear steps in short sentences.]
## Feedback
[Short, encouraging feedback.]
## Common Mistakes
[List 2-3 mistakes from responses or discussions.]
## Keep Practicing
[1-2 sentences with a tip.]
---
"""
        try:
            response = ollama.chat(model=primary_model, messages=[{"role": "user", "content": synthesis_prompt}])
            feedback = response["message"]["content"].strip()
            log("DEBUG", f"Synthesis from {primary_model} for Q{q_index}: {feedback[:200]}...")
            
            # Check if feedback follows the format
            if not feedback.startswith("**Correct Answer:**"):
                log("WARNING", f"Invalid format for Q{q_index}. Using default.")
                feedback = get_default_feedback(q_data, form_title)
            else:
                feedback = re.sub(r'^.*?(\*\*Correct Answer:\*\*.*)', r'\1', feedback, flags=re.DOTALL).strip()
            
            feedback_list.append(feedback)
            log("DEBUG", f"Feedback generated for Q{q_index}")
        except Exception as e:
            log("ERROR", f"Failed to synthesize feedback for Q{q_index}: {e}. Using default.")
            feedback_list.append(get_default_feedback(q_data, form_title))
    
    return feedback_list

def generate_form_feedback(form_id, form_title, form_questions):
    """Generate a feedback report for all questions."""
    log("INFO", f"Creating feedback report for form {form_id} - {form_title}")

    feedback_dir = "Feedback"
    if not os.path.exists(feedback_dir):
        os.makedirs(feedback_dir)
        log("INFO", "Created Feedback directory.")

    safe_title = sanitize_filename(form_title)
    report_filename = f"{safe_title}.md"
    report_path = os.path.join(feedback_dir, report_filename)

    content = f"# Feedback for {form_title}\n\n"

    sorted_questions = sorted(form_questions, key=lambda x: x["question"]["index"])
    log("DEBUG", f"Sorted questions: {[q['question']['index'] for q in sorted_questions]}")

    question_batches = [sorted_questions[i:i + BATCH_SIZE_LIMIT] for i in range(0, len(sorted_questions), BATCH_SIZE_LIMIT)]
    log("DEBUG", f"Split {len(sorted_questions)} questions into {len(question_batches)} calls")

    for batch in question_batches:
        log("DEBUG", f"Processing question {batch[0]['question']['index']}")
        feedback_list = generate_question_feedback_batch(form_title, batch)
        for q_data, feedback in zip(batch, feedback_list):
            question = q_data['question']
            q_index = question.get('index', '?')
            q_title = question.get('title', 'Untitled')
            q_type = question.get('type', 'Unknown')

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
import os
import json
from datetime import datetime
from logger import log
import re

def sanitize_filename(name: str) -> str:
    """Remove illegal characters so the title can be safely used as a filename."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()
import ollama

# Load config for models
with open("config.json") as f:
    config = json.load(f)

MODELS = config["models"]
FEEDBACK_MODEL = MODELS.get("judge", "gpt-oss:20b")
if isinstance(FEEDBACK_MODEL, list):
    FEEDBACK_MODEL = FEEDBACK_MODEL[0]


def generate_question_feedback(question, responses, correct_answers):
    """
    Generate human-readable, teacher-style feedback for a single question using Ollama.
    Returns a markdown-formatted string.
    """
    log("DEBUG", f"Generating feedback for Q{question.get('index', '?')}")

    if not responses:
        return "## No Responses\nNo responses collected for this question."

    # Pick one clear correct answer for display (avoid long lists)
    correct_display = correct_answers[0] if correct_answers else "Not provided"

    # Build teacher-style prompt
    prompt = f"""
            You are a kind and encouraging mathematics teacher giving homework feedback to your students. 
            The goal is not just to show the right answer, but to explain how to reach it, point out common errors, and motivate them.

            Question: {question.get('title', 'Untitled')}
            Description: {question.get('description', 'N/A')}

            Correct answer: {correct_display}

            Student responses: {responses}

            Write feedback in this structure:
            - Start with a warm note (e.g., "Good effort everyone!" or "I can see you all tried hard here.").
            - Give the correct answer clearly.
            - Show the step-by-step method to reach the correct answer.
            - Point out one or two common mistakes learners made based on the responses.
            - End with encouragement and advice to improve.

            Keep it short (100–150 words), natural, and supportive, like a teacher speaking directly to students.
            Use markdown with headings: ## Feedback, ## Steps to Solve, ## Common Mistakes, ## Keep Practicing
            """

    try:
        response = ollama.chat(model=FEEDBACK_MODEL, messages=[{"role": "user", "content": prompt}])

        feedback = None
        if "message" in response and "content" in response["message"]:
            feedback = response["message"]["content"]
        elif "messages" in response and isinstance(response["messages"], list):
            feedback = response["messages"][-1].get("content", "")
        else:
            log("ERROR", f"Unexpected Ollama response format: {response}")
            return f"## Feedback Generation Error\nUnable to generate feedback.\n\n**Correct Answer:** {correct_display}"

        feedback = feedback.strip()
        feedback = ''.join(c for c in feedback if c.isprintable() or c in '\n\t')
        log("DEBUG", f"Feedback generated successfully for Q{question.get('index', '?')}")
        return feedback

    except Exception as e:
        log("ERROR", f"Failed to generate feedback for Q{question.get('index', '?')}: {e}")
        return f"## Feedback Generation Error\nUnable to generate AI feedback.\n\n**Correct Answer:** {correct_display}"



def generate_form_feedback(form_id, form_title, form_questions):
    """
    Generate a concise, learner-focused feedback report.
    Shows question, description, a single canonical correct answer, and a step-by-step explanation from Ollama.
    """
    log("INFO", f"Generating feedback report for form {form_id}")
    
    feedback_dir = "Feedback"
    if not os.path.exists(feedback_dir):
        os.makedirs(feedback_dir)
        log("INFO", "Created Feedback directory.")

    safe_title = sanitize_filename(form_title)
    report_filename = f"{safe_title}.md"
    report_path = os.path.join(feedback_dir, report_filename)
    
    content = ""

    for q_data in form_questions:
        question = q_data['question']
        correct_answers = q_data.get('correct_answers', [])
        
        q_index = question.get('index', '?')
        q_title = question.get('title', 'Untitled')
        q_desc = question.get('description', '')

        # Choose a canonical answer to display (shortest variant)
        canonical_answer = None
        if correct_answers:
            candidates = [str(a).strip() for a in correct_answers if a and isinstance(a, (str,))]
            if candidates:
                canonical_answer = min(candidates, key=lambda s: len(s))
        canonical_answer = canonical_answer or (", ".join(correct_answers) if correct_answers else "None")

        # Question header (no long list of variants)
        content += f"## Question {q_index}: {q_title}\n"
        if q_desc:
            content += f"**Description:** {q_desc}\n"
        content += f"**Correct Answer:** {canonical_answer}\n"

        # Generate a numbered step-by-step solution from Ollama
        feedback = generate_question_feedback(question, q_data.get('responses', []), correct_answers)
        content += f"**Explanation / Steps:**\n{feedback}\n\n---\n\n"

    try:
        with open(report_path, 'a', encoding='utf-8') as f:
            f.write(content)
        log("INFO", f"Feedback report saved to {report_path}")
        return report_path
    except Exception as e:
        log("ERROR", f"Failed to save feedback report for form {form_id}: {e}")
        return None

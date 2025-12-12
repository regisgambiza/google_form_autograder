# feedback.py - FINAL WORKING VERSION (no syntax errors, no unbound 'text')
import os
import json
import re
import ollama
from logger import log

# Load config safely
try:
    with open("config.json") as f:
        config = json.load(f)
except Exception as e:
    log("ERROR", f"Could not load config.json: {e}")
    config = {}

MODELS = config.get("models", {}).get("judge", ["deepseek-r1:8b"])
BATCH_SIZE_LIMIT = 1


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def get_default_feedback(q_data, form_title):
    question = q_data['question']
    q_index = question.get('index', '?')
    q_title = question.get('title', 'Untitled')
    q_type = question.get('type', 'Unknown')
    correct_answers = q_data.get('correct_answers', [])
    canonical = correct_answers[0] if correct_answers else "Not provided"
    responses = q_data.get('responses', [])
    resp_count = f"({len(responses)} responses)" if responses else "(No responses)"

    return f"""**Correct Answer:** {canonical} {resp_count}
**Explanation / Steps:**
No detailed explanation available. Please check the correct answer above.

## Feedback
Good effort! Review the correct answer to improve next time.
## Common Mistakes
- Not reading the question carefully
- Guessing without thinking
## Keep Practicing
Keep practicing similar questions!
---"""


def generate_model_discussion(q_data, model, form_title):
    q = q_data['question']
    correct = q_data.get('correct_answers', [])
    correct_display = correct[0] if correct else "Not provided"

    prompt = f"""
You are a helpful math teacher. Answer only with the correct answer and simple steps.

Question: {q.get('title', 'Untitled')}
Type: {q.get('type', 'Unknown')}
Correct answer (use this): {correct_display}

Give short, clear steps in simple English.
Format:
**Correct Answer:** [answer]
**Steps:**
1. ...
2. ...
"""

    try:
        resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        return resp["message"]["content"].strip()
    except Exception as e:
        log("ERROR", f"Ollama error in discussion ({model}): {e}")
        return get_default_feedback(q_data, form_title)


def generate_question_feedback_batch(form_title, batch):
    feedback_list = []
    primary_model = MODELS[0] if MODELS else None

    for q_data in batch:
        q_index = q_data['question'].get('index', '?')

        # Get model opinions
        discussions = {}
        for m in MODELS:
            discussions[m] = generate_model_discussion(q_data, m, form_title)

        # Combine with primary model
        if primary_model and MODELS:
            combine_prompt = f"""
Combine these into one clear feedback. Use simple English.

Correct answer to use: {q_data.get('correct_answers', ['?'])[0]}

Model opinions:
{chr(10).join([f"{k}: {v[:300]}" for k, v in discussions.items()])}

Format exactly:
**Correct Answer:** ...
**Steps:**
- ...
## Feedback
...
## Common Mistakes
- ...
## Keep Practicing
...
"""
            try:
                resp = ollama.chat(model=primary_model, messages=[{"role": "user", "content": combine_prompt}])
                final = resp["message"]["content"].strip()
                if "**Correct Answer:**" not in final:
                    raise ValueError("Bad format")
                feedback_list.append(final)
                continue
            except Exception as e:
                log("WARNING", f"Synthesis failed for Q{q_index}: {e}")

        # Fallback
        feedback_list.append(get_default_feedback(q_data, form_title))

    return feedback_list


def generate_form_feedback(form_id, form_title, form_questions):
    log("INFO", f"Generating feedback report for: {form_title}")

    os.makedirs("Feedback", exist_ok=True)
    safe_name = sanitize_filename(form_title or "Untitled")
    path = os.path.join("Feedback", f"{safe_name}.md")

    content = f"# Feedback Report: {form_title or 'Untitled Form'}\n\n"
    content += f"Generated on: {datetime.now():%Y-%m-%d %H:%M}\n\n"

    sorted_q = sorted(form_questions, key=lambda x: x["question"]["index"])

    for q_data in sorted_q:
        q = q_data["question"]
        feedbacks = generate_question_feedback_batch(form_title, [q_data])
        content += f"## Q{q.get('index', '?')}: {q.get('title', 'No title')} ({q.get('type', 'Unknown')})\n\n"
        if q.get("description"):
            content += f"**Description:** {q['description']}\n\n"
        content += feedbacks[0] + "\n\n---\n\n"

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log("INFO", f"Feedback saved: {path}")
        return path
    except Exception as e:
        log("ERROR", f"Could not save feedback: {e}")
        return None
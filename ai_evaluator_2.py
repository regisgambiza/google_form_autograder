def prepend_ai_report_to_feedback(feedback_file_path, ai_report_path):
    """
    Prepend the contents of ai_report_path to the top of feedback_file_path.
    """
    try:
        with open(ai_report_path, "r", encoding="utf-8") as ai_file:
            ai_content = ai_file.read()
        with open(feedback_file_path, "r", encoding="utf-8") as feedback_file:
            feedback_content = feedback_file.read()
        with open(feedback_file_path, "w", encoding="utf-8") as feedback_file:
            feedback_file.write(ai_content + "\n" + feedback_content)
        log("INFO", f"Prepended AI report from {ai_report_path} to {feedback_file_path}")
        return True
    except Exception as e:
        log("ERROR", f"Failed to prepend AI report: {e}")
        return False
# You will need to import your AI model/chat API here, e.g., ollama, openai, etc.
# from ollama import chat

def generate_ai_feedback_report(form_id, form_title, questions):
    """
    Generate a feedback report for any question type using AI, in the specified markdown format.
    Each question dict should have: 'question' (dict), 'responses' (list), 'correct_answers' (list)
    """
    log("DEBUG", f"Starting AI feedback report generation for form_id={form_id}, form_title='{form_title}' with {len(questions)} non-text questions.")
    feedback_dir = "Feedback"
    if not os.path.exists(feedback_dir):
        os.makedirs(feedback_dir)
        log("INFO", "Created Feedback directory.")

    safe_title = form_title.replace('/', '_').replace('\\', '_').replace(':', '').replace('?', '').replace('*', '').replace('"', '').replace('<', '').replace('>', '').replace('|', '').strip()
    report_filename = f"{safe_title}_ai_report.md"
    report_path = os.path.join(feedback_dir, report_filename)
    log("DEBUG", f"AI feedback report will be saved to: {report_path}")

    content = ""
    import ollama
    FEEDBACK_MODEL = "gpt-oss:20b"
    for idx, q_data in enumerate(questions):
        question = q_data['question']
        responses = q_data.get('responses', [])
        correct_answers = q_data.get('correct_answers', [])
        q_index = question.get('index', '?')
        q_title = question.get('title', 'Untitled')
        q_type = question.get('type', 'Unknown')
        correct_display = correct_answers[0] if correct_answers else "Not provided"
        log("DEBUG", f"Processing non-text question {idx+1}/{len(questions)}: index={q_index}, title='{q_title}', type={q_type}, responses={len(responses)}")

        # Build AI prompt
        prompt = f"""
You are a teacher writing feedback for Grade 7-8 Thai learners. Use simple English. Format your answer in markdown like this:

## Question {q_index}: {q_title}
**Correct Answer:** {correct_display}
**Explanation / Steps:**
## Feedback
[Write a friendly, short feedback.]

## Steps to Solve
[List steps simply.]

## Common Mistakes
[List common mistakes as bullet points.]

## Keep Practicing
[Give encouragement and a tip.]

---

Here are the student responses: {responses}
"""
        log("DEBUG", f"Sending prompt to AI model for question index={q_index}")
        try:
            response = ollama.chat(model=FEEDBACK_MODEL, messages=[{"role": "user", "content": prompt}])
            feedback_md = None
            if "message" in response and "content" in response["message"]:
                feedback_md = response["message"]["content"]
                log("DEBUG", f"AI feedback received for question index={q_index} (message)")
            elif "messages" in response and isinstance(response["messages"], list):
                feedback_md = "".join([m.get("content", "") for m in response["messages"]])
                log("DEBUG", f"AI feedback received for question index={q_index} (messages list)")
            else:
                feedback_md = "[AI feedback unavailable]"
                log("ERROR", f"AI feedback unavailable for question index={q_index}")
        except Exception as e:
            log("ERROR", f"AI feedback generation failed for Q{q_index}: {e}")
            feedback_md = "[AI feedback error]"
        content += feedback_md + "\n"

    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
        log("INFO", f"AI feedback report saved to {report_path}")
        return report_path
    except Exception as e:
        log("ERROR", f"Failed to write AI feedback report: {e}")
        return None
from logger import log
import os

def prepend_ai_report_to_feedback(feedback_file_path, ai_report_path):
    log("DEBUG", f"Attempting to prepend AI report from {ai_report_path} to {feedback_file_path}")
    try:
        with open(ai_report_path, "r", encoding="utf-8") as ai_file:
            ai_content = ai_file.read()
        log("DEBUG", f"Read {len(ai_content)} characters from AI report file.")
        with open(feedback_file_path, "r", encoding="utf-8") as feedback_file:
            feedback_content = feedback_file.read()
        log("DEBUG", f"Read {len(feedback_content)} characters from feedback file.")
        with open(feedback_file_path, "w", encoding="utf-8") as feedback_file:
            feedback_file.write(ai_content + "\n" + feedback_content)
        log("INFO", f"Prepended AI report from {ai_report_path} to {feedback_file_path}")
        return True
    except Exception as e:
        log("ERROR", f"Failed to prepend AI report: {e}")
        return False
    report_path = os.path.join(feedback_dir, report_filename)

    content = f"# Feedback for Other Question Types in {form_title}\n\n"
    for q_data in other_questions:
        question = q_data['question']
        responses = q_data.get('responses', [])
        q_index = question.get('index', '?')
        q_title = question.get('title', 'Untitled')
        q_type = question.get('type', 'Unknown')
        content += f"## Question {q_index}: {q_title} ({q_type})\n"
        if not responses:
            content += "No responses collected for this question.\n\n"
            continue
        # Simple feedback for non-text types
        response_summary = {}  # Count each response
        for ans in responses:
            response_summary[ans] = response_summary.get(ans, 0) + 1
        content += "Responses summary:\n"
        for ans, count in response_summary.items():
            content += f"- {ans}: {count} responses\n"
        content += "\nFeedback: Good job! Review the correct answer and try to understand why it is correct.\n\n"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
        log("INFO", f"Other types feedback report saved to {report_path}")
        return report_path
    except Exception as e:
        log("ERROR", f"Failed to write other types feedback report: {e}")
        return None

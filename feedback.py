import os
import json
from datetime import datetime
from logger import log
import ollama

# Load config for models
with open("config.json") as f:
    config = json.load(f)

MODELS = config["models"]
FEEDBACK_MODEL = MODELS.get("feedback", "llama3.1")  # Default to llama3.1 or first judge model if not specified

def generate_question_feedback(question, responses, correct_answers):
    """
    Generate human-readable feedback for a single question using Ollama.
    Returns a markdown-formatted string.
    """
    log("DEBUG", f"Generating feedback for Q{question.get('index', '?')}")
    
    if not responses:
        return "## No Responses\nNo responses collected for this question."
    
    # Prepare data summary
    num_responses = len(responses)
    num_correct = len(correct_answers) if correct_answers else 0
    unique_responses = list(set(responses))
    response_counts = {ans: responses.count(ans) for ans in unique_responses}
    
    correct_str = ", ".join(correct_answers) if correct_answers else "No correct answers identified"
    responses_str = "\n".join([f"- {ans} ({count} responses)" for ans, count in sorted(response_counts.items(), key=lambda x: x[1], reverse=True)])
    
    prompt = f"""
Question: {question.get('title', 'Untitled')}
Description: {question.get('description', 'N/A')}

Correct answers: {correct_str}

All responses ({num_responses} total):
{responses_str}

Generate helpful, encouraging feedback for learners. Focus on:
- What the correct answers teach (key concepts).
- Common mistakes or partial understandings in responses.
- Tips for improvement.
- Positive reinforcement.

Keep it concise (200-300 words), engaging, and suitable for students.
Format as markdown with headings: ## Key Concepts, ## Common Challenges, ## Tips to Improve, ## Keep Going!
"""
    
    try:
        response = ollama.chat(model=FEEDBACK_MODEL, messages=[{"role": "user", "content": prompt}])
        feedback = response['message']['content'].strip()
        # Clean up any non-printable chars
        feedback = ''.join(c for c in feedback if c.isprintable() or c in '\n\t')
        log("DEBUG", f"Feedback generated successfully for Q{question.get('index', '?')}")
        return feedback
    except Exception as e:
        log("ERROR", f"Failed to generate feedback for Q{question.get('index', '?')}: {e}")
        return f"## Feedback Generation Error\nUnable to generate AI feedback. Please review responses manually.\n\n**Correct Answers:** {correct_str}\n\n**Responses:**\n{responses_str}"

def generate_form_feedback(form_id, form_questions):
    """
    Generate a feedback report for one or more questions in a Google Form.
    form_questions: list of dicts, each with keys: 'question' (dict from form_utils), 'responses' (list), 'correct_answers' (list)
    
    Saves to Feedback/feedback_form_{form_id}_q{index}.md for single question, or feedback_form_{form_id}.md for multiple questions.
    Returns path to the generated file.
    """
    log("INFO", f"Generating feedback report for form {form_id}")
    
    # Create Feedback folder if absent
    feedback_dir = "Feedback"
    if not os.path.exists(feedback_dir):
        os.makedirs(feedback_dir)
        log("INFO", f"Created Feedback directory.")
    
    # Determine filename based on number of questions
    if len(form_questions) == 1:
        q_index = form_questions[0]['question'].get('index', 'unknown')
        report_filename = f"feedback_form_{form_id}_q{q_index}.md"
    else:
        report_filename = f"feedback_form_{form_id}.md"
    report_path = os.path.join(feedback_dir, report_filename)
    
    # Prepare report content
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"# Feedback Report for Google Form {form_id}\n"
    content += f"**Generated on:** {timestamp}\n\n"
    content += f"This report provides feedback for {'question' if len(form_questions) == 1 else 'each question'} in the form. It includes correct answers, response summaries, and AI-generated insights for learners.\n\n"
    
    for q_data in form_questions:
        question = q_data['question']
        responses = q_data['responses']
        correct_answers = q_data['correct_answers']
        
        q_index = question.get('index', '?')
        q_title = question.get('title', 'Untitled')
        q_desc = question.get('description', '')
        q_type = question.get('type', 'Unknown')
        
        content += f"## Question {q_index}: {q_title}\n"
        content += f"**Type:** {q_type}\n"
        if q_desc:
            content += f"**Description:** {q_desc}\n"
        
        content += f"**Correct Answer(s):** {', '.join(correct_answers) if correct_answers else 'None identified'}\n\n"
        
        content += f"**Response Summary:**\n"
        if responses:
            unique_responses = list(set(responses))
            response_counts = {ans: responses.count(ans) for ans in unique_responses}
            for ans, count in sorted(response_counts.items(), key=lambda x: x[1], reverse=True):
                content += f"- '{ans}' ({count} responses)\n"
        else:
            content += "- No responses\n"
        content += "\n"
        
        # Generate and add AI feedback
        feedback = generate_question_feedback(question, responses, correct_answers)
        content += f"{feedback}\n\n---\n\n"
    
    # Write to file
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(content)
        log("INFO", f"Feedback report saved to {report_path}")
        return report_path
    except Exception as e:
        log("ERROR", f"Failed to save feedback report for form {form_id}: {e}")
        return None
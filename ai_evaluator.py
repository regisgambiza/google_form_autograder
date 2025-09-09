import json
import ollama
from logger import log
import re

# Load config
with open("config.json") as f:
    config = json.load(f)

MODELS = config["models"]

def evaluate_answers_batch(question, responses):
    log("DEBUG", f"Evaluating all answers for Q{question['index']} using model {MODELS['judge']}...")
    if not responses:
        log("DEBUG", "No responses found, skipping evaluation.")
        return []
    
    unique_answers = list(set(responses))
    log("DEBUG", f"Found {len(unique_answers)} unique answers to evaluate")
    
    # Create a mapping of numbers to answers for reference
    answer_map = {str(i): ans for i, ans in enumerate(unique_answers, 1)}
    log("DEBUG", f"Answer mapping: {answer_map}")
    
    # Create a prompt that includes all answers for batch evaluation
    base_prompt = f"Question: {question['title']}\n"
    if "description" in question and question["description"]:
        base_prompt += f"Description: {question['description']}\n"
    
    base_prompt += "\nBelow is a list of answers provided by students who are learners in Thailand and may have poor English skills. Be extremely lenient in evaluation: ignore spelling errors, grammar mistakes, extra spaces, capitalization differences, and accept approximate or close answers that convey the correct meaning. For example, accept 'rectanglr' or 'a recangle' as correct if the intended answer is 'rectangle'. Evaluate each answer and return ONLY a JSON object with the answer NUMBER as key and 'YES' or 'NO' as value, indicating whether the answer is correct under this lenient criteria.\n\n"
    
    for i, ans in enumerate(unique_answers, 1):
        base_prompt += f"{i}. {ans}\n"
    
    base_prompt += "\nReturn ONLY the JSON object with no additional text, comments, or explanations."

    try:
        # Use only the judge model for evaluation
        judge_model = MODELS["judge"]
        if isinstance(judge_model, list):
            judge_model = judge_model[0]
            
        response = ollama.chat(model=judge_model, messages=[{"role": "user", "content": base_prompt}])
        response_text = response['message']['content']
        log("DEBUG", f"Batch evaluation response: {response_text}")
        
        # Clean the response to remove comments and extra text
        # Remove single-line comments (// ...)
        response_text = re.sub(r'//.*?(?=\n|$)', '', response_text)
        # Remove multi-line comments (/* ... */)
        response_text = re.sub(r'/\*.*?\*/', '', response_text, flags=re.DOTALL)
        # Remove inline comments after "YES" or "NO" (e.g., "NO" (some text))
        response_text = re.sub(r'("YES"|"NO")\s*\([^)]*\)', r'\1', response_text)
        # Remove any extra whitespace
        response_text = response_text.strip()
        
        # Parse the JSON response
        try:
            # Extract JSON from the response (in case there's extra text)
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                response_text = response_text[json_start:json_end]
            
            evaluations = json.loads(response_text)
            
            # Map numbers back to actual answers
            correct_answers = [answer_map[num] for num, is_correct in evaluations.items() 
                              if is_correct.upper() == "YES" and num in answer_map]
            
            log("DEBUG", f"Batch evaluation results: {correct_answers}")
            return correct_answers
        except json.JSONDecodeError as e:
            log("ERROR", f"Failed to parse JSON response: {e}. Response was: {response_text}")
            return fallback_individual_evaluation(question, unique_answers)

    except Exception as e:
        log("ERROR", f"Ollama error during batch evaluation: {str(e)}")
        return fallback_individual_evaluation(question, unique_answers)

def fallback_individual_evaluation(question, answers):
    """Fallback to individual evaluation if batch processing fails"""
    log("DEBUG", "Falling back to individual evaluation")
    correct_answers = []
    for ans in answers:
        if is_correct_individual(question, ans):
            correct_answers.append(ans)
    return correct_answers

def is_correct_individual(question, ans):
    """Evaluate a single answer (used as fallback)"""
    base_prompt = f"Question: {question['title']}\n"
    if "description" in question and question["description"]:
        base_prompt += f"Description: {question['description']}\n"
    base_prompt += f"Answer: {ans}\n"
    base_prompt += "The student is a learner in Thailand and may have poor English skills. Be extremely lenient: ignore spelling errors, grammar mistakes, extra spaces, capitalization differences, and accept approximate or close answers that convey the correct meaning. For example, accept 'rectanglr' or 'a recangle' as correct if the intended answer is 'rectangle'. Is this answer correct under this lenient criteria? Reason step by step and conclude with exactly 'YES' or 'NO'."

    try:
        judge_model = MODELS["judge"]
        if isinstance(judge_model, list):
            judge_model = judge_model[0]
            
        response = ollama.chat(model=judge_model, messages=[{"role": "user", "content": base_prompt}])
        response_text = response['message']['content']
        
        # Extract the conclusion (last word should be YES or NO)
        conclusion = response_text.strip().upper().split()[-1]
        return conclusion == "YES"

    except Exception as e:
        log("ERROR", f"Ollama error during individual evaluation for answer '{ans}': {str(e)}")
        return False

# Keep the original function name for compatibility
evaluate_answers = evaluate_answers_batch
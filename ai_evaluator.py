import json
import ollama
from logger import log
import re
import unicodedata
import os
from sympy import sympify, simplify

# Load config
with open("config.json") as f:
    config = json.load(f)

MODELS = config["models"]
LENIENCY = config.get("leniency", "lenient").lower()
BATCH_SIZE_LIMIT = 20  # Maximum answers per batch to avoid token limits

# --- Helpers ---

def normalize_text(s):
    if not s:
        return ""
    s = str(s)
    s = ''.join(c for c in s if unicodedata.category(c)[0] != 'C')
    s = re.sub(r'\s+', ' ', s)
    return s.strip().lower()

def parse_number_if_possible(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None

def normalized_similarity(a, b):
    """Very simple token overlap similarity"""
    if not a or not b:
        return 0.0
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def clean_expr(expr: str) -> str:
    if not expr:
        return ""
    expr = str(expr)
    expr = expr.replace("×", "*").replace("·", "*")
    expr = expr.replace(" ", "")
    expr = expr.lower()
    expr = re.sub(r'(\d)([a-z])', r'\1*\2', expr)
    expr = re.sub(r'(\))([0-9a-z])', r'\1*\2', expr)
    return expr.strip()

def algebra_equal(a, b):
    try:
        ca, cb = clean_expr(a), clean_expr(b)
        return simplify(sympify(ca) - sympify(cb)) == 0
    except Exception:
        return False

def get_model_vote(model, question, answers, leniency, retries=1):
    """
    Send a batch of answers for a question to the model and get decisions for all.
    Returns a list of (decision, raw_response) tuples, one for each answer.
    """
    prompt = f"""
Question: {question.get("title")}

Answers to evaluate (exactly {len(answers)} answers):
{'\n'.join([f"Answer {i}: {ans}" for i, ans in enumerate(answers, 1)])}

Be {leniency.upper()} in judging correctness:
- EXTREME: Always YES unless totally unrelated nonsense.
- LENIENT: Accept if partially correct or similar.
- BALANCED: Accept if very similar or matches exactly.
- STRICT: Only accept if exact and precise.

Return ONLY a JSON array with exactly {len(answers)} elements, each being {{"decision": "YES" or "NO"}} corresponding to each answer in order.
Example: [{{"decision": "YES"}}, {{"decision": "NO"}}, {{"decision": "YES"}}]
DO NOT include any additional text, explanations, or comments outside the JSON array.
DO NOT return fewer or more than {len(answers)} decisions.
"""
    log("DEBUG", f"Sending {len(answers)} answers to {model}: {[ans for ans in answers]}")
    attempt = 0
    while attempt <= retries:
        try:
            response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
            text = response['message']['content']
            text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C').strip()
            
            decisions = json.loads(text)
            if not isinstance(decisions, list):
                raise ValueError("Model response is not a list")

            if len(decisions) < len(answers):
                log("WARNING", f"Model {model} returned {len(decisions)} decisions, expected {len(answers)}. Padding with NO.")
                while len(decisions) < len(answers):
                    decisions.append({"decision": "NO"})
            elif len(decisions) > len(answers):
                log("WARNING", f"Model {model} returned {len(decisions)} decisions, expected {len(answers)}. Trimming extra entries.")
                decisions = decisions[:len(answers)]

            for d in decisions:
                if not isinstance(d, dict) or "decision" not in d or d["decision"] not in ["YES", "NO"]:
                    raise ValueError("Invalid decision format in model response")
            log("DEBUG", f"Model {model} processed {len(answers)} answers successfully")
            return [(d["decision"], text) for d in decisions]
        except json.JSONDecodeError as e:
            log("DEBUG", f"Attempt {attempt+1}/{retries+1}: Error parsing JSON from {model}: {e}. Raw response: {text}")
            attempt += 1
            if attempt > retries:
                log("WARNING", f"Max retries reached for {model}. Falling back to NO for all answers.")
                return [("NO", text) for _ in answers]
        except ValueError as e:
            log("DEBUG", f"Attempt {attempt+1}/{retries+1}: Invalid response format from {model}: {e}. Raw response: {text}")
            attempt += 1
            if attempt > retries:
                log("WARNING", f"Max retries reached for {model}. Falling back to NO for all answers.")
                return [("NO", text) for _ in answers]
        except Exception as e:
            log("DEBUG", f"Attempt {attempt+1}/{retries+1}: Error calling {model}: {e}")
            attempt += 1
            if attempt > retries:
                log("WARNING", f"Max retries reached for {model}. Falling back to NO for all answers.")
                return [("NO", text) for _ in answers]

# --- Main evaluator ---

def evaluate_answers_batch(question, responses):
    log("DEBUG", f"Evaluating Q{question.get('index','?')} with leniency={LENIENCY} and judges={MODELS['judge']}")
    if not responses:
        return []

    # Filter out low-quality answers (e.g., too short or non-meaningful)
    filtered_out = [ans for ans in responses if not (len(str(ans).strip()) > 2 and str(ans).strip().lower() not in ["idk", "k"])]
    filtered_responses = [ans for ans in responses if ans not in filtered_out]

    log("DEBUG", f"Filtered out {len(filtered_out)} low-quality answers: {filtered_out}")
    log("DEBUG", f"Remaining after filter: {len(filtered_responses)}")

    # Deduplicate answers but keep first occurrence order
    seen = set()
    unique_answers = []
    duplicates = []
    for ans in filtered_responses:
        if ans not in seen:
            seen.add(ans)
            unique_answers.append(ans)
        else:
            duplicates.append(ans)

    log("DEBUG", f"Duplicates {len(duplicates)} {duplicates}")

    answer_map = {str(i): ans for i, ans in enumerate(unique_answers, 1)}
    log("DEBUG", f"Processing {len(unique_answers)} unique answers in batch")
    judges = MODELS["judge"]
    if not isinstance(judges, list):
        judges = [judges]

    accepted = []

    # Thresholds
    NUMERIC_VETO_ABS = {"extreme": 1e9, "lenient": 5.0, "balanced": 1.0, "strict": 0.001}
    SIMILARITY_ACCEPT_THRESH = {"extreme": 0.2, "lenient": 0.5, "balanced": 0.75, "strict": 0.9}

    expected_raw = question.get("expected", None)
    expected_num = parse_number_if_possible(expected_raw) if expected_raw is not None else None
    expected_norm = normalize_text(expected_raw) if expected_raw is not None else None

    # Split answers into batches if necessary
    answer_batches = [unique_answers[i:i + BATCH_SIZE_LIMIT] for i in range(0, len(unique_answers), BATCH_SIZE_LIMIT)]
    log("DEBUG", f"Split {len(unique_answers)} answers into {len(answer_batches)} batches")

    # Get votes for all answers from each model
    all_votes = [[] for _ in unique_answers]
    for batch_idx, answer_batch in enumerate(answer_batches):
        log("DEBUG", f"Processing batch {batch_idx + 1}/{len(answer_batches)} with {len(answer_batch)} answers")
        for model in judges:
            votes = get_model_vote(model, question, answer_batch, LENIENCY)
            batch_start_idx = batch_idx * BATCH_SIZE_LIMIT
            for i, vote in enumerate(votes):
                all_votes[batch_start_idx + i].append(vote)

    for idx, ans in answer_map.items():
        ans_norm = normalize_text(ans)
        ans_num = parse_number_if_possible(ans)
        log("DEBUG", f"Checking answer {idx} ({ans}) parsed_num={ans_num}")

        votes = all_votes[int(idx)-1]
        vote_decisions = [v[0] for v in votes]
        yes_count = vote_decisions.count("YES")
        n_judges = len(judges)
        log("DEBUG", f"Opinions for {idx} ({ans}): {vote_decisions}")

        local_similarity = 0.0
        if expected_raw is not None:
            if expected_num is not None and ans_num is not None:
                try:
                    diff = abs(ans_num - expected_num)
                    denom = max(abs(expected_num), 1.0)
                    local_similarity = max(0.0, 1.0 - (diff / (denom + 1e-9)))
                except Exception:
                    local_similarity = 0.0
            else:
                local_similarity = normalized_similarity(ans_norm, expected_norm)
            
            if algebra_equal(ans, expected_raw):
                local_similarity = 1.0

            log("DEBUG", f"Local similarity (vs expected): {local_similarity:.3f}")

        numeric_veto = False
        if expected_num is not None and ans_num is not None:
            abs_diff = abs(ans_num - expected_num)
            if abs_diff > NUMERIC_VETO_ABS.get(LENIENCY, 1.0):
                numeric_veto = True
                log("DEBUG", f"Numeric veto triggered for {idx} ({ans}): diff={abs_diff}")

        decision = False
        if LENIENCY == "extreme":
            decision = yes_count >= 1 or local_similarity >= SIMILARITY_ACCEPT_THRESH["extreme"]
        elif LENIENCY == "lenient":
            decision = (yes_count >= ((n_judges // 2) + 1) or 
                        local_similarity >= SIMILARITY_ACCEPT_THRESH["lenient"]) and not numeric_veto
        elif LENIENCY == "balanced":
            decision = (yes_count == n_judges or 
                        local_similarity >= SIMILARITY_ACCEPT_THRESH["balanced"]) and not numeric_veto
        else:  # strict
            decision = (yes_count == n_judges and 
                        (expected_raw is None or local_similarity >= SIMILARITY_ACCEPT_THRESH["strict"])) and not numeric_veto

        if decision:
            accepted.append(ans)
            log("DEBUG", f"Answer {idx} ({ans}) → YES")
        else:
            log("DEBUG", f"Answer {idx} ({ans}) → NO")

    log("DEBUG", f"Final accepted answers: {accepted}")
    return accepted

# --- Compatibility alias ---
evaluate_answers = evaluate_answers_batch

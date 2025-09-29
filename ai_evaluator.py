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

def get_model_vote(model, question, answers, leniency, retries=3):
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
DO NOT use <think> tags, reasoning, explanations, or any text outside the JSON array. Start directly with [.
Example: [{{"decision": "YES"}}, {{"decision": "NO"}}]
"""

    for attempt in range(1, retries + 1):
        try:
            response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
            text = response['message']['content']
            text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C').strip()
            
            # Strip <think> tags and any text before/after JSON
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.search(r'\[.*\]', text, re.DOTALL)
            if text:
                text = text.group(0).strip()
            else:
                raise ValueError("No JSON array found in response")
            
            decisions = json.loads(text)
            
            # Validate and fix decisions
            if len(decisions) != len(answers):
                raise ValueError(f"Expected {len(answers)} decisions, got {len(decisions)}")
            for d in decisions:
                decision = d.get("decision", "NO").upper()
                if decision not in ["YES", "NO"]:
                    log("WARNING", f"Invalid decision '{decision}' from {model}. Treating as NO.")
                    d["decision"] = "NO"
            
            return [(d["decision"], text) for d in decisions]
        except Exception as e:
            log("DEBUG", f"Attempt {attempt}/{retries}: Error parsing JSON from {model}: {str(e)}. Raw response: {text[:200]}...")
            if attempt == retries:
                log("WARNING", f"Max retries reached for {model}. Falling back to NO for all answers.")
                return [("NO", text) for _ in answers]
    
    return [("NO", "") for _ in answers]

def evaluate_answers_batch(question, answers, expected=None):
    """Evaluate a batch of answers for a single question."""
    log("DEBUG", f"Evaluating Q{question.get('index', '?')} (leniency={LENIENCY})")
    
    if not answers:
        log("INFO", "No answers to evaluate. Returning empty list.")
        return []

    expected_raw = expected[0] if expected else None
    expected_norm = normalize_text(expected_raw) if expected_raw else None
    expected_num = parse_number_if_possible(expected_raw) if expected_raw else None

    judges = MODELS.get("judge", ["gpt-oss:20b"])
    log("DEBUG", f"Found {len(set(answers))} duplicates")
    unique_answers = list(set(answers))
    log("DEBUG", f"Processing {len(unique_answers)} unique answers")
    
    SIMILARITY_ACCEPT_THRESH = {
        "extreme": 0.01,
        "lenient": 0.3,
        "balanced": 0.7,
        "strict": 0.95
    }
    NUMERIC_VETO_ABS = {
        "extreme": 1000.0,
        "lenient": 1.0,
        "balanced": 0.5,
        "strict": 0.001
    }

    accepted = []
    batches = [unique_answers[i:i + BATCH_SIZE_LIMIT] for i in range(0, len(unique_answers), BATCH_SIZE_LIMIT)]
    log("DEBUG", f"Split into {len(batches)} batches")

    all_votes = []
    for batch_idx, batch in enumerate(batches, 1):
        log("DEBUG", f"Batch {batch_idx}/{len(batches)}: {len(batch)} answers")
        batch_votes = []
        for model in judges:
            log("DEBUG", f"Sending {len(batch)} answers to {model}: {batch}")
            votes = get_model_vote(model, question, batch, LENIENCY)
            if len(votes) != len(batch):
                log("ERROR", f"Model {model} returned {len(votes)} votes, expected {len(batch)}. Falling back to NO.")
                votes = [("NO", "") for _ in batch]
            batch_votes.append(votes)
            log("DEBUG", f"Model {model} processed {len(batch)} answers successfully")
        all_votes.extend(list(zip(*batch_votes)))

    for idx, ans in enumerate(unique_answers, 1):
        ans_norm = normalize_text(ans)
        ans_num = parse_number_if_possible(ans)
        votes = all_votes[int(idx)-1]
        vote_decisions = [v[0] for v in votes]
        yes_count = vote_decisions.count("YES")
        log("DEBUG", f"Answer {idx} ({ans}): {yes_count}/{len(judges)} YES")

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

            log("DEBUG", f"Similarity: {local_similarity:.3f}")

        numeric_veto = False
        if expected_num is not None and ans_num is not None:
            abs_diff = abs(ans_num - expected_num)
            if abs_diff > NUMERIC_VETO_ABS.get(LENIENCY, 1.0):
                numeric_veto = True
                log("DEBUG", f"Numeric veto: diff={abs_diff}")

        decision = False
        if LENIENCY == "extreme":
            decision = yes_count >= 1 or local_similarity >= SIMILARITY_ACCEPT_THRESH["extreme"]
        elif LENIENCY == "lenient":
            decision = (yes_count >= ((len(judges) // 2) + 1) or 
                        local_similarity >= SIMILARITY_ACCEPT_THRESH["lenient"]) and not numeric_veto
        elif LENIENCY == "balanced":
            decision = (yes_count == len(judges) or 
                        local_similarity >= SIMILARITY_ACCEPT_THRESH["balanced"]) and not numeric_veto
        else:  # strict
            decision = (yes_count == len(judges) and 
                        (expected_raw is None or local_similarity >= SIMILARITY_ACCEPT_THRESH["strict"])) and not numeric_veto

        if decision:
            accepted.append(ans)
            log("DEBUG", f"Answer {idx} ({ans}) → YES")
        else:
            log("DEBUG", f"Answer {idx} ({ans}) → NO")

    log("DEBUG", f"Accepted: {len(accepted)} answers")
    return accepted

# --- Compatibility alias ---
evaluate_answers = evaluate_answers_batch
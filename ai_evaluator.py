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
    """Normalize text by removing control characters and extra whitespace."""
    if not s:
        return ""
    s = str(s)
    s = ''.join(c for c in s if unicodedata.category(c)[0] != 'C')  # Remove control chars
    s = re.sub(r'\s+', ' ', s)  # Normalize whitespace
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
{chr(10).join([f"Answer {i}: {ans}" for i, ans in enumerate(answers, 1)])}

Be {leniency.upper()} in judging correctness, ignoring any units (e.g., 'c', 'degrees', '°C'):
- EXTREME: Always YES unless totally unrelated nonsense.
- LENIENT: Accept if partially correct or similar, ignoring units.
- BALANCED: Accept if very similar or matches the core value, ignoring units.
- STRICT: Only accept if the core value matches exactly, ignoring units.

Return your answer in **EXACTLY** this format:

[
  {{"decision": "YES"}},
  {{"decision": "NO"}},
  ...
]

- The array MUST contain exactly {len(answers)} objects.
- Each object MUST have only the key "decision".
- "decision" MUST be ONLY "YES" or "NO". NEVER respond with "IDK", "MAYBE", "UNKNOWN", "UNCLEAR", or any other value.
- Do NOT include any explanations, reasoning, text, or extra arrays.
- Do NOT output more or fewer than {len(answers)} objects.
"""

    for attempt in range(1, retries + 1):
        try:
            response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
            text = response['message']['content']
            text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C').strip()

            # Strip <think> tags
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)

            # Use non-greedy regex to capture only the first JSON array
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if match:
                text = match.group(0).strip()
            else:
                raise ValueError("No JSON array found in response")

            try:
                decisions = json.loads(text)
            except json.JSONDecodeError:
                # Try to salvage by splitting multiple arrays
                if '][' in text:
                    salvage_text = text.split('][')[0] + ']'
                    decisions = json.loads(salvage_text)
                    text = salvage_text
                else:
                    raise

            # Validate and fix decisions
            if len(decisions) != len(answers):
                raise ValueError(f"Expected {len(answers)} decisions, got {len(decisions)}")
            for d in decisions:
                decision = d.get("decision", "NO").upper().strip()
                # Explicitly reject IDK and other non-binary responses
                if decision in ["IDK", "I DON'T KNOW", "IDON'TKNOW", "MAYBE", "UNKNOWN", "UNCLEAR", "N/A", "NA"]:
                    log("WARNING", f"Invalid indecisive decision '{decision}' from {model}. Treating as NO.")
                    d["decision"] = "NO"
                elif decision not in ["YES", "NO"]:
                    log("WARNING", f"Invalid decision '{decision}' from {model}. Treating as NO.")
                    d["decision"] = "NO"
                else:
                    # Normalize valid decisions to uppercase
                    d["decision"] = decision

            return [(d["decision"], text) for d in decisions]
        except Exception as e:
            log("DEBUG", f"Attempt {attempt}/{retries}: Error parsing JSON from {model}: {str(e)}. Raw response: {text[:200]}...")
            if attempt == retries:
                log("WARNING", f"Max retries reached for {model}. Falling back to NO for all answers.")
                return [("NO", text) for _ in answers]

    return [("NO", "") for _ in answers]

def extract_number(s):
    """Extract the first numeric value from a string, ignoring units."""
    if not s:
        return None
    # Match number (with optional decimal) and ignore anything after
    match = re.search(r'-?\d+(\.\d+)?', str(s))
    return float(match.group()) if match else None

def evaluate_answers_batch(question, answers, expected=None):
    """Evaluate a batch of answers for a single question, ignoring units."""
    log("DEBUG", f"Evaluating Q{question.get('index', '?')} (leniency={LENIENCY})")

    if not answers:
        log("INFO", "No answers to evaluate. Returning empty list.")
        return []

    # Expected answer
    expected_raw = expected[0] if expected and len(expected) > 0 else None
    expected_num = extract_number(expected_raw) if expected_raw else None
    log("DEBUG", f"Expected numeric value: {expected_num}")

    judges = MODELS.get("judge", ["gpt-oss:20b"])
    unique_answers = list(set(answers))
    log("DEBUG", f"Processing {len(unique_answers)} unique answers")

    accepted = []

    # --- 1. AI model votes ---
    all_votes = []
    batches = [unique_answers[i:i + BATCH_SIZE_LIMIT] for i in range(0, len(unique_answers), BATCH_SIZE_LIMIT)]
    for batch_idx, batch in enumerate(batches, 1):
        log("DEBUG", f"Batch {batch_idx}/{len(batches)}: {len(batch)} answers")
        batch_votes = []
        for model in judges:
            votes = get_model_vote(model, question, batch, LENIENCY)
            if len(votes) != len(batch):
                log("ERROR", f"Model {model} returned {len(votes)} votes, expected {len(batch)}. Falling back to NO.")
                votes = [("NO", "") for _ in batch]
            batch_votes.append(votes)
            log("DEBUG", f"Model {model} processed {len(batch)} answers successfully")
        all_votes.extend(list(zip(*batch_votes)))

    # --- 2. Numeric comparison (ignoring units) ---
    for idx, ans in enumerate(unique_answers, 1):
        ans_num = extract_number(ans)
        votes = all_votes[idx - 1]
        vote_decisions = [v[0] for v in votes]
        yes_count = vote_decisions.count("YES")
        log("DEBUG", f"Answer {idx} ({ans}): {yes_count}/{len(judges)} YES")

        decision = False

        # Case A: Numeric match (ignore units)
        if expected_num is not None and ans_num is not None:
            if abs(ans_num - expected_num) < 1e-6:
                decision = True
                log("DEBUG", f"Answer {idx} ({ans}) → YES (numeric match: {ans_num} vs {expected_num})")

        # Case B: Fallback to AI votes for non-numeric answers
        if not decision:
            if yes_count >= ((len(judges) // 2) + 1):
                decision = True
                log("DEBUG", f"Answer {idx} ({ans}) → YES (AI vote)")

        if decision:
            accepted.append(ans)
        else:
            log("DEBUG", f"Answer {idx} ({ans}) → NO")

    log("DEBUG", f"Accepted: {len(accepted)} answers")
    return accepted

# --- Compatibility alias ---
evaluate_answers = evaluate_answers_batch
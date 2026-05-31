import json
import ollama
from logger import log
import re
import unicodedata
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import subprocess
from datetime import datetime, timezone
from sympy import sympify, simplify


def _write_heartbeat_if_needed():
    """Write heartbeat to file if it exists."""
    try:
        if os.path.exists("heartbeat.json"):
            data = {
                "last_update": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid()
            }
            with open("heartbeat.json", "w") as f:
                json.dump(data, f, indent=2)
    except Exception:
        pass


# Load config
with open("config.json") as f:
    config = json.load(f)

MODELS = config["models"].get("judge", ["gpt-oss:20b"])
LENIENCY = config.get("leniency", "lenient").lower()
DEFAULT_BATCH_SIZE = 32  # Recommended default batch size
MAX_PARALLEL_WORKERS = config.get("max_parallel_workers", 8)

_AUTO_WORKERS = None
_AUTO_BATCH_SIZE = None

def _detect_vram_gb():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return None
        # Take the first GPU if multiple are present
        mb = float(lines[0])
        return mb / 1024.0
    except Exception:
        return None

def _get_batch_size():
    try:
        with open("config.json") as f:
            cfg = json.load(f)
        val = cfg.get("batch_size", DEFAULT_BATCH_SIZE)
        if isinstance(val, str) and val.lower() == "auto":
            global _AUTO_BATCH_SIZE
            if _AUTO_BATCH_SIZE is None:
                _AUTO_BATCH_SIZE = DEFAULT_BATCH_SIZE
            return _AUTO_BATCH_SIZE, True
        if isinstance(val, int) and val > 0:
            return val, False
    except Exception:
        pass
    return DEFAULT_BATCH_SIZE, False

def _reduce_auto_batch_size(current_size):
    global _AUTO_BATCH_SIZE
    new_size = max(1, int(current_size) // 2)
    if _AUTO_BATCH_SIZE is None or new_size < _AUTO_BATCH_SIZE:
        _AUTO_BATCH_SIZE = new_size
        log("WARNING", f"Auto batch size reduced to {new_size} due to model output mismatch")
    return new_size

def _get_votes_with_split(model, question, batch, expected, leniency, is_auto):
    try:
        votes = get_model_vote(model, question, batch, expected, leniency, allow_fallback_no=False)
        if len(votes) != len(batch):
            raise ValueError(f"Expected {len(batch)} decisions, got {len(votes)}")
        return votes
    except Exception as e:
        if len(batch) <= 1:
            log("ERROR", f"Batch size 1 failed for {model}: {e}. Falling back to NO.")
            return [("NO", "") for _ in batch]
        if is_auto:
            _reduce_auto_batch_size(len(batch))
        mid = len(batch) // 2
        left = _get_votes_with_split(model, question, batch[:mid], expected, leniency, is_auto)
        right = _get_votes_with_split(model, question, batch[mid:], expected, leniency, is_auto)
        return left + right

def _resolve_max_workers(total_tasks):
    global _AUTO_WORKERS
    if isinstance(MAX_PARALLEL_WORKERS, str) and MAX_PARALLEL_WORKERS.lower() == "auto":
        if _AUTO_WORKERS is None:
            vram_gb = _detect_vram_gb()
            if vram_gb:
                _AUTO_WORKERS = max(2, min(12, int(vram_gb)))
                log("INFO", f"Auto-tuned max_parallel_workers={_AUTO_WORKERS} based on GPU VRAM={vram_gb:.1f} GB")
            else:
                _AUTO_WORKERS = 8
                log("WARNING", "Auto-tune failed to detect VRAM. Using max_parallel_workers=8")
        max_workers = min(_AUTO_WORKERS, total_tasks) if total_tasks > 0 else 1
        return max_workers

    if isinstance(MAX_PARALLEL_WORKERS, (int, float)) and MAX_PARALLEL_WORKERS > 0:
        return min(int(MAX_PARALLEL_WORKERS), total_tasks) if total_tasks > 0 else 1

    # Fallback default
    return min(8, total_tasks) if total_tasks > 0 else 1

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

def extract_number(s):
    """Extract the first numeric value from a string, ignoring units."""
    if not s:
        return None
    # Match number (with optional decimal) and ignore anything after
    match = re.search(r'-?\d+(\.\d+)?', str(s))
    return float(match.group()) if match else None

def get_model_vote(model, question, answers, expected, leniency, retries=3, allow_fallback_no=True):
    """
    Send a batch of answers for a question to the model and get decisions for all, comparing to expected.
    Returns a list of (decision, raw_response) tuples, one for each answer.
    """
    expected_str = ', '.join(expected) if expected else "Not provided"

    prompt = f"""
Question: {question.get("title")}

Correct Answers: {expected_str}

Answers to evaluate (exactly {len(answers)} answers):
{chr(10).join([f"Answer {i}: {ans}" for i, ans in enumerate(answers, 1)])}

Be {leniency.upper()} in judging if the answer matches any of the correct answers, ignoring any units (e.g., 'c', 'degrees', '°C'):
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
- "decision" MUST be either "YES" or "NO".
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
                decision = d.get("decision", "NO").upper()
                if decision not in ["YES", "NO"]:
                    log("WARNING", f"Invalid decision '{decision}' from {model}. Treating as NO.")
                    d["decision"] = "NO"

            return [(d["decision"], text) for d in decisions]
        except Exception as e:
            log("DEBUG", f"Attempt {attempt}/{retries}: Error parsing JSON from {model}: {str(e)}. Raw response: {text[:200]}...")
            if attempt == retries:
                if allow_fallback_no:
                    log("WARNING", f"Max retries reached for {model}. Falling back to NO for all answers.")
                    return [("NO", text) for _ in answers]
                raise

    return [("NO", "") for _ in answers]

def evaluate_answers_batch(question, answers, expected=None):
    """Evaluate a batch of answers for a single question using AI models to compare against expected answers, ignoring units."""
    log("DEBUG", f"Evaluating Q{question.get('index', '?')} (leniency={LENIENCY})")

    # Write heartbeat before expensive operations
    _write_heartbeat_if_needed()

    if not answers:
        log("INFO", "No answers to evaluate. Returning empty list.")
        return []

    if not expected:
        log("WARNING", "No expected answers provided for AI evaluation. Returning empty list.")
        return []

    judges = MODELS
    unique_answers = list(set(answers))
    log("DEBUG", f"Processing {len(unique_answers)} unique answers against {len(expected)} expected")

    accepted = []

    # --- AI model votes ---
    all_votes = []
    batch_size, is_auto = _get_batch_size()
    batches = [unique_answers[i:i + batch_size] for i in range(0, len(unique_answers), batch_size)]
    total_tasks = len(batches) * len(judges)
    max_workers = _resolve_max_workers(total_tasks)
    log("DEBUG", f"Parallelizing {total_tasks} model calls with max_workers={max_workers} (batch_size={batch_size})")

    results = {}
    start_parallel = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for batch_idx, batch in enumerate(batches, 1):
            log("DEBUG", f"Batch {batch_idx}/{len(batches)}: {len(batch)} answers")
            for model in judges:
                future = executor.submit(_get_votes_with_split, model, question, batch, expected, LENIENCY, is_auto)
                future_map[future] = (batch_idx, model, batch)

        for future in as_completed(future_map):
            batch_idx, model, batch = future_map[future]
            try:
                votes = future.result()
            except Exception as e:
                log("ERROR", f"Model {model} crashed on batch {batch_idx}: {e}. Falling back to NO.")
                votes = [("NO", "") for _ in batch]
            results[(batch_idx, model)] = votes
            log("DEBUG", f"Model {model} processed batch {batch_idx} successfully")
    elapsed_parallel = time.perf_counter() - start_parallel
    log(
        "INFO",
        f"Timing Q{question.get('index', '?')}: {total_tasks} model calls across "
        f"{len(batches)} batch(es) in {elapsed_parallel:.2f}s "
        f"(workers={max_workers}, answers={len(unique_answers)})"
    )

    for batch_idx, batch in enumerate(batches, 1):
        batch_votes = []
        for model in judges:
            votes = results.get((batch_idx, model), [("NO", "") for _ in batch])
            if len(votes) != len(batch):
                log("ERROR", f"Model {model} returned {len(votes)} votes, expected {len(batch)}. Falling back to NO.")
                votes = [("NO", "") for _ in batch]
            batch_votes.append(votes)
        all_votes.extend(list(zip(*batch_votes)))

    for idx, ans in enumerate(unique_answers, 1):
        votes = all_votes[idx - 1]
        vote_decisions = [v[0] for v in votes]
        yes_count = vote_decisions.count("YES")
        log("DEBUG", f"Answer {idx} ({ans}): {yes_count}/{len(judges)} YES")

        if yes_count >= ((len(judges) // 2) + 1):
            accepted.append(ans)
            log("DEBUG", f"Answer {idx} ({ans}) → YES (AI vote)")
        else:
            log("DEBUG", f"Answer {idx} ({ans}) → NO")

    log("DEBUG", f"Accepted: {len(accepted)} answers")
    return list(set(accepted))

# --- Compatibility alias ---
evaluate_answers = evaluate_answers_batch

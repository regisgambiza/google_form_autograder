import asyncio
import json
import re
from typing import Dict, List

try:
    import aiohttp
except Exception:
    aiohttp = None
import ollama

from evaluator_config import load_config
from logger import log

JUDGE_PROMPTS = {
    "semantic_judge": "You are a semantic meaning evaluator. Your ONLY job is to determine whether the student's answer conveys the same MEANING as the expected answer, regardless of wording, grammar, or spelling. Ignore surface form completely. Focus only on whether the core idea is the same.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "concept_judge": "You are a concept coverage checker. Given the required concepts for a correct answer, determine what percentage of them appear in the student's answer (even if expressed differently). Return a coverage score from 0.0 to 1.0.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "factual_judge": "You are a factual accuracy checker for science and mathematics. Determine whether the student's answer is scientifically or mathematically correct, ignoring grammar and spelling. Flag anything factually wrong even if it sounds similar to the correct answer.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "strict_judge": "You are a strict but fair human examiner. Grade as you would in a real classroom. Do not accept vague or incomplete answers. Require the student to have demonstrated genuine understanding, not just a lucky guess.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "misconception_judge": "You are a misconception analyst. Your job is to detect whether the student's answer reveals a fundamental conceptual misunderstanding, even if parts of the answer sound correct on the surface. A misconception should lower the score significantly.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "language_filter": "You are a language quality assessor for ESL and Thai learner answers. Your job is to separate language errors (grammar, spelling, word order) from content errors. Report how much of the answer's incorrectness is due to language issues vs actual wrong content.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
}
REQUIRED_FIELDS = ["semantic_similarity", "concept_coverage", "factual_accuracy", "misconception_detected", "misconception_description", "language_noise_ratio", "confidence", "decision", "reason_short"]


def _abstain(reason: str = "judge unavailable") -> Dict[str, object]:
    return {"semantic_similarity": 0.0, "concept_coverage": 0.0, "factual_accuracy": 0.0, "misconception_detected": False, "misconception_description": "", "language_noise_ratio": 0.0, "confidence": 0.0, "decision": "ABSTAIN", "reason_short": reason}


def _extract_json_object(raw: str) -> Dict[str, object]:
    """Legacy JSON extraction function - kept as fallback for non-structured responses."""
    if not raw:
        raise ValueError("Empty response from LLM - no content returned")
    
    # Log first character to understand what we're dealing with
    first_char = repr(raw[0]) if raw else "empty"
    log("DEBUG", f"Extracting JSON from response starting with: {first_char} (raw len={len(raw)})")

    # Remove any markdown comments or notes
    clean = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.IGNORECASE | re.DOTALL)
    clean = re.sub(r"^\s*\[NOTE\].*?\[\/NOTE\]\s*", "", clean, flags=re.IGNORECASE | re.DOTALL)
    clean = clean.strip()

    # If after cleaning we have nothing, report it
    if not clean:
        raise ValueError(f"Empty response after removing markdown - original content: {repr(raw[:100])}")

    # FIRST: Look for JSON code block and extract it
    # Pattern: ```json ... }``` or ``` ... }```
    code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', clean, flags=re.DOTALL)
    if code_block_match:
        json_str = code_block_match.group(1)
        log("DEBUG", f"Found JSON in code block, extracting...")
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            log("DEBUG", f"Code block JSON parse failed: {e}")

    # Remove markdown code blocks (whole blocks) to clean up the text
    clean = re.sub(r'```(?:json)?\s*.*?```', '', clean, flags=re.IGNORECASE | re.DOTALL)
    clean = clean.strip()

    # Try parsing the entire cleaned string as JSON first
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        log("DEBUG", f"Full JSON parse failed at position {e.pos}: {e.msg}")

    # SECOND: Look for JSON at the START of the cleaned string (position 0)
    idx = clean.find('{')
    if idx == 0:
        log("DEBUG", f"Found JSON at start, attempting parse...")
        try:
            decoder = json.JSONDecoder()
            obj, end_idx = decoder.raw_decode(clean)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError as e:
            log("DEBUG", f"JSON parse at start failed: {e}")

    # THIRD: Look for JSON after a newline followed by whitespace, then {
    # This handles cases like:\n  { ... } where the { is at the start of a line
    for match in re.finditer(r'\n\s*\{', clean):
        start = match.start()
        if start + 1 < len(clean):
            # Try to parse from the position after the newline/whitespace
            json_start = start + 1
            while json_start < len(clean) and clean[json_start].isspace():
                json_start += 1
            if json_start < len(clean) and clean[json_start] == '{':
                log("DEBUG", f"Found JSON after newline at position {json_start}")
                try:
                    decoder = json.JSONDecoder()
                    obj, end_idx = decoder.raw_decode(clean[json_start:])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError as e:
                    log("DEBUG", f"JSON parse after newline failed: {e}")

    # FOURTH: Try every { position and attempt to parse JSON from there
    # This handles cases where the JSON is embedded in text without a newline
    # but we can still find and extract it
    idx = 0
    while idx >= 0 and idx < len(clean):
        next_brace = clean.find('{', idx)
        if next_brace == -1:
            break
        log("DEBUG", f"Trying JSON extraction from position {next_brace}...")
        try:
            decoder = json.JSONDecoder()
            obj, end_idx = decoder.raw_decode(clean[next_brace:])
            if isinstance(obj, dict):
                log("DEBUG", f"Successfully extracted JSON from position {next_brace}")
                return obj
        except json.JSONDecodeError as e:
            log("DEBUG", f"Failed to extract JSON from position {next_brace}: {e.msg}")
        idx = next_brace + 1

    # FIFTH: Use balanced brace counting to find valid JSON
    # Only consider { that appears at the start of a line or after certain delimiters
    brace_stack = 0
    start_idx = -1
    
    # Find potential JSON objects by looking for { that starts a line
    lines = clean.split('\n')
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith('{'):
            # Found a { at the start of a line (possibly after some whitespace)
            # Find the position in the original string
            pos = 0
            for j in range(i):
                pos += len(lines[j]) + 1  # +1 for the newline
            pos += len(line) - len(stripped)  # add leading whitespace
            start_idx = pos
            brace_stack = 1
            
            # Find the matching }
            end_pos = start_idx + 1
            while end_pos < len(clean) and brace_stack > 0:
                if clean[end_pos] == '{':
                    brace_stack += 1
                elif clean[end_pos] == '}':
                    brace_stack -= 1
                end_pos += 1
            
            if brace_stack == 0:
                candidate = clean[start_idx:end_pos]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as e:
                    log("DEBUG", f"JSON at line start failed: {e}")
            break  # Only try the first line-starting brace
    
    # LAST RESORT: Use regex to find balanced braces that look like JSON
    m = re.search(r'\{(?:[^{}]*(?:\{[^{}]*\}[^{}]*)*)*\}', clean, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            log("DEBUG", f"Balanced brace extraction failed: {e}")

    # If we still can't extract, provide detailed error message
    log("ERROR", f"Failed to extract JSON from response")
    log("ERROR", f"  Original (first 200 chars): {repr(raw[:200])}")
    log("ERROR", f"  After cleaning (first 200 chars): {repr(clean[:200])}")
    log("ERROR", f"  First 10 chars: {repr(clean[:10])}")
    raise ValueError(f"Failed to extract JSON - LLM returned invalid response format. Start: {repr(clean[:50])}")



def _normalize_decision(d: Dict[str, object]) -> Dict[str, object]:
    decision = str(d.get("decision", "ABSTAIN")).strip().upper()
    # Handle numeric decisions (e.g., 0, 1)
    if decision in {"0", "FALSE", "INCORRECT", "FAIL", "WRONG", "NO"}:
        d["decision"] = "NO"
    elif decision in {"1", "TRUE", "CORRECT", "PASS", "YES"}:
        d["decision"] = "YES"
    else:
        d["decision"] = "ABSTAIN"
    return d


def _fill_judge_defaults(data: Dict[str, object]) -> Dict[str, object]:
    defaults = _abstain("partial")
    for key in REQUIRED_FIELDS:
        if key not in data:
            data[key] = defaults[key]
    # Ensure numeric fields are actually numeric and within valid ranges
    for nf in ["semantic_similarity", "concept_coverage", "factual_accuracy", "language_noise_ratio", "confidence"]:
        try:
            val = float(data[nf])
            # Clamp values to valid range [0.0, 1.0]
            data[nf] = max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            data[nf] = 0.0
    # Ensure boolean field
    if isinstance(data.get("misconception_detected"), str):
        data["misconception_detected"] = data["misconception_detected"].lower() in {"true", "yes", "1"}
    elif not isinstance(data.get("misconception_detected"), bool):
        data["misconception_detected"] = bool(data.get("misconception_detected", False))
    return data


def _valid(d: Dict[str, object]) -> bool:
    return all(k in d for k in REQUIRED_FIELDS) and str(d.get("decision")) in {"YES", "NO", "ABSTAIN"}


def _make_judge_prompt(question: str, expected: str, answer: str, rubric: Dict[str, object]) -> str:
    return (
        f"Question: {question}\nExpected: {expected}\nAnswer: {answer}\n\n"
        f"Rubric for reference (do not return this):\n{json.dumps(rubric)}\n\n"
        "Provide your evaluation as a JSON object with these fields:"
    )


def _get_judge_format(role: str) -> Dict[str, object]:
    """Return the JSON schema for structured output based on judge role."""
    # All judges use the same output format
    return {
        "type": "object",
        "properties": {
            "semantic_similarity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "concept_coverage": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "factual_accuracy": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "misconception_detected": {"type": "boolean"},
            "misconception_description": {"type": "string"},
            "language_noise_ratio": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "decision": {"type": "string", "enum": ["YES", "NO", "ABSTAIN"]},
            "reason_short": {"type": "string"}
        },
        "required": [
            "semantic_similarity", "concept_coverage", "factual_accuracy",
            "misconception_detected", "misconception_description",
            "language_noise_ratio", "confidence", "decision", "reason_short"
        ],
        "additionalProperties": False
    }


async def call_judge_async(session, model: str, role: str, answer: str, question: str, expected: str, rubric: Dict[str, object], retries: int, num_ctx: int) -> Dict[str, object]:
    """Call a judge using structured output format for reliable JSON responses."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JUDGE_PROMPTS[role]},
            {"role": "user", "content": _make_judge_prompt(question, expected, answer, rubric)}
        ],
        "stream": False,
        "options": {"num_ctx": num_ctx},
        "format": _get_judge_format(role),  # Use structured output for reliable JSON
    }
    for i in range(retries):
        try:
            async with session.post("http://localhost:11434/api/chat", json=payload, timeout=180) as resp:
                data = await resp.json()

            # With structured output, Ollama returns the JSON object
            # The content might be a string (raw JSON) or already a dict
            content = data.get("message", {}).get("content", "")

            # Handle empty response (model may have timed out or failed)
            if not content or content == "":
                log("WARNING", f"Judge {role} attempt {i+1}/{retries}: Empty response from model")
                continue

            # Parse JSON string if needed, otherwise use as dict
            if isinstance(content, str):
                try:
                    obj = json.loads(content)
                except json.JSONDecodeError:
                    obj = content
            else:
                obj = content

            obj = _normalize_decision(obj)
            obj = _fill_judge_defaults(obj)
            if _valid(obj):
                return obj

        except json.JSONDecodeError as ex:
            content = data.get("message", {}).get("content", "")
            log("WARNING", f"Judge {role} attempt {i+1}/{retries} JSON decode error: {ex}")
            log("WARNING", f"  Content received: {repr(content)[:200]}")
        except Exception as ex:
            content = data.get("message", {}).get("content", "") if 'data' in locals() else ""
            log("WARNING", f"Judge {role} attempt {i+1}/{retries} failed: {ex}")
            log("WARNING", f"  Content received: {repr(content)[:200]}")
    return _abstain("retries_exhausted")


async def run_all_judges_with_early_exit(answer: str, question: str, expected: str, rubric: Dict[str, object], retries: int = 3) -> List[Dict[str, object]]:
    cfg = load_config()
    jury_models = cfg.get("jury_models", {})
    num_ctx = int(cfg.get("ollama_options", {}).get("judge_num_ctx", 2048))
    ee = cfg.get("early_exit", {})
    min_judges = int(ee.get("min_judges", 3))
    agree_thresh = float(ee.get("agreement_confidence", 0.90))
    enabled = bool(ee.get("enabled", True))

    if aiohttp is None:
        log("WARNING", "aiohttp not installed; falling back to synchronous judge calls")
        out: List[Dict[str, object]] = []
        for role in JUDGE_PROMPTS:
            role_model = jury_models.get(role)
            obj = _abstain("retries_exhausted")
            for i in range(retries):
                try:
                    # Use structured output format for reliable JSON
                    response = ollama.chat(
                        model=role_model,
                        options={"num_ctx": num_ctx},
                        format=_get_judge_format(role),  # Structured output
                        messages=[
                            {"role": "system", "content": JUDGE_PROMPTS[role]},
                            {"role": "user", "content": _make_judge_prompt(question, expected, answer, rubric)},
                        ],
                    )
                    # With format parameter, response["message"]["content"] is already a dict
                    raw = response.get("message", {}).get("content", "")
                    
                    # Handle both dict (structured output) and string fallback
                    if isinstance(raw, dict):
                        candidate = raw
                    elif not raw:
                        log("WARNING", f"Judge {role} sync attempt {i+1}/{retries} FAILED: Empty response from Ollama")
                        continue
                    else:
                        candidate = _extract_json_object(raw)
                    
                    candidate = _normalize_decision(candidate)
                    candidate = _fill_judge_defaults(candidate)
                    if _valid(candidate):
                        obj = candidate
                        break
                except json.JSONDecodeError as ex:
                    log("WARNING", f"Judge {role} sync attempt {i+1}/{retries} JSON decode error: {ex}")
                    log("WARNING", f"  Content received: {repr(raw)[:200]}")
                except Exception as ex:
                    log("WARNING", f"Judge {role} sync attempt {i+1}/{retries} failed: {ex}")
                    log("WARNING", f"  Content received: {repr(raw)[:200]}")
            out.append(obj)
        return out



    tasks = {}
    async with aiohttp.ClientSession() as session:
        for role in JUDGE_PROMPTS:
            role_model = jury_models.get(role)
            tasks[asyncio.create_task(call_judge_async(session, role_model, role, answer, question, expected, rubric, retries, num_ctx))] = role
        results: List[Dict[str, object]] = []
        for done in asyncio.as_completed(tasks):
            r = await done
            results.append(r)
            if enabled and len(results) >= min_judges:
                decisions = [x.get("decision") for x in results]
                confs = [float(x.get("confidence", 0.0)) for x in results]
                avg_conf = (sum(confs) / len(confs)) if confs else 0.0
                if len(set(decisions)) == 1 and avg_conf >= agree_thresh:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    log("DEBUG", f"Early exit after {len(results)} judges - unanimous {decisions[0]} @ {avg_conf:.2f}")
                    break
        return results


def run_judges(answer: str, question: str, expected: str, rubric: Dict[str, object], retries: int = 3) -> List[Dict[str, object]]:
    return asyncio.run(run_all_judges_with_early_exit(answer, question, expected, rubric, retries))

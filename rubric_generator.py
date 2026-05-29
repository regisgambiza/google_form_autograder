import json
import os
import re
from typing import Dict, Optional

import ollama

from evaluator_config import load_config, sha256_text
from logger import log

SYSTEM_PROMPT = (
    "You are an expert curriculum designer and teacher. Given a question and its correct answer, "
    "produce a structured grading rubric. Be generous with acceptable paraphrases - students may use "
    "different words but still be correct. List common misconceptions that would indicate the student "
    "does NOT understand the concept.\n\n"
    "CRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown code blocks, no text before or after.\n"
    "The FIRST character must be '{' and the LAST character must be '}'.\n"
    "Do NOT add any explanations or comments. Do NOT use markdown formatting.\n\n"
    "Return ONLY this JSON format:\n"
    '{\n'
    '  "required_concepts": ["list of core concepts the answer must contain"],\n'
    '  "optional_concepts": ["list of bonus concepts that strengthen the answer"],\n'
    '  "acceptable_paraphrases": ["list of alternative phrasings that are acceptable"],\n'
    '  "critical_errors": ["list of errors that would make the answer wrong"],\n'
    '  "strict_keywords": ["list of keywords that must appear or be implied"],\n'
    '  "misconceptions": ["list of common wrong beliefs students may show"],\n'
    '  "grading_notes": "any additional grading guidance"\n'
    '}\n\n'
    "IMPORTANT: Start your response with '{' and end with '}'. Nothing else."
)
REQUIRED_KEYS = ["required_concepts", "optional_concepts", "acceptable_paraphrases", "critical_errors", "strict_keywords", "misconceptions", "grading_notes"]


def _extract_json_object(raw: str) -> Dict[str, object]:
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

    # Remove markdown code blocks (whole blocks)
    clean = re.sub(r'```(?:json)?\s*.*?```', '', clean, flags=re.IGNORECASE | re.DOTALL)
    clean = clean.strip()

    # Try parsing the entire cleaned string as JSON first
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        log("DEBUG", f"Full JSON parse failed at position {e.pos}: {e.msg}")

    # SECOND: Look for JSON at the START of the cleaned string (position 0 or after whitespace)
    # This handles cases where LLM puts JSON at the beginning with some prefix text
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
    elif idx > 0:
        # There's text before the JSON - check if it looks like markdown that should be removed
        # Look for the JSON part starting at idx
        log("DEBUG", f"Found JSON at position {idx} (not at start)")
        try:
            decoder = json.JSONDecoder()
            obj, end_idx = decoder.raw_decode(clean[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError as e:
            log("DEBUG", f"JSON parse after prefix failed: {e}")

    # LAST RESORT: Use regex to find balanced braces that look like JSON
    # This handles cases where braces are embedded in text
    brace_stack = 0
    start_idx = -1
    for i, ch in enumerate(clean):
        if ch == '{':
            if brace_stack == 0:
                start_idx = i
            brace_stack += 1
        elif ch == '}':
            brace_stack -= 1
            if brace_stack == 0 and start_idx != -1:
                candidate = clean[start_idx:i+1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

    # Try to find a JSON-like pattern with balanced braces
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


def _make_fallback(expected: str) -> Dict[str, object]:
    return {
        "required_concepts": [expected],
        "optional_concepts": [],
        "acceptable_paraphrases": [expected],
        "critical_errors": [],
        "strict_keywords": [],
        "misconceptions": ["plants eat sunlight"],
        "grading_notes": "fallback rubric",
    }


def _fill_rubric_defaults(data: Dict[str, object], expected: str) -> Dict[str, object]:
    defaults = _make_fallback(expected)
    for key in REQUIRED_KEYS:
        if key not in data or not isinstance(data[key], (list, str)):
            if key == "grading_notes":
                data[key] = str(data.get(key, defaults[key]))
            else:
                data[key] = defaults[key]
    return data


def generate_rubric(question: str, expected: str, model: Optional[str] = None) -> Dict[str, object]:
    cfg = load_config()
    model = model or cfg.get('rubric_model')
    num_ctx = int(cfg.get('ollama_options', {}).get('rubric_num_ctx', 1024))
    os.makedirs("cache/rubrics", exist_ok=True)
    key = sha256_text(question + "||" + expected)
    path = os.path.join("cache/rubrics", f"{key}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    fallback = _make_fallback(expected)
    
    # Use structured output format for reliable JSON
    rubric_format = {
        "type": "object",
        "properties": {
            "required_concepts": {"type": "array", "items": {"type": "string"}},
            "optional_concepts": {"type": "array", "items": {"type": "string"}},
            "acceptable_paraphrases": {"type": "array", "items": {"type": "string"}},
            "critical_errors": {"type": "array", "items": {"type": "string"}},
            "strict_keywords": {"type": "array", "items": {"type": "string"}},
            "misconceptions": {"type": "array", "items": {"type": "string"}},
            "grading_notes": {"type": "string"}
        },
        "required": REQUIRED_KEYS,
        "additionalProperties": False
    }
    
    try:
        r = ollama.chat(
            model=model,
            options={"num_ctx": num_ctx},
            format=rubric_format,  # Structured output
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Question: {question}\nExpected: {expected}"},
            ],
        )
        # With format parameter, r["message"]["content"] is already a dict
        raw_content = r.get("message", {}).get("content", "")
        
        if isinstance(raw_content, dict):
            data = raw_content
        else:
            data = _extract_json_object(raw_content)
            
        if not isinstance(data, dict):
            raise ValueError("Extracted rubric is not a JSON object")
        # Unpack nested rubrics if the model wrapped it (e.g. {"rubric": {...}})
        if len(data) == 1 and list(data.keys())[0].lower() in {"rubric", "gradingrubric", "grading_rubric"}:
            inner = list(data.values())[0]
            if isinstance(inner, dict):
                data = inner
        data = _fill_rubric_defaults(data, expected)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    except Exception as ex:
        log("WARNING", f"Rubric generation failed; using fallback: {ex}")
        return fallback


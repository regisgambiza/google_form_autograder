import json
import os
import re
from typing import Dict

import ollama

from evaluator_config import sha256_text
from logger import log

SYSTEM_PROMPT = (
    "You are an expert curriculum designer and teacher. Given a question and its correct answer, "
    "produce a structured grading rubric. Be generous with acceptable paraphrases - students may use "
    "different words but still be correct. List common misconceptions that would indicate the student "
    "does NOT understand the concept.\n\n"
    "You MUST return ONLY a valid JSON object with EXACTLY these fields:\n"
    '{\n'
    '  "required_concepts": ["list of core concepts the answer must contain"],\n'
    '  "optional_concepts": ["list of bonus concepts that strengthen the answer"],\n'
    '  "acceptable_paraphrases": ["list of alternative phrasings that are acceptable"],\n'
    '  "critical_errors": ["list of errors that would make the answer wrong"],\n'
    '  "strict_keywords": ["list of keywords that must appear or be implied"],\n'
    '  "misconceptions": ["list of common wrong beliefs students may show"],\n'
    '  "grading_notes": "any additional grading guidance"\n'
    '}\n'
    "No preamble. No explanation. Only the JSON object."
)
REQUIRED_KEYS = ["required_concepts", "optional_concepts", "acceptable_paraphrases", "critical_errors", "strict_keywords", "misconceptions", "grading_notes"]


def _extract_json_object(raw: str) -> Dict[str, object]:
    """Extract first JSON object from model output, tolerating wrappers."""
    clean = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.IGNORECASE | re.DOTALL).strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except Exception:
        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _make_fallback(expected: str) -> Dict[str, object]:
    """Create a sensible fallback rubric from the expected answer."""
    return {
        "required_concepts": [expected],
        "optional_concepts": [],
        "acceptable_paraphrases": [expected],
        "critical_errors": [],
        "strict_keywords": [],
        "misconceptions": [],
        "grading_notes": "fallback rubric",
    }


def _fill_rubric_defaults(data: Dict[str, object], expected: str) -> Dict[str, object]:
    """Fill any missing rubric keys with safe defaults instead of failing."""
    defaults = _make_fallback(expected)
    for key in REQUIRED_KEYS:
        if key not in data:
            log("DEBUG", f"Rubric missing key '{key}'; filling default")
            data[key] = defaults[key]
    return data


def generate_rubric(question: str, expected: str, model: str = "qwen2.5:7b") -> Dict[str, object]:
    """Generate or load cached rubric JSON."""
    os.makedirs("cache/rubrics", exist_ok=True)
    key = sha256_text(question + "||" + expected)
    path = os.path.join("cache/rubrics", f"{key}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    fallback = _make_fallback(expected)

    try:
        r = ollama.chat(model=model, messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Question: {question}\nExpected answer: {expected}\nReturn the JSON rubric now."},
        ])
        data = _extract_json_object(r["message"]["content"])
        data = _fill_rubric_defaults(data, expected)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    except Exception as ex:
        log("WARNING", f"Rubric generation failed; using fallback: {ex}")
        return fallback

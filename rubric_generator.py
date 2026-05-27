import json
import os
import re
from typing import Dict

import ollama

from evaluator_config import load_config, sha256_text
from logger import log

SYSTEM_PROMPT = (
    "You are an expert curriculum designer and teacher. Given a question and its correct answer, "
    "produce a structured grading rubric. Be generous with acceptable paraphrases - students may use "
    "different words but still be correct. List common misconceptions that would indicate the student "
    "does NOT understand the concept. Output only valid JSON. No preamble. No explanation."
)
REQUIRED_KEYS = ["required_concepts", "optional_concepts", "acceptable_paraphrases", "critical_errors", "strict_keywords", "misconceptions", "grading_notes"]


def _extract_json_object(raw: str) -> Dict[str, object]:
    clean = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.IGNORECASE | re.DOTALL).strip()
    clean = re.sub(r"^```(?:json)?\\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\\s*```$", "", clean)
    try:
        return json.loads(clean)
    except Exception:
        m = re.search(r"\\{.*\\}", clean, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def generate_rubric(question: str, expected: str, model: str = "qwen2.5:7b") -> Dict[str, object]:
    cfg = load_config()
    num_ctx = int(cfg.get('ollama_options', {}).get('rubric_num_ctx', 1024))
    os.makedirs("cache/rubrics", exist_ok=True)
    key = sha256_text(question + "||" + expected)
    path = os.path.join("cache/rubrics", f"{key}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    fallback = {
        "required_concepts": [expected],
        "optional_concepts": [],
        "acceptable_paraphrases": [expected],
        "critical_errors": [],
        "strict_keywords": [],
        "misconceptions": ["plants eat sunlight"],
        "grading_notes": "fallback rubric",
    }
    try:
        r = ollama.chat(model=model, options={"num_ctx": num_ctx}, messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Question: {question}\\nExpected: {expected}"},
        ])
        data = _extract_json_object(r["message"]["content"])
        if not all(k in data for k in REQUIRED_KEYS):
            raise ValueError("rubric schema mismatch")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    except Exception as ex:
        log("WARNING", f"Rubric generation failed; using fallback: {ex}")
        return fallback

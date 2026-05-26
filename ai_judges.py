import json
import re
from typing import Dict

import ollama

from logger import log

JUDGE_PROMPTS = {
    "semantic_judge": "You are a semantic meaning evaluator. Your ONLY job is to determine whether the student's answer conveys the same MEANING as the expected answer, regardless of wording, grammar, or spelling. Ignore surface form completely. Focus only on whether the core idea is the same.",
    "concept_judge": "You are a concept coverage checker. Given the required concepts for a correct answer, determine what percentage of them appear in the student's answer (even if expressed differently). Return a coverage score from 0.0 to 1.0.",
    "factual_judge": "You are a factual accuracy checker for science and mathematics. Determine whether the student's answer is scientifically or mathematically correct, ignoring grammar and spelling. Flag anything factually wrong even if it sounds similar to the correct answer.",
    "strict_judge": "You are a strict but fair human examiner. Grade as you would in a real classroom. Do not accept vague or incomplete answers. Require the student to have demonstrated genuine understanding, not just a lucky guess.",
    "misconception_judge": "You are a misconception analyst. Your job is to detect whether the student's answer reveals a fundamental conceptual misunderstanding, even if parts of the answer sound correct on the surface. A misconception should lower the score significantly.",
    "language_filter": "You are a language quality assessor for ESL and Thai learner answers. Your job is to separate language errors (grammar, spelling, word order) from content errors. Report how much of the answer's incorrectness is due to language issues vs. actual wrong content.",
}

REQUIRED_FIELDS = ["semantic_similarity", "concept_coverage", "factual_accuracy", "misconception_detected", "misconception_description", "language_noise_ratio", "confidence", "decision", "reason_short"]


def _extract_json_object(raw: str) -> Dict[str, object]:
    """Extract first JSON object from model output with wrappers removed."""
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


def _abstain(reason: str = "judge unavailable") -> Dict[str, object]:
    return {
        "semantic_similarity": 0.0,
        "concept_coverage": 0.0,
        "factual_accuracy": 0.0,
        "misconception_detected": False,
        "misconception_description": "",
        "language_noise_ratio": 0.0,
        "confidence": 0.0,
        "decision": "ABSTAIN",
        "reason_short": reason,
    }


def _normalize_decision(data: Dict[str, object]) -> Dict[str, object]:
    """Normalize the decision field to YES/NO/ABSTAIN."""
    raw_decision = str(data.get("decision", "")).strip().upper()
    if raw_decision in {"YES", "CORRECT", "ACCEPT", "TRUE", "1", "PASS"}:
        data["decision"] = "YES"
    elif raw_decision in {"NO", "INCORRECT", "REJECT", "FALSE", "0", "FAIL", "WRONG"}:
        data["decision"] = "NO"
    elif raw_decision in {"ABSTAIN", "UNSURE", "UNCERTAIN", "SKIP", ""}:
        data["decision"] = "ABSTAIN"
    else:
        data["decision"] = "ABSTAIN"
    return data


def _fill_judge_defaults(data: Dict[str, object]) -> Dict[str, object]:
    """Fill any missing required fields with safe defaults."""
    defaults = _abstain("partial_response")
    for key in REQUIRED_FIELDS:
        if key not in data:
            log("DEBUG", f"Judge response missing key '{key}'; filling default")
            data[key] = defaults[key]
    # Ensure numeric fields are actually numeric
    for nf in ["semantic_similarity", "concept_coverage", "factual_accuracy", "language_noise_ratio", "confidence"]:
        try:
            data[nf] = float(data[nf])
        except (TypeError, ValueError):
            data[nf] = 0.0
    # Ensure boolean field
    if isinstance(data.get("misconception_detected"), str):
        data["misconception_detected"] = data["misconception_detected"].lower() in {"true", "yes", "1"}
    return data


def _valid(data: Dict[str, object]) -> bool:
    return all(k in data for k in REQUIRED_FIELDS) and str(data.get("decision")) in {"YES", "NO", "ABSTAIN"}


def call_judge(model: str, role: str, answer: str, question: str, expected: str, rubric: Dict[str, object], retries: int = 3) -> Dict[str, object]:
    """Call one specialist judge with strict JSON validation and retries."""
    if not model:
        return _abstain()
    prompt = (
        f"Question: {question}\nExpected answer: {expected}\nStudent answer: {answer}\n"
        f"Rubric: {json.dumps(rubric)}\n\n"
        "You MUST return ONLY a valid JSON object with EXACTLY these fields:\n"
        '{\n'
        '  "semantic_similarity": 0.0 to 1.0,\n'
        '  "concept_coverage": 0.0 to 1.0,\n'
        '  "factual_accuracy": 0.0 to 1.0,\n'
        '  "misconception_detected": true or false,\n'
        '  "misconception_description": "describe any misconception or empty string",\n'
        '  "language_noise_ratio": 0.0 to 1.0,\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "decision": "YES" or "NO" or "ABSTAIN",\n'
        '  "reason_short": "brief reason for decision"\n'
        '}\n'
        "No preamble. No explanation. Only the JSON object."
    )
    for i in range(retries):
        try:
            raw = ollama.chat(model=model, messages=[
                {"role": "system", "content": JUDGE_PROMPTS[role]},
                {"role": "user", "content": prompt},
            ])["message"]["content"]
            data = _extract_json_object(raw)
            data = _normalize_decision(data)
            data = _fill_judge_defaults(data)
            if _valid(data):
                return data
            log("WARNING", f"Judge {role} attempt {i + 1}/{retries}: schema still invalid after defaults")
        except Exception as ex:
            log("WARNING", f"Judge {role} attempt {i + 1}/{retries} failed: {ex}")
    return _abstain("retries_exhausted")

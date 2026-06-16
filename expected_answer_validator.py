import json
import os
from typing import Dict, List, Optional

import requests

from evaluator_config import load_config, sha256_text
from logger import log


VALIDATION_PROMPT = (
    "You validate teacher-provided expected answers for maths homework. "
    "Use the textbook/question context to check whether the expected answer is mathematically correct. "
    "Only validate the expected answer for the exact current question label/title. "
    "If the context contains a line beginning 'Mapped textbook question:', treat that as the "
    "authoritative exact prompt for the current Google Form question. "
    "If the exact current question cannot be identified from the context, or there is not enough "
    "information to verify the expected answer, return valid=true with low confidence and explain "
    "that it was not verifiable. Do not mark an answer invalid merely because context is ambiguous. "
    "Do not grade learner answers. Return only JSON with keys: "
    "valid, confidence, suggested_answers, reason."
)

VALIDATION_CACHE_VERSION = "v3"


def _ollama_chat_url() -> str:
    return os.environ.get("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")


def _cache_path(key: str) -> str:
    return os.path.join("cache", "expected_validation", f"{key}.json")


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split())


def _extract_json(content: str) -> Dict:
    if not content:
        return {}
    content = content.strip()
    if "</think>" in content:
        content = content.split("</think>", 1)[1].strip()
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _normalize_validation(data: Dict, model: str, expected: List[str]) -> Dict:
    cfg = load_config()
    valid = data.get("valid")
    if isinstance(valid, str):
        valid = valid.strip().lower() in {"true", "yes", "valid", "correct"}
    elif valid is None:
        valid = True
    else:
        valid = bool(valid)

    try:
        confidence = float(data.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    suggested = data.get("suggested_answers", [])
    if isinstance(suggested, str):
        suggested_answers = [suggested]
    elif isinstance(suggested, list):
        suggested_answers = [str(x) for x in suggested if _clean(x)]
    else:
        suggested_answers = []

    low_confidence_invalid_ignored = False
    min_conf = float(cfg.get("expected_answer_validator_min_confidence", 0.85))
    if valid is False and confidence < min_conf:
        valid = True
        low_confidence_invalid_ignored = True

    return {
        "validation_status": "ok",
        "valid": valid,
        "confidence": confidence,
        "suggested_answers": suggested_answers,
        "reason": _clean(data.get("reason")),
        "model_used": model,
        "original_expected": [str(x) for x in expected],
        "low_confidence_invalid_ignored": low_confidence_invalid_ignored,
    }


def _call_validator(model: str, question_context: str, expected: List[str], timeout: int, connect_timeout: int) -> Dict:
    user_prompt = (
        f"Question/context:\n{question_context}\n\n"
        f"Teacher expected answer(s):\n{json.dumps(expected, ensure_ascii=True)}\n\n"
        "Check the teacher expected answer(s). If invalid, suggest the corrected answer(s)."
    )
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": VALIDATION_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",
    }
    resp = requests.post(_ollama_chat_url(), json=payload, timeout=(connect_timeout, timeout))
    resp.raise_for_status()
    content = resp.json().get("message", {}).get("content", "")
    data = _extract_json(content)
    if not data:
        raise ValueError("validator returned no JSON")
    return _normalize_validation(data, model, expected)


def validate_expected_answer(question_context: str, expected: Optional[List[str]]) -> Dict:
    cfg = load_config()
    expected_values = [str(x) for x in (expected or []) if _clean(x)]
    if not bool(cfg.get("validate_expected_answers", False)):
        return {"validation_status": "disabled", "valid": True, "original_expected": expected_values}
    if not expected_values:
        return {"validation_status": "no_expected", "valid": True, "original_expected": []}

    key = sha256_text(
        VALIDATION_CACHE_VERSION + "||" + question_context + "||" + json.dumps(expected_values, sort_keys=True)
    )
    path = _cache_path(key)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    models = [
        str(cfg.get("expected_answer_validator_model", "deepseek-r1:8b")),
        str(cfg.get("expected_answer_validator_fallback_model", "qwen3:8b")),
    ]
    timeout = max(5, int(cfg.get("expected_answer_validator_timeout_seconds", 90)))
    connect_timeout = max(2, int(cfg.get("expected_answer_validator_connect_timeout_seconds", 10)))
    errors = []

    for model in [m for i, m in enumerate(models) if m and m not in models[:i]]:
        try:
            out = _call_validator(model, question_context, expected_values, timeout, connect_timeout)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=True)
            return out
        except Exception as ex:
            errors.append(f"{model}: {ex}")
            log("WARNING", f"[EXPECTED VALIDATOR] model failed ({model}): {ex}")

    out = {
        "validation_status": "failed",
        "valid": True,
        "confidence": 0.0,
        "suggested_answers": [],
        "reason": "validation failed; keeping original expected answer",
        "errors": errors,
        "original_expected": expected_values,
    }
    if bool(cfg.get("expected_answer_validation_optional", True)):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=True)
        except Exception:
            pass
        return out
    raise RuntimeError("; ".join(errors) or "expected answer validation failed")

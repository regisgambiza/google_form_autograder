import json
import re
import time
from typing import Dict, Optional, Tuple

import ollama

from evaluator_config import load_config
from logger import log


def _extract_json(raw: str) -> dict:
    """Extract JSON object from model output, tolerating think tags and fences."""
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


def invoke_reasoning_fallback(answer: str, question: str, rubric: Dict[str, object], judge_scores: Dict[str, float], model: Optional[str] = None) -> Tuple[str, float, str]:
    """Run reasoning fallback and strip hidden chain-of-thought tags."""
    start = time.perf_counter()
    log("INFO", f"START reasoning_fallback (model=gemma3:12b)")
    cfg = load_config()
    model = model or cfg.get("reasoning_model")
    prompt = (
        f"Question: {question}\n"
        f"Answer: {answer}\n"
        f"Rubric: {json.dumps(rubric)}\n"
        f"Judge scores: {json.dumps(judge_scores)}\n\n"
        "You MUST return ONLY a valid JSON object with EXACTLY these fields:\n"
        '{\n'
        '  "decision": "YES" or "NO",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "reason_short": "brief reason"\n'
        '}\n'
        "No preamble. No explanation. Only the JSON object."
    )
    try:
        raw = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])["message"]["content"]
        data = _extract_json(raw)
        decision = str(data.get("decision", "NO")).strip().upper()
        if decision not in {"YES", "NO"}:
            decision = "NO"
        duration_ms = (time.perf_counter() - start) * 1000
        log("INFO", f"END reasoning_fallback duration_ms={duration_ms:.0f} decision={decision}")
        return decision, float(data.get("confidence", 0.5)), str(data.get("reason_short", "fallback"))
    except Exception as ex:
        duration_ms = (time.perf_counter() - start) * 1000
        log("INFO", f"END reasoning_fallback duration_ms={duration_ms:.0f} decision=NO failed={ex}")
        return "NO", 0.5, "reasoning_fallback_failed"


def route_decision(final_score: float, answer: str, question: str, rubric: Dict[str, object], judge_scores: Dict[str, float], thresholds: Dict[str, float]) -> Tuple[str, float, str, str]:
    """Route by confidence thresholds and fallback band."""
    if final_score >= float(thresholds["auto_accept"]):
        return "YES", final_score, "auto_accept", "jury"
    if final_score < float(thresholds["auto_reject"]):
        return "NO", final_score, "auto_reject", "jury"
    decision, confidence, reason = invoke_reasoning_fallback(answer, question, rubric, judge_scores)
    return decision, confidence, reason, "reasoning"

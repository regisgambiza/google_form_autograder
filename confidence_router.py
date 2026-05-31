import json
import os
import queue
import re
import threading
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
    """Run reasoning fallback and strip hidden chain-of-thought tags with timeout protection."""
    start = time.perf_counter()
    log("INFO", f"START reasoning_fallback (model=gemma3:12b)")
    cfg = load_config()
    model = model or cfg.get("reasoning_model")
    timeout_seconds = cfg.get("max_latency_per_answer_seconds", 30)
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
    
    # Use thread with timeout for the Ollama call
    result_queue = queue.Queue()
    exception_queue = queue.Queue()
    
    def call_ollama():
        try:
            # num_gpu=-1 offloads all layers to GPU for optimal performance
            # num_ctx set to fallback_num_ctx from config or default 4096
            cfg = load_config()
            num_ctx = int(cfg.get("ollama_options", {}).get("fallback_num_ctx", 4096))
            raw = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}], options={"num_ctx": num_ctx, "num_gpu": -1}, timeout=timeout_seconds)["message"]["content"]
            result_queue.put(("success", raw))
        except Exception as e:
            exception_queue.put(e)
            result_queue.put(("exception", None))
    
    thread = threading.Thread(target=call_ollama, daemon=True)
    thread.start()
    
    # Poll for completion with timeout
    poll_interval = 0.1
    elapsed = 0
    while thread.is_alive() and elapsed < timeout_seconds:
        time.sleep(poll_interval)
        elapsed += poll_interval
    
    if thread.is_alive():
        log("WARNING", f"Reasoning fallback timed out after {timeout_seconds}s")
        duration_ms = (time.perf_counter() - start) * 1000
        log("INFO", f"END reasoning_fallback duration_ms={duration_ms:.0f} decision=NO timed_out=True")
        return "NO", 0.5, "reasoning_fallback_timeout"
    
    if not exception_queue.empty():
        ex = exception_queue.get()
        log("WARNING", f"Reasoning fallback exception: {ex}")
        duration_ms = (time.perf_counter() - start) * 1000
        log("INFO", f"END reasoning_fallback duration_ms={duration_ms:.0f} decision=NO failed={ex}")
        return "NO", 0.5, "reasoning_fallback_failed"
    
    status, raw = result_queue.get()
    if status != "success":
        duration_ms = (time.perf_counter() - start) * 1000
        log("INFO", f"END reasoning_fallback duration_ms={duration_ms:.0f} decision=NO failed=status")
        return "NO", 0.5, "reasoning_fallback_failed"
    
    try:
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

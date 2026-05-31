import json
import os
from typing import Dict, List, Optional

from evaluation_pipeline import evaluate_answers as semantic_evaluate_answers
from logger import log
from ollama_diagnostics import log_ollama_gpu_diagnostics_once


def _write_heartbeat_if_needed():
    """Write heartbeat to file for hang monitoring."""
    try:
        from datetime import datetime, timezone
        data = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid()
        }
        with open("heartbeat.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _progress_bar(yes_count: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "." * width
    fill = int((yes_count / total) * width)
    return ("#" * fill) + ("." * (width - fill))


def _print_pretty_block(question: Dict[str, object], results: List[object]) -> None:
    total = len(results)
    yes = sum(1 for r in results if r.decision == "YES")
    no = total - yes
    fast_yes = sum(1 for r in results if r.fast_path_used and r.decision == "YES")
    fast_no = sum(1 for r in results if r.fast_path_used and r.decision == "NO")
    sent_semantic = total - (fast_yes + fast_no)
    misconceptions = sum(1 for r in results if r.misconception_detected)
    avg_conf = (sum(r.confidence for r in results) / total) if total else 0.0
    avg_lat_ms = (sum(r.latency_ms for r in results) / total) if total else 0.0
    max_lat_ms = max((r.latency_ms for r in results), default=0.0)
    q_title = str(question.get("title", "Untitled Question"))
    qid = str(question.get("questionId", "unknown"))

    log("INFO", f"[PIPELINE] Question {q_title} (QID: {qid})  -  {total} responses")
    log("INFO", "[PIPELINE] Pipeline: normalize -> deterministic -> embeddings -> jury -> consensus")
    log("INFO", "[PIPELINE] Status: RUNNING")
    log("INFO", "[PIPELINE] Fast Path")
    log("INFO", f"[PIPELINE]   Deterministic accepted: {fast_yes}")
    log("INFO", f"[PIPELINE]   Deterministic rejected: {fast_no}")
    log("INFO", f"[PIPELINE]   Sent to semantic stages: {sent_semantic}")
    log("INFO", "[PIPELINE] Consensus Summary")
    log("INFO", f"[PIPELINE]   YES: {yes}   NO: {no}")
    log("INFO", f"[PIPELINE]   Avg confidence: {avg_conf:.2f}")
    log("INFO", f"[PIPELINE]   Misconceptions flagged: {misconceptions}")
    log("INFO", f"[PIPELINE]   Avg latency/answer: {avg_lat_ms/1000.0:.1f}s   Max: {max_lat_ms/1000.0:.1f}s (budget: 30.0s)")
    log("INFO", "[PIPELINE] Progress")
    log("INFO", f"[PIPELINE]   Question progress: {_progress_bar(yes, total)}")
    log("INFO", "[PIPELINE] ------------------------------------------------------------")
    log("INFO", f"[PIPELINE] DONE Question {q_title} | YES={yes} NO={no}")


def evaluate_answers(question: Dict[str, object], answers: List[str], expected: Optional[List[str]] = None) -> List[str]:
    """Legacy-compatible evaluator entrypoint returning accepted answers."""
    log("INFO", "[SEMANTIC PIPELINE ACTIVE] ai_evaluator_semantic")
    log_ollama_gpu_diagnostics_once()
    expected_values = expected or []
    qtext = str(question.get("title", "Untitled Question"))
    results = semantic_evaluate_answers(answers, expected_values, qtext)
    _print_pretty_block(question, results)
    return [r.answer for r in results if r.decision == "YES"]

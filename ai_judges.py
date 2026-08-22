"""AI Judges - Clean architecture with structured output."""
import asyncio
import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    import aiohttp
except Exception:
    aiohttp = None
import requests
import ollama

from evaluator_config import configured_provider_names, load_config
from logger import log, update_runtime_state
from ollama_diagnostics import log_post_inference_gpu_probe_once
from ollama_options import build_ollama_options
from provider_manager import get_provider_manager, is_provider_available, make_request_id
from provider_types import ProviderError, ProviderRequest

_JUDGE_HTTP_LIMIT_LOCK = threading.Lock()
_JUDGE_HTTP_SEMAPHORE = None

_MODEL_PROGRESS_LOCK = threading.Lock()
_MODEL_PROGRESS_DONE = 0
_MODEL_PROGRESS_TOTAL = 0
_MODEL_PROGRESS_OVERFLOW = 0


def reset_model_progress() -> None:
    """Reset the logical model-work progress counter to 0/0."""
    global _MODEL_PROGRESS_DONE, _MODEL_PROGRESS_TOTAL, _MODEL_PROGRESS_OVERFLOW
    with _MODEL_PROGRESS_LOCK:
        _MODEL_PROGRESS_DONE = 0
        _MODEL_PROGRESS_TOTAL = 0
        _MODEL_PROGRESS_OVERFLOW = 0


def _model_progress_register(estimate: int) -> None:
    """Add planned logical work units to the current progress plan."""
    if estimate <= 0:
        return
    global _MODEL_PROGRESS_TOTAL
    with _MODEL_PROGRESS_LOCK:
        _MODEL_PROGRESS_TOTAL += estimate


def _model_progress_extend(units: int, reason: str = "") -> None:
    """Extend a plan when runtime provider fallback creates extra logical units."""
    global _MODEL_PROGRESS_TOTAL
    units = max(0, int(units))
    if not units:
        return
    with _MODEL_PROGRESS_LOCK:
        _MODEL_PROGRESS_TOTAL += units
        total = _MODEL_PROGRESS_TOTAL
    label = f" reason={reason}" if reason else ""
    print(f"ModelPlanAdjust: +{units} total={total}{label}", flush=True)


def configure_model_progress(total: int, scope: str = "") -> None:
    """Start a fixed logical-work plan before model workers run."""
    reset_model_progress()
    _model_progress_register(total)
    label = f" scope={scope}" if scope else ""
    print(f"ModelPlan: total={max(0, int(total))}{label}", flush=True)
    print(f"ModelProgress: 0/{max(0, int(total))}", flush=True)


def _model_progress_tick(units: int = 1) -> None:
    """Complete logical work units, never raw HTTP attempts."""
    global _MODEL_PROGRESS_DONE, _MODEL_PROGRESS_OVERFLOW
    units = max(0, int(units))
    with _MODEL_PROGRESS_LOCK:
        if _MODEL_PROGRESS_TOTAL <= 0:
            _MODEL_PROGRESS_DONE += units
            done = _MODEL_PROGRESS_DONE
            total = done
            overflow = _MODEL_PROGRESS_OVERFLOW
        else:
            requested_done = _MODEL_PROGRESS_DONE + units
            if requested_done > _MODEL_PROGRESS_TOTAL:
                _MODEL_PROGRESS_OVERFLOW += requested_done - _MODEL_PROGRESS_TOTAL
                _MODEL_PROGRESS_DONE = _MODEL_PROGRESS_TOTAL
            else:
                _MODEL_PROGRESS_DONE = requested_done
            done = _MODEL_PROGRESS_DONE
            total = _MODEL_PROGRESS_TOTAL
            overflow = _MODEL_PROGRESS_OVERFLOW
    if overflow:
        print(f"ModelProgressWarning: overflow={overflow} done={done}/{total}", flush=True)
    print(f"ModelProgress: {done}/{total}", flush=True)


def _estimate_model_calls_single(roles: List[str], adaptive_cfg: Dict[str, object]) -> int:
    """Estimated model calls for one answer graded through the per-answer path."""
    if bool(adaptive_cfg.get("enabled", False)):
        primary = [r for r in adaptive_cfg.get("primary_roles", ["semantic_judge", "factual_judge", "concept_judge"]) if r in roles]
        adjudicator = str(adaptive_cfg.get("adjudicator_role", "strict_judge"))
        return len(primary) + (1 if adjudicator in roles else 0)
    return len(roles)


def _estimate_model_calls_for_question(
    answers: List[str],
    batch_size: int,
    roles: List[str],
    adaptive_cfg: Dict[str, object],
) -> int:
    """Estimated model calls for one question graded through the model-first batch path.

    Progress counts one unit per answer per role regardless of batching, so the
    queue progress bar tracks the answer count in every provider mode.
    """
    per_role = len(answers)
    if bool(adaptive_cfg.get("enabled", False)):
        primary = [r for r in adaptive_cfg.get("primary_roles", ["semantic_judge", "factual_judge", "concept_judge"]) if r in roles]
        adjudicator = str(adaptive_cfg.get("adjudicator_role", "strict_judge"))
        return len(primary) * per_role + (per_role if adjudicator in roles else 0)
    return len(roles) * per_role


def estimate_form_model_calls(
    answers_by_qid: Dict[str, List[str]],
    cfg: Optional[Dict[str, object]] = None,
    model_first_batching: bool = False,
) -> int:
    """Estimated total model calls needed to grade a whole form.

    Sums the per-question (or per-answer) estimates for every question that
    has at least one fetched answer. The dispatcher uses this to announce an
    upfront ``ModelProgress: 0/{total}`` line before grading starts, so the
    GUI progress bar reflects successful model calls against a whole-form total.
    """
    cfg = cfg if cfg is not None else load_config()
    roles = _selected_roles(cfg)
    adaptive_cfg = cfg.get("adaptive_math_jury", {})
    if model_first_batching:
        batch_size = _judge_answer_batch_size(cfg, _preferred_batch_provider(cfg))
        return sum(
            _estimate_model_calls_for_question(answers, batch_size, roles, adaptive_cfg)
            for answers in answers_by_qid.values()
            if answers
        )
    per_answer = _estimate_model_calls_single(roles, adaptive_cfg)
    return sum(len(answers) * per_answer for answers in answers_by_qid.values() if answers)


def _get_judge_http_semaphore():
    global _JUDGE_HTTP_SEMAPHORE
    if _JUDGE_HTTP_SEMAPHORE is not None:
        return _JUDGE_HTTP_SEMAPHORE
    with _JUDGE_HTTP_LIMIT_LOCK:
        if _JUDGE_HTTP_SEMAPHORE is None:
            cfg = load_config()
            max_inflight = max(1, int(cfg.get("max_concurrent_judge_http", 4)))
            _JUDGE_HTTP_SEMAPHORE = threading.Semaphore(max_inflight)
            log("INFO", f"[JUDGES] HTTP concurrency limit enabled (max_concurrent_judge_http={max_inflight})")
    return _JUDGE_HTTP_SEMAPHORE


def _write_heartbeat_if_needed():
    """Write heartbeat to file for hang monitoring."""
    try:
        data = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "stage": "jury_consensus",
            "timestamp_epoch": time.time(),
        }
        with open("heartbeat.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


JUDGE_PROMPTS = {
    "semantic_judge": "You are a semantic meaning evaluator. Your ONLY job is to determine whether the student's answer conveys the same MEANING as the expected answer, regardless of wording, grammar, or spelling. Ignore surface form completely. Focus only on whether the core idea is the same.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "concept_judge": "You are an independent answer-equivalence challenger. The teacher answer is authoritative. Look only for a substantive difference in value or meaning; do not invent a replacement answer or extra requirements. Ignore missing units, working, explanation, rounding presentation, factorisation form, spelling, grammar, and harmless formatting when the core answer matches.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "factual_judge": "You are a factual accuracy checker for science and mathematics. Determine whether the student's answer is scientifically or mathematically correct, ignoring grammar and spelling. Flag anything factually wrong even if it sounds similar to the correct answer.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "strict_judge": "You are a strict but fair human examiner. Grade as you would in a real classroom. Do not accept vague or incomplete answers. Require the student to have demonstrated genuine understanding, not just a lucky guess.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "misconception_judge": "You are a misconception analyst. Your job is to detect whether the student's answer reveals a fundamental conceptual misunderstanding, even if parts of the answer sound correct on the surface. A misconception should lower the score significantly.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "language_filter": "You are a language quality assessor for ESL and Thai learner answers. Your job is to separate language errors (grammar, spelling, word order) from content errors. Report how much of the answer's incorrectness is due to language issues vs actual wrong content.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
}
REQUIRED_FIELDS = [
    "decision", "confidence", "reason_short", "requirements_met",
    "requirements_missing", "contradictions", "calculation_check",
]
BATCH_RESULT_FIELDS = ["answer_index", *REQUIRED_FIELDS]


def _selected_roles(cfg: Dict[str, object]) -> List[str]:
    raw = cfg.get("active_judge_roles", [])
    if isinstance(raw, list):
        roles = [str(r) for r in raw if str(r) in JUDGE_PROMPTS]
        if roles:
            return roles
    return list(JUDGE_PROMPTS.keys())


def prewarm_judge_runtime():
    """Optional warm-up to avoid first-call latency spikes on local model runtimes."""
    cfg = load_config()
    if not bool(cfg.get("judge_prewarm_enabled", False)):
        return
    model = str(cfg.get("models", {}).get("judge", [""])[0] if cfg.get("models", {}).get("judge") else "")
    if not model:
        return
    try:
        timeout_s = max(5, int(cfg.get("judge_prewarm_timeout_seconds", 20)))
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": 'Return only JSON: {"ok": true}'}],
            "stream": False,
            "options": {"num_predict": 8, "temperature": 0.0},
            "keep_alive": cfg.get("ollama_options", {}).get("keep_alive", "30m"),
            "format": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        }
        log("INFO", f"[JUDGES] prewarm START model={model}")
        if bool(cfg.get("provider_manager_enabled", True)):
            get_provider_manager().ask(
                ProviderRequest(
                    request_id=make_request_id("prewarm"),
                    judge_name="semantic_judge",
                    payload=payload,
                    timeout_s=timeout_s,
                    schema=payload["format"],
                    retries=1,
                    metadata={"request_kind": "prewarm", "provider_priority": ["ollama"]},
                )
            )
        else:
            resp = requests.post(_ollama_chat_url(), json=payload, timeout=(5, timeout_s))
            resp.raise_for_status()
        log("INFO", f"[JUDGES] prewarm DONE model={model}")
    except Exception as ex:
        log("WARNING", f"[JUDGES] prewarm failed: {ex}")


def _abstain(reason: str = "judge unavailable") -> Dict[str, object]:
    """Return an internal failure response; never a grading verdict."""
    return {
        "confidence": 0.0,
        "decision": "ERROR",
        "reason_short": reason,
        "requirements_met": [],
        "requirements_missing": [],
        "contradictions": [],
        "calculation_check": "unavailable",
    }


def _failure_category(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "empty_response"
    if text.count("{") > text.count("}") or text.endswith((":", ",")):
        return "truncated_json"
    try:
        obj = json.loads(text)
    except Exception:
        return "invalid_json"
    if not isinstance(obj, dict):
        return "non_object_json"
    if "decision" not in obj:
        return "missing_decision"
    if str(obj.get("decision", "")).strip().upper() not in {"YES", "NO"}:
        return "non_binary_decision"
    return "missing_required_field"


def _ollama_chat_url() -> str:
    cfg = load_config()
    base = str(cfg.get("ollama_api_base_url", "http://127.0.0.1:11434")).rstrip("/")
    return f"{base}/api/chat"


def _provider_manager_enabled() -> bool:
    return bool(load_config().get("provider_manager_enabled", True))


def _judge_start_model_label(role: str, requested_model: object) -> str:
    if _provider_manager_enabled():
        return f"provider=managed role={role}"
    return f"model={requested_model or '-'}"


def _pre_provider_active_model(role: str, requested_model: object) -> str:
    if _provider_manager_enabled():
        return f"provider-managed:{role}"
    return str(requested_model or "-")


def _unavailable_model_label(role: str, requested_model: object) -> str:
    if _provider_manager_enabled():
        return f"provider-managed:{role}"
    return str(requested_model or "-")


def _provider_schema(payload: Dict[str, object]) -> Optional[Dict[str, object]]:
    fmt = payload.get("format")
    return fmt if isinstance(fmt, dict) else None


def _lane_request_metadata(
    avoid_models: Optional[List[str]],
    provider_hint: Optional[str],
    batch_answer_count: Optional[int] = None,
) -> Dict[str, object]:
    """Build request metadata; a lane hint pins provider order for this call.

    The hint becomes metadata["provider_priority"], which ProviderManager's
    _provider_order() already honors, so each dual-lane worker routes to its
    own provider first while keeping normal failover to the others.
    """
    meta: Dict[str, object] = {"avoid_models": list(avoid_models or [])}
    if batch_answer_count is not None and int(batch_answer_count) > 1:
        meta["batch_answer_count"] = int(batch_answer_count)
    hint = str(provider_hint or "").strip().lower()
    if hint:
        try:
            rest = [p for p in configured_provider_names(load_config()) if p != hint]
        except Exception:
            rest = [p for p in ("openrouter", "llamacpp", "ollama") if p != hint]
        meta["provider_priority"] = [hint, *rest]
    return meta


def _ask_provider(
    role: str,
    payload: Dict[str, object],
    timeout_s: int,
    request_kind: str,
    metadata: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    request_metadata = {"request_kind": request_kind}
    if metadata:
        request_metadata.update(metadata)
    response = get_provider_manager().ask(
        ProviderRequest(
            request_id=make_request_id(request_kind),
            judge_name=role,
            payload=payload,
            timeout_s=timeout_s,
            schema=_provider_schema(payload),
            metadata=request_metadata,
        )
    )
    out = dict(response.payload)
    out["_provider_info"] = {
        "provider": response.provider,
        "model": response.model,
        "latency_ms": response.latency_ms,
        "queue_wait_ms": response.queue_wait_ms,
        "retry_count": response.retry_count,
        "tokens": response.tokens,
    }
    return out


def _provider_info(response: Dict[str, object]) -> Dict[str, object]:
    info = response.get("_provider_info")
    return info if isinstance(info, dict) else {}


def _chat_response(
    role: str,
    payload: Dict[str, object],
    timeout_s: int,
    request_kind: str,
    metadata: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    if _provider_manager_enabled():
        resp = _ask_provider(role, payload, timeout_s, request_kind, metadata)
        return resp
    resp = requests.post(_ollama_chat_url(), json=payload, timeout=(10, timeout_s))
    resp.raise_for_status()
    return resp.json()


def _annotate_provider_result(obj: Dict[str, object], response: Dict[str, object], fallback_model: str) -> Dict[str, object]:
    info = _provider_info(response)
    obj["model"] = str(info.get("model") or fallback_model)
    if info:
        obj["provider"] = str(info.get("provider") or "")
        obj["provider_latency_ms"] = float(info.get("latency_ms", 0.0) or 0.0)
        obj["provider_queue_wait_ms"] = float(info.get("queue_wait_ms", 0.0) or 0.0)
        obj["provider_retry_count"] = int(info.get("retry_count", 0) or 0)
    return obj


def parse_judge_response(raw: str) -> Dict[str, object]:
    """
    Single-pass JSON extraction. Clean architecture.

    Returns defaults if parsing fails, allowing the pipeline to continue gracefully.
    """
    # Handle empty response
    if not raw or not raw.strip():
        return _abstain("empty_response")

    # Strip markdown code blocks (```json, ```python, etc.) - handle both trailing backticks on same line or separate line
    clean = re.sub(r"```[a-z]*\n(.*?)(?:\n```|```$)", r"\1", raw, flags=re.IGNORECASE | re.DOTALL)
    # Strip think blocks (Qwen3) and tool_call tags
    clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.IGNORECASE | re.DOTALL)
    clean = re.sub(r"<\|.*?\|>", "", clean, flags=re.DOTALL)  # strip tool_call tags
    clean = clean.strip()

    # Try direct parse first (works if format= is respected)
    try:
        obj = json.loads(clean)
        if isinstance(obj, dict):
            # Check if this is a valid format (has decision field)
            if "decision" in obj:
                return obj
            # If it's valid JSON but not the expected format, try to map fields
            return _map_response_to_required_fields(obj)
    except json.JSONDecodeError:
        pass

    # Fallback: find first { ... } block
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                # Check if this is a valid format (has decision field)
                if "decision" in obj:
                    return obj
                # If it's valid JSON but not the expected format, try to map fields
                return _map_response_to_required_fields(obj)
        except json.JSONDecodeError:
            pass

    # Give up - return defaults for graceful degradation
    log("DEBUG", f"Failed to parse judge response, using defaults")
    log("DEBUG", f"  Raw content: {repr(raw)[:200]}")
    return _abstain("parse_failed")


def _map_response_to_required_fields(obj: Dict[str, object]) -> Dict[str, object]:
    """
    Map various JSON response formats to the required fields.
    Returns a dict with at least the 'decision' field set, others may remain defaults.
    """
    if isinstance(obj.get("results"), list) and obj["results"] and isinstance(obj["results"][0], dict):
        obj = obj["results"][0]

    # Start with defaults, but preserve any existing confidence
    result = _abstain("partial_mapping")
    # Preserve any confidence that might have been set
    preserved_confidence = obj.get("confidence")

    # Try to extract decision
    decision = None
    for key in ["decision", "is_correct", "coverage_score", "score"]:
        if key in obj:
            val = obj[key]
            if isinstance(val, (bool, int, float)):
                if key == "coverage_score":
                    # coverage_score is a score, not decision
                    result["confidence"] = float(val)
                elif key == "score":
                    # score might be a numeric score - convert to confidence
                    result["confidence"] = min(1.0, float(val) / 100.0)
                else:
                    decision = "YES" if val in [True, 1, "true", "YES"] else "NO"
            elif isinstance(val, str):
                decision = val.upper() if val.upper() in ["YES", "NO"] else None

    if decision:
        result["decision"] = decision

    # Try to extract confidence - use explicit confidence field if available
    if "confidence" in obj and isinstance(obj["confidence"], (int, float)):
        result["confidence"] = max(0.0, min(1.0, float(obj["confidence"])))
    elif "score" in obj and isinstance(obj["score"], (int, float)):
        result["confidence"] = max(0.0, min(1.0, float(obj["score"]) / 100.0))
    elif "confidence" not in result:
        result["confidence"] = 0.0

    # Try to extract other fields
    for key, req_key in [
        ("semantic_similarity", "semantic_similarity"),
        ("concept_coverage", "concept_coverage"),
        ("factual_accuracy", "factual_accuracy"),
        ("language_error_ratio", "language_noise_ratio"),
        ("language_error_percentage", "language_noise_ratio"),
    ]:
        if key in obj and isinstance(obj[key], (int, float)):
            result[req_key] = max(0.0, min(1.0, float(obj[key]) / 100.0 if "percentage" in key else obj[key]))

    return result


def _normalize_decision(d: Dict[str, object]) -> Dict[str, object]:
    """Normalize to a binary verdict or an internal retryable ERROR."""
    decision = str(d.get("decision", "ERROR")).strip().upper()
    if decision in {"0", "FALSE", "INCORRECT", "FAIL", "WRONG", "NO"}:
        d["decision"] = "NO"
    elif decision in {"1", "TRUE", "CORRECT", "PASS", "YES"}:
        d["decision"] = "YES"
    else:
        d["decision"] = "ERROR"
    return d


def _fill_judge_defaults(data: Dict[str, object]) -> Dict[str, object]:
    """Fill missing fields and clamp numeric values to [0.0, 1.0]."""
    defaults = _abstain("partial")
    for key in REQUIRED_FIELDS:
        if key not in data:
            data[key] = defaults[key]
    
    # Clamp confidence to [0.0, 1.0].
    for nf in ["confidence"]:
        try:
            val = float(data[nf])
            data[nf] = max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            data[nf] = 0.0
    
    data["reason_short"] = str(data.get("reason_short", ""))[:500]
    for key in ("requirements_met", "requirements_missing", "contradictions"):
        value = data.get(key, [])
        data[key] = [str(item)[:300] for item in value][:20] if isinstance(value, list) else []
    data["calculation_check"] = str(data.get("calculation_check", ""))[:500]
    return data


def _valid(d: Dict[str, object]) -> bool:
    """Check if judge result has all required fields and valid decision."""
    return (
        all(k in d for k in REQUIRED_FIELDS) 
        and str(d.get("decision")) in {"YES", "NO"}
    )


def _make_judge_prompt(question: str, expected: str, answer: str, comparison_evidence: Dict[str, object]) -> str:
    """Create prompt for judge."""
    def compact(value: object, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        head = int(limit * 0.75)
        return text[:head] + "\n...[irrelevant context omitted]...\n" + text[-(limit - head):]

    compact_question = compact(question, 8000)
    return (
        f"Whole-paper context (interpretation only): {compact_question}\n"
        f"AUTHORITATIVE TEACHER ANSWER: {expected}\n"
        f"STUDENT ANSWER: {answer}\n\n"
        "The first teacher answer is the sole source of truth. Never recalculate it, correct it, replace it, "
        "or invent another expected answer. Decide only whether the student's core value or meaning is close "
        "enough to the teacher answer in this question's context. Accept equivalent algebra, decimal commas, "
        "equivalent fractions/percentages, capitalization, spelling, grammar, punctuation, Unicode symbols, "
        "and harmless whitespace. Accept a correct core answer when units, working, explanation, requested "
        "rounding presentation, or requested algebraic form are missing. Accept harmless extra compatible units. "
        "Reject units only when they are explicitly incompatible and materially change the answer. "
        "If the teacher answer contains alternatives joined by 'or', '/', or semicolons, accept the student "
        "answer when it correctly gives ANY ONE complete alternative unless the question explicitly asks for all parts. "
        "Do not mark the other alternatives as missing when one valid alternative is supplied. "
        "For example, if the teacher answer is 'No lines of symmetry or rotational symmetry order 4', then "
        "'Rotational symmetry order of 4' is a complete correct alternative and MUST be YES with no missing "
        "requirement for 'No lines of symmetry'. Do not reinterpret that teacher answer as 'no rotational symmetry'. "
        "For symmetry count/order answers, a bare number can be a valid shorthand when it matches the requested "
        "count or order in context; for example, expected '2 lines of symmetry or rotational symmetry order 2' "
        "and student answer '2' should usually be YES unless the question requires a written explanation. "
        "If a student gives one clearly correct alternative plus an extra imperfect phrase, accept the correct "
        "alternative unless the extra phrase directly negates or invalidates that same alternative. "
        "Be lenient with short negative answers. If the teacher answer begins with a clear negative condition "
        "such as 'No lines of symmetry', then a student answer like 'No', 'none', or '0' may correctly express "
        "that negative condition in context; do not reject it only because it is brief. "
        "Do not borrow requirements from nearby questions in the whole-paper context.\n\n"
        "You MUST make a binary decision. Choose YES if the answer is correct, otherwise choose NO. "
        "Never abstain, defer, or return an uncertain verdict. Uncertainty must be expressed only in "
        "the numeric confidence field while decision remains YES or NO. "
        "Base the verdict on explicit evidence. Return ONLY one compact JSON object in this exact shape: "
        '{"decision":"YES","confidence":0.95,"reason_short":"brief reason",'
        '"requirements_met":["requirement supported by the answer"],'
        '"requirements_missing":[],"contradictions":[],"calculation_check":"verified or not applicable"}'
    )


def _make_batch_judge_prompt(
    question: str,
    expected: str,
    indexed_answers: List[tuple[int, str]],
    comparison_evidence_by_index: Dict[int, Dict[str, object]],
) -> str:
    """Create prompt for a judge to evaluate a small batch of answers independently."""
    def compact(value: object, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        head = int(limit * 0.75)
        return text[:head] + "\n...[irrelevant context omitted]...\n" + text[-(limit - head):]

    compact_question = compact(question, 8000)
    answer_lines = []
    for index, answer in indexed_answers:
        evidence = comparison_evidence_by_index.get(index, {})
        evidence_text = compact(json.dumps(evidence, ensure_ascii=False), 1200)
        answer_lines.append(
            f"ANSWER_INDEX {index}\n"
            f"STUDENT ANSWER: {answer}\n"
            f"COMPARISON EVIDENCE: {evidence_text}"
        )

    return (
        f"Whole-paper context (interpretation only): {compact_question}\n"
        f"AUTHORITATIVE TEACHER ANSWER: {expected}\n\n"
        "Evaluate EACH student answer independently. Do not let one student's answer influence another. "
        "The first teacher answer is the sole source of truth. Never recalculate it, correct it, replace it, "
        "or invent another expected answer. Decide only whether each student's core value or meaning is close "
        "enough to the teacher answer in this question's context. Accept equivalent algebra, decimal commas, "
        "equivalent fractions/percentages, capitalization, spelling, grammar, punctuation, Unicode symbols, "
        "and harmless whitespace. Accept a correct core answer when units, working, explanation, requested "
        "rounding presentation, or requested algebraic form are missing. Accept harmless extra compatible units. "
        "Reject units only when they are explicitly incompatible and materially change the answer. "
        "If the teacher answer contains alternatives joined by 'or', '/', or semicolons, accept the student "
        "answer when it correctly gives ANY ONE complete alternative unless the question explicitly asks for all parts. "
        "Do not mark the other alternatives as missing when one valid alternative is supplied. "
        "For example, if the teacher answer is 'No lines of symmetry or rotational symmetry order 4', then "
        "'Rotational symmetry order of 4' is a complete correct alternative and MUST be YES with no missing "
        "requirement for 'No lines of symmetry'. Do not reinterpret that teacher answer as 'no rotational symmetry'. "
        "For symmetry count/order answers, a bare number can be a valid shorthand when it matches the requested "
        "count or order in context; for example, expected '2 lines of symmetry or rotational symmetry order 2' "
        "and student answer '2' should usually be YES unless the question requires a written explanation. "
        "If a student gives one clearly correct alternative plus an extra imperfect phrase, accept the correct "
        "alternative unless the extra phrase directly negates or invalidates that same alternative. "
        "Be lenient with short negative answers. If the teacher answer begins with a clear negative condition "
        "such as 'No lines of symmetry', then a student answer like 'No', 'none', or '0' may correctly express "
        "that negative condition in context; do not reject it only because it is brief. "
        "Do not borrow requirements from nearby questions in the whole-paper context.\n\n"
        "You MUST make a binary YES/NO decision for every ANSWER_INDEX. Never skip an answer. Never abstain. "
        "Return exactly one result object for each ANSWER_INDEX, using the same answer_index number. "
        "Return ONLY one compact JSON object in this exact shape:\n"
        '{"results":[{"answer_index":1,"decision":"YES","confidence":0.95,'
        '"reason_short":"brief reason","requirements_met":["requirement supported by the answer"],'
        '"requirements_missing":[],"contradictions":[],"calculation_check":"verified or not applicable"}]}\n\n'
        "Student answers to evaluate:\n"
        + "\n\n".join(answer_lines)
    )


def _extract_json_object(raw: str) -> object:
    if not raw or not raw.strip():
        raise ValueError("empty_response")
    clean = re.sub(r"```[a-z]*\n(.*?)(?:\n```|```$)", r"\1", raw, flags=re.IGNORECASE | re.DOTALL)
    clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.IGNORECASE | re.DOTALL)
    clean = re.sub(r"<\|.*?\|>", "", clean, flags=re.DOTALL)
    clean = clean.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def parse_batch_judge_response(raw: str, expected_indices: List[int]) -> Dict[int, Dict[str, object]]:
    """Parse a batch judge response into per-answer judge objects."""
    try:
        obj = _extract_json_object(raw)
    except Exception as ex:
        log("DEBUG", f"[BATCH PARSE] JSON extraction failed: {ex} raw={repr(raw)[:300]}")
        return {}
    if not isinstance(obj, dict):
        log("DEBUG", f"[BATCH PARSE] top-level response is not a dict: {type(obj)}")
        return {}
    results = obj.get("results")
    if not isinstance(results, list):
        log("DEBUG", f"[BATCH PARSE] 'results' key missing or not a list; keys={list(obj.keys())}")
        return {}

    expected_set = set(expected_indices)
    parsed: Dict[int, Dict[str, object]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            answer_index = int(item.get("answer_index"))
        except (TypeError, ValueError):
            log("DEBUG", f"[BATCH PARSE] item has no valid answer_index: {item}")
            continue
        if answer_index not in expected_set:
            log("DEBUG", f"[BATCH PARSE] unexpected answer_index={answer_index} (expected {sorted(expected_set)})")
            continue
        if answer_index in parsed:
            continue
        normalized = _normalize_decision(dict(item))
        normalized = _fill_judge_defaults(normalized)
        if _valid(normalized):
            parsed[answer_index] = normalized
        else:
            log("DEBUG", f"[BATCH PARSE] answer_index={answer_index} failed validation: "
                f"decision={normalized.get('decision')} category={_failure_category(str(item))}")
    return parsed


def _get_judge_format() -> Dict[str, object]:
    """Return JSON schema for structured output."""
    return {
        "type": "object",
        "properties": {
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "decision": {"type": "string", "enum": ["YES", "NO"]},
            "reason_short": {"type": "string", "maxLength": 500},
            "requirements_met": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "requirements_missing": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "contradictions": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "calculation_check": {"type": "string", "maxLength": 500}
        },
        "required": REQUIRED_FIELDS
    }


def _get_batch_judge_format() -> Dict[str, object]:
    """Return JSON schema for batched structured output."""
    item_properties = {
        "answer_index": {"type": "integer", "minimum": 1},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "decision": {"type": "string", "enum": ["YES", "NO"]},
        "reason_short": {"type": "string", "maxLength": 500},
        "requirements_met": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "requirements_missing": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "contradictions": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "calculation_check": {"type": "string", "maxLength": 500},
    }
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": item_properties,
                    "required": BATCH_RESULT_FIELDS,
                },
            }
        },
        "required": ["results"],
    }


def _get_ollama_options(role: str) -> Dict[str, object]:
    """Get Ollama options for a judge role."""
    out = build_ollama_options(
        ctx_key="judge_num_ctx",
        default_ctx=2048,
        predict_key="judge_num_predict",
        default_predict=256,
    )
    out["temperature"] = 0.0
    out["top_p"] = 0.9
    return out


def _get_batch_ollama_options(role: str, batch_size: int) -> Dict[str, object]:
    out = _get_ollama_options(role)
    cfg = load_config()
    # Each answer needs ~350 output tokens for a full judge JSON object.
    # Scale generously so later answers in the batch are never truncated.
    per_answer_tokens = int(cfg.get("judge_batch_tokens_per_answer", 350))
    min_predict = per_answer_tokens * max(1, batch_size)
    batch_predict = int(cfg.get("judge_batch_num_predict", max(1024, min_predict)))
    out["num_predict"] = max(int(out.get("num_predict", 512)), batch_predict)
    # Ensure context window is wide enough for prompt + all student answers + full response.
    # The default 2048 is far too small for batches of 3+; scale with batch_size.
    min_ctx = max(4096, batch_size * 1024)
    out["num_ctx"] = max(int(out.get("num_ctx", 2048)), min_ctx)
    return out


async def call_judge_async(
    session,
    model: str,
    role: str,
    answer: str,
    question: str,
    expected: str,
    rubric: Dict[str, object],
    retries: int
) -> Dict[str, object]:
    """Call a judge using Ollama with structured output."""
    _write_heartbeat_if_needed()
    cfg = load_config()
    judge_timeout_s = max(10, int(cfg.get("judge_timeout_seconds", 45)))
    judge_http_timeout_s = max(judge_timeout_s, int(cfg.get("judge_http_timeout_seconds", 60)))
    start = time.perf_counter()
    log("INFO", f"START judge_{role} ({_judge_start_model_label(role, model)})")
    update_runtime_state(active_model=_pre_provider_active_model(role, model), active_role=role, active_since=time.time())

    base_prompt = _make_judge_prompt(question, expected, answer, rubric)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JUDGE_PROMPTS[role]},
            {"role": "user", "content": base_prompt}
        ],
        "stream": False,
        "options": _get_ollama_options(role),
        "format": _get_judge_format(),  # Enforce structured JSON output
        "timeout": judge_http_timeout_s,
    }

    sem = _get_judge_http_semaphore()
    sem_wait = max(3, int(cfg.get("judge_http_semaphore_wait_seconds", judge_timeout_s)))

    for attempt in range(retries):
        if attempt:
            payload["messages"][1]["content"] = (
                base_prompt + "\n\nREPAIR: Your previous response was invalid. Output only the required JSON object."
            )
        acquired = False
        if not _provider_manager_enabled():
            if not sem.acquire(timeout=sem_wait):
                log("WARNING", f"Judge {role} semaphore wait timeout ({sem_wait}s)")
                continue
            acquired = True
        try:
            if _provider_manager_enabled():
                data = await asyncio.to_thread(_chat_response, role, payload, judge_http_timeout_s, "judge")
            else:
                async with session.post(_ollama_chat_url(), json=payload, timeout=judge_http_timeout_s) as resp:
                    data = await resp.json()

            content = data.get("message", {}).get("content", "")

            # Handle empty response
            if not content or content == "":
                log("WARNING", f"Judge {role} attempt {attempt+1}/{retries}: Empty response from model")
                continue

            # Parse JSON using parse_judge_response (handles markdown code blocks)
            if isinstance(content, dict):
                obj = content
            else:
                obj = parse_judge_response(content)
                if not _valid(obj):
                    log("WARNING", f"Judge {role} attempt {attempt+1}/{retries}: Invalid parsed response")
                    continue
            
            obj = _normalize_decision(obj)
            obj = _fill_judge_defaults(obj)
            if _valid(obj):
                obj["role"] = role
                obj = _annotate_provider_result(obj, data, model)
                duration_ms = (time.perf_counter() - start) * 1000
                log_post_inference_gpu_probe_once("judge_async")
                _log_judge_result(role, str(obj.get("model") or model), duration_ms, obj.get("decision", "ERROR"), obj.get("confidence", 0.0), obj)
                _write_heartbeat_if_needed()
                return obj
            log("WARNING", f"Judge {role} invalid output category={_failure_category(content)} raw={repr(content)[:1000]}")

        except json.JSONDecodeError as ex:
            content = data.get("message", {}).get("content", "") if 'data' in locals() else ""
            log("WARNING", f"Judge {role} JSON decode error: {ex}")
            log("WARNING", f"  Content: {repr(content)[:200]}")
        except Exception as ex:
            content = data.get("message", {}).get("content", "") if 'data' in locals() else ""
            log("WARNING", f"Judge {role} failed: {ex}")
            log("WARNING", f"  Content: {repr(content)[:200]}")
        finally:
            if acquired:
                try:
                    sem.release()
                except Exception:
                    pass

    out = _abstain("retries_exhausted")
    out.update({"role": role, "model": _unavailable_model_label(role, model)})
    return out


def _log_judge_result(role: str, model: str, duration_ms: float, decision: str, confidence: float, evidence=None):
    """Log judge completion with timing and result."""
    provider = f" provider={evidence.get('provider')}" if isinstance(evidence, dict) and evidence.get("provider") else ""
    log("INFO", f"END judge_{role} duration_ms={duration_ms:.0f} decision={decision} confidence={confidence:.2f} (model={model}{provider})")
    if isinstance(evidence, dict):
        log(
            "INFO",
            f"[JUDGE EVIDENCE] role={role} model={model} provider={evidence.get('provider', '')!r} reason={evidence.get('reason_short', '')!r} "
            f"met={evidence.get('requirements_met', [])!r} missing={evidence.get('requirements_missing', [])!r} "
            f"contradictions={evidence.get('contradictions', [])!r} calculation={evidence.get('calculation_check', '')!r}",
        )


def call_judge_role_sync(
    role: str,
    answer: str,
    question: str,
    expected: str,
    rubric: Dict[str, object],
    retries: int = 3,
    avoid_models: Optional[List[str]] = None,
    provider_hint: Optional[str] = None,
) -> Dict[str, object]:
    """Run one judge role for one answer.

    This is intentionally public so the dispatcher/pipeline can run judges
    model-first across a whole question: role A judges all answers, then role B
    judges all answers, etc. That keeps Ollama from swapping models for every
    single student answer.
    """
    cfg = load_config()
    jury_models = cfg.get("jury_models", {})
    TIMEOUT_SECONDS = max(10, int(cfg.get("judge_timeout_seconds", 45)))
    http_timeout_seconds = max(TIMEOUT_SECONDS, int(cfg.get("judge_http_timeout_seconds", 60)))
    role_model = jury_models.get(role)

    def _call_once(repair: bool = False) -> Dict[str, object]:
        _write_heartbeat_if_needed()
        start = time.perf_counter()
        log("INFO", f"START judge_{role} ({_judge_start_model_label(role, role_model)})")
        update_runtime_state(active_model=_pre_provider_active_model(role, role_model), active_role=role, active_since=time.time())
        user_prompt = _make_judge_prompt(question, expected, answer, rubric)
        if repair:
            user_prompt += "\n\nREPAIR: Your previous response was invalid. Output only the required JSON object."
        payload = {
            "model": role_model,
            "messages": [
                {"role": "system", "content": JUDGE_PROMPTS[role]},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": _get_ollama_options(role),
            "format": _get_judge_format(),
            "timeout": http_timeout_seconds,
        }
        sem = _get_judge_http_semaphore()
        sem_wait = max(3, int(cfg.get("judge_http_semaphore_wait_seconds", TIMEOUT_SECONDS)))
        acquired = False
        if not _provider_manager_enabled():
            if not sem.acquire(timeout=sem_wait):
                log("WARNING", f"Judge {role} semaphore wait timeout ({sem_wait}s); no verdict produced")
                duration_ms = (time.perf_counter() - start) * 1000
                _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
                out = _abstain("semaphore_timeout")
                out.update({"role": role, "model": _unavailable_model_label(role, role_model)})
                return out
            acquired = True

        try:
            response = _chat_response(
                role,
                payload,
                TIMEOUT_SECONDS,
                "judge",
                metadata=_lane_request_metadata(avoid_models, provider_hint),
            )
        except ProviderError as ex:
            category = getattr(ex, "category", "provider_error")
            log("WARNING", f"Judge {role} provider attempt failed category={category}: {ex}")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
            out = _abstain(category)
            out.update({"role": role, "model": _unavailable_model_label(role, role_model)})
            return out
        except requests.Timeout:
            log("WARNING", f"Judge {role} timed out after {TIMEOUT_SECONDS}s without a binary verdict")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
            out = _abstain("timeout")
            out.update({"role": role, "model": _unavailable_model_label(role, role_model)})
            return out
        except Exception as ex:
            log("WARNING", f"Judge {role} sync attempt failed: {ex}")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
            out = _abstain("exception")
            out.update({"role": role, "model": _unavailable_model_label(role, role_model)})
            return out
        finally:
            if acquired:
                try:
                    sem.release()
                except Exception:
                    pass

        raw = response.get("message", {}).get("content", "")
        obj = parse_judge_response(raw) if isinstance(raw, str) else raw
        obj = _normalize_decision(obj)
        obj = _fill_judge_defaults(obj)
        duration_ms = (time.perf_counter() - start) * 1000
        if _valid(obj):
            obj["role"] = role
            obj = _annotate_provider_result(obj, response, role_model)
            log_post_inference_gpu_probe_once("judge_sync")
            _log_judge_result(role, str(obj.get("model") or role_model), duration_ms, obj.get("decision", "ERROR"), obj.get("confidence", 0.0), obj)
            _write_heartbeat_if_needed()
            return obj
        _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
        log("WARNING", f"Judge {role} invalid output category={_failure_category(raw)} raw={repr(raw)[:1000]}")
        _write_heartbeat_if_needed()
        out = _abstain("invalid_response")
        out.update({"role": role, "model": _unavailable_model_label(role, role_model)})
        return out

    last = None
    for attempt in range(max(1, retries)):
        last = _call_once(repair=attempt > 0)
        if str(last.get("decision", "ERROR")).upper() in {"YES", "NO"}:
            return last
        log("WARNING", f"Judge {role} returned no binary verdict on attempt {attempt + 1}/{max(1, retries)}; retrying")
    return last or _abstain("retries_exhausted")


def _chunked(items: List[str], size: int) -> List[List[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _openrouter_model_used(result: Dict[str, object]) -> str:
    if str(result.get("provider", "")).casefold() != "openrouter":
        return ""
    return str(result.get("model", "")).strip()


def _preferred_batch_provider(cfg: Optional[Dict[str, object]] = None) -> str:
    cfg = cfg if cfg is not None else load_config()
    if not bool(cfg.get("provider_manager_enabled", True)):
        return "ollama"
    strategy = str(cfg.get("provider_strategy", "") or "").strip().lower().replace("-", "_")
    strategy_map = {
        "ollama_only": "ollama",
        "local_only": "ollama",
        "llamacpp_only": "llamacpp",
        "llama_cpp_only": "llamacpp",
        "llama.cpp_only": "llamacpp",
        "openrouter_only": "openrouter",
        "cheap_paid_only": "openrouter",
        "openrouter_then_llamacpp": "openrouter",
        "openrouter_llamacpp": "openrouter",
        "openrouter_llamacpp_ollama": "openrouter",
        "all_providers": "openrouter",
        "llamacpp_then_openrouter": "llamacpp",
        "llamacpp_openrouter": "llamacpp",
        "local_all": "llamacpp",
    }
    candidates: List[str] = []
    preferred = strategy_map.get(strategy)
    if preferred:
        candidates.append(preferred)
    priority = cfg.get("provider_priority", ["openrouter", "llamacpp", "ollama"])
    if not isinstance(priority, list):
        priority = ["openrouter", "llamacpp", "ollama"]
    for provider in priority:
        provider_name = str(provider).strip().lower()
        if provider_name in {"openrouter", "llamacpp", "ollama"} and provider_name not in candidates:
            candidates.append(provider_name)
    if not candidates:
        return "openrouter"
    try:
        for provider_name in candidates:
            if is_provider_available(provider_name):
                return provider_name
    except Exception as ex:
        log("DEBUG", f"Batch provider availability check failed: {ex}")
    return candidates[0]


def _judge_answer_batch_size(cfg: Optional[Dict[str, object]] = None, provider: Optional[str] = None) -> int:
    """Read provider-specific judge answer batch size from config.

    This is intentionally cheap and called during model-first judging so a
    Settings save can affect later roles/chunks in a running grading process.
    The legacy judge_answer_batch_size remains a fallback for older configs.
    """
    cfg = cfg if cfg is not None else load_config()
    provider_name = (provider or _preferred_batch_provider(cfg)).strip().lower()
    legacy = int(cfg.get("judge_answer_batch_size", 3))
    if provider_name == "ollama":
        return max(1, int(cfg.get("ollama_judge_answer_batch_size", legacy)))
    if provider_name == "openrouter":
        return max(1, int(cfg.get("openrouter_judge_answer_batch_size", legacy)))
    if provider_name in {"llamacpp", "llama.cpp", "llama_cpp"}:
        return 1
    return max(1, legacy)


def _local_answer_batch_size(cfg: Optional[Dict[str, object]] = None) -> int:
    cfg = cfg if cfg is not None else load_config()
    priority = cfg.get("provider_priority", ["ollama"])
    if not isinstance(priority, list):
        priority = ["ollama"]
    sizes: List[int] = []
    for provider in priority:
        provider_name = str(provider).strip().lower()
        if provider_name in {"llamacpp", "llama.cpp", "llama_cpp"}:
            sizes.append(1)
        elif provider_name == "ollama":
            sizes.append(_judge_answer_batch_size(cfg, "ollama"))
    if sizes:
        return max(1, min(sizes))
    return _judge_answer_batch_size(cfg, "ollama")


def _ollama_answer_batch_size(cfg: Optional[Dict[str, object]] = None) -> int:
    return _local_answer_batch_size(cfg)


def call_judge_role_batch_sync(
    role: str,
    answers: List[str],
    question: str,
    expected: str,
    rubrics_by_answer: Dict[str, Dict[str, object]],
    retries: int = 3,
    avoid_models: Optional[List[str]] = None,
    provider_hint: Optional[str] = None,
) -> Dict[str, Dict[str, object]]:
    """Run one judge role for a small batch of answers in one Ollama call.

    Invalid/missing batch items are retried through the single-answer path so
    batching improves speed without weakening correctness.
    """
    if not answers:
        return {}
    cfg = load_config()
    jury_models = cfg.get("jury_models", {})
    TIMEOUT_SECONDS = max(10, int(cfg.get("judge_timeout_seconds", 45)))
    http_timeout_seconds = max(TIMEOUT_SECONDS, int(cfg.get("judge_http_timeout_seconds", 60)))
    role_model = jury_models.get(role)
    indexed_answers = [(i + 1, answer) for i, answer in enumerate(answers)]
    comparison_by_index = {
        i + 1: rubrics_by_answer.get(answer, {})
        for i, answer in enumerate(answers)
    }
    last_provider_error_category = ""
    split_fallback_used = False

    def _call_once(repair: bool = False) -> Dict[int, Dict[str, object]]:
        nonlocal last_provider_error_category
        last_provider_error_category = ""
        _write_heartbeat_if_needed()
        start = time.perf_counter()
        log("INFO", f"START judge_{role}_batch ({_judge_start_model_label(role, role_model)}, answers={len(answers)})")
        update_runtime_state(active_model=_pre_provider_active_model(role, role_model), active_role=role, active_since=time.time())
        user_prompt = _make_batch_judge_prompt(question, expected, indexed_answers, comparison_by_index)
        if repair:
            user_prompt += "\n\nREPAIR: Your previous response was invalid or incomplete. Output only the required JSON object with one result for every answer_index."
        payload = {
            "model": role_model,
            "messages": [
                {"role": "system", "content": JUDGE_PROMPTS[role]},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": _get_batch_ollama_options(role, len(answers)),
            "format": _get_batch_judge_format(),
            "timeout": http_timeout_seconds,
        }
        sem = _get_judge_http_semaphore()
        sem_wait = max(3, int(cfg.get("judge_http_semaphore_wait_seconds", TIMEOUT_SECONDS)))
        acquired = False
        if not _provider_manager_enabled():
            if not sem.acquire(timeout=sem_wait):
                log("WARNING", f"Judge {role} batch semaphore wait timeout ({sem_wait}s); falling back")
                return {}
            acquired = True
        raw = ""
        try:
            response = _chat_response(
                role,
                payload,
                TIMEOUT_SECONDS,
                "judge",
                metadata=_lane_request_metadata(avoid_models, provider_hint, len(answers)),
            )
            raw = response.get("message", {}).get("content", "")
            parsed = parse_batch_judge_response(raw, [idx for idx, _ in indexed_answers])
        except ProviderError as ex:
            last_provider_error_category = ex.category
            log("WARNING", f"Judge {role} batch provider attempt failed category={ex.category}: {ex}; falling back")
            parsed = {}
        except requests.Timeout:
            log("WARNING", f"Judge {role} batch timed out after {TIMEOUT_SECONDS}s; falling back")
            parsed = {}
        except Exception as ex:
            log("WARNING", f"Judge {role} batch attempt failed: {ex}")
            parsed = {}
        finally:
            if acquired:
                try:
                    sem.release()
                except Exception:
                    pass

        for result in parsed.values():
            result["role"] = role
            _annotate_provider_result(result, response if "response" in locals() else {}, role_model)
        duration_ms = (time.perf_counter() - start) * 1000
        provider_info = _provider_info(response) if "response" in locals() else {}
        actual_provider = str(provider_info.get("provider") or ("ollama" if not _provider_manager_enabled() else "-"))
        actual_model = str(provider_info.get("model") or role_model)
        log(
            "INFO",
            f"END judge_{role}_batch duration_ms={duration_ms:.0f} parsed={len(parsed)}/{len(answers)} "
            f"(provider={actual_provider}, model={actual_model}, requested_model={role_model})",
        )
        if len(parsed) != len(answers):
            log("WARNING", f"Judge {role} batch incomplete parsed={len(parsed)}/{len(answers)} raw={repr(raw)[:1000]}")
        if parsed:
            log_post_inference_gpu_probe_once("judge_batch_sync")
        _write_heartbeat_if_needed()
        return parsed

    parsed_by_index: Dict[int, Dict[str, object]] = {}
    for attempt in range(max(1, retries)):
        parsed_by_index = _call_once(repair=attempt > 0)
        if len(parsed_by_index) == len(answers):
            break
        provider_managed = _provider_manager_enabled()
        ollama_limit = _ollama_answer_batch_size()
        if (
            provider_managed
            and last_provider_error_category
            and
            not split_fallback_used
            and len(answers) > ollama_limit
        ):
            split_fallback_used = True
            error_reason = last_provider_error_category or "incomplete batch results"
            log(
                "WARNING",
                f"Judge {role} batch switching to Ollama-sized chunks after {error_reason}; "
                f"answers={len(answers)} chunk_size={ollama_limit}",
            )
            chunked_by_index: Dict[int, Dict[str, object]] = {}
            for chunk_start in range(0, len(indexed_answers), ollama_limit):
                indexed_chunk = indexed_answers[chunk_start:chunk_start + ollama_limit]
                chunk_answers = [answer for _index, answer in indexed_chunk]
                chunk_results = call_judge_role_batch_sync(
                    role,
                    chunk_answers,
                    question,
                    expected,
                    rubrics_by_answer,
                    retries=1,
                    avoid_models=avoid_models,
                    provider_hint=provider_hint,
                )
                for index, answer in indexed_chunk:
                    if answer in chunk_results:
                        chunked_by_index[index] = chunk_results[answer]
            parsed_by_index = chunked_by_index
            if len(parsed_by_index) == len(answers):
                break
        log("WARNING", f"Judge {role} batch returned incomplete results on attempt {attempt + 1}/{max(1, retries)}; retrying")

    out: Dict[str, Dict[str, object]] = {}
    for index, answer in indexed_answers:
        result = parsed_by_index.get(index)
        if result is None:
            log("WARNING", f"Judge {role} batch missing answer_index={index}; falling back to single-answer judge")
            result = call_judge_role_sync(
                role,
                answer,
                question,
                expected,
                rubrics_by_answer.get(answer, {}),
                retries,
                avoid_models=avoid_models,
                provider_hint=provider_hint,
            )
        out[answer] = result
    return out


def run_judges_model_first(
    answers: List[str],
    question: str,
    expected: str,
    rubrics_by_answer: Dict[str, Dict[str, object]],
    retries: int = 3,
    provider_hint: Optional[str] = None,
    progress_callback: Optional[object] = None,
) -> Dict[str, List[Dict[str, object]]]:
    """Run judges by model/role across all answers for one question.

    ``provider_hint`` pins routing for dual-lane dispatch: chunk sizing uses
    the hinted provider's batch size and every judge call prefers that
    provider first (normal failover to the others still applies).
    """
    cfg = load_config()
    roles = _selected_roles(cfg)
    out: Dict[str, List[Dict[str, object]]] = {answer: [] for answer in answers}
    used_openrouter_models_by_answer: Dict[str, List[str]] = {answer: [] for answer in answers}
    adaptive_cfg = cfg.get("adaptive_math_jury", {})
    initial_batch_provider = str(provider_hint).strip().lower() if provider_hint else _preferred_batch_provider(cfg)
    initial_batch_size = _judge_answer_batch_size(cfg, initial_batch_provider)

    def avoid_models_for_chunk(role_answers: List[str]) -> List[str]:
        seen: Dict[str, None] = {}
        for answer in role_answers:
            for model in used_openrouter_models_by_answer.get(answer, []):
                seen.setdefault(model, None)
        return list(seen)

    def remember_used_model(answer: str, result: Dict[str, object]) -> None:
        used_model = _openrouter_model_used(result)
        if used_model and used_model not in used_openrouter_models_by_answer.setdefault(answer, []):
            used_openrouter_models_by_answer[answer].append(used_model)

    def run_role_for_answers(role: str, role_answers: List[str]) -> int:
        if not role_answers:
            return 0
        completed_units = 0
        runtime_cfg = load_config()
        batch_provider = str(provider_hint).strip().lower() if provider_hint else _preferred_batch_provider(runtime_cfg)
        batch_size = _judge_answer_batch_size(runtime_cfg, batch_provider)
        # A chunk can fail over to ANY other configured provider mid-flight.
        # The effective chunk must fit the smallest limit in that chain, or
        # the fallback provider receives oversized batches it cannot fulfill
        # (llama.cpp returned only answer_index=1 for 25-answer chunks).
        try:
            chain_limits = [
                _judge_answer_batch_size(runtime_cfg, provider_name)
                for provider_name in configured_provider_names(runtime_cfg)
            ]
            if chain_limits:
                batch_size = min([batch_size, *chain_limits])
        except Exception:
            pass
        planned_units = len(role_answers)
        actual_units = len(role_answers)
        if actual_units > planned_units:
            _model_progress_extend(actual_units - planned_units, reason=f"{role}:{batch_provider}")
        if batch_size <= 1:
            for answer in role_answers:
                avoid_models = avoid_models_for_chunk([answer])
                result = call_judge_role_sync(
                    role,
                    answer,
                    question,
                    expected,
                    rubrics_by_answer.get(answer, {}),
                    retries,
                    avoid_models=avoid_models,
                    provider_hint=provider_hint,
                )
                out[answer].append(result)
                remember_used_model(answer, result)
                completed_units += 1
                _model_progress_tick()
                if callable(progress_callback):
                    try:
                        progress_callback(1)
                    except Exception:
                        pass
            remaining = max(0, planned_units - completed_units)
            _model_progress_tick(remaining)
            if callable(progress_callback) and remaining > 0:
                try:
                    progress_callback(remaining)
                except Exception:
                    pass
            return completed_units
        for chunk in _chunked(role_answers, batch_size):
            avoid_models = avoid_models_for_chunk(chunk)
            batch_results = call_judge_role_batch_sync(
                role,
                chunk,
                question,
                expected,
                rubrics_by_answer,
                retries,
                avoid_models=avoid_models,
                provider_hint=provider_hint,
            )
            for answer in chunk:
                result = batch_results[answer]
                out[answer].append(result)
                remember_used_model(answer, result)
            completed_units += len(chunk)
            _model_progress_tick(len(chunk))
            if callable(progress_callback):
                try:
                    progress_callback(len(chunk))
                except Exception:
                    pass
        remaining = max(0, planned_units - completed_units)
        _model_progress_tick(remaining)
        if callable(progress_callback) and remaining > 0:
            try:
                progress_callback(remaining)
            except Exception:
                pass
        return completed_units

    log(
        "INFO",
        f"[JUDGES] Model-first question batch START answers={len(answers)} roles={roles} "
        f"batch_provider={initial_batch_provider} answer_batch_size={initial_batch_size}",
    )
    if bool(adaptive_cfg.get("enabled", False)):
        primary_roles = [
            role for role in adaptive_cfg.get("primary_roles", ["semantic_judge", "factual_judge", "concept_judge"])
            if role in roles
        ]
        adjudicator_role = str(adaptive_cfg.get("adjudicator_role", "strict_judge"))
        threshold = float(adaptive_cfg.get("minimum_primary_confidence", 0.90))
        ambiguity_words = tuple(str(x).casefold() for x in adaptive_cfg.get(
            "ambiguity_markers", ["ambiguous", "uncertain", "unclear", "insufficient", "depends"]
        ))

        for role in primary_roles:
            log("INFO", f"[JUDGES] Model-first role START role={role} answers={len(answers)}")
            run_role_for_answers(role, answers)
            log("INFO", f"[JUDGES] Model-first role DONE role={role}")

        needs_adjudication: List[str] = []
        for answer in answers:
            judges = out[answer]
            valid_primary = all(str(j.get("decision", "ERROR")) in {"YES", "NO"} for j in judges)
            decisions = [str(j.get("decision", "ERROR")) for j in judges]
            confidences = [float(j.get("confidence", 0.0) or 0.0) for j in judges]
            ambiguous = any(
                any(marker in str(j.get("reason_short", "")).casefold() for marker in ambiguity_words)
                for j in judges
            ) or any(j.get("requirements_missing") or j.get("contradictions") for j in judges)
            if (
                len(judges) != len(primary_roles)
                or not valid_primary
                or len(set(decisions)) != 1
                or min(confidences, default=0.0) < threshold
                or ambiguous
            ):
                needs_adjudication.append(answer)

        if needs_adjudication and adjudicator_role in roles:
            log("INFO", f"[JUDGES] Model-first adjudicator START role={adjudicator_role} answers={len(needs_adjudication)}")
            actual_units = run_role_for_answers(adjudicator_role, needs_adjudication)
            reserved_units = len(answers)
            _model_progress_tick(max(0, reserved_units - actual_units))
            log("INFO", f"[JUDGES] Model-first adjudicator DONE role={adjudicator_role}")
        else:
            reserved_units = len(answers)
            _model_progress_tick(reserved_units if adjudicator_role in roles else 0)
            log("INFO", "[JUDGES] Model-first adjudicator skipped")
        return out

    for role in roles:
        log("INFO", f"[JUDGES] Model-first role START role={role} answers={len(answers)}")
        run_role_for_answers(role, answers)
        log("INFO", f"[JUDGES] Model-first role DONE role={role}")
    return out


async def run_all_judges_with_early_exit(
    answer: str,
    question: str,
    expected: str,
    rubric: Dict[str, object],
    retries: int = 3
) -> List[Dict[str, object]]:
    """Run all judges with early exit if unanimous + high confidence."""
    # Write heartbeat before expensive operations
    _write_heartbeat_if_needed()

    cfg = load_config()
    jury_models = cfg.get("jury_models", {})
    judge_timeout_s = max(10, int(cfg.get("judge_timeout_seconds", 45)))
    ee = cfg.get("early_exit", {})
    min_judges = int(ee.get("min_judges", 3))
    agree_thresh = float(ee.get("agreement_confidence", 0.90))
    enabled = bool(ee.get("enabled", True))

    if aiohttp is None:
        log("WARNING", "aiohttp not installed; falling back to synchronous judge calls")
        return _run_judges_sync(answer, question, expected, rubric, jury_models, retries)

    # Run judges concurrently
    tasks = {}
    roles = _selected_roles(cfg)
    async with aiohttp.ClientSession() as session:
        for role in roles:
            role_model = jury_models.get(role)
            tasks[asyncio.create_task(call_judge_async(
                session, role_model, role, answer, question, expected, rubric, retries
            ))] = role
        
        results: List[Dict[str, object]] = []
        for done in asyncio.as_completed(tasks):
            try:
                r = await asyncio.wait_for(done, timeout=judge_timeout_s)
            except asyncio.TimeoutError:
                log("WARNING", "Judge call timed out without a binary verdict; retry state recorded")
                r = _abstain("timeout")
            results.append(r)
            _model_progress_tick()
            
            # Early exit check
            if enabled and len(results) >= min_judges:
                decisions = [x.get("decision") for x in results]
                confs = [float(x.get("confidence", 0.0)) for x in results]
                avg_conf = sum(confs) / len(confs) if confs else 0.0
                
                if len(set(decisions)) == 1 and avg_conf >= agree_thresh:
                    pending_count = sum(1 for t in tasks if not t.done())
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    _model_progress_tick(pending_count)
                    log("DEBUG", f"Early exit: {len(results)} judges, unanimous {decisions[0]} @ {avg_conf:.2f}")
                    break
        
        return results


def _run_judges_sync(
    answer: str,
    question: str,
    expected: str,
    rubric: Dict[str, object],
    jury_models: Dict[str, str],
    retries: int,
    provider_hint: Optional[str] = None
) -> List[Dict[str, object]]:
    """Synchronous judge execution (fallback when aiohttp unavailable)."""
    cfg = load_config()
    TIMEOUT_SECONDS = max(10, int(cfg.get("judge_timeout_seconds", 45)))
    http_timeout_seconds = max(TIMEOUT_SECONDS, int(cfg.get("judge_http_timeout_seconds", 60)))
    roles = _selected_roles(cfg)
    sync_parallelism = max(1, int(cfg.get("sync_judge_parallelism", len(roles))))
    ee = cfg.get("early_exit", {})
    ee_enabled = bool(ee.get("enabled", True))
    ee_min = max(1, int(ee.get("min_judges", 3)))
    ee_agree = float(ee.get("agreement_confidence", 0.90))

    def _call_one_once(role: str, repair: bool = False, avoid_models: Optional[List[str]] = None) -> Dict[str, object]:
        _write_heartbeat_if_needed()
        role_model = jury_models.get(role)
        start = time.perf_counter()
        log("INFO", f"START judge_{role} ({_judge_start_model_label(role, role_model)})")
        update_runtime_state(active_model=_pre_provider_active_model(role, role_model), active_role=role, active_since=time.time())
        user_prompt = _make_judge_prompt(question, expected, answer, rubric)
        if repair:
            user_prompt += "\n\nREPAIR: Your previous response was invalid. Output only the required JSON object."
        payload = {
            "model": role_model,
            "messages": [
                {"role": "system", "content": JUDGE_PROMPTS[role]},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": _get_ollama_options(role),
            "format": _get_judge_format(),
            "timeout": http_timeout_seconds,
        }
        sem = _get_judge_http_semaphore()
        sem_wait = max(3, int(cfg.get("judge_http_semaphore_wait_seconds", TIMEOUT_SECONDS)))
        acquired = False
        if not _provider_manager_enabled():
            if not sem.acquire(timeout=sem_wait):
                log("WARNING", f"Judge {role} semaphore wait timeout ({sem_wait}s); no verdict produced")
                duration_ms = (time.perf_counter() - start) * 1000
                _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
                out = _abstain("semaphore_timeout"); out.update({"role": role, "model": _unavailable_model_label(role, role_model)}); return out
            acquired = True

        try:
            response = _chat_response(
                role,
                payload,
                TIMEOUT_SECONDS,
                "judge",
                metadata=_lane_request_metadata(avoid_models, provider_hint),
            )
        except ProviderError as ex:
            category = getattr(ex, "category", "provider_error")
            log("WARNING", f"Judge {role} provider attempt failed category={category}: {ex}")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
            out = _abstain(category); out.update({"role": role, "model": _unavailable_model_label(role, role_model)}); return out
        except requests.Timeout:
            log("WARNING", f"Judge {role} timed out after {TIMEOUT_SECONDS}s without a binary verdict")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
            out = _abstain("timeout"); out.update({"role": role, "model": _unavailable_model_label(role, role_model)}); return out
        except Exception as ex:
            log("WARNING", f"Judge {role} sync attempt failed: {ex}")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
            out = _abstain("exception"); out.update({"role": role, "model": _unavailable_model_label(role, role_model)}); return out
        finally:
            if acquired:
                try:
                    sem.release()
                except Exception:
                    pass

        raw = response.get("message", {}).get("content", "")
        obj = parse_judge_response(raw) if isinstance(raw, str) else raw
        obj = _normalize_decision(obj)
        obj = _fill_judge_defaults(obj)
        duration_ms = (time.perf_counter() - start) * 1000
        if _valid(obj):
            obj["role"] = role
            obj = _annotate_provider_result(obj, response, role_model)
            log_post_inference_gpu_probe_once("judge_sync")
            _log_judge_result(role, str(obj.get("model") or role_model), duration_ms, obj.get("decision", "ERROR"), obj.get("confidence", 0.0), obj)
            _write_heartbeat_if_needed()
            return obj
        _log_judge_result(role, _unavailable_model_label(role, role_model), duration_ms, "ERROR", 0.0)
        log("WARNING", f"Judge {role} invalid output category={_failure_category(raw)} raw={repr(raw)[:1000]}")
        _write_heartbeat_if_needed()
        out = _abstain("invalid_response"); out.update({"role": role, "model": _unavailable_model_label(role, role_model)}); return out

    def _call_one(role: str, avoid_models: Optional[List[str]] = None) -> Dict[str, object]:
        """Retry abstentions so transient/invalid model output is not final."""
        last = None
        for attempt in range(max(1, retries)):
            last = _call_one_once(role, repair=attempt > 0, avoid_models=avoid_models)
            if str(last.get("decision", "ERROR")).upper() in {"YES", "NO"}:
                return last
            log("WARNING", f"Judge {role} returned no binary verdict on attempt {attempt + 1}/{max(1, retries)}; retrying")
        return last or _abstain("retries_exhausted")

    out: List[Dict[str, object]] = []
    adaptive_cfg = cfg.get("adaptive_math_jury", {})
    if bool(adaptive_cfg.get("enabled", False)):
        primary_roles = [
            role for role in adaptive_cfg.get("primary_roles", ["semantic_judge", "factual_judge", "concept_judge"])
            if role in roles
        ]
        adjudicator_role = str(adaptive_cfg.get("adjudicator_role", "strict_judge"))
        threshold = float(adaptive_cfg.get("minimum_primary_confidence", 0.90))
        ambiguity_words = tuple(str(x).casefold() for x in adaptive_cfg.get(
            "ambiguity_markers", ["ambiguous", "uncertain", "unclear", "insufficient", "depends"]
        ))
        used_openrouter_models: List[str] = []
        for role in primary_roles:
            result = _call_one(role, avoid_models=used_openrouter_models)
            out.append(result)
            _model_progress_tick()
            used_model = _openrouter_model_used(result)
            if used_model and used_model not in used_openrouter_models:
                used_openrouter_models.append(used_model)

        valid_primary = all(str(j.get("decision", "ERROR")) in {"YES", "NO"} for j in out)
        decisions = [str(j.get("decision", "ERROR")) for j in out]
        confidences = [float(j.get("confidence", 0.0) or 0.0) for j in out]
        ambiguous = any(
            any(marker in str(j.get("reason_short", "")).casefold() for marker in ambiguity_words)
            for j in out
        ) or any(j.get("requirements_missing") or j.get("contradictions") for j in out)
        expected_primary_count = len(primary_roles)
        needs_adjudicator = (
            len(out) != expected_primary_count
            or not valid_primary
            or len(set(decisions)) != 1
            or min(confidences, default=0.0) < threshold
            or ambiguous
        )
        if needs_adjudicator and adjudicator_role in roles:
            reason = "invalid" if not valid_primary else "disagreement" if len(set(decisions)) != 1 else "low-confidence/ambiguous"
            log("INFO", f"[JUDGES] Escalating to {adjudicator_role}: {reason}")
            out.append(_call_one(adjudicator_role, avoid_models=used_openrouter_models))
            _model_progress_tick()
        else:
            if adjudicator_role in roles:
                _model_progress_tick()
            log("INFO", f"[JUDGES] {expected_primary_count} primary roles agree confidently; adjudicator skipped")
        return out

    if sync_parallelism <= 1:
        used_openrouter_models: List[str] = []
        for role in roles:
            result = _call_one(role, avoid_models=used_openrouter_models)
            out.append(result)
            _model_progress_tick()
            used_model = _openrouter_model_used(result)
            if used_model and used_model not in used_openrouter_models:
                used_openrouter_models.append(used_model)
            if ee_enabled and len(out) >= ee_min:
                decisions = [str(x.get("decision", "ERROR")) for x in out]
                confs = [float(x.get("confidence", 0.0)) for x in out]
                avg_conf = (sum(confs) / len(confs)) if confs else 0.0
                if len(set(decisions)) == 1 and avg_conf >= ee_agree:
                    break
        return out

    with ThreadPoolExecutor(max_workers=min(sync_parallelism, len(roles))) as ex:
        fut_to_role = {ex.submit(_call_one, role): role for role in roles}
        pending = set(fut_to_role.keys())
        try:
            for fut in as_completed(fut_to_role.keys(), timeout=http_timeout_seconds + 5):
                pending.discard(fut)
                try:
                    out.append(fut.result())
                    _model_progress_tick()
                except Exception as exx:
                    log("WARNING", f"Judge future failed: {exx}")
                    out.append(_abstain("future_exception"))
                    _model_progress_tick()

                if ee_enabled and len(out) >= ee_min:
                    decisions = [str(x.get("decision", "ERROR")) for x in out]
                    confs = [float(x.get("confidence", 0.0)) for x in out]
                    avg_conf = (sum(confs) / len(confs)) if confs else 0.0
                    if len(set(decisions)) == 1 and avg_conf >= ee_agree:
                        for pf in list(pending):
                            pf.cancel()
                        _model_progress_tick(len(pending))
                        break
        except FuturesTimeoutError:
            log("WARNING", "Synchronous judge batch timed out; unresolved judges produced no verdict")
        finally:
            for pf in pending:
                pf.cancel()

    # Keep output size stable for downstream logic.
    while len(out) < ee_min:
        out.append(_abstain("insufficient_judges"))
    return out


def run_judges(
    answer: str,
    question: str,
    expected: str,
    rubric: Dict[str, object],
    retries: int = 3,
    provider_hint: Optional[str] = None
) -> List[Dict[str, object]]:
    """Public API - run all judges with asyncio support.
    
    This function safely handles both asyncio and non-asyncio contexts.
    In GUI applications (PySide6) or other contexts with existing event loops,
    it uses the existing loop instead of creating a new one.
    """
    import asyncio
    
    cfg = load_config()
    use_async = bool(cfg.get("enable_async_judges", False))
    
    if not use_async or aiohttp is None:
        # Fallback to synchronous execution
        log("INFO", "Running judges synchronously (async disabled or aiohttp unavailable)")
        jury_models = cfg.get("jury_models", {})
        return _run_judges_sync(answer, question, expected, rubric, jury_models, retries, provider_hint)
    
    try:
        # Try to get the existing event loop first
        loop = asyncio.get_event_loop()
        # If we're in the main thread and the loop is running, use it
        if loop.is_running():
            log("DEBUG", "Detected running event loop; executing judges in dedicated async thread")
            import threading
            result_container = [None]
            exception_container = [None]
            
            def run_async():
                try:
                    result_container[0] = asyncio.run(
                        run_all_judges_with_early_exit(answer, question, expected, rubric, retries)
                    )
                except Exception as e:
                    exception_container[0] = e
            
            thread = threading.Thread(target=run_async, daemon=True)
            thread.start()
            thread.join(timeout=300)  # 5 minute max wait
            
            if thread.is_alive():
                log("WARNING", "Async judge execution timed out after 300s")
                raise TimeoutError("Async judge execution timed out")
            
            if exception_container[0]:
                raise exception_container[0]
            
            return result_container[0]
            
    except RuntimeError:
        # No event loop exists, create a new one
        log("DEBUG", "Creating new event loop for judge execution")
        pass
    
    # No existing loop, create a new one
    try:
        return asyncio.run(run_all_judges_with_early_exit(answer, question, expected, rubric, retries))
    except RuntimeError as e:
        # This should not happen after the try/except above, but handle it just in case
        log("ERROR", f"Failed to run async judges: {e}")
        # Fallback to sync
        jury_models = cfg.get("jury_models", {})
        return _run_judges_sync(answer, question, expected, rubric, jury_models, retries)

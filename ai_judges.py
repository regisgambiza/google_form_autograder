"""AI Judges - Clean architecture with structured output."""
import asyncio
import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Dict, List

try:
    import aiohttp
except Exception:
    aiohttp = None
import requests
import ollama

from evaluator_config import load_config
from logger import log, update_runtime_state
from ollama_diagnostics import log_post_inference_gpu_probe_once
from ollama_options import build_ollama_options

_JUDGE_HTTP_LIMIT_LOCK = threading.Lock()
_JUDGE_HTTP_SEMAPHORE = None


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
            "messages": [{"role": "user", "content": "Reply with: OK"}],
            "stream": False,
            "options": {"num_predict": 8, "temperature": 0.0},
            "keep_alive": cfg.get("ollama_options", {}).get("keep_alive", "30m"),
        }
        log("INFO", f"[JUDGES] prewarm START model={model}")
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
        "Do not borrow requirements from nearby questions in the whole-paper context.\n\n"
        "You MUST make a binary decision. Choose YES if the answer is correct, otherwise choose NO. "
        "Never abstain, defer, or return an uncertain verdict. Uncertainty must be expressed only in "
        "the numeric confidence field while decision remains YES or NO. "
        "Base the verdict on explicit evidence. Return ONLY one compact JSON object in this exact shape: "
        '{"decision":"YES","confidence":0.95,"reason_short":"brief reason",'
        '"requirements_met":["requirement supported by the answer"],'
        '"requirements_missing":[],"contradictions":[],"calculation_check":"verified or not applicable"}'
    )


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
    log("INFO", f"START judge_{role} (model={model})")
    update_runtime_state(active_model=model, active_role=role, active_since=time.time())

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
        if not sem.acquire(timeout=sem_wait):
            log("WARNING", f"Judge {role} semaphore wait timeout ({sem_wait}s)")
            continue
        try:
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
                obj["model"] = model
                duration_ms = (time.perf_counter() - start) * 1000
                log_post_inference_gpu_probe_once("judge_async")
                _log_judge_result(role, model, duration_ms, obj.get("decision", "ERROR"), obj.get("confidence", 0.0), obj)
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
            try:
                sem.release()
            except Exception:
                pass

    out = _abstain("retries_exhausted")
    out.update({"role": role, "model": model})
    return out


def _log_judge_result(role: str, model: str, duration_ms: float, decision: str, confidence: float, evidence=None):
    """Log judge completion with timing and result."""
    log("INFO", f"END judge_{role} duration_ms={duration_ms:.0f} decision={decision} confidence={confidence:.2f} (model={model})")
    if isinstance(evidence, dict):
        log(
            "INFO",
            f"[JUDGE EVIDENCE] role={role} model={model} reason={evidence.get('reason_short', '')!r} "
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
        log("INFO", f"START judge_{role} (model={role_model})")
        update_runtime_state(active_model=role_model, active_role=role, active_since=time.time())
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
        if not sem.acquire(timeout=sem_wait):
            log("WARNING", f"Judge {role} semaphore wait timeout ({sem_wait}s); no verdict produced")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ERROR", 0.0)
            out = _abstain("semaphore_timeout")
            out.update({"role": role, "model": role_model})
            return out

        try:
            resp = requests.post(
                _ollama_chat_url(),
                json=payload,
                timeout=(10, TIMEOUT_SECONDS),
            )
            resp.raise_for_status()
            response = resp.json()
        except requests.Timeout:
            log("WARNING", f"Judge {role} timed out after {TIMEOUT_SECONDS}s without a binary verdict")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ERROR", 0.0)
            out = _abstain("timeout")
            out.update({"role": role, "model": role_model})
            return out
        except Exception as ex:
            log("WARNING", f"Judge {role} sync attempt failed: {ex}")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ERROR", 0.0)
            out = _abstain("exception")
            out.update({"role": role, "model": role_model})
            return out
        finally:
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
            obj["model"] = role_model
            log_post_inference_gpu_probe_once("judge_sync")
            _log_judge_result(role, role_model, duration_ms, obj.get("decision", "ERROR"), obj.get("confidence", 0.0), obj)
            _write_heartbeat_if_needed()
            return obj
        _log_judge_result(role, role_model, duration_ms, "ERROR", 0.0)
        log("WARNING", f"Judge {role} invalid output category={_failure_category(raw)} raw={repr(raw)[:1000]}")
        _write_heartbeat_if_needed()
        out = _abstain("invalid_response")
        out.update({"role": role, "model": role_model})
        return out

    last = None
    for attempt in range(max(1, retries)):
        last = _call_once(repair=attempt > 0)
        if str(last.get("decision", "ERROR")).upper() in {"YES", "NO"}:
            return last
        log("WARNING", f"Judge {role} returned no binary verdict on attempt {attempt + 1}/{max(1, retries)}; retrying")
    return last or _abstain("retries_exhausted")


def run_judges_model_first(
    answers: List[str],
    question: str,
    expected: str,
    rubrics_by_answer: Dict[str, Dict[str, object]],
    retries: int = 3,
) -> Dict[str, List[Dict[str, object]]]:
    """Run judges by model/role across all answers for one question."""
    cfg = load_config()
    roles = _selected_roles(cfg)
    out: Dict[str, List[Dict[str, object]]] = {answer: [] for answer in answers}
    adaptive_cfg = cfg.get("adaptive_math_jury", {})

    log("INFO", f"[JUDGES] Model-first question batch START answers={len(answers)} roles={roles}")
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
            for answer in answers:
                out[answer].append(call_judge_role_sync(role, answer, question, expected, rubrics_by_answer.get(answer, {}), retries))
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
            for answer in needs_adjudication:
                out[answer].append(call_judge_role_sync(adjudicator_role, answer, question, expected, rubrics_by_answer.get(answer, {}), retries))
            log("INFO", f"[JUDGES] Model-first adjudicator DONE role={adjudicator_role}")
        else:
            log("INFO", "[JUDGES] Model-first adjudicator skipped")
        return out

    for role in roles:
        log("INFO", f"[JUDGES] Model-first role START role={role} answers={len(answers)}")
        for answer in answers:
            out[answer].append(call_judge_role_sync(role, answer, question, expected, rubrics_by_answer.get(answer, {}), retries))
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
            
            # Early exit check
            if enabled and len(results) >= min_judges:
                decisions = [x.get("decision") for x in results]
                confs = [float(x.get("confidence", 0.0)) for x in results]
                avg_conf = sum(confs) / len(confs) if confs else 0.0
                
                if len(set(decisions)) == 1 and avg_conf >= agree_thresh:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    log("DEBUG", f"Early exit: {len(results)} judges, unanimous {decisions[0]} @ {avg_conf:.2f}")
                    break
        
        return results


def _run_judges_sync(
    answer: str,
    question: str,
    expected: str,
    rubric: Dict[str, object],
    jury_models: Dict[str, str],
    retries: int
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

    def _call_one_once(role: str, repair: bool = False) -> Dict[str, object]:
        _write_heartbeat_if_needed()
        role_model = jury_models.get(role)
        start = time.perf_counter()
        log("INFO", f"START judge_{role} (model={role_model})")
        update_runtime_state(active_model=role_model, active_role=role, active_since=time.time())
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
        if not sem.acquire(timeout=sem_wait):
            log("WARNING", f"Judge {role} semaphore wait timeout ({sem_wait}s); no verdict produced")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ERROR", 0.0)
            out = _abstain("semaphore_timeout"); out.update({"role": role, "model": role_model}); return out

        try:
            resp = requests.post(
                _ollama_chat_url(),
                json=payload,
                timeout=(10, TIMEOUT_SECONDS),
            )
            resp.raise_for_status()
            response = resp.json()
        except requests.Timeout:
            log("WARNING", f"Judge {role} timed out after {TIMEOUT_SECONDS}s without a binary verdict")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ERROR", 0.0)
            out = _abstain("timeout"); out.update({"role": role, "model": role_model}); return out
        except Exception as ex:
            log("WARNING", f"Judge {role} sync attempt failed: {ex}")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ERROR", 0.0)
            out = _abstain("exception"); out.update({"role": role, "model": role_model}); return out
        finally:
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
            obj["model"] = role_model
            log_post_inference_gpu_probe_once("judge_sync")
            _log_judge_result(role, role_model, duration_ms, obj.get("decision", "ERROR"), obj.get("confidence", 0.0), obj)
            _write_heartbeat_if_needed()
            return obj
        _log_judge_result(role, role_model, duration_ms, "ERROR", 0.0)
        log("WARNING", f"Judge {role} invalid output category={_failure_category(raw)} raw={repr(raw)[:1000]}")
        _write_heartbeat_if_needed()
        out = _abstain("invalid_response"); out.update({"role": role, "model": role_model}); return out

    def _call_one(role: str) -> Dict[str, object]:
        """Retry abstentions so transient/invalid model output is not final."""
        last = None
        for attempt in range(max(1, retries)):
            last = _call_one_once(role, repair=attempt > 0)
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
        for role in primary_roles:
            out.append(_call_one(role))

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
            out.append(_call_one(adjudicator_role))
        else:
            log("INFO", f"[JUDGES] {expected_primary_count} primary roles agree confidently; adjudicator skipped")
        return out

    if sync_parallelism <= 1:
        for role in roles:
            out.append(_call_one(role))
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
                except Exception as exx:
                    log("WARNING", f"Judge future failed: {exx}")
                    out.append(_abstain("future_exception"))

                if ee_enabled and len(out) >= ee_min:
                    decisions = [str(x.get("decision", "ERROR")) for x in out]
                    confs = [float(x.get("confidence", 0.0)) for x in out]
                    avg_conf = (sum(confs) / len(confs)) if confs else 0.0
                    if len(set(decisions)) == 1 and avg_conf >= ee_agree:
                        for pf in list(pending):
                            pf.cancel()
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
    retries: int = 3
) -> List[Dict[str, object]]:
    """Public API - run all judges with asyncio support.
    
    This function safely handles both asyncio and non-asyncio contexts.
    In GUI applications (PyQt5) or other contexts with existing event loops,
    it uses the existing loop instead of creating a new one.
    """
    import asyncio
    
    cfg = load_config()
    use_async = bool(cfg.get("enable_async_judges", False))
    
    if not use_async or aiohttp is None:
        # Fallback to synchronous execution
        log("INFO", "Running judges synchronously (async disabled or aiohttp unavailable)")
        jury_models = cfg.get("jury_models", {})
        return _run_judges_sync(answer, question, expected, rubric, jury_models, retries)
    
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

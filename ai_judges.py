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
from logger import log
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
    "concept_judge": "You are a concept coverage checker. Given the required concepts for a correct answer, determine what percentage of them appear in the student's answer (even if expressed differently). Return a coverage score from 0.0 to 1.0.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "factual_judge": "You are a factual accuracy checker for science and mathematics. Determine whether the student's answer is scientifically or mathematically correct, ignoring grammar and spelling. Flag anything factually wrong even if it sounds similar to the correct answer.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "strict_judge": "You are a strict but fair human examiner. Grade as you would in a real classroom. Do not accept vague or incomplete answers. Require the student to have demonstrated genuine understanding, not just a lucky guess.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "misconception_judge": "You are a misconception analyst. Your job is to detect whether the student's answer reveals a fundamental conceptual misunderstanding, even if parts of the answer sound correct on the surface. A misconception should lower the score significantly.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
    "language_filter": "You are a language quality assessor for ESL and Thai learner answers. Your job is to separate language errors (grammar, spelling, word order) from content errors. Report how much of the answer's incorrectness is due to language issues vs actual wrong content.\n\nCRITICAL: Your response MUST be ONLY valid JSON. No explanations, no markdown, no text before or after.",
}
REQUIRED_FIELDS = ["semantic_similarity", "concept_coverage", "factual_accuracy", "misconception_detected", "misconception_description", "language_noise_ratio", "confidence", "decision", "reason_short"]


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
    """Return a default abstain response."""
    return {
        "semantic_similarity": 0.0,
        "concept_coverage": 0.0,
        "factual_accuracy": 0.0,
        "misconception_detected": False,
        "misconception_description": "",
        "language_noise_ratio": 0.0,
        "confidence": 0.0,
        "decision": "ABSTAIN",
        "reason_short": reason
    }


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
                decision = val.upper() if val.upper() in ["YES", "NO", "ABSTAIN"] else None

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
    """Normalize decision field to YES/NO/ABSTAIN."""
    decision = str(d.get("decision", "ABSTAIN")).strip().upper()
    if decision in {"0", "FALSE", "INCORRECT", "FAIL", "WRONG", "NO"}:
        d["decision"] = "NO"
    elif decision in {"1", "TRUE", "CORRECT", "PASS", "YES"}:
        d["decision"] = "YES"
    else:
        d["decision"] = "ABSTAIN"
    return d


def _fill_judge_defaults(data: Dict[str, object]) -> Dict[str, object]:
    """Fill missing fields and clamp numeric values to [0.0, 1.0]."""
    defaults = _abstain("partial")
    for key in REQUIRED_FIELDS:
        if key not in data:
            data[key] = defaults[key]
    
    # Clamp numeric fields to [0.0, 1.0]
    for nf in ["semantic_similarity", "concept_coverage", "factual_accuracy", "language_noise_ratio", "confidence"]:
        try:
            val = float(data[nf])
            data[nf] = max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            data[nf] = 0.0
    
    # Normalize misconception_detected to boolean
    if isinstance(data.get("misconception_detected"), str):
        data["misconception_detected"] = data["misconception_detected"].lower() in {"true", "yes", "1"}
    elif not isinstance(data.get("misconception_detected"), bool):
        data["misconception_detected"] = bool(data.get("misconception_detected", False))
    
    return data


def _valid(d: Dict[str, object]) -> bool:
    """Check if judge result has all required fields and valid decision."""
    return (
        all(k in d for k in REQUIRED_FIELDS) 
        and str(d.get("decision")) in {"YES", "NO", "ABSTAIN"}
    )


def _make_judge_prompt(question: str, expected: str, answer: str, rubric: Dict[str, object]) -> str:
    """Create prompt for judge."""
    return (
        f"Question: {question}\n"
        f"Expected: {expected}\n"
        f"Answer: {answer}\n\n"
        f"Rubric for reference:\n{json.dumps(rubric)}\n\n"
        "Provide your evaluation as a JSON object with these fields:"
    )


def _get_judge_format() -> Dict[str, object]:
    """Return JSON schema for structured output."""
    return {
        "type": "object",
        "properties": {
            "semantic_similarity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "concept_coverage": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "factual_accuracy": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "misconception_detected": {"type": "boolean"},
            "misconception_description": {"type": "string"},
            "language_noise_ratio": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "decision": {"type": "string", "enum": ["YES", "NO", "ABSTAIN"]},
            "reason_short": {"type": "string"}
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
    out["temperature"] = 0.1
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

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JUDGE_PROMPTS[role]},
            {"role": "user", "content": _make_judge_prompt(question, expected, answer, rubric)}
        ],
        "stream": False,
        "options": _get_ollama_options(role),
        "format": _get_judge_format(),  # Enforce structured JSON output
        "timeout": judge_http_timeout_s,
    }

    sem = _get_judge_http_semaphore()
    sem_wait = max(3, int(cfg.get("judge_http_semaphore_wait_seconds", judge_timeout_s)))

    for attempt in range(retries):
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
                duration_ms = (time.perf_counter() - start) * 1000
                log_post_inference_gpu_probe_once("judge_async")
                _log_judge_result(role, model, duration_ms, obj.get("decision", "ABSTAIN"), obj.get("confidence", 0.0))
                _write_heartbeat_if_needed()
                return obj

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

    return _abstain("retries_exhausted")


def _log_judge_result(role: str, model: str, duration_ms: float, decision: str, confidence: float):
    """Log judge completion with timing and result."""
    log("INFO", f"END judge_{role} duration_ms={duration_ms:.0f} decision={decision} confidence={confidence:.2f} (model={model})")


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
                log("WARNING", "Judge call timed out, using ABSTAIN")
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

    def _call_one(role: str) -> Dict[str, object]:
        _write_heartbeat_if_needed()
        role_model = jury_models.get(role)
        start = time.perf_counter()
        log("INFO", f"START judge_{role} (model={role_model})")
        payload = {
            "model": role_model,
            "messages": [
                {"role": "system", "content": JUDGE_PROMPTS[role]},
                {"role": "user", "content": _make_judge_prompt(question, expected, answer, rubric)},
            ],
            "stream": False,
            "options": _get_ollama_options(role),
            "format": _get_judge_format(),
            "timeout": http_timeout_seconds,
        }
        sem = _get_judge_http_semaphore()
        sem_wait = max(3, int(cfg.get("judge_http_semaphore_wait_seconds", TIMEOUT_SECONDS)))
        if not sem.acquire(timeout=sem_wait):
            log("WARNING", f"Judge {role} semaphore wait timeout ({sem_wait}s), falling back to ABSTAIN")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ABSTAIN", 0.0)
            return _abstain("semaphore_timeout")

        try:
            resp = requests.post(
                _ollama_chat_url(),
                json=payload,
                timeout=(10, TIMEOUT_SECONDS),
            )
            resp.raise_for_status()
            response = resp.json()
        except requests.Timeout:
            log("WARNING", f"Judge {role} timed out after {TIMEOUT_SECONDS}s, falling back to ABSTAIN")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ABSTAIN", 0.0)
            return _abstain("timeout")
        except Exception as ex:
            log("WARNING", f"Judge {role} sync attempt failed: {ex}")
            duration_ms = (time.perf_counter() - start) * 1000
            _log_judge_result(role, role_model, duration_ms, "ABSTAIN", 0.0)
            return _abstain("exception")
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
            log_post_inference_gpu_probe_once("judge_sync")
            _log_judge_result(role, role_model, duration_ms, obj.get("decision", "ABSTAIN"), obj.get("confidence", 0.0))
            _write_heartbeat_if_needed()
            return obj
        _log_judge_result(role, role_model, duration_ms, "ABSTAIN", 0.0)
        _write_heartbeat_if_needed()
        return _abstain("invalid_response")

    out: List[Dict[str, object]] = []
    if sync_parallelism <= 1:
        for role in roles:
            out.append(_call_one(role))
            if ee_enabled and len(out) >= ee_min:
                decisions = [str(x.get("decision", "ABSTAIN")) for x in out]
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
                    decisions = [str(x.get("decision", "ABSTAIN")) for x in out]
                    confs = [float(x.get("confidence", 0.0)) for x in out]
                    avg_conf = (sum(confs) / len(confs)) if confs else 0.0
                    if len(set(decisions)) == 1 and avg_conf >= ee_agree:
                        for pf in list(pending):
                            pf.cancel()
                        break
        except FuturesTimeoutError:
            log("WARNING", "Synchronous judge batch timed out; unresolved judges will ABSTAIN")
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

import hashlib
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from ai_judges import run_judges, run_judges_model_first
from consensus_engine import combine_scores
from deterministic_checks import run_deterministic_checks
from evaluator_config import effective_jury_concurrency, load_config
from logger import log
from normalization import normalize, semantic_deduplicate
from accuracy_policy import adaptive_math_jury_decision, conservative_jury_decision, strictness_profile
from decision_audit import record_decision
from domain_validation import validate_answer_domain
from semantic_scoring import score_concepts
from misconception_detector import detect_misconception


def _write_heartbeat_if_needed(hang_stage: str = "unknown"):
    """Write heartbeat to file with stage information for hang monitoring."""
    try:
        data = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "stage": hang_stage,
            "timestamp_epoch": time.time()
        }
        with open("heartbeat.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass  # Silent failure - heartbeat is not critical


# === Global caches for optimization ===
RESULT_CACHE: Dict[str, "EvaluationResult"] = {}
RESULT_CACHE_LOCK = threading.Lock()
JURY_SEMAPHORE_LOCK = threading.Lock()
JURY_SEMAPHORE: Optional[threading.Semaphore] = None
JURY_CIRCUIT_LOCK = threading.Lock()
JURY_DISABLED_UNTIL_TS = 0.0


def _get_jury_semaphore() -> threading.Semaphore:
    """Lazy-init semaphore to cap concurrent jury phases across worker threads."""
    global JURY_SEMAPHORE
    if JURY_SEMAPHORE is not None:
        return JURY_SEMAPHORE
    with JURY_SEMAPHORE_LOCK:
        if JURY_SEMAPHORE is None:
            max_jury = effective_jury_concurrency(load_config())
            JURY_SEMAPHORE = threading.Semaphore(max_jury)
            log("INFO", f"[PIPELINE] Jury concurrency limit enabled (max_concurrent_jury_answers={max_jury})")
    return JURY_SEMAPHORE


@dataclass
class EvaluationResult:
    answer: str
    decision: str
    final_score: float
    semantic_score: float
    concept_score: float
    factual_score: float
    misconception_detected: bool
    misconception_description: str
    missing_concepts: List[str]
    accepted_concepts: List[str]
    model_agreement: float
    confidence: float
    fast_path_used: bool
    latency_ms: float
    stage_reached: str
    evidence: Dict[str, object] = field(default_factory=dict)

    @property
    def raw_answer(self) -> str:
        """Exact response payload associated with this logical result."""
        return self.answer


def _result_for_raw_answer(result: EvaluationResult, raw_answer: str) -> EvaluationResult:
    """Reuse a decision without leaking a normalized/cache representative into write-back."""
    if result.answer == raw_answer and result.evidence.get("answer", raw_answer) == raw_answer:
        return result
    evidence = dict(result.evidence)
    evidence["answer"] = raw_answer
    return replace(result, answer=raw_answer, evidence=evidence)


def _expected_text(expected: Union[str, List[str]]) -> str:
    if isinstance(expected, list):
        return " | ".join(expected)
    return expected


def _domain_contradiction_can_force_rejection(domain) -> bool:
    """Only hard deterministic contradictions may overrule the AI jury."""
    hard_domains = {
        "blank",
        "irrelevant",
        "numeric",
        "numeric_range",
        "date",
        "inequality",
        "units",
        "copied_question",
    }
    return (
        domain.status == "CONTRADICTED"
        and domain.domain in hard_domains
        and float(domain.confidence) >= 0.99
    )


def _domain_proof_can_confirm_ai_acceptance(domain) -> bool:
    """Allow narrow exact/formatting proofs to confirm unanimous AI YES votes."""
    return (
        domain.status == "PROVEN"
        and domain.domain in {"exact", "formatting_equivalence"}
        and float(domain.confidence) >= 0.99
    )


def _qhash(question: str, expected: Union[str, List[str]]) -> str:
    return hashlib.sha256(f"{question}:{_expected_text(expected)}".encode()).hexdigest()


def get_or_generate_rubric(*args, **kwargs) -> Dict:
    """Backward-compatible stub: return an empty rubric structure when not present."""
    return {}


def _deduplication_enabled(cfg: Optional[Dict[str, object]] = None) -> bool:
    """Whether equivalent-looking responses may share evaluation work."""
    cfg = cfg if cfg is not None else load_config()
    return bool(cfg.get("enable_deduplication", True))


def _cache_key(answer: str, question_hash: str, deduplicate: bool = True) -> str:
    cache_answer = normalize(answer) if deduplicate else str(answer)
    return hashlib.sha256(f"{cache_answer}:{question_hash}".encode()).hexdigest()


def _resolve_worker_bounds(cfg: Dict[str, object], unique_count: int) -> tuple[int, int, int]:
    workers_cfg = cfg.get("max_parallel_workers", "auto")
    if workers_cfg == "auto":
        default_workers = min(8, max(2, (os.cpu_count() or 4)))
    else:
        default_workers = max(1, int(workers_cfg))
    min_workers = max(1, int(cfg.get("adaptive_min_workers", 2)))
    max_workers = max(min_workers, int(cfg.get("adaptive_max_workers", default_workers)))
    initial_workers = max(min_workers, min(default_workers, max_workers, max(1, unique_count)))
    return min_workers, max_workers, initial_workers


def _chunked(items: List[str], size: int) -> List[List[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def evaluate_answer(
    answer: str,
    expected: Union[str, List[str]],
    question: str,
    precomputed_judges: Optional[List[Dict[str, object]]] = None,
) -> EvaluationResult:
    global JURY_DISABLED_UNTIL_TS
    cfg = load_config()
    start = time.perf_counter()
    log("DEBUG", f"START evaluate_answer (answer_len={len(answer)}, question_hash={_qhash(question, expected)[:8]})")
    max_latency_ms = float(cfg.get("max_latency_per_answer_seconds", 30.0)) * 1000.0
    force_ai_for_all = bool(cfg.get("force_ai_jury_for_all_answers", False))
    deduplicate = _deduplication_enabled(cfg)
    qh = _qhash(question, expected)
    ck = _cache_key(answer, qh, deduplicate=deduplicate)

    with RESULT_CACHE_LOCK:
        if precomputed_judges is None and ck in RESULT_CACHE:
            r = RESULT_CACHE[ck]
            log("DEBUG", f"cache_hit=True stage={r.stage_reached}")
            try:
                pass
            except Exception:
                pass
            return _result_for_raw_answer(r, answer)

    # Write heartbeat with stage info for hang monitoring
    _write_heartbeat_if_needed(hang_stage="deterministic_checks")

    det = run_deterministic_checks(answer, expected, float(cfg.get("numeric_tolerance", 0.01)))
    if not force_ai_for_all and det.accepted and det.confidence >= 0.95:
        lat = (time.perf_counter() - start) * 1000.0
        evidence = {"question": question, "expected": expected, "answer": answer, "proof": det.method, "key_eligible": True}
        res = EvaluationResult(answer, "YES", det.confidence, det.confidence, det.confidence, det.confidence, False, "", [], [], 1.0, det.confidence, True, lat, "deterministic", evidence)
        with RESULT_CACHE_LOCK:
            RESULT_CACHE[ck] = res
        record_decision(asdict(res), str(cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")))
        log("DEBUG", f"latency_ms={lat:.2f} stage=deterministic")
        return res

    domain = validate_answer_domain(answer, expected if isinstance(expected, list) else [expected], question)
    if (not force_ai_for_all and domain.status in {"PROVEN", "CONTRADICTED", "REVIEW"}) or domain.domain == "missing_key":
        lat = (time.perf_counter() - start) * 1000.0
        decision = {"PROVEN": "YES", "CONTRADICTED": "NO", "REVIEW": "REVIEW"}[domain.status]
        evidence = {
            "question": question,
            "expected": expected,
            "answer": answer,
            "domain_validation": domain.to_dict(),
            "key_eligible": bool(domain.key_eligible),
        }
        confidence = float(domain.confidence)
        res = EvaluationResult(
            answer, decision, confidence, confidence, confidence, confidence,
            False, domain.reason if decision == "NO" else "", [], [], 1.0,
            confidence, True, lat, f"domain_{domain.domain}", evidence,
        )
        with RESULT_CACHE_LOCK:
            RESULT_CACHE[ck] = res
        record_decision(asdict(res), str(cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")))
        try:
            agent = get_global_agent()
            if agent:
                agent.ingest_metrics(answers=1, errors=1 if res.decision == "NO" else 0, latency_ms=lat)
        except Exception:
            pass
        return res

    exp_text = _expected_text(expected)
    comparison_evidence = {"teacher_answer_is_authoritative": True, "deterministic_comparison": domain.to_dict()}
    emb_score = 0.0
    concept = {"semantic_score": 0.0, "concept_score": 0.0, "missing_concepts": [], "accepted_concepts": []}
    misconception = {"misconception_detected": False, "misconception_description": ""}

    # Write heartbeat before judges (most time-consuming step)
    _write_heartbeat_if_needed(hang_stage="jury_consensus")
    with JURY_CIRCUIT_LOCK:
        jury_disabled_until = float(JURY_DISABLED_UNTIL_TS)
    patient_mode = bool(cfg.get("patient_ai_mode", False))
    circuit_enabled = bool(cfg.get("enable_jury_circuit_breaker", not patient_mode))
    if circuit_enabled and jury_disabled_until > time.time():
        lat = (time.perf_counter() - start) * 1000.0
        decision = "ERROR"
        res = EvaluationResult(
            answer, decision, emb_score, float(concept["semantic_score"]), float(concept["concept_score"]),
            float(concept["semantic_score"]), bool(misconception["misconception_detected"]),
            str(misconception["misconception_description"]), list(concept["missing_concepts"]),
            list(concept["accepted_concepts"]), 0.0, emb_score, False, lat, "jury_circuit_open",
            {"question": question, "expected": expected, "answer": answer, "policy": {
                "policy_reason": "ai_grading_unavailable",
                "processing_error": "jury_circuit_open",
            }, "domain_validation": domain.to_dict(), "teacher_answer_is_authoritative": True, "key_eligible": False}
        )
        with RESULT_CACHE_LOCK:
            RESULT_CACHE[ck] = res
        record_decision(asdict(res), str(cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")))
        return res
    judges = precomputed_judges
    if judges is None:
        jury_sem = _get_jury_semaphore()
        sem_wait_timeout_s = max(5.0, float(cfg.get("jury_semaphore_acquire_timeout_seconds", max(30.0, float(cfg.get("max_latency_per_answer_seconds", 30.0))))))
        acquired = jury_sem.acquire() if patient_mode else jury_sem.acquire(timeout=sem_wait_timeout_s)
        if not acquired:
            lat = (time.perf_counter() - start) * 1000.0
            log("WARNING", f"[PIPELINE] jury semaphore wait timed out after {sem_wait_timeout_s:.1f}s; marking answer as ERROR")
            decision = "ERROR"
            res = EvaluationResult(
                answer,
                decision,
                emb_score,
                float(concept["semantic_score"]),
                float(concept["concept_score"]),
                float(concept["semantic_score"]),
                bool(misconception["misconception_detected"]),
                str(misconception["misconception_description"]),
                list(concept["missing_concepts"]),
                list(concept["accepted_concepts"]),
                0.0,
                emb_score,
                False,
                lat,
                "jury_wait_timeout",
                {"question": question, "expected": expected, "answer": answer, "policy": {
                    "policy_reason": "ai_grading_unavailable",
                    "processing_error": "jury_wait_timeout",
                }, "domain_validation": domain.to_dict(), "teacher_answer_is_authoritative": True, "key_eligible": False},
            )
            with RESULT_CACHE_LOCK:
                RESULT_CACHE[ck] = res
            record_decision(asdict(res), str(cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")))
            try:
                agent = get_global_agent()
                if agent:
                    agent.ingest_metrics(answers=1, errors=1 if res.decision == "NO" else 0, latency_ms=lat)
            except Exception:
                pass
            return res

        def _run_judges_bounded():
            holder = {}
            err = {}
            def _runner():
                try:
                    holder["judges"] = run_judges(answer, question, exp_text, comparison_evidence, retries=int(cfg.get("retry_attempts", 3)))
                except Exception as ex:
                    err["ex"] = ex
            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            hard_timeout_s = max(20, int(cfg.get("judge_total_hard_timeout_seconds", 70)))
            t.join(timeout=hard_timeout_s)
            if t.is_alive():
                raise TimeoutError(f"jury hard timeout after {hard_timeout_s}s")
            if "ex" in err:
                raise err["ex"]
            return holder.get("judges", [])

        try:
            judges = _run_judges_bounded()
        except Exception as jury_ex:
            if circuit_enabled:
                with JURY_CIRCUIT_LOCK:
                    JURY_DISABLED_UNTIL_TS = time.time() + max(60, int(cfg.get("jury_circuit_break_seconds", 600)))
            log("ERROR", f"[PIPELINE] jury failed after retries: {jury_ex}; marking answer as ERROR")
            judges = []
        finally:
            try:
                jury_sem.release()
            except Exception:
                pass
    active = [j for j in judges if j.get("decision") in {"YES", "NO"}]
    policy_evidence = {}
    if not active:
        final_score, decision, stage, confidence = emb_score, "ERROR", "jury_unavailable", 0.0
        factual = 0.0
        policy_evidence = {
            "policy_reason": "ai_grading_unavailable",
            "processing_error": "no_valid_ai_judge_verdicts",
            "judge_decisions": {
                str(j.get("role") or f"judge_{idx + 1}"): dict(j)
                for idx, j in enumerate(judges or [])
            },
        }
        agg = {"semantic_similarity": float(concept["semantic_score"]), "concept_coverage": float(concept["concept_score"]), "factual_accuracy": 0.0, "strict_judge_score": 0.0, "language_noise_ratio": 0.0}
    else:
        by_role = {str(j.get("role", "")): j for j in active}

        def verdict_score(role: str) -> float:
            judge = by_role.get(role)
            if not judge:
                return 0.0
            confidence_value = max(0.0, min(1.0, float(judge.get("confidence", 0.0))))
            return confidence_value if judge.get("decision") == "YES" else 1.0 - confidence_value

        agg = {
            "semantic_similarity": verdict_score("semantic_judge"),
            "concept_coverage": float(concept["concept_score"]),
            "factual_accuracy": verdict_score("factual_judge"),
            "strict_judge_score": verdict_score("strict_judge") if "strict_judge" in by_role else min(
                verdict_score("semantic_judge"), verdict_score("factual_judge")
            ),
            "language_noise_ratio": 0.0,
        }
        final_score = combine_scores(agg, emb_score, bool(misconception["misconception_detected"]), cfg)
        accuracy_cfg = cfg.get("accuracy_policy", {})
        adaptive_cfg = cfg.get("adaptive_math_jury", {})
        profile = strictness_profile(cfg.get("grading_strictness", cfg.get("leniency", "balanced")))
        policy_min_confidence = float(profile.get(
            "minimum_judge_confidence",
            accuracy_cfg.get("minimum_judge_confidence", 0.90),
        ))
        require_distinct = bool(profile.get(
            "require_distinct_models",
            accuracy_cfg.get("require_distinct_models", True),
        ))
        required_ai_roles = accuracy_cfg.get(
            "required_accept_roles",
            ["semantic_judge", "factual_judge", "strict_judge"],
        )
        if bool(adaptive_cfg.get("enabled", False)):
            primary_roles = adaptive_cfg.get("primary_roles", ["semantic_judge", "factual_judge", "concept_judge"])
            required_ai_roles = primary_roles
            decision, confidence, reason, policy_evidence = adaptive_math_jury_decision(
                judges,
                cfg.get("jury_models", {}),
                min_confidence=policy_min_confidence,
                primary_roles=primary_roles,
                adjudicator_role=str(adaptive_cfg.get("adjudicator_role", "strict_judge")),
                require_distinct_models=require_distinct,
            )
            if reason in {"adjudicator_unavailable", "judge_unavailable_or_invalid", "missing_required_judges"}:
                valid_primary = [
                    role for role in primary_roles
                    if str((by_role.get(role) or {}).get("decision", "")).upper() in {"YES", "NO"}
                ]
                if len(valid_primary) < len(primary_roles):
                    decision = "ERROR"
                    confidence = 0.0
                    reason = "incomplete_ai_jury"
                    policy_evidence["processing_error"] = "incomplete_ai_jury"
        else:
            decision, confidence, reason, policy_evidence = conservative_jury_decision(
                judges,
                cfg.get("jury_models", {}),
                min_confidence=policy_min_confidence,
                required_roles=accuracy_cfg.get("required_accept_roles", ["semantic_judge", "factual_judge", "strict_judge"]),
                require_distinct_models=require_distinct,
            )
            if reason in {"judge_unavailable_or_invalid", "missing_required_judges"}:
                decision = "ERROR"
                confidence = 0.0
                reason = "incomplete_ai_jury"
                policy_evidence["processing_error"] = "incomplete_ai_jury"
        valid_required_roles = [
            role for role in required_ai_roles
            if str((by_role.get(role) or {}).get("decision", "")).upper() in {"YES", "NO"}
        ]
        required_jury_complete = len(valid_required_roles) == len(required_ai_roles)
        if not required_jury_complete and decision != "NO":
            decision = "ERROR"
            confidence = 0.0
            reason = "incomplete_ai_jury"
            policy_evidence["processing_error"] = "incomplete_ai_jury"
            policy_evidence["missing_or_invalid_required_roles"] = [
                role for role in required_ai_roles if role not in valid_required_roles
            ]
        policy_evidence["strictness_mode"] = profile.get("mode", "balanced")
        policy_evidence["strictness_profile"] = profile
        if decision == "REVIEW" and bool(profile.get("accept_unanimous_yes", False)):
            yes_votes = [
                j for j in active
                if str(j.get("decision", "")).upper() == "YES"
                and float(j.get("confidence", 0.0) or 0.0) >= policy_min_confidence
                and not j.get("requirements_missing")
                and not j.get("contradictions")
            ]
            if active and len(yes_votes) == len(active):
                decision = "YES"
                confidence = min(float(j.get("confidence", 0.0) or 0.0) for j in yes_votes)
                reason = f"{profile.get('mode', 'balanced')}_unanimous_yes"
                policy_evidence["strictness_override"] = reason
        if decision == "REVIEW" and bool(profile.get("accept_yes_majority", False)):
            yes_votes = [
                j for j in active
                if str(j.get("decision", "")).upper() == "YES"
                and float(j.get("confidence", 0.0) or 0.0) >= policy_min_confidence
                and not j.get("contradictions")
            ]
            no_votes = [j for j in active if str(j.get("decision", "")).upper() == "NO"]
            if len(yes_votes) >= 2 and len(no_votes) <= 1:
                decision = "YES"
                confidence = min(float(j.get("confidence", 0.0) or 0.0) for j in yes_votes)
                reason = f"{profile.get('mode', 'balanced')}_yes_majority"
                policy_evidence["strictness_override"] = reason
        if decision == "NO" and bool(profile.get("soften_rejections", False)):
            no_votes = [j for j in active if str(j.get("decision", "")).upper() == "NO"]
            high_conf_no = [
                j for j in no_votes
                if float(j.get("confidence", 0.0) or 0.0) >= policy_min_confidence
                and (j.get("requirements_missing") or j.get("contradictions"))
            ]
            if len(high_conf_no) < max(2, len(active) - 1):
                decision = "REVIEW"
                confidence = min((float(j.get("confidence", 0.0) or 0.0) for j in no_votes), default=0.0)
                reason = f"{profile.get('mode', 'balanced')}_softened_rejection"
                policy_evidence["strictness_override"] = reason
        # Domain/deterministic PROVEN evidence is diagnostic only: it must not
        # turn a jury rejection/review into an auto-accept. Only hard
        # deterministic contradictions may force a rejection; broad symbolic
        # math checks can be too brittle when the mark scheme is prose.
        policy_evidence["deterministic_evidence_non_authoritative"] = domain.to_dict()
        if (
            decision in {"YES", "REVIEW"}
            and _domain_proof_can_confirm_ai_acceptance(domain)
            and required_jury_complete
            and active
            and all(str(j.get("decision", "")).upper() == "YES" for j in active)
        ):
            decision = "YES"
            confidence = max(float(confidence), float(domain.confidence))
            reason = f"domain_{domain.domain}_confirmed_by_ai"
            policy_evidence["domain_proof_confirmed_unanimous_ai_yes"] = True
        if decision != "ERROR" and _domain_contradiction_can_force_rejection(domain):
            decision = "NO"
            confidence = max(float(confidence), float(domain.confidence))
            reason = f"domain_contradiction_{domain.domain}"
        stage = "jury" if decision in {"YES", "NO"} else "jury_unavailable" if decision == "ERROR" else "review"
        policy_evidence["policy_reason"] = reason
        factual = float(agg["factual_accuracy"])

    lat = (time.perf_counter() - start) * 1000.0
    if lat > max_latency_ms:
        log("WARNING", f"Latency exceeded: {lat:.2f}ms > {max_latency_ms:.2f}ms")

    votes = [1.0 if j.get("decision") == decision else 0.0 for j in active]
    agree = (sum(votes) / len(votes)) if votes else 0.0
    key_eligible = decision == "YES" and domain.domain in {
        "exact", "natural_language", "multipart_list", "formatting_equivalence"
    }
    evidence = {"question": question, "expected": expected, "answer": answer, "policy": policy_evidence, "domain_validation": domain.to_dict(), "teacher_answer_is_authoritative": True, "key_eligible": key_eligible}
    res = EvaluationResult(answer, decision, float(final_score), float(concept["semantic_score"]), float(concept["concept_score"]), factual, bool(misconception["misconception_detected"]), str(misconception["misconception_description"]), list(concept["missing_concepts"]), list(concept["accepted_concepts"]), agree, float(confidence), False, lat, stage, evidence)
    record_decision(asdict(res), str(cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")))
    with RESULT_CACHE_LOCK:
        RESULT_CACHE[ck] = res

    if bool(cfg.get("persist_result_cache", False)):
        os.makedirs("cache/results", exist_ok=True)
        with open(os.path.join("cache/results", f"{qh}.json"), "w", encoding="utf-8") as f:
            with RESULT_CACHE_LOCK:
                json.dump({k: asdict(v) for k, v in RESULT_CACHE.items()}, f)

    log("DEBUG", f"EvaluationResult={res}")
    log("DEBUG", f"decision={res.decision} score={res.final_score:.3f} stage={res.stage_reached} latency_ms={res.latency_ms:.2f}")
    return res


def evaluate_answers(answers: List[str], expected: Union[str, List[str]], question: str) -> List[EvaluationResult]:
    cfg = load_config()
    deduplicate = _deduplication_enabled(cfg)
    if not deduplicate:
        log("INFO", f"[PIPELINE] Answer processing mode: raw form responses ({len(answers)} answers, no deduplication)")
        return [evaluate_answer(answer, expected, question) for answer in answers]

    unique, mapping = semantic_deduplicate(answers, normalize_fn=normalize)
    log("INFO", f"[PIPELINE] Answer processing mode: deduplicated ({len(unique)}/{len(answers)} representative answers)")
    rep_results: Dict[str, EvaluationResult] = {}
    if len(unique) <= 1:
        rep_results = {u: evaluate_answer(u, expected, question) for u in unique}
    else:
        min_workers, max_workers, current_workers = _resolve_worker_bounds(cfg, len(unique))
        target_ms = float(cfg.get("adaptive_target_latency_ms", 15000.0))
        timeout_ms = float(cfg.get("max_latency_per_answer_seconds", 30.0)) * 1000.0
        timeout_rate_threshold = float(cfg.get("adaptive_timeout_rate_threshold", 0.20))
        chunk_size = max(1, int(cfg.get("adaptive_chunk_size", 12)))
        pending = list(unique)
        log("INFO", f"[PIPELINE] Adaptive parallel evaluation enabled (workers={current_workers}, bounds={min_workers}-{max_workers}, unique={len(unique)})")

        for chunk in _chunked(pending, chunk_size):
            with ThreadPoolExecutor(max_workers=min(current_workers, len(chunk))) as ex:
                fut_to_answer = {ex.submit(evaluate_answer, u, expected, question): u for u in chunk}
                batch_results: List[EvaluationResult] = []
                for fut in as_completed(fut_to_answer):
                    u = fut_to_answer[fut]
                    r = fut.result()
                    rep_results[u] = r
                    batch_results.append(r)

            if not batch_results:
                continue
            avg_lat = sum(r.latency_ms for r in batch_results) / len(batch_results)
            timeout_rate = sum(1 for r in batch_results if r.latency_ms >= timeout_ms) / len(batch_results)

            if timeout_rate > timeout_rate_threshold and current_workers > min_workers:
                current_workers -= 1
                log("WARNING", f"[PIPELINE] Adaptive workers downshift -> {current_workers} (timeout_rate={timeout_rate:.2f})")
            elif avg_lat < target_ms and timeout_rate == 0 and current_workers < max_workers:
                current_workers += 1
                log("INFO", f"[PIPELINE] Adaptive workers upshift -> {current_workers} (avg_latency_ms={avg_lat:.0f})")
    out: List[EvaluationResult] = []
    for rep, originals in mapping.items():
        for original in originals:
            r = rep_results[rep]
            out.append(_result_for_raw_answer(r, original))
    return out


def evaluate_answers_model_first(answers: List[str], expected: Union[str, List[str]], question: str) -> List[EvaluationResult]:
    """Evaluate one question's answers by running each judge role across the whole answer set."""
    if not answers:
        return []
    cfg = load_config()
    exp_text = _expected_text(expected)
    rubrics_by_answer: Dict[str, Dict[str, object]] = {}
    cached: Dict[str, EvaluationResult] = {}
    uncached: List[str] = []

    for answer in answers:
        qh = _qhash(question, expected)
        ck = _cache_key(answer, qh, deduplicate=_deduplication_enabled(cfg))
        with RESULT_CACHE_LOCK:
            existing = RESULT_CACHE.get(ck)
        if existing is not None:
            cached[answer] = _result_for_raw_answer(existing, answer)
            continue
        domain = validate_answer_domain(answer, expected if isinstance(expected, list) else [expected], question)
        rubrics_by_answer[answer] = {
            "teacher_answer_is_authoritative": True,
            "deterministic_comparison": domain.to_dict(),
        }
        uncached.append(answer)

    judged: Dict[str, List[Dict[str, object]]] = {}
    if uncached:
        judged = run_judges_model_first(
            uncached,
            question,
            exp_text,
            rubrics_by_answer,
            retries=int(cfg.get("retry_attempts", 3)),
        )

    results: List[EvaluationResult] = []
    for answer in answers:
        if answer in cached:
            results.append(cached[answer])
        else:
            results.append(evaluate_answer(answer, expected, question, precomputed_judges=judged.get(answer, [])))
    return results

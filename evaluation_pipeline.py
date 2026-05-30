import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Union

from ai_judges import run_judges
from confidence_router import route_decision
from consensus_engine import combine_scores
from deterministic_checks import run_deterministic_checks
from evaluator_config import load_config
from logger import log
from misconception_detector import detect_misconception
from normalization import normalize, semantic_deduplicate
from rubric_generator import generate_rubric
from semantic_scoring import score_concepts

# === Global caches for optimization ===
RESULT_CACHE: Dict[str, "EvaluationResult"] = {}
# Cache rubrics per question (one rubric reused for all students)
QUESTION_RUBRIC_CACHE: Dict[str, Dict] = {}


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


def _expected_text(expected: Union[str, List[str]]) -> str:
    if isinstance(expected, list):
        return " | ".join(expected)
    return expected


def _qhash(question: str, expected: Union[str, List[str]]) -> str:
    return hashlib.sha256(f"{question}:{_expected_text(expected)}".encode()).hexdigest()


def _question_cache_key(question: str, expected: Union[str, List[str]]) -> str:
    """Cache key for question-level rubric caching."""
    return _qhash(question, expected)


def get_or_generate_rubric(question: str, expected: Union[str, List[str]], question_id: Optional[str] = None) -> Dict:
    """Get rubric from cache or generate and cache it.

    One rubric per question is reused for all students, dramatically reducing LLM calls.
    """
    qkey = _question_cache_key(question, expected)

    if qkey in QUESTION_RUBRIC_CACHE:
        log("DEBUG", f"rubric_cache_hit=True (question_id={question_id})")
        return QUESTION_RUBRIC_CACHE[qkey]
    
    start = time.perf_counter()
    log("INFO", f"START rubric_generate (model=gemma3:12b, question_id={question_id})")

    # Generate new rubric
    exp_text = _expected_text(expected)
    rubric = generate_rubric(question, exp_text)

    duration_ms = (time.perf_counter() - start) * 1000
    log("INFO", f"END rubric_generate duration_ms={duration_ms:.0f} (question_id={question_id})")
    log("INFO", f"rubric_cache_miss=True generated_for={question_id}")

    # Add expected values to acceptable paraphrases
    if isinstance(expected, list) and expected:
        rubric["acceptable_paraphrases"] = list(dict.fromkeys(
            [str(x) for x in rubric.get("acceptable_paraphrases", [])] + [str(x) for x in expected]
        ))

    # Cache for future use
    QUESTION_RUBRIC_CACHE[qkey] = rubric
    log("DEBUG", f"rubric_cache_miss=True generated_for={question_id or 'unknown'}")
    
    return rubric


def _cache_key(answer: str, question_hash: str) -> str:
    return hashlib.sha256(f"{normalize(answer)}:{question_hash}".encode()).hexdigest()


def evaluate_answer(answer: str, expected: Union[str, List[str]], question: str) -> EvaluationResult:
    cfg = load_config()
    start = time.perf_counter()
    log("INFO", f"START evaluate_answer (answer_len={len(answer)}, question_hash={_qhash(question, expected)[:8]})")
    max_latency_ms = float(cfg.get("max_latency_per_answer_seconds", 30.0)) * 1000.0
    qh = _qhash(question, expected)
    ck = _cache_key(answer, qh)

    if ck in RESULT_CACHE:
        r = RESULT_CACHE[ck]
        log("DEBUG", f"cache_hit=True stage={r.stage_reached}")
        return r

    det = run_deterministic_checks(answer, expected, float(cfg.get("numeric_tolerance", 0.01)))
    if det.accepted and det.confidence >= 0.95:
        lat = (time.perf_counter() - start) * 1000.0
        res = EvaluationResult(answer, "YES", det.confidence, det.confidence, det.confidence, det.confidence, False, "", [], [], 1.0, det.confidence, True, lat, "deterministic")
        RESULT_CACHE[ck] = res
        log("DEBUG", f"latency_ms={lat:.2f} stage=deterministic")
        return res

    # Get rubric from cache (one rubric per question reused for all students)
    exp_text = _expected_text(expected)
    rubric = get_or_generate_rubric(question, exp_text, question_id=qh)
    concept = score_concepts(normalize(answer), rubric)
    emb_score = float(concept["embedding_score"])
    emb_th = cfg.get("embedding_thresholds", {})
    misconception = detect_misconception(answer, rubric)

    # High-coverage semantic accept fast path (pre-jury) when no misconception is detected.
    if float(concept["concept_score"]) >= 1.0 and float(concept["semantic_score"]) >= 0.70 and not bool(misconception["misconception_detected"]):
        lat = (time.perf_counter() - start) * 1000.0
        res = EvaluationResult(
            answer,
            "YES",
            float(concept["semantic_score"]),
            float(concept["semantic_score"]),
            float(concept["concept_score"]),
            float(concept["semantic_score"]),
            False,
            "",
            list(concept["missing_concepts"]),
            list(concept["accepted_concepts"]),
            1.0,
            float(concept["semantic_score"]),
            False,
            lat,
            "embedding",
        )
        RESULT_CACHE[ck] = res
        return res

    # Embedding pre-filter BEFORE jury
    if emb_score >= float(emb_th.get("auto_accept", 0.92)):
        lat = (time.perf_counter() - start) * 1000.0
        res = EvaluationResult(answer, "YES", emb_score, float(concept["semantic_score"]), float(concept["concept_score"]), float(concept["semantic_score"]), False, "", list(concept["missing_concepts"]), list(concept["accepted_concepts"]), 1.0, emb_score, False, lat, "embedding")
        RESULT_CACHE[ck] = res
        log("INFO", f"stage=embedding auto_accept=True score={emb_score:.3f} latency_ms={lat:.0f}")
        return res
    if emb_score < float(emb_th.get("auto_reject", 0.35)):
        lat = (time.perf_counter() - start) * 1000.0
        res = EvaluationResult(answer, "NO", emb_score, float(concept["semantic_score"]), float(concept["concept_score"]), float(concept["semantic_score"]), False, "", list(concept["missing_concepts"]), list(concept["accepted_concepts"]), 1.0, emb_score, False, lat, "embedding")
        RESULT_CACHE[ck] = res
        log("INFO", f"stage=embedding auto_reject=True score={emb_score:.3f} latency_ms={lat:.0f}")
        return res

    judges = run_judges(answer, question, exp_text, rubric, retries=int(cfg.get("retry_attempts", 3)))
    active = [j for j in judges if j.get("decision") != "ABSTAIN"]
    if not active:
        final_score = emb_score
        decision = "YES" if final_score >= float(cfg["confidence_thresholds"]["auto_accept"]) else "NO"
        stage = "embedding"
        confidence = final_score
        factual = float(concept["semantic_score"])
        agg = {"semantic_similarity": float(concept["semantic_score"]), "concept_coverage": float(concept["concept_score"]), "factual_accuracy": float(concept["semantic_score"]), "strict_judge_score": 0.0, "language_noise_ratio": 0.0}
    else:
        agg = {
            "semantic_similarity": sum(float(j.get("semantic_similarity", 0.0)) for j in active) / len(active),
            "concept_coverage": max(float(concept["concept_score"]), sum(float(j.get("concept_coverage", 0.0)) for j in active) / len(active)),
            "factual_accuracy": sum(float(j.get("factual_accuracy", 0.0)) for j in active) / len(active),
            "strict_judge_score": max(float(j.get("confidence", 0.0)) for j in active),
            "language_noise_ratio": sum(float(j.get("language_noise_ratio", 0.0)) for j in active) / len(active),
        }
        final_score = combine_scores(agg, emb_score, bool(misconception["misconception_detected"]), cfg)
        decision, confidence, _, stage = route_decision(final_score, answer, question, rubric, agg, cfg["confidence_thresholds"])
        factual = float(agg["factual_accuracy"])

    lat = (time.perf_counter() - start) * 1000.0
    if lat > max_latency_ms:
        log("WARNING", f"Latency exceeded: {lat:.2f}ms > {max_latency_ms:.2f}ms")

    votes = [1.0 if j.get("decision") == decision else 0.0 for j in active]
    agree = (sum(votes) / len(votes)) if votes else 0.0
    res = EvaluationResult(answer, decision, float(final_score), float(concept["semantic_score"]), float(concept["concept_score"]), factual, bool(misconception["misconception_detected"]), str(misconception["misconception_description"]), list(concept["missing_concepts"]), list(concept["accepted_concepts"]), agree, float(confidence), False, lat, stage)
    RESULT_CACHE[ck] = res

    if bool(cfg.get("persist_result_cache", False)):
        os.makedirs("cache/results", exist_ok=True)
        with open(os.path.join("cache/results", f"{qh}.json"), "w", encoding="utf-8") as f:
            json.dump({k: asdict(v) for k, v in RESULT_CACHE.items()}, f)

    log("DEBUG", f"EvaluationResult={res}")
    log("INFO", f"decision={res.decision} score={res.final_score:.3f} stage={res.stage_reached} latency_ms={res.latency_ms:.2f}")
    return res


def evaluate_answers(answers: List[str], expected: Union[str, List[str]], question: str) -> List[EvaluationResult]:
    unique, mapping = semantic_deduplicate(answers, normalize_fn=normalize)
    rep_results = {u: evaluate_answer(u, expected, question) for u in unique}
    out: List[EvaluationResult] = []
    for rep, originals in mapping.items():
        for original in originals:
            r = rep_results[rep]
            out.append(EvaluationResult(original, r.decision, r.final_score, r.semantic_score, r.concept_score, r.factual_score, r.misconception_detected, r.misconception_description, r.missing_concepts, r.accepted_concepts, r.model_agreement, r.confidence, r.fast_path_used, r.latency_ms, r.stage_reached))
    return out

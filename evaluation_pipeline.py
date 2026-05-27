import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Union

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

RESULT_CACHE: Dict[str, "EvaluationResult"] = {}


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


def _cache_key(answer: str, question_hash: str) -> str:
    return hashlib.sha256(f"{normalize(answer)}:{question_hash}".encode()).hexdigest()


def evaluate_answer(answer: str, expected: Union[str, List[str]], question: str) -> EvaluationResult:
    cfg = load_config()
    start = time.perf_counter()
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

    exp_text = _expected_text(expected)
    rubric = generate_rubric(question, exp_text)
    if isinstance(expected, list) and expected:
        rubric["acceptable_paraphrases"] = list(dict.fromkeys(
            [str(x) for x in rubric.get("acceptable_paraphrases", [])] + [str(x) for x in expected]
        ))
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
        return res
    if emb_score < float(emb_th.get("auto_reject", 0.35)):
        lat = (time.perf_counter() - start) * 1000.0
        res = EvaluationResult(answer, "NO", emb_score, float(concept["semantic_score"]), float(concept["concept_score"]), float(concept["semantic_score"]), False, "", list(concept["missing_concepts"]), list(concept["accepted_concepts"]), 1.0, emb_score, False, lat, "embedding")
        RESULT_CACHE[ck] = res
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

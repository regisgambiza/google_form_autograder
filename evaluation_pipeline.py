import time
from dataclasses import dataclass
from typing import Dict, List

from ai_judges import call_judge
from confidence_router import route_decision
from consensus_engine import combine_scores
from deterministic_checks import run_deterministic_checks
from evaluator_config import load_config
from logger import log
from misconception_detector import detect_misconception
from normalization import normalize, semantic_deduplicate
from rubric_generator import generate_rubric
from semantic_scoring import score_concepts


@dataclass
class EvaluationResult:
    """Rich result metadata for each evaluated answer."""
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


def evaluate_answer(answer: str, expected: str, question: str) -> EvaluationResult:
    """Evaluate a single answer through deterministic->embedding->jury->reasoning pipeline."""
    cfg = load_config()
    start = time.perf_counter()
    max_latency_ms = float(cfg.get("max_latency_per_answer_seconds", 30.0)) * 1000.0

    det = run_deterministic_checks(answer, expected, float(cfg.get("numeric_tolerance", 0.01)))
    if det.accepted and det.confidence >= 0.95:
        latency_ms = (time.perf_counter() - start) * 1000.0
        log("DEBUG", f"stage=deterministic method={det.method} latency_ms={latency_ms:.2f}")
        return EvaluationResult(answer, "YES", det.confidence, det.confidence, det.confidence, det.confidence, False, "", [], [], 1.0, det.confidence, True, latency_ms, "deterministic")

    rubric = generate_rubric(question, expected)
    concept = score_concepts(normalize(answer), rubric)

    thresholds = cfg["embedding_thresholds"]
    emb_score = float(concept["embedding_score"])
    if emb_score >= float(thresholds["auto_accept"]):
        latency_ms = (time.perf_counter() - start) * 1000.0
        return EvaluationResult(answer, "YES", emb_score, concept["semantic_score"], concept["concept_score"], concept["semantic_score"], False, "", concept["missing_concepts"], concept["accepted_concepts"], 1.0, emb_score, False, latency_ms, "embedding")
    if emb_score < float(thresholds["auto_reject"]):
        latency_ms = (time.perf_counter() - start) * 1000.0
        return EvaluationResult(answer, "NO", emb_score, concept["semantic_score"], concept["concept_score"], concept["semantic_score"], False, "", concept["missing_concepts"], concept["accepted_concepts"], 1.0, emb_score, False, latency_ms, "embedding")

    judges_cfg = cfg.get("jury_models", {})
    retries = int(cfg.get("retry_attempts", 3))
    judges: Dict[str, Dict[str, object]] = {}
    for role, model in judges_cfg.items():
        judges[role] = call_judge(model, role, answer, question, expected, rubric, retries=retries)

    active = [j for j in judges.values() if j.get("decision") != "ABSTAIN"]
    misconception = detect_misconception(answer, rubric)

    if not active:
        final_score = emb_score
        decision = "YES" if final_score >= float(cfg["confidence_thresholds"]["auto_accept"]) else "NO"
        stage = "embedding"
        confidence = final_score
        factual = concept["semantic_score"]
    else:
        agg = {
            "semantic_similarity": sum(float(j.get("semantic_similarity", 0.0)) for j in active) / len(active),
            "concept_coverage": max(float(concept["concept_score"]), sum(float(j.get("concept_coverage", 0.0)) for j in active) / len(active)),
            "factual_accuracy": sum(float(j.get("factual_accuracy", 0.0)) for j in active) / len(active),
            "strict_judge_score": max([float(j.get("confidence", 0.0)) for role, j in judges.items() if role == "strict_judge"] or [0.0]),
            "language_noise_ratio": sum(float(j.get("language_noise_ratio", 0.0)) for j in active) / len(active),
        }
        final_score = combine_scores(agg, emb_score, bool(misconception["misconception_detected"]), cfg)
        decision, confidence, _, stage = route_decision(final_score, answer, question, rubric, agg, cfg["confidence_thresholds"])
        factual = agg["factual_accuracy"]

    latency_ms = (time.perf_counter() - start) * 1000.0
    if latency_ms > max_latency_ms:
        log("WARNING", f"Latency exceeded: {latency_ms:.2f}ms > {max_latency_ms:.2f}ms")

    agreement_votes = [1.0 if j.get("decision") == decision else 0.0 for j in judges.values() if j.get("decision") != "ABSTAIN"]
    model_agreement = (sum(agreement_votes) / len(agreement_votes)) if agreement_votes else 0.0

    result = EvaluationResult(
        answer=answer,
        decision=decision,
        final_score=final_score,
        semantic_score=float(concept["semantic_score"]),
        concept_score=float(concept["concept_score"]),
        factual_score=float(factual),
        misconception_detected=bool(misconception["misconception_detected"]),
        misconception_description=str(misconception["misconception_description"]),
        missing_concepts=list(concept["missing_concepts"]),
        accepted_concepts=list(concept["accepted_concepts"]),
        model_agreement=model_agreement,
        confidence=float(confidence),
        fast_path_used=False,
        latency_ms=latency_ms,
        stage_reached=stage,
    )
    log("DEBUG", f"EvaluationResult={result}")
    log("INFO", f"decision={result.decision} score={result.final_score:.3f} stage={result.stage_reached}")
    return result


def evaluate_answers(answers: List[str], expected: str, question: str) -> List[EvaluationResult]:
    """Evaluate list of answers with normalized semantic deduplication."""
    unique, mapping = semantic_deduplicate(answers, normalize_fn=normalize)
    rep_results = {u: evaluate_answer(u, expected, question) for u in unique}
    out: List[EvaluationResult] = []
    for rep, originals in mapping.items():
        for original in originals:
            r = rep_results[rep]
            out.append(EvaluationResult(original, r.decision, r.final_score, r.semantic_score, r.concept_score, r.factual_score, r.misconception_detected, r.misconception_description, r.missing_concepts, r.accepted_concepts, r.model_agreement, r.confidence, r.fast_path_used, r.latency_ms, r.stage_reached))
    return out

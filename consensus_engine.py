import time
from typing import Dict

from logger import log


def combine_scores(judge_scores: Dict[str, float], embedding_similarity: float, misconception_detected: bool, config: Dict[str, object]) -> float:
    """Combine component scores with configurable weighted consensus."""
    start = time.perf_counter()
    log("INFO", f"START combine_scores (judges={len(judge_scores)}, misconception={misconception_detected})")
    w = config["consensus_weights"]
    penalty = float(config.get("misconception_penalty", 0.4)) if misconception_detected else 1.0
    language_noise_ratio = max(0.0, min(1.0, float(judge_scores.get("language_noise_ratio", 0.0))))
    language_noise_bonus = min(1.0, 1.0 - language_noise_ratio)

    final_score = (
        max(0.0, min(1.0, float(judge_scores.get("semantic_similarity", 0.0)))) * float(w["semantic_similarity"]) +
        max(0.0, min(1.0, float(judge_scores.get("concept_coverage", 0.0)))) * float(w["concept_coverage"]) +
        max(0.0, min(1.0, float(judge_scores.get("factual_accuracy", 0.0)))) * float(w["factual_accuracy"]) +
        max(0.0, min(1.0, float(judge_scores.get("strict_judge_score", 0.0)))) * float(w["strict_judge"]) +
        language_noise_bonus * float(w["language_noise"]) +
        max(0.0, min(1.0, float(embedding_similarity))) * float(w["embedding"])
    ) * penalty

    duration_ms = (time.perf_counter() - start) * 1000
    log("INFO", f"END combine_scores duration_ms={duration_ms:.0f} final_score={final_score:.3f}")
    return max(0.0, min(1.0, final_score))

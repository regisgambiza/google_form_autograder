from typing import Dict

from embeddings import semantic_similarity


def score_concepts(answer: str, rubric: Dict[str, object]) -> Dict[str, object]:
    """Score required/optional concepts and paraphrase similarity."""
    required = [str(x) for x in rubric.get("required_concepts", [])]
    optional = [str(x) for x in rubric.get("optional_concepts", [])]
    paraphrases = [str(x) for x in rubric.get("acceptable_paraphrases", [])]

    req_scores = [(c, semantic_similarity(answer, c)) for c in required]
    opt_scores = [(c, semantic_similarity(answer, c)) for c in optional]
    para_score = max([semantic_similarity(answer, p) for p in paraphrases], default=0.0)

    accepted = [c for c, s in req_scores if s >= 0.70]
    missing = [c for c, s in req_scores if s < 0.70]
    concept_coverage = (len(accepted) / len(required)) if required else para_score
    semantic_score = max([s for _, s in req_scores] + [para_score, 0.0])
    embedding_score = max(semantic_score, max([s for _, s in opt_scores], default=0.0))

    return {
        "semantic_score": semantic_score,
        "concept_score": concept_coverage,
        "embedding_score": embedding_score,
        "accepted_concepts": accepted,
        "missing_concepts": missing,
    }

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict

from embeddings import semantic_similarity


def _write_heartbeat_if_needed():
    """Write heartbeat to file for hang monitoring."""
    try:
        data = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "stage": "concept_scoring",
            "timestamp_epoch": time.time(),
        }
        with open("heartbeat.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def score_concepts(answer: str, rubric: Dict[str, object]) -> Dict[str, object]:
    """Score required/optional concepts and paraphrase similarity."""
    _write_heartbeat_if_needed()
    required = [str(x) for x in rubric.get("required_concepts", [])]
    optional = [str(x) for x in rubric.get("optional_concepts", [])]
    paraphrases = [str(x) for x in rubric.get("acceptable_paraphrases", [])]

    max_workers = int(os.getenv("SEMANTIC_SIM_WORKERS", "8"))
    sim_inputs = []
    sim_inputs.extend([("req", c) for c in required])
    sim_inputs.extend([("opt", c) for c in optional])
    sim_inputs.extend([("para", p) for p in paraphrases])

    req_scores = []
    opt_scores = []
    para_vals = []

    if sim_inputs:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            fut_to_item = {ex.submit(semantic_similarity, answer, text): (kind, text) for kind, text in sim_inputs}
            for fut in as_completed(fut_to_item):
                kind, text = fut_to_item[fut]
                try:
                    score = float(fut.result())
                except Exception:
                    score = 0.0
                if kind == "req":
                    req_scores.append((text, score))
                elif kind == "opt":
                    opt_scores.append((text, score))
                else:
                    para_vals.append(score)
    para_score = max(para_vals, default=0.0)

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

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import List

import ollama

from evaluator_config import load_config, sha256_text
from logger import log


def _write_heartbeat_if_needed():
    """Write heartbeat to file if it exists."""
    try:
        if os.path.exists("heartbeat.json"):
            data = {
                "last_update": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid()
            }
            with open("heartbeat.json", "w") as f:
                json.dump(data, f, indent=2)
    except Exception:
        pass


PREFERRED_MODELS = ["mxbai-embed-large", "nomic-embed-text", "all-minilm"]


def get_embedding(text: str, model: str) -> List[float]:
    # Write heartbeat before expensive operations
    _write_heartbeat_if_needed()
    cfg = load_config()
    num_ctx = int(cfg.get('ollama_options', {}).get('judge_num_ctx', 2048))
    os.makedirs("cache/embeddings", exist_ok=True)
    key = sha256_text(model + text)
    path = os.path.join("cache/embeddings", f"{key}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)["embedding"]
    start = time.perf_counter()
    log("INFO", f"START embedding_generate (model={model})")
    emb = ollama.embeddings(model=model, prompt=text, options={"num_ctx": num_ctx})["embedding"]
    duration_ms = (time.perf_counter() - start) * 1000
    log("INFO", f"END embedding_generate duration_ms={duration_ms:.0f} (model={model})")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"embedding": emb}, f)
    return emb


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def semantic_similarity(answer: str, expected: str) -> float:
    cfg = load_config()
    model = cfg.get("embedding_model")
    if model:
        try:
            return cosine_similarity(get_embedding(answer, model), get_embedding(expected, model))
        except Exception:
            pass
    for model in PREFERRED_MODELS:
        try:
            return cosine_similarity(get_embedding(answer, model), get_embedding(expected, model))
        except Exception:
            continue
    return 0.0

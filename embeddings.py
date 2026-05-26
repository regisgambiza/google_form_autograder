import json
import math
import os
from typing import List

import ollama

from evaluator_config import sha256_text

PREFERRED_MODELS = ["mxbai-embed-large", "nomic-embed-text", "all-minilm"]


def get_embedding(text: str, model: str) -> List[float]:
    """Get cached embedding from local Ollama model."""
    os.makedirs("cache/embeddings", exist_ok=True)
    key = sha256_text(model + text)
    path = os.path.join("cache/embeddings", f"{key}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)["embedding"]
    emb = ollama.embeddings(model=model, prompt=text)["embedding"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"embedding": emb}, f)
    return emb


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def semantic_similarity(answer: str, expected: str) -> float:
    """Compute semantic similarity with ordered model fallback."""
    for model in PREFERRED_MODELS:
        try:
            return cosine_similarity(get_embedding(answer, model), get_embedding(expected, model))
        except Exception:
            continue
    return 0.0

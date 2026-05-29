import copy
import hashlib
import json
import os
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "models": {"judge": ["qwen2.5:7b"]},
    "jury_models": {
        "semantic_judge": "qwen2.5:7b",
        "concept_judge": "deepseek-r1:8b",
        "factual_judge": "llama3.1:8b",
        "strict_judge": "phi4",
        "misconception_judge": "deepseek-r1:8b",
        "language_filter": "qwen2.5:7b",
    },
    "rubric_model": "qwen2.5:7b",
    "embedding_model": "mxbai-embed-large",
    "reasoning_model": "deepseek-r1:8b",
    "numeric_tolerance": 0.01,
    "max_latency_per_answer_seconds": 30,
    "enable_reflection": False,
    "misconception_penalty": 0.4,
    "retry_attempts": 3,
    "confidence_thresholds": {"auto_accept": 0.92, "auto_reject": 0.35},
    "embedding_thresholds": {"auto_accept": 0.92, "auto_reject": 0.35, "send_to_jury": [0.35, 0.92]},
    "consensus_weights": {
        "semantic_similarity": 0.35,
        "concept_coverage": 0.25,
        "factual_accuracy": 0.20,
        "strict_judge": 0.10,
        "language_noise": 0.05,
        "embedding": 0.05,
    },
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge dictionaries."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str = "config.json") -> Dict[str, Any]:
    """Load config with defaults."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = deep_merge(cfg, json.load(f))
    return cfg


def sha256_text(text: str) -> str:
    """Return SHA-256 for stable cache keys."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

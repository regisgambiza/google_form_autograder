import copy
import hashlib
import json
import os
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "models": {"judge": ["llama3.1:8b"]},
    "jury_models": {
        "semantic_judge": "llama3.1:8b",
        "concept_judge": "llama3.1:8b",
        "factual_judge": "llama3.1:8b",
        "strict_judge": "llama3.1:8b",
        "misconception_judge": "llama3.1:8b",
        "language_filter": "llama3.1:8b",
    },
    "rubric_model": "llama3.1:8b",
    "embedding_model": "mxbai-embed-large",
    "reasoning_model": "llama3.1:8b",
    "console_stage_banners": True,
    "console_color_enabled": True,
    "enable_form_context": True,
    "enable_vision_context": False,
    "vision_context_optional": True,
    "vision_model": "minicpm-v4.5",
    "vision_fallback_model": "qwen3-vl:8b",
    "vision_timeout_seconds": 180,
    "vision_fallback_timeout_seconds": 1000,
    "vision_connect_timeout_seconds": 10,
    "vision_download_timeout_seconds": 45,
    "validate_expected_answers": True,
    "expected_answer_validation_optional": True,
    "expected_answer_validator_model": "deepseek-r1:8b",
    "expected_answer_validator_fallback_model": "qwen3:8b",
    "expected_answer_validator_timeout_seconds": 120,
    "expected_answer_validator_fallback_timeout_seconds": 120,
    "expected_answer_validator_connect_timeout_seconds": 10,
    "expected_answer_validator_min_confidence": 0.85,
    "use_validated_expected_for_grading": True,
    "auto_replace_invalid_expected": False,
    "invalid_expected_blocks_updates": True,
    "answer_key_max_variants": 5,
    "numeric_tolerance": 0.01,
    "max_latency_per_answer_seconds": 30,
    "enable_reflection": False,
    "misconception_penalty": 0.4,
    "retry_attempts": 3,
    "confidence_thresholds": {"auto_accept": 0.90, "auto_reject": 0.35},
    "embedding_thresholds": {"auto_accept": 0.90, "auto_reject": 0.42, "send_to_jury": [0.42, 0.90]},
    "consensus_weights": {
        "semantic_similarity": 0.45,
        "concept_coverage": 0.25,
        "factual_accuracy": 0.15,
        "strict_judge": 0.05,
        "language_noise": 0.0,
        "embedding": 0.10,
    },
    "execution_mode": "Math: deterministic checks + semantic judge only (recommended)",
    "active_judge_roles": [
        "semantic_judge",
    ],
    "judge_prewarm_enabled": True,
    "judge_prewarm_timeout_seconds": 20,
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

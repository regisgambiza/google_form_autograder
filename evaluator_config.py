import copy
import hashlib
import json
import os
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "models": {"judge": ["mistral-nemo:12b"]},
    "jury_models": {
        "semantic_judge": "mistral-nemo:12b",
        "factual_judge": "gemma3:12b",
        "concept_judge": "phi4:14b",
        "strict_judge": "gpt-oss:latest",
        "misconception_judge": "llama3.1:8b",
        "language_filter": "llama3.1:8b",
    },
    "embedding_model": "mxbai-embed-large",
    "reasoning_model": "phi4:14b",
    "console_stage_banners": True,
    "console_color_enabled": True,
    "external_heartbeat_interval_seconds": 20,
    "external_log_student_answers": True,
    "gui_show_student_answers": True,
    "gui_terminal_log_path": "logs/gui_terminal.log",
    "gui_terminal_jsonl_path": "logs/gui_terminal.jsonl",
    "enable_form_context": True,
    "enable_vision_context": False,
    "vision_context_optional": True,
    "vision_model": "minicpm-v4.5",
    "vision_fallback_model": "qwen3-vl:8b",
    "vision_timeout_seconds": 180,
    "vision_fallback_timeout_seconds": 1000,
    "vision_connect_timeout_seconds": 10,
    "vision_download_timeout_seconds": 45,
    "answer_key_max_variants": 5,
    "answer_key_dry_run": False,
    "answer_key_auto_apply_confidence": 0.95,
    "answer_key_auto_add_proven_equivalents": False,
    "ignore_grading_cache": True,
    "force_ai_jury_for_all_answers": True,
    "model_first_question_batching": True,
    "judge_answer_batch_size": 3,
    "judge_batch_num_predict": 1024,
    "patient_ai_mode": True,
    "enable_jury_circuit_breaker": False,
    "judge_timeout_seconds": 7200,
    "judge_http_timeout_seconds": 7200,
    "judge_total_hard_timeout_seconds": 21600,
    "answer_hard_timeout_seconds": 21600,
    "question_batch_hard_timeout_seconds": 21600,
    "jury_semaphore_acquire_timeout_seconds": 21600,
    "max_latency_per_answer_seconds": 21600,
    "ai_stall_timeout_seconds": 900,
    "dispatcher_stall_timeout_seconds": 7200,
    "embedding_timeout_seconds": 1800,
    "ollama_options": {
        "judge_num_ctx": 2048,
        "judge_num_predict": 512,
        "embedding_num_ctx": 1024,
        "fallback_num_ctx": 2048,
        "fallback_num_predict": 256,
        "num_gpu": -1,
        "num_batch": 512,
        "num_thread": 12,
        "keep_alive": "30m",
    },
    "decision_audit_path": "logs/grading_decisions.jsonl",
    "teacher_benchmark_path": "teacher_benchmark.jsonl",
    "accuracy_policy": {
        "enabled": True,
        "minimum_judge_confidence": 0.90,
        "required_accept_roles": ["semantic_judge", "factual_judge", "concept_judge"],
        "require_distinct_models": True,
        "embeddings_can_accept": False,
        "ambiguous_outcome": "REVIEW",
    },
    "adaptive_math_jury": {
        "enabled": True,
        "primary_roles": ["semantic_judge", "factual_judge", "concept_judge"],
        "adjudicator_role": "strict_judge",
        "minimum_primary_confidence": 0.90,
        "ambiguity_markers": ["ambiguous", "uncertain", "unclear", "insufficient", "depends"],
    },
    "early_exit": {"enabled": False, "min_judges": 3, "agreement_confidence": 0.90},
    "numeric_tolerance": 0.000001,
    "max_latency_per_answer_seconds": 30,
    "enable_reflection": False,
    "misconception_penalty": 0.4,
    "retry_attempts": 5,
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
    "execution_mode": "Maximum accuracy: independent unanimous jury + review",
    "active_judge_roles": [
        "semantic_judge", "factual_judge", "concept_judge", "strict_judge",
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

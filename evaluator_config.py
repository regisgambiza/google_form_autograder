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
    "gui_decision_log_path": "logs/gui_decisions.log",
    "gui_decision_jsonl_path": "logs/gui_decisions.jsonl",
    "detailed_log_max_mb": 50,
    "model_selection_trace_max_mb": 50,
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
    "teacher_learning_prompt_enabled": True,
    "teacher_learning_prompt_examples": 8,
    "teacher_memory_similar_accept_enabled": True,
    "teacher_memory_similarity_threshold": 0.94,
    "ignore_grading_cache": True,
    "enable_deduplication": True,
    "force_ai_jury_for_all_answers": True,
    "model_first_question_batching": True,
    "judge_answer_batch_size": 2,
    "ollama_judge_answer_batch_size": 1,
    "openrouter_judge_answer_batch_size": 2,
    "judge_batch_num_predict": 8192,
    "provider_manager_enabled": True,
    "provider_strategy": "openrouter_llamacpp_ollama",
    "provider_priority": ["openrouter", "llamacpp", "ollama"],
    "provider_retry_count": 2,
    "provider_timeout_seconds": 60,
    "provider_queue_size": 500,
    "provider_circuit_failure_threshold": 3,
    "provider_circuit_recovery_seconds": 60,
    "provider_health_check_interval_seconds": 30,
    "openrouter_worker_count": 10,
    "ollama_worker_count": 1,
    "llamacpp_worker_count": 1,
    "llamacpp_enabled": True,
    "llamacpp_require_server": True,
    "llamacpp_api_base_url": "http://127.0.0.1:8081",
    "llamacpp_auto_detect_base_url": True,
    "llamacpp_json_min_predict": 256,
    "llamacpp_model_dir": r"C:\Users\regis\.lmstudio\models",
    "llamacpp_judge_answer_batch_size": 1,
    "llamacpp_models": {
        "semantic_judge": [],
        "factual_judge": [],
        "concept_judge": [],
        "strict_judge": [],
        "misconception_judge": [],
        "language_filter": [],
    },
    "openrouter_api_base_url": "https://openrouter.ai/api/v1",
    "openrouter_api_key": "env:OPENROUTER_API_KEY",
    "openrouter_models": {
        "semantic_judge": ["nvidia/nemotron-3-nano-30b-a3b:free"],
        "factual_judge": ["qwen/qwen3-next-80b-a3b-instruct:free"],
        "concept_judge": ["poolside/laguna-m.1:free"],
        "strict_judge": ["google/gemma-4-31b-it:free"],
        "misconception_judge": ["meta-llama/llama-3.3-70b-instruct:free"],
        "language_filter": ["tencent/hy3:free"],
    },
    "openrouter_fallback_models": [
        "nvidia/nemotron-3-nano-30b-a3b:free",
        "poolside/laguna-m.1:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "poolside/laguna-xs-2.1:free",
        "liquid/lfm-2.5-1.2b-instruct:free",
        "liquid/lfm-2.5-1.2b-thinking:free",
        "nvidia/nemotron-nano-9b-v2:free",
        "openai/gpt-oss-120b:free",
        "openai/gpt-oss-20b:free",
        "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "tencent/hy3:free",
        "openrouter/free",
    ],
    "openrouter_paid_fallback_models": [
        "inclusionai/ling-2.6-flash",
        "meta-llama/llama-3.1-8b-instruct",
        "mistralai/mistral-nemo",
        "qwen/qwen-2.5-7b-instruct",
    ],
    "openrouter_model_prices_per_million": {
        "inclusionai/ling-2.6-flash": {"prompt": 0.01, "completion": 0.03},
        "meta-llama/llama-3.1-8b-instruct": {"prompt": 0.02, "completion": 0.03},
        "mistralai/mistral-nemo": {"prompt": 0.02, "completion": 0.03},
        "qwen/qwen-2.5-7b-instruct": {"prompt": 0.04, "completion": 0.10},
    },
    "max_openrouter_spend_usd_per_run": 0.0,
    "openrouter_dynamic_model_pool_enabled": True,
    "openrouter_model_catalog_refresh_enabled": True,
    "openrouter_model_catalog_refresh_seconds": 3600,
    "openrouter_model_rate_limit_cooldown_seconds": 300,
    "openrouter_model_validation_cooldown_seconds": 60,
    "openrouter_model_error_cooldown_seconds": 120,
    "openrouter_model_credit_cooldown_seconds": 3600,
    "openrouter_model_not_found_cooldown_seconds": 86400,
    "openrouter_use_cooling_models_when_all_unavailable": False,
    "openrouter_avoid_reused_models": True,
    "openrouter_allow_model_reuse_when_exhausted": True,
    "model_selection_trace_enabled": True,
    "model_selection_trace_path": "logs/model_selection.jsonl",
    "openrouter_ollama_supervisor_enabled": True,
    "openrouter_supervisor_ollama_model": "gpt-oss:latest",
    "openrouter_supervisor_sample_every": 10,
    "openrouter_supervisor_queue_size": 250,
    "openrouter_supervisor_timeout_seconds": 45,
    "openrouter_supervisor_num_predict": 1024,
    "openrouter_blocked_models": [
        "cohere/north-mini-code:free",
        "qwen/qwen3-coder:free",
        "nvidia/nemotron-3.5-content-safety:free",
    ],
    "openrouter_blocked_model_keywords": [],
    "openrouter_free_model_catalog": [
        "nvidia/nemotron-3-nano-30b-a3b:free",
        "poolside/laguna-m.1:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "poolside/laguna-xs-2.1:free",
        "liquid/lfm-2.5-1.2b-instruct:free",
        "liquid/lfm-2.5-1.2b-thinking:free",
        "nvidia/nemotron-nano-9b-v2:free",
        "openai/gpt-oss-120b:free",
        "openai/gpt-oss-20b:free",
        "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "tencent/hy3:free",
        "openrouter/free",
    ],
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
    "grading_strictness": "balanced",
    "enable_pipeline_workers": True,
    "deterministic_worker_count": 4,
    "ai_worker_count": 10,
    "worker_queue_size": 1200,
    "max_concurrent_jury_answers": 10,
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


def configured_provider_names(cfg: Dict[str, Any]) -> list[str]:
    """Return provider names enabled by the current routing strategy."""
    strategy = str(cfg.get("provider_strategy", "") or "").strip().lower()
    if strategy in {"openrouter_only"}:
        return ["openrouter"]
    if strategy in {"ollama_only", "local_only"}:
        return ["ollama"]
    if strategy in {"llamacpp_only", "llama.cpp_only", "llama_cpp_only"}:
        return ["llamacpp"]
    if strategy in {"openrouter_llamacpp", "openrouter_then_llamacpp"}:
        return ["openrouter", "llamacpp"]
    if strategy in {"llamacpp_openrouter", "llamacpp_then_openrouter"}:
        return ["llamacpp", "openrouter"]
    if strategy in {"openrouter_ollama", "openrouter_then_ollama", "free_first_ollama_fallback"}:
        return ["openrouter", "ollama"]
    if strategy in {"ollama_openrouter", "ollama_then_openrouter"}:
        return ["ollama", "openrouter"]
    if strategy in {"openrouter_llamacpp_ollama", "all_providers"}:
        return ["openrouter", "llamacpp", "ollama"]

    priority = cfg.get("provider_priority", ["openrouter", "llamacpp", "ollama"])
    if not isinstance(priority, list):
        priority = ["openrouter", "llamacpp", "ollama"]
    out: list[str] = []
    for provider in priority:
        provider_name = str(provider).strip().lower()
        if provider_name in {"openrouter", "llamacpp", "ollama"} and provider_name not in out:
            out.append(provider_name)
    return out or ["openrouter", "llamacpp", "ollama"]


def is_llamacpp_only(cfg: Dict[str, Any]) -> bool:
    """True when every AI model request should be routed only to llama.cpp."""
    return configured_provider_names(cfg) == ["llamacpp"]


def effective_ai_worker_count(cfg: Dict[str, Any]) -> int:
    """Application AI workers after local-provider safety caps."""
    if is_llamacpp_only(cfg):
        return 1
    return max(1, int(cfg.get("ai_worker_count", 4) or 4))


def effective_provider_worker_counts(cfg: Dict[str, Any]) -> Dict[str, int]:
    """Provider worker counts after strategy and local-provider safety caps."""
    active = set(configured_provider_names(cfg))
    counts = {
        "openrouter": max(1, int(cfg.get("openrouter_worker_count", 4) or 4)),
        "llamacpp": 1,
        "ollama": max(1, int(cfg.get("ollama_worker_count", 1) or 1)),
    }
    return {
        provider: (counts[provider] if provider in active else 0)
        for provider in ("openrouter", "llamacpp", "ollama")
    }


def sha256_text(text: str) -> str:
    """Return SHA-256 for stable cache keys."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

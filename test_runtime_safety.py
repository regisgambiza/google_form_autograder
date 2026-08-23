import json
from pathlib import Path

import grader_thread
from evaluator_config import (
    configured_provider_names,
    effective_ai_worker_count,
    effective_jury_concurrency,
    effective_lane_workers,
    effective_provider_worker_counts,
    is_dual_lane,
)


def test_current_jury_models_use_configured_fast_local_roles():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg["jury_models"]["semantic_judge"] == "llama3.1:8b"
    assert cfg["jury_models"]["factual_judge"] == "qwen3:8b"
    assert cfg["jury_models"]["concept_judge"] == "qwen2.5:7b"
    assert cfg["jury_models"]["strict_judge"] == "qwen3:8b"
    assert "rubric_model" not in cfg
    assert cfg["reasoning_model"] == "phi4:14b"


def test_llamacpp_server_launch_defaults_match_local_9b_profile():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg["llamacpp_server_context_size"] == 32768
    assert cfg["llamacpp_server_gpu_layers"] == "auto"
    assert cfg["llamacpp_server_threads"] == 8
    assert cfg["llamacpp_server_threads_batch"] == 8
    assert cfg["llamacpp_server_batch_size"] == 1024
    assert cfg["llamacpp_server_ubatch_size"] == 512
    assert cfg["llamacpp_server_flash_attn"] == "auto"
    assert cfg["llamacpp_server_cache_type_k"] == "q8_0"
    assert cfg["llamacpp_server_cache_type_v"] == "q8_0"
    # 2 slots: benchmark-verified; a single slot saturates under failover
    # floods and starves the llamacpp lane (client-side timeouts).
    assert cfg["llamacpp_server_parallel"] == 2
    assert cfg["llamacpp_server_mmap"] is True
    assert cfg["llamacpp_server_jinja"] is True


def test_provider_manager_strategy_and_priority_are_consistent():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg["provider_manager_enabled"] is True
    strategy = str(cfg.get("provider_strategy", "")).casefold()
    assert cfg.get("provider_priority", []) == configured_provider_names(cfg)
    assert cfg["openrouter_worker_count"] >= 1
    assert cfg["llamacpp_worker_count"] >= 1
    assert cfg["ollama_worker_count"] == 1
    assert cfg["openrouter_api_key"] == "env:OPENROUTER_API_KEY"
    assert "openrouter/free" in cfg["openrouter_fallback_models"]
    assert cfg["llamacpp_model_dir"].endswith(r".lmstudio\models")
    if strategy == "llamacpp_only":
        assert effective_ai_worker_count(cfg) == 1
        assert effective_provider_worker_counts(cfg) == {
            "openrouter": 0,
            "llamacpp": 1,
            "ollama": 0,
        }


def test_provider_specific_ai_worker_counts_drive_effective_count():
    assert effective_ai_worker_count(
        {
            "provider_strategy": "openrouter_only",
            "ai_worker_count": 2,
            "openrouter_ai_worker_count": 7,
        }
    ) == 7
    assert effective_ai_worker_count(
        {
            "provider_strategy": "ollama_only",
            "ai_worker_count": 9,
            "ollama_ai_worker_count": 2,
        }
    ) == 2
    assert effective_ai_worker_count(
        {
            "provider_strategy": "llamacpp_only",
            "ai_worker_count": 9,
            "llamacpp_ai_worker_count": 5,
        }
    ) == 1
    assert effective_ai_worker_count(
        {
            "provider_strategy": "openrouter_llamacpp_ollama",
            "openrouter_ai_worker_count": 6,
            "ollama_ai_worker_count": 2,
            "llamacpp_ai_worker_count": 1,
        }
    ) == 6


def test_dual_lane_strategy_helpers():
    dual = {
        "provider_strategy": "dual_lane",
        "ai_worker_count": 10,
        "openrouter_ai_worker_count": 10,
        "llamacpp_ai_worker_count": 2,
    }
    assert is_dual_lane(dual) is True
    assert configured_provider_names(dual) == ["openrouter", "llamacpp"]
    assert effective_lane_workers(dual) == {"openrouter": 10, "llamacpp": 2}
    assert effective_ai_worker_count(dual) == 12
    # Semaphore auto-bumps to lane total when set too low; explicit higher wins.
    assert effective_jury_concurrency({**dual, "max_concurrent_jury_answers": 3}) == 12
    assert effective_jury_concurrency({**dual, "max_concurrent_jury_answers": 20}) == 20
    # Legacy strategies never see lanes.
    legacy = {"provider_strategy": "openrouter_only", "max_concurrent_jury_answers": 5}
    assert is_dual_lane(legacy) is False
    assert effective_lane_workers(legacy) == {}
    assert effective_jury_concurrency(legacy) == 5


def test_patient_ai_mode_avoids_short_timeout_fallbacks():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg["patient_ai_mode"] is True
    assert cfg["enable_jury_circuit_breaker"] is False
    # Judge HTTP calls fail fast (hang protection); patience comes from
    # dispatcher-level requeue of failed answers, not multi-hour timeouts.
    assert 10 <= cfg["judge_timeout_seconds"] <= 60
    assert 30 <= cfg["judge_http_timeout_seconds"] <= 120
    assert cfg["jury_semaphore_acquire_timeout_seconds"] >= 21600
    assert cfg["retry_attempts"] >= 5


def test_every_answer_is_forced_through_ai_jury():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg["force_ai_jury_for_all_answers"] is True


def test_jury_uses_three_blind_roles_and_conditional_gpt_adjudicator():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    adaptive = cfg["adaptive_math_jury"]
    assert adaptive["enabled"] is True
    assert adaptive["primary_roles"] == ["semantic_judge", "factual_judge", "concept_judge"]
    assert adaptive["adjudicator_role"] == "strict_judge"
    assert cfg["active_judge_roles"] == ["semantic_judge", "factual_judge", "concept_judge", "strict_judge"]
    assert cfg["jury_models"][adaptive["primary_roles"][0]] == "llama3.1:8b"
    assert cfg["jury_models"][adaptive["primary_roles"][1]] == "qwen3:8b"
    assert cfg["jury_models"][adaptive["primary_roles"][2]] == "qwen2.5:7b"
    assert cfg["jury_models"][adaptive["adjudicator_role"]] == "qwen3:8b"


def test_teacher_key_validator_was_removed_for_teacher_answer_master_flow():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert "expected_answer_validator_model" not in cfg
    assert "expected_answer_validator_fallback_model" not in cfg
    assert "expected_answer_validator_timeout_seconds" not in cfg
    assert "expected_answer_validator_fallback_timeout_seconds" not in cfg


def test_stop_before_thread_run_never_spawns_grader(monkeypatch):
    spawned = []
    monkeypatch.setattr(grader_thread.subprocess, "Popen", lambda *a, **k: spawned.append((a, k)))
    monkeypatch.setattr(grader_thread.GraderThread, "terminate_existing_graders", staticmethod(lambda: None))
    worker = grader_thread.GraderThread()
    worker.stop_grading()
    worker.run()
    assert spawned == []


def test_window_close_is_exit_not_implicit_tray_hide():
    source = Path("gui_studio/main_window.py").read_text(encoding="utf-8")
    close_body = source.split("def closeEvent(self, event):", 1)[1].split("def ", 1)[0]
    assert "self._shutdown_owned_work()" in close_body
    assert "event.accept()" in close_body
    assert "event.ignore()" not in close_body


def test_grader_subprocess_is_unbuffered():
    source = Path("grader_thread.py").read_text(encoding="utf-8")
    assert 'my_env["PYTHONUNBUFFERED"] = "1"' in source


def test_stale_grader_processes_are_waited_after_stop():
    source = Path("grader_thread.py").read_text(encoding="utf-8")
    assert "Stop-Process -Id $_.ProcessId -Force" in source
    assert "Wait-Process -Id $_.ProcessId -Timeout 5" in source


def test_active_grading_path_uses_provider_managed_pipeline():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    dispatcher = Path("global_dispatcher.py").read_text(encoding="utf-8")
    judges = Path("ai_judges.py").read_text(encoding="utf-8")

    assert cfg["dispatch_mode"] == "global"
    assert cfg["evaluator"] == "ai_evaluator_semantic"
    assert "from evaluation_pipeline import EvaluationResult, evaluate_answer, evaluate_answers_model_first" in dispatcher
    assert "get_provider_manager().ask" in judges

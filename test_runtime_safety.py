import json
from pathlib import Path

import grader_thread


def test_current_jury_models_use_reliability_first_independent_roles():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg["jury_models"]["semantic_judge"] == "mistral-nemo:12b"
    assert cfg["jury_models"]["factual_judge"] == "gemma3:12b"
    assert cfg["jury_models"]["concept_judge"] == "phi4:14b"
    assert cfg["jury_models"]["strict_judge"] == "gpt-oss:latest"
    assert len(set(cfg["jury_models"][role] for role in ("semantic_judge", "factual_judge", "concept_judge", "strict_judge"))) == 4
    assert "rubric_model" not in cfg
    assert cfg["reasoning_model"] == "phi4:14b"


def test_patient_ai_mode_avoids_short_timeout_fallbacks():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg["patient_ai_mode"] is True
    assert cfg["enable_jury_circuit_breaker"] is False
    assert cfg["judge_timeout_seconds"] >= 7200
    assert cfg["judge_total_hard_timeout_seconds"] >= 21600
    assert cfg["answer_hard_timeout_seconds"] >= 21600
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
    assert cfg["jury_models"][adaptive["primary_roles"][0]] == "mistral-nemo:12b"
    assert cfg["jury_models"][adaptive["primary_roles"][1]] == "gemma3:12b"
    assert cfg["jury_models"][adaptive["primary_roles"][2]] == "phi4:14b"
    assert cfg["jury_models"][adaptive["adjudicator_role"]] == "gpt-oss:latest"


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
    source = Path("gui_main.py").read_text(encoding="utf-8")
    close_body = source.split("def closeEvent(self, event):", 1)[1].split("if __name__ ==", 1)[0]
    assert "self._shutdown_owned_work()" in close_body
    assert "event.accept()" in close_body
    assert "event.ignore()" not in close_body


def test_grader_subprocess_is_unbuffered():
    source = Path("grader_thread.py").read_text(encoding="utf-8")
    assert 'my_env["PYTHONUNBUFFERED"] = "1"' in source

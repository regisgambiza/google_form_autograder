"""Dual-lane dispatcher tests.

Verifies that provider_strategy == "dual_lane" spawns per-provider worker
pools (named ai-<provider>-N) draining one shared backlog with lane hints,
and that legacy strategies keep the single generic pool with no hint.
Successful completion doubles as the sentinel-count assertion: a wrong
sentinel count leaves workers alive and the stall watchdog fails the run.
"""

import time
import types

import global_dispatcher as gd


_STRUCTURE = [
    {"questionId": f"q{i}", "itemId": f"item{i}", "index": i - 1, "type": "SHORT_ANSWER", "title": f"Q{i}"}
    for i in range(1, 7)
]

_FORM_PAYLOAD = {
    "info": {"title": "Dual Lane Form"},
    "items": [
        {
            "itemId": f"item{i}",
            "questionItem": {"question": {"grading": {"correctAnswers": {"answers": [{"value": str(5 + i)}]}}}},
        }
        for i in range(1, 7)
    ],
}

_RESPONSES_PAYLOAD = {
    "responses": [
        {
            "answers": {
                f"q{i}": {"textAnswers": {"answers": [{"value": str(5 + i)}]}}
                for i in range(1, 7)
            }
        }
    ]
}


def _fake_result(answer: str) -> "gd.EvaluationResult":
    return gd.EvaluationResult(
        answer=answer,
        decision="YES",
        final_score=0.99,
        semantic_score=0.99,
        concept_score=0.99,
        factual_score=0.99,
        misconception_detected=False,
        misconception_description="",
        missing_concepts=[],
        accepted_concepts=[],
        model_agreement=1.0,
        confidence=0.99,
        fast_path_used=False,
        latency_ms=1.0,
        stage_reached="jury",
        evidence={"key_eligible": True},
    )


def _base_config(strategy: str, extra: dict | None = None) -> dict:
    cfg = {
        "global_prefetch_workers": 1,
        "deterministic_worker_count": 1,
        "ai_worker_count": 4,
        "max_latency_per_answer_seconds": 5,
        "forms_expensive_reads_per_minute": 6000,
        "dispatcher_stall_timeout_seconds": 60,
        "worker_queue_size": 200,
        "numeric_tolerance": 0.01,
        "enable_deduplication": True,
        "ignore_grading_cache": True,
        "force_ai_jury_for_all_answers": True,
        "model_first_question_batching": True,
        "provider_strategy": strategy,
        "openrouter_ai_worker_count": 10,
        "llamacpp_ai_worker_count": 1,
        "ollama_ai_worker_count": 1,
        "teacher_learning_prompt_enabled": False,
        "gui_terminal_log_path": "logs/gui_terminal.log",
        "gui_terminal_jsonl_path": "logs/gui_terminal.jsonl",
        "decision_audit_path": "logs/grading_decisions.jsonl",
        "task_builder_log_enabled": False,
        "hang_diagnostics_enabled": False,
        "generate_report": False,
    }
    if extra:
        cfg.update(extra)
    return cfg


def _install_common_mocks(monkeypatch, hint_calls: list, require_both_lanes: bool = False):
    seen_entries: list[str] = []

    class _FakeReq:
        def __init__(self, payload):
            self._payload = payload

        def execute(self):
            return self._payload

    class _FakeResponses:
        def __init__(self, payload):
            self._payload = payload

        def list(self, formId=None, pageToken=None):
            return _FakeReq(self._payload)

    class _FakeForms:
        def __init__(self, payload):
            self._payload = payload

        def get(self, formId=None):
            return _FakeReq(self._payload)

        def responses(self):
            return _FakeResponses(_RESPONSES_PAYLOAD)

    class _FakeService:
        def __init__(self):
            self._forms = _FakeForms(_FORM_PAYLOAD)

        def forms(self):
            return self._forms

    monkeypatch.setattr(gd, "get_service", lambda: _FakeService())
    monkeypatch.setattr(gd, "get_form_structure", lambda service, form_id: [dict(q) for q in _STRUCTURE])
    monkeypatch.setattr(gd, "generate_form_feedback", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "save_grading_time", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "update_correct_answers", lambda *args, **kwargs: None)

    def fake_model_first(answers, expected, question, provider_hint=None):
        if require_both_lanes:
            # Hold the first grader until a different lane also shows up so
            # tiny fixtures deterministically exercise both pools.
            seen_entries.append(provider_hint or "")
            deadline = time.time() + 5
            while time.time() < deadline:
                if any(h != seen_entries[0] for h in seen_entries):
                    break
                time.sleep(0.01)
        for answer in answers:
            hint_calls.append((answer, provider_hint))
        return [_fake_result(a) for a in answers]

    monkeypatch.setattr(gd, "evaluate_answers_model_first", fake_model_first)


def test_dual_lane_pools_drain_shared_backlog_with_hints(monkeypatch):
    hint_calls = []
    worker_starts = []
    base_log = gd.log

    def spy_log(level, msg):
        worker_starts.append(msg)
        base_log(level, msg)

    _install_common_mocks(monkeypatch, hint_calls, require_both_lanes=True)
    monkeypatch.setattr(gd, "log", spy_log)
    monkeypatch.setattr(
        gd,
        "load_config",
        lambda: _base_config("dual_lane", {
            "openrouter_ai_worker_count": 2,
            "llamacpp_ai_worker_count": 2,
        }),
    )

    gd.run_global_dispatcher(["form-x"], grade_recent_only=True, generate_report=False)

    graded_answers = sorted((int(a) for a, _ in hint_calls))
    assert graded_answers == list(range(6, 12)), f"each answer graded exactly once, got {hint_calls}"
    hints = {h for _, h in hint_calls}
    assert hints == {"openrouter", "llamacpp"}, f"both lanes must process work, got {hints}"
    starts = [m for m in worker_starts if "[Worker: AI] START" in m]
    assert any("id=ai-openrouter-1 lane=openrouter" in m for m in starts)
    assert any("id=ai-openrouter-2 lane=openrouter" in m for m in starts)
    assert any("id=ai-llamacpp-1 lane=llamacpp" in m for m in starts)
    assert any("id=ai-llamacpp-2 lane=llamacpp" in m for m in starts)


def test_legacy_strategy_keeps_generic_pool_without_hints(monkeypatch):
    hint_calls = []
    worker_starts = []
    base_log = gd.log

    def spy_log(level, msg):
        worker_starts.append(msg)
        base_log(level, msg)

    _install_common_mocks(monkeypatch, hint_calls)
    monkeypatch.setattr(gd, "log", spy_log)
    monkeypatch.setattr(
        gd,
        "load_config",
        lambda: _base_config("openrouter_only", {"ai_worker_count": 3}),
    )

    gd.run_global_dispatcher(["form-x"], grade_recent_only=True, generate_report=False)

    assert sorted(int(a) for a, _ in hint_calls) == list(range(6, 12))
    # Generic pool: no lane tag in names, and every call carries no provider hint.
    assert all(h is None for _, h in hint_calls), f"legacy runs must not pass hints, got {hint_calls}"
    starts = [m for m in worker_starts if "[Worker: AI] START" in m]
    assert any("id=ai-1 lane=generic" in m for m in starts)
    assert not any("ai-openrouter-" in m or "ai-llamacpp-" in m for m in starts)


def test_dual_lane_partitions_answers_between_lanes_without_duplicates(monkeypatch):
    """Each question's answers split: llama gets its native slice, openrouter
    the remainder. Every answer graded exactly once across both lanes."""
    hint_calls = []
    _install_common_mocks(monkeypatch, hint_calls)
    monkeypatch.setattr(
        gd,
        "load_config",
        lambda: _base_config("dual_lane", {
            "openrouter_ai_worker_count": 2,
            "llamacpp_ai_worker_count": 1,
            "llamacpp_judge_answer_batch_size": 1,
            "openrouter_judge_answer_batch_size": 25,
        }),
    )

    gd.run_global_dispatcher(["form-x"], grade_recent_only=True, generate_report=False)

    llama = [a for a, h in hint_calls if h == "llamacpp"]
    orouter = [a for a, h in hint_calls if h == "openrouter"]
    assert len(llama) >= 1, f"llama lane must receive real work, got {hint_calls}"
    assert len(orouter) >= 1, f"openrouter lane must receive the remainder, got {hint_calls}"
    all_answers = sorted(int(a) for a in [*llama, *orouter])
    assert all_answers == list(range(6, 12)), f"no answer lost or duplicated: {hint_calls}"
    assert len(llama) + len(orouter) == len(hint_calls)


def test_dead_llamacpp_lane_reroutes_to_openrouter_without_losing_work(monkeypatch):
    """When the llamacpp circuit is open its queued batches move to the
    healthy openrouter lane; nothing is dropped and nothing grades twice."""
    hint_calls = []
    _install_common_mocks(monkeypatch, hint_calls)
    monkeypatch.setattr(
        gd,
        "load_config",
        lambda: _base_config("dual_lane", {
            "openrouter_ai_worker_count": 2,
            "llamacpp_ai_worker_count": 1,
            "llamacpp_judge_answer_batch_size": 1,
        }),
    )
    import provider_manager as pm

    monkeypatch.setattr(pm, "is_provider_available", lambda name: name != "llamacpp")

    gd.run_global_dispatcher(["form-x"], grade_recent_only=True, generate_report=False)

    assert hint_calls, "work must still be graded"
    assert all(h != "llamacpp" for _, h in hint_calls), f"dead lane must not grade: {hint_calls}"
    assert sorted(int(a) for a, _ in hint_calls) == list(range(6, 12))

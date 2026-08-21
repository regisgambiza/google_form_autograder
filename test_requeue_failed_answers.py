"""Failed-answer requeue tests.

Verifies that ERROR grading results are requeued with backoff instead of
being dropped, that exhaustion falls back to the legacy drop behavior, and
that progress/apply accounting stays exact while answers are in flight.
"""

import types

import global_dispatcher as gd


def _result(answer: str, decision: str, stage: str = "jury") -> "gd.EvaluationResult":
    return gd.EvaluationResult(
        answer=answer,
        decision=decision,
        final_score=0.99 if decision == "YES" else 0.0,
        semantic_score=0.99 if decision == "YES" else 0.0,
        concept_score=0.99 if decision == "YES" else 0.0,
        factual_score=0.99 if decision == "YES" else 0.0,
        misconception_detected=False,
        misconception_description="",
        missing_concepts=[],
        accepted_concepts=[],
        model_agreement=1.0,
        confidence=0.99 if decision == "YES" else 0.0,
        fast_path_used=False,
        latency_ms=1.0,
        stage_reached=stage,
        evidence={"key_eligible": decision == "YES"},
    )


_STRUCTURE = [
    {"questionId": "q1", "itemId": "item1", "index": 0, "type": "SHORT_ANSWER", "title": "Q1"},
]

_FORM_PAYLOAD = {
    "info": {"title": "Requeue Form"},
    "items": [
        {
            "itemId": "item1",
            "questionItem": {"question": {"grading": {"correctAnswers": {"answers": [{"value": "7"}]}}}},
        },
    ],
}

_RESPONSES_PAYLOAD = {
    "responses": [
        {"answers": {"q1": {"textAnswers": {"answers": [{"value": "42"}]}}}},
    ]
}


def _install_harness(monkeypatch, behavior):
    """Shared dispatcher harness; behavior(answer)->decision string."""
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
    update_calls = []
    monkeypatch.setattr(gd, "update_correct_answers", lambda *args, **kwargs: update_calls.append(args))

    calls = []

    def fake_evaluate_answer(answer, expected, question, precomputed_judges=None, provider_hint=None):
        calls.append(answer)
        return _result(answer, behavior())

    monkeypatch.setattr(gd, "evaluate_answer", fake_evaluate_answer)
    return calls, update_calls


def _base_config(max_attempts: int) -> dict:
    return {
        "global_prefetch_workers": 1,
        "deterministic_worker_count": 1,
        "ai_worker_count": 1,
        "worker_queue_size": 100,
        "max_latency_per_answer_seconds": 5,
        "forms_expensive_reads_per_minute": 6000,
        "dispatcher_stall_timeout_seconds": 120,
        "numeric_tolerance": 0.01,
        "enable_deduplication": False,
        "ignore_grading_cache": True,
        "force_ai_jury_for_all_answers": True,
        "model_first_question_batching": False,
        "provider_strategy": "openrouter_only",
        "requeue_failed_answers": True,
        "requeue_max_attempts": max_attempts,
        "requeue_base_delay_seconds": 1,
        "teacher_learning_prompt_enabled": False,
        "gui_terminal_log_path": "logs/gui_terminal.log",
        "gui_terminal_jsonl_path": "logs/gui_terminal.jsonl",
        "decision_audit_path": "logs/grading_decisions.jsonl",
        "task_builder_log_enabled": False,
        "hang_diagnostics_enabled": False,
        "generate_report": False,
    }


def test_error_answer_is_requeued_and_graded_on_retry(monkeypatch):
    state = {"n": 0}

    def behavior():
        state["n"] += 1
        return "ERROR" if state["n"] <= 2 else "YES"

    calls, update_calls = _install_harness(monkeypatch, behavior)
    monkeypatch.setattr(gd, "load_config", lambda: _base_config(2))

    gd.run_global_dispatcher(["form-x"], grade_recent_only=True, generate_report=False)

    # Two failed attempts plus one successful retry.
    assert len(calls) == 3, f"expected 3 grading attempts, got {len(calls)}"
    # The answer reaches the Google Form exactly once, via the successful pass.
    assert len(update_calls) == 1
    assert "'42'" in str(update_calls[0]), f"answer not written: {update_calls}"


def test_permanent_error_drops_after_exhaustion(monkeypatch):
    attempts = {"n": 0}

    def behavior():
        attempts["n"] += 1
        return "ERROR"

    calls, update_calls = _install_harness(monkeypatch, behavior)
    logs = []
    base_log = gd.log

    def spy_log(level, msg):
        logs.append(msg)
        base_log(level, msg)

    monkeypatch.setattr(gd, "log", spy_log)
    monkeypatch.setattr(gd, "load_config", lambda: _base_config(2))

    gd.run_global_dispatcher(["form-x"], grade_recent_only=True, generate_report=False)

    # Initial pass + 2 requeues, then the legacy drop behavior takes over.
    assert len(calls) == 3, f"expected initial + 2 requeue attempts, got {len(calls)}"
    assert not update_calls, "dropped answers must not reach the Google Form"
    assert any("[REQUEUE]" in m for m in logs), "requeue scheduling should be logged"

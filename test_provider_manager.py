import json
from pathlib import Path

import pytest

import provider_manager
from provider_manager import ProviderManager, _AuditItem
from provider_types import ProviderError, ProviderRequest, ProviderValidationError


class _FakeProvider:
    def __init__(self, responses=None, configured=True):
        self.responses = list(responses or [])
        self.configured = configured
        self.calls = []

    def is_configured(self):
        return self.configured

    def chat(self, payload, timeout_s):
        self.calls.append((dict(payload), timeout_s))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _ollama_response(content):
    return {"message": {"content": json.dumps(content)}}


def _raw_ollama_response(content):
    return {"message": {"content": content}}


def _request(judge_name="semantic_judge"):
    schema = {
        "type": "object",
        "required": ["decision", "confidence", "reason_short"],
    }
    return ProviderRequest(
        request_id="req-1",
        judge_name=judge_name,
        payload={
            "model": "ollama-semantic",
            "messages": [{"role": "user", "content": "grade this"}],
            "format": schema,
        },
        timeout_s=5,
        schema=schema,
    )


def test_provider_strategy_cheap_paid_only_uses_paid_models(monkeypatch):
    cfg = {
        "provider_strategy": "cheap_paid_only",
        "openrouter_dynamic_model_pool_enabled": False,
        "openrouter_models": {"semantic_judge": ["free-role:free"]},
        "openrouter_fallback_models": ["free-fallback:free"],
        "openrouter_paid_fallback_models": ["cheap-paid"],
        "openrouter_blocked_models": [],
        "openrouter_blocked_model_keywords": [],
    }
    monkeypatch.setattr(provider_manager, "load_config", lambda: cfg)
    manager = ProviderManager()

    assert manager._provider_order(_request()) == ["openrouter"]
    assert manager._models_for_provider("openrouter", _request()) == ["cheap-paid"]


def test_provider_strategy_free_first_paid_fallback_appends_paid_models(monkeypatch):
    cfg = {
        "provider_strategy": "free_first_paid_fallback",
        "provider_priority": ["openrouter", "ollama"],
        "openrouter_dynamic_model_pool_enabled": False,
        "openrouter_models": {"semantic_judge": ["free-role:free"]},
        "openrouter_fallback_models": ["free-fallback:free"],
        "openrouter_paid_fallback_models": ["cheap-paid"],
        "openrouter_blocked_models": [],
        "openrouter_blocked_model_keywords": [],
    }
    monkeypatch.setattr(provider_manager, "load_config", lambda: cfg)
    manager = ProviderManager()

    assert manager._provider_order(_request()) == ["openrouter", "ollama"]
    assert manager._models_for_provider("openrouter", _request()) == [
        "free-role:free",
        "free-fallback:free",
        "cheap-paid",
    ]


def _audit_item():
    return _AuditItem(
        request_id="audit-1",
        judge_name="semantic_judge",
        model="openrouter-model",
        payload={"messages": [{"role": "user", "content": "grade this"}]},
        parsed={"decision": "YES", "confidence": 0.95, "reason_short": "ok"},
        latency_ms=123.0,
    )


def test_openrouter_supervisor_accepts_json_wrapped_in_markdown(monkeypatch):
    cfg = {
        "openrouter_supervisor_ollama_model": "llama3.1:8b",
        "openrouter_supervisor_timeout_seconds": 10,
        "openrouter_supervisor_num_predict": 256,
        "ollama_options": {"judge_num_ctx": 2048},
    }
    content = """Sure, here is the audit:
```json
{"reliable": true, "aligned": true, "alignment_score": 0.92, "suspicion_score": 0.08, "too_strict": false, "too_lenient": false, "json_quality": "valid", "reason_short": "consistent"}
```"""
    ollama = _FakeProvider([_raw_ollama_response(content)])
    manager = ProviderManager()

    audit = manager._run_openrouter_audit(ollama, _audit_item(), cfg)

    assert audit["reliable"] is True
    assert audit["alignment_score"] == pytest.approx(0.92)
    assert audit["suspicion_score"] == pytest.approx(0.08)


def test_openrouter_supervisor_reports_empty_audit_response():
    ollama = _FakeProvider([_raw_ollama_response("")])
    manager = ProviderManager()

    with pytest.raises(ProviderValidationError, match="empty"):
        manager._run_openrouter_audit(ollama, _audit_item(), {"openrouter_supervisor_timeout_seconds": 10})


def test_openrouter_supervisor_prompt_is_audit_only_and_omits_grader_prompt():
    item = _AuditItem(
        request_id="audit-2",
        judge_name="strict_judge",
        model="openrouter-model",
        payload={
            "messages": [
                {"role": "system", "content": "You are a math grader."},
                {"role": "user", "content": "Solve 4x - 7x and grade the student answer."},
            ]
        },
        parsed={
            "decision": "NO",
            "confidence": 0.9,
            "reason_short": "student answer contradicts expected",
            "requirements_missing": ["correct expression"],
            "contradictions": ["wrong simplification"],
        },
        latency_ms=55.0,
    )

    prompt = ProviderManager._make_openrouter_audit_prompt(item)

    assert "AUDIT ONLY" in prompt
    assert "Do not solve" in prompt
    assert "OPENROUTER_OUTPUT_TO_AUDIT" in prompt
    assert "Solve 4x - 7x" not in prompt
    assert "You are a math grader" not in prompt
    assert "student answer contradicts expected" in prompt


def test_openrouter_supervisor_sends_audit_only_prompt_to_ollama():
    cfg = {
        "openrouter_supervisor_ollama_model": "gpt-oss:latest",
        "openrouter_supervisor_timeout_seconds": 10,
        "openrouter_supervisor_num_predict": 256,
        "ollama_options": {"judge_num_ctx": 2048},
    }
    ollama = _FakeProvider([
        _ollama_response({
            "reliable": True,
            "aligned": True,
            "alignment_score": 1.0,
            "suspicion_score": 0.0,
            "too_strict": False,
            "too_lenient": False,
            "json_quality": "valid",
            "reason_short": "consistent audit output",
        })
    ])
    manager = ProviderManager()

    manager._run_openrouter_audit(ollama, _audit_item(), cfg)

    sent_payload = ollama.calls[0][0]
    messages = sent_payload["messages"]
    assert sent_payload["model"] == "gpt-oss:latest"
    assert sent_payload["format"] == "json"
    assert sent_payload["options"]["num_predict"] == 256
    assert "NOT solving" in messages[0]["content"]
    assert "AUDIT ONLY" in messages[1]["content"]
    assert "grade this" not in messages[1]["content"]


def _batch_request(answer_count, judge_name="semantic_judge"):
    schema = {
        "type": "object",
        "required": ["results"],
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["answer_index", "decision", "confidence", "reason_short"],
                },
            }
        },
    }
    return ProviderRequest(
        request_id="req-batch-1",
        judge_name=judge_name,
        payload={
            "model": "ollama-semantic",
            "messages": [{"role": "user", "content": "grade this batch"}],
            "format": schema,
        },
        timeout_s=5,
        schema=schema,
        metadata={"request_kind": "judge-batch", "batch_answer_count": answer_count},
    )


def _make_manager(monkeypatch, cfg, openrouter, ollama):
    monkeypatch.setattr(provider_manager, "load_config", lambda: cfg)
    manager = ProviderManager()
    manager._providers = {"openrouter": openrouter, "ollama": ollama}
    manager._states = {name: provider_manager._ProviderState() for name in manager._providers}
    manager._queues = {name: provider_manager.queue.Queue(maxsize=20) for name in manager._providers}
    manager._provider_metrics = {
        name: {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "validation_failed": 0,
            "total_latency_ms": 0.0,
            "last_model": "-",
            "last_latency_ms": 0.0,
            "last_error": "",
        }
        for name in manager._providers
    }
    return manager


def test_provider_manager_prefers_openrouter(monkeypatch):
    cfg = {
        "provider_priority": ["openrouter", "ollama"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "openrouter_models": {"semantic_judge": ["openrouter-model"]},
        "openrouter_fallback_models": [],
        "jury_models": {"semantic_judge": "ollama-semantic"},
    }
    openrouter = _FakeProvider([_ollama_response({"decision": "YES", "confidence": 1.0, "reason_short": "ok"})])
    ollama = _FakeProvider([_ollama_response({"decision": "NO", "confidence": 1.0, "reason_short": "wrong"})])
    manager = _make_manager(monkeypatch, cfg, openrouter, ollama)

    response = manager.ask(_request())

    assert response.provider == "openrouter"
    assert response.model == "openrouter-model"
    assert len(openrouter.calls) == 1
    assert ollama.calls == []


def test_openrouter_fallback_models_rotate_by_judge_role(monkeypatch):
    cfg = {
        "provider_queue_size": 20,
        "openrouter_dynamic_model_pool_enabled": False,
        "openrouter_models": {
            "semantic_judge": [],
            "factual_judge": [],
            "concept_judge": [],
            "strict_judge": [],
        },
        "openrouter_fallback_models": ["model-a", "model-b", "model-c", "model-d"],
    }
    monkeypatch.setattr(provider_manager, "load_config", lambda: cfg)
    manager = ProviderManager()

    selected = {
        role: manager._models_for_provider("openrouter", _request(role))[0]
        for role in ("semantic_judge", "factual_judge", "concept_judge", "strict_judge")
    }

    assert selected == {
        "semantic_judge": "model-a",
        "factual_judge": "model-b",
        "concept_judge": "model-c",
        "strict_judge": "model-d",
    }


def test_openrouter_avoids_models_used_by_previous_jury_roles(monkeypatch):
    cfg = {
        "provider_queue_size": 20,
        "openrouter_dynamic_model_pool_enabled": False,
        "openrouter_avoid_reused_models": True,
        "openrouter_allow_model_reuse_when_exhausted": False,
        "openrouter_models": {"semantic_judge": []},
        "openrouter_fallback_models": ["model-a", "model-b", "model-c"],
    }
    monkeypatch.setattr(provider_manager, "load_config", lambda: cfg)
    manager = ProviderManager()
    request = ProviderRequest(
        request_id="req-avoid",
        judge_name="semantic_judge",
        payload={"model": "ollama-semantic", "messages": []},
        timeout_s=5,
        metadata={"avoid_models": ["model-a"]},
    )

    models = manager._models_for_provider("openrouter", request)

    assert models == ["model-b", "model-c"]


def test_openrouter_returns_no_models_when_every_candidate_was_already_used(monkeypatch):
    cfg = {
        "provider_queue_size": 20,
        "openrouter_dynamic_model_pool_enabled": False,
        "openrouter_avoid_reused_models": True,
        "openrouter_allow_model_reuse_when_exhausted": False,
        "openrouter_models": {"semantic_judge": []},
        "openrouter_fallback_models": ["model-a", "model-b"],
    }
    monkeypatch.setattr(provider_manager, "load_config", lambda: cfg)
    manager = ProviderManager()
    request = ProviderRequest(
        request_id="req-avoid-all",
        judge_name="semantic_judge",
        payload={"model": "ollama-semantic", "messages": []},
        timeout_s=5,
        metadata={"avoid_models": ["model-a", "model-b"]},
    )

    assert manager._models_for_provider("openrouter", request) == []


def test_openrouter_does_not_retry_avoided_model_after_fresh_model_fails(monkeypatch):
    cfg = {
        "provider_priority": ["openrouter", "ollama"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "provider_circuit_failure_threshold": 10,
        "openrouter_dynamic_model_pool_enabled": False,
        "openrouter_avoid_reused_models": True,
        "openrouter_allow_model_reuse_when_exhausted": False,
        "openrouter_models": {"semantic_judge": []},
        "openrouter_fallback_models": ["fresh-model", "used-model"],
        "jury_models": {"semantic_judge": "ollama-semantic"},
    }
    openrouter = _FakeProvider([
        ProviderError("fresh model rate limited", "rate_limited"),
        _ollama_response({"decision": "NO", "confidence": 1.0, "reason_short": "should not use reused"}),
    ])
    ollama = _FakeProvider([_ollama_response({"decision": "YES", "confidence": 0.9, "reason_short": "fallback"})])
    manager = _make_manager(monkeypatch, cfg, openrouter, ollama)
    request = _request()
    request = ProviderRequest(
        request_id=request.request_id,
        judge_name=request.judge_name,
        payload=request.payload,
        timeout_s=request.timeout_s,
        schema=request.schema,
        metadata={"avoid_models": ["used-model"]},
    )

    response = manager.ask(request)

    assert response.provider == "ollama"
    assert response.model == "ollama-semantic"
    assert [call[0]["model"] for call in openrouter.calls] == ["fresh-model"]


def test_model_selection_trace_logs_candidate_and_attempt_events(monkeypatch, tmp_path):
    trace_path = tmp_path / "model_selection.jsonl"
    cfg = {
        "provider_priority": ["openrouter"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "openrouter_dynamic_model_pool_enabled": False,
        "openrouter_avoid_reused_models": True,
        "openrouter_allow_model_reuse_when_exhausted": False,
        "model_selection_trace_enabled": True,
        "model_selection_trace_path": str(trace_path),
        "openrouter_models": {"semantic_judge": []},
        "openrouter_fallback_models": ["fresh-model", "used-model"],
    }
    openrouter = _FakeProvider([_ollama_response({"decision": "YES", "confidence": 1.0, "reason_short": "ok"})])
    manager = _make_manager(monkeypatch, cfg, openrouter, _FakeProvider([]))
    request = ProviderRequest(
        request_id="req-trace",
        judge_name="semantic_judge",
        payload={
            "model": "ollama-semantic",
            "messages": [{"role": "user", "content": "grade this"}],
            "format": {"type": "object", "required": ["decision", "confidence", "reason_short"]},
        },
        timeout_s=5,
        schema={"type": "object", "required": ["decision", "confidence", "reason_short"]},
        metadata={"avoid_models": ["used-model"]},
    )

    response = manager.ask(request)
    records = [json.loads(line) for line in Path(trace_path).read_text(encoding="utf-8").splitlines()]
    events = [record["event"] for record in records]

    assert response.model == "fresh-model"
    assert "openrouter_candidates" in events
    assert "openrouter_selected_pool" in events
    assert "model_attempt" in events
    assert "model_success" in events
    candidate_record = next(record for record in records if record["event"] == "openrouter_candidates")
    assert candidate_record["avoid_models"] == ["used-model"]
    selected_record = next(record for record in records if record["event"] == "openrouter_selected_pool")
    assert selected_record["selected"] == ["fresh-model"]


def test_provider_manager_fails_over_to_ollama_after_malformed_openrouter(monkeypatch):
    cfg = {
        "provider_priority": ["openrouter", "ollama"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "provider_circuit_failure_threshold": 3,
        "openrouter_models": {"semantic_judge": ["openrouter-model"]},
        "openrouter_fallback_models": [],
        "jury_models": {"semantic_judge": "ollama-semantic"},
    }
    openrouter = _FakeProvider([{"message": {"content": "not-json"}}])
    ollama = _FakeProvider([_ollama_response({"decision": "YES", "confidence": 0.9, "reason_short": "fallback"})])
    manager = _make_manager(monkeypatch, cfg, openrouter, ollama)

    response = manager.ask(_request())
    snapshot = manager.snapshot()

    assert response.provider == "ollama"
    assert response.model == "ollama-semantic"
    assert snapshot["metrics"]["failovers"] == 1
    assert snapshot["metrics"]["validation_failed"] == 1


def test_provider_manager_does_not_send_oversized_batch_to_ollama_fallback(monkeypatch):
    cfg = {
        "provider_priority": ["openrouter", "ollama"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "provider_circuit_failure_threshold": 3,
        "judge_answer_batch_size": 25,
        "ollama_judge_answer_batch_size": 5,
        "openrouter_models": {"semantic_judge": ["openrouter-model"]},
        "openrouter_fallback_models": [],
        "jury_models": {"semantic_judge": "ollama-semantic"},
    }
    openrouter = _FakeProvider([{"message": {"content": "not-json"}}])
    ollama = _FakeProvider([
        _ollama_response({
            "results": [
                {"answer_index": i, "decision": "YES", "confidence": 0.9, "reason_short": "ok"}
                for i in range(1, 26)
            ]
        })
    ])
    manager = _make_manager(monkeypatch, cfg, openrouter, ollama)

    with pytest.raises(ProviderError):
        manager.ask(_batch_request(25))

    assert len(openrouter.calls) == 1
    assert ollama.calls == []


def test_provider_manager_allows_ollama_batch_within_configured_limit(monkeypatch):
    cfg = {
        "provider_priority": ["ollama"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "judge_answer_batch_size": 25,
        "ollama_judge_answer_batch_size": 5,
        "openrouter_models": {},
        "openrouter_fallback_models": [],
        "jury_models": {"semantic_judge": "ollama-semantic"},
    }
    ollama = _FakeProvider([
        _ollama_response({
            "results": [
                {"answer_index": i, "decision": "YES", "confidence": 0.9, "reason_short": "ok"}
                for i in range(1, 6)
            ]
        })
    ])
    manager = _make_manager(monkeypatch, cfg, _FakeProvider([]), ollama)

    response = manager.ask(_batch_request(5))

    assert response.provider == "ollama"
    assert len(ollama.calls) == 1


def test_provider_manager_skips_missing_openrouter_model_without_retry_or_circuit(monkeypatch):
    cfg = {
        "provider_priority": ["openrouter", "ollama"],
        "provider_retry_count": 2,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "provider_circuit_failure_threshold": 1,
        "openrouter_models": {"semantic_judge": ["missing-model", "working-model"]},
        "openrouter_fallback_models": [],
        "jury_models": {"semantic_judge": "ollama-semantic"},
    }
    openrouter = _FakeProvider([
        ProviderError("OpenRouter model not found: missing-model", "model_not_found"),
        _ollama_response({"decision": "YES", "confidence": 0.9, "reason_short": "second model"}),
    ])
    manager = _make_manager(monkeypatch, cfg, openrouter, _FakeProvider([]))

    response = manager.ask(_request())
    snapshot = manager.snapshot()

    assert response.provider == "openrouter"
    assert response.model == "working-model"
    assert [call[0]["model"] for call in openrouter.calls] == ["missing-model", "working-model"]
    assert snapshot["metrics"]["retries"] == 0
    assert snapshot["providers"]["openrouter"]["health"] == "HEALTHY"
    assert snapshot["providers"]["openrouter"]["circuit"] == "CLOSED"


def test_openrouter_model_rate_limit_cools_down_model_for_next_request(monkeypatch):
    cfg = {
        "provider_priority": ["openrouter"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "provider_circuit_failure_threshold": 99,
        "openrouter_model_rate_limit_cooldown_seconds": 300,
        "openrouter_dynamic_model_pool_enabled": True,
        "openrouter_models": {"semantic_judge": ["busy-model", "backup-model"]},
        "openrouter_fallback_models": [],
        "openrouter_free_model_catalog": [],
    }
    openrouter = _FakeProvider([
        ProviderError("OpenRouter rate limited", "rate_limited"),
        _ollama_response({"decision": "YES", "confidence": 0.9, "reason_short": "backup"}),
        _ollama_response({"decision": "YES", "confidence": 0.9, "reason_short": "backup again"}),
    ])
    manager = _make_manager(monkeypatch, cfg, openrouter, _FakeProvider([]))

    first = manager.ask(_request())
    second = manager.ask(_request())

    assert first.model == "backup-model"
    assert second.model == "backup-model"
    assert [call[0]["model"] for call in openrouter.calls] == [
        "busy-model",
        "backup-model",
        "backup-model",
    ]
    snapshot = manager.snapshot()
    assert snapshot["openrouter_models"]["models"]["busy-model"]["cooldown_remaining_s"] > 0


def test_ollama_model_comes_from_existing_jury_settings(monkeypatch):
    cfg = {
        "provider_priority": ["ollama"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "openrouter_models": {},
        "openrouter_fallback_models": [],
        "jury_models": {"factual_judge": "settings-factual-model"},
    }
    openrouter = _FakeProvider([])
    ollama = _FakeProvider([_ollama_response({"decision": "YES", "confidence": 0.95, "reason_short": "ok"})])
    manager = _make_manager(monkeypatch, cfg, openrouter, ollama)
    req = _request("factual_judge")
    req.payload.pop("model")

    response = manager.ask(req)

    assert response.provider == "ollama"
    assert response.model == "settings-factual-model"
    assert ollama.calls[0][0]["model"] == "settings-factual-model"


def test_provider_manager_raises_when_every_provider_fails(monkeypatch):
    cfg = {
        "provider_priority": ["openrouter"],
        "provider_retry_count": 1,
        "openrouter_worker_count": 1,
        "ollama_worker_count": 1,
        "openrouter_models": {"semantic_judge": ["openrouter-model"]},
        "openrouter_fallback_models": [],
    }
    openrouter = _FakeProvider([{"message": {"content": "{}"}}])
    manager = _make_manager(monkeypatch, cfg, openrouter, _FakeProvider([]))

    with pytest.raises(Exception):
        manager.ask(_request())

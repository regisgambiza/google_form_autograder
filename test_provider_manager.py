import json

import pytest

import provider_manager
from provider_manager import ProviderManager
from provider_types import ProviderError, ProviderRequest


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

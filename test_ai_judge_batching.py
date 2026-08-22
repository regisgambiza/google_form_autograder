import json

import ai_judges
from openrouter_model_registry import OpenRouterModelRegistry


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _batch_payload(results):
    return {"message": {"content": json.dumps({"results": results})}}


def _judge_result(answer_index, decision="YES"):
    return {
        "answer_index": answer_index,
        "decision": decision,
        "confidence": 0.99,
        "reason_short": f"answer {answer_index}",
        "requirements_met": ["matches"],
        "requirements_missing": [],
        "contradictions": [],
        "calculation_check": "ok",
    }


def test_batch_judge_response_parser_keeps_results_by_answer_index():
    raw = json.dumps({"results": [_judge_result(1), _judge_result(3, "NO")]})

    parsed = ai_judges.parse_batch_judge_response(raw, [1, 2, 3])

    assert parsed[1]["decision"] == "YES"
    assert parsed[3]["decision"] == "NO"
    assert 2 not in parsed


def test_call_judge_role_batch_sync_uses_one_ollama_call_for_three_answers(monkeypatch):
    calls = []

    monkeypatch.setattr(
        ai_judges,
        "load_config",
        lambda: {
            "jury_models": {"semantic_judge": "model-a"},
            "provider_manager_enabled": False,
            "judge_timeout_seconds": 30,
            "judge_http_timeout_seconds": 30,
            "judge_http_semaphore_wait_seconds": 30,
            "ollama_options": {"judge_num_ctx": 2048, "judge_num_predict": 512},
            "judge_batch_num_predict": 1024,
        },
    )
    monkeypatch.setattr(ai_judges, "log_post_inference_gpu_probe_once", lambda *_args, **_kwargs: None)

    def fake_post(_url, json=None, timeout=None):
        calls.append(json)
        return _FakeResponse(_batch_payload([_judge_result(1), _judge_result(2), _judge_result(3)]))

    monkeypatch.setattr(ai_judges.requests, "post", fake_post)

    out = ai_judges.call_judge_role_batch_sync(
        "semantic_judge",
        ["a", "b", "c"],
        "question",
        "expected",
        {"a": {}, "b": {}, "c": {}},
        retries=1,
    )

    assert len(calls) == 1
    assert calls[0]["format"]["required"] == ["results"]
    assert set(out) == {"a", "b", "c"}
    assert all(result["decision"] == "YES" for result in out.values())


def test_call_judge_role_batch_sync_falls_back_for_missing_answer(monkeypatch):
    single_calls = []

    monkeypatch.setattr(
        ai_judges,
        "load_config",
        lambda: {
            "jury_models": {"semantic_judge": "model-a"},
            "provider_manager_enabled": False,
            "judge_timeout_seconds": 30,
            "judge_http_timeout_seconds": 30,
            "judge_http_semaphore_wait_seconds": 30,
            "ollama_options": {"judge_num_ctx": 2048, "judge_num_predict": 512},
            "judge_batch_num_predict": 1024,
        },
    )
    monkeypatch.setattr(ai_judges, "log_post_inference_gpu_probe_once", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ai_judges.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(_batch_payload([_judge_result(1), _judge_result(3)])),
    )

    def fake_single(role, answer, question, expected, rubric, retries, avoid_models=None, provider_hint=None):
        single_calls.append((answer, list(avoid_models or [])))
        return {
            "role": role,
            "model": "model-a",
            "decision": "NO",
            "confidence": 0.95,
            "reason_short": "single fallback",
            "requirements_met": [],
            "requirements_missing": ["missing from batch"],
            "contradictions": [],
            "calculation_check": "fallback",
        }

    monkeypatch.setattr(ai_judges, "call_judge_role_sync", fake_single)

    out = ai_judges.call_judge_role_batch_sync(
        "semantic_judge",
        ["a", "b", "c"],
        "question",
        "expected",
        {"a": {}, "b": {}, "c": {}},
        retries=1,
        avoid_models=["used-model"],
    )

    assert single_calls == [("b", ["used-model"])]
    assert out["a"]["decision"] == "YES"
    assert out["b"]["decision"] == "NO"
    assert out["c"]["decision"] == "YES"


def test_judge_answer_batch_size_is_provider_specific():
    cfg = {
        "provider_manager_enabled": True,
        "provider_priority": ["openrouter", "ollama"],
        "judge_answer_batch_size": 9,
        "ollama_judge_answer_batch_size": 1,
        "openrouter_judge_answer_batch_size": 2,
    }

    assert ai_judges._preferred_batch_provider(cfg) == "openrouter"
    assert ai_judges._judge_answer_batch_size(cfg) == 2
    assert ai_judges._judge_answer_batch_size(cfg, "ollama") == 1
    assert ai_judges._judge_answer_batch_size(cfg, "openrouter") == 2


def test_judge_answer_batch_size_respects_provider_strategy():
    cfg = {
        "provider_manager_enabled": True,
        "provider_strategy": "llamacpp_only",
        "provider_priority": ["openrouter", "llamacpp", "ollama"],
        "judge_answer_batch_size": 25,
        "ollama_judge_answer_batch_size": 1,
        "openrouter_judge_answer_batch_size": 25,
        "llamacpp_judge_answer_batch_size": 20,
    }

    assert ai_judges._preferred_batch_provider(cfg) == "llamacpp"
    assert ai_judges._judge_answer_batch_size(cfg) == 1
    assert ai_judges._judge_answer_batch_size(cfg, "llamacpp") == 1


def test_provider_manager_start_label_does_not_claim_requested_model(monkeypatch):
    monkeypatch.setattr(ai_judges, "load_config", lambda: {"provider_manager_enabled": True})

    assert ai_judges._judge_start_model_label("semantic_judge", "llama3.1:8b") == (
        "provider=managed role=semantic_judge"
    )
    assert ai_judges._pre_provider_active_model("semantic_judge", "llama3.1:8b") == (
        "provider-managed:semantic_judge"
    )
    assert ai_judges._unavailable_model_label("factual_judge", "qwen3:8b") == (
        "provider-managed:factual_judge"
    )


def test_judge_answer_batch_size_uses_ollama_when_provider_manager_disabled():
    cfg = {
        "provider_manager_enabled": False,
        "judge_answer_batch_size": 9,
        "ollama_judge_answer_batch_size": 1,
        "openrouter_judge_answer_batch_size": 2,
    }

    assert ai_judges._preferred_batch_provider(cfg) == "ollama"
    assert ai_judges._judge_answer_batch_size(cfg) == 1


def test_oversized_provider_batch_splits_to_ollama_sized_chunks(monkeypatch):
    calls = []

    monkeypatch.setattr(
        ai_judges,
        "load_config",
        lambda: {
            "jury_models": {"semantic_judge": "model-a"},
            "provider_manager_enabled": True,
            "provider_priority": ["openrouter", "ollama"],
            "judge_timeout_seconds": 30,
            "judge_http_timeout_seconds": 30,
            "ollama_options": {"judge_num_ctx": 2048, "judge_num_predict": 512},
            "judge_batch_num_predict": 1024,
            "judge_answer_batch_size": 18,
            "ollama_judge_answer_batch_size": 5,
            "openrouter_judge_answer_batch_size": 18,
        },
    )
    monkeypatch.setattr(ai_judges, "log_post_inference_gpu_probe_once", lambda *_args, **_kwargs: None)

    def fake_chat_response(role, payload, timeout_s, request_kind, metadata=None):
        answer_count = int((metadata or {}).get("batch_answer_count", 1))
        calls.append((answer_count, list((metadata or {}).get("avoid_models") or [])))
        if answer_count > 5:
            raise ai_judges.ProviderError("OpenRouter rate limited", "rate_limited")
        return _batch_payload([_judge_result(i) for i in range(1, answer_count + 1)])

    monkeypatch.setattr(ai_judges, "_chat_response", fake_chat_response)

    answers = [f"a{i}" for i in range(18)]
    out = ai_judges.call_judge_role_batch_sync(
        "semantic_judge",
        answers,
        "question",
        "expected",
        {answer: {} for answer in answers},
        retries=5,
        avoid_models=["used-model"],
    )

    assert calls == [
        (18, ["used-model"]),
        (5, ["used-model"]),
        (5, ["used-model"]),
        (5, ["used-model"]),
        (3, ["used-model"]),
    ]
    assert set(out) == set(answers)
    assert all(result["decision"] == "YES" for result in out.values())


def test_oversized_provider_batch_splits_to_single_ollama_calls_after_validation_failure(monkeypatch):
    calls = []

    monkeypatch.setattr(
        ai_judges,
        "load_config",
        lambda: {
            "jury_models": {"semantic_judge": "model-a"},
            "provider_manager_enabled": True,
            "provider_priority": ["openrouter", "ollama"],
            "judge_timeout_seconds": 30,
            "judge_http_timeout_seconds": 30,
            "ollama_options": {"judge_num_ctx": 2048, "judge_num_predict": 512},
            "judge_batch_num_predict": 1024,
            "judge_answer_batch_size": 25,
            "ollama_judge_answer_batch_size": 1,
            "openrouter_judge_answer_batch_size": 25,
        },
    )
    monkeypatch.setattr(ai_judges, "log_post_inference_gpu_probe_once", lambda *_args, **_kwargs: None)

    def fake_chat_response(role, payload, timeout_s, request_kind, metadata=None):
        answer_count = int((metadata or {}).get("batch_answer_count", 1))
        calls.append(answer_count)
        if answer_count > 1:
            raise ai_judges.ProviderError("OpenRouter returned malformed JSON", "validation")
        return _batch_payload([_judge_result(1)])

    monkeypatch.setattr(ai_judges, "_chat_response", fake_chat_response)

    answers = [f"a{i}" for i in range(4)]
    out = ai_judges.call_judge_role_batch_sync(
        "semantic_judge",
        answers,
        "question",
        "expected",
        {answer: {} for answer in answers},
        retries=1,
    )

    assert calls == [4, 1, 1, 1, 1]
    assert set(out) == set(answers)
    assert all(result["decision"] == "YES" for result in out.values())


def test_model_first_judging_refreshes_answer_batch_size_between_roles(monkeypatch):
    state = {"legacy_batch_size": 2}
    batch_calls = []
    single_calls = []

    def fake_load_config():
        return {
            "jury_models": {
                "semantic_judge": "model-a",
                "factual_judge": "model-b",
            },
            "active_judge_roles": ["semantic_judge", "factual_judge"],
            "adaptive_math_jury": {"enabled": False},
            "provider_priority": ["openrouter", "ollama"],
            "judge_answer_batch_size": state["legacy_batch_size"],
        }

    monkeypatch.setattr(ai_judges, "load_config", fake_load_config)
    monkeypatch.setattr(ai_judges, "_selected_roles", lambda _cfg: ["semantic_judge", "factual_judge"])

    def fake_batch(role, answers, question, expected, rubrics_by_answer, retries, avoid_models=None, provider_hint=None):
        batch_calls.append((role, list(answers)))
        # Simulate a Settings save lowering the batch size after the first
        # role finishes; the next role must pick it up.
        state["legacy_batch_size"] = 1
        return {
            answer: {
                "role": role,
                "model": "model",
                "decision": "YES",
                "confidence": 0.99,
                "reason_short": "batch",
                "requirements_met": [],
                "requirements_missing": [],
                "contradictions": [],
                "calculation_check": "ok",
            }
            for answer in answers
        }

    monkeypatch.setattr(ai_judges, "call_judge_role_batch_sync", fake_batch)

    single_calls = []

    def fake_single(role, answer, question, expected, rubric, retries, avoid_models=None, provider_hint=None):
        single_calls.append((role, answer))
        return {
            "role": role,
            "model": "model",
            "decision": "YES",
            "confidence": 0.99,
            "reason_short": "single",
            "requirements_met": [],
            "requirements_missing": [],
            "contradictions": [],
            "calculation_check": "ok",
        }

    monkeypatch.setattr(ai_judges, "call_judge_role_sync", fake_single)

    ai_judges.run_judges_model_first(
        ["a", "b", "c"],
        "question",
        "expected",
        {"a": {}, "b": {}, "c": {}},
        retries=1,
    )

    assert batch_calls == [("semantic_judge", ["a", "b"]), ("semantic_judge", ["c"])]
    assert single_calls == [("factual_judge", "a"), ("factual_judge", "b"), ("factual_judge", "c")]


def test_form_model_plan_counts_answers_per_role_with_adjudicator_capacity(monkeypatch):
    cfg = {
        "provider_manager_enabled": False,
        "active_judge_roles": ["semantic_judge", "factual_judge", "strict_judge"],
        "adaptive_math_jury": {
            "enabled": True,
            "primary_roles": ["semantic_judge", "factual_judge"],
            "adjudicator_role": "strict_judge",
        },
        "judge_answer_batch_size": 25,
        "ollama_judge_answer_batch_size": 25,
    }
    monkeypatch.setattr(ai_judges, "load_config", lambda: cfg)

    # 26 answers count one unit per answer per role regardless of batching:
    # two primary roles (52) plus one reserved adjudicator slot per answer (26).
    # Retries do not change this logical plan.
    assert ai_judges.estimate_form_model_calls({"q1": ["a"] * 26}, cfg, True) == 78


def test_model_progress_never_exceeds_fixed_plan_when_batch_succeeds(monkeypatch, capsys):
    cfg = {
        "provider_manager_enabled": False,
        "jury_models": {"semantic_judge": "model-a", "factual_judge": "model-b"},
        "active_judge_roles": ["semantic_judge", "factual_judge"],
        "adaptive_math_jury": {"enabled": False},
        "judge_answer_batch_size": 2,
        "ollama_judge_answer_batch_size": 2,
    }
    monkeypatch.setattr(ai_judges, "load_config", lambda: cfg)
    monkeypatch.setattr(ai_judges, "_selected_roles", lambda _cfg: ["semantic_judge", "factual_judge"])
    monkeypatch.setattr(
        ai_judges,
        "call_judge_role_batch_sync",
        lambda role, answers, *args, **kwargs: {
            answer: _judge_result(index + 1)
            for index, answer in enumerate(answers)
        },
    )

    ai_judges.configure_model_progress(6, scope="test")
    ai_judges.run_judges_model_first(["a", "b", "c"], "q", "e", {}, retries=1)

    progress = [line for line in capsys.readouterr().out.splitlines() if line.startswith("ModelProgress:")]
    assert progress[-1] == "ModelProgress: 6/6"
    assert not any("ModelProgressWarning:" in line for line in progress)


def test_model_first_avoid_models_are_scoped_to_answer_chunk(monkeypatch):
    batch_calls = []

    monkeypatch.setattr(
        ai_judges,
        "load_config",
        lambda: {
            "jury_models": {
                "semantic_judge": "model-a",
                "factual_judge": "model-b",
            },
            "active_judge_roles": ["semantic_judge", "factual_judge"],
            "adaptive_math_jury": {"enabled": False},
            "provider_manager_enabled": True,
            "provider_priority": ["openrouter", "ollama"],
            "judge_answer_batch_size": 2,
            "openrouter_judge_answer_batch_size": 2,
        },
    )
    monkeypatch.setattr(ai_judges, "_selected_roles", lambda _cfg: ["semantic_judge", "factual_judge"])

    def fake_batch(role, answers, question, expected, rubrics_by_answer, retries, avoid_models=None, provider_hint=None):
        batch_calls.append((role, list(answers), list(avoid_models or [])))
        model = "model-x" if answers == ["a", "b"] else "model-y"
        if role == "factual_judge":
            model = "model-factual"
        return {
            answer: {
                "role": role,
                "provider": "openrouter",
                "model": model,
                "decision": "YES",
                "confidence": 0.99,
                "reason_short": "ok",
                "requirements_met": [],
                "requirements_missing": [],
                "contradictions": [],
                "calculation_check": "ok",
            }
            for answer in answers
        }

    monkeypatch.setattr(ai_judges, "call_judge_role_batch_sync", fake_batch)

    ai_judges.run_judges_model_first(
        ["a", "b", "c", "d"],
        "question",
        "expected",
        {"a": {}, "b": {}, "c": {}, "d": {}},
        retries=1,
    )

    assert batch_calls == [
        ("semantic_judge", ["a", "b"], []),
        ("semantic_judge", ["c", "d"], []),
        ("factual_judge", ["a", "b"], ["model-x"]),
        ("factual_judge", ["c", "d"], ["model-y"]),
    ]


def test_model_first_provider_hint_controls_chunking_and_routing(monkeypatch):
    batch_calls = []
    single_calls = []

    monkeypatch.setattr(
        ai_judges,
        "load_config",
        lambda: {
            "jury_models": {"semantic_judge": "model-a"},
            "active_judge_roles": ["semantic_judge"],
            "adaptive_math_jury": {"enabled": False},
            "provider_manager_enabled": True,
            "provider_priority": ["openrouter", "llamacpp", "ollama"],
            "judge_answer_batch_size": 25,
            "openrouter_judge_answer_batch_size": 25,
            "llamacpp_judge_answer_batch_size": 1,
        },
    )
    monkeypatch.setattr(ai_judges, "_selected_roles", lambda _cfg: ["semantic_judge"])

    def fake_batch(role, answers, question, expected, rubrics_by_answer, retries, avoid_models=None, provider_hint=None):
        batch_calls.append((role, list(answers), provider_hint))
        return {
            answer: {
                "role": role,
                "provider": provider_hint or "openrouter",
                "model": "model-a",
                "decision": "YES",
                "confidence": 0.99,
                "reason_short": "ok",
                "requirements_met": [],
                "requirements_missing": [],
                "contradictions": [],
                "calculation_check": "ok",
            }
            for answer in answers
        }

    def fake_single(role, answer, question, expected, rubric, retries, avoid_models=None, provider_hint=None):
        single_calls.append((answer, provider_hint))
        return {
            "role": role,
            "provider": provider_hint or "openrouter",
            "model": "model-a",
            "decision": "YES",
            "confidence": 0.99,
            "reason_short": "ok",
            "requirements_met": [],
            "requirements_missing": [],
            "contradictions": [],
            "calculation_check": "ok",
        }

    monkeypatch.setattr(ai_judges, "call_judge_role_batch_sync", fake_batch)
    monkeypatch.setattr(ai_judges, "call_judge_role_sync", fake_single)

    # llamacpp hint -> per-answer chunking (batch size 1) routed with the hint.
    ai_judges.run_judges_model_first(
        ["a", "b"],
        "question",
        "expected",
        {"a": {}, "b": {}},
        retries=1,
        provider_hint="llamacpp",
    )
    assert single_calls == [("a", "llamacpp"), ("b", "llamacpp")]
    assert batch_calls == []

    # openrouter hint -> whole-set chunk (batch size 25) routed with the hint.
    single_calls.clear()
    ai_judges.run_judges_model_first(
        ["a", "b"],
        "question",
        "expected",
        {"a": {}, "b": {}},
        retries=1,
        provider_hint="openrouter",
    )
    # The chunk must fit EVERY provider in the failover chain; llamacpp is
    # pinned to 1 answer per call, so even an OR-hint call degrades to
    # singles instead of shipping llama an oversized batch.
    assert sorted(single_calls) == [("a", "openrouter"), ("b", "openrouter")]
    assert batch_calls == []

    # When every provider allows it, the hint still controls batching.
    batch_calls.clear()
    single_calls.clear()
    monkeypatch.setattr(
        ai_judges,
        "load_config",
        lambda: {
            "jury_models": {"semantic_judge": "model-a"},
            "active_judge_roles": ["semantic_judge"],
            "adaptive_math_jury": {"enabled": False},
            "provider_manager_enabled": True,
            "provider_priority": ["openrouter", "ollama"],
            "judge_answer_batch_size": 25,
            "openrouter_judge_answer_batch_size": 25,
            "ollama_judge_answer_batch_size": 25,
        },
    )
    ai_judges.run_judges_model_first(
        ["a", "b"],
        "question",
        "expected",
        {"a": {}, "b": {}},
        retries=1,
        provider_hint="openrouter",
    )
    assert single_calls == []
    assert batch_calls == [("semantic_judge", ["a", "b"], "openrouter")]


def test_lane_request_metadata_pins_provider_priority(monkeypatch):
    monkeypatch.setattr(
        ai_judges,
        "load_config",
        lambda: {"provider_priority": ["openrouter", "llamacpp", "ollama"]},
    )
    meta = ai_judges._lane_request_metadata(["used-model"], "llamacpp")
    assert meta["avoid_models"] == ["used-model"]
    assert meta["provider_priority"] == ["llamacpp", "openrouter", "ollama"]
    # No hint -> no priority override; normal strategy routing applies.
    plain = ai_judges._lane_request_metadata([], None)
    assert "provider_priority" not in plain


def test_preferred_batch_provider_falls_back_when_openrouter_unavailable(monkeypatch):
    cfg = {
        "provider_manager_enabled": True,
        "provider_strategy": "openrouter_llamacpp",
        "provider_priority": ["openrouter", "llamacpp", "ollama"],
        "judge_answer_batch_size": 25,
        "openrouter_judge_answer_batch_size": 25,
        "llamacpp_judge_answer_batch_size": 1,
    }

    class _FakeManager:
        def provider_available(self, provider_name):
            return provider_name != "openrouter"

    monkeypatch.setattr(ai_judges, "is_provider_available", _FakeManager().provider_available)

    assert ai_judges._preferred_batch_provider(cfg) == "llamacpp"
    assert ai_judges._judge_answer_batch_size(cfg) == 1


def test_registry_has_any_available_model_handles_cooldown_and_unknown():
    cfg = {"openrouter_models": {"semantic_judge": ["model-a", "model-b"]}}
    registry = OpenRouterModelRegistry()

    assert registry.has_any_available_model(cfg) is True

    registry.record_failure("model-a", "rate_limited", "busy", cfg)
    registry.record_failure("model-b", "rate_limited", "busy", cfg)

    assert registry.has_any_available_model(cfg) is False

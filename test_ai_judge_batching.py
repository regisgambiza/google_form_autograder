import json

import ai_judges


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

    def fake_single(role, answer, question, expected, rubric, retries):
        single_calls.append(answer)
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
    )

    assert single_calls == ["b"]
    assert out["a"]["decision"] == "YES"
    assert out["b"]["decision"] == "NO"
    assert out["c"]["decision"] == "YES"


def test_model_first_judging_refreshes_answer_batch_size_between_roles(monkeypatch):
    config_calls = []
    batch_calls = []

    def fake_load_config():
        config_calls.append(len(config_calls))
        batch_size = 2 if len(config_calls) <= 2 else 1
        return {
            "jury_models": {
                "semantic_judge": "model-a",
                "factual_judge": "model-b",
            },
            "active_judge_roles": ["semantic_judge", "factual_judge"],
            "adaptive_math_jury": {"enabled": False},
            "judge_answer_batch_size": batch_size,
        }

    monkeypatch.setattr(ai_judges, "load_config", fake_load_config)
    monkeypatch.setattr(ai_judges, "_selected_roles", lambda _cfg: ["semantic_judge", "factual_judge"])

    def fake_batch(role, answers, question, expected, rubrics_by_answer, retries):
        batch_calls.append((role, list(answers)))
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

    def fake_single(role, answer, question, expected, rubric, retries):
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

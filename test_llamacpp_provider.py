import providers.llamacpp_provider as llamacpp_provider
from provider_types import ProviderError
from providers.llamacpp_provider import JUDGE_JSON_GRAMMAR, LlamaCppProvider


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {"content-type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"{self.status_code} error")

    def json(self):
        return self._payload


def test_llamacpp_provider_uses_completion_endpoint_for_structured_judges_by_default(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, dict(json)))
        return _FakeResponse(200, {"content": _valid_judge_json()})

    monkeypatch.setattr(llamacpp_provider.requests, "post", fake_post)

    response = LlamaCppProvider().chat(
        {
            "model": "local/model.gguf",
            "messages": [{"role": "user", "content": "grade this"}],
            "options": {"temperature": 0.0, "num_predict": 128},
            "format": {"type": "object"},
        },
        5,
    )

    assert len(calls) == 1
    assert calls[0][0].endswith("/completion")
    assert calls[0][1]["n_predict"] == 256
    assert calls[0][1]["grammar"] == JUDGE_JSON_GRAMMAR
    assert "grade this" in calls[0][1]["prompt"]
    assert response["message"]["content"].startswith("{")


def test_llamacpp_provider_can_fall_back_from_chat_to_completion_endpoint(monkeypatch):
    calls = []

    monkeypatch.setattr(
        llamacpp_provider,
        "load_config",
        lambda: {
            "llamacpp_api_base_url": "http://127.0.0.1:8081",
            "llamacpp_auto_detect_base_url": False,
            "llamacpp_endpoint_mode": "chat",
            "llamacpp_json_min_predict": 256,
            "llamacpp_response_timeout_seconds": 600,
        },
    )

    def fake_post(url, json, timeout):
        calls.append((url, dict(json)))
        if url.endswith("/v1/chat/completions"):
            return _FakeResponse(405, {})
        return _FakeResponse(200, {"content": _valid_judge_json()})

    monkeypatch.setattr(llamacpp_provider.requests, "post", fake_post)

    response = LlamaCppProvider().chat(
        {
            "model": "local/model.gguf",
            "messages": [{"role": "user", "content": "grade this"}],
            "options": {"temperature": 0.0, "num_predict": 128},
            "format": {"type": "object"},
        },
        5,
    )

    assert calls[0][0].endswith("/v1/chat/completions")
    assert calls[1][0].endswith("/completion")
    assert response["message"]["content"].startswith("{")


def test_llamacpp_provider_rejects_html_server_as_unconfigured(monkeypatch):
    monkeypatch.setattr(
        llamacpp_provider.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(
            200,
            {},
            text="<!doctype html><html></html>",
            headers={"content-type": "text/html"},
        ),
    )

    assert LlamaCppProvider().is_configured() is False


def test_llamacpp_provider_reads_llama_server_models_shape(monkeypatch):
    monkeypatch.setattr(
        llamacpp_provider.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(
            200,
            {
                "models": [{"name": "server-name", "model": "server-model"}],
                "data": [{"id": "openai-id"}],
            },
        ),
    )

    assert LlamaCppProvider().list_server_models() == ["openai-id", "server-model"]


def test_llamacpp_provider_reports_model_not_found_without_completion_fallback(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(url)
        return _FakeResponse(
            404,
            {"error": {"message": "model local/missing.gguf not found"}},
            text='{"error":{"message":"model local/missing.gguf not found"}}',
        )

    monkeypatch.setattr(llamacpp_provider.requests, "post", fake_post)

    try:
        LlamaCppProvider().chat({"model": "local/missing.gguf", "messages": []}, 5)
    except ProviderError as exc:
        assert exc.category == "model_not_found"
    else:
        raise AssertionError("expected ProviderError")

    assert len(calls) == 1
    assert calls[0].endswith("/v1/chat/completions")


def _valid_judge_json() -> str:
    return (
        '{"decision":"YES","confidence":1.0,"reason_short":"ok",'
        '"requirements_met":["match"],"requirements_missing":[],'
        '"contradictions":[],"calculation_check":"not applicable"}'
    )


def test_llamacpp_extracts_markdown_fenced_json():
    raw = "```json\n" + _valid_judge_json() + "\n```"
    assert LlamaCppProvider._extract_final_json_text(raw) == _valid_judge_json()


def test_llamacpp_extracts_think_then_final_json():
    raw = "<think>{\"decision\":\"NO\"}</think>\n" + _valid_judge_json()
    assert LlamaCppProvider._extract_final_json_text(raw) == _valid_judge_json()


def test_llamacpp_extracts_json_after_explanatory_text():
    raw = "Here is the result:\n" + _valid_judge_json()
    assert LlamaCppProvider._extract_final_json_text(raw) == _valid_judge_json()


def test_llamacpp_extracts_json_before_harmless_trailing_text():
    raw = _valid_judge_json() + "\nDone."
    assert LlamaCppProvider._extract_final_json_text(raw) == _valid_judge_json()


def test_llamacpp_balanced_extractor_handles_nested_json_and_quoted_braces():
    raw = (
        'prefix {"decision":"YES","confidence":1.0,"reason_short":"has {quoted} braces",'
        '"requirements_met":["path C:\\\\tmp\\\\x","nested { text }"],'
        '"requirements_missing":[],"contradictions":[],"calculation_check":"ok"} suffix'
    )
    extracted = LlamaCppProvider._extract_final_json_text(raw)
    parsed = llamacpp_provider.json.loads(extracted)
    assert parsed["reason_short"] == "has {quoted} braces"
    assert parsed["requirements_met"][0] == "path C:\\tmp\\x"


def test_llamacpp_chat_empty_content_with_reasoning_length_is_specific_error(monkeypatch):
    monkeypatch.setattr(
        llamacpp_provider,
        "load_config",
        lambda: {
            "llamacpp_api_base_url": "http://127.0.0.1:8081",
            "llamacpp_auto_detect_base_url": False,
            "llamacpp_endpoint_mode": "chat",
            "llamacpp_json_min_predict": 256,
            "llamacpp_response_timeout_seconds": 600,
        },
    )

    def fake_post(url, json, timeout):
        return _FakeResponse(200, {
            "choices": [{
                "finish_reason": "length",
                "message": {"content": "", "reasoning_content": "thinking forever"},
            }]
        })

    monkeypatch.setattr(llamacpp_provider.requests, "post", fake_post)

    try:
        LlamaCppProvider().chat({"model": "m", "messages": [], "format": {"type": "object"}}, 5)
    except ProviderError as exc:
        assert exc.category == "llama_truncated_response"
        assert "reasoning only" in str(exc)
    else:
        raise AssertionError("expected ProviderError")


def test_llamacpp_completion_malformed_then_repair(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(dict(json))
        if len(calls) == 1:
            return _FakeResponse(200, {"content": "not json"})
        return _FakeResponse(200, {"content": _valid_judge_json()})

    monkeypatch.setattr(llamacpp_provider.requests, "post", fake_post)

    response = LlamaCppProvider().chat(
        {"model": "m", "messages": [{"role": "user", "content": "grade"}], "format": {"type": "object"}},
        5,
    )

    assert len(calls) == 2
    assert "Repair the following malformed" in calls[1]["prompt"]
    assert response["message"]["content"].startswith("{")


def test_llamacpp_provider_fills_harmless_missing_calculation_check(monkeypatch):
    content = (
        '{"decision":"NO","confidence":0.95,"reason_short":"outside accepted range",'
        '"requirements_met":[],"requirements_missing":["bearing in accepted range"],'
        '"contradictions":["student bearing outside range"]}'
    )
    monkeypatch.setattr(
        llamacpp_provider.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(200, {"content": content}),
    )

    response = LlamaCppProvider().chat(
        {"model": "m", "messages": [], "format": {"type": "object"}},
        5,
    )

    parsed = llamacpp_provider.json.loads(response["message"]["content"])
    assert parsed["decision"] == "NO"
    assert parsed["calculation_check"] == "not applicable"


def test_llamacpp_completion_repair_failure_is_specific(monkeypatch):
    monkeypatch.setattr(
        llamacpp_provider.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(200, {"content": "not json"}),
    )

    try:
        LlamaCppProvider().chat({"model": "m", "messages": [], "format": {"type": "object"}}, 5)
    except ProviderError as exc:
        assert exc.category == "llama_repair_failed"
    else:
        raise AssertionError("expected ProviderError")


def test_llamacpp_completion_timeout_category(monkeypatch):
    def fake_post(*_args, **_kwargs):
        raise llamacpp_provider.requests.ReadTimeout("slow")

    monkeypatch.setattr(llamacpp_provider.requests, "post", fake_post)

    try:
        LlamaCppProvider().chat({"model": "m", "messages": [], "format": {"type": "object"}}, 5)
    except ProviderError as exc:
        assert exc.category == "llama_read_timeout"
    else:
        raise AssertionError("expected ProviderError")

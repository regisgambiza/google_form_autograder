import json

import providers.openrouter_provider as openrouter_provider
from providers.openrouter_provider import OpenRouterProvider


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {"message": {"content": json.dumps({"decision": "YES"})}}
            ],
            "usage": {"completion_tokens": 12},
        }


def test_openrouter_provider_maps_num_predict_to_max_tokens(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        openrouter_provider,
        "load_config",
        lambda: {
            "openrouter_api_key": "test-key",
            "openrouter_api_base_url": "https://openrouter.ai/api/v1",
        },
    )

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(openrouter_provider.requests, "post", fake_post)

    response = OpenRouterProvider().chat(
        {
            "model": "model-a",
            "messages": [{"role": "user", "content": "grade"}],
            "options": {"temperature": 0.0, "num_predict": 8192},
            "format": {"type": "object", "required": ["decision"]},
        },
        timeout_s=60,
    )

    assert captured["json"]["max_tokens"] == 8192
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert response["message"]["content"]

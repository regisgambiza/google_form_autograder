import os
from typing import Any, Dict

import requests

from evaluator_config import load_config
from provider_types import ProviderError
from providers.base import BaseProvider


class OpenRouterProvider(BaseProvider):
    name = "openrouter"

    def _api_key(self) -> str:
        cfg = load_config()
        configured = str(cfg.get("openrouter_api_key") or "").strip()
        if configured.startswith("env:"):
            return os.environ.get(configured.split(":", 1)[1], "").strip()
        return configured or os.environ.get("OPENROUTER_API_KEY", "").strip()

    def _chat_url(self) -> str:
        cfg = load_config()
        return str(cfg.get("openrouter_api_base_url", "https://openrouter.ai/api/v1")).rstrip("/") + "/chat/completions"

    def _models_url(self) -> str:
        cfg = load_config()
        return str(cfg.get("openrouter_api_base_url", "https://openrouter.ai/api/v1")).rstrip("/") + "/models"

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def list_free_models(self, timeout_s: int = 20) -> list[str]:
        """Return currently advertised free model ids from OpenRouter."""
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            resp = requests.get(self._models_url(), headers=headers, timeout=(5, timeout_s))
            resp.raise_for_status()
            data = resp.json()
        except Exception as ex:
            raise ProviderError(str(ex), "transport") from ex
        models = data.get("data") if isinstance(data, dict) else []
        out = []
        for item in models or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            pricing = item.get("pricing") or {}
            prompt_price = str(pricing.get("prompt", "")).strip()
            completion_price = str(pricing.get("completion", "")).strip()
            is_free = model_id.endswith(":free") or (
                prompt_price in {"0", "0.0", "0.000000"}
                and completion_price in {"0", "0.0", "0.000000"}
            )
            if model_id and is_free:
                out.append(model_id)
        return out

    def chat(self, payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
        api_key = self._api_key()
        if not api_key:
            raise ProviderError("OpenRouter API key is not configured", "disabled")

        options = payload.get("options") or {}
        body = {
            "model": payload.get("model"),
            "messages": payload.get("messages", []),
            "temperature": options.get("temperature", 0.0),
            "stream": False,
        }
        if options.get("num_predict"):
            body["max_tokens"] = int(options["num_predict"])
        if payload.get("format"):
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "judge_response",
                    "strict": True,
                    "schema": payload["format"],
                },
            }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://local.google-form-autograder",
            "X-Title": "Google Form Autograder",
        }
        try:
            resp = requests.post(self._chat_url(), json=body, headers=headers, timeout=(10, timeout_s))
            if resp.status_code == 401:
                raise ProviderError("OpenRouter authentication failed", "auth")
            if resp.status_code == 402:
                raise ProviderError("OpenRouter credits exhausted", "out_of_credits")
            if resp.status_code == 404:
                raise ProviderError(f"OpenRouter model not found: {payload.get('model')}", "model_not_found")
            if resp.status_code == 429:
                raise ProviderError("OpenRouter rate limited", "rate_limited")
            resp.raise_for_status()
            data = resp.json()
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            usage = data.get("usage") or {}
            return {"message": {"content": content}, "provider_raw": data, "usage": usage}
        except ProviderError:
            raise
        except requests.Timeout as ex:
            raise ProviderError(str(ex), "timeout") from ex
        except Exception as ex:
            raise ProviderError(str(ex), "transport") from ex

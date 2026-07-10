from typing import Any, Dict

import requests

from evaluator_config import load_config
from provider_types import ProviderError
from providers.base import BaseProvider


class OllamaProvider(BaseProvider):
    name = "ollama"

    def _chat_url(self) -> str:
        cfg = load_config()
        base = str(cfg.get("ollama_api_base_url", "http://127.0.0.1:11434")).rstrip("/")
        return f"{base}/api/chat"

    def is_configured(self) -> bool:
        return True

    def chat(self, payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
        try:
            resp = requests.post(self._chat_url(), json=payload, timeout=(10, timeout_s))
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout as ex:
            raise ProviderError(str(ex), "timeout") from ex
        except Exception as ex:
            raise ProviderError(str(ex), "transport") from ex

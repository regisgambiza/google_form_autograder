import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from evaluator_config import load_config
from logger import log
from provider_types import ProviderError
from providers.base import BaseProvider


JUDGE_JSON_GRAMMAR = r'''
root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws
object ::= "{" ws (string ":" ws value ("," ws string ":" ws value)*)? "}" ws
array  ::= "[" ws (value ("," ws value)*)? "]" ws
string ::= "\"" ([^"\\] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]))* "\"" ws
number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)? ([eE] [-+]? [0-9]+)? ws
ws     ::= [ \t\n\r]*
'''.strip()


LLAMACPP_JUDGE_CONTRACT = (
    "LLAMA.CPP LOCAL JUDGE RESPONSE CONTRACT\n"
    "Return exactly one JSON object. Do not return Markdown fences. Do not return <think> tags. "
    "Do not write commentary before or after the JSON.\n"
    "Required keys and exact value types:\n"
    "- decision: string, exactly \"YES\" or \"NO\"\n"
    "- confidence: number from 0.0 to 1.0\n"
    "- reason_short: short string\n"
    "- requirements_met: array of strings\n"
    "- requirements_missing: array of strings\n"
    "- contradictions: array of strings\n"
    "- calculation_check: string\n"
    "Valid example:\n"
    "{\"decision\":\"YES\",\"confidence\":1.0,\"reason_short\":\"exact match\","
    "\"requirements_met\":[\"student answer matches expected\"],"
    "\"requirements_missing\":[],\"contradictions\":[],\"calculation_check\":\"not applicable\"}\n"
)


LLAMACPP_BATCH_JUDGE_CONTRACT = (
    "LLAMA.CPP LOCAL BATCH JUDGE RESPONSE CONTRACT\n"
    "Return exactly one JSON object with a single \"results\" array. "
    "Do not return Markdown fences. Do not return <think> tags. "
    "Do not write commentary before or after the JSON.\n"
    "Each results[] item needs these keys and exact value types:\n"
    "- answer_index: integer >= 1\n"
    "- decision: string, exactly \"YES\" or \"NO\"\n"
    "- confidence: number from 0.0 to 1.0\n"
    "- reason_short: short string\n"
    "- requirements_met: array of strings\n"
    "- requirements_missing: array of strings\n"
    "- contradictions: array of strings\n"
    "- calculation_check: string\n"
    "Valid example for two answers:\n"
    "{\"results\":[{\"answer_index\":1,\"decision\":\"YES\",\"confidence\":1.0,"
    "\"reason_short\":\"exact match\",\"requirements_met\":[],"
    "\"requirements_missing\":[],\"contradictions\":[],"
    "\"calculation_check\":\"not applicable\"},"
    "{\"answer_index\":2,\"decision\":\"NO\",\"confidence\":0.95,"
    "\"reason_short\":\"wrong value\",\"requirements_met\":[],"
    "\"requirements_missing\":[\"correct value 3\"],"
    "\"contradictions\":[],\"calculation_check\":\"not applicable\"}]}\n"
)


class LlamaCppProvider(BaseProvider):
    name = "llamacpp"

    def _base_url(self) -> str:
        cfg = load_config()
        configured = str(cfg.get("llamacpp_api_base_url", "http://127.0.0.1:8081")).rstrip("/")
        if not bool(cfg.get("llamacpp_auto_detect_base_url", True)):
            return configured
        if self._base_url_looks_like_llamacpp(configured):
            return configured
        for candidate in ("http://127.0.0.1:8081", "http://127.0.0.1:1234", "http://127.0.0.1:8080"):
            candidate = candidate.rstrip("/")
            if candidate != configured and self._base_url_looks_like_llamacpp(candidate):
                return candidate
        return configured

    def _base_url_looks_like_llamacpp(self, base_url: str) -> bool:
        base = str(base_url or "").rstrip("/")
        return self._json_endpoint_available(f"{base}/v1/models") or self._json_endpoint_available(f"{base}/props")

    def _chat_url(self) -> str:
        return f"{self._base_url()}/v1/chat/completions"

    def _completion_url(self) -> str:
        return f"{self._base_url()}/completion"

    def _models_url(self) -> str:
        return f"{self._base_url()}/v1/models"

    def _props_url(self) -> str:
        return f"{self._base_url()}/props"

    def is_configured(self) -> bool:
        cfg = load_config()
        if not bool(cfg.get("llamacpp_enabled", True)):
            return False
        if bool(cfg.get("llamacpp_require_server", True)):
            return self._json_endpoint_available(self._models_url()) or self._json_endpoint_available(self._props_url())
        return True

    @staticmethod
    def _json_endpoint_available(url: str) -> bool:
        try:
            resp = requests.get(url, timeout=(1, 2))
            resp.raise_for_status()
            content_type = str(resp.headers.get("content-type", "")).lower()
            if "json" not in content_type and not str(resp.text or "").lstrip().startswith(("{", "[")):
                return False
            resp.json()
            return True
        except Exception:
            return False

    def list_local_models(self) -> List[str]:
        cfg = load_config()
        root = os.path.expandvars(os.path.expanduser(str(
            cfg.get("llamacpp_model_dir", r"C:\Users\regis\.lmstudio\models")
        )))
        out: List[str] = []
        if not os.path.isdir(root):
            return out
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                lower_name = filename.lower()
                if not lower_name.endswith(".gguf"):
                    continue
                if lower_name.startswith("mmproj-") or "mmproj" in lower_name:
                    continue
                path = os.path.join(dirpath, filename)
                rel = os.path.relpath(path, root).replace("\\", "/")
                out.append(rel)
        return sorted(out, key=str.casefold)

    def list_server_models(self, timeout_s: int = 5) -> List[str]:
        try:
            resp = requests.get(self._models_url(), timeout=(2, timeout_s))
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        models = []
        if isinstance(data, dict):
            if isinstance(data.get("data"), list):
                models.extend(data.get("data") or [])
            if isinstance(data.get("models"), list):
                models.extend(data.get("models") or [])
        out = []
        for item in models or []:
            if isinstance(item, dict):
                model_id = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
                if model_id:
                    out.append(model_id)
        return out

    def chat(self, payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
        cfg = load_config()
        mode = str(cfg.get("llamacpp_endpoint_mode", "completion") or "completion").strip().lower()
        if payload.get("format") and mode not in {"chat", "openai_chat"}:
            return self._completion_chat(payload, timeout_s)
        return self._chat_completion(payload, timeout_s)

    def _chat_completion(self, payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
        cfg = load_config()
        options = payload.get("options") or {}
        body = {
            "model": payload.get("model") or "local",
            "messages": payload.get("messages", []),
            "temperature": float(options.get("temperature", 0.0) or 0.0),
            "stream": False,
        }
        if payload.get("format"):
            body["response_format"] = {"type": "json_object"}
        max_tokens = self._max_tokens(payload, options, cfg)
        if max_tokens:
            body["max_tokens"] = max_tokens

        url = self._chat_url()
        start = time.perf_counter()
        try:
            resp = requests.post(url, json=body, timeout=self._timeout_tuple(timeout_s, cfg))
            if resp.status_code == 404 and self._looks_like_model_not_found(resp):
                raise ProviderError(f"llama.cpp model not found: {payload.get('model')}", "model_not_found")
            if resp.status_code in {404, 405}:
                return self._completion_chat(payload, timeout_s)
            if resp.status_code == 429:
                raise ProviderError("llama.cpp server rate limited", "rate_limited")
            resp.raise_for_status()
            data = resp.json()
            content, finish_reason = self._extract_chat_content(data)
            content = self._prepare_generated_content(content, finish_reason, "chat", payload, timeout_s)
            self._log_success("chat", url, payload.get("model"), data, content, start)
            return {"message": {"content": content}, "provider_raw": data, "usage": data.get("usage") or {}}
        except ProviderError:
            raise
        except requests.ConnectTimeout as ex:
            raise ProviderError(str(ex), "llama_connection_timeout") from ex
        except requests.ReadTimeout as ex:
            raise ProviderError(str(ex), "llama_read_timeout") from ex
        except requests.Timeout as ex:
            raise ProviderError(str(ex), "llama_read_timeout") from ex
        except Exception as ex:
            raise ProviderError(str(ex), "llama_transport_error") from ex

    def _completion_chat(self, payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
        cfg = load_config()
        options = payload.get("options") or {}
        body = self._completion_body(payload, options, cfg, repair_prompt=None)
        url = self._completion_url()
        start = time.perf_counter()
        try:
            data = self._post_completion(url, body, timeout_s, cfg)
            content, finish_reason = self._extract_completion_content(data)
            try:
                content = self._prepare_generated_content(content, finish_reason, "completion", payload, timeout_s)
            except ProviderError as first_error:
                attempts = max(0, int(cfg.get("llamacpp_max_repair_attempts", 1) or 1))
                if attempts <= 0 or first_error.category not in {"llama_malformed_json", "llama_truncated_response", "llama_schema_mismatch"}:
                    raise
                repair_prompt = self._make_repair_prompt(payload, content, first_error)
                repair_body = self._completion_body(payload, options, cfg, repair_prompt=repair_prompt)
                repair_data = self._post_completion(url, repair_body, timeout_s, cfg)
                repair_content, repair_finish = self._extract_completion_content(repair_data)
                try:
                    content = self._prepare_generated_content(repair_content, repair_finish, "completion_repair", payload, timeout_s)
                    data = repair_data
                except ProviderError as repair_error:
                    raise ProviderError(
                        f"llama.cpp repair failed after {attempts} attempt(s): {repair_error}; original={first_error}",
                        "llama_repair_failed",
                    ) from repair_error
            self._log_success("completion", url, payload.get("model"), data, content, start)
            return {"message": {"content": content}, "provider_raw": data, "usage": data.get("usage") or {}}
        except ProviderError:
            raise
        except requests.ConnectTimeout as ex:
            raise ProviderError(str(ex), "llama_connection_timeout") from ex
        except requests.ReadTimeout as ex:
            raise ProviderError(str(ex), "llama_read_timeout") from ex
        except requests.Timeout as ex:
            raise ProviderError(str(ex), "llama_read_timeout") from ex
        except Exception as ex:
            raise ProviderError(str(ex), "llama_transport_error") from ex

    def _completion_body(
        self,
        payload: Dict[str, Any],
        options: Dict[str, Any],
        cfg: Dict[str, Any],
        repair_prompt: Optional[str],
    ) -> Dict[str, Any]:
        prompt = repair_prompt if repair_prompt is not None else self._messages_to_prompt(
            payload.get("messages", []),
            bool(payload.get("format")),
            is_batch=self._payload_is_batch(payload),
        )
        body = {
            "prompt": prompt,
            "temperature": float(options.get("temperature", 0.0) or 0.0),
            "stream": False,
        }
        n_predict = self._max_tokens(payload, options, cfg)
        if n_predict:
            body["n_predict"] = n_predict
        if payload.get("format") and bool(cfg.get("llamacpp_use_gbnf_grammar", True)):
            body["grammar"] = JUDGE_JSON_GRAMMAR
        return body

    def _post_completion(self, url: str, body: Dict[str, Any], timeout_s: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
        resp = requests.post(url, json=body, timeout=self._timeout_tuple(timeout_s, cfg))
        if resp.status_code == 404:
            raise ProviderError("llama.cpp /completion endpoint not found", "llama_transport_error")
        if resp.status_code == 429:
            raise ProviderError("llama.cpp server rate limited", "rate_limited")
        resp.raise_for_status()
        return resp.json()

    def _max_tokens(self, payload: Dict[str, Any], options: Dict[str, Any], cfg: Dict[str, Any]) -> int:
        raw = int(options.get("num_predict") or options.get("max_tokens") or 0)
        if payload.get("format"):
            raw = max(raw, int(cfg.get("llamacpp_json_min_predict", 256) or 256))
        return raw

    def _timeout_tuple(self, timeout_s: int, cfg: Dict[str, Any]) -> Tuple[int, int]:
        connect = max(1, int(cfg.get("llamacpp_connect_timeout_seconds", 30) or 30))
        read = max(600, int(cfg.get("llamacpp_response_timeout_seconds", timeout_s) or timeout_s))
        return connect, max(read, int(timeout_s or 0))

    def _extract_chat_content(self, data: Dict[str, Any]) -> Tuple[str, str]:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("llama.cpp chat response missing choices", "llama_unsupported_response_shape")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("llama.cpp chat choice is not an object", "llama_unsupported_response_shape")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("llama.cpp chat response missing message", "llama_unsupported_response_shape")
        content = message.get("content")
        finish_reason = str(choice.get("finish_reason") or "")
        if content is None:
            raise ProviderError("llama.cpp chat message.content is null", "llama_empty_response")
        if isinstance(content, list):
            content = self._content_parts_to_text(content)
        if not isinstance(content, str):
            raise ProviderError("llama.cpp chat message.content is not text", "llama_unsupported_response_shape")
        if not content.strip():
            reasoning_excerpt = str(message.get("reasoning_content") or "")[:300].replace("\n", "\\n")
            if finish_reason in {"length", "max_tokens"}:
                raise ProviderError(
                    f"llama.cpp chat content empty; reasoning only and truncated finish_reason={finish_reason} reasoning={reasoning_excerpt!r}",
                    "llama_truncated_response",
                )
            raise ProviderError(
                f"llama.cpp chat content empty; reasoning={reasoning_excerpt!r}",
                "llama_empty_response",
            )
        return content, finish_reason

    def _extract_completion_content(self, data: Dict[str, Any]) -> Tuple[str, str]:
        if not isinstance(data, dict):
            raise ProviderError("llama.cpp completion response is not an object", "llama_unsupported_response_shape")
        content = data.get("content")
        if content is None:
            content = data.get("completion")
        if content is None:
            content = data.get("response")
        if content is None and isinstance(data.get("choices"), list) and data.get("choices"):
            choice = data["choices"][0]
            if isinstance(choice, dict):
                content = choice.get("text")
        if content is None:
            raise ProviderError("llama.cpp completion response has no content field", "llama_unsupported_response_shape")
        if isinstance(content, list):
            content = self._content_parts_to_text(content)
        if not isinstance(content, str):
            raise ProviderError("llama.cpp completion content is not text", "llama_unsupported_response_shape")
        finish_reason = str(data.get("stop_type") or data.get("finish_reason") or "")
        if not content.strip():
            raise ProviderError("llama.cpp completion content is empty", "llama_empty_response")
        return content, finish_reason

    @staticmethod
    def _content_parts_to_text(parts: List[Any]) -> str:
        out: List[str] = []
        for part in parts:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if text:
                    out.append(str(text))
        return "".join(out)

    def _prepare_generated_content(
        self,
        content: str,
        finish_reason: str,
        mode: str,
        payload: Dict[str, Any],
        timeout_s: int,
    ) -> str:
        text = self._extract_final_json_text(content)
        if not text:
            if finish_reason in {"length", "max_tokens"}:
                raise ProviderError(
                    f"llama.cpp {mode} response truncated before a complete JSON object; raw={self._excerpt(content)!r}",
                    "llama_truncated_response",
                )
            raise ProviderError(
                f"llama.cpp {mode} response is malformed JSON; raw={self._excerpt(content)!r}",
                "llama_malformed_json",
            )
        if finish_reason in {"length", "max_tokens"} and not self._has_complete_json_object(content):
            raise ProviderError(
                f"llama.cpp {mode} response hit token limit before complete JSON; raw={self._excerpt(content)!r}",
                "llama_truncated_response",
            )
        try:
            parsed = json.loads(text)
        except Exception as exc:
            raise ProviderError(
                f"llama.cpp {mode} JSON parse failed: {exc}; raw={self._excerpt(text)!r}",
                "llama_malformed_json",
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderError("llama.cpp parsed response is not a JSON object", "llama_schema_mismatch")
        if payload.get("format"):
            if self._payload_is_batch(payload):
                parsed = self._fill_harmless_batch_defaults(parsed)
                self._validate_batch_judge_contract(parsed, mode)
            else:
                parsed = self._fill_harmless_judge_defaults(parsed)
                self._validate_judge_contract(parsed, mode)
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _payload_is_batch(payload: Dict[str, Any]) -> bool:
        """Whether the payload's format describes a batch (results[]) contract.

        The judge layer sends single contracts for one answer and a
        ``results``-array contract for batched calls; the provider must
        validate against the same contract it was handed, otherwise every
        successful batch response is rejected as schema-mismatched.
        """
        fmt = payload.get("format")
        if not isinstance(fmt, dict):
            return False
        properties = fmt.get("properties")
        return isinstance(properties, dict) and "results" in properties

    @staticmethod
    def _fill_harmless_batch_defaults(parsed: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(parsed)
        results = out.get("results")
        if not isinstance(results, list):
            return out
        filled: List[Any] = []
        for item in results:
            if not isinstance(item, dict):
                filled.append(item)
                continue
            decision = str(item.get("decision", "")).strip().upper()
            if decision in {"0", "FALSE", "INCORRECT", "FAIL", "WRONG", "NO"}:
                decision = "NO"
            elif decision in {"1", "TRUE", "CORRECT", "PASS", "YES"}:
                decision = "YES"
            raw_conf = item.get("confidence")
            conf_val = None
            if raw_conf is not None and not isinstance(raw_conf, bool):
                try:
                    conf_val = float(raw_conf)
                except (TypeError, ValueError):
                    pass
            has_core_verdict = (
                decision in {"YES", "NO"}
                and conf_val is not None
                and isinstance(item.get("reason_short"), str)
            )
            if not has_core_verdict:
                filled.append(item)
                continue
            entry = dict(item)
            entry["decision"] = decision
            entry["confidence"] = conf_val
            entry.setdefault("requirements_met", [])
            entry.setdefault("requirements_missing", [])
            entry.setdefault("contradictions", [])
            entry.setdefault("calculation_check", "not applicable")
            filled.append(entry)
        out["results"] = filled
        return out

    def _validate_batch_judge_contract(self, parsed: Dict[str, Any], mode: str) -> None:
        results = parsed.get("results")
        if not isinstance(results, list) or not results:
            raise ProviderError(
                f"llama.cpp {mode} batch JSON missing non-empty results array",
                "llama_schema_mismatch",
            )
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                raise ProviderError(
                    f"llama.cpp {mode} batch results[{index}] is not an object",
                    "llama_schema_mismatch",
                )
            answer_index = item.get("answer_index")
            if not isinstance(answer_index, int) or isinstance(answer_index, bool) or answer_index < 1:
                raise ProviderError(
                    f"llama.cpp {mode} batch results[{index}].answer_index must be an integer >= 1",
                    "llama_schema_mismatch",
                )
            for key in ("decision", "reason_short", "calculation_check"):
                if key not in item or not isinstance(item[key], str):
                    raise ProviderError(
                        f"llama.cpp {mode} batch results[{index}] field {key} missing/not string",
                        "llama_schema_mismatch",
                    )
            confidence = item.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ProviderError(
                    f"llama.cpp {mode} batch results[{index}] field confidence has wrong type",
                    "llama_schema_mismatch",
                )
            if float(confidence) < 0.0 or float(confidence) > 1.0:
                raise ProviderError(
                    f"llama.cpp {mode} batch results[{index}] confidence outside 0..1",
                    "llama_schema_mismatch",
                )
            if item.get("decision") not in {"YES", "NO"}:
                raise ProviderError(
                    f"llama.cpp {mode} batch results[{index}] decision is not YES/NO: {item.get('decision')!r}",
                    "llama_schema_mismatch",
                )
            for key in ("requirements_met", "requirements_missing", "contradictions"):
                value = item.get(key, [])
                if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                    raise ProviderError(
                        f"llama.cpp {mode} batch results[{index}] field {key} must be string array",
                        "llama_schema_mismatch",
                    )

    @staticmethod
    def _fill_harmless_judge_defaults(parsed: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(parsed)
        decision = str(out.get("decision", "")).strip().upper()
        if decision in {"0", "FALSE", "INCORRECT", "FAIL", "WRONG", "NO"}:
            decision = "NO"
        elif decision in {"1", "TRUE", "CORRECT", "PASS", "YES"}:
            decision = "YES"
        raw_conf = out.get("confidence")
        conf_val = None
        if raw_conf is not None and not isinstance(raw_conf, bool):
            try:
                conf_val = float(raw_conf)
            except (TypeError, ValueError):
                pass
        has_core_verdict = decision in {"YES", "NO"} and conf_val is not None and isinstance(out.get("reason_short"), str)
        if not has_core_verdict:
            return out
        out["decision"] = decision
        out["confidence"] = conf_val
        out.setdefault("requirements_met", [])
        out.setdefault("requirements_missing", [])
        out.setdefault("contradictions", [])
        out.setdefault("calculation_check", "not applicable")
        return out

    def _validate_judge_contract(self, parsed: Dict[str, Any], mode: str) -> None:
        required = {
            "decision": str,
            "confidence": (int, float),
            "reason_short": str,
            "requirements_met": list,
            "requirements_missing": list,
            "contradictions": list,
            "calculation_check": str,
        }
        for key, expected_type in required.items():
            if key not in parsed:
                raise ProviderError(f"llama.cpp {mode} JSON missing required field {key}", "llama_schema_mismatch")
            if not isinstance(parsed[key], expected_type) or (key == "confidence" and isinstance(parsed[key], bool)):
                raise ProviderError(f"llama.cpp {mode} JSON field {key} has wrong type", "llama_schema_mismatch")
        if parsed.get("decision") not in {"YES", "NO"}:
            raise ProviderError(f"llama.cpp {mode} JSON decision is not YES/NO: {parsed.get('decision')!r}", "llama_schema_mismatch")
        confidence = float(parsed.get("confidence"))
        if confidence < 0.0 or confidence > 1.0:
            raise ProviderError(f"llama.cpp {mode} JSON confidence is outside 0..1", "llama_schema_mismatch")
        for key in ("requirements_met", "requirements_missing", "contradictions"):
            if any(not isinstance(item, str) for item in parsed.get(key, [])):
                raise ProviderError(f"llama.cpp {mode} JSON field {key} contains non-string item", "llama_schema_mismatch")

    def _make_repair_prompt(self, payload: Dict[str, Any], malformed: str, error: ProviderError) -> str:
        contract = (
            LLAMACPP_BATCH_JUDGE_CONTRACT
            if self._payload_is_batch(payload)
            else LLAMACPP_JUDGE_CONTRACT
        )
        return (
            contract
            + "\nRepair the following malformed llama.cpp judge output.\n"
            + f"Validation error: {error.category}: {str(error)[:500]}\n"
            + "Return only the corrected JSON object. Do not explain the repair.\n\n"
            + "Malformed output:\n"
            + str(malformed or "")[:4000]
        )

    @classmethod
    def _extract_final_json_text(cls, raw: str) -> str:
        text = str(raw or "").lstrip("\ufeff").strip()
        if not text:
            return ""
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        return cls._first_balanced_json_object(text)

    @staticmethod
    def _first_balanced_json_object(text: str) -> str:
        start = text.find("{")
        if start < 0:
            return ""
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            ch = text[index]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return ""

    @classmethod
    def _has_complete_json_object(cls, text: str) -> bool:
        return bool(cls._first_balanced_json_object(str(text or "")))

    def _messages_to_prompt(self, messages: Any, strict_json: bool = False, is_batch: bool = False) -> str:
        lines: List[str] = []
        if strict_json:
            lines.append(LLAMACPP_BATCH_JUDGE_CONTRACT if is_batch else LLAMACPP_JUDGE_CONTRACT)
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip().upper()
            content = str(message.get("content") or "").strip()
            if content:
                lines.append(f"{role}:\n{content}")
        lines.append("ASSISTANT JSON:")
        return "\n\n".join(lines)

    def _log_success(self, mode: str, url: str, model: Any, data: Dict[str, Any], content: str, start: float) -> None:
        try:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            finish = "-"
            if isinstance(data.get("choices"), list) and data.get("choices"):
                finish = str((data.get("choices") or [{}])[0].get("finish_reason") or "-")
            else:
                finish = str(data.get("stop_type") or data.get("finish_reason") or "-")
            log(
                "INFO",
                f"[LLAMACPP] mode={mode} endpoint={url} model={model or '-'} "
                f"elapsed_ms={elapsed_ms:.0f} finish={finish} content_excerpt={self._excerpt(content)!r}",
            )
        except Exception:
            pass

    @staticmethod
    def _excerpt(text: str, limit: int = 300) -> str:
        return str(text or "")[:limit].replace("\n", "\\n")

    @staticmethod
    def _looks_like_model_not_found(resp) -> bool:
        try:
            data = resp.json()
        except Exception:
            data = {}
        text = str(data or getattr(resp, "text", "") or "").casefold()
        return "model" in text and any(marker in text for marker in ("not found", "unknown", "does not exist"))

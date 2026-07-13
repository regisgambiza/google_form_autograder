import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from evaluator_config import load_config
from logger import log, update_runtime_state
from openrouter_model_registry import OpenRouterModelRegistry
from provider_types import (
    CircuitState,
    HealthState,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderValidationError,
)
from providers.ollama_provider import OllamaProvider
from providers.openrouter_provider import OpenRouterProvider


@dataclass
class _ProviderState:
    health: HealthState = HealthState.HEALTHY
    circuit: CircuitState = CircuitState.CLOSED
    failures: int = 0
    successes: int = 0
    opened_at: float = 0.0
    last_error: str = ""


@dataclass
class _WorkItem:
    request: ProviderRequest
    provider_name: str
    model: str
    attempt: int
    result_q: "queue.Queue"
    queued_at: float = field(default_factory=time.monotonic)


class ProviderManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._providers = {
            "openrouter": OpenRouterProvider(),
            "ollama": OllamaProvider(),
        }
        self._openrouter_registry = OpenRouterModelRegistry()
        self._states = {name: _ProviderState() for name in self._providers}
        self._queues = {name: queue.Queue(maxsize=self._queue_size()) for name in self._providers}
        self._workers_started = False
        self._worker_status: Dict[str, Dict[str, Any]] = {}
        self._metrics = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "validation_failed": 0,
            "failovers": 0,
            "retries": 0,
            "total_latency_ms": 0.0,
            "started_at": time.time(),
        }
        self._provider_metrics = {
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
            for name in self._providers
        }

    def ask(self, request: ProviderRequest) -> ProviderResponse:
        self._ensure_workers()
        attempts = max(1, int(request.retries if request.retries is not None else load_config().get("provider_retry_count", 2)))
        last_error: Optional[ProviderError] = None
        tried_provider = False
        first_provider = True

        for provider_name in self._provider_order(request):
            if not self._provider_available(provider_name):
                continue
            if not self._provider_accepts_request(provider_name, request):
                continue
            if tried_provider and first_provider is False:
                self._record_failover(provider_name, str(last_error or "provider unavailable"))
            first_provider = False
            models = self._models_for_provider(provider_name, request)
            for model in models:
                for attempt in range(attempts):
                    payload = dict(request.payload)
                    payload["model"] = model
                    item = _WorkItem(
                        request=ProviderRequest(
                            request_id=request.request_id,
                            judge_name=request.judge_name,
                            payload=payload,
                            timeout_s=request.timeout_s,
                            schema=request.schema,
                            model_preferences=request.model_preferences,
                            fallback_models=request.fallback_models,
                            retries=request.retries,
                            metadata=request.metadata,
                        ),
                        provider_name=provider_name,
                        model=model,
                        attempt=attempt,
                        result_q=queue.Queue(maxsize=1),
                    )
                    tried_provider = True
                    try:
                        response = self._submit_and_wait(item)
                        self._record_success(provider_name, response.latency_ms)
                        self._record_model_success(provider_name, model, response.latency_ms, request.judge_name)
                        self._emit_metrics()
                        return response
                    except ProviderError as ex:
                        last_error = ex
                        self._record_failure(provider_name, ex)
                        self._record_model_failure(provider_name, model, ex, request.judge_name)
                        log(
                            "WARNING",
                            f"[PROVIDER] selected={provider_name} request={request.request_id} "
                            f"judge={request.judge_name} model={model} retry={attempt}/{attempts - 1} "
                            f"category={ex.category} error={ex}",
                        )
                        if ex.category == "model_not_found":
                            break
                        if attempt + 1 < attempts:
                            self._record_retry(provider_name)
                        if ex.category in {"rate_limited", "out_of_credits", "disabled"}:
                            break
                if provider_name == "ollama":
                    break

        self._emit_metrics()
        if last_error:
            raise last_error
        raise ProviderError("No configured provider is available", "unavailable")

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            states = {
                name: {
                    "health": state.health.value,
                    "circuit": state.circuit.value,
                    "failures": state.failures,
                    "successes": state.successes,
                    "last_error": state.last_error,
                    "queue_size": self._queues[name].qsize(),
                }
                for name, state in self._states.items()
            }
            workers = {k: dict(v) for k, v in self._worker_status.items()}
            metrics = dict(self._metrics)
            provider_metrics = {k: dict(v) for k, v in self._provider_metrics.items()}
        return {
            "providers": states,
            "workers": workers,
            "metrics": metrics,
            "provider_metrics": provider_metrics,
            "openrouter_models": self._openrouter_registry.snapshot(),
        }

    def _queue_size(self) -> int:
        try:
            return max(1, int(load_config().get("provider_queue_size", 500)))
        except Exception:
            return 500

    def _ensure_workers(self) -> None:
        with self._lock:
            if self._workers_started:
                return
            cfg = load_config()
            self._openrouter_registry.configure_from_config(cfg)
            openrouter_fetcher = getattr(self._providers["openrouter"], "list_free_models", lambda: [])
            self._openrouter_registry.start_background_refresh(
                load_config,
                openrouter_fetcher,
            )
            counts = {
                "openrouter": max(1, int(cfg.get("openrouter_worker_count", 4))),
                "ollama": max(1, int(cfg.get("ollama_worker_count", 1))),
            }
            for provider_name, count in counts.items():
                for i in range(count):
                    self._start_worker(provider_name, i + 1)
            self._workers_started = True

    def _start_worker(self, provider_name: str, index: int) -> None:
        worker_id = f"{provider_name}-{index}"
        self._worker_status[worker_id] = {
            "provider": provider_name,
            "model": "-",
            "status": "idle",
            "request_id": "-",
            "latency_ms": 0.0,
            "queue_wait_ms": 0.0,
        }
        thread = threading.Thread(target=self._worker_loop, args=(worker_id, provider_name), daemon=True)
        thread.start()
        log("INFO", f"[PROVIDER WORKER] id={worker_id} provider={provider_name} status=idle model=- request=- latency_ms=0 queue_wait_ms=0")

    def _worker_loop(self, worker_id: str, provider_name: str) -> None:
        provider = self._providers[provider_name]
        q = self._queues[provider_name]
        while True:
            item: _WorkItem = q.get()
            start = time.perf_counter()
            queue_wait_ms = max(0.0, (time.monotonic() - item.queued_at) * 1000.0)
            self._set_worker_status(worker_id, "running", item.model, item.request.request_id, 0.0, queue_wait_ms)
            try:
                if not provider.is_configured():
                    raise ProviderError(f"{provider_name} is not configured", "disabled")
                raw_response = provider.chat(item.request.payload, item.request.timeout_s)
                parsed = self._validate_response(raw_response, item.request.schema)
                latency_ms = (time.perf_counter() - start) * 1000.0
                usage = raw_response.get("usage") if isinstance(raw_response, dict) else {}
                item.result_q.put(ProviderResponse(
                    request_id=item.request.request_id,
                    provider=provider_name,
                    model=item.model,
                    latency_ms=latency_ms,
                    raw_response=raw_response,
                    parsed_json=parsed,
                    success=True,
                    retry_count=item.attempt,
                    queue_wait_ms=queue_wait_ms,
                    tokens=usage if isinstance(usage, dict) else {},
                ))
                self._set_worker_status(worker_id, "idle", "-", "-", latency_ms, queue_wait_ms)
            except ProviderError as ex:
                item.result_q.put(ex)
                self._set_worker_status(worker_id, "failed", item.model, item.request.request_id, (time.perf_counter() - start) * 1000.0, queue_wait_ms)
            except Exception as ex:
                item.result_q.put(ProviderError(str(ex), "worker_exception"))
                self._set_worker_status(worker_id, "failed", item.model, item.request.request_id, (time.perf_counter() - start) * 1000.0, queue_wait_ms)
            finally:
                q.task_done()

    def _submit_and_wait(self, item: _WorkItem) -> ProviderResponse:
        with self._lock:
            self._metrics["submitted"] += 1
            self._provider_metrics[item.provider_name]["submitted"] += 1
            self._provider_metrics[item.provider_name]["last_model"] = item.model
        log(
            "INFO",
            f"[PROVIDER ROUTE] request={item.request.request_id} judge={item.request.judge_name} "
            f"provider={item.provider_name} model={item.model} qsize={self._queues[item.provider_name].qsize()}",
        )
        update_runtime_state(
            active_model=f"{item.provider_name}:{item.model}",
            active_role=item.request.judge_name,
            active_since=time.time(),
        )
        try:
            self._queues[item.provider_name].put(item, timeout=max(1, item.request.timeout_s))
        except queue.Full as ex:
            raise ProviderError(f"{item.provider_name} queue is full", "queue_full") from ex
        deadline = time.monotonic() + item.request.timeout_s + 15
        while True:
            self._touch_provider_heartbeat(item)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("Provider worker timed out", "timeout")
            try:
                result = item.result_q.get(timeout=min(10.0, remaining))
                break
            except queue.Empty:
                continue
        if isinstance(result, ProviderResponse):
            return result
        if isinstance(result, ProviderError):
            raise result
        raise ProviderError("Provider worker returned an invalid result", "worker_exception")

    def _touch_provider_heartbeat(self, item: _WorkItem) -> None:
        try:
            data = {
                "last_update": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pid": os.getpid(),
                "stage": "provider_request",
                "provider": item.provider_name,
                "model": item.model,
                "request_id": item.request.request_id,
                "judge_name": item.request.judge_name,
                "timestamp_epoch": time.time(),
            }
            with open("heartbeat.json", "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception:
            pass

    def _provider_order(self, request: ProviderRequest) -> List[str]:
        cfg = load_config()
        configured = [str(x).lower() for x in cfg.get("provider_priority", ["openrouter", "ollama"])]
        order = [str(x).lower() for x in (request.metadata.get("provider_priority") or configured)]
        deduped = []
        for provider in order:
            if provider in self._providers and provider not in deduped:
                deduped.append(provider)
        return deduped or ["openrouter", "ollama"]

    def _models_for_provider(self, provider_name: str, request: ProviderRequest) -> List[str]:
        cfg = load_config()
        if provider_name == "ollama":
            model = request.payload.get("model") or (cfg.get("jury_models", {}) or {}).get(request.judge_name)
            return [str(model)] if model else []
        role_models = ((cfg.get("openrouter_models") or {}).get(request.judge_name) or [])
        fallback_models = cfg.get("openrouter_fallback_models") or []
        models = [*request.model_preferences, *role_models, *request.fallback_models, *fallback_models]
        return self._openrouter_registry.order_models(request.judge_name, models, cfg)

    def _provider_accepts_request(self, provider_name: str, request: ProviderRequest) -> bool:
        """Prevent oversized batched prompts from falling back into local Ollama."""
        if provider_name != "ollama":
            return True
        try:
            batch_count = int(request.metadata.get("batch_answer_count", 1) or 1)
        except Exception:
            batch_count = 1
        if batch_count <= 1:
            return True
        cfg = load_config()
        legacy = int(cfg.get("judge_answer_batch_size", 1) or 1)
        ollama_limit = max(1, int(cfg.get("ollama_judge_answer_batch_size", legacy) or legacy))
        if batch_count <= ollama_limit:
            return True
        log(
            "WARNING",
            f"[PROVIDER ROUTE] skip provider=ollama request={request.request_id} "
            f"judge={request.judge_name} batch_answers={batch_count} "
            f"ollama_limit={ollama_limit}; batch will fall back to smaller judge calls",
        )
        return False

    def _provider_available(self, provider_name: str) -> bool:
        with self._lock:
            state = self._states[provider_name]
            if state.health == HealthState.DISABLED:
                return False
            if state.circuit == CircuitState.OPEN:
                recover_after = max(1, int(load_config().get("provider_circuit_recovery_seconds", 60)))
                if time.monotonic() - state.opened_at >= recover_after:
                    state.circuit = CircuitState.HALF_OPEN
                    state.health = HealthState.RECOVERING
                    log("INFO", f"[PROVIDER RECOVERY] provider={provider_name} state=RECOVERING")
                    return True
                return False
            return True

    def _record_success(self, provider_name: str, latency_ms: float) -> None:
        with self._lock:
            state = self._states[provider_name]
            state.successes += 1
            state.failures = 0
            state.last_error = ""
            state.circuit = CircuitState.CLOSED
            state.health = HealthState.HEALTHY
            self._metrics["completed"] += 1
            self._metrics["total_latency_ms"] += latency_ms
            provider_metrics = self._provider_metrics[provider_name]
            provider_metrics["completed"] += 1
            provider_metrics["total_latency_ms"] += latency_ms
            provider_metrics["last_latency_ms"] = latency_ms
            provider_metrics["last_error"] = ""

    def _record_model_success(self, provider_name: str, model: str, latency_ms: float, role: str) -> None:
        if provider_name == "openrouter":
            self._openrouter_registry.record_success(model, latency_ms, role=role)

    def _record_failure(self, provider_name: str, ex: ProviderError) -> None:
        with self._lock:
            state = self._states[provider_name]
            state.failures += 1
            state.last_error = str(ex)
            self._metrics["failed"] += 1
            provider_metrics = self._provider_metrics[provider_name]
            provider_metrics["failed"] += 1
            provider_metrics["last_error"] = str(ex)
            if ex.category == "validation":
                self._metrics["validation_failed"] += 1
                provider_metrics["validation_failed"] += 1
            if ex.category == "model_not_found":
                return
            if ex.category == "rate_limited":
                state.health = HealthState.RATE_LIMITED
            elif ex.category == "out_of_credits":
                state.health = HealthState.OUT_OF_CREDITS
            elif ex.category == "disabled":
                state.health = HealthState.DISABLED
            elif state.failures == 1:
                state.health = HealthState.DEGRADED
            failure_threshold = max(1, int(load_config().get("provider_circuit_failure_threshold", 3)))
            if state.failures >= failure_threshold or ex.category in {"out_of_credits", "disabled"}:
                state.circuit = CircuitState.OPEN
                state.health = HealthState.OFFLINE if ex.category != "disabled" else HealthState.DISABLED
                state.opened_at = time.monotonic()

    def _record_model_failure(self, provider_name: str, model: str, ex: ProviderError, role: str) -> None:
        if provider_name == "openrouter":
            self._openrouter_registry.record_failure(model, ex.category, str(ex), load_config(), role=role)

    def _record_retry(self, provider_name: str) -> None:
        with self._lock:
            self._metrics["retries"] += 1
        log("INFO", f"[PROVIDER RETRY] provider={provider_name} retries={self._metrics['retries']}")

    def _record_failover(self, provider_name: str, reason: str) -> None:
        with self._lock:
            self._metrics["failovers"] += 1
        log("WARNING", f"[PROVIDER FAILOVER] to={provider_name} reason={reason}")

    def _set_worker_status(self, worker_id: str, status: str, model: str, request_id: str, latency_ms: float, queue_wait_ms: float) -> None:
        with self._lock:
            self._worker_status[worker_id] = {
                "provider": self._worker_status.get(worker_id, {}).get("provider", worker_id.split("-", 1)[0]),
                "model": model,
                "status": status,
                "request_id": request_id,
                "latency_ms": latency_ms,
                "queue_wait_ms": queue_wait_ms,
            }
        log(
            "INFO",
            f"[PROVIDER WORKER] id={worker_id} provider={worker_id.split('-', 1)[0]} status={status} "
            f"model={model} request={request_id} latency_ms={latency_ms:.0f} queue_wait_ms={queue_wait_ms:.0f}",
        )

    def _emit_metrics(self) -> None:
        snap = self.snapshot()
        providers = snap["providers"]
        metrics = snap["metrics"]
        provider_metrics = snap["provider_metrics"]
        completed = int(metrics.get("completed", 0))
        avg_ms = (float(metrics.get("total_latency_ms", 0.0)) / completed) if completed else 0.0
        elapsed_min = max(1.0 / 60.0, (time.time() - float(metrics.get("started_at", time.time()))) / 60.0)

        def token(value: object) -> str:
            return str(value or "-").replace(" ", "_")

        parts = [
            f"q_openrouter={providers['openrouter']['queue_size']}",
            f"q_ollama={providers['ollama']['queue_size']}",
            f"openrouter_health={providers['openrouter']['health']}",
            f"openrouter_circuit={providers['openrouter']['circuit']}",
            f"openrouter_done={provider_metrics['openrouter']['completed']}",
            f"openrouter_failed={provider_metrics['openrouter']['failed']}",
            f"openrouter_last_ms={float(provider_metrics['openrouter']['last_latency_ms']):.0f}",
            f"openrouter_last_model={provider_metrics['openrouter']['last_model']}",
            f"openrouter_last_error={token(provider_metrics['openrouter']['last_error'])}",
            f"ollama_health={providers['ollama']['health']}",
            f"ollama_circuit={providers['ollama']['circuit']}",
            f"ollama_done={provider_metrics['ollama']['completed']}",
            f"ollama_failed={provider_metrics['ollama']['failed']}",
            f"ollama_last_ms={float(provider_metrics['ollama']['last_latency_ms']):.0f}",
            f"ollama_last_model={provider_metrics['ollama']['last_model']}",
            f"ollama_last_error={token(provider_metrics['ollama']['last_error'])}",
            f"submitted={metrics['submitted']}",
            f"completed={metrics['completed']}",
            f"failed={metrics['failed']}",
            f"validation_failed={metrics['validation_failed']}",
            f"retries={metrics['retries']}",
            f"failovers={metrics['failovers']}",
            f"rpm={float(metrics['submitted']) / elapsed_min:.2f}",
            f"avg_ms={avg_ms:.0f}",
        ]
        log("INFO", "[PROVIDER METRICS] " + " ".join(parts))

    def _validate_response(self, payload: Dict[str, Any], schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProviderValidationError("transport response is not a JSON object")
        content = (payload.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderValidationError("response message content is empty")
        try:
            parsed = json.loads(content)
        except Exception as ex:
            raise ProviderValidationError(f"message content is not valid JSON: {ex}") from ex
        if schema:
            self._validate_against_required_shape(parsed, schema)
        return parsed

    def _validate_against_required_shape(self, parsed: Any, schema: Dict[str, Any]) -> None:
        required = list(schema.get("required") or [])
        if not isinstance(parsed, dict):
            raise ProviderValidationError("parsed content is not a JSON object")
        for key in required:
            if key not in parsed:
                raise ProviderValidationError(f"parsed content missing required field {key}")
        if "results" in required:
            results = parsed.get("results")
            if not isinstance(results, list):
                raise ProviderValidationError("batch results field is not a list")
            item_schema = (((schema.get("properties") or {}).get("results") or {}).get("items") or {})
            item_required = list(item_schema.get("required") or [])
            for item in results:
                if not isinstance(item, dict):
                    raise ProviderValidationError("batch result item is not an object")
                for key in item_required:
                    if key not in item:
                        raise ProviderValidationError(f"batch result item missing required field {key}")


_MANAGER: Optional[ProviderManager] = None
_MANAGER_LOCK = threading.Lock()


def get_provider_manager() -> ProviderManager:
    global _MANAGER
    if _MANAGER is not None:
        return _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ProviderManager()
    return _MANAGER


def make_request_id(prefix: str = "ai") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"

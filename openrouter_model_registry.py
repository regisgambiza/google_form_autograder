import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from logger import log


@dataclass
class ModelStats:
    model_id: str
    successes: int = 0
    failures: int = 0
    validation_failures: int = 0
    rate_limits: int = 0
    quality_audits: int = 0
    quality_flags: int = 0
    too_strict_flags: int = 0
    too_lenient_flags: int = 0
    alignment_total: float = 0.0
    suspicion_total: float = 0.0
    total_latency_ms: float = 0.0
    last_ok: float = 0.0
    last_failure: float = 0.0
    cooldown_until: float = 0.0
    last_error: str = ""
    roles: set[str] = field(default_factory=set)

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.successes if self.successes else 0.0

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total else 0.5

    @property
    def avg_suspicion(self) -> float:
        return self.suspicion_total / self.quality_audits if self.quality_audits else 0.0

    @property
    def avg_alignment(self) -> float:
        return self.alignment_total / self.quality_audits if self.quality_audits else 1.0


class OpenRouterModelRegistry:
    """Thread-safe scorecard for OpenRouter free models."""

    ROLE_ORDER = {
        "semantic_judge": 0,
        "factual_judge": 1,
        "concept_judge": 2,
        "strict_judge": 3,
        "misconception_judge": 4,
        "language_filter": 5,
    }

    def __init__(self):
        self._lock = threading.RLock()
        self._models: Dict[str, ModelStats] = {}
        self._catalog: List[str] = []
        self._refresh_started = False
        self._last_refresh_error = ""

    def configure_from_config(self, cfg: Dict[str, Any]) -> None:
        candidates: List[str] = []
        role_models = cfg.get("openrouter_models") or {}
        if isinstance(role_models, dict):
            for role, models in role_models.items():
                for model in self._as_list(models):
                    self._remember(model, role=str(role))
                    candidates.append(model)
        for key in ("openrouter_fallback_models", "openrouter_free_model_catalog"):
            for model in self._as_list(cfg.get(key) or []):
                self._remember(model)
                candidates.append(model)
        with self._lock:
            self._catalog = self._dedupe([*self._catalog, *candidates])

    def start_background_refresh(
        self,
        cfg_loader: Callable[[], Dict[str, Any]],
        fetcher: Callable[[], List[str]],
    ) -> None:
        cfg = cfg_loader()
        if not bool(cfg.get("openrouter_model_catalog_refresh_enabled", False)):
            return
        with self._lock:
            if self._refresh_started:
                return
            self._refresh_started = True
        thread = threading.Thread(
            target=self._refresh_loop,
            args=(cfg_loader, fetcher),
            name="openrouter-model-registry",
            daemon=True,
        )
        thread.start()
        log("INFO", "[OPENROUTER MODELS] background catalogue refresh enabled")

    def order_models(self, role: str, preferred: Iterable[str], cfg: Dict[str, Any]) -> List[str]:
        self.configure_from_config(cfg)
        enabled = bool(cfg.get("openrouter_dynamic_model_pool_enabled", True))
        base = self._dedupe(
            model
            for model in (str(model).strip() for model in preferred if str(model).strip())
            if not self._is_blocked(model, cfg)
        )
        if enabled:
            base = self._dedupe([*base, *self._role_rotated_catalog(role, cfg)])
        now = time.monotonic()
        with self._lock:
            for model in base:
                self._models.setdefault(model, ModelStats(model))

            def sort_key(model: str):
                stats = self._models[model]
                available = stats.cooldown_until <= now
                role_fit = role in stats.roles
                # Keep configured role models near the front, but let recent
                # failures and cooldowns push them behind healthier options.
                preferred_rank = base.index(model)
                latency_penalty = min(stats.avg_latency_ms / 10000.0, 3.0)
                failure_penalty = stats.failures + stats.validation_failures + (stats.rate_limits * 2)
                quality_penalty = (
                    stats.avg_suspicion * 4.0
                    + max(0.0, 1.0 - stats.avg_alignment) * 3.0
                    + (stats.quality_flags * 0.5)
                )
                return (
                    0 if available else 1,
                    quality_penalty,
                    0 if role_fit else 1,
                    failure_penalty,
                    latency_penalty,
                    preferred_rank,
                )

            ordered = sorted(base, key=sort_key)
            available = [model for model in ordered if self._models[model].cooldown_until <= now]
            if bool(cfg.get("model_selection_trace_enabled", False)):
                self._write_registry_trace(role, base, ordered, available, now, cfg)
            if available or not bool(cfg.get("openrouter_use_cooling_models_when_all_unavailable", False)):
                return available
        return ordered

    def _write_registry_trace(
        self,
        role: str,
        base: List[str],
        ordered: List[str],
        available: List[str],
        now: float,
        cfg: Dict[str, Any],
    ) -> None:
        try:
            import json
            import os
            path = str(cfg.get("model_selection_trace_path", "logs/model_selection.jsonl"))
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            diagnostics = []
            for index, model in enumerate(ordered):
                stats = self._models[model]
                diagnostics.append({
                    "rank": index,
                    "model": model,
                    "available": stats.cooldown_until <= now,
                    "cooldown_remaining_s": max(0.0, stats.cooldown_until - now),
                    "successes": stats.successes,
                    "failures": stats.failures,
                    "validation_failures": stats.validation_failures,
                    "rate_limits": stats.rate_limits,
                    "avg_latency_ms": stats.avg_latency_ms,
                    "success_rate": stats.success_rate,
                    "quality_audits": stats.quality_audits,
                    "quality_flags": stats.quality_flags,
                    "avg_suspicion": stats.avg_suspicion,
                    "avg_alignment": stats.avg_alignment,
                    "roles": sorted(stats.roles),
                    "last_error": stats.last_error,
                })
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "event": "registry_order",
                "role": role,
                "base_count": len(base),
                "ordered_count": len(ordered),
                "available_count": len(available),
                "base": base,
                "ordered": ordered,
                "available": available,
                "diagnostics": diagnostics,
            }
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
        except Exception:
            pass

    def record_success(self, model: str, latency_ms: float, role: str = "") -> None:
        if not model:
            return
        with self._lock:
            stats = self._models.setdefault(model, ModelStats(model))
            stats.successes += 1
            stats.total_latency_ms += max(0.0, float(latency_ms or 0.0))
            stats.last_ok = time.monotonic()
            stats.cooldown_until = 0.0
            stats.last_error = ""
            if role:
                stats.roles.add(role)

    def record_failure(self, model: str, category: str, error: str, cfg: Dict[str, Any], role: str = "") -> None:
        if not model:
            return
        now = time.monotonic()
        with self._lock:
            stats = self._models.setdefault(model, ModelStats(model))
            stats.failures += 1
            stats.last_failure = now
            stats.last_error = str(error)
            if role:
                stats.roles.add(role)
            if category == "validation":
                stats.validation_failures += 1
            if category == "rate_limited":
                stats.rate_limits += 1
            cooldown = self._cooldown_seconds(category, cfg)
            if cooldown > 0:
                stats.cooldown_until = max(stats.cooldown_until, now + cooldown)
                log(
                    "WARNING",
                    f"[OPENROUTER MODELS] cooldown model={model} category={category} "
                    f"seconds={cooldown}",
                )

    def record_quality_audit(self, model: str, audit: Dict[str, Any], role: str = "") -> None:
        if not model:
            return
        suspicion = max(0.0, min(1.0, float(audit.get("suspicion_score", 0.0) or 0.0)))
        alignment = max(0.0, min(1.0, float(audit.get("alignment_score", 1.0) or 1.0)))
        too_strict = bool(audit.get("too_strict", False))
        too_lenient = bool(audit.get("too_lenient", False))
        reliable = bool(audit.get("reliable", True))
        with self._lock:
            stats = self._models.setdefault(model, ModelStats(model))
            stats.quality_audits += 1
            stats.suspicion_total += suspicion
            stats.alignment_total += alignment
            if not reliable or suspicion >= 0.60 or alignment <= 0.50:
                stats.quality_flags += 1
            if too_strict:
                stats.too_strict_flags += 1
            if too_lenient:
                stats.too_lenient_flags += 1
            if role:
                stats.roles.add(role)

    def snapshot(self) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            models = {
                model: {
                    "successes": stats.successes,
                    "failures": stats.failures,
                    "validation_failures": stats.validation_failures,
                    "rate_limits": stats.rate_limits,
                    "quality_audits": stats.quality_audits,
                    "quality_flags": stats.quality_flags,
                    "too_strict_flags": stats.too_strict_flags,
                    "too_lenient_flags": stats.too_lenient_flags,
                    "avg_suspicion": stats.avg_suspicion,
                    "avg_alignment": stats.avg_alignment,
                    "avg_latency_ms": stats.avg_latency_ms,
                    "success_rate": stats.success_rate,
                    "cooldown_remaining_s": max(0.0, stats.cooldown_until - now),
                    "last_error": stats.last_error,
                    "roles": sorted(stats.roles),
                }
                for model, stats in self._models.items()
            }
            return {
                "catalog_size": len(self._catalog),
                "models": models,
                "last_refresh_error": self._last_refresh_error,
            }

    def snapshot_catalog(self) -> List[str]:
        with self._lock:
            return list(self._catalog)

    def _role_rotated_catalog(self, role: str, cfg: Dict[str, Any]) -> List[str]:
        with self._lock:
            catalog = [
                model
                for model in self._catalog
                if not self._models.get(model, ModelStats(model)).roles
                and not self._is_blocked(model, cfg)
            ]
        if not catalog:
            return []
        offset = self.ROLE_ORDER.get(str(role), 0) % len(catalog)
        return [*catalog[offset:], *catalog[:offset]]

    def _refresh_loop(self, cfg_loader: Callable[[], Dict[str, Any]], fetcher: Callable[[], List[str]]) -> None:
        while True:
            cfg = cfg_loader()
            interval_s = max(300, int(cfg.get("openrouter_model_catalog_refresh_seconds", 3600)))
            try:
                self.configure_from_config(cfg)
                fetched = self._dedupe(fetcher())
                if fetched:
                    with self._lock:
                        self._catalog = self._dedupe([*fetched, *self._catalog])
                        for model in fetched:
                            self._models.setdefault(model, ModelStats(model))
                        self._last_refresh_error = ""
                    log("INFO", f"[OPENROUTER MODELS] refreshed free catalogue models={len(fetched)}")
            except Exception as exc:
                with self._lock:
                    self._last_refresh_error = str(exc)
                log("WARNING", f"[OPENROUTER MODELS] catalogue refresh failed: {exc}")
            time.sleep(interval_s)

    def _remember(self, model: str, role: str = "") -> None:
        model = str(model or "").strip()
        if not model:
            return
        with self._lock:
            stats = self._models.setdefault(model, ModelStats(model))
            if role:
                stats.roles.add(role)

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value:
            return [str(value).strip()]
        return []

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                out.append(text)
                seen.add(text)
        return out

    @staticmethod
    def _is_blocked(model: str, cfg: Dict[str, Any]) -> bool:
        text = str(model or "").strip().casefold()
        if not text:
            return True
        blocked_models = {
            str(item).strip().casefold()
            for item in cfg.get("openrouter_blocked_models", [])
            if str(item).strip()
        }
        if text in blocked_models:
            return True
        blocked_keywords = [
            str(item).strip().casefold()
            for item in cfg.get("openrouter_blocked_model_keywords", [])
            if str(item).strip()
        ]
        return any(keyword in text for keyword in blocked_keywords)

    @staticmethod
    def _cooldown_seconds(category: str, cfg: Dict[str, Any]) -> int:
        if category == "rate_limited":
            return max(30, int(cfg.get("openrouter_model_rate_limit_cooldown_seconds", 300)))
        if category == "out_of_credits":
            return max(300, int(cfg.get("openrouter_model_credit_cooldown_seconds", 3600)))
        if category == "model_not_found":
            return max(3600, int(cfg.get("openrouter_model_not_found_cooldown_seconds", 86400)))
        if category == "validation":
            return max(0, int(cfg.get("openrouter_model_validation_cooldown_seconds", 60)))
        if category in {"timeout", "transport"}:
            return max(0, int(cfg.get("openrouter_model_error_cooldown_seconds", 120)))
        return 0

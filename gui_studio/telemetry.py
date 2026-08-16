# gui_studio/telemetry.py - Structured telemetry for the Studio GUI.
#
# Two data sources, both read-only with respect to the backend:
#   1. Plain diagnostic lines forwarded by GraderThread.debug_message
#      ("[Worker Metrics] ...", "[PROVIDER METRICS] ...", "[HEARTBEAT] ...",
#       "[APP WORKER] ...", "[PROVIDER WORKER] ...") parsed into dicts.
#   2. logs/gui_terminal.jsonl - the structured GUI event transcript written
#      by GraderThread during every run - tailed here to build the live
#      answer feed without touching any backend module.
import json
import os
import re

from PySide6.QtCore import QObject, QTimer, Signal


def parse_kv_payload(payload: str) -> dict:
    """Parse 'k=v k=v ...' telemetry payloads into a dict of raw strings."""
    data = {}
    for token in str(payload or "").split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        data[key] = value
    return data


def _to_int(data, key, default=None):
    try:
        return int(float(data.get(key)))
    except (TypeError, ValueError):
        return default


def _to_float(data, key, default=None):
    try:
        return float(data.get(key))
    except (TypeError, ValueError):
        return default


def parse_worker_metrics(payload: str) -> dict:
    """Parse [Worker Metrics] / [DISPATCH METRICS] payloads."""
    data = parse_kv_payload(payload)
    done = None
    total = None
    if "done=" in data:
        raw = data.get("done", "")
        if "/" in raw:
            try:
                d_s, t_s = raw.split("/", 1)
                done = int(d_s)
                total = int(t_s)
            except (TypeError, ValueError):
                pass
    q_fetch = _to_int(data, "q_fetch")
    return {
        "q_fetch": q_fetch if q_fetch is not None else _to_int(data, "q_det"),
        "pending": _to_int(data, "pending"),
        "q_det": _to_int(data, "q_det"),
        "q_ai": _to_int(data, "q_ai"),
        "q_ai_actual": _to_int(data, "q_ai_actual"),
        "q_result": _to_int(data, "q_result"),
        "done": done,
        "total": total,
    }


def parse_provider_metrics(payload: str) -> dict:
    """Parse [PROVIDER METRICS] payloads (see provider_manager._emit_metrics)."""
    data = parse_kv_payload(payload)
    providers = {}
    for name in ("openrouter", "llamacpp", "ollama"):
        prefix = f"{name}_"
        if f"q_{name}" not in data and f"{prefix}health" not in data:
            continue
        providers[name] = {
            "queue": _to_int(data, f"q_{name}", 0),
            "health": data.get(f"{prefix}health", "-"),
            "circuit": data.get(f"{prefix}circuit", "-"),
            "done": _to_int(data, f"{prefix}done", 0),
            "failed": _to_int(data, f"{prefix}failed", 0),
            "last_ms": _to_int(data, f"{prefix}last_ms", 0),
            "last_model": (data.get(f"{prefix}last_model", "-") or "-").replace("_", " "),
            "last_error": (data.get(f"{prefix}last_error", "-") or "-").replace("_", " "),
        }
    return {
        "providers": providers,
        "retries": _to_int(data, "retries", 0),
        "failovers": _to_int(data, "failovers", 0),
        "rpm": _to_float(data, "rpm", 0.0),
        "avg_ms": _to_int(data, "avg_ms", 0),
        "or_models_total": _to_int(data, "or_models_total", 0),
        "or_models_available": _to_int(data, "or_models_available", 0),
        "or_models_rate_limited": _to_int(data, "or_models_rate_limited", 0),
        "or_models_failed": _to_int(data, "or_models_failed", 0),
        "or_json_failures": _to_int(data, "or_json_failures", 0),
        "or_last_json_failures": _to_int(data, "or_last_json_failures", 0),
        "or_last_success_rate": _to_float(data, "or_last_success_rate", 0.0),
        "or_avg_suspicion": _to_float(data, "or_avg_suspicion", 0.0),
        "or_last_suspicion": _to_float(data, "or_last_suspicion", 0.0),
        "or_max_cooldown_s": _to_float(data, "or_max_cooldown_s", 0.0),
        "or_last_cooldown_s": _to_float(data, "or_last_cooldown_s", 0.0),
        "or_cost_usd": _to_float(data, "or_cost_usd", 0.0),
        "or_selection_reason": (data.get("or_selection_reason", "-") or "-").replace("_", " "),
    }


def parse_app_worker(payload: str) -> dict:
    data = parse_kv_payload(payload)
    return {
        "id": data.get("id", "ai"),
        "status": data.get("status", "idle"),
        "current": data.get("current", "-"),
        "answers": data.get("answers", "0"),
        "latency_ms": data.get("latency_ms", "0"),
        "queue_wait_ms": data.get("queue_wait_ms", "0"),
    }


def parse_provider_worker(payload: str) -> dict:
    data = parse_kv_payload(payload)
    return {
        "id": data.get("id", "-"),
        "provider": data.get("provider", "-"),
        "status": data.get("status", "-"),
        "model": (data.get("model", "-") or "-").replace("_", " "),
        "request": data.get("request", "-"),
        "latency_ms": data.get("latency_ms", "0"),
        "queue_wait_ms": data.get("queue_wait_ms", "0"),
    }


def parse_active_model(message: str):
    """Extract active_model from a [HEARTBEAT] line."""
    match = re.search(r"\bactive_model=([^\s]+)", str(message or ""))
    return match.group(1).strip() if match else None


class JsonlTailer(QObject):
    """Tail a JSONL transcript file and emit structured events.

    GraderThread truncates logs/gui_terminal.jsonl at the start of every run;
    the tailer detects truncation and restarts from offset 0 automatically.
    """

    answer_result = Signal(dict)
    run_start = Signal(dict)
    form_skipped = Signal(dict)
    run_complete = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = ""
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._poll)

    def start(self, path):
        self._path = str(path)
        self._offset = 0
        if not os.path.exists(self._path):
            try:
                os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
                open(self._path, "a", encoding="utf-8").close()
            except OSError:
                pass
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        try:
            size = os.path.getsize(self._path)
        except OSError:
            return
        if size < self._offset:
            self._offset = 0
        if size <= self._offset:
            return
        try:
            with open(self._path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                self._offset = fh.tell()
        except OSError:
            return
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "answer_result":
                self.answer_result.emit(event)
            elif kind == "run_start":
                self.run_start.emit(event)
            elif kind == "form_skipped":
                self.form_skipped.emit(event)
            elif kind == "run_complete":
                self.run_complete.emit(event)

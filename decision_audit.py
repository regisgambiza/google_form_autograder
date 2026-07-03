import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

_LOCK = threading.Lock()


def record_decision(record: Dict[str, object], path: str = "logs/grading_decisions.jsonl") -> None:
    payload = dict(record)
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    for key, value in list(payload.items()):
        if is_dataclass(value):
            payload[key] = asdict(value)
    target = Path(path)
    with _LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")


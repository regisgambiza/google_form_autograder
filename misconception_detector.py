import json
import os
import time
from datetime import datetime, timezone
from typing import Dict

from logger import log


def _write_heartbeat_if_needed():
    """Write heartbeat to file if it exists."""
    try:
        if os.path.exists("heartbeat.json"):
            data = {
                "last_update": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid()
            }
            with open("heartbeat.json", "w") as f:
                json.dump(data, f, indent=2)
    except Exception:
        pass


def detect_misconception(answer: str, rubric: Dict[str, object]) -> Dict[str, object]:
    """Detect misconception from rubric patterns and known hard negatives."""
    # Write heartbeat before operations
    _write_heartbeat_if_needed()
    start = time.perf_counter()
    log("INFO", f"START misconception_detection (answer_len={len(answer)})")
    text = answer.lower()
    misconceptions = [str(x).lower() for x in rubric.get("misconceptions", [])]
    for m in misconceptions:
        m_norm = " ".join(m.split())
        if len(m_norm) >= 6 and m_norm in text:
            duration_ms = (time.perf_counter() - start) * 1000
            log("INFO", f"END misconception_detection duration_ms={duration_ms:.0f} detected={True} description={m}")
            return {"misconception_detected": True, "misconception_description": m}
    if "eat sunlight" in text:
        duration_ms = (time.perf_counter() - start) * 1000
        log("INFO", f"END misconception_detection duration_ms={duration_ms:.0f} detected=True description=Confuses photosynthesis with literal eating")
        return {"misconception_detected": True, "misconception_description": "Confuses photosynthesis with literal eating."}
    duration_ms = (time.perf_counter() - start) * 1000
    log("INFO", f"END misconception_detection duration_ms={duration_ms:.0f} detected=False")
    return {"misconception_detected": False, "misconception_description": ""}

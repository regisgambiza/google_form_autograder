import time
from typing import Dict

from logger import log


def detect_misconception(answer: str, rubric: Dict[str, object]) -> Dict[str, object]:
    """Detect misconception from rubric patterns and known hard negatives."""
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

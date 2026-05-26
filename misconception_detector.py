from typing import Dict


def detect_misconception(answer: str, rubric: Dict[str, object]) -> Dict[str, object]:
    """Detect misconception from rubric patterns and known hard negatives."""
    text = answer.lower()
    misconceptions = [str(x).lower() for x in rubric.get("misconceptions", [])]
    for m in misconceptions:
        if any(tok in text for tok in m.split()[:3]):
            return {"misconception_detected": True, "misconception_description": m}
    if "eat sunlight" in text:
        return {"misconception_detected": True, "misconception_description": "Confuses photosynthesis with literal eating."}
    return {"misconception_detected": False, "misconception_description": ""}

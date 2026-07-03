"""Evaluate grading decisions against teacher-labeled examples."""
import json
from pathlib import Path
from typing import Callable, Dict


def evaluate_benchmark(path: str, evaluator: Callable[[str, list, str], object]) -> Dict[str, object]:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = {"total": 0, "false_positive": 0, "false_negative": 0, "review": 0, "correct": 0}
    for row in rows:
        result = evaluator(str(row["answer"]), list(row["expected"]), str(row["question"]))
        actual, wanted = result.decision, str(row["label"]).upper()
        counts["total"] += 1
        if actual == "REVIEW": counts["review"] += 1
        elif actual == wanted: counts["correct"] += 1
        elif actual == "YES": counts["false_positive"] += 1
        else: counts["false_negative"] += 1
    decided = counts["total"] - counts["review"]
    counts["decided_accuracy"] = counts["correct"] / decided if decided else 0.0
    counts["false_positive_rate"] = counts["false_positive"] / counts["total"] if counts["total"] else 0.0
    return counts

"""Evaluate grading decisions against teacher-labeled examples."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable


_BENCHMARK_LOCK = threading.Lock()


def save_teacher_labels(path: str, examples: Iterable[Dict[str, object]]) -> int:
    """Upsert teacher-labelled examples so later model changes can be measured."""
    target = Path(path)
    with _BENCHMARK_LOCK:
        rows = []
        if target.exists():
            for line in target.read_text(encoding="utf-8").splitlines():
                try:
                    if line.strip():
                        rows.append(json.loads(line))
                except (TypeError, ValueError):
                    continue
        keyed = {
            (str(row.get("question", "")), json.dumps(row.get("expected", []), sort_keys=True), str(row.get("answer", ""))): row
            for row in rows
        }
        changed = 0
        for example in examples:
            row = dict(example)
            key = (str(row.get("question", "")), json.dumps(row.get("expected", []), sort_keys=True), str(row.get("answer", "")))
            previous = keyed.get(key)
            row["label"] = str(row.get("label", "")).upper()
            row["teacher_labeled_at"] = datetime.now(timezone.utc).isoformat()
            if previous != row:
                keyed[key] = row
                changed += 1
        if changed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in keyed.values()),
                encoding="utf-8",
            )
        return changed


def summarize_recorded_decisions(path: str) -> Dict[str, object]:
    """Measure saved model decisions against subsequent teacher labels."""
    target = Path(path)
    counts = {"total": 0, "correct": 0, "false_positive": 0, "false_negative": 0, "review": 0}
    if not target.exists():
        return {**counts, "decided_accuracy": 0.0, "false_positive_rate": 0.0}
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        predicted = str(row.get("model_decision", "REVIEW")).upper()
        wanted = str(row.get("label", "")).upper()
        counts["total"] += 1
        if predicted == "REVIEW":
            counts["review"] += 1
        elif predicted == wanted:
            counts["correct"] += 1
        elif predicted == "YES":
            counts["false_positive"] += 1
        else:
            counts["false_negative"] += 1
    decided = counts["total"] - counts["review"]
    return {
        **counts,
        "decided_accuracy": counts["correct"] / decided if decided else 0.0,
        "false_positive_rate": counts["false_positive"] / counts["total"] if counts["total"] else 0.0,
    }


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

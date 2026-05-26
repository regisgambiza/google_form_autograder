from typing import Dict, List, Optional

from evaluation_pipeline import evaluate_answers as semantic_evaluate_answers


def evaluate_answers(question: Dict[str, object], answers: List[str], expected: Optional[List[str]] = None) -> List[str]:
    """Legacy-compatible evaluator entrypoint returning accepted answers."""
    expected_text = " | ".join(expected) if expected else ""
    qtext = str(question.get("title", "Untitled Question"))
    results = semantic_evaluate_answers(answers, expected_text, qtext)
    return [r.answer for r in results if r.decision == "YES"]

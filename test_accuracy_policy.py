import json

from accuracy_policy import adaptive_math_jury_decision, conservative_jury_decision
from benchmark import evaluate_benchmark
from deterministic_checks import run_deterministic_checks
from ai_judges import _get_judge_format, _make_judge_prompt, _normalize_decision


MODELS = {
    "semantic_judge": "model-a",
    "factual_judge": "model-b",
    "strict_judge": "model-c",
}


def _votes(decision="YES", confidence=0.95):
    return [
        {"role": role, "decision": decision, "confidence": confidence, "reason_short": "test"}
        for role in MODELS
    ]


def test_unanimous_independent_jury_can_accept():
    decision, _, reason, _ = conservative_jury_decision(_votes(), MODELS)
    assert decision == "YES"
    assert reason == "unanimous_independent_jury"


def test_shared_model_cannot_auto_accept():
    models = dict.fromkeys(MODELS, "same-model")
    decision, _, reason, _ = conservative_jury_decision(_votes(), models)
    assert (decision, reason) == ("REVIEW", "judges_not_independent")


def test_missing_or_low_confidence_judge_routes_to_review():
    assert conservative_jury_decision(_votes()[:2], MODELS)[0] == "REVIEW"
    assert conservative_jury_decision(_votes(confidence=0.70), MODELS)[0] == "REVIEW"


def test_any_required_no_blocks_acceptance():
    votes = _votes()
    votes[1]["decision"] = "NO"
    assert conservative_jury_decision(votes, MODELS)[0] == "NO"


def test_units_must_match_for_deterministic_acceptance():
    assert run_deterministic_checks("5 kg", "5 kg").accepted
    assert not run_deterministic_checks("5 m", "5 kg").accepted
    assert not run_deterministic_checks("5", "5 kg").accepted


def test_interval_shape_is_not_proof():
    assert not run_deterministic_checks("1 < x < 4", "(1, 4)").accepted


def test_benchmark_reports_false_positives(tmp_path):
    path = tmp_path / "teacher.jsonl"
    path.write_text(json.dumps({"question": "q", "expected": ["a"], "answer": "bad", "label": "NO"}) + "\n")
    class R: decision = "YES"
    report = evaluate_benchmark(str(path), lambda answer, expected, question: R())
    assert report["false_positive"] == 1
    assert report["false_positive_rate"] == 1.0


def test_judges_are_forced_to_binary_verdicts():
    assert _get_judge_format()["properties"]["decision"]["enum"] == ["YES", "NO"]
    prompt = _make_judge_prompt("question", "expected", "answer", {})
    assert "MUST make a binary decision" in prompt
    assert _normalize_decision({"decision": "ABSTAIN"})["decision"] == "ERROR"


def test_adaptive_math_jury_accepts_two_confident_agreeing_judges():
    judges = _votes()[:2]
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("YES", "two_judge_agreement")


def test_adaptive_math_jury_uses_adjudicator_on_disagreement():
    judges = _votes()
    judges[1]["decision"] = "NO"
    judges[2]["decision"] = "NO"
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("NO", "adjudicator_decision")


def test_adaptive_math_jury_uses_adjudicator_for_low_confidence():
    judges = _votes(confidence=0.70)
    judges[2]["confidence"] = 0.96
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("YES", "adjudicator_decision")


def test_adaptive_math_jury_reviews_if_adjudicator_is_not_confident():
    judges = _votes(confidence=0.70)
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("REVIEW", "adjudicator_low_confidence")

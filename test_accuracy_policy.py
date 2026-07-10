import json

from accuracy_policy import adaptive_math_jury_decision, conservative_jury_decision
from benchmark import evaluate_benchmark, save_teacher_labels, summarize_recorded_decisions
from deterministic_checks import run_deterministic_checks
from domain_validation import DomainValidation, validate_answer_domain
from evaluation_pipeline import _domain_contradiction_can_force_rejection
from ai_judges import _get_judge_format, _make_judge_prompt, _normalize_decision
from rubric_generator import _make_fallback, rubric_format


MODELS = {
    "semantic_judge": "model-a",
    "factual_judge": "model-b",
    "concept_judge": "model-a",
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


def test_teacher_review_builds_persistent_measurable_benchmark(tmp_path):
    path = tmp_path / "teacher.jsonl"
    save_teacher_labels(str(path), [
        {"question": "q", "expected": ["a"], "answer": "bad", "label": "NO", "model_decision": "YES"},
        {"question": "q", "expected": ["a"], "answer": "good", "label": "YES", "model_decision": "YES"},
    ])

    report = summarize_recorded_decisions(str(path))

    assert report["total"] == 2
    assert report["false_positive"] == 1
    assert report["correct"] == 1
    assert report["decided_accuracy"] == 0.5


def test_judges_are_forced_to_binary_verdicts():
    assert _get_judge_format()["properties"]["decision"]["enum"] == ["YES", "NO"]
    prompt = _make_judge_prompt("question", "expected", "answer", {})
    assert "MUST make a binary decision" in prompt
    assert _normalize_decision({"decision": "ABSTAIN"})["decision"] == "ERROR"


def test_question_contract_and_judge_evidence_are_structured():
    contract = _make_fallback("42 cm")
    assert contract["required_result"] == "42 cm"
    assert "required_units" in rubric_format["required"]
    schema = _get_judge_format()
    for field in ("requirements_met", "requirements_missing", "contradictions", "calculation_check"):
        assert field in schema["required"]


def test_adaptive_jury_accepts_three_confident_agreeing_roles():
    judges = _votes()[:3]
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("YES", "primary_unanimous_agreement")


def test_adaptive_math_jury_uses_adjudicator_on_disagreement():
    judges = _votes()
    judges[1]["decision"] = "NO"
    judges[3]["decision"] = "NO"
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("NO", "adjudicator_rejection")


def test_positive_adjudication_requires_teacher_review():
    judges = _votes(confidence=0.70)
    judges[3]["confidence"] = 0.96
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("REVIEW", "adjudicator_positive_requires_teacher_review")


def test_primary_yes_with_missing_requirement_cannot_auto_accept():
    judges = _votes()
    judges[2]["requirements_missing"] = ["required explanation"]
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("REVIEW", "adjudicator_positive_requires_teacher_review")


def test_adaptive_math_jury_reviews_if_adjudicator_is_not_confident():
    judges = _votes(confidence=0.70)
    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)
    assert (decision, reason) == ("REVIEW", "adjudicator_low_confidence")


def test_adaptive_math_jury_rejects_unanimous_primary_no_with_low_confidence_adjudicator():
    judges = [
        {
            "role": "semantic_judge",
            "decision": "NO",
            "confidence": 1.0,
            "reason_short": "contradicts canonical 130",
            "requirements_missing": ["numeric value matches canonical"],
            "contradictions": [],
        },
        {
            "role": "factual_judge",
            "decision": "NO",
            "confidence": 1.0,
            "reason_short": "incorrect numeric value",
            "requirements_missing": ["correct numeric answer"],
            "contradictions": [],
        },
        {
            "role": "concept_judge",
            "decision": "NO",
            "confidence": 1.0,
            "reason_short": "student answer does not match teacher answer",
            "requirements_missing": ["correct estimated sum"],
            "contradictions": ["numeric value 120 contradicts 130"],
        },
        {
            "role": "strict_judge",
            "decision": "NO",
            "confidence": 0.05,
            "reason_short": "contradicts canonical",
        },
    ]

    decision, confidence, reason, _ = adaptive_math_jury_decision(judges, MODELS)

    assert (decision, reason) == ("NO", "primary_unanimous_rejection")
    assert confidence == 1.0


def test_adaptive_math_jury_rejects_clear_unanimous_no_even_with_low_no_confidence():
    judges = [
        {
            "role": "semantic_judge",
            "decision": "NO",
            "confidence": 0.0,
            "reason_short": "incorrect value",
            "requirements_missing": ["correct answer"],
        },
        {
            "role": "factual_judge",
            "decision": "NO",
            "confidence": 0.0,
            "reason_short": "does not match expected answer",
            "requirements_missing": ["matches teacher answer"],
        },
        {
            "role": "concept_judge",
            "decision": "NO",
            "confidence": 0.99,
            "reason_short": "553 is a number, not the operation name",
            "requirements_missing": ["operation name"],
        },
        {
            "role": "strict_judge",
            "decision": "NO",
            "confidence": 0.05,
            "reason_short": "incorrect form",
        },
    ]

    decision, confidence, reason, _ = adaptive_math_jury_decision(judges, MODELS)

    assert (decision, reason) == ("NO", "primary_unanimous_rejection_with_evidence")
    assert confidence == 0.99


def test_adaptive_math_jury_keeps_borderline_low_confidence_no_for_review():
    judges = [
        {"role": "semantic_judge", "decision": "NO", "confidence": 0.0, "reason_short": "insufficient match"},
        {"role": "factual_judge", "decision": "NO", "confidence": 0.0, "reason_short": "does not match expected answer"},
        {"role": "concept_judge", "decision": "NO", "confidence": 0.8, "reason_short": "verb form, not noun form"},
        {"role": "strict_judge", "decision": "NO", "confidence": 0.05, "reason_short": "incorrect form"},
    ]

    decision, _, reason, _ = adaptive_math_jury_decision(judges, MODELS)

    assert (decision, reason) == ("REVIEW", "adjudicator_low_confidence")


def test_symbolic_math_contradiction_does_not_force_rejection_over_jury():
    domain = DomainValidation(
        "CONTRADICTED",
        "mathematics",
        0.99,
        "mathematical contradiction or answer-type mismatch",
        False,
        {"candidate": "a(2a-3)", "canonical": "Since 2a^2 = 2a * a, the factorisation should be a(2a - 3)"},
    )

    assert not _domain_contradiction_can_force_rejection(domain)


def test_hard_numeric_contradiction_can_still_force_rejection():
    domain = DomainValidation(
        "CONTRADICTED",
        "numeric",
        1.0,
        "numeric value contradicts canonical",
        False,
        {"candidate": "120", "canonical": "130"},
    )

    assert _domain_contradiction_can_force_rejection(domain)


def test_missing_teacher_answer_key_is_review_even_for_blank_student_answer():
    domain = validate_answer_domain("", [""], "8 c)")

    assert domain.status == "REVIEW"
    assert domain.domain == "missing_key"

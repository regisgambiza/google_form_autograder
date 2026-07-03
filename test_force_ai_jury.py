import evaluation_pipeline as pipeline


def test_exact_answer_still_reaches_full_ai_jury_when_forced(monkeypatch):
    cfg = {
        "force_ai_jury_for_all_answers": True,
        "numeric_tolerance": 0.000001,
        "max_latency_per_answer_seconds": 60,
        "embedding_thresholds": {"auto_reject": 0.9},
        "patient_ai_mode": True,
        "enable_jury_circuit_breaker": False,
        "jury_semaphore_acquire_timeout_seconds": 60,
        "judge_total_hard_timeout_seconds": 60,
        "retry_attempts": 1,
        "max_concurrent_jury_answers": 1,
        "adaptive_math_jury": {
            "enabled": True,
            "primary_roles": ["semantic_judge", "factual_judge", "concept_judge"],
            "adjudicator_role": "strict_judge",
            "minimum_primary_confidence": 0.9,
        },
        "accuracy_policy": {"minimum_judge_confidence": 0.9, "require_distinct_models": True},
        "jury_models": {
            "strict_judge": "gpt-oss:latest",
            "factual_judge": "gemma3:12b",
            "semantic_judge": "llama3.1:8b",
            "concept_judge": "llama3.1:8b",
        },
        "persist_result_cache": False,
    }
    calls = []
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "get_or_generate_rubric", lambda *_a, **_k: {})
    monkeypatch.setattr(pipeline, "score_concepts", lambda *_a, **_k: {
        "embedding_score": 0.0, "semantic_score": 0.0, "concept_score": 0.0,
        "missing_concepts": [], "accepted_concepts": [],
    })
    monkeypatch.setattr(pipeline, "detect_misconception", lambda *_a, **_k: {
        "misconception_detected": False, "misconception_description": "",
    })
    monkeypatch.setattr(pipeline, "combine_scores", lambda *_a, **_k: 0.99)
    monkeypatch.setattr(pipeline, "record_decision", lambda *_a, **_k: None)

    def judges(*_args, **_kwargs):
        calls.append(True)
        return [
            {"role": "semantic_judge", "decision": "YES", "confidence": 0.99, "reason_short": "correct"},
            {"role": "factual_judge", "decision": "YES", "confidence": 0.99, "reason_short": "verified"},
            {"role": "concept_judge", "decision": "YES", "confidence": 0.99, "reason_short": "complete"},
        ]

    monkeypatch.setattr(pipeline, "run_judges", judges)
    pipeline.RESULT_CACHE.clear()
    pipeline.JURY_SEMAPHORE = None

    result = pipeline.evaluate_answer("6", ["6"], "How many lines of symmetry?")

    assert calls == [True]
    assert result.decision == "YES"
    assert result.stage_reached == "jury"
    assert result.fast_path_used is False

    def mistaken_formatting_rejections(*_args, **_kwargs):
        calls.append(True)
        return [
            {"role": role, "decision": "NO", "confidence": 0.99, "reason_short": "format mismatch"}
            for role in ("semantic_judge", "factual_judge", "concept_judge")
        ]

    monkeypatch.setattr(pipeline, "run_judges", mistaken_formatting_rejections)
    formatted = pipeline.evaluate_answer(
        "50,9 cm", ["50.9 (cm)"],
        "Find the diameter. Give your answer to 1 decimal place.",
    )

    assert formatted.decision == "YES"
    assert formatted.evidence["policy"]["policy_reason"] == "proven_formatting_equivalence"

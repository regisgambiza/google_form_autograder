import evaluation_pipeline as pipeline
from domain_validation import DomainValidation


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

    assert formatted.decision == "NO"

    # Even a faulty deterministic parser claiming PROVEN cannot overturn the jury.
    monkeypatch.setattr(
        pipeline,
        "validate_answer_domain",
        lambda *_a, **_k: DomainValidation(
            "PROVEN", "numeric_range", 1.0,
            "incorrect deterministic proof", True, {"fault_injected": True},
        ),
    )
    pipeline.RESULT_CACHE.clear()
    contradicted = pipeline.evaluate_answer(
        "20", ["m + 20 or 20 + m"], "the cost of a television"
    )

    assert contradicted.decision == "NO"
    assert contradicted.evidence["policy"]["deterministic_evidence_non_authoritative"]["status"] == "PROVEN"


def test_exact_answer_accepts_when_unanimous_ai_reuses_same_provider_model(monkeypatch):
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
            "strict_judge": "openrouter/free",
            "factual_judge": "openrouter/free",
            "semantic_judge": "openrouter/free",
            "concept_judge": "openrouter/free",
        },
        "persist_result_cache": False,
    }
    calls = []
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "combine_scores", lambda *_a, **_k: 0.99)
    monkeypatch.setattr(pipeline, "record_decision", lambda *_a, **_k: None)

    def same_model_yes(*_args, **_kwargs):
        calls.append(True)
        return [
            {
                "role": role,
                "decision": "YES",
                "confidence": confidence,
                "reason_short": "exact match to 36",
                "model": "tencent/hy3:free",
                "provider": "openrouter",
            }
            for role, confidence in (
                ("semantic_judge", 1.0),
                ("factual_judge", 1.0),
                ("concept_judge", 0.99),
            )
        ]

    monkeypatch.setattr(pipeline, "run_judges", same_model_yes)
    pipeline.RESULT_CACHE.clear()
    pipeline.JURY_SEMAPHORE = None

    result = pipeline.evaluate_answer("36", ["36"], "3a")

    assert calls == [True]
    assert result.decision == "YES"
    assert result.stage_reached == "jury"
    assert result.fast_path_used is False
    assert result.evidence["policy"]["policy_reason"] == "domain_exact_confirmed_by_ai"
    assert result.evidence["key_eligible"] is True


def test_forced_ai_rejects_proven_numeric_contradiction_even_with_low_confidence_judge(monkeypatch):
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
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "combine_scores", lambda *_a, **_k: 0.01)
    monkeypatch.setattr(pipeline, "record_decision", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline,
        "validate_answer_domain",
        lambda *_a, **_k: DomainValidation(
            "CONTRADICTED",
            "numeric",
            1.0,
            "numeric value contradicts canonical",
            False,
            {"candidate": "120", "canonical": "130"},
        ),
    )

    def unanimous_no(*_args, **_kwargs):
        return [
            {"role": "semantic_judge", "decision": "NO", "confidence": 0.0, "reason_short": "numeric contradiction"},
            {"role": "factual_judge", "decision": "NO", "confidence": 0.95, "reason_short": "numeric value contradicts canonical"},
            {"role": "concept_judge", "decision": "NO", "confidence": 1.0, "reason_short": "does not match"},
            {"role": "strict_judge", "decision": "NO", "confidence": 0.95, "reason_short": "contradicts canonical"},
        ]

    monkeypatch.setattr(pipeline, "run_judges", unanimous_no)
    pipeline.RESULT_CACHE.clear()
    pipeline.JURY_SEMAPHORE = None

    result = pipeline.evaluate_answer("120", ["130"], "1a")

    assert result.decision == "NO"
    assert result.stage_reached == "jury"
    assert result.confidence == 1.0
    assert result.evidence["policy"]["policy_reason"] == "domain_contradiction_numeric"


def test_unavailable_ai_jury_fails_instead_of_reviewing(monkeypatch):
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
            "strict_judge": "openrouter/free",
            "factual_judge": "openrouter/free",
            "semantic_judge": "openrouter/free",
            "concept_judge": "openrouter/free",
        },
        "persist_result_cache": False,
    }
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "combine_scores", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(pipeline, "record_decision", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline,
        "run_judges",
        lambda *_args, **_kwargs: [
            {"role": "semantic_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "provider unavailable"},
            {"role": "factual_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "provider unavailable"},
            {"role": "concept_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "provider unavailable"},
        ],
    )
    pipeline.RESULT_CACHE.clear()
    pipeline.JURY_SEMAPHORE = None

    result = pipeline.evaluate_answer("36", ["36"], "3a")

    assert result.decision == "ERROR"
    assert result.stage_reached == "jury_unavailable"


def test_incomplete_ai_jury_with_one_valid_vote_fails_instead_of_reviewing(monkeypatch):
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
        "grading_strictness": "lenient",
        "adaptive_math_jury": {
            "enabled": True,
            "primary_roles": ["semantic_judge", "factual_judge", "concept_judge"],
            "adjudicator_role": "strict_judge",
            "minimum_primary_confidence": 0.9,
        },
        "accuracy_policy": {"minimum_judge_confidence": 0.9, "require_distinct_models": False},
        "jury_models": {
            "strict_judge": "qwen3:8b",
            "factual_judge": "qwen3:8b",
            "semantic_judge": "llama3.1:8b",
            "concept_judge": "qwen2.5:7b",
        },
        "persist_result_cache": False,
    }
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "combine_scores", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(pipeline, "record_decision", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline,
        "run_judges",
        lambda *_a, **_k: [
            {
                "role": "semantic_judge",
                "decision": "NO",
                "confidence": 0.99,
                "reason_short": "wrong number",
                "model": "local/model.gguf",
                "provider": "llamacpp",
                "contradictions": ["numeric mismatch"],
            },
            {"role": "factual_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "unavailable"},
            {"role": "concept_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "unavailable"},
            {"role": "strict_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "unavailable"},
        ],
    )
    pipeline.RESULT_CACHE.clear()
    pipeline.JURY_SEMAPHORE = None

    result = pipeline.evaluate_answer("68", ["40"], "a =")

    assert result.decision == "ERROR"
    assert result.evidence["policy"]["policy_reason"] == "incomplete_ai_jury"
    assert result.evidence["policy"]["processing_error"] == "incomplete_ai_jury"


def test_exact_match_with_incomplete_primary_jury_fails_instead_of_accepting(monkeypatch):
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
        "grading_strictness": "lenient",
        "adaptive_math_jury": {
            "enabled": True,
            "primary_roles": ["semantic_judge", "factual_judge", "concept_judge"],
            "adjudicator_role": "strict_judge",
            "minimum_primary_confidence": 0.9,
        },
        "accuracy_policy": {"minimum_judge_confidence": 0.9, "require_distinct_models": False},
        "jury_models": {
            "strict_judge": "local/gemma.gguf",
            "factual_judge": "local/qwen.gguf",
            "semantic_judge": "local/qwen.gguf",
            "concept_judge": "local/gemma.gguf",
        },
        "persist_result_cache": False,
    }
    monkeypatch.setattr(pipeline, "load_config", lambda: cfg)
    monkeypatch.setattr(pipeline, "combine_scores", lambda *_a, **_k: 0.2)
    monkeypatch.setattr(pipeline, "record_decision", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline,
        "run_judges",
        lambda *_a, **_k: [
            {"role": "semantic_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "invalid_response"},
            {"role": "factual_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "invalid_response"},
            {"role": "concept_judge", "decision": "ERROR", "confidence": 0.0, "reason_short": "invalid_response"},
            {
                "role": "strict_judge",
                "decision": "YES",
                "confidence": 1.0,
                "reason_short": "Student answer exactly matches the expected answer.",
                "model": "local/gemma.gguf",
                "provider": "llamacpp",
            },
        ],
    )
    pipeline.RESULT_CACHE.clear()
    pipeline.JURY_SEMAPHORE = None

    result = pipeline.evaluate_answer("D", ["D"], "Question 7: ii)")

    assert result.decision == "ERROR"
    assert result.stage_reached == "jury_unavailable"
    assert result.evidence["policy"]["policy_reason"] == "incomplete_ai_jury"
    assert result.evidence["policy"]["processing_error"] == "incomplete_ai_jury"

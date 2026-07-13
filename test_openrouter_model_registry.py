from openrouter_model_registry import OpenRouterModelRegistry


def test_catalogue_fallback_is_rotated_by_judge_role():
    cfg = {
        "openrouter_dynamic_model_pool_enabled": True,
        "openrouter_use_cooling_models_when_all_unavailable": False,
        "openrouter_model_rate_limit_cooldown_seconds": 300,
        "openrouter_free_model_catalog": ["model-a:free", "model-b:free", "model-c:free", "model-d:free"],
        "openrouter_models": {
            "semantic_judge": ["configured-semantic:free"],
            "factual_judge": ["configured-factual:free"],
            "concept_judge": ["configured-concept:free"],
            "strict_judge": ["configured-strict:free"],
        },
    }
    registry = OpenRouterModelRegistry()
    registry.configure_from_config(cfg)
    for model in (
        "configured-semantic:free",
        "configured-factual:free",
        "configured-concept:free",
        "configured-strict:free",
    ):
        registry.record_failure(model, "rate_limited", "busy", cfg)

    selected = {
        role: registry.order_models(role, cfg["openrouter_models"][role], cfg)[0]
        for role in ("semantic_judge", "factual_judge", "concept_judge", "strict_judge")
    }

    assert selected == {
        "semantic_judge": "model-a:free",
        "factual_judge": "model-b:free",
        "concept_judge": "model-c:free",
        "strict_judge": "model-d:free",
    }


def test_blocked_free_models_are_not_selected():
    cfg = {
        "openrouter_dynamic_model_pool_enabled": True,
        "openrouter_use_cooling_models_when_all_unavailable": False,
        "openrouter_blocked_models": ["cohere/north-mini-code:free"],
        "openrouter_free_model_catalog": [
            "cohere/north-mini-code:free",
            "general/model:free",
        ],
    }
    registry = OpenRouterModelRegistry()
    registry.configure_from_config(cfg)

    selected = registry.order_models("semantic_judge", [], cfg)

    assert "cohere/north-mini-code:free" not in selected
    assert selected[0] == "general/model:free"


def test_ollama_quality_audit_demotes_suspicious_openrouter_model():
    cfg = {
        "openrouter_dynamic_model_pool_enabled": True,
        "openrouter_use_cooling_models_when_all_unavailable": False,
        "openrouter_free_model_catalog": ["configured-semantic:free", "clean-backup:free"],
        "openrouter_models": {
            "semantic_judge": ["configured-semantic:free"],
        },
    }
    registry = OpenRouterModelRegistry()
    registry.configure_from_config(cfg)
    registry.record_success("configured-semantic:free", 1200, role="semantic_judge")
    registry.record_success("clean-backup:free", 1400, role="semantic_judge")
    registry.record_quality_audit(
        "configured-semantic:free",
        {
            "reliable": False,
            "alignment_score": 0.25,
            "suspicion_score": 0.90,
            "too_strict": True,
            "too_lenient": False,
        },
        role="semantic_judge",
    )

    selected = registry.order_models(
        "semantic_judge",
        ["configured-semantic:free", "clean-backup:free"],
        cfg,
    )
    snapshot = registry.snapshot()

    assert selected[0] == "clean-backup:free"
    assert snapshot["models"]["configured-semantic:free"]["quality_flags"] == 1
    assert snapshot["models"]["configured-semantic:free"]["too_strict_flags"] == 1

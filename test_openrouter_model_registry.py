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

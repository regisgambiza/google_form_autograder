import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from providers.llamacpp_provider import LlamaCppProvider
from provider_types import ProviderError


BATCH_FORMAT = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {"type": "object", "properties": {"decision": {"type": "string"}}},
        }
    },
    "required": ["results"],
}

SINGLE_FORMAT = {
    "type": "object",
    "properties": {"decision": {"type": "string", "enum": ["YES", "NO"]}},
}


def _item(index, decision="YES", confidence=1.0):
    return {
        "answer_index": index,
        "decision": decision,
        "confidence": confidence,
        "reason_short": "ok",
        "requirements_met": [],
        "requirements_missing": [],
        "contradictions": [],
        "calculation_check": "not applicable",
    }


def test_payload_is_batch_detects_results_contract():
    assert LlamaCppProvider._payload_is_batch({"format": BATCH_FORMAT})
    assert not LlamaCppProvider._payload_is_batch({"format": SINGLE_FORMAT})
    assert not LlamaCppProvider._payload_is_batch({})


def test_batch_response_passes_contract_validation():
    content = json.dumps({"results": [_item(1), _item(2, "NO", 0.9)]})
    out = LlamaCppProvider()._prepare_generated_content(
        content, "eos", "completion", {"format": BATCH_FORMAT}, 30
    )
    parsed = json.loads(out)
    assert [r["answer_index"] for r in parsed["results"]] == [1, 2]
    assert parsed["results"][1]["decision"] == "NO"


def test_single_shaped_reply_under_batch_format_is_rejected():
    """The pre-fix failure mode: grammar/prompt produced one object instead
    of a results array; it must fail loudly, never pass as valid."""
    content = json.dumps(_item(1))
    with pytest.raises(ProviderError) as exc:
        LlamaCppProvider()._prepare_generated_content(
            content, "eos", "completion", {"format": BATCH_FORMAT}, 30
        )
    assert "results" in str(exc.value)


def test_batch_defaults_are_filled_per_item():
    minimal = {"answer_index": 3, "decision": "yes", "confidence": 1.0, "reason_short": "match"}
    content = json.dumps({"results": [minimal]})
    out = LlamaCppProvider()._prepare_generated_content(
        content, "eos", "completion", {"format": BATCH_FORMAT}, 30
    )
    item = json.loads(out)["results"][0]
    assert item["decision"] == "YES"
    assert item["requirements_met"] == []
    assert item["calculation_check"] == "not applicable"


def test_batch_confidence_bounds_enforced():
    content = json.dumps({"results": [_item(1, confidence=1.5)]})
    with pytest.raises(ProviderError) as exc:
        LlamaCppProvider()._prepare_generated_content(
            content, "eos", "completion", {"format": BATCH_FORMAT}, 30
        )
    assert "confidence" in str(exc.value)


def test_llamacpp_worker_count_respects_config_and_server_slots(monkeypatch):
    import evaluator_config

    base = {"llamacpp_enabled": True}
    monkeypatch.setattr(evaluator_config, "configured_provider_names", lambda cfg: ["llamacpp"])

    cfg = dict(base, llamacpp_worker_count=2, llamacpp_server_parallel=2)
    counts = evaluator_config.effective_provider_worker_counts(cfg)
    assert counts["llamacpp"] == 2

    # More workers than server slots are capped to keep GPU queue sane.
    cfg = dict(base, llamacpp_worker_count=6, llamacpp_server_parallel=2)
    counts = evaluator_config.effective_provider_worker_counts(cfg)
    assert counts["llamacpp"] == 4  # slots * 2 cap

    # Default stays a single safe worker.
    cfg = dict(base)
    counts = evaluator_config.effective_provider_worker_counts(cfg)
    assert counts["llamacpp"] == 1

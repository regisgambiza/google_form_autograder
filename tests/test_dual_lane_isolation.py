# tests/test_dual_lane_isolation.py - Dual Lane failure-domain and settings
# isolation regression tests.
#
# Contract under test:
#   * Each lane routes to its own provider first, failing over to the other.
#   * A failure on OpenRouter never stops or poisons the Local Llama lane
#     (and vice versa); restricted models/rate limits only affect their lane.
#   * A congested lane reroutes quickly instead of blocking the caller for the
#     whole judge timeout.
#   * dual_lane settings live in their own namespace and fall back to legacy
#     keys, so modes cannot clobber each other's configuration.
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import provider_manager as pm_module  # noqa: E402
from evaluator_config import (  # noqa: E402
    dual_lane_role_models,
    dual_lane_settings,
    effective_lane_workers,
)
from provider_manager import ProviderManager  # noqa: E402
from provider_types import ProviderError  # noqa: E402

OK_CONTENT = '{"decision": "YES", "confidence": 0.99}'


class FakeProvider:
    """Configurable fake provider for ProviderManager worker pools."""

    def __init__(self, behavior=None):
        self.calls = 0
        self._lock = threading.Lock()
        self._behavior = behavior or "ok"          # ok | rate_limited | disabled | hang
        self._release = threading.Event()          # used by 'hang'
        self.failures_logged = 0

    def set_behavior(self, behavior):
        with self._lock:
            self._behavior = behavior

    def calls_count(self):
        with self._lock:
            return self.calls

    def is_configured(self):
        return self._behavior != "disabled"

    def chat(self, payload, timeout_s):
        with self._lock:
            self.calls += 1
            behavior = self._behavior
        if behavior == "rate_limited":
            raise ProviderError("model restricted / rate limited", "rate_limited")
        if behavior == "hard_down":
            raise ProviderError("connection refused", "connection")
        if behavior == "hang":
            self._release.wait(timeout=30)
            raise ProviderError("hung call released", "timeout")
        return {"message": {"content": OK_CONTENT}}


BASE_CFG = {
    "provider_strategy": "dual_lane",
    "provider_priority": ["openrouter", "llamacpp"],
    "provider_retry_count": 1,
    "provider_timeout_seconds": 20,
    "provider_queue_size": 50,
    "provider_circuit_failure_threshold": 3,
    "provider_circuit_recovery_seconds": 60,
    "provider_pickup_timeout_seconds": 45,
    "openrouter_worker_count": 1,
    "llamacpp_worker_count": 1,
    "openrouter_ai_worker_count": 1,
    "llamacpp_ai_worker_count": 1,
    "openrouter_models": {"semantic_judge": ["fake/or-model"]},
    "llamacpp_models": {"semantic_judge": ["fake/local-model"]},
}


@pytest.fixture()
def dual_manager(monkeypatch):
    """A fresh ProviderManager whose two lanes are fake providers."""
    cfg = dict(BASE_CFG)

    def _fake_load_config():
        return dict(cfg)

    monkeypatch.setattr(pm_module, "load_config", _fake_load_config)

    mgr = ProviderManager()
    openrouter = FakeProvider(behavior="ok")
    llamacpp = FakeProvider(behavior="ok")
    mgr._providers["openrouter"] = openrouter
    mgr._providers["llamacpp"] = llamacpp
    mgr._states["openrouter"] = pm_module._ProviderState()
    mgr._states["llamacpp"] = pm_module._ProviderState()

    def make_request(provider_priority):
        from provider_types import ProviderRequest

        return ProviderRequest(
            request_id="req-test",
            judge_name="semantic_judge",
            payload={"prompt": "p"},
            timeout_s=10,
            schema=None,
            metadata={"provider_priority": list(provider_priority)},
        )

    return mgr, openrouter, llamacpp, make_request


def test_both_healthy_lanes_both_receive_work(dual_manager):
    """Test 1 + 6: both lanes actively process jobs."""
    mgr, openrouter, llamacpp, make_request = dual_manager
    # Interleave requests so both single-worker pools get traffic even under
    # race conditions: alternate hint priority per request.
    for i in range(6):
        priority = ["openrouter", "llamacpp"] if i % 2 == 0 else ["llamacpp", "openrouter"]
        resp = mgr.ask(make_request(priority))
        assert resp.success is True
    assert openrouter.calls_count() > 0, "OpenRouter lane never processed a job"
    assert llamacpp.calls_count() > 0, "Local Llama lane stayed idle while healthy"


def test_openrouter_failure_falls_back_to_local_lane(dual_manager):
    """Test 2: restricted/rate-limited OpenRouter -> Local Llama continues."""
    mgr, openrouter, llamacpp, make_request = dual_manager
    openrouter.set_behavior("rate_limited")

    resp = mgr.ask(make_request(["openrouter", "llamacpp"]))

    assert resp.success is True
    assert resp.provider == "llamacpp", "grading did not continue on the local lane"
    # The OpenRouter failure was recorded on ITS OWN lane only.
    assert mgr._states["openrouter"].failures >= 1
    assert mgr._states["llamacpp"].failures == 0
    assert mgr._states["llamacpp"].circuit.value.upper() == "CLOSED"
    # Subsequent request goes straight to the healthy lane again.
    resp2 = mgr.ask(make_request(["openrouter", "llamacpp"]))
    assert resp2.provider == "llamacpp"


def test_local_lane_failure_falls_back_to_openrouter(dual_manager):
    """Test 4: Local Llama down -> OpenRouter continues independently."""
    mgr, openrouter, llamacpp, make_request = dual_manager
    llamacpp.set_behavior("hard_down")

    resp = mgr.ask(make_request(["llamacpp", "openrouter"]))

    assert resp.success is True
    assert resp.provider == "openrouter"
    assert mgr._states["llamacpp"].failures >= 1
    assert mgr._states["openrouter"].failures == 0


def test_both_lanes_fail_reports_and_raises(dual_manager):
    """Test 5: both fail -> error raised after trying both; each lane attempted."""
    mgr, openrouter, llamacpp, make_request = dual_manager
    openrouter.set_behavior("rate_limited")
    llamacpp.set_behavior("hard_down")

    before_or = openrouter.calls_count()
    before_ll = llamacpp.calls_count()
    with pytest.raises(ProviderError):
        mgr.ask(make_request(["openrouter", "llamacpp"]))
    assert openrouter.calls_count() == before_or + 1
    assert llamacpp.calls_count() == before_ll + 1


def test_congested_lane_reroutes_without_poisoning_it(dual_manager, monkeypatch):
    """Pickup-timeout rerouting: a backed-up lane is bypassed fast and its
    circuit/cool-downs stay untouched (temporary congestion != lane death)."""
    mgr, openrouter, llamacpp, make_request = dual_manager
    cfg = dict(BASE_CFG)
    cfg["provider_pickup_timeout_seconds"] = 0.5
    monkeypatch.setattr(pm_module, "load_config", lambda: dict(cfg))

    # Occupy the single llamacpp worker with a long call, so the next item
    # cannot be picked up within the tiny pickup window.
    llamacpp.set_behavior("hang")

    def _blocker():
        try:
            mgr.ask(make_request(["llamacpp"]))
        except Exception:
            pass

    blocker = threading.Thread(target=_blocker, daemon=True)
    blocker.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        if mgr._queues["llamacpp"].qsize() == 0 and llamacpp.calls_count() >= 1:
            break
        time.sleep(0.05)

    llamacpp.set_behavior("ok")  # would succeed if it were ever picked up
    resp = mgr.ask(make_request(["llamacpp", "openrouter"]))

    assert resp.provider == "openrouter", "congested lane was not rerouted"
    # lane_busy must NOT count as a provider failure (no circuit poisoning).
    assert mgr._states["llamacpp"].failures == 0
    assert mgr._states["llamacpp"].circuit.value.upper() == "CLOSED"
    llamacpp._release.set()


# ---------------------------------------------------------------------------
# Settings isolation
# ---------------------------------------------------------------------------

def test_dual_lane_worker_counts_prefer_namespaced_settings():
    cfg = {
        "provider_strategy": "dual_lane",
        "openrouter_ai_worker_count": 10,
        "llamacpp_ai_worker_count": 1,
        "dual_lane": {"openrouter_ai_worker_count": 3, "llamacpp_ai_worker_count": 2},
    }
    lanes = effective_lane_workers(cfg)
    assert lanes == {"openrouter": 3, "llamacpp": 2}


def test_dual_lane_worker_counts_fall_back_to_legacy_keys():
    cfg = {
        "provider_strategy": "dual_lane",
        "openrouter_ai_worker_count": 7,
        "ai_worker_count": 5,
    }
    lanes = effective_lane_workers(cfg)
    assert lanes == {"openrouter": 7, "llamacpp": 1}
    assert dual_lane_settings(cfg) == {}


def test_dual_lane_empty_namespace_falls_back_to_legacy_keys():
    cfg = {"provider_strategy": "dual_lane", "dual_lane": {}, "openrouter_ai_worker_count": 4}
    assert effective_lane_workers(cfg)["openrouter"] == 4


def test_dual_lane_model_overrides_are_namespaced():
    cfg = {
        "provider_strategy": "dual_lane",
        "openrouter_models": {"semantic_judge": ["shared/or-model"]},
        "llamacpp_models": {"semantic_judge": ["shared/local.gguf"]},
        "dual_lane": {
            "openrouter_models": {"semantic_judge": ["dual/or-model"]},
            "llamacpp_models": {"semantic_judge": ["dual/local.gguf"]},
        },
    }
    assert dual_lane_role_models(cfg, "openrouter", "semantic_judge") == ["dual/or-model"]
    assert dual_lane_role_models(cfg, "llamacpp", "semantic_judge") == ["dual/local.gguf"]
    # Shared single-mode keys untouched by the presence of dual-lane overrides.
    assert cfg["openrouter_models"]["semantic_judge"] == ["shared/or-model"]
    assert cfg["llamacpp_models"]["semantic_judge"] == ["shared/local.gguf"]
    # No override configured -> empty list (caller falls back to shared keys).
    assert dual_lane_role_models({"provider_strategy": "dual_lane"}, "openrouter", "strict_judge") == []


def test_non_dual_lane_strategies_ignore_dual_lane_namespace_counts():
    cfg = {
        "provider_strategy": "llamacpp_only",
        "llamacpp_ai_worker_count": 1,
        "dual_lane": {"llamacpp_ai_worker_count": 2},
    }
    assert effective_lane_workers(cfg) == {}  # not dual lane -> no lane split

"""Conservative three-way grading policy.

Similarity is useful for routing, but never constitutes proof of correctness.
"""
from typing import Dict, List, Sequence, Tuple


REQUIRED_ACCEPT_ROLES = ("semantic_judge", "factual_judge", "concept_judge", "strict_judge")
STRONG_REJECTION_CONFIDENCE = 0.90


STRICTNESS_PROFILES: Dict[str, Dict[str, object]] = {
    "strict": {
        "minimum_judge_confidence": 0.95,
        "require_distinct_models": True,
        "review_unanimous_yes_when_not_independent": True,
        "soften_rejections": False,
    },
    "balanced": {
        "minimum_judge_confidence": 0.90,
        "require_distinct_models": False,
        "accept_unanimous_yes": True,
        "soften_rejections": False,
    },
    "lenient": {
        "minimum_judge_confidence": 0.82,
        "require_distinct_models": False,
        "accept_unanimous_yes": True,
        "accept_yes_majority": True,
        "soften_rejections": True,
    },
    "review-heavy": {
        "minimum_judge_confidence": 0.95,
        "require_distinct_models": True,
        "accept_unanimous_yes": False,
        "soften_rejections": True,
    },
    "practice": {
        "minimum_judge_confidence": 0.75,
        "require_distinct_models": False,
        "accept_unanimous_yes": True,
        "accept_yes_majority": True,
        "soften_rejections": True,
    },
}


def strictness_profile(mode: str) -> Dict[str, object]:
    key = str(mode or "balanced").strip().casefold().replace("_", "-")
    aliases = {
        "extreme": "strict",
        "conservative": "strict",
        "normal": "balanced",
        "review": "review-heavy",
        "review heavy": "review-heavy",
    }
    key = aliases.get(key, key)
    profile = dict(STRICTNESS_PROFILES.get(key, STRICTNESS_PROFILES["balanced"]))
    profile["mode"] = key if key in STRICTNESS_PROFILES else "balanced"
    return profile


def judge_role(result: Dict[str, object]) -> str:
    return str(result.get("role", result.get("judge_role", ""))).strip()


def _role_models_are_independent(roles: Sequence[str], jury_models: Dict[str, str]) -> bool:
    """Allow only the intentional blind Llama semantic/completeness role reuse."""
    groups: Dict[str, List[str]] = {}
    for role in roles:
        groups.setdefault(str(jury_models.get(role, "")).strip().casefold(), []).append(role)
    if "" in groups:
        return False
    return all(
        len(group_roles) == 1 or set(group_roles) <= {"semantic_judge", "concept_judge"}
        for group_roles in groups.values()
    )


def _effective_role_models(roles: Sequence[str], by_role: Dict[str, Dict[str, object]], jury_models: Dict[str, str]) -> Dict[str, str]:
    """Prefer actual provider model names over configured role defaults."""
    return {
        role: str((by_role.get(role) or {}).get("model") or jury_models.get(role, "")).strip()
        for role in roles
    }


def _evidence_model_label(role: str, judge: Dict[str, object], jury_models: Dict[str, str]) -> str:
    """Avoid displaying configured legacy model hints for unavailable provider-managed judges."""
    model = str(judge.get("model") or "").strip()
    if model:
        return model
    if str(judge.get("decision", "ERROR")).upper() == "ERROR":
        return f"provider-managed:{role}"
    return str(jury_models.get(role, "")).strip()


def _has_minimum_model_diversity(roles: Sequence[str], by_role: Dict[str, Dict[str, object]], jury_models: Dict[str, str], minimum: int = 2) -> bool:
    models = {
        model.casefold()
        for model in _effective_role_models(roles, by_role, jury_models).values()
        if model
    }
    return len(models) >= minimum


def _has_strong_rejection_evidence(judge: Dict[str, object], min_confidence: float = STRONG_REJECTION_CONFIDENCE) -> bool:
    """Treat low numeric confidence on a NO as usable only with clear evidence."""
    if str(judge.get("decision", "")).upper() != "NO":
        return False
    confidence = float(judge.get("confidence", 0.0) or 0.0)
    if confidence < min_confidence:
        return False
    if judge.get("requirements_missing") or judge.get("contradictions"):
        return True
    reason = str(judge.get("reason_short", judge.get("reason", ""))).casefold()
    return any(
        marker in reason
        for marker in (
            "contradict",
            "incorrect",
            "does not match",
            "not match",
            "different",
            "not equivalent",
            "wrong",
        )
    )


def conservative_jury_decision(
    judges: Sequence[Dict[str, object]],
    jury_models: Dict[str, str],
    min_confidence: float = 0.90,
    required_roles: Sequence[str] = REQUIRED_ACCEPT_ROLES,
    require_distinct_models: bool = True,
) -> Tuple[str, float, str, Dict[str, object]]:
    """Return YES only for complete, confident, independent unanimous evidence."""
    by_role = {judge_role(j): j for j in judges if judge_role(j)}
    evidence: Dict[str, object] = {
        "required_roles": list(required_roles),
        "received_roles": sorted(by_role),
        "judge_models": {role: jury_models.get(role, "") for role in required_roles},
        "judge_decisions": {
            role: {
                "decision": str(j.get("decision", "ERROR")).upper(),
                "confidence": float(j.get("confidence", 0.0) or 0.0),
                "reason": str(j.get("reason_short", "")),
                "model": _evidence_model_label(role, j, jury_models),
                "provider": str(j.get("provider", "")),
                "provider_latency_ms": float(j.get("provider_latency_ms", 0.0) or 0.0),
                "provider_queue_wait_ms": float(j.get("provider_queue_wait_ms", 0.0) or 0.0),
                "provider_retry_count": int(j.get("provider_retry_count", 0) or 0),
                "requirements_met": list(j.get("requirements_met", []) or []),
                "requirements_missing": list(j.get("requirements_missing", []) or []),
                "contradictions": list(j.get("contradictions", []) or []),
                "calculation_check": str(j.get("calculation_check", "")),
            }
            for role, j in by_role.items()
        },
    }
    missing = [role for role in required_roles if role not in by_role]
    if missing:
        evidence["missing_roles"] = missing
        return "REVIEW", 0.0, "missing_required_judges", evidence

    selected = [by_role[role] for role in required_roles]
    decisions = [str(j.get("decision", "ERROR")).upper() for j in selected]
    confidences = [float(j.get("confidence", 0.0) or 0.0) for j in selected]
    if "NO" in decisions:
        return "NO", min(confidences), "material_contradiction_or_incorrect", evidence
    if any(d != "YES" for d in decisions):
        return "REVIEW", min(confidences), "judge_unavailable_or_invalid", evidence
    if min(confidences) < min_confidence:
        return "REVIEW", min(confidences), "insufficient_judge_confidence", evidence

    effective_models = _effective_role_models(required_roles, by_role, jury_models)
    if require_distinct_models and not _role_models_are_independent(required_roles, effective_models):
        evidence["independence_failure"] = "required roles share a model"
        return "REVIEW", min(confidences), "judges_not_independent", evidence
    return "YES", min(confidences), "unanimous_independent_jury", evidence


def adaptive_math_jury_decision(
    judges: Sequence[Dict[str, object]],
    jury_models: Dict[str, str],
    min_confidence: float = 0.90,
    primary_roles: Sequence[str] = ("semantic_judge", "factual_judge", "concept_judge"),
    adjudicator_role: str = "strict_judge",
    require_distinct_models: bool = True,
) -> Tuple[str, float, str, Dict[str, object]]:
    """Two independent judges decide easy cases; a third resolves uncertainty."""
    by_role = {judge_role(j): j for j in judges if judge_role(j)}
    evidence: Dict[str, object] = {
        "mode": "adaptive_math_jury",
        "primary_roles": list(primary_roles),
        "adjudicator_role": adjudicator_role,
        "judge_models": {role: jury_models.get(role, "") for role in list(primary_roles) + [adjudicator_role]},
        "judge_decisions": {
            role: {
                "decision": str(j.get("decision", "ERROR")).upper(),
                "confidence": float(j.get("confidence", 0.0) or 0.0),
                "reason": str(j.get("reason_short", "")),
                "model": _evidence_model_label(role, j, jury_models),
                "provider": str(j.get("provider", "")),
                "provider_latency_ms": float(j.get("provider_latency_ms", 0.0) or 0.0),
                "provider_queue_wait_ms": float(j.get("provider_queue_wait_ms", 0.0) or 0.0),
                "provider_retry_count": int(j.get("provider_retry_count", 0) or 0),
                "requirements_met": list(j.get("requirements_met", []) or []),
                "requirements_missing": list(j.get("requirements_missing", []) or []),
                "contradictions": list(j.get("contradictions", []) or []),
                "calculation_check": str(j.get("calculation_check", "")),
            }
            for role, j in by_role.items()
        },
    }
    primary = [by_role.get(role) for role in primary_roles]
    primary_valid = all(j and str(j.get("decision", "")).upper() in {"YES", "NO"} for j in primary)
    if primary_valid:
        decisions = [str(j.get("decision")).upper() for j in primary]
        confidences = [float(j.get("confidence", 0.0) or 0.0) for j in primary]
        evidence_clean = all(not j.get("requirements_missing") and not j.get("contradictions") for j in primary)
        # Roles may share one physical model when they use blind, specialized
        # prompts. Still require at least two distinct primary model families.
        effective_primary_models = _effective_role_models(primary_roles, by_role, jury_models)
        independent = (
            _role_models_are_independent(primary_roles, effective_primary_models)
            or _has_minimum_model_diversity(primary_roles, by_role, jury_models, minimum=2)
        )
        if len(set(decisions)) == 1 and decisions[0] == "NO" and min(confidences) >= min_confidence:
            return "NO", min(confidences), "primary_unanimous_rejection", evidence
        if len(set(decisions)) == 1 and decisions[0] == "NO" and any(_has_strong_rejection_evidence(j) for j in primary):
            evidence["low_confidence_no_votes"] = [
                {
                    "role": judge_role(j),
                    "confidence": float(j.get("confidence", 0.0) or 0.0),
                    "reason": str(j.get("reason_short", j.get("reason", ""))),
                }
                for j in primary
                if float(j.get("confidence", 0.0) or 0.0) < min_confidence
            ]
            return "NO", max(confidences), "primary_unanimous_rejection_with_evidence", evidence
        if evidence_clean and len(set(decisions)) == 1 and min(confidences) >= min_confidence and (independent or not require_distinct_models):
            return decisions[0], min(confidences), "primary_unanimous_agreement", evidence

    adjudicator = by_role.get(adjudicator_role)
    if not adjudicator or str(adjudicator.get("decision", "")).upper() not in {"YES", "NO"}:
        return "REVIEW", 0.0, "adjudicator_unavailable", evidence
    adjudicator_conf = float(adjudicator.get("confidence", 0.0) or 0.0)
    if adjudicator_conf < min_confidence:
        return "REVIEW", adjudicator_conf, "adjudicator_low_confidence", evidence
    if require_distinct_models:
        effective_primary_models = _effective_role_models(primary_roles, by_role, jury_models)
        primary_models = {str(model).casefold() for model in effective_primary_models.values() if model}
        adjudicator_model = str((adjudicator or {}).get("model") or jury_models.get(adjudicator_role, "")).casefold()
        primary_independent = (
            _role_models_are_independent(primary_roles, effective_primary_models)
            or _has_minimum_model_diversity(primary_roles, by_role, jury_models, minimum=2)
        )
        if not primary_independent or adjudicator_model in primary_models:
            return "REVIEW", adjudicator_conf, "judges_not_independent", evidence
    decision = str(adjudicator.get("decision")).upper()
    if decision == "YES":
        yes_primary = [
            j for j in primary
            if j and str(j.get("decision", "")).upper() == "YES"
            and float(j.get("confidence", 0.0) or 0.0) >= min_confidence
        ]
        if len(yes_primary) >= 2:
            evidence["positive_adjudication_supported_by_primary_yes"] = [
                judge_role(j) for j in yes_primary
            ]
            return "YES", min(adjudicator_conf, min(float(j.get("confidence", 0.0) or 0.0) for j in yes_primary)), "adjudicator_supported_majority_acceptance", evidence
        # A lonely/low-confidence positive is useful evidence, but it is not
        # enough to auto-accept.
        return "REVIEW", adjudicator_conf, "adjudicator_positive_requires_teacher_review", evidence
    return "NO", adjudicator_conf, "adjudicator_rejection", evidence

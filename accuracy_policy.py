"""Conservative three-way grading policy.

Similarity is useful for routing, but never constitutes proof of correctness.
"""
from typing import Dict, List, Sequence, Tuple


REQUIRED_ACCEPT_ROLES = ("semantic_judge", "factual_judge", "concept_judge", "strict_judge")


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
                "model": str(j.get("model", jury_models.get(role, ""))),
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

    if require_distinct_models and not _role_models_are_independent(required_roles, jury_models):
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
                "model": str(j.get("model", jury_models.get(role, ""))),
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
        models = [str(jury_models.get(role, "")).casefold() for role in primary_roles]
        evidence_clean = all(not j.get("requirements_missing") and not j.get("contradictions") for j in primary)
        # Roles may share one physical model when they use blind, specialized
        # prompts. Still require at least two distinct primary model families.
        independent = _role_models_are_independent(primary_roles, jury_models)
        if evidence_clean and len(set(decisions)) == 1 and min(confidences) >= min_confidence and (independent or not require_distinct_models):
            return decisions[0], min(confidences), "primary_unanimous_agreement", evidence

    adjudicator = by_role.get(adjudicator_role)
    if not adjudicator or str(adjudicator.get("decision", "")).upper() not in {"YES", "NO"}:
        return "REVIEW", 0.0, "adjudicator_unavailable", evidence
    adjudicator_conf = float(adjudicator.get("confidence", 0.0) or 0.0)
    if adjudicator_conf < min_confidence:
        return "REVIEW", adjudicator_conf, "adjudicator_low_confidence", evidence
    if require_distinct_models:
        primary_models = {str(jury_models.get(role, "")).casefold() for role in primary_roles}
        adjudicator_model = str(jury_models.get(adjudicator_role, "")).casefold()
        if not _role_models_are_independent(primary_roles, jury_models) or adjudicator_model in primary_models:
            return "REVIEW", adjudicator_conf, "judges_not_independent", evidence
    decision = str(adjudicator.get("decision")).upper()
    if decision == "YES":
        # A disputed/low-confidence positive is useful evidence, but it is not
        # independent primary agreement and therefore requires teacher review.
        return "REVIEW", adjudicator_conf, "adjudicator_positive_requires_teacher_review", evidence
    return "NO", adjudicator_conf, "adjudicator_rejection", evidence

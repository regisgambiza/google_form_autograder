"""Question-aware deterministic validation before semantic AI judging."""
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from fractions import Fraction
from typing import Dict, List, Optional, Sequence

from sympy import simplify, sympify

from normalization import normalize


@dataclass(frozen=True)
class DomainValidation:
    status: str  # PROVEN, CONTRADICTED, SEMANTIC, REVIEW
    domain: str
    confidence: float
    reason: str
    key_eligible: bool
    evidence: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


_MATH_CHARS = re.compile(r"^[0-9A-Za-z\s.+\-*/^=(),%°$]+$")
_MATH_HINT = re.compile(
    r"\b(calculate|evaluate|simplif|expand|factor|factoris|solve|equation|expression|"
    r"perimeter|area|volume|angle|fraction|percentage|percent|mean|median|ratio|"
    r"integer|decimal|algebra|find\s+[a-z])\b",
    re.I,
)
_LIST_HINT = re.compile(r"\b(list|name|state|give|identify)\s+(?:the\s+)?(?:two|three|four|\d+)", re.I)
_DATE_HINT = re.compile(r"\b(date|day|month|year)\b", re.I)
_UNITS = ("kg", "km", "cm", "mm", "mph", "kph", "°c", "°f", "degrees", "%", "$", "m", "g", "s", "h")


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def _numeric(value: str) -> Optional[Fraction]:
    text = _clean(value).replace(",", "")
    percent = text.endswith("%")
    if percent:
        text = text[:-1]
    try:
        result = Fraction(text)
        return result / 100 if percent else result
    except Exception:
        try:
            result = Fraction(str(float(text)))
            return result / 100 if percent else result
        except Exception:
            return None


def _unit(value: str) -> str:
    text = _clean(value).casefold()
    for unit in sorted(_UNITS, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(unit)}(?![a-z])", text):
            return unit
    return ""


def _number_without_unit(value: str) -> Optional[Fraction]:
    text = _clean(value).casefold()
    unit = _unit(text)
    if unit:
        text = re.sub(rf"(?<![a-z]){re.escape(unit)}(?![a-z])", "", text).strip()
    return _numeric(text)


def _date(value: str) -> Optional[str]:
    text = _clean(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %B %Y", "%B %d %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _math_expr(value: str):
    text = _clean(value).replace("×", "*").replace("÷", "/").replace("^", "**")
    if not text or len(text) > 200 or not _MATH_CHARS.fullmatch(text):
        return None
    text = re.sub(r"(\d)([A-Za-z])", r"\1*\2", text)
    text = re.sub(r"([A-Za-z])(\d)", r"\1*\2", text)
    text = re.sub(r"(\))([A-Za-z0-9])", r"\1*\2", text)
    text = re.sub(r"([A-Za-z0-9])(\()", r"\1*\2", text)
    try:
        return sympify(text)
    except Exception:
        return None


def _equivalent_math(candidate: str, expected: str) -> Optional[bool]:
    if ("=" in candidate) != ("=" in expected):
        return False
    if "=" in candidate:
        ca, cb = candidate.split("=", 1)
        ea, eb = expected.split("=", 1)
        cexpr, eexpr = _math_expr(ca), _math_expr(ea)
        crhs, erhs = _math_expr(cb), _math_expr(eb)
        if any(value is None for value in (cexpr, eexpr, crhs, erhs)):
            return None
        cdiff, ediff = simplify(cexpr - crhs), simplify(eexpr - erhs)
        if cdiff.free_symbols != ediff.free_symbols:
            return False
        if ediff == 0:
            return bool(cdiff == 0)
        ratio = simplify(cdiff / ediff)
        return bool(not ratio.free_symbols and ratio != 0)
    cexpr, eexpr = _math_expr(candidate), _math_expr(expected)
    if cexpr is None or eexpr is None:
        return None
    if cexpr.free_symbols != eexpr.free_symbols:
        return False
    return bool(simplify(cexpr - eexpr) == 0)


def _looks_structured_math(question: str, expected: str) -> bool:
    return bool(_MATH_HINT.search(question) or (re.search(r"\d", expected) and re.search(r"[=+\-*/^()]|[A-Za-z]\d|\d[A-Za-z]", expected)))


def validate_answer_domain(answer: str, expected_values: Sequence[str], question: str) -> DomainValidation:
    candidate = _clean(answer)
    expected = _clean(expected_values[0] if expected_values else "")
    base = {"candidate": candidate, "canonical": expected}
    if not candidate:
        return DomainValidation("CONTRADICTED", "blank", 1.0, "blank answer", False, base)
    if not expected:
        return DomainValidation("REVIEW", "missing_key", 0.0, "no teacher canonical answer", False, base)
    if normalize(candidate) == normalize(expected):
        return DomainValidation("PROVEN", "exact", 1.0, "exact normalized match", True, base)
    if normalize(candidate) == normalize(question) or len(re.sub(r"\W", "", candidate)) < 1:
        return DomainValidation("CONTRADICTED", "irrelevant", 0.99, "copied question or nonsensical answer", False, base)

    cnum, enum = _number_without_unit(candidate), _number_without_unit(expected)
    if cnum is not None and enum is not None:
        cu, eu = _unit(candidate), _unit(expected)
        if cu != eu and (cu or eu):
            return DomainValidation("CONTRADICTED", "numeric", 1.0, "unit mismatch", False, {**base, "candidate_unit": cu, "canonical_unit": eu})
        if cnum == enum:
            return DomainValidation("PROVEN", "numeric", 1.0, "exact numeric equivalence", True, base)
        return DomainValidation("CONTRADICTED", "numeric", 1.0, "numeric value contradicts canonical", False, base)

    if _DATE_HINT.search(question) or (_date(candidate) and _date(expected)):
        cd, ed = _date(candidate), _date(expected)
        if cd and ed:
            status = "PROVEN" if cd == ed else "CONTRADICTED"
            return DomainValidation(status, "date", 1.0, "same parsed date" if cd == ed else "different parsed date", cd == ed, {**base, "candidate_date": cd, "canonical_date": ed})
        return DomainValidation("REVIEW", "date", 0.0, "date format could not be verified", False, base)

    if _looks_structured_math(question, expected):
        equivalent = _equivalent_math(candidate, expected)
        if equivalent is False:
            return DomainValidation("CONTRADICTED", "mathematics", 0.99, "mathematical contradiction or answer-type mismatch", False, base)
        if equivalent is None:
            return DomainValidation("REVIEW", "mathematics", 0.0, "mathematical equivalence could not be proven", False, base)
        q = question.casefold()
        if "factor" in q and not ("(" in candidate and ")" in candidate):
            return DomainValidation("CONTRADICTED", "mathematics", 0.98, "equivalent but not in requested factorised form", False, base)
        return DomainValidation("PROVEN", "mathematics", 0.99, "symbolic equivalence proven", True, base)

    if _LIST_HINT.search(question):
        return DomainValidation("SEMANTIC", "multipart_list", 0.0, "requires completeness and factual review", False, base)

    return DomainValidation("SEMANTIC", "natural_language", 0.0, "requires semantic and factual judging", False, base)

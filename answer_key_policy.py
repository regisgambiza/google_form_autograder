import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Sequence


_UNICODE_MINUSES = "\u2212\u2012\u2013\u2014\ufe63\uff0d"
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?$")
_FRACTION_RE = re.compile(
    r"^(?P<num>[+-]?(?:\d+(?:\.\d*)?|\.\d+))/(?P<den>[+-]?(?:\d+(?:\.\d*)?|\.\d+))$"
)


def clean_display(value: object) -> str:
    """Return a stable display value without changing mathematical notation."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({char: "-" for char in _UNICODE_MINUSES}))
    return re.sub(r"\s+", " ", text).strip()


def identity_key(value: object) -> str:
    """Key for duplicate detection; intentional internal spacing remains distinct."""
    return clean_display(value).casefold()


def _numeric_value(value: object) -> Optional[Fraction]:
    compact = re.sub(r"\s+", "", clean_display(value))
    fraction_match = _FRACTION_RE.fullmatch(compact)
    try:
        if fraction_match:
            denominator = Fraction(fraction_match.group("den"))
            if denominator == 0:
                return None
            return Fraction(fraction_match.group("num")) / denominator
        if not _NUMBER_RE.fullmatch(compact):
            return None
        percent = compact.endswith("%")
        if percent:
            compact = compact[:-1]
        result = Fraction(compact)
        return result / 100 if percent else result
    except (ValueError, ZeroDivisionError):
        return None


def safely_equivalent(candidate: object, canonical: object) -> bool:
    """Prove equivalence without semantic AI or numeric tolerances."""
    if identity_key(candidate) == identity_key(canonical):
        return True
    candidate_number = _numeric_value(candidate)
    canonical_number = _numeric_value(canonical)
    return (
        candidate_number is not None
        and canonical_number is not None
        and candidate_number == canonical_number
    )


@dataclass(frozen=True)
class AnswerKeyPlan:
    answers: List[str]
    duplicates: List[str]
    rejected: List[str]
    changed: bool


def prepare_answer_key(
    existing: Sequence[object],
    candidates: Sequence[object],
    trusted_expected: Sequence[object],
    max_variants: int = 5,
) -> AnswerKeyPlan:
    """Build an idempotent answer key anchored to the first teacher answer."""
    raw_canonical = clean_display(trusted_expected[0]) if trusted_expected else ""
    legacy_variants = raw_canonical.split("|") if "|" in raw_canonical else []
    canonical = clean_display(legacy_variants[0]) if legacy_variants else raw_canonical
    if not canonical:
        return AnswerKeyPlan([], [], [clean_display(v) for v in candidates if clean_display(v)], False)

    limit = max(1, int(max_variants))
    final: List[str] = []
    seen = set()
    duplicates: List[str] = []
    rejected: List[str] = []

    def consider(raw: object) -> None:
        value = clean_display(raw)
        if not value:
            return
        key = identity_key(value)
        if key in seen:
            duplicates.append(value)
            return
        if not safely_equivalent(value, canonical):
            rejected.append(value)
            return
        if len(final) >= limit:
            rejected.append(value)
            return
        seen.add(key)
        final.append(value)

    consider(canonical)
    for value in legacy_variants[1:]:
        consider(value)
    for value in existing:
        consider(value)
    for value in candidates:
        consider(value)

    old_values = [clean_display(v) for v in existing if clean_display(v)]
    return AnswerKeyPlan(final, duplicates, rejected, final != old_values)

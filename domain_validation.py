"""Question-aware deterministic validation before semantic AI judging."""
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from fractions import Fraction
from typing import Dict, List, Optional, Sequence

from sympy import Ge, Gt, Interval, Le, Lt, simplify, solve_univariate_inequality, sympify

from normalization import normalize
from format_equivalence import compare_formatting


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
_NUMBER_TOKEN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


def _clean(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"−": "-", "–": "-", "—": "-", "×": "*", "÷": "/"}))
    return " ".join(text.replace("\r", " ").replace("\n", " ").split()).strip()


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
        text = re.sub(r"\(\s*\)", "", text).strip()
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


def _allowed_numeric_spec(expected: str) -> Dict[str, object]:
    """Extract explicit ranges and alternatives from teacher mark-scheme prose."""
    text = _clean(expected).casefold()
    spec: Dict[str, object] = {"range": None, "alternatives": []}
    # Do not mine standalone numbers from symbolic alternatives such as
    # "m + 20 or 20 + m". Those are complete expressions, not the numeric
    # alternative 20.
    if re.search(r"(?:\b[a-z]\b\s*[+\-*/^]|[+\-*/^]\s*\b[a-z]\b)", text):
        return spec
    match = re.search(rf"\bbetween\s+({_NUMBER_TOKEN})\s+and\s+({_NUMBER_TOKEN})", text)
    if not match:
        match = re.search(rf"({_NUMBER_TOKEN})\s*(?:to|\.\.|-)\s*({_NUMBER_TOKEN})", text)
    if match:
        low, high = _numeric(match.group(1)), _numeric(match.group(2))
        if low is not None and high is not None:
            spec["range"] = (min(low, high), max(low, high))
    for pattern in (rf"\banswer\s+of\s+({_NUMBER_TOKEN})", rf"\bor\s+({_NUMBER_TOKEN})(?:\s|\(|$)"):
        for token in re.findall(pattern, text):
            parsed = _numeric(token)
            if parsed is not None and parsed not in spec["alternatives"]:
                spec["alternatives"].append(parsed)
    return spec


def _decimal_places(value: str) -> Optional[int]:
    match = re.fullmatch(rf"\s*({_NUMBER_TOKEN})\s*(?:[a-z°%$]+)?\s*", _clean(value), re.I)
    if not match:
        return None
    token = match.group(1)
    return len(token.split(".", 1)[1]) if "." in token else 0


def _required_decimal_places(question: str) -> Optional[int]:
    match = re.search(r"(?:to|give\s+your\s+answer\s+to)\s+(\d+)\s+decimal\s+place", _clean(question), re.I)
    return int(match.group(1)) if match else None


def _not_equal_values(value: str) -> List[Fraction]:
    text = _clean(value).casefold().replace("≠", " != ").replace("=/", " != ")
    found = []
    pattern = rf"(?:!=|not\s+equal\s+to|is\s+not|not)\s*({_NUMBER_TOKEN})"
    for token in re.findall(pattern, text):
        parsed = _numeric(token)
        if parsed is not None and parsed not in found:
            found.append(parsed)
    return found


def _equation_fragments(value: str) -> List[Dict[str, object]]:
    """Extract arithmetic equations from prose without parsing the prose itself."""
    fragments = []
    pattern = re.compile(r"(?<![A-Za-z])([0-9.()\s+*/^-]+=[0-9.()\s+*/^-]+)")
    for raw in pattern.findall(_clean(value)):
        fragment = raw.strip(" ,.;")
        left, right = fragment.split("=", 1)
        lhs, rhs = _math_expr(left), _math_expr(right)
        valid = bool(
            lhs is not None and rhs is not None
            and hasattr(lhs, 'free_symbols') and hasattr(rhs, 'free_symbols')
            and not lhs.free_symbols and not rhs.free_symbols
            and simplify(lhs - rhs) == 0
        )
        fragments.append({"text": fragment, "valid": valid})
    return fragments


def _inequality_set(value: str):
    text = _clean(value).replace("≤", "<=").replace("≥", ">=")
    interval = re.fullmatch(rf"\s*([\[(])\s*({_NUMBER_TOKEN})\s*,\s*({_NUMBER_TOKEN})\s*([\])])\s*", text)
    if interval:
        low, high = _numeric(interval.group(2)), _numeric(interval.group(3))
        if low is not None and high is not None:
            return Interval(low, high, left_open=interval.group(1) == "(", right_open=interval.group(4) == ")")
    compound = re.fullmatch(r"\s*(.+?)\s*(<=|>=|<|>)\s*(.+?)\s*(<=|>=|<|>)\s*(.+?)\s*", text)
    if compound:
        left, op1, middle, op2, right = compound.groups()
        first = _inequality_set(f"{left}{op1}{middle}")
        second = _inequality_set(f"{middle}{op2}{right}")
        if first is not None and second is not None:
            return first.intersect(second)
        return None
    match = re.fullmatch(r"\s*(.+?)\s*(<=|>=|<|>)\s*(.+?)\s*", text)
    if not match:
        return None
    left, op, right = _math_expr(match.group(1)), match.group(2), _math_expr(match.group(3))
    if left is None or right is None:
        return None
    if not hasattr(left, 'free_symbols') or not hasattr(right, 'free_symbols'):
        return None
    symbols = left.free_symbols | right.free_symbols
    if len(symbols) != 1:
        return None
    relation = {"<": Lt, "<=": Le, ">": Gt, ">=": Ge}[op](left, right)
    try:
        return solve_univariate_inequality(relation, next(iter(symbols)), relational=False)
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
        if not hasattr(cdiff, 'free_symbols') or not hasattr(ediff, 'free_symbols'):
            return False
        if cdiff.free_symbols != ediff.free_symbols:
            return False
        if ediff == 0:
            return bool(cdiff == 0)
        ratio = simplify(cdiff / ediff)
        if not hasattr(ratio, 'free_symbols'):
            return bool(ratio != 0)
        return bool(not ratio.free_symbols and ratio != 0)
    cexpr, eexpr = _math_expr(candidate), _math_expr(expected)
    if cexpr is None or eexpr is None:
        return None
    if not hasattr(cexpr, 'free_symbols') or not hasattr(eexpr, 'free_symbols'):
        return False
    if cexpr.free_symbols != eexpr.free_symbols:
        return False
    result = simplify(cexpr - eexpr)
    if not hasattr(result, 'free_symbols'):
        return bool(result == 0)
    return bool(result == 0)


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

    formatting = compare_formatting(candidate, expected, question)
    if formatting.equivalent:
        return DomainValidation(
            "PROVEN", "formatting_equivalence", 1.0,
            "candidate differs from the teacher answer only in harmless formatting",
            True, {**base, "formatting": formatting.to_dict()},
        )

    numeric_spec = _allowed_numeric_spec(expected)
    cnum, enum = _number_without_unit(candidate), _number_without_unit(expected)
    if cnum is not None and (numeric_spec["range"] or numeric_spec["alternatives"]):
        allowed_range = numeric_spec["range"]
        alternatives = numeric_spec["alternatives"]
        in_range = bool(allowed_range and allowed_range[0] <= cnum <= allowed_range[1])
        allowed = in_range or cnum in alternatives
        evidence = {**base, "candidate_value": str(cnum), "allowed_range": tuple(map(str, allowed_range)) if allowed_range else None, "alternatives": [str(v) for v in alternatives]}
        return DomainValidation(
            "PROVEN" if allowed else "CONTRADICTED", "numeric_range", 1.0,
            "value is within an accepted range/alternative" if allowed else "value is outside the accepted range/alternatives",
            allowed, evidence,
        )
    if cnum is not None and enum is not None:
        cu, eu = _unit(candidate), _unit(expected)
        if cu and eu and cu != eu:
            return DomainValidation("CONTRADICTED", "numeric", 1.0, "unit mismatch", False, {**base, "candidate_unit": cu, "canonical_unit": eu})
        if cnum == enum:
            # Enforce required decimal places when the question requests rounding
            # or when the expected uses parenthesized unit notation to indicate precision.
            required_places = _required_decimal_places(question)
            if required_places is None and '(' in expected:
                required_places = _decimal_places(expected)
            cand_places = _decimal_places(candidate)
            if required_places is not None and cand_places is not None and cand_places != required_places:
                return DomainValidation("CONTRADICTED", "numeric", 1.0, "required precision mismatch", False, {**base, "required_decimal_places": required_places, "candidate_decimal_places": cand_places})
            return DomainValidation("PROVEN", "numeric", 1.0, "exact numeric equivalence", True, base)
        return DomainValidation("CONTRADICTED", "numeric", 1.0, "numeric value contradicts canonical", False, base)

    if _DATE_HINT.search(question) or (_date(candidate) and _date(expected)):
        cd, ed = _date(candidate), _date(expected)
        if cd and ed:
            status = "PROVEN" if cd == ed else "CONTRADICTED"
            return DomainValidation(status, "date", 1.0, "same parsed date" if cd == ed else "different parsed date", cd == ed, {**base, "candidate_date": cd, "canonical_date": ed})
        return DomainValidation("REVIEW", "date", 0.0, "date format could not be verified", False, base)

    if _looks_structured_math(question, expected):
        candidate_inequality = _inequality_set(candidate)
        expected_inequality = _inequality_set(expected)
        if candidate_inequality is not None and expected_inequality is not None:
            same = candidate_inequality == expected_inequality
            return DomainValidation(
                "PROVEN" if same else "CONTRADICTED", "inequality", 0.99,
                "equivalent inequality solution set" if same else "different inequality solution set",
                same, {**base, "candidate_set": str(candidate_inequality), "expected_set": str(expected_inequality)},
            )
        expected_not_equal = _not_equal_values(expected)
        candidate_not_equal = _not_equal_values(candidate)
        candidate_equations = _equation_fragments(candidate)
        if expected_not_equal and set(expected_not_equal) & set(candidate_not_equal):
            valid_working = [fragment for fragment in candidate_equations if fragment["valid"]]
            return DomainValidation(
                "PROVEN", "mathematical_explanation", 0.98,
                "student explicitly reaches the required not-equal conclusion",
                True, {**base, "shared_not_equal_values": [str(v) for v in set(expected_not_equal) & set(candidate_not_equal)], "equations": candidate_equations, "valid_working": valid_working},
            )
        # Prose containing calculations is not itself a symbolic expression.
        # Preserve the extracted working for the AI jury instead of rejecting it.
        if candidate_equations:
            return DomainValidation(
                "REVIEW", "mathematical_explanation", 0.0,
                "calculation extracted from explanatory prose for AI evaluation",
                False, {**base, "equations": candidate_equations},
            )
        equivalent = _equivalent_math(candidate, expected)
        # If the question explicitly requested a factorised form, require the candidate
        # to also be factorised (presence of parentheses or explicit multiplication).
        qtext = question.lower() if question else ""
        wants_factor = bool(re.search(r"\bfactor\w*\b", qtext))
        expected_factorised = bool(re.search(r"\(|\*", expected))
        candidate_factorised = bool(re.search(r"\(|\*", candidate))
        if wants_factor and expected_factorised and not candidate_factorised:
            return DomainValidation("CONTRADICTED", "mathematics", 0.99, "factorised form required", False, base)

        if equivalent is False:
            return DomainValidation("CONTRADICTED", "mathematics", 0.99, "mathematical contradiction or answer-type mismatch", False, base)
        if equivalent is None:
            return DomainValidation("REVIEW", "mathematics", 0.0, "mathematical equivalence could not be proven", False, base)
        return DomainValidation("PROVEN", "mathematics", 0.99, "symbolic equivalence proven", True, base)

    if _LIST_HINT.search(question):
        return DomainValidation("SEMANTIC", "multipart_list", 0.0, "requires completeness and factual review", False, base)

    return DomainValidation("SEMANTIC", "natural_language", 0.0, "requires semantic and factual judging", False, base)

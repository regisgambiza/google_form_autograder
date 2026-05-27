import re
from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Union

from sympy import simplify, sympify

from normalization import normalize

UNITS = ["°c", "°f", "k", "kg", "m", "km", "cm", "mph", "kph", "$", "%", "degrees"]


@dataclass
class DeterministicResult:
    """Result from deterministic pre-checks."""
    accepted: bool
    confidence: float
    method: str


def _strip_units(s: str) -> str:
    out = str(s).lower()
    for unit in UNITS:
        out = out.replace(unit, "")
    return re.sub(r"\s+", " ", out).strip()


def _to_fraction(value: str) -> Optional[Fraction]:
    s = value.strip().lower().replace("½", "1/2").replace("×", "x")
    if s.endswith("%"):
        return Fraction(s[:-1]) / 100
    sci = re.match(r"^([\d\.]+)(?:\s*[x*]\s*10\^?([\-\d]+)|e([\-\d]+))$", s)
    if sci:
        exp = sci.group(2) or sci.group(3)
        return Fraction(sci.group(1)) * (Fraction(10) ** int(exp))
    try:
        return Fraction(s)
    except Exception:
        try:
            return Fraction(str(float(s)))
        except Exception:
            return None


def algebra_equal(a: str, b: str) -> bool:
    """SymPy algebraic equivalence check."""
    try:
        def prep(x: str) -> str:
            x = str(x).replace(" ", "")
            x = re.sub(r"(\d)([a-zA-Z])", r"\1*\2", x)
            x = re.sub(r"([a-zA-Z])(\d)", r"\1*\2", x)
            x = re.sub(r"(\))([a-zA-Z0-9])", r"\1*\2", x)
            return x
        return simplify(sympify(prep(a)) - sympify(prep(b))) == 0
    except Exception:
        return False


def run_deterministic_checks(answer: str, expected: Union[str, List[str]], numeric_tolerance: float = 0.01) -> DeterministicResult:
    """Run stage-2 deterministic checks; returns first high-confidence hit."""
    exp_list = expected if isinstance(expected, list) else [expected]
    na = normalize(answer)
    ne_list = [normalize(e) for e in exp_list]

    if na in ne_list:
        return DeterministicResult(True, 1.0, "exact_normalized")

    if isinstance(expected, list):
        matches = sum(1 for e in expected if normalize(e) in na)
        if matches == len(expected) and matches > 0:
            return DeterministicResult(True, 0.97, "multiple_answer")

    for exp in exp_list:
        # Try raw first (preserves percentage semantics like 50% == 0.5), then unit-stripped.
        af = _to_fraction(answer)
        ef = _to_fraction(exp)
        if af is None or ef is None:
            af = _to_fraction(_strip_units(answer))
            ef = _to_fraction(_strip_units(exp))
        if af is not None and ef is not None:
            av = float(af)
            ev = float(ef)
            if av == ev:
                return DeterministicResult(True, 0.99, "numeric_equivalence")
            if ev != 0 and abs(av - ev) / abs(ev) <= numeric_tolerance:
                return DeterministicResult(True, 0.98, "numeric_tolerance")

        if algebra_equal(answer, exp):
            return DeterministicResult(True, 0.98, "algebraic_equivalence")

        if "=" in answer and "=" in exp:
            try:
                a_l, a_r = [sympify(x) for x in answer.split("=", 1)]
                e_l, e_r = [sympify(x) for x in exp.split("=", 1)]
                if simplify((a_l - a_r) - (e_l - e_r)) == 0:
                    return DeterministicResult(True, 0.97, "equation_equivalence")
            except Exception:
                pass

        if re.search(r"\(\s*[-\d\.]+\s*,\s*[-\d\.]+\s*\)", exp) and re.search(r"<\s*x\s*<", answer.lower()):
            return DeterministicResult(True, 0.96, "interval_equivalence")

    return DeterministicResult(False, 0.0, "none")

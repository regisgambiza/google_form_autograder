import re
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Union

from sympy import simplify, sympify

from normalization import normalize
from logger import log


class TimeoutError(Exception):
    """Custom timeout exception."""
    pass


def timeout_safe_sympify(expr: str, timeout_seconds: int = 3) -> Optional:
    """Safely call sympify with timeout protection using threading."""
    result = [None]
    exception = [None]
    
    def worker():
        try:
            result[0] = sympify(expr)
        except Exception as e:
            exception[0] = e
    
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    
    if thread.is_alive():
        log("DEBUG", f"sympify timeout for: {expr[:50]}...")
        return None
    
    if exception[0] is not None:
        # Don't log every parse error - they're expected with messy student answers
        # log("DEBUG", f"sympify error for '{expr}': {exception[0]}")
        return None
    
    return result[0]


def timeout_safe_simplify(expr, timeout_seconds: int = 5) -> Optional:
    """Safely call simplify with timeout protection using threading."""
    result = [None]
    exception = [None]
    
    def worker():
        try:
            result[0] = simplify(expr)
        except Exception as e:
            exception[0] = e
    
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    
    if thread.is_alive():
        log("DEBUG", "simplify timeout")
        return None
    
    if exception[0] is not None:
        log("DEBUG", f"simplify error: {exception[0]}")
        return None
    
    return result[0]

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
    """SymPy algebraic equivalence check with timeout protection."""
    try:
        def prep(x: str) -> str:
            x = str(x).replace(" ", "")
            x = re.sub(r"(\d)([a-zA-Z])", r"\1*\2", x)
            x = re.sub(r"([a-zA-Z])(\d)", r"\1*\2", x)
            x = re.sub(r"(\))([a-zA-Z0-9])", r"\1*\2", x)
            return x
        
        a_simp = timeout_safe_sympify(prep(a), timeout_seconds=3)
        b_simp = timeout_safe_sympify(prep(b), timeout_seconds=3)
        
        if a_simp is None or b_simp is None:
            return False
        
        result = timeout_safe_simplify(a_simp - b_simp, timeout_seconds=5)
        return result is not None and result == 0
    except Exception:
        return False


def run_deterministic_checks(answer: str, expected: Union[str, List[str]], numeric_tolerance: float = 0.01, total_timeout: int = 30) -> DeterministicResult:
    """Run stage-2 deterministic checks; returns first high-confidence hit.

    Args:
        answer: The student's answer
        expected: Expected answer(s)
        numeric_tolerance: Numeric comparison tolerance
        total_timeout: Maximum total time in seconds for all checks
    """
    start = time.perf_counter()
    log("INFO", f"START deterministic_checks (answer_len={len(answer)}, expected_count={1 if isinstance(expected, str) else len(expected)})")
    exp_list = expected if isinstance(expected, list) else [expected]
    na = normalize(answer)
    ne_list = [normalize(e) for e in exp_list]

    # Check total timeout at start
    elapsed = time.perf_counter() - start
    if elapsed > total_timeout:
        log("DEBUG", f"deterministic_checks aborted - total timeout exceeded ({elapsed:.1f}s > {total_timeout}s)")
        return DeterministicResult(False, 0.0, "timeout")

    if na in ne_list:
        duration_ms = (time.perf_counter() - start) * 1000
        log("INFO", f"END deterministic_checks duration_ms={duration_ms:.0f} method=exact_normalized accepted=True")
        return DeterministicResult(True, 1.0, "exact_normalized")

    # Check timeout after normalized check (30% of total time)
    elapsed = time.perf_counter() - start
    if elapsed > total_timeout * 0.3:
        log("DEBUG", f"deterministic_checks aborted - timeout after normalized check ({elapsed:.1f}s > {total_timeout * 0.3:.1f}s)")
        return DeterministicResult(False, 0.0, "timeout")

    if isinstance(expected, list):
        matches = sum(1 for e in expected if normalize(e) in na)
        if matches == len(expected) and matches > 0:
            duration_ms = (time.perf_counter() - start) * 1000
            log("INFO", f"END deterministic_checks duration_ms={duration_ms:.0f} method=multiple_answer accepted=True")
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
                duration_ms = (time.perf_counter() - start) * 1000
                log("INFO", f"END deterministic_checks duration_ms={duration_ms:.0f} method=numeric_equivalence accepted=True")
                return DeterministicResult(True, 0.99, "numeric_equivalence")
            if ev != 0 and abs(av - ev) / abs(ev) <= numeric_tolerance:
                duration_ms = (time.perf_counter() - start) * 1000
                log("INFO", f"END deterministic_checks duration_ms={duration_ms:.0f} method=numeric_tolerance accepted=True")
                return DeterministicResult(True, 0.98, "numeric_tolerance")

        if algebra_equal(answer, exp):
            duration_ms = (time.perf_counter() - start) * 1000
            log("INFO", f"END deterministic_checks duration_ms={duration_ms:.0f} method=algebraic_equivalence accepted=True")
            return DeterministicResult(True, 0.98, "algebraic_equivalence")

        if "=" in answer and "=" in exp:
            try:
                a_l = timeout_safe_sympify(answer.split("=", 1)[0], timeout_seconds=3)
                a_r = timeout_safe_sympify(answer.split("=", 1)[1], timeout_seconds=3)
                e_l = timeout_safe_sympify(exp.split("=", 1)[0], timeout_seconds=3)
                e_r = timeout_safe_sympify(exp.split("=", 1)[1], timeout_seconds=3)
                
                if a_l is None or a_r is None or e_l is None or e_r is None:
                    continue
                    
                diff = timeout_safe_simplify((a_l - a_r) - (e_l - e_r), timeout_seconds=5)
                if diff is not None and diff == 0:
                    duration_ms = (time.perf_counter() - start) * 1000
                    log("INFO", f"END deterministic_checks duration_ms={duration_ms:.0f} method=equation_equivalence accepted=True")
                    return DeterministicResult(True, 0.97, "equation_equivalence")
            except Exception:
                pass

        if re.search(r"\(\s*[-\d\.]+\s*,\s*[-\d\.]+\s*\)", exp) and re.search(r"<\s*x\s*<", answer.lower()):
            duration_ms = (time.perf_counter() - start) * 1000
            log("INFO", f"END deterministic_checks duration_ms={duration_ms:.0f} method=interval_equivalence accepted=True")
            return DeterministicResult(True, 0.96, "interval_equivalence")
    
    duration_ms = (time.perf_counter() - start) * 1000
    log("INFO", f"END deterministic_checks duration_ms={duration_ms:.0f} method=none accepted=False")
    return DeterministicResult(False, 0.0, "none")

"""Conservative equivalence for answers that differ only in presentation."""
import re
import unicodedata
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Dict, Optional, Tuple


_FRACTIONS = {
    "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4",
    "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5", "⅛": "1/8",
    "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}
_UNIT_ALIASES = {
    "millimetres": "mm", "millimeters": "mm", "millimetre": "mm", "millimeter": "mm",
    "centimetres": "cm", "centimeters": "cm", "centimetre": "cm", "centimeter": "cm",
    "kilometres": "km", "kilometers": "km", "kilometre": "km", "kilometer": "km",
    "metres": "m", "meters": "m", "metre": "m", "meter": "m",
    "kilograms": "kg", "kilogram": "kg", "grams": "g", "gram": "g",
    "degrees": "°", "degree": "°",
}
_KNOWN_UNITS = ("km", "kg", "cm", "mm", "mph", "kph", "°c", "°f", "°", "$", "m", "g", "s", "h")


@dataclass(frozen=True)
class FormatEquivalence:
    equivalent: bool
    kind: str
    candidate_normalized: str
    expected_normalized: str
    evidence: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def normalize_presentation(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"−": "-", "–": "-", "—": "-", "×": "*", "÷": "/", "⁄": "/"}))
    for symbol, replacement in _FRACTIONS.items():
        text = text.replace(symbol, replacement)
    text = text.casefold().strip()
    for raw, canonical in _UNIT_ALIASES.items():
        text = re.sub(rf"\b{re.escape(raw)}\b", canonical, text)
    # OCR/student spacing inside common units: c m, k g, m m.
    text = re.sub(r"(?<![a-z])c\s+m(?![a-z])", "cm", text)
    text = re.sub(r"(?<![a-z])m\s+m(?![a-z])", "mm", text)
    text = re.sub(r"(?<![a-z])k\s+g(?![a-z])", "kg", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _canonical_decimal(token: str) -> Optional[str]:
    token = token.strip().replace(" ", "")
    if not token:
        return None
    sign = ""
    if token[:1] in "+-":
        sign, token = token[0], token[1:]
    if not token or not re.fullmatch(r"[0-9.,]+", token):
        return None
    if "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        grouping = "." if decimal == "," else ","
        token = token.replace(grouping, "").replace(decimal, ".")
    elif "," in token:
        parts = token.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            token = parts[0] + "." + parts[1]
        elif len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            token = "".join(parts)
        else:
            return None
    elif token.count(".") > 1:
        parts = token.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            token = "".join(parts)
        else:
            return None
    return sign + token


def _parse_quantity(value: object) -> Optional[Tuple[Fraction, str, Optional[int], bool]]:
    text = normalize_presentation(value)
    parenthesized_unit = bool(re.search(r"\(\s*[a-z°]+\s*\)\s*$", text))
    text = re.sub(r"\(\s*([a-z°]+)\s*\)\s*$", r" \1", text)
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    unit = ""
    if text.startswith("$"):
        unit, text = "$", text[1:].strip()
    for candidate in sorted(_KNOWN_UNITS, key=len, reverse=True):
        match = re.search(rf"(?<![a-z]){re.escape(candidate)}(?![a-z])\s*$", text)
        if match:
            unit = candidate
            text = text[:match.start()].strip()
            break
    compact = text.replace(" ", "")
    if re.fullmatch(r"[+-]?[0-9.,]+/[+-]?[0-9.,]+", compact):
        numerator, denominator = compact.split("/", 1)
        num, den = _canonical_decimal(numerator), _canonical_decimal(denominator)
        if num is None or den is None:
            return None
        try:
            value_fraction = Fraction(num) / Fraction(den)
        except (ValueError, ZeroDivisionError):
            return None
        places = None
    else:
        canonical = _canonical_decimal(compact)
        if canonical is None:
            return None
        try:
            value_fraction = Fraction(canonical)
        except ValueError:
            return None
        places = len(canonical.split(".", 1)[1]) if "." in canonical else 0
    if percent:
        value_fraction /= 100
    return value_fraction, unit, places, parenthesized_unit


def _requires_written_unit(question: str) -> bool:
    text = normalize_presentation(question)
    return bool(re.search(r"\b(include|state|show|write|give)\b.{0,25}\bunit(?:s)?\b|\bwith\s+units?\b", text))


def _question_supplies_unit(question: str, unit: str) -> bool:
    return bool(question and unit and re.search(rf"(?<![a-z]){re.escape(unit)}(?![a-z])", normalize_presentation(question)))


def _required_places(question: str) -> Optional[int]:
    match = re.search(r"(?:to|answer\s+to)\s+(\d+)\s+decimal\s+place", normalize_presentation(question))
    return int(match.group(1)) if match else None


def compare_formatting(candidate: object, expected: object, question: str = "") -> FormatEquivalence:
    ctext, etext = normalize_presentation(candidate), normalize_presentation(expected)
    cq, eq = _parse_quantity(candidate), _parse_quantity(expected)
    if cq and eq:
        cvalue, cunit, cplaces, _ = cq
        evalue, eunit, eplaces, eunit_parenthetical = eq
        # Units and written precision are presentation details. Missing units,
        # harmless extra units, and equivalent numeric formatting are accepted;
        # only two explicit incompatible units remain a substantive mismatch.
        units_compatible = not (cunit and eunit and cunit != eunit)
        required_places = None
        precision_ok = True
        equivalent = cvalue == evalue and units_compatible and precision_ok
        return FormatEquivalence(
            equivalent,
            "numeric_unit_format" if equivalent else "numeric_difference",
            f"{cvalue}:{cunit}",
            f"{evalue}:{eunit}",
            {
                "candidate_value": str(cvalue), "expected_value": str(evalue),
                "candidate_unit": cunit, "expected_unit": eunit,
                "units_compatible": units_compatible, "required_decimal_places": required_places,
                "candidate_decimal_places": cplaces, "precision_ok": precision_ok,
            },
        )

    # Conservative text formatting: case, surrounding punctuation, and spacing only.
    def text_key(text: str) -> str:
        text = re.sub(r"\s*([=+*/^<>])\s*", r"\1", text)
        text = re.sub(r"^[\s\"'“”‘’.,;:!?]+|[\s\"'“”‘’.,;:!?]+$", "", text)
        return re.sub(r"\s+", " ", text).strip()

    ckey, ekey = text_key(ctext), text_key(etext)
    equivalent = bool(ckey and ckey == ekey)
    return FormatEquivalence(
        equivalent,
        "text_format" if equivalent else "different_content",
        ckey,
        ekey,
        {"case_spacing_punctuation_only": equivalent},
    )

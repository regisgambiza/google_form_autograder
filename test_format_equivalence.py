from format_equivalence import compare_formatting, normalize_presentation
from domain_validation import validate_answer_domain


QUESTION = "Find the diameter of a circle with circumference 160 cm. Give your answer to 1 decimal place."
EXPECTED = "50.9 (cm)"


def test_decimal_comma_and_unit_format_variants_are_equivalent():
    for candidate in ("50,9 cm", "50.9", "50.9cm", "50.9 c m", " 50.9 ( cm ) "):
        result = compare_formatting(candidate, EXPECTED, QUESTION)
        assert result.equivalent, candidate


def test_value_sign_unit_and_required_precision_remain_meaningful():
    for candidate in ("51", "50", "50.95", "-50.9", "50.9 kg", "50.90 cm"):
        assert not compare_formatting(candidate, EXPECTED, QUESTION).equivalent, candidate


def test_locale_grouping_fraction_percent_and_unicode_symbols_normalize():
    assert compare_formatting("1.234,56", "1,234.56").equivalent
    assert compare_formatting("1,234", "1234").equivalent
    assert compare_formatting("½", "0.5").equivalent
    assert compare_formatting("50%", "0.5").equivalent
    assert compare_formatting("6 × (5m + 7)", "6 * (5m + 7)").equivalent
    assert normalize_presentation("10 − 7") == "10 - 7"


def test_case_whitespace_and_surrounding_punctuation_are_harmless_only():
    assert compare_formatting("  TRUE. ", "true").equivalent
    assert compare_formatting("Photosynthesis!", "photosynthesis").equivalent
    assert not compare_formatting("not true", "true").equivalent


def test_domain_validation_proves_formatting_without_confusing_wrong_values():
    correct = validate_answer_domain("50,9 cm", [EXPECTED], QUESTION)
    wrong = validate_answer_domain("51.0 cm", [EXPECTED], QUESTION)
    assert correct.status == "PROVEN"
    assert correct.domain == "formatting_equivalence"
    assert wrong.status == "CONTRADICTED"

from domain_validation import validate_answer_domain


def check(answer, expected, question):
    return validate_answer_domain(answer, [expected], question)


def test_numeric_sign_and_value_contradictions_are_rejected():
    assert check("-13", "-13", "Calculate the value").status == "PROVEN"
    assert check("13", "-13", "Calculate the value").status == "CONTRADICTED"
    assert check("72", "6(5m+7)", "Factorise 30m + 42").status == "CONTRADICTED"


def test_units_must_be_compatible():
    result = check("5 m", "5 kg", "Give the mass with units")
    assert result.status == "CONTRADICTED"
    assert result.reason == "unit mismatch"


def test_symbolic_equivalence_and_variable_preservation():
    assert check("6*(5*m+7)", "6(5m+7)", "Factorise 30m + 42").status == "PROVEN"
    assert check("72", "6(5m+7)", "Factorise 30m + 42").key_eligible is False
    assert check("m=12", "6(5m+7)", "Factorise 30m + 42").status == "CONTRADICTED"


def test_requested_mathematical_form_is_enforced():
    result = check("30m + 42", "6(5m+7)", "Factorise fully: 30m + 42")
    assert result.status == "CONTRADICTED"
    assert "factorised form" in result.reason


def test_equivalent_equations_are_proven_but_different_solutions_rejected():
    assert check("2*x=4", "x=2", "Solve the equation").status == "PROVEN"
    assert check("x=3", "x=2", "Solve the equation").status == "CONTRADICTED"


def test_dates_are_parsed_canonically():
    assert check("2 July 2026", "02/07/2026", "Give the date").status == "PROVEN"
    assert check("3 July 2026", "02/07/2026", "Give the date").status == "CONTRADICTED"


def test_open_explanations_and_lists_route_to_semantic_jury():
    explanation = check("Plants convert light energy into chemical energy", "Plants use sunlight to make food", "Explain photosynthesis")
    listing = check("gravity and friction", "gravity; friction", "Name two forces")
    assert explanation.status == "SEMANTIC"
    assert listing.status == "SEMANTIC"


def test_blank_and_copied_question_are_rejected():
    assert check("", "Paris", "What is the capital of France?").status == "CONTRADICTED"
    assert check("What is the capital of France?", "Paris", "What is the capital of France?").status == "CONTRADICTED"


def test_mark_scheme_ranges_and_explicit_alternatives_are_supported():
    expected = "An answer between 62.8 and 62.84 (cm) or for an answer of 63 (cm)"
    assert check("62.82", expected, "Work out the ribbon length").status == "PROVEN"
    assert check("63 cm", expected, "Work out the ribbon length").status == "PROVEN"
    assert check("64", expected, "Work out the ribbon length").status == "CONTRADICTED"


def test_symbolic_or_alternatives_do_not_accept_an_extracted_number():
    result = check("20", "m + 20 or 20 + m", "the cost of a television")

    assert result.status != "PROVEN"
    assert result.reason != "value is within an accepted range/alternative"


def test_required_rounding_and_parenthesized_units_are_supported():
    question = "Find the diameter. Give your answer to 1 decimal place."
    assert check("50.9 cm", "50.9 (cm)", question).status == "PROVEN"
    assert check("50.90 cm", "50.9 (cm)", question).status == "CONTRADICTED"


def test_equivalent_simple_inequalities_have_same_solution_set():
    assert check("4 < x", "x > 4", "Solve the inequality").status == "PROVEN"
    assert check("x >= 4", "x > 4", "Solve the inequality").status == "CONTRADICTED"
    assert check("(1, 4]", "1 < x <= 4", "Solve the inequality").status == "PROVEN"


def test_not_equal_reasoning_is_extracted_from_explanatory_prose():
    expected = "10 + 15 - 7 is not 2 or she would be correct with different values"
    answer = "10 + 15 - 7 = 18, which is not equal to 2, so the polyhedron cannot exist"
    result = check(answer, expected, "Explain why the polyhedron is impossible")
    assert result.status == "PROVEN"
    assert result.domain == "mathematical_explanation"
    assert result.evidence["equations"][0]["valid"] is True


def test_unresolved_calculation_in_prose_is_sent_to_ai_not_rejected():
    result = check(
        "I calculated 10 + 15 - 7 = 17 but I am unsure",
        "10 + 15 - 7 is not 2",
        "Explain why the polyhedron is impossible",
    )
    assert result.status == "REVIEW"
    assert result.domain == "mathematical_explanation"

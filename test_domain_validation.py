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

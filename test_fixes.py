"""Quick test to verify schema mismatch fixes work."""
from ai_judges import _normalize_decision, _fill_judge_defaults, _valid

# Test 1: Model returns 'Correct' instead of 'YES'
test1 = {
    "confidence": 0.85, "decision": "Correct", "reason_short": "good answer"
}
test1 = _normalize_decision(test1)
test1 = _fill_judge_defaults(test1)
assert test1["decision"] == "YES", f"Expected YES, got {test1['decision']}"
assert _valid(test1), "Test 1 should be valid after normalization"
print("Test 1 PASSED: 'Correct' normalized to 'YES', missing fields filled")

# Test 2: Model returns partial response (missing most fields)
test2 = {"decision": "yes", "confidence": 0.6}
test2 = _normalize_decision(test2)
test2 = _fill_judge_defaults(test2)
assert test2["decision"] == "YES"
assert _valid(test2), "Test 2 should be valid after defaults filled"
assert test2["reason_short"] == "partial"
print("Test 2 PASSED: Partial response filled with defaults")

# Test 3: Completely different field names (worst case)
test3 = {"score": 0.8, "result": "correct"}
test3 = _normalize_decision(test3)
test3 = _fill_judge_defaults(test3)
assert test3["decision"] == "ERROR", f"Unknown decision should be internal ERROR, got {test3['decision']}"
assert not _valid(test3), "Internal errors must be retried rather than accepted as verdicts"
print("Test 3 PASSED: Unknown fields become a retryable internal error")

# Test 4: Minimal NO response
test4 = {"decision": "NO", "confidence": 0.9, "reason_short": "incorrect"}
test4 = _normalize_decision(test4)
test4 = _fill_judge_defaults(test4)
assert _valid(test4)
print("Test 4 PASSED: Minimal binary NO response is valid")

# Test rubric defaults
from rubric_generator import _fill_rubric_defaults
test5 = {"required_concepts": ["photosynthesis"], "grading_notes": "test"}
test5 = _fill_rubric_defaults(test5, "plants make food using sunlight")
assert "optional_concepts" in test5, "Missing rubric key should be filled"
assert "misconceptions" in test5, "Missing rubric key should be filled"
print("Test 5 PASSED: Rubric missing keys filled with defaults")

print("\n=== ALL TESTS PASSED ===")

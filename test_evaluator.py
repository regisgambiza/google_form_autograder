from evaluation_pipeline import evaluate_answer

CASES = [
    ("50%", "0.5", True, "Numeric equivalence"),
    ("1/2", "0.5", True, "Fraction equivalence"),
    ("plants use sun to make food", "photosynthesis converts sunlight to energy", True, "Semantic paraphrase"),
    ("plants eat sunlight", "photosynthesis converts sunlight to energy", False, "Misconception"),
    ("4x", "2x + 2x", True, "Algebraic equivalence"),
    ("sky is blue", "the sky is blue", True, "Thai ESL article drop"),
    ("i dont know", "photosynthesis", False, "Unrelated answer"),
    ("42 degrees", "42", True, "Unit-insensitive"),
]


def main() -> None:
    passed = 0
    for i, (student, expected, should_pass, reason) in enumerate(CASES, 1):
        result = evaluate_answer(student, expected, "Answer the question")
        got_pass = result.decision == "YES"
        ok = got_pass == should_pass
        passed += int(ok)
        print(f"[{i}] {'PASS' if ok else 'FAIL'} | expected={'YES' if should_pass else 'NO'} got={result.decision} | score={result.final_score:.3f} | stage={result.stage_reached} | {reason}")
    total = len(CASES)
    print(f"Overall accuracy: {passed}/{total} = {passed/total:.2%}")


if __name__ == "__main__":
    main()

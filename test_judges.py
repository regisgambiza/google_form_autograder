"""Optional live judge smoke test.

Normal pytest runs must not call local or remote AI providers. Set
RUN_LIVE_JUDGE_TESTS=1 when intentionally checking the installed model stack.
"""

import json
import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_JUDGE_TESTS") != "1",
    reason="live AI judge integration test; set RUN_LIVE_JUDGE_TESTS=1 to run",
)


def test_run_judges_live_smoke():
    from ai_judges import run_judges
    from rubric_generator import generate_rubric

    question = "What is photosynthesis?"
    expected = "Plants make food using sunlight, water, and carbon dioxide."
    answer = "plants use sun to make food"

    rubric = generate_rubric(question, expected)
    results = run_judges(answer, question, expected, rubric)

    assert isinstance(rubric, dict), json.dumps(rubric, ensure_ascii=True)
    assert isinstance(results, list)
    assert results

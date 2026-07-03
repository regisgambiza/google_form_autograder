from answer_key_policy import equivalence_confidence, prepare_answer_key, safely_equivalent


def test_deduplicates_same_batch_and_is_idempotent():
    first = prepare_answer_key([], ["-13", "-13", " -13 "], ["-13"])
    assert first.answers == ["-13"]
    assert first.duplicates == ["-13", "-13", "-13"]

    second = prepare_answer_key(first.answers, ["-13"], ["-13"])
    assert second.answers == ["-13"]
    assert second.changed is False


def test_keeps_one_common_spaced_variant():
    plan = prepare_answer_key([], ["-  13", "- 13"], ["-13"])
    assert plan.answers == ["-13", "- 13"]


def test_numeric_equivalence_is_exact_not_tolerant():
    assert safely_equivalent("1/2", "0.5")
    assert safely_equivalent("50%", "0.5")
    assert safely_equivalent("- 13.0", "-13")
    assert not safely_equivalent("-12.999", "-13")
    assert not safely_equivalent("13", "-13")
    assert equivalence_confidence("13", "-13") == 0.0
    assert equivalence_confidence("negative thirteen", "-13") == 0.60
    assert equivalence_confidence("20p+4q+4", "56") == 0.0


def test_ai_semantic_match_cannot_mutate_key():
    plan = prepare_answer_key(
        ["photosynthesis"],
        ["plants use sunlight to make food"],
        ["photosynthesis"],
    )
    assert plan.answers == ["photosynthesis"]
    assert plan.rejected == ["plants use sunlight to make food"]


def test_cleans_existing_duplicates_and_wrong_answers():
    plan = prepare_answer_key(
        ["-13", "-13", "13", "-  13"],
        [],
        ["-13"],
    )
    assert plan.answers == ["-13", "- 13"]
    assert "13" in plan.rejected
    assert plan.changed is True


def test_blocks_update_without_teacher_answer():
    plan = prepare_answer_key(["possibly wrong"], ["new answer"], [])
    assert plan.answers == []
    assert plan.changed is False


def test_caps_variant_growth():
    plan = prepare_answer_key([], ["0.5", "1/2", "50%", ".5"], ["0.50"], max_variants=3)
    assert plan.answers == ["0.50", "0.5", "1/2"]
    assert plan.rejected == ["50%", ".5"]


def test_recovers_legacy_pipe_delimited_answer_without_trusting_all_tokens():
    legacy = "-13 | - 13 | negative thirteen | -13.0 | 13"
    plan = prepare_answer_key([legacy, "-13", "13"], [], [legacy])
    assert plan.answers == ["-13", "- 13", "-13.0"]
    assert "negative thirteen" in plan.rejected
    assert "13" in plan.rejected

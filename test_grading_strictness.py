from accuracy_policy import strictness_profile


def test_strictness_profiles_have_expected_routing_behavior():
    strict = strictness_profile("strict")
    balanced = strictness_profile("balanced")
    lenient = strictness_profile("lenient")
    review = strictness_profile("review-heavy")

    assert strict["require_distinct_models"] is True
    assert strict["minimum_judge_confidence"] > balanced["minimum_judge_confidence"]
    assert balanced["accept_unanimous_yes"] is True
    assert lenient["accept_yes_majority"] is True
    assert lenient["soften_rejections"] is True
    assert review["accept_unanimous_yes"] is False


def test_unknown_strictness_falls_back_to_balanced():
    assert strictness_profile("surprise")["mode"] == "balanced"

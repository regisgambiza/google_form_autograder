import form_context_builder
from form_context_builder import apply_question_context, build_form_context, get_question_context


def test_build_form_context_uses_section_text_and_nearby_questions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(form_context_builder, "log", lambda *_args, **_kwargs: None)
    tmp_path.joinpath("config.json").write_text('{"validate_expected_answers": false}', encoding="utf-8")
    form_data = {
        "items": [
            {
                "itemId": "section1",
                "title": "Exercise 2",
                "description": "TEXTBOOK CONTEXT - use this image for questions 2a-2c",
                "pageBreakItem": {},
            },
            {
                "itemId": "text1",
                "title": "Textbook Context",
                "description": "The picture shows evaporation and condensation.",
                "textItem": {},
            },
            {
                "itemId": "item-a",
                "title": "2a",
                "description": "Answer from the exercise.",
                "questionItem": {
                    "question": {
                        "questionId": "qid-a",
                        "textQuestion": {},
                        "grading": {"correctAnswers": {"answers": [{"value": "evaporation"}]}},
                    }
                },
            },
            {
                "itemId": "item-b",
                "title": "2b",
                "questionItem": {
                    "question": {
                        "questionId": "qid-b",
                        "textQuestion": {},
                        "grading": {"correctAnswers": {"answers": [{"value": "condensation"}]}},
                    }
                },
            },
        ]
    }
    structure = [
        {"itemId": "item-a", "questionId": "qid-a", "index": 3, "title": "2a", "description": "Answer from the exercise.", "type": "SHORT_ANSWER"},
        {"itemId": "item-b", "questionId": "qid-b", "index": 4, "title": "2b", "description": "", "type": "SHORT_ANSWER"},
    ]
    expected = {"item-a": ["evaporation"], "item-b": ["condensation"]}

    context = build_form_context("form123", "Water Cycle", form_data, structure, expected)
    enriched = apply_question_context(structure, context)

    qtext = get_question_context(enriched[0])
    assert "Form title: Water Cycle" in qtext
    assert "Section context: Exercise 2" in qtext
    assert "The picture shows evaporation and condensation." in qtext
    assert "Current question label/title: 2a" in qtext
    assert "Q4: 2b" in qtext
    assert "evaporation" in qtext


def test_get_question_context_falls_back_to_title():
    assert get_question_context({"title": "2a"}) == "2a"


def test_expected_validation_only_checks_first_teacher_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(form_context_builder, "log", lambda *_args, **_kwargs: None)
    tmp_path.joinpath("config.json").write_text(
        '{"validate_expected_answers": true}', encoding="utf-8"
    )

    import expected_answer_validator

    calls = []

    def capture_validation(context, expected):
        calls.append((context, expected))
        return {
            "validation_status": "ok",
            "valid": True,
            "confidence": 1.0,
            "suggested_answers": [],
            "reason": "Teacher answer is correct.",
            "original_expected": expected,
        }

    monkeypatch.setattr(expected_answer_validator, "validate_expected_answer", capture_validation)
    structure = [
        {
            "itemId": "item-a",
            "questionId": "qid-a",
            "index": 1,
            "title": "2a",
            "description": "",
            "type": "SHORT_ANSWER",
        }
    ]
    form_data = {
        "items": [
            {
                "itemId": "item-a",
                "title": "2a",
                "questionItem": {"question": {"questionId": "qid-a", "textQuestion": {}}},
            }
        ]
    }

    context = build_form_context(
        "form123", "Integers", form_data, structure, {"item-a": ["16", "sixteen", "4 x 4"]}
    )

    assert len(calls) == 1
    assert calls[0][1] == ["16"]
    assert "- 16" in calls[0][0]
    assert "sixteen" not in calls[0][0]
    assert context["questions"]["qid-a"]["effective_expected"] == ["16", "sixteen", "4 x 4"]


def test_validator_defensively_uses_only_first_expected_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tmp_path.joinpath("config.json").write_text(
        '{"validate_expected_answers": true, "expected_answer_validator_model": "test-model", '
        '"expected_answer_validator_fallback_model": ""}',
        encoding="utf-8",
    )

    import expected_answer_validator

    captured = []

    def fake_call(_model, _context, expected, _timeout, _connect_timeout):
        captured.append(expected)
        return {"validation_status": "ok", "valid": True, "original_expected": expected}

    monkeypatch.setattr(expected_answer_validator, "_call_validator", fake_call)

    result = expected_answer_validator.validate_expected_answer("Question", ["16", "sixteen"])

    assert captured == [["16"]]
    assert result["original_expected"] == ["16"]


def test_invalid_expected_can_use_suggested_answer_for_grading(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(form_context_builder, "log", lambda *_args, **_kwargs: None)
    tmp_path.joinpath("config.json").write_text(
        '{"validate_expected_answers": true, "use_validated_expected_for_grading": true, '
        '"expected_answer_validator_min_confidence": 0.85}',
        encoding="utf-8",
    )

    import expected_answer_validator

    monkeypatch.setattr(
        expected_answer_validator,
        "validate_expected_answer",
        lambda _context, expected: {
            "validation_status": "ok",
            "valid": False,
            "confidence": 0.97,
            "suggested_answers": ["-16"],
            "reason": "A negative times a positive is negative.",
            "original_expected": expected,
        },
    )

    form_data = {
        "items": [
            {
                "itemId": "item-a",
                "title": "2a",
                "questionItem": {
                    "question": {
                        "questionId": "qid-a",
                        "textQuestion": {},
                        "grading": {"correctAnswers": {"answers": [{"value": "16"}]}},
                    }
                },
            }
        ]
    }
    structure = [
        {"itemId": "item-a", "questionId": "qid-a", "index": 1, "title": "2a", "description": "", "type": "SHORT_ANSWER"},
    ]

    context = build_form_context("form123", "Integers", form_data, structure, {"item-a": ["16"]})
    enriched = apply_question_context(structure, context)

    assert enriched[0]["effective_expected"] == ["-16"]
    assert enriched[0]["expected_was_replaced_for_grading"] is True
    assert form_context_builder.should_block_answer_updates(enriched[0]) is True


def test_low_confidence_invalid_expected_does_not_block_updates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(form_context_builder, "log", lambda *_args, **_kwargs: None)
    tmp_path.joinpath("config.json").write_text(
        '{"validate_expected_answers": true, "expected_answer_validator_min_confidence": 0.85}',
        encoding="utf-8",
    )

    import expected_answer_validator

    monkeypatch.setattr(
        expected_answer_validator,
        "validate_expected_answer",
        lambda _context, expected: {
            "validation_status": "ok",
            "valid": False,
            "confidence": 0.40,
            "suggested_answers": ["-16"],
            "reason": "Ambiguous context.",
            "original_expected": expected,
        },
    )

    form_data = {
        "items": [
            {
                "itemId": "item-a",
                "title": "2a",
                "questionItem": {
                    "question": {
                        "questionId": "qid-a",
                        "textQuestion": {},
                        "grading": {"correctAnswers": {"answers": [{"value": "16"}]}},
                    }
                },
            }
        ]
    }
    structure = [
        {"itemId": "item-a", "questionId": "qid-a", "index": 1, "title": "2a", "description": "", "type": "SHORT_ANSWER"},
    ]

    context = build_form_context("form123", "Integers", form_data, structure, {"item-a": ["16"]})
    enriched = apply_question_context(structure, context)

    assert enriched[0]["effective_expected"] == ["16"]
    assert enriched[0]["expected_was_replaced_for_grading"] is False
    assert form_context_builder.should_block_answer_updates(enriched[0]) is False


def test_validator_normalizes_low_confidence_invalid_to_uncertain_valid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tmp_path.joinpath("config.json").write_text(
        '{"expected_answer_validator_min_confidence": 0.85}',
        encoding="utf-8",
    )

    import expected_answer_validator

    result = expected_answer_validator._normalize_validation(
        {
            "valid": False,
            "confidence": 0.0,
            "suggested_answers": ["-16"],
            "reason": "Ambiguous mapping.",
        },
        "deepseek-r1:8b",
        ["16"],
    )

    assert result["valid"] is True
    assert result["low_confidence_invalid_ignored"] is True


def test_validator_can_use_plain_text_model_response():
    import expected_answer_validator

    parsed = expected_answer_validator._text_to_validation(
        "The teacher-provided expected answer is correct. The calculation matches the textbook prompt."
    )

    assert parsed["valid"] is True
    assert parsed["confidence"] == 0.5


def test_validator_caps_context_and_output_options(monkeypatch):
    import expected_answer_validator

    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": '{"valid": true, "confidence": 1, "suggested_answers": [], "reason": "ok"}'}}

    def post(_url, json, timeout):
        captured.update(json)
        return Response()

    monkeypatch.setattr(expected_answer_validator.requests, "post", post)
    result = expected_answer_validator._call_validator("llama3.1:8b", "Question", ["4"], 10, 2)
    assert result["valid"] is True
    assert captured["options"]["num_ctx"] == 8192
    assert captured["options"]["num_predict"] == 512


def test_question_map_adds_exact_textbook_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(form_context_builder, "log", lambda *_args, **_kwargs: None)
    tmp_path.joinpath("config.json").write_text(
        '{"enable_vision_context": true, "validate_expected_answers": false}',
        encoding="utf-8",
    )

    import vision_context

    monkeypatch.setattr(
        vision_context,
        "analyze_image_uri",
        lambda _uri: {
            "vision_status": "ok",
            "model_used": "test-vision",
            "context": {
                "summary": "Exercise 2 integer multiplication.",
                "question_map": {"2a": "-4 x 4", "2b": "5 x (-7)"},
            },
        },
    )

    form_data = {
        "items": [
            {"itemId": "text1", "title": "TEXTBOOK CONTEXT", "description": "Use these textbook images.", "textItem": {}},
            {
                "itemId": "img1",
                "title": "textbook page image 1",
                "imageItem": {"image": {"contentUri": "https://example.test/image.png"}},
            },
            {
                "itemId": "item-a",
                "title": "2a",
                "questionItem": {
                    "question": {
                        "questionId": "qid-a",
                        "textQuestion": {},
                        "grading": {"correctAnswers": {"answers": [{"value": "-16"}]}},
                    }
                },
            },
        ]
    }
    structure = [
        {"itemId": "item-a", "questionId": "qid-a", "index": 3, "title": "2a", "description": "", "type": "SHORT_ANSWER"},
    ]

    context = build_form_context("form123", "Integers", form_data, structure, {"item-a": ["-16"]})
    enriched = apply_question_context(structure, context)
    qtext = get_question_context(enriched[0])

    assert enriched[0]["mapped_textbook_question"] == "-4 x 4"
    assert "Mapped textbook question: -4 x 4" in qtext

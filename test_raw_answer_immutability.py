import evaluation_pipeline as ep
import worker_pipeline as wp
import global_dispatcher as gd
import global_prefetch
import response_utils
import updater


RAW_VARIANTS = [
    "5x - 5",
    "5x-5",
    " 5x - 5",
    "5x - 5 ",
    "5X - 5",
    "5x − 5",
    "5x\t -  5",
    "line 1\nline 2",
]


class _Executable:
    def __init__(self, result=None, callback=None):
        self.result = result
        self.callback = callback

    def execute(self):
        if self.callback:
            self.callback()
        return self.result


class _ResponsesApi:
    def __init__(self, payload):
        self.payload = payload

    def list(self, formId=None, pageToken=None):
        return _Executable(self.payload)


class _FormsApi:
    def __init__(self, answers=None, responses_payload=None):
        self.answers = list(answers or [])
        self.responses_payload = responses_payload or {}
        self.requests = []

    def get(self, formId=None):
        return _Executable(
            {
                "items": [
                    {
                        "itemId": "item-1",
                        "questionItem": {
                            "question": {
                                "questionId": "q1",
                                "textQuestion": {},
                                "grading": {
                                    "correctAnswers": {
                                        "answers": [{"value": value} for value in self.answers]
                                    },
                                    "pointValue": 1,
                                },
                            }
                        },
                    }
                ]
            }
        )

    def responses(self):
        return _ResponsesApi(self.responses_payload)

    def batchUpdate(self, formId=None, body=None):
        def apply():
            self.requests.append(body)
            submitted = body["requests"][0]["updateItem"]["item"]["questionItem"]["question"]
            self.answers = [
                item["value"]
                for item in submitted["grading"]["correctAnswers"]["answers"]
            ]

        return _Executable(callback=apply)


class _Service:
    def __init__(self, answers=None, responses_payload=None):
        self.api = _FormsApi(answers, responses_payload)

    def forms(self):
        return self.api


def _responses_payload(values):
    return {
        "responses": [
            {
                "answers": {
                    "q1": {
                        "textAnswers": {
                            "answers": [{"value": value} for value in values]
                        }
                    }
                }
            }
        ]
    }


def test_response_fetch_preserves_raw_google_form_values(monkeypatch):
    monkeypatch.setattr(response_utils, "log", lambda *_args, **_kwargs: None)
    service = _Service(responses_payload=_responses_payload(RAW_VARIANTS))

    assert response_utils.get_responses(service, "form-1", "q1") == RAW_VARIANTS


def test_global_prefetch_preserves_raw_google_form_values():
    responses = _responses_payload(RAW_VARIANTS)["responses"]

    assert global_prefetch._extract_answers_for_question(responses, "q1", False) == RAW_VARIANTS


def test_exact_fetch_dedup_does_not_normalize_spacing_case_or_symbols():
    answers = RAW_VARIANTS + ["5x - 5", "5x\t -  5"]

    assert gd.remove_exact_duplicate_answers(answers) == RAW_VARIANTS


def test_updater_writes_accepted_answers_to_google_forms_unchanged(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        updater,
        "load_config",
        lambda *args, **kwargs: {
            **__import__("evaluator_config").DEFAULT_CONFIG,
            "answer_key_auto_add_proven_equivalents": True,
            "answer_key_max_variants": 20,
        },
    )
    service = _Service(answers=["teacher"])

    updater.update_correct_answers(
        service,
        "form-1",
        "item-1",
        RAW_VARIANTS,
        question_index=0,
        trusted_expected=["teacher"],
        create_backup=False,
    )

    assert service.api.answers == ["teacher", *RAW_VARIANTS]
    submitted = service.api.requests[0]["requests"][0]["updateItem"]["item"]["questionItem"]["question"]
    written_values = [
        item["value"]
        for item in submitted["grading"]["correctAnswers"]["answers"]
    ]
    assert written_values == ["teacher", *RAW_VARIANTS]


def test_updater_duplicate_check_uses_exact_google_form_string_equality(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        updater,
        "load_config",
        lambda *args, **kwargs: {
            **__import__("evaluator_config").DEFAULT_CONFIG,
            "answer_key_auto_add_proven_equivalents": True,
            "answer_key_max_variants": 20,
        },
    )
    service = _Service(answers=["5x - 5"])

    updater.update_correct_answers(
        service,
        "form-1",
        "item-1",
        ["5x - 5", "5x-5", " 5x - 5", "5x - 5 "],
        question_index=0,
        trusted_expected=["5x - 5"],
        create_backup=False,
    )

    assert service.api.answers == ["5x - 5", "5x-5", " 5x - 5", "5x - 5 "]


def test_evaluation_cache_normalization_cannot_mutate_raw_answer(monkeypatch):
    monkeypatch.setattr(ep, "normalize", lambda _value: "same-normalized-key")
    ep.RESULT_CACHE.clear()
    question = "Simplify."
    expected = ["5x - 5"]
    cached = ep.EvaluationResult(
        answer="5x - 5",
        decision="YES",
        final_score=1.0,
        semantic_score=1.0,
        concept_score=1.0,
        factual_score=1.0,
        misconception_detected=False,
        misconception_description="",
        missing_concepts=[],
        accepted_concepts=[],
        model_agreement=1.0,
        confidence=1.0,
        fast_path_used=False,
        latency_ms=1.0,
        stage_reached="jury",
        evidence={"answer": "5x - 5", "key_eligible": True},
    )
    with ep.RESULT_CACHE_LOCK:
        ep.RESULT_CACHE[ep._cache_key("5x - 5", ep._qhash(question, expected))] = cached

    result = ep.evaluate_answer(" 5x - 5", expected, question)

    assert result.answer == " 5x - 5"
    assert result.raw_answer == " 5x - 5"
    assert result.evidence["answer"] == " 5x - 5"
    assert cached.answer == "5x - 5"


def test_model_first_cache_normalization_cannot_mutate_raw_answer(monkeypatch):
    monkeypatch.setattr(ep, "normalize", lambda _value: "same-normalized-key")
    ep.RESULT_CACHE.clear()
    question = "Simplify."
    expected = ["5x - 5"]
    cached = ep.EvaluationResult(
        answer="5x - 5",
        decision="YES",
        final_score=1.0,
        semantic_score=1.0,
        concept_score=1.0,
        factual_score=1.0,
        misconception_detected=False,
        misconception_description="",
        missing_concepts=[],
        accepted_concepts=[],
        model_agreement=1.0,
        confidence=1.0,
        fast_path_used=False,
        latency_ms=1.0,
        stage_reached="jury",
        evidence={"answer": "5x - 5", "key_eligible": True},
    )
    with ep.RESULT_CACHE_LOCK:
        ep.RESULT_CACHE[ep._cache_key("5x - 5", ep._qhash(question, expected))] = cached

    results = ep.evaluate_answers_model_first(["5x - 5", "5x-5"], expected, question)

    assert [result.answer for result in results] == ["5x - 5", "5x-5"]
    assert [result.evidence["answer"] for result in results] == ["5x - 5", "5x-5"]


def test_evaluate_answers_raw_mode_preserves_every_form_response(monkeypatch):
    monkeypatch.setattr(
        ep,
        "load_config",
        lambda *args, **kwargs: {
            **__import__("evaluator_config").DEFAULT_CONFIG,
            "enable_deduplication": False,
        },
    )
    calls = []

    def fake_evaluate_answer(answer, expected, question):
        calls.append(answer)
        return ep.EvaluationResult(
            answer=answer,
            decision="YES",
            final_score=1.0,
            semantic_score=1.0,
            concept_score=1.0,
            factual_score=1.0,
            misconception_detected=False,
            misconception_description="",
            missing_concepts=[],
            accepted_concepts=[],
            model_agreement=1.0,
            confidence=1.0,
            fast_path_used=False,
            latency_ms=1.0,
            stage_reached="test",
            evidence={"answer": answer, "key_eligible": True},
        )

    monkeypatch.setattr(ep, "evaluate_answer", fake_evaluate_answer)

    results = ep.evaluate_answers(["5x - 5", "5x-5", "5x - 5"], ["5x - 5"], "Simplify.")

    assert calls == ["5x - 5", "5x-5", "5x - 5"]
    assert [result.answer for result in results] == ["5x - 5", "5x-5", "5x - 5"]


def test_raw_cache_key_does_not_normalize_spacing(monkeypatch):
    monkeypatch.setattr(ep, "normalize", lambda _value: "same-normalized-key")
    qh = ep._qhash("Simplify.", ["5x - 5"])

    assert ep._cache_key("5x - 5", qh, deduplicate=True) == ep._cache_key(" 5x - 5", qh, deduplicate=True)
    assert ep._cache_key("5x - 5", qh, deduplicate=False) != ep._cache_key(" 5x - 5", qh, deduplicate=False)


def test_worker_pipeline_deduplicated_mode_expands_raw_answers(monkeypatch):
    monkeypatch.setattr(
        wp,
        "load_config",
        lambda *args, **kwargs: {
            **__import__("evaluator_config").DEFAULT_CONFIG,
            "enable_deduplication": True,
            "deterministic_worker_count": 1,
            "ai_worker_count": 1,
            "worker_queue_size": 100,
            "numeric_tolerance": 0.01,
        },
    )
    calls = []

    def fake_evaluate_answer(answer, expected, question):
        calls.append(answer)
        return ep.EvaluationResult(
            answer=answer,
            decision="YES",
            final_score=1.0,
            semantic_score=1.0,
            concept_score=1.0,
            factual_score=1.0,
            misconception_detected=False,
            misconception_description="",
            missing_concepts=[],
            accepted_concepts=[],
            model_agreement=1.0,
            confidence=1.0,
            fast_path_used=False,
            latency_ms=1.0,
            stage_reached="test",
            evidence={"answer": answer, "key_eligible": True},
        )

    monkeypatch.setattr(wp, "evaluate_answer", fake_evaluate_answer)

    results = wp.evaluate_answers_worker_pipeline(["foo", " foo ", "foo"], ["bar"], "Question?")

    assert calls == ["foo"]
    assert [result.answer for result in results] == ["foo", " foo ", "foo"]


def test_worker_pipeline_raw_mode_preserves_duplicate_work(monkeypatch):
    monkeypatch.setattr(
        wp,
        "load_config",
        lambda *args, **kwargs: {
            **__import__("evaluator_config").DEFAULT_CONFIG,
            "enable_deduplication": False,
            "deterministic_worker_count": 1,
            "ai_worker_count": 1,
            "worker_queue_size": 100,
            "numeric_tolerance": 0.01,
        },
    )
    calls = []

    def fake_evaluate_answer(answer, expected, question):
        calls.append(answer)
        return ep.EvaluationResult(
            answer=answer,
            decision="YES",
            final_score=1.0,
            semantic_score=1.0,
            concept_score=1.0,
            factual_score=1.0,
            misconception_detected=False,
            misconception_description="",
            missing_concepts=[],
            accepted_concepts=[],
            model_agreement=1.0,
            confidence=1.0,
            fast_path_used=False,
            latency_ms=1.0,
            stage_reached="test",
            evidence={"answer": answer, "key_eligible": True},
        )

    monkeypatch.setattr(wp, "evaluate_answer", fake_evaluate_answer)

    results = wp.evaluate_answers_worker_pipeline(["foo", " foo ", "foo"], ["bar"], "Question?")

    assert calls == ["foo", " foo ", "foo"]
    assert [result.answer for result in results] == ["foo", " foo ", "foo"]

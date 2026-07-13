import time
import types

import global_dispatcher as gd


def test_exact_duplicates_are_removed_after_fetch_in_first_seen_order():
    answers = ["7", "7", "9 - 2 = 7", "7", "9 - 2 = 7", " 7 "]

    assert gd.remove_exact_duplicate_answers(answers) == ["7", "9 - 2 = 7", " 7 "]


def test_strict_dedup_keeps_unicode_minus_distinct_from_ascii_hyphen():
    answers = ["5x - 5", "5x − 5", "5x - 5"]

    assert gd.remove_exact_duplicate_answers(answers) == ["5x - 5", "5x − 5"]


def test_strict_dedup_keeps_spacing_case_and_symbol_variants_separate():
    answers = ["5x - 5", "5x-5", "5x - 5 ", "5X - 5", "5x − 5", "5x - 5"]

    assert gd.remove_exact_duplicate_answers(answers) == [
        "5x - 5", "5x-5", "5x - 5 ", "5X - 5", "5x − 5"
    ]


def test_missing_answer_key_questions_only_flags_answered_questions():
    structure = [
        {"questionId": "q1", "itemId": "item1", "index": 0, "title": "Answered missing key"},
        {"questionId": "q2", "itemId": "item2", "index": 1, "title": "Answered keyed"},
        {"questionId": "q3", "itemId": "item3", "index": 2, "title": "Unanswered missing key"},
    ]

    missing = gd.missing_answer_key_questions(
        structure,
        {"item1": [""], "item2": ["7"], "item3": []},
        {"q1": ["student"], "q2": ["7"], "q3": []},
    )

    assert missing == [{
        "question_id": "q1",
        "question_number": 1,
        "title": "Answered missing key",
        "responses": 1,
    }]


class _FakeReq:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeFormsResponsesApi:
    def __init__(self, responses_payload):
        self._responses_payload = responses_payload

    def list(self, formId=None, pageToken=None):
        return _FakeReq(self._responses_payload)


class _FakeFormsApi:
    def __init__(self, form_payload, responses_payload):
        self._form_payload = form_payload
        self._responses_payload = responses_payload

    def get(self, formId=None):
        return _FakeReq(self._form_payload)

    def responses(self):
        return _FakeFormsResponsesApi(self._responses_payload)


class _FakeService:
    def __init__(self, form_payload, responses_payload):
        self._forms_api = _FakeFormsApi(form_payload, responses_payload)

    def forms(self):
        return self._forms_api


def test_task_builder_supplies_work_without_starving(monkeypatch, capsys):
    # 4 questions x 120 responses => enough volume to exercise buffering/refill.
    structure = []
    items = []
    qids = []
    for i in range(4):
        qid = f"q{i+1}"
        item_id = f"item{i+1}"
        qids.append(qid)
        structure.append({"questionId": qid, "itemId": item_id, "index": i, "type": "SHORT_ANSWER", "title": f"Q{i+1}"})
        items.append(
            {
                "itemId": item_id,
                "questionItem": {
                    "question": {
                        "grading": {"correctAnswers": {"answers": [{"value": f"a{i+1}"}]}}
                    }
                },
            }
        )

    responses = []
    for ridx in range(120):
        ans = {}
        for qid in qids:
            ans[qid] = {"textAnswers": {"answers": [{"value": f"resp-{qid}-{ridx}"}]}}
        responses.append({"answers": ans})

    form_payload = {"info": {"title": "Synthetic Form"}, "items": items}
    responses_payload = {"responses": responses}
    fake_service = _FakeService(form_payload, responses_payload)

    logs = []
    update_calls = []

    monkeypatch.setattr(gd, "get_service", lambda: fake_service)
    monkeypatch.setattr(gd, "get_form_structure", lambda service, form_id: structure)
    monkeypatch.setattr(gd, "generate_form_feedback", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "save_grading_time", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "update_correct_answers", lambda *args, **kwargs: update_calls.append(1))
    monkeypatch.setattr(gd, "log", lambda level, msg: logs.append((level, msg)))

    # Force deterministic fast path so test focuses on producer/task-builder throughput.
    det_result = types.SimpleNamespace(accepted=True, confidence=1.0)
    def deterministic_check(answer, *_args, **_kwargs):
        if "-q1-" not in answer:
            time.sleep(0.001)
        return det_result
    monkeypatch.setattr(gd, "run_deterministic_checks", deterministic_check)

    monkeypatch.setattr(
        gd,
        "load_config",
        lambda: {
            "global_prefetch_workers": 2,
            "deterministic_worker_count": 4,
            "ai_worker_count": 2,
            "max_latency_per_answer_seconds": 5,
            "forms_expensive_reads_per_minute": 6000,
            "dispatcher_stall_timeout_seconds": 30,
            "worker_queue_size": 500,
            "producer_det_queue_high_watermark": 420,
            "producer_det_queue_low_watermark": 180,
            "numeric_tolerance": 0.01,
            "enable_form_context": False,
        },
    )

    gd.run_global_dispatcher(
        form_urls=["https://docs.google.com/forms/d/fake_form_1/viewform"],
        grade_recent_only=False,
        generate_report=False,
    )

    emitted_lines = capsys.readouterr().out.splitlines()
    progress_lines = [
        line for line in emitted_lines
        if line.startswith("FormProgress:")
    ]
    assert progress_lines[0] == "FormProgress: 0/480"
    assert progress_lines[-1] == "FormProgress: 480/480"
    completed = [int(line.split()[1].split("/", 1)[0]) for line in progress_lines]
    assert completed == sorted(completed)
    assert len(set(completed)) > 100
    metric_lines = [
        line for line in emitted_lines
        if line.startswith("FormMetrics:")
    ]
    assert metric_lines
    # Accepted answer-key audit variants are not "Needs review" questions.
    assert metric_lines[-1].split()[1:4] == ["480/480", "480", "0"]
    review_ready = [i for i, line in enumerate(emitted_lines) if line.startswith("QuestionAvailableForReview:")]
    assert len(review_ready) == 4
    assert review_ready[0] < max(i for i, line in enumerate(emitted_lines) if line == "FormProgress: 480/480")

    # Form has 4 short-answer questions and all deterministic checks accepted.
    assert len(update_calls) == 4
    metrics_lines = [m for _, m in logs if "[DISPATCH METRICS]" in m]
    assert metrics_lines, "Expected dispatch metrics logs to be emitted"


def test_model_first_batching_evaluates_question_answers_together(monkeypatch, capsys):
    structure = [{"questionId": "q1", "itemId": "item1", "index": 0, "type": "SHORT_ANSWER", "title": "Q1"}]
    form_payload = {
        "info": {"title": "Batch Form"},
        "items": [{
            "itemId": "item1",
            "questionItem": {
                "question": {
                    "grading": {"correctAnswers": {"answers": [{"value": "7"}]}}
                }
            },
        }],
    }
    responses_payload = {
        "responses": [
            {"answers": {"q1": {"textAnswers": {"answers": [{"value": "7"}]}}}},
            {"answers": {"q1": {"textAnswers": {"answers": [{"value": "7"}]}}}},
            {"answers": {"q1": {"textAnswers": {"answers": [{"value": " 7 "}]}}}},
        ]
    }
    fake_service = _FakeService(form_payload, responses_payload)
    calls = []

    monkeypatch.setattr(gd, "get_service", lambda: fake_service)
    monkeypatch.setattr(gd, "get_form_structure", lambda service, form_id: structure)
    monkeypatch.setattr(gd, "generate_form_feedback", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "save_grading_time", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "update_correct_answers", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "enqueue_review", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "log", lambda *args, **kwargs: None)

    def fake_evaluate_answers_model_first(answers, expected, question):
        calls.append((list(answers), list(expected), question))
        return [
            gd.EvaluationResult(
                answer=answer,
                decision="NO",
                final_score=0.0,
                semantic_score=0.0,
                concept_score=0.0,
                factual_score=0.0,
                misconception_detected=False,
                misconception_description="",
                missing_concepts=[],
                accepted_concepts=[],
                model_agreement=1.0,
                confidence=1.0,
                fast_path_used=False,
                latency_ms=1.0,
                stage_reached="jury",
                evidence={"key_eligible": False},
            )
            for answer in answers
        ]

    monkeypatch.setattr(gd, "evaluate_answers_model_first", fake_evaluate_answers_model_first)
    monkeypatch.setattr(
        gd,
        "load_config",
        lambda: {
            "global_prefetch_workers": 1,
            "ai_worker_count": 1,
            "model_first_question_batching": True,
            "force_ai_jury_for_all_answers": True,
            "max_latency_per_answer_seconds": 5,
            "forms_expensive_reads_per_minute": 6000,
            "dispatcher_stall_timeout_seconds": 30,
            "worker_queue_size": 100,
            "enable_form_context": False,
            "patient_ai_mode": True,
        },
    )

    gd.run_global_dispatcher(
        form_urls=["https://docs.google.com/forms/d/fake_form_1/viewform"],
        grade_recent_only=False,
        generate_report=False,
    )

    assert len(calls) == 1
    assert calls[0][0] == ["7", " 7 "]
    emitted_lines = capsys.readouterr().out.splitlines()
    assert "FormProgress: 2/2" in emitted_lines


def test_model_first_batching_raw_mode_keeps_duplicate_form_answers(monkeypatch, capsys):
    structure = [{"questionId": "q1", "itemId": "item1", "index": 0, "type": "SHORT_ANSWER", "title": "Q1"}]
    form_payload = {
        "info": {"title": "Raw Batch Form"},
        "items": [{
            "itemId": "item1",
            "questionItem": {
                "question": {
                    "grading": {"correctAnswers": {"answers": [{"value": "7"}]}}
                }
            },
        }],
    }
    responses_payload = {
        "responses": [
            {"answers": {"q1": {"textAnswers": {"answers": [{"value": "7"}]}}}},
            {"answers": {"q1": {"textAnswers": {"answers": [{"value": "7"}]}}}},
            {"answers": {"q1": {"textAnswers": {"answers": [{"value": " 7 "}]}}}},
        ]
    }
    fake_service = _FakeService(form_payload, responses_payload)
    calls = []

    monkeypatch.setattr(gd, "get_service", lambda: fake_service)
    monkeypatch.setattr(gd, "get_form_structure", lambda service, form_id: structure)
    monkeypatch.setattr(gd, "generate_form_feedback", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "save_grading_time", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "update_correct_answers", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "enqueue_review", lambda *args, **kwargs: None)
    monkeypatch.setattr(gd, "log", lambda *args, **kwargs: None)

    def fake_evaluate_answers_model_first(answers, expected, question):
        calls.append((list(answers), list(expected), question))
        return [
            gd.EvaluationResult(
                answer=answer,
                decision="NO",
                final_score=0.0,
                semantic_score=0.0,
                concept_score=0.0,
                factual_score=0.0,
                misconception_detected=False,
                misconception_description="",
                missing_concepts=[],
                accepted_concepts=[],
                model_agreement=1.0,
                confidence=1.0,
                fast_path_used=False,
                latency_ms=1.0,
                stage_reached="jury",
                evidence={"key_eligible": False},
            )
            for answer in answers
        ]

    monkeypatch.setattr(gd, "evaluate_answers_model_first", fake_evaluate_answers_model_first)
    monkeypatch.setattr(
        gd,
        "load_config",
        lambda: {
            "global_prefetch_workers": 1,
            "ai_worker_count": 1,
            "model_first_question_batching": True,
            "force_ai_jury_for_all_answers": True,
            "enable_deduplication": False,
            "max_latency_per_answer_seconds": 5,
            "forms_expensive_reads_per_minute": 6000,
            "dispatcher_stall_timeout_seconds": 30,
            "worker_queue_size": 100,
            "enable_form_context": False,
            "patient_ai_mode": True,
        },
    )

    gd.run_global_dispatcher(
        form_urls=["https://docs.google.com/forms/d/fake_form_1/viewform"],
        grade_recent_only=False,
        generate_report=False,
    )

    assert len(calls) == 1
    assert calls[0][0] == ["7", "7", " 7 "]
    emitted_lines = capsys.readouterr().out.splitlines()
    assert "FormProgress: 3/3" in emitted_lines


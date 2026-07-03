import time
import types

import global_dispatcher as gd


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
    assert metric_lines[-1].split()[1:4] == ["480/480", "480", "4"]
    review_ready = [i for i, line in enumerate(emitted_lines) if line.startswith("QuestionAvailableForReview:")]
    assert len(review_ready) == 4
    assert review_ready[0] < max(i for i, line in enumerate(emitted_lines) if line == "FormProgress: 480/480")

    # Form has 4 short-answer questions and all deterministic checks accepted.
    assert len(update_calls) == 4
    metrics_lines = [m for _, m in logs if "[DISPATCH METRICS]" in m]
    assert metrics_lines, "Expected dispatch metrics logs to be emitted"


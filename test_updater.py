import updater


class _Executable:
    def __init__(self, result=None, callback=None):
        self.result = result
        self.callback = callback

    def execute(self):
        if self.callback:
            self.callback()
        return self.result


class _Forms:
    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def get(self, formId):
        return _Executable(
            {
                "items": [
                    {
                        "itemId": "item-1",
                        "questionItem": {
                            "question": {
                                "questionId": "item-1",
                                "textQuestion": {},
                                "grading": {
                                    "correctAnswers": {
                                        "answers": [{"value": value} for value in self.answers]
                                    },
                                    "pointValue": 3,
                                },
                            }
                        },
                    }
                ]
            }
        )

    def batchUpdate(self, formId, body):
        def apply():
            self.requests.append(body)
            submitted = body["requests"][0]["updateItem"]["item"]["questionItem"]["question"]
            self.answers = [a["value"] for a in submitted["grading"]["correctAnswers"]["answers"]]

        return _Executable(callback=apply)


class _Service:
    def __init__(self, answers):
        self.api = _Forms(answers)

    def forms(self):
        return self.api


def test_update_payload_is_clean_safe_and_idempotent(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    service = _Service(["-13", "-13", "13"])

    updater.update_correct_answers(
        service,
        "form-1",
        "item-1",
        ["-13", "-13", "-  13", "12.999", "a plausible AI answer"],
        0,
        ["-13"],
        create_backup=False,
    )

    # Add-first workflow preserves existing variants and appends graded candidates;
    # domain validation upstream decides which candidates reach this updater.
    assert service.api.answers == ["-13", "13", "- 13", "12.999", "a plausible AI answer"]
    assert len(service.api.requests) == 1
    submitted_grading = service.api.requests[0]["requests"][0]["updateItem"]["item"]["questionItem"]["question"]["grading"]
    assert submitted_grading["pointValue"] == 3

    updater.update_correct_answers(
        service, "form-1", "item-1", ["-13", "- 13"], 0, ["-13"], create_backup=False
    )
    assert len(service.api.requests) == 1


def test_update_is_blocked_without_trusted_teacher_answer(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    service = _Service(["possibly wrong"])

    updater.update_correct_answers(
        service, "form-1", "item-1", ["new answer"], 0, [], create_backup=False
    )

    assert service.api.answers == ["possibly wrong"]
    assert service.api.requests == []


def test_dry_run_reports_plan_without_backup_or_update(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        updater,
        "backup_form_grading",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("backup should not run")),
    )
    service = _Service(["-13", "-13", "13"])

    updater.update_correct_answers(
        service, "form-1", "item-1", ["-13"], 0, ["-13"], dry_run=True
    )

    assert service.api.answers == ["-13", "-13", "13"]
    assert service.api.requests == []


def test_real_update_creates_backup_first(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    events = []
    updater._BACKED_UP_FORMS.clear()
    monkeypatch.setattr(updater, "backup_form_grading", lambda *_args, **_kwargs: events.append("backup"))
    service = _Service(["-13", "-13"])
    original_batch = service.api.batchUpdate

    def batch(formId, body):
        events.append("update")
        return original_batch(formId, body)

    service.api.batchUpdate = batch
    updater.update_correct_answers(service, "form-1", "item-1", ["-13"], 0, ["-13"])
    assert events == ["backup", "update"]


def test_only_one_pristine_backup_is_created_per_form_run(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    updater._BACKED_UP_FORMS.clear()
    backups = []
    monkeypatch.setattr(updater, "backup_form_grading", lambda *_args, **_kwargs: backups.append("saved"))
    service = _Service(["-13", "-13"])

    updater.update_correct_answers(service, "form-2", "item-1", ["-13"], 0, ["-13"])
    service.api.answers = ["-13", "-13"]
    updater.update_correct_answers(service, "form-2", "item-1", ["-13"], 0, ["-13"])

    assert backups == ["saved"]


def test_uncertain_existing_text_is_queued_for_review_and_not_preserved(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    queued = []
    monkeypatch.setattr(updater, "enqueue_review", lambda record: queued.append(record))
    service = _Service(["photosynthesis", "plants use sunlight to make food", "photosynthesis"])

    updater.update_correct_answers(
        service,
        "form-3",
        "item-1",
        ["photosynthesis"],
        0,
        ["photosynthesis"],
        create_backup=False,
    )

    assert service.api.answers == ["photosynthesis", "plants use sunlight to make food"]
    assert queued == []  # Existing variants remain until the teacher edits/deletes them in review.


def test_manual_approval_can_remove_uncertain_existing_text(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    service = _Service(["photosynthesis", "plants use sunlight to make food"])

    updater.update_correct_answers(
        service,
        "form-4",
        "item-1",
        ["photosynthesis"],
        0,
        ["photosynthesis"],
        create_backup=False,
        manual_approval=True,
    )

    assert service.api.answers == ["photosynthesis"]


def test_manual_approval_can_add_uncertain_candidate(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "enqueue_review", lambda *_args, **_kwargs: None)
    service = _Service(["photosynthesis"])
    updater.update_correct_answers(
        service, "form-5", "item-1",
        ["photosynthesis", "plants use sunlight to make food"], 0,
        ["photosynthesis"], create_backup=False, manual_approval=True,
    )
    assert service.api.answers == ["photosynthesis", "plants use sunlight to make food"]


def test_ai_added_variant_is_written_then_queued_for_audit(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    queued = []
    monkeypatch.setattr(updater, "enqueue_review", lambda record: queued.append(record))
    service = _Service(["photosynthesis"])

    updater.update_correct_answers(
        service, "form-6", "item-1",
        ["plants use sunlight to make food"], 0,
        ["photosynthesis"], create_backup=False,
    )

    assert service.api.answers == ["photosynthesis", "plants use sunlight to make food"]
    assert queued[0]["route"] == "ai_added_to_form"
    assert queued[0]["added_to_form"] is True

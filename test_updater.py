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
    service = _Service(["-13", "-13", "13"])

    updater.update_correct_answers(
        service,
        "form-1",
        "item-1",
        ["-13", "-13", "-  13", "12.999", "a plausible AI answer"],
        0,
        ["-13"],
    )

    assert service.api.answers == ["-13", "- 13"]
    assert len(service.api.requests) == 1
    submitted_grading = service.api.requests[0]["requests"][0]["updateItem"]["item"]["questionItem"]["question"]["grading"]
    assert submitted_grading["pointValue"] == 3

    updater.update_correct_answers(
        service, "form-1", "item-1", ["-13", "- 13"], 0, ["-13"]
    )
    assert len(service.api.requests) == 1


def test_update_is_blocked_without_trusted_teacher_answer(monkeypatch):
    monkeypatch.setattr(updater, "log", lambda *_args, **_kwargs: None)
    service = _Service(["possibly wrong"])

    updater.update_correct_answers(
        service, "form-1", "item-1", ["new answer"], 0, []
    )

    assert service.api.answers == ["possibly wrong"]
    assert service.api.requests == []

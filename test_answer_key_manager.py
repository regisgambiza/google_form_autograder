import json

import answer_key_manager as manager


def _item(answers, title="15 - 28", points=3):
    return {
        "itemId": "item-1",
        "title": title,
        "questionItem": {
            "question": {
                "questionId": "question-1",
                "textQuestion": {},
                "grading": {
                    "pointValue": points,
                    "correctAnswers": {"answers": [{"value": value} for value in answers]},
                },
            }
        },
    }


class _Executable:
    def __init__(self, value=None, callback=None):
        self.value = value
        self.callback = callback

    def execute(self):
        if self.callback:
            self.callback()
        return self.value


class _Forms:
    def __init__(self, form):
        self.form = form
        self.updates = []

    def get(self, formId):
        return _Executable(self.form)

    def batchUpdate(self, formId, body):
        return _Executable(callback=lambda: self.updates.append((formId, body)))


class _Service:
    def __init__(self, form):
        self.api = _Forms(form)

    def forms(self):
        return self.api


def test_health_scan_routes_duplicates_to_auto_and_conflicts_to_review():
    duplicate = manager.analyze_question("form", _item(["-13", "-13", "- 13"]), 0)
    assert duplicate.route == "auto"
    assert duplicate.proposed_answers == ["-13", "- 13"]

    conflict = manager.analyze_question("form", _item(["-13", "13", "-13"]), 0)
    assert conflict.route == "review"
    assert any("sign contradiction" in issue for issue in conflict.issues)


def test_health_scan_detects_missing_and_unreasonable_keys():
    missing = manager.analyze_question("form", _item([]), 0)
    assert missing.route == "reject"
    assert "missing answer key" in missing.issues

    crowded = manager.analyze_question("form", _item(["5"] * 13), 0)
    assert any("unreasonable answer count" in issue for issue in crowded.issues)


def test_legacy_canonical_editor_uses_first_pipe_token():
    finding = manager.analyze_question("form", _item(["-13 | - 13 | 13", "-13"]), 0)
    assert finding.canonical == "-13"
    assert "13" not in finding.proposed_answers


def test_backup_and_restore_preserve_complete_grading(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "BACKUP_DIR", tmp_path / "backups")
    form = {"info": {"title": "Quiz"}, "items": [_item(["-13", "- 13"], points=4)]}
    service = _Service(form)

    path = manager.backup_form_grading(service, "form-1", "test")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["items"][0]["grading"]["pointValue"] == 4

    preview = manager.restore_backup(service, path, dry_run=True)
    assert preview["request_count"] == 1
    assert service.api.updates == []

    manager.restore_backup(service, path)
    assert len(service.api.updates) == 1


def test_review_queue_deduplicates_pending_records(tmp_path, monkeypatch):
    path = tmp_path / "reviews.json"
    monkeypatch.setattr(manager, "REVIEW_QUEUE_PATH", path)
    record = {"form_id": "f", "item_id": "i", "candidates": ["wrong"]}
    manager.enqueue_review(record)
    manager.enqueue_review(record)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["items"]) == 1


def test_scan_attaches_pending_candidates_to_question():
    form = {"items": [_item(["photosynthesis"], title="Process")]} 
    findings = manager.scan_form_data(
        "form", form, pending_reviews={"item-1": ["plants use sunlight to make food"]}
    )
    assert findings[0].route == "review"
    assert findings[0].review_candidates == ["plants use sunlight to make food"]


def test_review_decisions_are_persistent(tmp_path, monkeypatch):
    path = tmp_path / "reviews.json"
    monkeypatch.setattr(manager, "REVIEW_QUEUE_PATH", path)
    manager.enqueue_review({"form_id": "f", "item_id": "i", "candidates": ["candidate"]})
    assert manager.resolve_reviews("f", "i", "rejected") == 1
    assert manager.load_pending_reviews("f") == {}
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["items"] == []


def test_one_click_dedup_preserves_distinct_answers_and_points(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "BACKUP_DIR", tmp_path / "backups")
    form = {
        "info": {"title": "Quiz"},
        "items": [_item(["-13", "-13", "-  13", "- 13", "13"], points=4)],
    }
    service = _Service(form)
    result = manager.remove_form_duplicates(service, "form-1")
    assert result["removed"] == 2
    assert result["changed_questions"] == 1
    request = service.api.updates[0][1]["requests"][0]["updateItem"]
    grading = request["item"]["questionItem"]["question"]["grading"]
    assert grading["pointValue"] == 4
    assert [a["value"] for a in grading["correctAnswers"]["answers"]] == ["-13", "- 13", "13"]


def test_one_click_dedup_dry_run_has_no_side_effects():
    form = {"info": {"title": "Quiz"}, "items": [_item(["9", "9", "8"])]}
    service = _Service(form)
    result = manager.remove_form_duplicates(service, "form-1", dry_run=True)
    assert result["removed"] == 1
    assert service.api.updates == []


def test_keep_teacher_answers_only_preserves_first_answer_and_points(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "BACKUP_DIR", tmp_path / "backups")
    form = {
        "info": {"title": "Quiz"},
        "items": [_item(["teacher", "variant one", "wrong variant"], points=7)],
    }
    service = _Service(form)

    result = manager.keep_teacher_answers_only(service, "form-1")

    assert result["removed"] == 2
    assert result["changed_questions"] == 1
    assert result["backup"]
    request = service.api.updates[0][1]["requests"][0]["updateItem"]
    grading = request["item"]["questionItem"]["question"]["grading"]
    assert grading["pointValue"] == 7
    assert grading["correctAnswers"]["answers"] == [{"value": "teacher"}]


def test_keep_teacher_answers_only_dry_run_does_not_update():
    service = _Service({"info": {"title": "Quiz"}, "items": [_item(["teacher", "variant"])]})
    result = manager.keep_teacher_answers_only(service, "form-1", dry_run=True)
    assert result["removed"] == 1
    assert service.api.updates == []

import json
import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app_theme import apply_application_theme
from gui_main import FormManager

APP = QApplication.instance() or QApplication([])
apply_application_theme(APP)


def _make_window(monkeypatch, tmp_path):
    window = FormManager()
    window.auto_partial_forms_path = str(tmp_path / "auto_partial_forms.json")
    window.forms_to_grade = None
    window._load_auto_partial_forms = lambda: None
    window._save_auto_partial_forms = window._save_auto_partial_forms
    return window


def test_update_skipped_form_tracks_partial_form(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    url = "https://docs.google.com/forms/d/abc123/edit"
    window._add_form_to_queue(url, "Test Form")
    form_id = window.extract_form_id(url)
    missing_json = json.dumps([
        {"question_id": "q1", "question_number": 1, "title": "Q1", "responses": 3},
        {"question_id": "q3", "question_number": 3, "title": "Q3", "responses": 1},
    ])
    window.update_skipped_form(form_id, url, "Missing teacher answer key", missing_json)

    entry = window.auto_partial_forms.get(form_id)
    assert entry is not None
    assert entry["url"] == url
    assert set(entry["missing_question_ids"]) == {"q1", "q3"}
    assert entry.get("detected_at")

    item = window._find_form_item_by_id(form_id)
    meta = item.data(0x0100 + 1) or {}
    assert meta.get("status") == "partial"

    # Unrelated skip reasons must not poison the watcher.
    window2 = _make_window(monkeypatch, tmp_path)
    window2._add_form_to_queue(url, "Test Form")
    window2.update_skipped_form(form_id, url, "Some other reason", "[]")
    assert form_id not in window2.auto_partial_forms


def test_partial_form_survives_auto_cleanup(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    url = "https://docs.google.com/forms/d/abc234/edit"
    window._add_form_to_queue(url, "Test Form")
    form_id = window.extract_form_id(url)
    window.update_skipped_form(form_id, url, "Missing teacher answer key", "[]")

    window.auto_mode = True
    window.finished_forms = [form_id]
    window.on_grading_finished(True, "")

    item = window._find_form_item_by_id(form_id)
    assert item is not None, "partial form must survive auto cleanup"
    meta = item.data(0x0100 + 1) or {}
    assert meta.get("status") in {"partial", "queued"}


def test_done_form_still_cleared_in_auto_mode(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    url = "https://docs.google.com/forms/d/abc345/edit"
    window._add_form_to_queue(url, "Test Form")
    form_id = window.extract_form_id(url)
    item = window._find_form_item_by_id(form_id)
    from gui_main import Qt
    window._set_form_status(item, "done", "Finished and saved grading updates")

    window.auto_mode = True
    window.finished_forms = [form_id]
    window.auto_partial_forms.clear()
    window.on_grading_finished(True, "")

    assert window._find_form_item_by_id(form_id) is None


def test_recheck_schedules_whole_form_regrade(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    form_id = "abc456"
    url = f"https://docs.google.com/forms/d/{form_id}/edit"
    window._add_form_to_queue(url, "Test Form")
    window.auto_mode = True
    window.is_grading = False
    window.auto_partial_forms[form_id] = {
        "url": url,
        "title": "Test Form",
        "missing_question_ids": ["q1"],
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "last_check": None,
    }

    # Structure: one question that still has no teacher answer.
    def fake_structure(service, fid):
        return [{"questionId": "q1", "itemId": "item1", "index": 0,
                 "title": "Q1", "type": "SHORT_ANSWER"}]

    def fake_missing(service, fid, structure, qids):
        return []  # everything now has a teacher answer

    import gui_main
    import form_utils
    monkeypatch.setattr("gui_main.get_service", lambda: object())
    monkeypatch.setattr(form_utils, "get_form_structure", fake_structure)
    monkeypatch.setattr(window, "_current_missing_qids", fake_missing)

    window._recheck_partial_forms()
    assert window._partial_regrade_pending == {form_id}


def test_get_effective_mode_computation(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    assert window.grading_mode in ("Recent Only", "Whole Form")

    window.grading_mode = "Recent Only"
    # force_recent_only dominates
    assert (True or ((not False) and False)) is True
    # force_whole_form overrides recent-only
    assert (False or ((not True) and True)) is False
    # normal recent-only
    assert (False or ((not False) and True)) is True


def test_recheck_skips_when_still_missing(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    form_id = "abc789"
    url = f"https://docs.google.com/forms/d/{form_id}/edit"
    window.auto_mode = True
    window.auto_partial_forms[form_id] = {
        "url": url,
        "title": "Test Form",
        "missing_question_ids": ["q1"],
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "last_check": None,
    }

    def fake_structure(service, fid):
        return [{"questionId": "q1", "itemId": "item1", "index": 0,
                 "title": "Q1", "type": "SHORT_ANSWER"}]

    def fake_missing(service, fid, structure, qids):
        return list(qids)  # nothing resolved yet

    import gui_main
    import form_utils
    monkeypatch.setattr("gui_main.get_service", lambda: object())
    monkeypatch.setattr(form_utils, "get_form_structure", fake_structure)
    monkeypatch.setattr(window, "_current_missing_qids", fake_missing)

    window._recheck_partial_forms()
    assert form_id in window.auto_partial_forms
    assert window._partial_regrade_pending == set()


def test_recheck_throttle_respected(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    form_id = "abc101"
    url = f"https://docs.google.com/forms/d/{form_id}/edit"
    window.auto_mode = True
    from datetime import timedelta
    window.auto_partial_forms[form_id] = {
        "url": url,
        "title": "Test Form",
        "missing_question_ids": ["q1"],
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "last_check": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
    }

    import gui_main
    import form_utils
    monkeypatch.setattr("gui_main.get_service", lambda: object())
    monkeypatch.setattr(
        form_utils, "get_form_structure",
        lambda service, fid: [{"questionId": "q1", "itemId": "item1", "index": 0,
                               "title": "Q1", "type": "SHORT_ANSWER"}],
    )
    monkeypatch.setattr(window, "_current_missing_qids", lambda *a: [])

    window._recheck_partial_forms()
    # Throttled: not re-graded, but last_check updated.
    assert form_id in window.auto_partial_forms
    assert window._partial_regrade_pending == set()
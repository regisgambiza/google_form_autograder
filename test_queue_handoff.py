import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from gui_studio.main_window import AutograderWindow

# Ensure a QApplication exists for widget construction
APP = QApplication.instance() or QApplication([])


def test_start_next_form_after_finish(monkeypatch):
    window = AutograderWindow()
    # Ensure clean state
    window.form_list.clear()
    window.forms_data.clear()

    url1 = "https://docs.google.com/forms/d/a/edit"
    url2 = "https://docs.google.com/forms/d/b/edit"

    item1 = window._add_form_to_queue(url1, "Form A", source="Test")
    item2 = window._add_form_to_queue(url2, "Form B", source="Test")

    # Sanity: both queued
    meta1 = item1.data(0x0100 + 1) or {}
    meta2 = item2.data(0x0100 + 1) or {}
    assert meta1.get("status", "queued") == "queued"
    assert meta2.get("status", "queued") == "queued"

    calls = {}

    def fake_run_grader(target_urls=None, force_recent_only=False):
        calls['called'] = True
        calls['target_urls'] = list(target_urls or [])
        # Simulate setting grading active
        window.is_grading = True

    # Make timers run immediately in the test environment
    monkeypatch.setattr(QTimer, "singleShot", lambda _ms, fn: fn())
    monkeypatch.setattr(window, "run_grader", fake_run_grader)

    # Simulate finishing the first form
    form_id_a = window.extract_form_id(url1)
    assert form_id_a
    window.update_finished_form(form_id_a)

    # Call the helper directly to avoid relying on QTimer in tests
    window._maybe_start_next_after_finish()

    assert calls.get('called') is True
    # Expect the next queued form's URL to be passed
    assert any(url2 in u for u in calls.get('target_urls', []))

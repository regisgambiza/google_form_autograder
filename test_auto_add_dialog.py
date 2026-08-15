import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton

from app_theme import apply_application_theme
from auto_add_dialog import (
    AutoAddDialog,
    _load_auto_run_config,
    _save_auto_run_config,
    count_identifiers,
)


APP = QApplication.instance() or QApplication([])
apply_application_theme(APP)


def test_auto_dialog_uses_theme_widgets():
    d = AutoAddDialog(mode="auto")
    assert d.search_btn.objectName() == "Primary"
    spinboxes = d.findChildren(QSpinBox)
    doublespin = d.findChildren(QDoubleSpinBox)
    assert spinboxes, "expected interval/recency spin boxes"
    assert doublespin, "expected budget double spin box"


def test_recent_only_toggle_greys_out_recency():
    d = AutoAddDialog(mode="auto")
    d.mode_combo.setCurrentText("Whole Form")
    assert not d.recency_edit.isEnabled()
    assert not d.recency_unit.isEnabled()
    d.mode_combo.setCurrentText("Recent Only")
    assert d.recency_edit.isEnabled()
    assert d.recency_unit.isEnabled()


def test_preview_reflects_settings():
    d = AutoAddDialog(mode="auto")
    d.mode_combo.setCurrentText("Recent Only")
    d.interval_edit.setValue(30)
    d.recency_edit.setValue(2)
    d.recency_unit.setCurrentText("hours")
    d.budget_edit.setValue(2.5)
    text = d.preview_label.text()
    assert "every 30 min" in text
    assert "scanning last 2 h" in text
    assert "budget $2.50/run" in text


def test_settings_persist_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = {
        "grading_mode": "Recent Only",
        "recency_value": 3,
        "recency_unit": "hours",
        "interval_value": 15,
        "interval_unit": "minutes",
        "notify_on_new": False,
        "spend_budget_usd": 4.0,
        "use_time_schedule": True,
        "schedule_time": "08:15",
        "selected_days": [True, True, False, True, True, False, False],
        "sources": ["https://docs.google.com/forms/d/abc/edit"],
    }
    _save_auto_run_config(settings)
    loaded = _load_auto_run_config()
    assert loaded["grading_mode"] == "Recent Only"
    assert loaded["interval_value"] == 15
    assert loaded["spend_budget_usd"] == 4.0
    assert loaded["schedule_time"] == "08:15"


def test_search_and_add_stores_settings_and_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    window = _make_fake_parent()
    d = AutoAddDialog(parent=window, mode="auto")
    d.predefined_list.addItem("https://docs.google.com/forms/d/abc/edit")
    d.mode_combo.setCurrentText("Recent Only")
    d.interval_edit.setValue(20)
    d.notify_check.setChecked(False)
    d.budget_edit.setValue(1.75)

    d.search_thread = _FakeThread(d.on_search_finished)
    monkeypatch.setattr(
        d, "search_thread", _FakeThread(d.on_search_finished)
    )
    d.progress_dialog = _FakeProgress()
    d.recency_minutes = 60
    d.interval_seconds = 20 * 60
    d.all_folders = ["https://docs.google.com/forms/d/abc/edit"]
    d.use_time_schedule = False
    d.schedule_time_val = d.schedule_time.time()
    d.selected_days = [cb.isChecked() for cb in d.days_checkboxes]
    d.grading_mode = "Recent Only"
    d.notify_on_new = False
    d.auto_spend_budget_usd = 1.75
    d.search_and_add_called = True
    d.on_search_finished([])

    assert window.recency_minutes is not None
    assert window.interval_seconds == 20 * 60
    assert window.grading_mode == "Recent Only"
    assert window.auto_notify_on_new is False
    assert abs(window.auto_spend_budget_usd - 1.75) < 0.001
    assert window.config.get("max_openrouter_spend_usd_per_run") == 1.75


class _FakeProgress:
    def __init__(self):
        self.calls = []

    def close(self):
        self.calls.append("close")


class _FakeThread:
    def __init__(self, finished):
        self._finished = finished

    def progress(self):
        return self

    def finished(self):
        return self

    def connect(self, *_args):
        return self

    def start(self):
        pass


def _make_fake_parent():
    from PySide6.QtWidgets import QWidget

    class FakeParent(QWidget):
        def __init__(self):
            super().__init__()
            self.forms_data = {}
            self.recency_minutes = None
            self.interval_seconds = None
            self.folders = []
            self.use_time_schedule = False
            self.schedule_time_val = None
            self.selected_days = [True] * 7
            self.grading_mode = "Whole Form"
            self.auto_notify_on_new = True
            self.auto_spend_budget_usd = 0.0
            self.config = {}

        def _add_form_to_queue(self, *args, **kwargs):
            return None

        def save_forms(self):
            pass

        def _refresh_queue_positions(self):
            pass

        def start_auto_mode(self):
            self.started = True

        def run_grader(self):
            pass

        def schedule_next_cycle(self):
            pass

        def update_config(self, key, value):
            self.config[key] = value

    return FakeParent()


def test_count_identifiers_counts_sources():
    assert count_identifiers("") == 0
    assert count_identifiers("https://docs.google.com/forms/d/a/edit") == 1
    assert count_identifiers(
        "https://docs.google.com/forms/d/a/edit, https://docs.google.com/forms/d/b/edit"
    ) == 2

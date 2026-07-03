import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton, QSplitter

from app_theme import apply_application_theme
from gui_main import FormManager


APP = QApplication.instance() or QApplication([])
apply_application_theme(APP)


def test_main_window_uses_approved_workspace_layout():
    window = FormManager()
    buttons = {button.text(): button for button in window.findChildren(QPushButton)}
    assert {"Add Sources", "Scan Source", "Run Grading", "Answer Keys"}.issubset(buttons)
    assert {buttons[name].width() for name in ("Add Sources", "Scan Source", "Run Grading", "Answer Keys")} == {145}
    assert not buttons["Scan Source"].icon().isNull()
    assert buttons["Run Grading"].objectName() == "Secondary"
    assert not buttons["Run Grading"].icon().isNull()
    splitter = window.findChild(QSplitter, "WorkspaceSplitter")
    assert splitter is not None
    assert splitter.count() == 2
    assert window.form_list is not None
    assert window.detail_title is not None


def test_terminal_drawer_collapses_opens_and_expands():
    window = FormManager()
    assert window.terminal_state == "collapsed"
    assert window.terminal_frame.height() == 38
    assert window.log_tabs.isHidden()

    window.toggle_terminal()
    assert window.terminal_state == "open"
    assert window.terminal_frame.height() == 230
    assert not window.log_tabs.isHidden()

    window.expand_terminal()
    assert window.terminal_state == "expanded"
    assert window.terminal_frame.height() >= 280

    window.set_terminal_state("collapsed")
    assert window.terminal_frame.height() == 38


def test_queue_search_and_status_filter_hide_nonmatches():
    window = FormManager()
    window.form_list.clear()
    window.forms_data.clear()
    first = window._add_form_to_queue("https://docs.google.com/forms/d/a/edit", "Algebra", source="Test")
    second = window._add_form_to_queue("https://docs.google.com/forms/d/b/edit", "Fractions", source="Test")
    window._set_form_status(second, "done")

    window.form_search_input.setText("alg")
    assert not first.isHidden()
    assert second.isHidden()

    window.form_search_input.clear()
    window.form_filter_combo.setCurrentText("Done")
    assert first.isHidden()
    assert not second.isHidden()


def test_live_metric_cards_show_accept_review_and_elapsed():
    window = FormManager()
    window.update_form_metrics(207, 462, 180, 6, 3723)
    assert window.metric_responses.text() == "207 / 462"
    assert window.metric_accepted.text() == "180"
    assert ">6<" in window.metric_review.text()
    assert window.metric_elapsed.text() == "01:02:03"


def test_review_metric_deep_links_to_current_form(monkeypatch):
    window = FormManager()
    window.current_form_url = "https://docs.google.com/forms/d/form-1/edit"
    calls = []
    monkeypatch.setattr(
        window,
        "open_answer_key_dashboard",
        lambda target_url=None, auto_scan=False: calls.append((target_url, auto_scan)),
    )
    window.open_current_form_review()
    assert calls == [(window.current_form_url, True)]


def test_settings_exposes_cache_and_history_clear_action(monkeypatch):
    window = FormManager()
    # Inspecting the source avoids entering the modal settings event loop.
    source = __import__("pathlib").Path("gui_main.py").read_text(encoding="utf-8")
    assert "Clear Cache & Grading History" in source
    assert "clear_grading_cache(reset_history=True)" in source
    assert "Always grade from fresh data (ignore previous-run cache)" in source
    assert "Send every answer through the full AI jury" in source

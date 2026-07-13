import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton, QSplitter, QFrame, QLabel, QScrollArea
from PyQt5.QtCore import Qt

from app_theme import apply_application_theme
from gui_main import FormManager


APP = QApplication.instance() or QApplication([])
apply_application_theme(APP)


def test_main_window_uses_approved_workspace_layout():
    window = FormManager()
    buttons = {button.text(): button for button in window.findChildren(QPushButton)}
    assert {"Add Sources", "Scan Source", "Run Grading", "Answer Keys"}.issubset(buttons)
    assert {buttons[name].height() for name in ("Add Sources", "Scan Source", "Run Grading", "Answer Keys")} == {42}
    assert not buttons["Scan Source"].icon().isNull()
    assert buttons["Run Grading"].objectName() == "CommandButton"
    assert buttons["Run Grading"].property("variant") == "secondary"
    assert not buttons["Run Grading"].icon().isNull()
    splitter = window.findChild(QSplitter, "WorkspaceSplitter")
    assert splitter is not None
    assert splitter.count() == 2
    detail_scroll = window.findChild(QScrollArea, "DetailScroll")
    assert detail_scroll is not None
    assert detail_scroll.widgetResizable()
    assert detail_scroll.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
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


def test_terminal_log_buffers_are_bounded():
    window = FormManager()
    for i in range(window.max_gui_log_lines + 20):
        window.append_debug(f"[TEST] line {i}")

    assert len(window.debug_lines) == window.max_gui_log_lines
    assert window.debug_output.document().maximumBlockCount() == window.max_gui_visible_blocks
    assert window.debug_output.document().blockCount() <= window.max_gui_visible_blocks


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


def test_form_queue_uses_compact_table_rows():
    window = FormManager()
    window.form_list.clear()
    window.forms_data.clear()
    header = window.findChild(QFrame, "FormQueueHeader")
    assert header is not None

    first = window._add_form_to_queue("https://docs.google.com/forms/d/a/edit", "Algebra", source="Test")
    second = window._add_form_to_queue("https://docs.google.com/forms/d/b/edit", "Fractions", source="Test")
    window.current_form_url = first.data(Qt.UserRole)
    window.update_form_metrics(5, 10, 4, 1, 60, 0, 2, 3, 1200.0)

    first_widget = window.form_list.itemWidget(first)
    second_widget = window.form_list.itemWidget(second)
    assert first_widget.property("rowParity") == "even"
    assert second_widget.property("rowParity") == "odd"
    assert first_widget._progress_bar.value() == 50
    assert first_widget._eta_label.text() == "01:00"
    assert first.sizeHint().height() < 70


def test_live_metric_cards_show_accept_review_and_elapsed():
    window = FormManager()
    window.update_form_metrics(207, 462, 180, 6, 3723, 21, 80, 127, 4321.0)
    assert window.metric_responses.text() == "207 / 462"
    assert window.metric_accepted.text() == "180"
    assert window.metric_rejected.text() == "21"
    assert ">6<" in window.metric_review.text()
    assert window.metric_elapsed.text() == "01:02:03"
    assert window.metric_rate.text() == "3.3/min"
    assert window.metric_avg_latency.text() == "4.3s"
    assert window.metric_eta.text() == "01:16:26"
    assert "Det / AI" not in {label.text() for label in window.findChildren(QLabel)}


def test_top_progress_tracks_overall_forms_not_answer_progress():
    window = FormManager()
    window.update_overall_progress(50, 100)
    assert window.detail_progress.value() == 50
    assert window.detail_progress_value.text() == "50%"

    window.update_progress(22, 100)
    assert window.metric_responses.text() == "22 / 100"
    assert window.detail_progress.value() == 50
    assert window.detail_progress_value.text() == "50%"


def test_live_dashboard_updates_ai_backlog_and_current_model():
    window = FormManager()
    window.form_list.clear()
    item = window._add_form_to_queue("https://docs.google.com/forms/d/form-1/edit", "Algebra", source="Test")
    window.form_list.setCurrentItem(item)
    window.current_form_url = item.data(Qt.UserRole)

    window.update_form_metrics(5, 10, 4, 1, 60, 0, 2, 3, 1200.0)
    window._update_worker_tab_queue_counts("q_fetch=0 q_det=0 q_ai=1 q_ai_actual=7 q_result=0 done=5/10")
    assert window.metric_ai_backlog.text() == "7"

    window._update_current_model_from_heartbeat("[HEARTBEAT] active_model=gemma3:12b progress=5/10 q_ai=7")
    assert window.metric_current_model.text() == "gemma3:12b"


def test_detail_panel_shows_live_worker_rows():
    window = FormManager()
    rows = window.findChildren(QFrame, "WorkerRow")
    assert rows
    assert len(window.app_worker_cards) >= 1
    assert len(window.provider_worker_cards) >= 1

    window._update_app_worker(
        "id=ai-1 type=ai status=running current=f1:q123 answers=30 latency_ms=0 queue_wait_ms=42"
    )
    app_card = window.app_worker_cards["ai-1"]
    assert app_card["status"].text() == "Running"
    assert "30 answers" in app_card["primary"].text()
    assert "f1:q123" in app_card["secondary"].text()

    window._update_provider_worker(
        "id=openrouter-1 provider=openrouter status=running model=nvidia/test:free "
        "request=judge-batch-1 latency_ms=0 queue_wait_ms=5"
    )
    assert window.provider_worker_states["openrouter-1"]["state"] == "running"
    provider_card = window.provider_worker_cards["openrouter-1"]
    assert provider_card["status"].text() == "Running"
    assert provider_card["primary"].text() == "nvidia/test:free"
    assert "judge-batch-1" in provider_card["secondary"].text()
    assert window.provider_worker_summary.text()


def test_ai_worker_rows_use_transformers_names_and_expand_dynamically(monkeypatch):
    window = FormManager()
    assert window.app_worker_cards["ai-1"]["title"].text() == "Optimus Prime"
    assert window.app_worker_cards["ai-2"]["title"].text() == "Bumblebee"

    monkeypatch.setattr(
        window,
        "_configured_worker_counts",
        lambda: {"ai": 6, "openrouter": 4, "ollama": 1},
    )
    window._sync_worker_cards_to_config()

    assert len(window.app_worker_cards) >= 6
    assert window.app_worker_cards["ai-5"]["title"].text() == "Arcee"
    assert window.app_worker_cards["ai-6"]["title"].text() == "Jazz"


def test_provider_worker_rows_expand_dynamically(monkeypatch):
    window = FormManager()
    monkeypatch.setattr(
        window,
        "_configured_worker_counts",
        lambda: {"ai": 4, "openrouter": 6, "ollama": 2},
    )
    window._sync_worker_cards_to_config()

    assert "openrouter-6" in window.provider_worker_cards
    assert "ollama-2" in window.provider_worker_cards
    assert window.provider_worker_cards["openrouter-6"]["title"].text() == "OpenRouter 6"
    assert window.provider_worker_cards["ollama-2"]["title"].text() == "Ollama 2"


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
    assert "Answer Processing:" in source
    assert "raw mode; take every response exactly as read from the form" in source
    assert "Ollama Answers per Judge Call:" in source
    assert "OpenRouter Answers per Judge Call:" in source
    assert 'config_data["ollama_judge_answer_batch_size"]' in source
    assert 'config_data["openrouter_judge_answer_batch_size"]' in source


def test_settings_hides_low_level_expert_controls():
    source = __import__("pathlib").Path("gui_main.py").read_text(encoding="utf-8")
    for obsolete_row in (
        'form.addRow("Evaluator:"',
        'form.addRow("Leniency:"',
        'form.addRow("Primary Judge Model:"',
        'form.addRow("Rubric Model:"',
        'form.addRow("Embedding Model:"',
        'form.addRow("Reasoning Model:"',
        'form.addRow("Decision Evidence Log:"',
        'form.addRow("Teacher Benchmark:"',
        'form.addRow("Batch Size:"',
        'form.addRow("Execution Mode:"',
    ):
        assert obsolete_row not in source

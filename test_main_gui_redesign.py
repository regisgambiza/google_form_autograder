import json
import os
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QToolButton, QTreeWidget

from gui_studio import theme as studio_theme
from gui_studio.main_window import AutograderWindow


APP = QApplication.instance() or QApplication([])
APP.setStyleSheet(studio_theme.load_stylesheet())


def _make_window():
    window = AutograderWindow()
    window.save_forms = lambda: None
    return window


def _add_form(window, url, title):
    return window._add_form_to_queue(url, title, source="Test")


def _clear_queue(window):
    window.form_list.clear()
    window.forms_data.clear()


def _status_text(widget, row):
    return window_status_cell(widget, row, 1)


def window_status_cell(window, row, column):
    item = window.queue_table.item(row, column)
    return item.text() if item else ""


# ---------------------------------------------------------------------------
# Shell structure
# ---------------------------------------------------------------------------
def test_shell_uses_menu_toolbar_tree_and_table():
    window = _make_window()
    menu_titles = [action.text() for action in window.menuBar().actions()]
    assert menu_titles == ["Tasks", "File", "Grading", "View", "Help"]
    tools = {button.text(): button for button in window.findChildren(QToolButton)}
    assert {"Add URL", "Start", "Stop", "Answer Keys", "Settings"}.issubset(tools)
    assert not tools["Start"].icon().isNull()
    assert window.category_tree.objectName() == "CategoryTree"
    assert window.queue_table.objectName() == "QueueTable"
    assert window.queue_table.columnCount() == 10
    assert window.stack.count() == 5


def test_tree_categories_filter_table_and_panels_switch_pages():
    window = _make_window()
    _clear_queue(window)
    _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    second = _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")
    window._set_form_status(second, "done")

    def visible_urls():
        return [
            window.queue_table.item(row, 0).data(Qt.UserRole)
            for row in range(window.queue_table.rowCount())
            if not window.queue_table.isRowHidden(row)
        ]

    window._goto_page("queued")
    assert visible_urls() == ["https://docs.google.com/forms/d/a/edit"]
    window._goto_page("done")
    assert visible_urls() == ["https://docs.google.com/forms/d/b/edit"]
    window._goto_page("all")
    assert len(visible_urls()) == 2

    window._goto_page("dashboard")
    assert window.stack.currentWidget() is window.dashboard
    window._goto_page("providers")
    assert window.stack.currentWidget() is window.providers_page
    window._goto_page("activity")
    assert window.stack.currentWidget() is window.activity
    window._goto_page("drive")
    assert window.stack.currentWidget() is window.drive_page
    window._goto_page("all")
    assert window.stack.currentWidget() is window.queue_page


def test_tree_labels_carry_counts():
    window = _make_window()
    _clear_queue(window)
    _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")
    assert window._tree_items["all"].text(0) == "All Forms (2)"
    assert window._tree_items["queued"].text(0) == "Queued (2)"


def test_run_controls_toggle_like_classic_toolbar():
    window = _make_window()
    assert window.run_tool.isEnabled()
    assert not window.stop_tool.isEnabled()
    window._set_run_controls(True)
    assert not window.run_tool.isEnabled()
    assert window.stop_tool.isEnabled()
    assert not window.dashboard.run_button.isVisibleTo(window.dashboard)
    window._set_run_controls(False)
    assert window.run_tool.isEnabled()
    assert not window.stop_tool.isEnabled()


def test_console_log_buffers_are_bounded():
    window = _make_window()
    for i in range(window.max_gui_log_lines + 20):
        window.append_debug(f"[TEST] line {i}")
    assert len(window.debug_lines) == window.max_gui_log_lines
    assert window.dashboard.console.document().blockCount() <= 900
    assert window.activity.console.document().blockCount() <= 1200


# ---------------------------------------------------------------------------
# Queue table
# ---------------------------------------------------------------------------
def test_queue_search_and_status_filter_hide_nonmatches():
    window = _make_window()
    _clear_queue(window)
    first = _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    second = _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")
    window._set_form_status(second, "done")

    window.queue_page.search_input.setText("alg")
    assert not window.queue_table.isRowHidden(first.row())
    assert window.queue_table.isRowHidden(second.row())

    window.queue_page.search_input.clear()
    window.queue_page.filter_combo.setCurrentText("Done")
    assert window.queue_table.isRowHidden(first.row())
    assert not window.queue_table.isRowHidden(second.row())
    window.queue_page.filter_combo.setCurrentText("All")


def test_form_table_rows_show_progress_eta_and_answers():
    window = _make_window()
    _clear_queue(window)
    first = _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    url = first.data(Qt.UserRole)
    window.current_form_url = url
    window._set_form_status(first, "running", "Grading now")
    window.update_form_metrics(5, 10, 4, 1, 60, 0, 2, 3, 1200.0)

    row = first.row()
    assert window_status_cell(window, row, 1) == "RUNNING"
    assert window._row_bars[url].value() == 50
    assert window_status_cell(window, row, 3) == "5/10"
    assert window_status_cell(window, row, 4) == "4"
    assert window_status_cell(window, row, 5) == "0"
    assert window_status_cell(window, row, 6) == "1"
    assert window_status_cell(window, row, 7) == "01:00"


def test_partial_form_badge_is_shown_on_queue_row():
    window = _make_window()
    _clear_queue(window)
    item = _add_form(window, "https://docs.google.com/forms/d/form-1/edit", "Algebra")
    window.update_skipped_form(
        "form-1", "", "Missing teacher answer key",
        '[{"question_number": 5, "title": "8 c)", "responses": 2}]',
    )
    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "partial"
    row = item.row()
    assert window_status_cell(window, row, 1) == "PARTIAL"
    assert window_status_cell(window, row, 7) == "Partial"
    assert "Missing teacher answer key" in window.queue_table.item(row, 0).toolTip()
    assert "form-1" in window.auto_partial_forms


def test_partial_form_badge_can_match_queue_row_by_url():
    window = _make_window()
    _clear_queue(window)
    url = "https://docs.google.com/forms/d/form-1/edit"
    item = _add_form(window, url, "Algebra")
    window.update_skipped_form("", url, "Missing teacher answer key", "[]")
    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "partial"
    assert window_status_cell(window, item.row(), 1) == "PARTIAL"


def test_finished_event_does_not_overwrite_partial_badge():
    window = _make_window()
    _clear_queue(window)
    item = _add_form(window, "https://docs.google.com/forms/d/form-1/edit", "Algebra")
    window.update_skipped_form("form-1", "", "Missing teacher answer key", "[]")
    window.update_finished_form("form-1")
    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "partial"
    assert window_status_cell(window, item.row(), 1) == "PARTIAL"


# ---------------------------------------------------------------------------
# Dashboard metrics
# ---------------------------------------------------------------------------
def test_live_metric_cards_show_accept_review_and_elapsed():
    window = _make_window()
    window.update_form_metrics(207, 462, 180, 6, 3723, 21, 80, 127, 4321.0)
    d = window.dashboard
    assert d.accepted_card.value_label.text() == "180"
    assert d.rejected_card.value_label.text() == "21"
    assert d.review_card.value_label.text() == "6"
    assert d.elapsed_label.text() == "Elapsed 01:02:03"
    assert d.eta_label.text() == "ETA 01:16:26"
    assert d.rate_card.value_label.text() == "3.3/min"
    assert d.latency_card.value_label.text() == "4.3s"
    assert d.det_card.value_label.text() == "80"
    assert d.ai_card.value_label.text() == "127"


def test_partial_metric_updates_do_not_wipe_other_cards():
    window = _make_window()
    window.update_form_metrics(10, 20, 5, 1, 60, 2, 3, 4, 900.0)
    d = window.dashboard
    d.set_metrics(rate="5.0/min")
    d.set_metrics(backlog="9")
    d.set_metrics(pipeline="AI-drain")
    assert d.rate_card.value_label.text() == "5.0/min"
    assert d.backlog_card.value_label.text() == "9"
    assert d.pipeline_card.value_label.text() == "AI-drain"
    assert d.latency_card.value_label.text() == "900ms"
    assert d.det_card.value_label.text() == "3"
    assert d.ai_card.value_label.text() == "4"


def test_top_progress_tracks_overall_forms_not_answer_progress():
    window = _make_window()
    window.update_overall_progress(50, 100)
    assert window.dashboard.forms_progress.value() == 50
    assert "50%" in window.dashboard.forms_progress_label.text()

    window.update_progress(22, 100)
    assert window.dashboard.forms_progress.value() == 50
    assert window.dashboard.ring.fraction == 0.22


def test_live_dashboard_updates_ai_backlog_and_current_model():
    window = _make_window()
    _clear_queue(window)
    item = _add_form(window, "https://docs.google.com/forms/d/form-1/edit", "Algebra")
    window.current_form_url = item.data(Qt.UserRole)

    window.update_form_metrics(5, 10, 4, 1, 60, 0, 2, 3, 1200.0)
    window._update_worker_metrics("q_fetch=0 q_det=0 q_ai=1 q_ai_actual=7 q_result=0 done=5/10")
    assert window.dashboard.backlog_card.value_label.text() == "7"
    assert item.data(Qt.UserRole + 1)["ai_backlog"] == 7

    window._update_current_model_from_heartbeat("[HEARTBEAT] active_model=gemma3:12b progress=5/10 q_ai=7")
    assert window.dashboard.model_label.text() == "Model: gemma3:12b"
    assert window.providers_page.active_model.text() == "gemma3:12b"


def test_model_progress_clamps_stale_overflow_in_running_queue():
    window = _make_window()
    _clear_queue(window)
    item = _add_form(window, "https://docs.google.com/forms/d/form-1/edit", "Algebra")
    window.current_form_url = item.data(Qt.UserRole)
    window._set_form_status(item, "running", "Grading now")

    window.update_model_progress(24, 21)

    meta = item.data(Qt.UserRole + 1)
    assert meta["model_done"] == 21
    assert meta["model_total"] == 21
    assert window_status_cell(window, item.row(), 3) == "21/21"
    assert window._row_bars[item.data(Qt.UserRole)].value() == 100


def test_stage_stepper_reflects_queue_depths():
    window = _make_window()
    window.is_grading = True
    window._update_worker_metrics("q_fetch=2 pending=1 q_det=3 q_ai=5 q_result=0 done=4/10")
    states = window.dashboard.stepper._dots
    # Producer backlog keeps the queue stage active while workers churn.
    assert states["queued"]._state == "active"
    assert states["deterministic"]._state == "active"
    assert states["ai"]._state == "active"
    assert states["consensus"]._state == "todo"
    window.is_grading = False


# ---------------------------------------------------------------------------
# Providers page
# ---------------------------------------------------------------------------
def test_provider_health_cards_summarize_metrics():
    window = _make_window()
    window._update_provider_metrics(
        "q_openrouter=2 q_ollama=0 openrouter_health=HEALTHY openrouter_circuit=CLOSED "
        "openrouter_done=40 openrouter_failed=3 openrouter_last_ms=1234 "
        "openrouter_last_model=tencent/hy3:free openrouter_last_error=OpenRouter_rate_limited "
        "or_models_total=20 or_models_available=4 or_models_rate_limited=12 or_models_failed=8 "
        "or_json_failures=6 or_last_success_rate=0.875 or_last_json_failures=2 "
        "or_avg_suspicion=0.420 or_last_suspicion=0.900 "
        "or_max_cooldown_s=360 or_last_cooldown_s=60 or_cost_usd=0.123456 "
        "or_selection_reason=fresh_then_reused_reuse_enabled "
        "ollama_health=HEALTHY ollama_circuit=CLOSED ollama_done=1 ollama_failed=0 "
        "ollama_last_ms=900 ollama_last_model=gpt-oss:latest ollama_last_error=- "
        "submitted=44 completed=41 failed=3 validation_failed=6 retries=5 failovers=1 rpm=10.0 avg_ms=1500"
    )
    page = window.providers_page
    assert page.provider_cards["openrouter"].tag.text() == "Online"
    assert page.provider_cards["openrouter"].property("health") == "online"
    assert "tencent/hy3:free" in page.model_rows["current"].text()
    assert "87.5%" in page.model_rows["success"].text()
    assert "12 rate-limited" in page.model_rows["limits"].text()
    assert "6 JSON failures" in page.model_rows["json"].text()
    assert "0.900" in page.model_rows["quality"].text()
    assert "1m 0s" in page.model_rows["cooldown"].text()
    assert "$0.1235" in page.model_rows["cost"].text()
    assert "fresh then reused reuse enabled" in page.model_rows["reason"].text()


def test_provider_health_maps_offline_states():
    window = _make_window()
    window._update_provider_metrics(
        "q_ollama=0 ollama_health=OFFLINE ollama_circuit=OPEN ollama_done=0 ollama_failed=9 "
        "ollama_last_ms=0 ollama_last_model=- ollama_last_error=conn_refused"
    )
    card = window.providers_page.provider_cards["ollama"]
    assert card.tag.text() == "Offline"
    assert card.property("health") == "offline"


def test_worker_chips_update_and_expand_dynamically(monkeypatch):
    window = _make_window()
    assert window.app_worker_cards["ai-1"]["title"] == "Optimus Prime"
    assert len(window.app_worker_cards) >= 1
    assert len(window.provider_worker_cards) >= 1

    window._update_app_worker(
        "id=ai-1 type=ai status=running current=f1:q123 answers=30 latency_ms=0 queue_wait_ms=42"
    )
    chip = window.app_worker_cards["ai-1"]["chip"]
    assert chip.state.text() == "Running"
    assert "30 answers" in chip.detail.text()
    assert "f1:q123" in chip.detail.text()

    window._update_provider_worker(
        "id=llamacpp-1 provider=llamacpp status=running model=local/test.gguf "
        "request=judge-batch-1 latency_ms=0 queue_wait_ms=5"
    )
    assert window.provider_worker_states["llamacpp-1"]["state"] == "running"
    provider_chip = window.provider_worker_cards["llamacpp-1"]["chip"]
    assert provider_chip.state.text() == "Running"
    assert "local/test.gguf" in provider_chip.detail.text()
    assert "judge-batch-1" in provider_chip.detail.text()
    assert window.providers_page.provider_summary.text()

    monkeypatch.setattr(
        window, "_configured_worker_counts",
        lambda: {"ai": 6, "openrouter": 6, "llamacpp": 1, "ollama": 2},
    )
    window._sync_worker_cards_to_config()
    assert len(window.app_worker_cards) >= 6
    assert window.app_worker_cards["ai-2"]["title"] == "Bumblebee"
    assert window.app_worker_cards["ai-5"]["title"] == "Arcee"
    assert "openrouter-6" in window.provider_worker_cards
    assert "ollama-2" in window.provider_worker_cards
    assert window.provider_worker_cards["openrouter-6"]["title"] == "OpenRouter 6"


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------
def test_activity_feed_receives_structured_answer_events():
    window = _make_window()
    window._on_feed_run_start({"form_title": "Quiz", "total": 5})
    window._on_answer_event({
        "decision": "YES", "current": 1, "total": 5, "question_number": 2,
        "confidence": 0.9, "question": "Q?", "answer": "A",
        "judges": [{"role": "semantic_judge", "decision": "YES", "confidence": 1.0}],
        "elapsed": "00:01",
    })
    window._on_feed_run_complete({"accepted": 1, "review": 0, "rejected": 0, "elapsed": "00:10"})
    assert window.activity.feed_list.count() == 3


def test_activity_consoles_route_worker_logs():
    window = _make_window()
    window.activity.clear_all()
    window.activity.route_raw("[Worker: Producer] hello")
    window.activity.route_raw("[Worker: Deterministic] det")
    window.activity.route_raw("[Worker: AI] ai")
    window.activity.route_raw("[PROVIDER WORKER] pw")
    window.activity.route_raw("[Worker: Aggregator] agg")
    assert "hello" in window.activity.pipeline_output.toPlainText()
    assert "det" in window.activity.det_output.toPlainText()
    assert "ai" in window.activity.ai_output.toPlainText()
    assert "pw" in window.activity.provider_output.toPlainText()
    assert "agg" in window.activity.agg_output.toPlainText()


# ---------------------------------------------------------------------------
# Context menu
# ---------------------------------------------------------------------------
def test_queue_list_has_custom_context_menu_policy():
    window = _make_window()
    assert window.queue_table.contextMenuPolicy() == Qt.CustomContextMenu


def test_context_menu_builds_expected_actions_and_separators():
    window = _make_window()
    _clear_queue(window)
    _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    second = _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")
    menu = window._build_form_context_menu(second)
    labels = {act.text() for act in menu.actions() if not act.isSeparator()}
    assert {"Grade Now", "Open Answer Key Dashboard", "Requeue (Reset to Queued)",
            "Mark as Done", "Mark as Skipped", "Move to Top", "Move Up",
            "Move Down", "Move to Bottom", "Copy URL", "Open in Browser",
            "Remove from Queue"}.issubset(labels)
    seps = [act.isSeparator() for act in menu.actions()]
    assert seps.count(True) == 4


def _menu_item(menu, partial):
    for act in menu.actions():
        if partial in act.text() and not act.isSeparator():
            return act
    return None


def test_context_menu_move_enabled_state_tracks_row_boundaries():
    window = _make_window()
    _clear_queue(window)
    a = _add_form(window, "https://docs.google.com/forms/d/a/edit", "A")
    b = _add_form(window, "https://docs.google.com/forms/d/b/edit", "B")

    menu_top = window._build_form_context_menu(a)
    assert not _menu_item(menu_top, "Move to Top").isEnabled()
    assert not _menu_item(menu_top, "Move Up").isEnabled()
    assert _menu_item(menu_top, "Move Down").isEnabled()
    assert _menu_item(menu_top, "Move to Bottom").isEnabled()

    menu_bottom = window._build_form_context_menu(b)
    assert _menu_item(menu_bottom, "Move to Top").isEnabled()
    assert _menu_item(menu_bottom, "Move Up").isEnabled()
    assert not _menu_item(menu_bottom, "Move Down").isEnabled()
    assert not _menu_item(menu_bottom, "Move to Bottom").isEnabled()


def test_context_requeue_resets_done_status_and_counters():
    window = _make_window()
    _clear_queue(window)
    item = _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    window._set_form_status(item, "done", "Finished")
    meta = item.data(Qt.UserRole + 1) or {}
    meta["completed"] = 42
    meta["total"] = 50
    meta["review_questions"] = 7
    item.setData(Qt.UserRole + 1, meta)

    window._context_set_status(item, "queued")

    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "queued"
    assert meta.get("completed") is None or meta["completed"] == 0
    assert meta.get("total") is None or meta["total"] == 0
    assert meta.get("review_questions") is None or meta["review_questions"] == 0


def test_context_mark_status_changes_status():
    window = _make_window()
    _clear_queue(window)
    item = _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    window._context_set_status(item, "done")
    assert (item.data(Qt.UserRole + 1) or {})["status"] == "done"
    assert window_status_cell(window, item.row(), 1) == "DONE"
    window._context_set_status(item, "skipped")
    assert (item.data(Qt.UserRole + 1) or {})["status"] == "skipped"
    assert window_status_cell(window, item.row(), 1) == "SKIPPED"


def test_context_move_to_top_and_bottom_reorders_queue_and_saves():
    window = _make_window()
    _clear_queue(window)
    urls = ["https://docs.google.com/forms/d/a/edit",
            "https://docs.google.com/forms/d/b/edit",
            "https://docs.google.com/forms/d/c/edit"]
    for url in urls:
        _add_form(window, url, url.split("/d/")[1].split("/")[0])

    def ordered_urls():
        return [window.queue_table.item(i, 0).data(Qt.UserRole)
                for i in range(window.queue_table.rowCount())]

    last = window.queue_table.item(2, 0)
    window._context_move(last, "top")
    assert ordered_urls()[0] == urls[2]
    assert list(window.forms_data.keys())[0] == urls[2]

    first = window.queue_table.item(0, 0)
    window._context_move(first, "bottom")
    assert ordered_urls()[-1] == urls[2]


def test_context_move_keeps_row_metadata_attached():
    window = _make_window()
    _clear_queue(window)
    urls = ["https://docs.google.com/forms/d/a1/edit",
            "https://docs.google.com/forms/d/b1/edit"]
    for url in urls:
        _add_form(window, url, url)
    second = window.queue_table.item(1, 0)
    second.setData(Qt.UserRole + 1, {**(second.data(Qt.UserRole + 1) or {}), "detail": "keep-me"})
    window._context_move(second, "top")
    moved = window.queue_table.item(0, 0)
    assert moved.data(Qt.UserRole) == urls[1]
    assert (moved.data(Qt.UserRole + 1) or {}).get("detail") == "keep-me"
    assert urls[1] in window._row_bars


def test_context_remove_single_item_deletes_and_saves(monkeypatch):
    window = _make_window()
    _clear_queue(window)
    _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    item = _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")
    monkeypatch.setattr(window, "save_forms", lambda: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    window._context_remove(item, item.data(Qt.UserRole))
    assert window.queue_table.rowCount() == 1
    assert window.queue_table.item(0, 0).data(Qt.UserRole) == "https://docs.google.com/forms/d/a/edit"


def test_context_remove_cancelled_keeps_item(monkeypatch):
    window = _make_window()
    _clear_queue(window)
    _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    item = _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    window._context_remove(item, item.data(Qt.UserRole))
    assert window.queue_table.rowCount() == 2


# ---------------------------------------------------------------------------
# Shortcuts / theme
# ---------------------------------------------------------------------------
def test_keyboard_shortcuts_are_registered():
    window = _make_window()
    keys = [s.key().toString() for s in window.findChildren(QShortcut)]
    for expected in ("Ctrl+R", "Ctrl+D", "Ctrl+Shift+A", "Ctrl+A", "Ctrl+K", "Ctrl+E", "Ctrl+Shift+S"):
        assert expected in keys, f"missing shortcut {expected}"


def test_light_theme_only_no_dark_mode_toggle():
    from app_theme import is_dark_mode

    window = _make_window()
    assert is_dark_mode() is False
    assert not hasattr(window, "toggle_dark_mode")


def test_review_metric_deep_links_to_current_form(monkeypatch):
    window = _make_window()
    window.current_form_url = "https://docs.google.com/forms/d/form-1/edit"
    calls = []
    monkeypatch.setattr(
        window, "open_answer_key_dashboard",
        lambda target_url=None, auto_scan=False: calls.append((target_url, auto_scan)),
    )
    window.open_current_form_review()
    assert calls == [(window.current_form_url, True)]


# ---------------------------------------------------------------------------
# llama.cpp launcher
# ---------------------------------------------------------------------------
def test_llamacpp_server_command_uses_configurable_performance_defaults():
    command = AutograderWindow._llamacpp_server_command(
        None, {}, "llama-server.exe", "model.gguf", "127.0.0.1", 8081,
    )
    expected_pairs = {
        "--ctx-size": "32768",
        "--gpu-layers": "auto",
        "--threads": "8",
        "--threads-batch": "8",
        "--batch-size": "1024",
        "--ubatch-size": "512",
        "--flash-attn": "auto",
        "--cache-type-k": "q8_0",
        "--cache-type-v": "q8_0",
        "--parallel": "1",
    }
    for option, value in expected_pairs.items():
        assert command[command.index(option) + 1] == value
    assert "--mmap" in command
    assert "--jinja" in command


def test_llamacpp_server_command_emits_disabled_boolean_flags():
    command = AutograderWindow._llamacpp_server_command(
        None,
        {"llamacpp_server_mmap": False, "llamacpp_server_jinja": False},
        "server.exe", "model.gguf", "127.0.0.1", 8081,
    )
    assert "--no-mmap" in command
    assert "--no-jinja" in command


# ---------------------------------------------------------------------------
# Settings dialog integration (source checks avoid the modal loop)
# ---------------------------------------------------------------------------
def test_settings_exposes_cache_and_history_clear_action():
    from pathlib import Path

    source = Path("settings_dialog.py").read_text(encoding="utf-8")
    source += "\n" + Path("gui_studio/main_window.py").read_text(encoding="utf-8")
    assert "Clear Cache & Grading History" in source
    assert "clear_grading_cache(reset_history=True)" in source
    assert "_stop_llamacpp_server_if_enabled(\"llamacpp_stop_server_after_grading\"" in source
    assert "_stop_llamacpp_server_if_enabled(\"llamacpp_stop_server_on_app_close\"" in source
    assert "_start_llamacpp_server(preflight_cfg)" in source
    assert "llama.cpp-only mode is selected" in source
    assert "Large GGUF models can take a few minutes to load." in source


def test_settings_hides_low_level_expert_controls():
    from pathlib import Path

    source_lines = Path("settings_dialog.py").read_text(encoding="utf-8").splitlines()
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
        assert not any(line.strip().startswith(obsolete_row) for line in source_lines)


# ---------------------------------------------------------------------------
# Audit tooling (unchanged modules)
# ---------------------------------------------------------------------------
def test_audit_records_loader_reads_jsonl(tmp_path):
    from decision_audit_viewer import load_audit_records

    target = tmp_path / "grading_decisions.jsonl"
    target.write_text(
        json.dumps({"decision": "YES", "final_score": 1.0, "answer": "42"}) + "\n"
        + json.dumps({"decision": "NO", "final_score": 0.0, "answer": "oops"}) + "\n",
        encoding="utf-8",
    )
    records = load_audit_records(str(target))
    assert [r["decision"] for r in records] == ["NO", "YES"]


def test_audit_viewer_builds_and_filters(tmp_path):
    from decision_audit_viewer import DecisionAuditViewer

    target = tmp_path / "grading_decisions.jsonl"
    target.write_text(
        json.dumps({"decision": "YES", "final_score": 1.0, "answer": "correct"}) + "\n"
        + json.dumps({"decision": "NO", "final_score": 0.0, "answer": "wrong"}) + "\n",
        encoding="utf-8",
    )
    viewer = DecisionAuditViewer(str(target))
    assert viewer.table.rowCount() == 2
    viewer.filter_combo.setCurrentText("YES (accepted)")
    assert viewer.table.rowCount() == 1
    viewer.search_input.setText("wrong")
    assert viewer.table.rowCount() == 0
    viewer.search_input.setText("")
    viewer.filter_combo.setCurrentText("NO (rejected)")
    assert viewer.table.rowCount() == 1


# ---------------------------------------------------------------------------
# Auto-run behavior
# ---------------------------------------------------------------------------
class _FakeSignal:
    def connect(self, *_args):
        return self


class _FakeSearchThread:
    progress = _FakeSignal()
    finished = _FakeSignal()

    def start(self):
        pass


def test_auto_cycle_search_window_respects_grading_mode(monkeypatch):
    """Recent Only mode uses the recency window; Whole Form scans full history."""
    from datetime import datetime, timezone

    import gui_studio.main_window as mw

    window = _make_window()
    window.last_check_time = None
    window.recency_minutes = 5
    window.is_searching = False
    window.is_closing = False
    window.auto_mode = True

    captured = {}

    def fake_search_thread(folders, from_dt, to_dt):
        captured["from_dt"] = from_dt
        captured["to_dt"] = to_dt
        return _FakeSearchThread()

    monkeypatch.setattr(window, "append_debug", lambda *a, **k: None)
    monkeypatch.setattr(mw, "SearchThread", fake_search_thread)

    window.grading_mode = "Whole Form"
    window.auto_cycle()
    assert captured["from_dt"] < datetime.now(timezone.utc) - timedelta(days=30), (
        "Whole Form mode should scan well beyond the recency window"
    )

    captured.clear()
    window.is_searching = False
    window.grading_mode = "Recent Only"
    window.auto_cycle()
    delta = (datetime.now(timezone.utc) - captured["from_dt"]).total_seconds() / 60
    assert delta <= 6, "Recent Only mode should scan only the recency window"


def test_on_auto_search_finished_forces_recent_only_by_mode(monkeypatch):
    window = _make_window()
    window.grading_mode = "Recent Only"
    window.is_closing = False
    window.is_searching = False
    calls = []
    monkeypatch.setattr(window, "run_grader", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(window, "save_forms", lambda: None)
    monkeypatch.setattr(window, "_add_form_to_queue", lambda *a, **k: None)
    monkeypatch.setattr(window, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(window, "append_debug", lambda *a, **k: None)
    monkeypatch.setattr(window, "schedule_next_cycle", lambda: None)

    window.on_auto_search_finished(
        [{"url": "https://docs.google.com/forms/d/abc/edit", "title": "T", "last_submission": None}]
    )
    assert calls and calls[0].get("force_recent_only") is True

    calls.clear()
    window.grading_mode = "Whole Form"
    window.forms_data.clear()
    window.on_auto_search_finished(
        [{"url": "https://docs.google.com/forms/d/def/edit", "title": "T2", "last_submission": None}]
    )
    assert calls and calls[0].get("force_recent_only") is False


def test_on_auto_search_finished_notifies_when_nothing_found(monkeypatch):
    window = _make_window()
    window.grading_mode = "Whole Form"
    window.is_closing = False
    window.is_searching = False
    _clear_queue(window)
    notified = []
    monkeypatch.setattr(window, "run_grader", lambda **k: None)
    monkeypatch.setattr(window, "save_forms", lambda: None)
    monkeypatch.setattr(window, "_add_form_to_queue", lambda *a, **k: None)
    monkeypatch.setattr(window, "_notify", lambda *a, **k: notified.append(a))
    monkeypatch.setattr(window, "append_debug", lambda *a, **k: None)
    monkeypatch.setattr(window, "schedule_next_cycle", lambda: None)
    window.on_auto_search_finished([])
    assert notified, "Expected a notification when no submissions are found"

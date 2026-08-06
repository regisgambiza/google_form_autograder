import os

import json

from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import (QApplication, QPushButton, QSplitter, QFrame, QLabel,
                             QScrollArea, QMenu, QMessageBox)
from PyQt5.QtCore import Qt

from app_theme import apply_application_theme
from gui_main import FormManager
import gui_main


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


def test_partial_form_badge_is_shown_on_queue_row():
    window = FormManager()
    window.form_list.clear()
    window.forms_data.clear()
    item = window._add_form_to_queue("https://docs.google.com/forms/d/form-1/edit", "Algebra", source="Test")

    window.update_skipped_form(
        "form-1",
        "",
        "Missing teacher answer key",
        '[{"question_number": 5, "title": "8 c)", "responses": 2}]',
    )

    widget = window.form_list.itemWidget(item)
    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "partial"
    assert widget._badge_label.text() == "PARTIAL"
    assert widget._eta_label.text() == "Partial"
    assert "Missing teacher answer key" in widget._detail_label.text()
    window.form_list.setCurrentItem(item)
    window._on_form_selection_changed(item)
    assert "Q5: 8 c)" in window.detail_warning.text()


def test_partial_form_badge_can_match_queue_row_by_url():
    window = FormManager()
    window.form_list.clear()
    window.forms_data.clear()
    url = "https://docs.google.com/forms/d/form-1/edit"
    item = window._add_form_to_queue(url, "Algebra", source="Test")

    window.update_skipped_form("", url, "Missing teacher answer key", "[]")

    widget = window.form_list.itemWidget(item)
    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "partial"
    assert widget._badge_label.text() == "PARTIAL"


def test_finished_event_does_not_overwrite_partial_badge():
    window = FormManager()
    window.form_list.clear()
    window.forms_data.clear()
    item = window._add_form_to_queue("https://docs.google.com/forms/d/form-1/edit", "Algebra", source="Test")
    window.update_skipped_form("form-1", "", "Missing teacher answer key", "[]")

    window.update_finished_form("form-1")

    widget = window.form_list.itemWidget(item)
    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "partial"
    assert widget._badge_label.text() == "PARTIAL"


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
        "id=llamacpp-1 provider=llamacpp status=running model=local/test.gguf "
        "request=judge-batch-1 latency_ms=0 queue_wait_ms=5"
    )
    assert window.provider_worker_states["llamacpp-1"]["state"] == "running"
    provider_card = window.provider_worker_cards["llamacpp-1"]
    assert provider_card["status"].text() == "Running"
    assert provider_card["primary"].text() == "local/test.gguf"
    assert "judge-batch-1" in provider_card["secondary"].text()
    assert window.provider_worker_summary.text()


def test_model_health_dashboard_summarizes_provider_metrics():
    window = FormManager()
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

    assert "tencent/hy3:free" in window.model_health_rows["current"]["detail"].text()
    assert "87.5%" in window.model_health_rows["success"]["detail"].text()
    assert "12 rate-limited" in window.model_health_rows["limits"]["detail"].text()
    assert "6 JSON failures" in window.model_health_rows["json"]["detail"].text()
    assert "0.900" in window.model_health_rows["quality"]["detail"].text()
    assert "1m 0s" in window.model_health_rows["cooldown"]["detail"].text()
    assert "$0.1235" in window.model_health_rows["cost"]["detail"].text()
    assert "fresh then reused reuse enabled" in window.model_health_rows["reason"]["detail"].text()


def test_ai_worker_rows_use_transformers_names_and_expand_dynamically(monkeypatch):
    window = FormManager()
    assert window.app_worker_cards["ai-1"]["title"].text() == "Optimus Prime"

    monkeypatch.setattr(
        window,
        "_configured_worker_counts",
        lambda: {"ai": 6, "openrouter": 4, "llamacpp": 1, "ollama": 1},
    )
    window._sync_worker_cards_to_config()

    assert len(window.app_worker_cards) >= 6
    assert window.app_worker_cards["ai-2"]["title"].text() == "Bumblebee"
    assert window.app_worker_cards["ai-5"]["title"].text() == "Arcee"
    assert window.app_worker_cards["ai-6"]["title"].text() == "Jazz"


def test_provider_worker_rows_expand_dynamically(monkeypatch):
    window = FormManager()
    monkeypatch.setattr(
        window,
        "_configured_worker_counts",
        lambda: {"ai": 4, "openrouter": 6, "llamacpp": 1, "ollama": 2},
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
    assert "Global Settings" in source
    assert "OpenRouter" in source
    assert "llama.cpp" in source
    assert "Ollama" in source
    settings_sections = source[source.index('global_form = make_settings_section'):]
    assert settings_sections.index('"Global Settings"') < settings_sections.index('"OpenRouter"')
    assert settings_sections.index('"OpenRouter"') < settings_sections.index('"llama.cpp"')
    assert settings_sections.index('"llama.cpp"') < settings_sections.index('"Ollama"')
    assert "scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)" in source
    assert 'openrouter_form.addRow("Answers per Judge Call:"' in source
    assert 'llamacpp_form.addRow("Answers per Judge Call:"' in source
    assert 'ollama_form.addRow("Answers per Judge Call:"' in source
    assert 'global_form.addRow("AI Worker Threads:"' not in source
    assert 'openrouter_form.addRow("AI Worker Threads:", openrouter_ai_worker_count_spin)' in source
    assert 'llamacpp_form.addRow("AI Worker Threads:", llamacpp_ai_worker_count_spin)' in source
    assert 'ollama_form.addRow("AI Worker Threads:", ollama_ai_worker_count_spin)' in source
    assert 'config_data["openrouter_ai_worker_count"]' in source
    assert 'config_data["llamacpp_ai_worker_count"]' in source
    assert 'config_data["ollama_ai_worker_count"]' in source
    assert 'llamacpp_form.addRow("Model Folder:", llamacpp_model_dir_picker)' in source
    assert 'llamacpp_form.addRow("Context Size:", llamacpp_context_size_spin)' in source
    assert 'llamacpp_form.addRow("GPU Layers:", llamacpp_gpu_layers_combo)' in source
    assert 'llamacpp_form.addRow("Generation Threads:", llamacpp_threads_spin)' in source
    assert 'llamacpp_form.addRow("Batch Threads:", llamacpp_threads_batch_spin)' in source
    assert 'llamacpp_form.addRow("Server Batch Size:", llamacpp_server_batch_size_spin)' in source
    assert 'llamacpp_form.addRow("Server Micro-batch:", llamacpp_server_ubatch_size_spin)' in source
    assert 'llamacpp_form.addRow("Flash Attention:", llamacpp_flash_attn_combo)' in source
    assert 'llamacpp_form.addRow("K Cache Type:", llamacpp_cache_type_k_combo)' in source
    assert 'llamacpp_form.addRow("V Cache Type:", llamacpp_cache_type_v_combo)' in source
    assert 'llamacpp_form.addRow("Parallel Slots:", llamacpp_parallel_spin)' in source
    assert 'config_data["llamacpp_server_context_size"]' in source
    assert 'config_data["llamacpp_server_gpu_layers"]' in source
    assert 'config_data["llamacpp_server_mmap"]' in source
    assert 'config_data["llamacpp_server_jinja"]' in source
    assert 'llamacpp_form.addRow("Auto-start Server:", llamacpp_auto_start_checkbox)' in source
    assert 'llamacpp_form.addRow("Server Executable:", llamacpp_server_exe_picker)' in source
    assert 'QFileDialog.getExistingDirectory' in source
    assert 'QFileDialog.getOpenFileName' in source
    assert 'Select llama.cpp Model Folder' in source
    assert 'Select llama-server.exe' in source
    assert 'global_form.addRow("Acceptance Diversity:", distinct_models_checkbox)' in source
    assert 'visible_settings_jury_roles = ("semantic_judge", "factual_judge", "concept_judge", "strict_judge")' in source
    assert "if role not in visible_settings_jury_roles:" in source
    assert "Stop llama.cpp server after grading" in source
    assert "Stop llama.cpp server when app closes" in source
    assert "No llama.cpp GGUF models found" in source
    assert "mmproj files are hidden" in source
    assert "OpenRouter Monitor Model:" in source
    assert 'config_data["openrouter_supervisor_ollama_model"]' in source
    assert 'config_data["ollama_judge_answer_batch_size"]' in source
    assert 'config_data["openrouter_judge_answer_batch_size"]' in source
    assert 'config_data["llamacpp_judge_answer_batch_size"]' in source
    assert 'config_data["llamacpp_stop_server_after_grading"]' in source
    assert 'config_data["llamacpp_stop_server_on_app_close"]' in source
    assert '_stop_llamacpp_server_if_enabled("llamacpp_stop_server_after_grading"' in source
    assert '_stop_llamacpp_server_if_enabled("llamacpp_stop_server_on_app_close"' in source
    assert "llama.cpp-only mode is selected" in source
    assert "Grading was not started" in source
    assert "_start_llamacpp_server(preflight_cfg)" in source
    assert 'QProgressDialog(' in source
    assert '"Loading llama.cpp"' in source
    assert "Large GGUF models can take a few minutes to load." in source


def test_llamacpp_server_command_uses_configurable_performance_defaults():
    command = FormManager._llamacpp_server_command(
        None,
        {},
        r"C:\Tools\llama.cpp\llama-server.exe",
        r"C:\models\Qwen3.5-9B-Q4_K_M.gguf",
        "127.0.0.1",
        8081,
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
    command = FormManager._llamacpp_server_command(
        None,
        {"llamacpp_server_mmap": False, "llamacpp_server_jinja": False},
        "server.exe",
        "model.gguf",
        "127.0.0.1",
        8081,
    )
    assert "--no-mmap" in command
    assert "--no-jinja" in command


def test_settings_hides_low_level_expert_controls():
    source_lines = __import__("pathlib").Path("gui_main.py").read_text(encoding="utf-8").splitlines()
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


def _make_window():
    window = FormManager()
    window.save_forms = lambda: None
    return window


def _add_form(window, url, title):
    item = window._add_form_to_queue(url, title, source="Test")
    return item


def test_queue_list_has_custom_context_menu_policy():
    window = _make_window()
    assert window.form_list.contextMenuPolicy() == Qt.CustomContextMenu
    assert window.form_list.customContextMenuRequested is not None


def test_context_menu_builds_expected_actions_and_separators():
    window = _make_window()
    window.form_list.clear()
    window.forms_data.clear()
    _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    second = _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")

    menu = window._build_form_context_menu(second)
    assert _menu_item(menu, "Requeue (Reset to Queued)") is not None
    labels = set()
    for act in menu.actions():
        if not act.isSeparator():
            labels.add(act.text())
    assert {"Grade Now", "Open Answer Key Dashboard", "Requeue (Reset to Queued)",
            "Mark as Done", "Mark as Skipped", "Move to Top", "Move Up",
            "Move Down", "Move to Bottom", "Copy URL", "Open in Browser",
            "Remove from Queue"}.issubset(labels)
    seps = [a.isSeparator() for a in menu.actions()]
    assert seps.count(True) == 4


def _menu_item(menu, partial):
    for act in menu.actions():
        if partial in act.text() and not act.isSeparator():
            return act
    return None


def test_context_menu_move_enabled_state_tracks_row_boundaries():
    window = _make_window()
    window.form_list.clear()
    window.forms_data.clear()
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
    window.form_list.clear()
    window.forms_data.clear()
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
    window.form_list.clear()
    window.forms_data.clear()
    item = _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")

    window._context_set_status(item, "done")
    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "done"
    assert window.form_list.itemWidget(item)._badge_label.text() == "DONE"

    window._context_set_status(item, "skipped")
    meta = item.data(Qt.UserRole + 1) or {}
    assert meta["status"] == "skipped"
    assert window.form_list.itemWidget(item)._badge_label.text() == "SKIPPED"


def test_context_move_to_top_and_bottom_reorders_queue_and_saves():
    window = _make_window()
    window.form_list.clear()
    window.forms_data.clear()
    urls = ["https://docs.google.com/forms/d/a/edit",
            "https://docs.google.com/forms/d/b/edit",
            "https://docs.google.com/forms/d/c/edit"]
    for url in urls:
        _add_form(window, url, url.split("/d/")[1].split("/")[0])

    def ordered_urls():
        return [window.form_list.item(i).data(Qt.UserRole) for i in range(window.form_list.count())]

    last = window.form_list.item(2)
    window._context_move(last, "top")
    assert ordered_urls()[0] == urls[2]
    assert list(window.forms_data.keys())[0] == urls[2]

    first = window.form_list.item(0)
    window._context_move(first, "bottom")
    assert ordered_urls()[-1] == urls[2]


def test_context_move_keeps_item_widget_attached():
    window = _make_window()
    window.form_list.clear()
    window.forms_data.clear()
    urls = ["https://docs.google.com/forms/d/a1/edit",
            "https://docs.google.com/forms/d/b1/edit"]
    for url in urls:
        _add_form(window, url, url)

    second = window.form_list.item(1)
    second_widget = window.form_list.itemWidget(second)
    window._context_move(second, "top")
    moved = window.form_list.item(0)
    assert moved is second
    assert window.form_list.itemWidget(moved) is second_widget


def test_context_remove_single_item_deletes_and_saves(monkeypatch):
    window = _make_window()
    window.form_list.clear()
    window.forms_data.clear()
    _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    item = _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")

    monkeypatch.setattr(window, "save_forms", lambda: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    window._context_remove(item, item.data(Qt.UserRole))

    assert window.form_list.count() == 1
    assert window.form_list.item(0).data(Qt.UserRole) == "https://docs.google.com/forms/d/a/edit"


def test_context_remove_cancelled_keeps_item(monkeypatch):
    window = _make_window()
    window.form_list.clear()
    window.forms_data.clear()
    _add_form(window, "https://docs.google.com/forms/d/a/edit", "Algebra")
    item = _add_form(window, "https://docs.google.com/forms/d/b/edit", "Fractions")

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    window._context_remove(item, item.data(Qt.UserRole))

    assert window.form_list.count() == 2


def test_more_menu_exposes_export_audit_report_and_theme():
    window = _make_window()
    labels = []
    more_button = next(
        (btn for btn in window.findChildren(QPushButton) if btn.text() == "..."),
        None,
    )
    if more_button is not None and more_button.menu() is not None:
        labels = [a.text() for a in more_button.menu().actions() if not a.isSeparator()]
    assert any("Export Results" in label for label in labels)
    assert any("Decision Audit" in label for label in labels)
    assert any("Run Report" in label for label in labels)
    assert any("Dark Mode" in label or "Light Mode" in label for label in labels)


def test_keyboard_shortcuts_are_registered():
    from PyQt5.QtWidgets import QShortcut
    window = _make_window()
    shortcuts = window.findChildren(QShortcut)
    keys = [s.key().toString() for s in shortcuts]
    for expected in ("Ctrl+R", "Ctrl+D", "Ctrl+Shift+A", "Ctrl+A", "Ctrl+K", "Ctrl+E", "Ctrl+Shift+S", "Del"):
        assert expected in keys, f"missing shortcut {expected}"


def test_dark_mode_toggle_flips_state_and_persists(tmp_path, monkeypatch):
    from app_theme import set_dark_mode, is_dark_mode
    window = _make_window()
    set_dark_mode(False)
    window.toggle_dark_mode()
    assert is_dark_mode() is True
    with open("config.json", "r", encoding="utf-8") as fh:
        import json
        cfg = json.load(fh)
    assert cfg.get("dark_mode") is True
    window.toggle_dark_mode()
    assert is_dark_mode() is False


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


def test_auto_cycle_search_window_respects_grading_mode():
    """Recent Only mode uses the recency window; Whole Form scans full history."""
    window = _make_window()
    window.last_check_time = None
    window.recency_minutes = 5
    window.is_searching = False
    window.is_closing = False
    window.auto_mode = True

    from datetime import datetime, timezone

    captured = {}

    def fake_search_thread(folders, from_dt, to_dt):
        captured["from_dt"] = from_dt
        captured["to_dt"] = to_dt
        return _FakeSearchThread()

    _patch(window, "append_debug", lambda *a, **k: None)
    original_thread = gui_main.SearchThread
    try:
        gui_main.SearchThread = fake_search_thread
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
    finally:
        gui_main.SearchThread = original_thread


def test_on_auto_search_finished_forces_recent_only_by_mode():
    window = _make_window()
    window.grading_mode = "Recent Only"
    window.is_closing = False
    window.is_searching = False
    calls = []

    def fake_run_grader(**kwargs):
        calls.append(kwargs)

    _patch(window, "run_grader", fake_run_grader)
    _patch(window, "save_forms", lambda: None)
    _patch(window, "_add_form_to_queue", lambda *a, **k: None)
    _patch(window, "_notify", lambda *a, **k: None)
    _patch(window, "append_debug", lambda *a, **k: None)
    _patch(window, "schedule_next_cycle", lambda: None)

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


def test_on_auto_search_finished_notifies_when_nothing_found():
    window = _make_window()
    window.grading_mode = "Whole Form"
    window.is_closing = False
    window.is_searching = False
    window.form_list.clear()
    window.forms_data.clear()
    notified = []

    _patch(window, "run_grader", lambda **k: None)
    _patch(window, "save_forms", lambda: None)
    _patch(window, "_add_form_to_queue", lambda *a, **k: None)
    _patch(window, "_notify", lambda *a, **k: notified.append(a))
    _patch(window, "append_debug", lambda *a, **k: None)
    _patch(window, "schedule_next_cycle", lambda: None)

    window.on_auto_search_finished([])
    assert notified, "Expected a notification when no submissions are found"


def _patch(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    return original


def _restore(obj, name):
    try:
        if hasattr(obj, "__dict__") and name in obj.__dict__:
            del obj.__dict__[name]
        elif hasattr(obj, name):
            delattr(obj, name)
    except Exception:
        pass


class _FakeSignal:
    def connect(self, *_args):
        return self


class _FakeSearchThread:
    progress = _FakeSignal()
    finished = _FakeSignal()

    def start(self):
        pass

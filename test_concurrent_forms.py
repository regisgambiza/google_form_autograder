import json
from pathlib import Path

import main
import grader_thread
from evaluator_config import DEFAULT_CONFIG


def _chunks(items, n):
    return [list(c) for c in main.chunked(items, n)]


def test_chunked_empty():
    assert _chunks([], 3) == []


def test_chunked_smaller_than_chunk_size():
    assert _chunks([1, 2], 3) == [[1, 2]]


def test_chunked_exactly_chunk_size():
    assert _chunks([1, 2, 3], 3) == [[1, 2, 3]]


def test_chunked_larger_than_chunk_size():
    assert _chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_chunked_remainder():
    assert _chunks(list(range(10)), 4) == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]


def test_chunked_single():
    assert _chunks([1, 2, 3], 1) == [[1], [2], [3]]


def test_concurrent_forms_default_is_one():
    assert DEFAULT_CONFIG.get("concurrent_forms", 1) == 1


def test_concurrent_forms_loaded_with_default_when_absent():
    from evaluator_config import load_config

    cfg = load_config("config.json")
    assert cfg.get("concurrent_forms", 1) == 1


def test_form_done_line_parsing_emits_form_done_signal():
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])

    def handle(form_id, total, accepted, review, rejected):
        received.append((form_id, total, accepted, review, rejected))

    received = []
    thread = grader_thread.GraderThread(form_urls=[])
    thread.form_done.connect(handle)

    thread.parse_line("FormDone: abc123 total=10 accepted=7 review=2 rejected=1")

    assert received == [("abc123", 10, 7, 2, 1)]


def test_form_done_without_form_id_is_ignored():
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])

    received = []
    thread = grader_thread.GraderThread(form_urls=[])
    thread.form_done.connect(lambda *args: received.append(args))

    thread.parse_line("FormDone: total=10 accepted=7 review=2 rejected=1")

    assert received == []


def test_grader_thread_source_parses_form_done():
    source = Path("grader_thread.py").read_text(encoding="utf-8")
    assert "form_done = Signal" in source
    assert 'ls.startswith("FormDone:")' in source


def test_settings_dialog_creates_and_persists_concurrent_forms():
    source = Path("settings_dialog.py").read_text(encoding="utf-8")
    assert "concurrent_forms_spin = QSpinBox(dialog)" in source
    assert "concurrent_forms_spin.setRange(1, 8)" in source
    assert 'config_data["concurrent_forms"] = int(concurrent_forms_spin.value())' in source
    assert 'cfg.get("concurrent_forms", 1)' in source


def test_dispatcher_emits_form_done_line():
    source = Path("global_dispatcher.py").read_text(encoding="utf-8")
    assert 'f"FormDone: {form_id} total={total} accepted={accepted} "' in source


def test_main_dispatch_branch_uses_concurrent_forms_chunks():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "chunked(form_urls, concurrent_forms)" in source
    assert "run_global_dispatcher(form_urls=list(chunk)" in source
    assert "config.get(\"concurrent_forms\", 1)" in source


def test_main_window_wires_form_done_signal():
    source = Path("gui_studio/main_window.py").read_text(encoding="utf-8")
    assert "self.grader_thread.form_done.connect(self.update_form_done)" in source
    assert "def update_form_done(self, form_id, total, accepted, review, rejected):" in source


def test_grader_thread_parses_form_row_progress():
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    received = []

    thread = grader_thread.GraderThread()
    thread.form_row_progress.connect(lambda fid, done, total: received.append((fid, done, total)))
    thread.parse_line("FormRowProgress: abc123 42/96")
    thread.parse_line("FormRowProgress:  0/0")  # missing form id ignored
    thread.parse_line("garbage")

    assert received == [("abc123", 42, 96)]


def test_grader_thread_parses_form_totals():
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    received = []

    thread = grader_thread.GraderThread()
    thread.form_totals.connect(lambda fid, total: received.append((fid, total)))
    thread.parse_line("FormTotals: abc123 263")
    thread.parse_line("FormTotals: abc123 -5")  # negative totals ignored
    thread.parse_line("FormTotals: 0")          # missing id ignored

    assert received == [("abc123", 263)]


def test_main_window_wires_per_form_progress_signals():
    source = Path("gui_studio/main_window.py").read_text(encoding="utf-8")
    assert "form_row_progress.connect(self.update_form_row_progress)" in source
    assert "form_totals.connect(self.update_form_totals)" in source
    assert "def update_form_row_progress(self, form_id, done, total):" in source
    assert "def update_form_totals(self, form_id, total):" in source


def test_dispatcher_emits_per_form_build_totals_and_row_progress():
    dispatcher_source = Path("global_dispatcher.py").read_text(encoding="utf-8")
    assert "FormTotals:" in dispatcher_source
    assert "FormRowProgress:" in dispatcher_source
    assert "form_progress[i] = " in dispatcher_source


def test_protocol_lines_are_guarded_against_dead_stdout_pipe():
    """Windows raises OSError [Errno 22] on a closed pipe; protocol prints
    must never crash grading workers."""
    import re

    dispatcher_source = Path("global_dispatcher.py").read_text(encoding="utf-8")
    assert "def _progress_print(" in dispatcher_source
    for marker in (
        "FormProgress:", "FormRowProgress:", "FormTotals:",
        "FormDone:", "QuestionAvailableForReview:",
    ):
        assert marker in dispatcher_source
        assert not re.search(
            rf"(?<!_progress_)print\((f?['\"]|f?\n\s*){marker}", dispatcher_source
        )


def test_requeue_injection_failure_exhausts_task_attempts():
    """A task finalized via injection failure must never be rescheduled."""
    dispatcher_source = Path("global_dispatcher.py").read_text(encoding="utf-8")
    assert "requeue_attempts[task_id(t)] = requeue_max_attempts" in dispatcher_source


def test_staged_queues_are_unbounded_so_requeues_never_blocked():
    dispatcher_source = Path("global_dispatcher.py").read_text(encoding="utf-8")
    assert 'ai_batch_q: "queue.Queue[Optional[QuestionBatch]]" = queue.Queue(\n        maxsize=0 if staged_startup' in dispatcher_source

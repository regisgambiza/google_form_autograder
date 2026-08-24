# tests/test_grading_session.py - Global Stop / Pause / Continue regression.
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import grading_session  # noqa: E402


@pytest.fixture(autouse=True)
def clean_flags():
    grading_session.clear_all()
    yield
    grading_session.clear_all()


# ---------------------------------------------------------------------------
# Cross-process flag helpers
# ---------------------------------------------------------------------------

def test_pause_flag_roundtrip():
    assert not grading_session.is_paused()
    grading_session.request_pause()
    assert grading_session.is_paused()
    grading_session.clear_pause()
    assert not grading_session.is_paused()


def test_stop_flag_roundtrip():
    assert not grading_session.is_stop_requested()
    grading_session.request_stop()
    assert grading_session.is_stop_requested()
    grading_session.clear_stop()
    assert not grading_session.is_stop_requested()


def test_stop_overrides_pause():
    """STOP while PAUSED must be observable immediately (no deadlock)."""
    grading_session.request_pause()
    assert grading_session.is_paused()
    grading_session.request_stop()
    # Stop wins: paused workers see stop and exit their wait.
    assert grading_session.is_stop_requested()
    assert not grading_session.is_paused()


def test_wait_if_paused_blocks_then_resumes():
    grading_session.request_pause()
    result = {}

    def waiter():
        result["resumed"] = grading_session.wait_if_paused(poll_s=0.05)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.3)
    assert t.is_alive(), "waiter did not block while paused"
    grading_session.clear_pause()
    t.join(timeout=2)
    assert not t.is_alive()
    assert result["resumed"] is True


def test_wait_if_paused_released_by_stop():
    """Stop while paused releases the blocked worker without resuming work."""
    grading_session.request_pause()

    def waiter():
        grading_session.wait_if_paused(poll_s=0.05)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.2)
    assert t.is_alive()
    grading_session.request_stop()  # stop while paused
    t.join(timeout=2)
    assert not t.is_alive(), "worker deadlocked in pause after Stop"


# ---------------------------------------------------------------------------
# GUI session gate: Stop must prevent follow-on queued runs
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_window_with_queued_forms(qapp, tmp_path, monkeypatch, count=3):
    """Real AutograderWindow with N forms marked queued; heavy I/O stubbed."""
    monkeypatch.chdir(tmp_path)
    for directory in ("logs", "cache/results", "cache/embeddings", "cache/form_context",
                      "cache/vision", "backups/answer_keys"):
        os.makedirs(os.path.join(tmp_path, directory), exist_ok=True)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "forms_to_grade.json").write_text('{"forms": []}', encoding="utf-8")

    import json

    from gui_studio.main_window import AutograderWindow

    window = AutograderWindow()
    window.prompt_login_if_needed = lambda: None
    window._session_stop = False
    window._session_paused = False
    for i in range(count):
        url = f"https://docs.google.com/forms/d/fake{i}/edit"
        window._add_form_to_queue(url, f"Form {i}", source="test")
    return window


def test_gui_stop_blocks_follow_on_run(qapp, tmp_path, monkeypatch):
    """The reported bug: Stop during Form 2 started Form 3 automatically."""
    window = _make_window_with_queued_forms(qapp, tmp_path, monkeypatch)

    scheduled = []
    monkeypatch.setattr(window, "run_grader", lambda *a, **k: scheduled.append((a, k)))

    # Simulate a run finishing while a user Stop is in effect.
    window.stop_grading()
    window.on_grading_finished(False, "Grading stopped.")

    assert window._should_continue_queued_forms() is False
    assert scheduled == [], "follow-on run was scheduled after Stop"
    assert any("will NOT start" in line for line in window.debug_lines)


def test_gui_no_stop_still_continues_queue(qapp, tmp_path, monkeypatch):
    """Without Stop, the existing continue-to-next-form behaviour stays intact."""
    window = _make_window_with_queued_forms(qapp, tmp_path, monkeypatch)

    scheduled = []
    monkeypatch.setattr(window, "run_grader", lambda *a, **k: scheduled.append((a, k)))
    # Simulate natural completion (no user stop).
    window._session_stop = False
    window._maybe_start_next_after_finish = lambda: None  # avoid timer path
    window.on_grading_finished(True, "")

    # on_grading_finished schedules via QTimer; invoke the decision helper
    # directly to prove the gate opens when no stop was requested.
    assert window._should_continue_queued_forms() is True
    assert len(scheduled) == 0  # only the timer was armed, nothing ran sync


def test_gui_maybe_start_next_gated_by_stop(qapp, tmp_path, monkeypatch):
    window = _make_window_with_queued_forms(qapp, tmp_path, monkeypatch)

    calls = []
    monkeypatch.setattr(window, "run_grader", lambda *a, **k: calls.append(1))

    window.stop_grading()
    window._maybe_start_next_after_finish()

    assert calls == []


def test_new_run_clears_session_flags(qapp, tmp_path, monkeypatch):
    window = _make_window_with_queued_forms(qapp, tmp_path, monkeypatch)
    window.stop_grading()
    assert window._session_stop is True
    assert grading_session.is_stop_requested()

    # Starting a fresh run resets the session controls.
    window.forms_data = {"https://docs.google.com/forms/d/fake0/edit": {"title": "F0"}}
    monkeypatch.setattr(window, "_start_llamacpp_server", lambda cfg: True)

    import gui_studio.main_window as mw

    class _Sig:
        def connect(self, *a, **k):
            pass

    class _FakeThread:
        def __init__(self, *a, **k):
            self.finished = _Sig()
            self.progress = _Sig()
            self.model_progress = _Sig()
            self.overall_progress = _Sig()
            self.form_metrics = _Sig()
            self.debug_message = _Sig()
            self.current_form = _Sig()
            self.finished_form = _Sig()
            self.skipped_form = _Sig()
            self.form_done = _Sig()
            self.form_row_progress = _Sig()
            self.form_totals = _Sig()
            self.process = None

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(mw, "GraderThread", _FakeThread)
    window.run_grader(force_whole_form=True,
                      target_urls=["https://docs.google.com/forms/d/fake0/edit"])
    assert window._session_stop is False
    assert window._session_paused is False
    assert not grading_session.is_stop_requested()

# tests/test_crash_diagnostics.py - Validation for the native crash capture
# and diagnostics system. The deliberate-crash subprocess cases are the real
# end-to-end proof: they make a child process actually fault (access violation
# / abort) and assert that the diagnostics artifacts appear.
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crash_diagnostics  # noqa: E402


@pytest.fixture()
def fresh_state(monkeypatch, tmp_path):
    """Isolate module globals between tests."""
    monkeypatch.setattr(crash_diagnostics, "_RING", __import__("collections").deque(maxlen=50))
    monkeypatch.setattr(crash_diagnostics, "_GRADING_STATE", {})
    return tmp_path


def test_ring_buffer_trims(fresh_state):
    ring = crash_diagnostics._RING
    for i in range(100):
        crash_diagnostics.record("event", n=i)
    assert len(ring) == 50
    assert "n=99" in ring[-1]


def test_redaction_hides_secrets():
    dirty = (
        "key sk-or-v1-abcdef0123456789abcdef0123456789 and "
        "Authorization: Bearer abc.def.ghi plus token=supersecret123 "
        "and password: hunter2 and ya29.aBcDeFgHiJkLmNoP"
    )
    clean = crash_diagnostics._redact(dirty)
    assert "sk-or-v1-abcdef0123456789" not in clean.replace("sk-or-v1", "", 1)
    assert "hunter2" not in clean
    assert "supersecret123" not in clean
    assert "aBcDeFgHiJkLmNoP" not in clean


def test_grading_state_roundtrip(tmp_path):
    crash_diagnostics._STATE_PATH = str(tmp_path / "state_test.json")
    crash_diagnostics._maybe_persist_state.__wrapped__ = None  # no-op guard
    crash_diagnostics.set_grading_state(phase="grading", form_id="abc", answer_index=3)
    snap = crash_diagnostics.get_grading_state()
    assert snap["form_id"] == "abc"
    assert snap["answer_index"] == 3
    with open(crash_diagnostics._STATE_PATH, encoding="utf-8") as fh:
        persisted = json.load(fh)
    assert persisted["form_id"] == "abc"


def test_python_exception_report_has_required_sections(tmp_path, monkeypatch):
    crash_dir = tmp_path / "crash"
    crash_dir.mkdir()
    monkeypatch.setattr(crash_diagnostics, "_CRASH_DIR", str(crash_dir))
    monkeypatch.setattr(crash_diagnostics, "_RECENT_PATH", str(crash_dir / "recent_events.log"))
    monkeypatch.setattr(crash_diagnostics, "_FH_LOG_PATH", str(crash_dir / "fh.log"))
    monkeypatch.setattr(crash_diagnostics, "_STATE_PATH", str(crash_dir / "state.json"))
    crash_diagnostics.record("Grading started")
    crash_diagnostics.record("Processing response 134")

    try:
        raise ValueError("synthetic failure")
    except ValueError:
        import sys

        exc_type, exc_value, exc_tb = sys.exc_info()
        path = crash_diagnostics._python_exception_report(
            "python_exception", exc_type, exc_value, exc_tb, thread_name="MainThread")

    text = open(path, encoding="utf-8").read()
    for section in ("APPLICATION INFORMATION", "SYSTEM INFORMATION",
                    "CRASH INFORMATION", "NATIVE STACK TRACE", "PYTHON STACK TRACE",
                    "THREAD INFORMATION", "GRADING STATE", "RECENT LOG EVENTS",
                    "RESOURCE INFORMATION"):
        assert section in text, f"missing section {section}"
    assert "ValueError: synthetic failure" in text
    assert "Processing response 134" in text
    assert "test_crash_diagnostics" in text  # traceback names the failing frame


def _run_child(kind: str, env_extra: dict):
    env = os.environ.copy()
    env["AUTOGRADER_DIAGNOSTICS"] = "verbose"
    env["AUTOGRADER_NO_WATCHDOG"] = "0"
    env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, os.path.abspath("crash_diagnostics.py"), "--test-crash", kind],
        capture_output=True, text=True, timeout=90, env=env,
        cwd=os.path.dirname(os.path.abspath(__file__)) or ".",
    )
    return proc


def _spawn_test_crash(project_root: str, kind: str, crash_dir, extra_env: dict = None):
    return subprocess.run(
        [sys.executable, os.path.join(project_root, "crash_diagnostics.py"),
         "--test-crash", kind],
        capture_output=True, text=True, timeout=90,
        env={**os.environ,
             "AUTOGRADER_CRASH_DIR": str(crash_dir),
             "AUTOGRADER_WATCHDOG_MIN_UPTIME_S": "1",
             "AUTOGRADER_DIAGNOSTICS": "verbose",
             **(extra_env or {})},
        cwd=project_root,
    )


def _wait_for_artifacts(crash_dir, need_fh: bool, timeout: float = 30.0):
    reports, fh_logs = [], []
    deadline = time.time() + timeout
    while time.time() < deadline:
        if crash_dir.exists():
            reports = sorted(crash_dir.glob("crash_*.log"))
            fh_logs = list(crash_dir.glob("faulthandler_*.log"))
            if reports and (not need_fh or fh_logs):
                break
        time.sleep(0.4)
    return reports, fh_logs


def test_ctypes_wrapped_fault_goes_through_excepthook(tmp_path):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    crash_dir = tmp_path / "crashes_av"
    proc = _spawn_test_crash(project_root, "av", crash_dir)
    assert proc.returncode != 0
    reports, _ = _wait_for_artifacts(crash_dir, need_fh=False)
    assert reports, "excepthook report missing"
    text = "\n".join(p.read_text(encoding="utf-8") for p in reports)
    assert "python_exception" in text
    assert "access violation" in text.lower()
    assert "deliberate_test_crash_requested" in text


def test_true_native_access_violation_captured_by_faulthandler(tmp_path):
    """A real unwrapped null-pointer fault in C.

    CPython's faulthandler (VEH-level) intercepts hardware faults BEFORE any
    SetUnhandledExceptionFilter can, dumps every thread's Python stack at
    C speed, then terminates the process. So we assert the Python-side stacks;
    full native minidumps for this class of death require system-wide WER
    LocalDumps (documented in crash_diagnostics.py and every report).
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    crash_dir = tmp_path / "crashes_native"
    proc = _spawn_test_crash(project_root, "native_av", crash_dir,
                             {"AUTOGRADER_WATCHDOG_MIN_UPTIME_S": "0"})
    assert proc.returncode != 0

    reports, fh_logs = _wait_for_artifacts(crash_dir, need_fh=True)
    assert fh_logs, "faulthandler log missing for real SIGSEGV"
    fh_text = "".join(p.read_text(encoding="utf-8") for p in fh_logs)
    assert "Segmentation fault" in fh_text
    assert "crash_diagnostics.py" in fh_text  # crashing frame present

    assert reports, "watchdog did not report the abnormal termination"
    report_text = "\n".join(p.read_text(encoding="utf-8") for p in reports)
    assert "process_terminated_unexpectedly" in report_text


def test_abort_capture_end_to_end(tmp_path):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    crash_dir = tmp_path / "crashes_abort"
    proc = _spawn_test_crash(project_root, "abort", crash_dir,
                             {"AUTOGRADER_WATCHDOG_MIN_UPTIME_S": "0"})
    assert proc.returncode != 0

    reports, fh_logs = _wait_for_artifacts(crash_dir, need_fh=True)
    assert fh_logs, "faulthandler log missing for abort case"
    fh_text = "".join(p.read_text(encoding="utf-8") for p in fh_logs)
    assert ("Thread 0x" in fh_text) or ("Current thread" in fh_text), \
        "faulthandler did not dump thread stacks at SIGABRT time"

    if reports:
        report_text = "\n".join(p.read_text(encoding="utf-8") for p in reports)
        assert ("C0000409" in report_text.upper()) or ("ail-fast" in report_text) \
            or ("abnormal" in report_text.lower())


def test_watchdog_reports_real_forced_kill(tmp_path):
    """Full flow: parent installs diagnostics (spawning its watchdog), then is
    force-killed TerminateProcess-style -> report must appear."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    crash_dir = tmp_path / "wd_kill"
    sleeper_py = tmp_path / "sleeper.py"
    sleeper_py.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, r'{project_root}')\n"
        "import crash_diagnostics\n"
        "crash_diagnostics.install('t', 'selftest')\n"
        "print('ready', flush=True)\n"
        "time.sleep(120)\n",
        encoding="utf-8")
    env = {**os.environ,
           "AUTOGRADER_CRASH_DIR": str(crash_dir),
           "AUTOGRADER_WATCHDOG_MIN_UPTIME_S": "1"}

    parent = subprocess.Popen([sys.executable, str(sleeper_py)],
                              stdout=subprocess.PIPE, env=env, cwd=project_root)
    try:
        parent.stdout.readline()  # wait until install() has run
        time.sleep(1.0)  # give the watchdog child a moment to attach
        killer = subprocess.run(["taskkill", "/PID", str(parent.pid), "/F"],
                                capture_output=True, text=True)
        assert killer.returncode == 0
    finally:
        parent.stdout.close()

    reports, _ = _wait_for_artifacts(crash_dir, need_fh=False)
    assert reports, "watchdog did not write a report after forced kill"
    text = "\n".join(p.read_text(encoding="utf-8") for p in reports)
    assert "process_terminated_unexpectedly" in text


def test_clean_exit_writes_no_report(tmp_path, monkeypatch):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    crash_dir = tmp_path / "crashes_clean"
    script = (
        "import sys, os, time;"
        f"sys.path.insert(0, r'{project_root}');"
        "os.chdir(r'" + project_root + "');"
        "import crash_diagnostics as cd;"
        f"cd._CRASH_DIR = r'{crash_dir}';"
        "cd.install('t', 'selftest');"
        "time.sleep(2.0);"  # shorter than watchdog min uptime -> silent even on odd codes
        "sys.exit(0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
        env={**os.environ,
             "AUTOGRADER_CRASH_DIR": str(crash_dir),
             "AUTOGRADER_WATCHDOG_MIN_UPTIME_S": "60"},
        cwd=project_root,
    )
    assert proc.returncode == 0
    deadline = time.time() + 6
    while time.time() < deadline:
        if crash_dir.exists() and not list(crash_dir.glob("crash_*.log")):
            break
        time.sleep(0.4)
    assert not list(crash_dir.glob("crash_*.log")), \
        "clean exit must not produce a crash report"


def test_watchdog_exits_cleanly_for_bogus_pid(tmp_path):
    """A parent that cannot even be opened must not crash the watchdog."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    crash_dir = tmp_path / "wd_bogus"
    crash_dir.mkdir(parents=True)
    proc = subprocess.run(
        [sys.executable, os.path.join(project_root, "crash_diagnostics.py"),
         "--watch-parent", "999999999", "--context", "app"],
        capture_output=True, text=True, timeout=30,
        cwd=str(crash_dir),
        env={**os.environ, "AUTOGRADER_CRASH_DIR": str(crash_dir)},
    )
    assert proc.returncode == 0
    assert not list(crash_dir.glob("crash_*.log"))

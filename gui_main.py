# gui_main.py - Launcher for the Studio GUI (gui_studio package).
#
# The previous monolithic window (FormManager) was fully replaced by the
# gui_studio rebuild. This file remains the application entry point so the
# existing workflows keep working:
#   * `python gui_main.py` launches the new GUI.
#   * Frozen builds spawn `GoogleFormAutograder.exe --grader`, which runs the
#     grading pipeline in-process (handled inside gui_studio.entry.main).
#
# All backend modules (grader_thread, worker_pipeline, consensus_engine,
# ai_judges, provider_manager, ...) are untouched by the rebuild.
import os
import sys
import traceback
from datetime import datetime

_CRASH_LOG = os.path.join("logs", "gui_crash.log")


def _install_gui_crash_diagnostics() -> None:
    """Capture uncaught GUI exceptions to logs/gui_crash.log.

    The grader subprocess already survives a dead GUI pipe; this makes the
    GUI side diagnosable too — without it a Qt slot exception just kills the
    window silently and takes the subprocess's stdout reader with it
    (which then Errno-22-poisons the grading run).
    """
    try:
        os.makedirs("logs", exist_ok=True)
        _fh = open(_CRASH_LOG, "a", encoding="utf-8")
    except Exception:
        _fh = None

    def _hook(etype, value, tb):
        stamp = datetime.now().isoformat(timespec="seconds")
        text = "".join(traceback.format_exception(etype, value, tb))
        if _fh is not None:
            try:
                _fh.write(f"\n=== GUI CRASH {stamp} ===\n{text}\n")
                _fh.flush()
            except Exception:
                pass
        try:
            sys.__stderr__.write(f"=== GUI CRASH {stamp} ===\n{text}")
            sys.__stderr__.flush()
        except Exception:
            pass

    sys.excepthook = _hook


_install_gui_crash_diagnostics()

from gui_studio.entry import main  # noqa: E402  (after diagnostics install)

if __name__ == "__main__":
    main()

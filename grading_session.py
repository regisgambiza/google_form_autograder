# grading_session.py - Cross-process grading session control (Pause/Stop).
#
# The GUI owns the grading session; the actual grading pipeline runs in a
# child process (python main.py ...). Pause/Stop must therefore reach the
# child. Simplest reliable channel: flag files in logs/, polled by the
# dispatcher's worker loops at natural checkpoints ("before starting new
# work"). In-flight AI/API requests may finish; nothing new starts.
#
# State model:
#   RUNNING -> PAUSED -> RUNNING        (pause preserves the session)
#   RUNNING | PAUSED -> STOPPED         (stop always wins over pause)
import os
import time

_LOG_DIR = os.path.join("logs", "session")
PAUSE_FLAG_PATH = os.path.join(_LOG_DIR, "grading_pause.flag")
STOP_FLAG_PATH = os.path.join(_LOG_DIR, "grading_stop.flag")


def _write_flag(path: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def _clear_flag(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def request_pause() -> None:
    _write_flag(PAUSE_FLAG_PATH)


def clear_pause() -> None:
    _clear_flag(PAUSE_FLAG_PATH)


def request_stop() -> None:
    # Stop takes priority over pause: releasing a paused run happens by
    # clearing the pause flag; workers observe stop on their next poll.
    clear_pause()
    _write_flag(STOP_FLAG_PATH)


def clear_stop() -> None:
    _clear_flag(STOP_FLAG_PATH)


def clear_all() -> None:
    """Reset both controls (start of a brand-new grading session)."""
    clear_pause()
    clear_stop()


def is_paused() -> bool:
    return os.path.exists(PAUSE_FLAG_PATH) and not os.path.exists(STOP_FLAG_PATH)


def is_stop_requested() -> bool:
    return os.path.exists(STOP_FLAG_PATH)


def wait_if_paused(poll_s: float = 0.25, max_wait_s: float = 0.0) -> bool:
    """Block while paused. Returns False if a stop was requested (or the
    optional max_wait budget elapsed), True when running/resumed normally."""
    waited = 0.0
    while is_paused():
        if is_stop_requested():
            return False
        if max_wait_s > 0 and waited >= max_wait_s:
            return False
        time.sleep(poll_s)
        waited += poll_s
    return not is_stop_requested()


class GradingSessionStopped(Exception):
    """Raised inside long-running grading work when Stop was requested."""

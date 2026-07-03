import atexit
import datetime
import json
import os
import sys
import threading
import time

_LOG_LOCK = threading.Lock()
_DIAG_INIT = False
_DIAG_TEXT_FH = None
_DIAG_JSON_FH = None
_DIAG_TEXT_PATH = "logs/runtime_detailed.log"
_DIAG_JSON_PATH = "logs/runtime_detailed.jsonl"
_CONSOLE_MIN_LEVEL = "INFO"
_CONSOLE_ENABLED = True
_CONSOLE_STAGE_BANNERS = True
_CONSOLE_COLOR_ENABLED = True
_RUNTIME_STATE = {}
_RUNTIME_STATE_LOCK = threading.Lock()
_LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


def _init_diagnostics():
    global _DIAG_INIT, _DIAG_TEXT_FH, _DIAG_JSON_FH, _DIAG_TEXT_PATH, _DIAG_JSON_PATH
    global _CONSOLE_MIN_LEVEL, _CONSOLE_ENABLED, _CONSOLE_STAGE_BANNERS, _CONSOLE_COLOR_ENABLED
    if _DIAG_INIT:
        return
    _DIAG_INIT = True
    try:
        cfg = {}
        if os.path.exists("config.json"):
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
        _DIAG_TEXT_PATH = str(cfg.get("detailed_log_text_path", _DIAG_TEXT_PATH))
        _DIAG_JSON_PATH = str(cfg.get("detailed_log_json_path", _DIAG_JSON_PATH))
        _CONSOLE_MIN_LEVEL = str(cfg.get("console_log_min_level", _CONSOLE_MIN_LEVEL)).upper()
        _CONSOLE_ENABLED = bool(cfg.get("console_log_enabled", True))
        _CONSOLE_STAGE_BANNERS = bool(cfg.get("console_stage_banners", True))
        _CONSOLE_COLOR_ENABLED = bool(cfg.get("console_color_enabled", True))
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(_DIAG_TEXT_PATH) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(_DIAG_JSON_PATH) or ".", exist_ok=True)
        _DIAG_TEXT_FH = open(_DIAG_TEXT_PATH, "a", encoding="utf-8")
        _DIAG_JSON_FH = open(_DIAG_JSON_PATH, "a", encoding="utf-8")
    except Exception:
        _DIAG_TEXT_FH = None
        _DIAG_JSON_FH = None


def _close_diagnostics():
    global _DIAG_TEXT_FH, _DIAG_JSON_FH
    for fh in (_DIAG_TEXT_FH, _DIAG_JSON_FH):
        try:
            if fh:
                fh.flush()
                fh.close()
        except Exception:
            pass
    _DIAG_TEXT_FH = None
    _DIAG_JSON_FH = None


atexit.register(_close_diagnostics)


def log(level, message):
    _init_diagnostics()
    now = datetime.datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    iso_ts = now.isoformat()
    lvl = str(level).upper()
    msg = str(message)
    log_message = f"[{ts}] [{lvl}] {msg}"

    # Write to diagnostic files first. Keep the lock scoped only to file writes so
    # blocked console I/O cannot freeze all logging threads.
    acquired = _LOG_LOCK.acquire(timeout=0.2)
    if acquired:
        try:
            if _DIAG_TEXT_FH:
                _DIAG_TEXT_FH.write(log_message + "\n")
                _DIAG_TEXT_FH.flush()
            if _DIAG_JSON_FH:
                frame = sys._getframe(1)
                record = {
                    "ts": iso_ts,
                    "level": str(level),
                    "message": msg,
                    "pid": os.getpid(),
                    "thread_name": threading.current_thread().name,
                    "thread_id": threading.get_ident(),
                    "monotonic_s": time.monotonic(),
                    "source_file": os.path.basename(frame.f_code.co_filename),
                    "source_line": frame.f_lineno,
                }
                _DIAG_JSON_FH.write(json.dumps(record, ensure_ascii=True) + "\n")
                _DIAG_JSON_FH.flush()
        except Exception:
            pass
        finally:
            _LOG_LOCK.release()

    if not _CONSOLE_ENABLED:
        return
    min_level = _LEVEL_ORDER.get(_CONSOLE_MIN_LEVEL, _LEVEL_ORDER["INFO"])
    cur_level = _LEVEL_ORDER.get(lvl, _LEVEL_ORDER["INFO"])
    if cur_level < min_level:
        return

    try:
        print(log_message, flush=True)
    except UnicodeEncodeError:
        try:
            if sys.stdout.encoding != "utf-8":
                import codecs
                sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
            print(log_message, flush=True)
        except Exception:
            pass
    except Exception:
        pass


def update_runtime_state(**values):
    """Publish compact process state for external heartbeat diagnostics."""
    with _RUNTIME_STATE_LOCK:
        _RUNTIME_STATE.update(values)


def runtime_snapshot():
    with _RUNTIME_STATE_LOCK:
        return dict(_RUNTIME_STATE)


def gui_event(event_type: str, **payload):
    """Emit one machine-readable event intended exclusively for the GUI terminal."""
    state = runtime_snapshot()
    record = {
        "type": str(event_type),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run_id": state.get("run_id", ""),
        **payload,
    }
    try:
        print("GUI_EVENT:" + json.dumps(record, ensure_ascii=True), flush=True)
    except Exception:
        pass


def stage_banner(title, subtitle="", color="cyan"):
    """Print a high-visibility console/log section marker."""
    _init_diagnostics()
    if not _CONSOLE_STAGE_BANNERS:
        log("INFO", f"[STAGE] {title}" + (f" | {subtitle}" if subtitle else ""))
        return

    title_text = str(title).upper()
    subtitle_text = str(subtitle or "")
    width = max(88, len(title_text) + 12, len(subtitle_text) + 12)
    line = "=" * width
    body = f"===== {title_text} =====".center(width)
    sub = subtitle_text.center(width) if subtitle_text else ""

    ansi = {
        "cyan": "\033[96m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "magenta": "\033[95m",
        "blue": "\033[94m",
        "reset": "\033[0m",
    }
    prefix = ansi.get(str(color), ansi["cyan"]) if (_CONSOLE_COLOR_ENABLED and sys.stdout.isatty()) else ""
    suffix = ansi["reset"] if prefix else ""

    # Persist a plain marker in logs.
    log("INFO", f"[STAGE] {title_text}" + (f" | {subtitle_text}" if subtitle_text else ""))
    if not _CONSOLE_ENABLED:
        return
    try:
        print(prefix + line + suffix, flush=True)
        print(prefix + body + suffix, flush=True)
        if sub:
            print(prefix + sub + suffix, flush=True)
        print(prefix + line + suffix, flush=True)
    except Exception:
        pass

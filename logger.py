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
_LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


def _init_diagnostics():
    global _DIAG_INIT, _DIAG_TEXT_FH, _DIAG_JSON_FH, _DIAG_TEXT_PATH, _DIAG_JSON_PATH
    global _CONSOLE_MIN_LEVEL, _CONSOLE_ENABLED
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

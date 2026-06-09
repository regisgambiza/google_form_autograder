import datetime
import faulthandler
import json
import os
import sys
import threading
import time
from typing import Dict, Optional

from logger import log


def _parse_iso_ts(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _read_heartbeat(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _thread_snapshot() -> Dict[str, object]:
    threads = []
    for t in threading.enumerate():
        threads.append(
            {
                "name": t.name,
                "ident": t.ident,
                "daemon": bool(t.daemon),
                "alive": bool(t.is_alive()),
            }
        )
    return {
        "thread_count": len(threads),
        "threads": threads,
    }


def _write_stack_dump(path: str, reason: str, heartbeat: Dict[str, object]):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + "=" * 100 + "\n")
        f.write(
            f"[{datetime.datetime.now().isoformat()}] STACK DUMP reason={reason} "
            f"pid={os.getpid()} heartbeat_stage={heartbeat.get('stage', 'unknown')}\n"
        )
        f.write("=" * 100 + "\n")
        try:
            faulthandler.dump_traceback(file=f, all_threads=True)
        except Exception as ex:
            f.write(f"faulthandler error: {ex}\n")
        f.flush()


def start_hang_diagnostics(config: Dict[str, object]) -> threading.Event:
    enabled = bool(config.get("hang_diagnostics_enabled", True))
    stop_event = threading.Event()
    if not enabled:
        return stop_event

    interval_s = max(3, int(config.get("hang_diag_poll_interval_seconds", 5)))
    stale_s = max(10, int(config.get("hang_diag_stale_seconds", int(config.get("heartbeat_timeout", 30)))))
    dump_cooldown_s = max(10, int(config.get("hang_diag_dump_cooldown_seconds", 30)))
    heartbeat_path = str(config.get("hang_diag_heartbeat_path", "heartbeat.json"))
    events_path = str(config.get("hang_diag_events_path", "logs/hang_events.jsonl"))
    stackdump_path = str(config.get("hang_diag_stackdump_path", "logs/hang_stack_dumps.log"))

    os.makedirs(os.path.dirname(events_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(stackdump_path) or ".", exist_ok=True)

    def _append_event(event: Dict[str, object]):
        try:
            with open(events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=True) + "\n")
        except Exception:
            pass

    def _worker():
        last_dump_at = 0.0
        while not stop_event.is_set():
            now = datetime.datetime.now(datetime.timezone.utc)
            hb = _read_heartbeat(heartbeat_path)
            hb_dt = _parse_iso_ts(str(hb.get("last_update")) if hb else None)
            hb_age = None
            stale = False
            if hb_dt is not None:
                hb_age = (now - hb_dt.astimezone(datetime.timezone.utc)).total_seconds()
                stale = hb_age > stale_s
            snap = _thread_snapshot()
            event = {
                "ts": now.isoformat(),
                "event": "poll",
                "pid": os.getpid(),
                "heartbeat_stage": hb.get("stage", "") if hb else "",
                "heartbeat_age_s": hb_age,
                "stale": stale,
                "thread_count": snap["thread_count"],
                "thread_names": [t["name"] for t in snap["threads"]],
            }
            _append_event(event)
            if stale and (time.time() - last_dump_at) >= dump_cooldown_s:
                log("ERROR", f"[HANG_DIAG] heartbeat stale for {hb_age:.1f}s at stage={hb.get('stage', 'unknown')}")
                _write_stack_dump(stackdump_path, "heartbeat_stale", hb)
                _append_event(
                    {
                        "ts": now.isoformat(),
                        "event": "stack_dump_written",
                        "pid": os.getpid(),
                        "reason": "heartbeat_stale",
                        "heartbeat_stage": hb.get("stage", "") if hb else "",
                        "heartbeat_age_s": hb_age,
                        "stackdump_path": stackdump_path,
                    }
                )
                last_dump_at = time.time()
            stop_event.wait(interval_s)

    t = threading.Thread(target=_worker, name="hang-diagnostics", daemon=True)
    t.start()
    log("INFO", f"[HANG_DIAG] started (interval={interval_s}s stale={stale_s}s)")
    return stop_event


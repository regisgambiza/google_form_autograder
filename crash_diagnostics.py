# crash_diagnostics.py - Native crash capture + diagnostics for this project.
#
# WHY THIS EXISTS
# ---------------
# The application intermittently dies several minutes into grading with no
# Python traceback (Windows Event Log shows python.exe terminating with
# fail-fast/abort style exception codes such as 0xC0000409 and occasionally
# 0xC0000005). Those deaths bypass sys.excepthook entirely because they happen
# in native code (Qt/C runtime/extensions), so nothing was ever recorded.
#
# WHAT IS CAPTURED
# ----------------
# 1. Python uncaught exceptions        -> sys.excepthook + threading.excepthook
# 2. Native faults (access violation, illegal instruction, in-page errors...)
#    -> Windows SetUnhandledExceptionFilter + dbghelp!MiniDumpWriteDump
#       (a real .dmp that WinDbg / Visual Studio can open, plus the Windows
#       Error Reporting pipeline continues untouched)
# 3. abort()/fail-fast family          -> faulthandler.enable(all_threads=True)
#       UCRT abort() raises SIGABRT before failing fast, so faulthandler's
#       C-level handler can still dump every thread's Python stack at speed,
#       without needing the GIL (safe even while other threads are wedged).
# 4. ANY other sudden death (fail-fast exceptions bypass ALL user-mode
#    exception filters, TerminateProcess, heap corruption kills...)
#    -> an out-of-process watchdog child that notices the parent died and
#       writes the timestamped text report from the artifacts left behind.
#
# KNOWN LIMITATION (documented, by design of the OS):
# Fail-fast exceptions (0xC0000409 with fail-fast code, e.g. raised by
# __fastfail / qFatal paths that use them) terminate the process without ever
# invoking SetUnhandledExceptionFilter or vectored handlers. They cannot be
# intercepted in-process by any user-mode filter. Coverage for them comes from
# (a) faulthandler's SIGABRT dump when abort() is the origin, (b) the
# watchdog's post-mortem report, and (c) the native .dmp when WER LocalDumps
# is configured system-wide. An external debugger (WinDbg/VS) can always be
# attached normally; nothing here blocks that.
#
# DEBUG MODE (no source changes needed)
# -------------------------------------
#   set AUTOGRADER_DIAGNOSTICS=1          -> force on
#   set AUTOGRADER_DIAGNOSTICS=verbose    -> on + resource/thread sampling
#   set AUTOGRADER_DIAGNOSTICS=0          -> off (only excepthook stays)
#   python gui_main.py --diagnostics      -> same as =1
#   python gui_main.py --diagnostics=verbose
# config.json keys: "crash_diagnostics_enabled": true/false,
#                   "crash_diagnostics_verbose": true/false
#
# OUTPUT LOCATION
# ---------------
#   logs/crash/crash_YYYY-MM-DD_HH-MM-SS.log   <- full text reports
#   logs/crash/recent_events.log               <- rolling diagnostic ring
#   logs/crash/faulthandler_<context>.log      <- fatal-signal Python stacks
#   logs/crash/native_pidNNN.dmp               <- minidump for WinDbg/VS
#   logs/crash/state_<context>.json            <- last grading state snapshot
#
# This module deliberately imports NOTHING from the rest of the project, so it
# can never participate in an import cycle and stays usable from any entry
# point, including the headless grader and the frozen build.
from __future__ import annotations

import io
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime

IS_WINDOWS = (os.name == "nt")

_CRASH_DIR_ENV = os.environ.get("AUTOGRADER_CRASH_DIR", "").strip() or None

# ---------------------------------------------------------------------------
# Module state (kept conservative; everything guarded so this module can never
# take the application down).
# ---------------------------------------------------------------------------
_INSTALLED = False
_INSTALL_LOCK = threading.Lock()
_VERBOSE = False
_CONTEXT = "app"
_APP_NAME = "app"
_CRASH_DIR = os.path.join("logs", "crash")
_RECENT_PATH = ""
_FH_LOG_PATH = ""
_STATE_PATH = ""

_RING_LOCK = threading.Lock()
_RING: deque = deque(maxlen=1200)
_RING_TOTAL = 0      # entries ever appended (monotonic)
_RING_FLUSHED = 0    # entries already written to the on-disk ring file

_STATE_LOCK = threading.Lock()
_GRADING_STATE: dict = {}

_FILTER_REF = None          # keep the SEH callback alive (GC would crash us)
_FH_FILE = None             # keep faulthandler's file object alive
_WATCHDOG_PROC = None
_SAMPLER_STOP = threading.Event()
_IN_REPORT = False          # recursion guard for report writers

_MIN_WATCHDOG_UPTIME_S = float(os.environ.get("AUTOGRADER_WATCHDOG_MIN_UPTIME_S", "10") or 10)

_SECRET_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"sk-or-v1-[0-9a-zA-Z]{16,}",
    r"sk-[0-9a-zA-Z_-]{16,}",
    r"Bearer\s+[A-Za-z0-9._\-]+",
    r"ya29\.[A-Za-z0-9._\-]+",
    r"1//[A-Za-z0-9._\-]{16,}",
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization)"
    r"(\s*[=:]\s*|\s*=\s*)\S+",
))


def _redact(text: str) -> str:
    """Strip credentials/tokens from any text destined for crash reports."""
    out = str(text or "")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: m.group(0)[:8] + "<redacted>", out)
    return out


# ---------------------------------------------------------------------------
# Public recording API (used by logger.py and the grading pipeline)
# ---------------------------------------------------------------------------
def record(event: str, **fields) -> None:
    """Append one diagnostic event line to the rolling ring buffer."""
    global _RING_TOTAL
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        parts = []
        for key, value in fields.items():
            text = str(value)
            if len(text) > 220:
                text = text[:217] + "..."
            parts.append(f"{key}={text}")
        suffix = (" | " + ", ".join(parts)) if parts else ""
        line = f"{ts} - {event}{suffix}"
        with _RING_LOCK:
            _RING.append(line)
            _RING_TOTAL += 1
        if _VERBOSE:
            _maybe_persist_state()
    except Exception:
        pass


def set_grading_state(**fields) -> None:
    """Publish what the grader/GUI is currently doing (form/question/answer...).

    Values are merged into the live state dict and mirrored to a small JSON
    file so the out-of-process watchdog can read it after a sudden death.
    """
    global _STATE_PATH
    try:
        with _STATE_LOCK:
            _GRADING_STATE.update(fields)
            _GRADING_STATE["updated_at"] = datetime.now().isoformat(timespec="seconds")
        record("state:" + ",".join(f"{k}={_short(str(v))}" for k, v in sorted(fields.items())))
        _maybe_persist_state(force=True)
    except Exception:
        pass


def get_grading_state() -> dict:
    with _STATE_LOCK:
        return dict(_GRADING_STATE)


def _short(value: str, limit: int = 60) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _maybe_persist_state(force: bool = False) -> None:
    """Throttled write of the grading state snapshot (>=0.5s apart unless forced)."""
    now = time.monotonic()
    last = getattr(_maybe_persist_state, "_last", 0.0)
    if not force and (now - last) < 0.5:
        return
    _maybe_persist_state._last = now
    try:
        with _STATE_LOCK:
            payload = dict(_GRADING_STATE)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=True, indent=1)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rolling ring persistence (small background writer; cheap when idle)
# ---------------------------------------------------------------------------
def _ring_flush_loop() -> None:
    global _RING_FLUSHED
    while not _SAMPLER_STOP.wait(1.0):
        try:
            with _RING_LOCK:
                items = list(_RING)
                total = _RING_TOTAL
                oldest_available = total - len(items)
                from_idx = max(_RING_FLUSHED, oldest_available)
                pending = items[from_idx - oldest_available:]
                _RING_FLUSHED = total
            if not pending:
                continue
            try:
                if os.path.exists(_RECENT_PATH) and os.path.getsize(_RECENT_PATH) > 1_000_000:
                    rotated = _RECENT_PATH + ".1"
                    if os.path.exists(rotated):
                        os.remove(rotated)
                    os.replace(_RECENT_PATH, rotated)
                with open(_RECENT_PATH, "a", encoding="utf-8", errors="replace") as fh:
                    fh.write("\n".join(pending) + "\n")
            except OSError:
                pass
        except Exception:
            pass


def _tail_file(path: str, max_bytes: int = 24_000) -> str:
    try:
        if not os.path.exists(path):
            return "(not available)"
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read(max_bytes)
        text = data.decode("utf-8", errors="replace")
        if size > max_bytes:
            nl = text.find("\n")
            if nl >= 0:
                text = text[nl + 1:]
        return text.rstrip() or "(empty)"
    except Exception as exc:
        return f"(unreadable: {exc})"


# ---------------------------------------------------------------------------
# Resource sampling (verbose/diagnostic mode only)
# ---------------------------------------------------------------------------
def _process_snapshot() -> dict:
    snap = {"rss_mb": None, "cpu_pct": None, "threads": threading.active_count(), "handles": None}
    try:
        if IS_WINDOWS:
            import ctypes.wintypes as wt

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class VM_COUNTERS(ctypes.Structure):
                _fields_ = [("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            k32 = ctypes.windll.kernel32
            handle = k32.GetCurrentProcess()
            counters = IO_COUNTERS()
            vm = VM_COUNTERS()
            # GetProcessMemoryInfo(psapi)
            psapi = ctypes.windll.psapi
            pmc_buf = (ctypes.c_byte * 72)()
            if psapi.GetProcessMemoryInfo(handle, pmc_buf, ctypes.sizeof(pmc_buf)):
                working = int.from_bytes(bytes(pmc_buf[8:16]), "little")
                snap["rss_mb"] = round(working / (1024 * 1024), 1)
            class _TIMES(ctypes.Structure):
                _fields_ = [(n, wt.FILETIME) for n in ("creation", "exit", "kernel", "user")]
            t = _TIMES()
            if k32.GetProcessTimes(handle, ctypes.byref(t.creation), ctypes.byref(t.exit),
                                   ctypes.byref(t.kernel), ctypes.byref(t.user)):
                total_s = (t.kernel.dwHighDateTime * 4294967296 + t.kernel.dwLowDateTime +
                           t.user.dwHighDateTime * 4294967296 + t.user.dwLowDateTime) / 1e7
                now = time.monotonic()
                prev_t = getattr(_process_snapshot, "_t", None)
                prev_m = getattr(_process_snapshot, "_m", None)
                if prev_t and prev_m and now > prev_m:
                    snap["cpu_pct"] = round((total_s - prev_t) / (now - prev_m) * 100.0, 1)
                _process_snapshot._t = total_s
                _process_snapshot._m = now
    except Exception:
        pass
    return snap


def _thread_names_snapshot() -> list:
    names = []
    try:
        for th in threading.enumerate():
            names.append(th.name)
    except Exception:
        pass
    return names


def _sampler_loop() -> None:
    counter = 0
    while not _SAMPLER_STOP.wait(5.0):
        counter += 1
        try:
            snap = _process_snapshot()
            record(
                "resources",
                rss_mb=snap.get("rss_mb"),
                cpu_pct=snap.get("cpu_pct"),
                threads=snap.get("threads"),
            )
            if counter % 4 == 0:
                record("threads", names=", ".join(_thread_names_snapshot())[:400])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Report composition
# ---------------------------------------------------------------------------
def _dependency_versions() -> dict:
    versions = {}
    for modname in ("PySide6", "shiboken6", "requests", "urllib3", "googleapiclient",
                    "google_auth_oauthlib", "numpy", "psutil"):
        try:
            mod = __import__(modname)
            versions[modname] = getattr(mod, "__version__", "?")
        except Exception:
            continue
    if "PySide6" in versions:
        try:
            from PySide6 import QtCore

            versions["Qt_runtime"] = QtCore.qVersion()
        except Exception:
            pass
    return versions


def _build_id() -> str:
    try:
        import subprocess as sp

        out = sp.run(["git", "describe", "--always", "--dirty"], capture_output=True,
                     text=True, timeout=4)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    try:
        stamp = os.path.getmtime(__file__)
        return "no-git (module mtime %s)" % datetime.fromtimestamp(stamp).isoformat(timespec="seconds")
    except Exception:
        return "unknown"


def _system_info_lines() -> list:
    lines = [
        f"os              : {platform.system()} {platform.release()} build {platform.version()}",
        f"machine         : {platform.machine()}  python={sys.version.split()[0]} "
        f"({platform.architecture()[0]})",
        f"executable      : {sys.executable}",
        f"frozen          : {getattr(sys, 'frozen', False)}",
    ]
    for name, ver in sorted(_dependency_versions().items()):
        lines.append(f"dep             : {name}=={ver}")
    return lines


def _resource_lines() -> list:
    snap = _process_snapshot()
    lines = [
        f"rss_mb          : {snap.get('rss_mb')}",
        f"cpu_pct         : {snap.get('cpu_pct')}",
        f"python_threads  : {snap.get('threads')}",
        f"thread_names    : {', '.join(_thread_names_snapshot())[:500]}",
    ]
    try:
        usage_dir = _CRASH_DIR
        free = os.statvfs(usage_dir).f_bavail * os.statvfs(usage_dir).f_frsize
    except AttributeError:
        try:
            import shutil

            free = shutil.disk_usage(_CRASH_DIR or ".").free
        except Exception:
            free = None
    except Exception:
        free = None
    if free is not None:
        lines.append(f"disk_free_mb    : {round(free / (1024 * 1024))}")
    return lines


def _crash_dir_files_fresh(seconds: float) -> list:
    fresh = []
    cutoff = time.time() - seconds
    try:
        for name in os.listdir(_CRASH_DIR):
            path = os.path.join(_CRASH_DIR, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) >= cutoff:
                    fresh.append((os.path.getmtime(path), name, os.path.getsize(path)))
            except OSError:
                continue
    except OSError:
        pass
    fresh.sort(reverse=True)
    return fresh


def _write_report(kind: str, sections: dict) -> str:
    """Compose and persist one timestamped crash report. Never raises."""
    global _IN_REPORT
    if _IN_REPORT:
        return ""
    _IN_REPORT = True
    path = ""
    try:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(_CRASH_DIR, f"crash_{stamp}.log")
        ordered = ["APPLICATION INFORMATION", "SYSTEM INFORMATION", "CRASH INFORMATION",
                   "NATIVE STACK TRACE", "PYTHON STACK TRACE", "THREAD INFORMATION",
                   "GRADING STATE", "RECENT LOG EVENTS", "RESOURCE INFORMATION"]
        chunks = ["=" * 78]
        for title in ordered:
            body = sections.get(title)
            if body is None:
                continue
            chunks.append(title)
            chunks.append("-" * len(title))
            chunks.append(str(body).rstrip())
            chunks.append("")
        chunks.append("=" * 78)
        text = _redact("\n".join(chunks))
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            fallback = os.path.join(os.path.expanduser("~"), f"autograder_crash_{stamp}.log")
            with open(fallback, "w", encoding="utf-8") as fh:
                fh.write(text)
            path = fallback
    except Exception:
        path = ""
    finally:
        _IN_REPORT = False
    return path


def _ring_dump(limit: int = 400) -> str:
    """Current in-memory ring contents (fresher than the flushed file)."""
    try:
        with _RING_LOCK:
            lines = list(_RING)[-limit:]
        return "\n".join(lines) or "(empty)"
    except Exception:
        return "(unavailable)"


def _python_exception_report(kind: str, exc_type, exc_value, exc_tb, thread_name: str = "") -> str:
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb)) \
        if exc_tb else f"{exc_type.__name__}: {exc_value}"
    threads = []
    for th in threading.enumerate():
        extra = ""
        frame = getattr(th, "_target", None)
        extra = f" target={getattr(frame, '__name__', '?')}" if frame else ""
        threads.append(f"id={th.ident:>10} daemon={th.daemon} name={th.name}{extra}")
    sections = {
        "APPLICATION INFORMATION":
            f"name       : {_APP_NAME}\n"
            f"context    : {_CONTEXT}\n"
            f"build      : {_build_id()}\n"
            f"pid        : {os.getpid()}\n"
            f"timestamp  : {datetime.now().isoformat()}",
        "SYSTEM INFORMATION": "\n".join(_system_info_lines()),
        "CRASH INFORMATION":
            f"kind           : {kind}\n"
            f"exception      : {getattr(exc_type, '__name__', exc_type)}: {exc_value}\n"
            f"thread         : {thread_name or threading.current_thread().name}\n"
            f"note           : captured by Python excepthook (not a native fault)",
        "NATIVE STACK TRACE":
            "(not applicable - failure surfaced as a Python exception; no native\n"
            " fault occurred. Native faults produce a native_pid*.dmp plus this\n"
            " same report layout.)",
        "PYTHON STACK TRACE": tb_text,
        "THREAD INFORMATION": "\n".join(threads) or "(none)",
        "GRADING STATE": json.dumps(get_grading_state(), indent=1, ensure_ascii=True),
        "RECENT LOG EVENTS": _ring_dump() + "\n\n--- flushed ring file tail ---\n" + _tail_file(_RECENT_PATH),
        "RESOURCE INFORMATION": "\n".join(_resource_lines()),
    }
    path = _write_report(kind, sections)
    if path:
        record("crash_report_written", path=path, kind=kind)
    return path


# ---------------------------------------------------------------------------
# Windows native machinery
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes as _wt

    _EXCEPTION_CONTINUE_SEARCH = 0
    _STATUS_ACCESS_VIOLATION = 0xC0000005
    _FAIL_FAST_CODES = frozenset(range(0x00000000, 0x00000020))  # informational only

    class _EXCEPTION_RECORD64(ctypes.Structure):
        _fields_ = [("ExceptionCode", ctypes.c_uint32),
                    ("ExceptionFlags", ctypes.c_uint32),
                    ("ExceptionRecord", ctypes.c_uint64),
                    ("ExceptionAddress", ctypes.c_uint64),
                    ("NumberParameters", ctypes.c_uint32),
                    ("__unusedAlignment", ctypes.c_uint32),
                    ("ExceptionInformation", ctypes.c_uint64 * 15)]

    class _EXCEPTION_POINTERS(ctypes.Structure):
        _fields_ = [("ExceptionRecord", ctypes.POINTER(_EXCEPTION_RECORD64)),
                    ("ContextRecord", ctypes.c_void_p)]

    _LPTOP_LEVEL_EXCEPTION_FILTER = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.POINTER(_EXCEPTION_POINTERS))

    class _MINIDUMP_EXCEPTION_INFORMATION(ctypes.Structure):
        _fields_ = [("ThreadId", ctypes.c_uint32),
                    ("ExceptionPointers", ctypes.c_void_p),
                    ("ClientPointers", ctypes.c_int)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _dbhlp = ctypes.WinDLL("dbghelp", use_last_error=True)

    _MiniDumpWriteDump = _dbhlp.MiniDumpWriteDump
    _MiniDumpWriteDump.argtypes = [
        _wt.HANDLE, _wt.DWORD, _wt.HANDLE, _wt.DWORD,
        ctypes.POINTER(_MINIDUMP_EXCEPTION_INFORMATION), ctypes.c_void_p, ctypes.c_void_p]
    _MiniDumpWriteDump.restype = ctypes.c_int

    # MiniDumpNormal | WithHandleData | WithIndirectlyReferencedMemory |
    # WithUnloadedModules | WithProcessThreadData
    _DUMP_TYPE = 0x00000001 | 0x00000004 | 0x00000040 | 0x00000020 | 0x00001000

    def _describe_exception_code(code: int) -> str:
        known = {
            0xC0000005: "ACCESS_VIOLATION",
            0xC00000FD: "STACK_OVERFLOW",
            0xC0000409: "STACK_BUFFER_OVERRUN / fail-fast (see fail-fast code below)",
            0xC0000374: "HEAP_CORRUPTION",
            0xC0000417: "INVALID_CRT_PARAMETER",
            0x80000003: "BREAKPOINT",
            0xE0434352: "CLR_EXCEPTION",
            0xE06D7363: "CPP_EXCEPTION (msvc)",
            0x40000015: "STATUS_FATAL_APP_EXIT (abort)",
        }
        desc = known.get(code & 0xFFFFFFFF, "")
        extra = ""
        if (code & 0xFFFFFFFF) == 0xC0000409:
            ff = code >> 0  # fail-fast code arrives via ExceptionInformation/exit code
            extra = f" (FAST_FAIL code family; observed value {ff})"
        return f"{code:#010x} {desc}{extra}"

    def _seh_filter(exception_pointers):
        """Runs INSIDE the crashing thread right before the process unwinds.

        Only direct C calls happen here (no Python allocation beyond the two
        small ints/bytes already materialised) because the GIL may be held by
        the faulting code path. Writes the minidump, then lets the OS continue
        (WER still fires; the watchdog turns the artifacts into the text
        report afterwards).
        """
        try:
            rec = exception_pointers.contents.ExceptionRecord.contents
            code = rec.ExceptionCode & 0xFFFFFFFF
        except Exception:
            rec = None
            code = 0
        try:
            dump_path = os.path.abspath(os.path.join(_CRASH_DIR, f"native_pid{os.getpid()}.dmp"))
            wide = ctypes.create_unicode_buffer(dump_path)
            GENERIC_WRITE = 0x40000000
            FILE_SHARE_READ = 0x1
            CREATE_ALWAYS = 2
            FILE_ATTRIBUTE_NORMAL = 0x80
            handle = _k32.CreateFileW(wide, GENERIC_WRITE, FILE_SHARE_READ, None,
                                      CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
            if handle != -1 and handle != 0xFFFFFFFFFFFFFFFF:  # INVALID_HANDLE_VALUE
                try:
                    ei = _MINIDUMP_EXCEPTION_INFORMATION(
                        ThreadId=_k32.GetCurrentThreadId(),
                        ExceptionPointers=ctypes.cast(exception_pointers, ctypes.c_void_p),
                        ClientPointers=False)
                    _MiniDumpWriteDump(_k32.GetCurrentProcess(), _k32.GetCurrentProcessId(),
                                       handle, _DUMP_TYPE, ctypes.byref(ei), None, None)
                finally:
                    _k32.CloseHandle(handle)
            # Best-effort breadcrumb readable even if the report step fails.
            try:
                marker = (f"native_fault code={_describe_exception_code(code)} "
                          f"addr={rec.ExceptionAddress if rec else '?'} "
                          f"time={datetime.now().isoformat()} ctx={_CONTEXT}\n").encode()
                mpath = os.path.abspath(os.path.join(_CRASH_DIR, f"native_pid{os.getpid()}.txt"))
                mw = ctypes.create_unicode_buffer(mpath)
                mh = _k32.CreateFileW(mw, GENERIC_WRITE, FILE_SHARE_READ, None,
                                      CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
                if mh != -1 and mh != 0xFFFFFFFFFFFFFFFF:
                    try:
                        written = ctypes.c_uint32(0)
                        _k32.WriteFile(mh, marker, len(marker), ctypes.byref(written), None)
                    finally:
                        _k32.CloseHandle(mh)
            except Exception:
                pass
        except Exception:
            pass
        return _EXCEPTION_CONTINUE_SEARCH

    def _install_native_filter() -> bool:
        global _FILTER_REF
        try:
            _FILTER_REF = _LPTOP_LEVEL_EXCEPTION_FILTER(_seh_filter)
            _k32.SetUnhandledExceptionFilter(_FILTER_REF)
            return True
        except Exception:
            _FILTER_REF = None
            return False
else:

    def _install_native_filter() -> bool:  # pragma: no cover (non-Windows)
        return False

    def _describe_exception_code(code: int) -> str:  # pragma: no cover
        return hex(code)


# ---------------------------------------------------------------------------
# Out-of-process watchdog
# ---------------------------------------------------------------------------
def _spawn_watchdog(parent_pid: int, parent_started_epoch: float) -> None:
    global _WATCHDOG_PROC
    try:
        flags = 0
        if IS_WINDOWS:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        _WATCHDOG_PROC = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__),
             "--watch-parent", str(parent_pid),
             "--context", _CONTEXT,
             "--parent-started-epoch", f"{parent_started_epoch:.3f}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=os.getcwd(), creationflags=flags if IS_WINDOWS else 0,
            close_fds=not IS_WINDOWS,
        )
        record("watchdog_spawned", pid=_WATCHDOG_PROC.pid)
    except Exception:
        _WATCHDOG_PROC = None


_EXIT_CODE_NOTES = {
    0xC0000005: "Access violation (native memory fault)",
    0xC00000FD: "Stack overflow",
    0xC0000409: "Fail-fast / STATUS_STACK_BUFFER_OVERRUN - typically UCRT abort() "
                "(FAST_FAIL_FATAL_APP_EXIT when data=0x7), qFatal, or /GS cookie failure",
    0xC0000374: "Heap corruption detected by ntdll",
    0x40000015: "STATUS_FATAL_APP_EXIT - abort() path",
    0x80000003: "Breakpoint instruction",
}


_EXIT_CODE_NOTES = {
    0xC0000005: "Access violation (native memory fault)",
    0xC00000FD: "Stack overflow",
    0xC0000409: "Fail-fast / STATUS_STACK_BUFFER_OVERRUN - typically UCRT abort() "
                "(FAST_FAIL_FATAL_APP_EXIT when data=0x7), qFatal, or /GS cookie failure",
    0xC0000374: "Heap corruption detected by ntdll",
    0x40000015: "STATUS_FATAL_APP_EXIT - abort() path",
    0x80000003: "Breakpoint instruction",
    3: "CRT-style abnormal termination (abort()/fatal-signal default action; also "
       "the exit code CPython's faulthandler leaves after dumping a fatal fault)",
}


def _cmd_watch_parent(parent_pid: int, context: str, parent_started_epoch: float = 0.0) -> int:
    """Runs as a separate lightweight process; watches the parent and, if it
    dies unexpectedly, writes the consolidated text crash report."""
    global _CONTEXT, _CRASH_DIR, _RECENT_PATH, _STATE_PATH, _FH_LOG_PATH
    _CONTEXT = context
    if _CRASH_DIR_ENV:
        _CRASH_DIR = _CRASH_DIR_ENV
    _RECENT_PATH = os.path.join(_CRASH_DIR, "recent_events.log")
    _FH_LOG_PATH = os.path.join(_CRASH_DIR, f"faulthandler_{context}.log")
    _STATE_PATH = os.path.join(_CRASH_DIR, f"state_{context}.json")
    parent_started_epoch = float(parent_started_epoch or 0.0) or time.time()
    if IS_WINDOWS:
        import ctypes
        import ctypes.wintypes as wt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        SYNCHRONIZE = 0x00100000
        QUERY_LIMITED = 0x1000
        STILL_ACTIVE = 259
        handle = k32.OpenProcess(SYNCHRONIZE | QUERY_LIMITED, False, parent_pid)
        if not handle:
            # Parent vanished before we attached; nothing useful to say.
            return 0
        try:
            code = ctypes.c_uint32(STILL_ACTIVE)
            while True:
                wait = k32.WaitForSingleObject(handle, 1500)
                if wait == 0:  # WAIT_OBJECT_0
                    k32.GetExitCodeProcess(handle, ctypes.byref(code))
                    exit_code = code.value & 0xFFFFFFFF
                    break
                if wait != 0x102:  # WAIT_TIMEOUT expected; anything else bail
                    return 0
        finally:
            k32.CloseHandle(handle)
    else:  # pragma: no cover
        import signal as _signal
        import errno

        while True:
            try:
                os.kill(parent_pid, 0)
                time.sleep(1.0)
            except OSError as exc:
                if exc.errno in (errno.ESRCH, errno.EPERM):
                    break
                return 0
        exit_code = 0xFFFFFFFF  # unknown on POSIX polling path

    uptime = time.time() - parent_started_epoch
    signed = exit_code - (1 << 32) if exit_code >= (1 << 31) and exit_code <= 0xFFFFFFFF else exit_code
    clean = (exit_code == 0) or (signed == 0)
    if clean or uptime < _MIN_WATCHDOG_UPTIME_S:
        # Normal shutdown (or the parent never really got going) -> stay silent.
        return 0

    note = _EXIT_CODE_NOTES.get(exit_code) or _EXIT_CODE_NOTES.get(signed, "")
    if not note:
        note = ("abnormal exit code - see Windows Event Log, Application channel, "
                "source 'Application Error'")
    fresh = _crash_dir_files_fresh(300)
    fresh_lines = "\n".join(
        f"{datetime.fromtimestamp(mt).isoformat(timespec='seconds')}  {name} ({size:,} bytes)"
        for mt, name, size in fresh[:12]) or "(none)"
    sections = {
        "APPLICATION INFORMATION":
            f"name       : {_APP_NAME}\n"
            f"context    : {_CONTEXT} (report composed by out-of-process watchdog)\n"
            f"pid        : {parent_pid}\n"
            f"timestamp  : {datetime.now().isoformat()}",
        "SYSTEM INFORMATION": "\n".join(_system_info_lines()),
        "CRASH INFORMATION":
            f"kind           : process_terminated_unexpectedly\n"
            f"exit_code      : {signed} (unsigned {exit_code:#010x})\n"
            f"interpretation : {note}\n"
            f"parent_lifetime_s    : {uptime:.0f}\n"
            "note           : Python produced no traceback; this death happened in "
            "native code or via fail-fast. Cross-check the faulthandler log below "
            "and the minidump listing.",
        "NATIVE STACK TRACE":
            "Native stacks are preserved in the minidump(s) listed below - open with:\n"
            "    windbg -y <project> -z <dump>   then run:  !analyze -v; ~*k\n"
            "or drag the .dmp onto Visual Studio.\n"
            "If no .dmp is listed, enable system-wide WER LocalDumps ONCE (admin)\n"
            "so every future crash of python.exe leaves a full dump:\n"
            "    reg add \"HKLM\\SOFTWARE\\Microsoft\\Windows\\Windows Error Reporting\\\n"
            "LocalDumps\\python.exe\" /v DumpFolder /t REG_EXPAND_SZ "
            "/d C:\\CrashDumps /f\n"
            "    reg add ... /v DumpType /t REG_DWORD /d 2 /f\n\n"
            f"fresh artifacts:\n{fresh_lines}",
        "PYTHON STACK TRACE":
            "(none captured - see faulthandler log tail below; if the origin was "
            "UCRT abort(), faulthandler printed every thread's Python stack there.)\n\n"
            "=== faulthandler log tail ===\n" + _tail_file(_FH_LOG_PATH),
        "THREAD INFORMATION":
            "Last known thread list (from rolling diagnostics):\n"
            + _tail_file(_RECENT_PATH, 6000),
        "GRADING STATE": _tail_file(_STATE_PATH, 4000) if os.path.exists(_STATE_PATH) else "{}",
        "RECENT LOG EVENTS": _tail_file(_RECENT_PATH),
        "RESOURCE INFORMATION":
            "Sampled values appear interleaved in RECENT LOG EVENTS ('resources' lines).\n"
            "Last sampled state:\n"
            + "\n".join(
                line for line in reversed(_tail_file(_RECENT_PATH, 20000).splitlines())
                if "resources" in line)[:1500],
    }
    _write_report(f"unexpected_termination_ctx-{context}", sections)
    return 0


# ---------------------------------------------------------------------------
# Controlled test-crash helpers (explicit opt-in ONLY)
# ---------------------------------------------------------------------------
def _self_test() -> int:
    status = {
        "installed": _INSTALLED,
        "context": _CONTEXT,
        "crash_dir": os.path.abspath(_CRASH_DIR),
        "native_seh_filter": _FILTER_REF is not None,
        "faulthandler": "enabled" if getattr(sys, "_autograder_fh_on", False) else "off",
        "watchdog_child": (_WATCHDOG_PROC.pid if _WATCHDOG_PROC else None),
        "ring_entries": len(_RING),
    }
    print(json.dumps(status, indent=1))
    return 0


def _main(argv: list) -> int:
    if "--watch-parent" in argv:
        idx = argv.index("--watch-parent")
        if idx + 1 >= len(argv) or not argv[idx + 1].isdigit():
            print("invalid --watch-parent pid", file=sys.stderr)
            return 2
        pid = int(argv[idx + 1])
        ctx = "app"
        started_epoch = 0.0
        if "--context" in argv:
            ctx = argv[argv.index("--context") + 1]
        if "--parent-started-epoch" in argv:
            started_epoch = float(argv[argv.index("--parent-started-epoch") + 1])
        return _cmd_watch_parent(pid, ctx, started_epoch)
    if "--test-crash" in argv:
        idx = argv.index("--test-crash")
        kind = argv[idx + 1] if idx + 1 < len(argv) else "python_exc"
        install(app_name=_APP_NAME, context=os.environ.get("AUTOGRADER_CONTEXT", "selftest"))
        record("deliberate_test_crash_requested", kind=kind)
        if kind == "python_exc":
            raise RuntimeError("deliberate test exception for diagnostics validation")
        if kind == "av":
            import ctypes

            # Wrapped by ctypes' own SEH guards -> surfaces as OSError through
            # the excepthook path (validates the Python-exception capture).
            ctypes.string_at(0)
            return 0
        if kind == "native_av":
            import faulthandler

            # Real, unwrapped null-pointer fault inside C -> hardware
            # EXCEPTION_ACCESS_VIOLATION -> our SetUnhandledExceptionFilter ->
            # minidump -> process dies 0xC0000005 -> watchdog report.
            faulthandler._sigsegv()
            return 0
        if kind == "abort":
            os.abort()  # UCRT abort(): SIGABRT -> faulthandler dump -> fail-fast
            return 0
        print(f"unknown test-crash kind: {kind}", file=sys.stderr)
        return 2
    if "--selftest" in argv:
        return _self_test()
    print("usage: crash_diagnostics.py --watch-parent PID --context CTX | "
          "--test-crash python_exc|av|native_av|abort | --selftest")
    return 2


# ---------------------------------------------------------------------------
# install()
# ---------------------------------------------------------------------------
def install(app_name: str = "GoogleFormAutograder", context: str = "app",
            verbose: bool | None = None) -> None:
    """Initialise all diagnostics. Safe to call more than once; never raises;
    designed to cost ~nothing when idle (one 1 Hz flusher wake-up + one
    watchdog process doing a blocking 1.5 s wait in the kernel)."""
    global _INSTALLED, _VERBOSE, _CONTEXT, _APP_NAME, _CRASH_DIR, _RECENT_PATH
    global _FH_LOG_PATH, _STATE_PATH, _FH_FILE
    if _INSTALLED:
        return
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        _APP_NAME = app_name
        _CONTEXT = context

        flag = os.environ.get("AUTOGRADER_DIAGNOSTICS", "").strip().lower()
        cfg_enabled = None
        cfg_verbose = False
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as fh:
                    cfg = json.load(fh)
                if isinstance(cfg.get("crash_diagnostics_enabled"), bool):
                    cfg_enabled = cfg["crash_diagnostics_enabled"]
                cfg_verbose = bool(cfg.get("crash_diagnostics_verbose", False))
        except Exception:
            pass

        if flag in ("0", "false", "off"):
            enabled = False
        elif flag in ("1", "true", "on", "verbose"):
            enabled = True
        else:
            enabled = True if cfg_enabled is None else cfg_enabled
        _VERBOSE = verbose if verbose is not None else (flag == "verbose" or cfg_verbose)

        if _CRASH_DIR_ENV:
            _CRASH_DIR = _CRASH_DIR_ENV
        try:
            os.makedirs(_CRASH_DIR, exist_ok=True)
        except OSError:
            _CRASH_DIR = os.path.join(os.path.expanduser("~"), ".autograder_crash")
            try:
                os.makedirs(_CRASH_DIR, exist_ok=True)
            except OSError:
                return  # nowhere to write; degrade to excepthook-only below

        _RECENT_PATH = os.path.join(_CRASH_DIR, "recent_events.log")
        _FH_LOG_PATH = os.path.join(_CRASH_DIR, f"faulthandler_{context}.log")
        _STATE_PATH = os.path.join(_CRASH_DIR, f"state_{context}.json")

        # 1) faulthandler FIRST: C-speed stacks for SIGSEGV/SIGABRT/etc.
        try:
            import faulthandler

            _FH_FILE = open(_FH_LOG_PATH, "a", encoding="utf-8", buffering=1)
            faulthandler.enable(file=_FH_FILE, all_threads=True)
            sys._autograder_fh_on = True  # noqa: SLF001 (status flag for self-test)
        except Exception:
            _FH_FILE = None

        # 2) native SEH filter + minidump writer
        native_ok = _install_native_filter()

        # 3) excepthooks for ordinary Python failures
        prior_hook = sys.excepthook

        def _hook(etype, value, tb):
            try:
                _python_exception_report("python_exception", etype, value, tb)
            except Exception:
                pass
            try:
                if prior_hook and prior_hook is not sys.__excepthook__:
                    prior_hook(etype, value, tb)
            except Exception:
                pass

        sys.excepthook = _hook

        if hasattr(threading, "excepthook"):
            prior_thread_hook = threading.excepthook

            def _thread_hook(args):
                try:
                    _python_exception_report(
                        "thread_exception", args.exc_type, args.exc_value,
                        args.exc_traceback, thread_name=getattr(args.thread, "name", ""))
                except Exception:
                    pass
                try:
                    if prior_thread_hook:
                        prior_thread_hook(args)
                except Exception:
                    pass

            threading.excepthook = _thread_hook

        # 4) background flusher (+ optional resource sampler)
        try:
            threading.Thread(target=_ring_flush_loop, name="crash-ring-flush",
                             daemon=True).start()
            if _VERBOSE:
                _SAMPLER_STOP.clear()
                threading.Thread(target=_sampler_loop, name="crash-sampler",
                                 daemon=True).start()
        except Exception:
            pass

        # 5) watchdog child (post-mortem reporter for ANY sudden death)
        if os.environ.get("AUTOGRADER_NO_WATCHDOG", "") != "1":
            _spawn_watchdog(os.getpid(), time.time())

        set_grading_state(phase="starting", context=context)
        record("diagnostics_installed", context=context, native_filter=native_ok,
               faulthandler=_FH_FILE is not None, verbose=_VERBOSE,
               crash_dir=os.path.abspath(_CRASH_DIR))
        _INSTALLED = True


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

"""
hang_monitor.py - Comprehensive hang detection and protection system

This module provides:
1. Global timeout protection for evaluation operations
2. Watchdog monitoring with heartbeat checking
3. Process-level hang detection and intervention
4. Automatic recovery mechanisms
"""

import json
import os
import sys
import time
import threading
import signal
from datetime import datetime, timezone
from typing import Optional, Callable, Any
from contextlib import contextmanager

from logger import log


# === Configuration ===
DEFAULT_HANG_TIMEOUT_SECONDS = 60  # How long before we consider something hung
DEFAULT_WATCHDOG_INTERVAL_SECONDS = 5  # How often to check for hangs
DEFAULT_MAX_RESTARTS = 3  # Maximum times to restart a hanging evaluation


# === Global State ===
_hang_monitor_thread: Optional[threading.Thread] = None
_hang_monitor_stop_event: Optional[threading.Event] = None
_last_heartbeat_check: float = 0
_hang_count: int = 0


# === Heartbeat Management ===

def write_heartbeat_file(hang_stage: str = "unknown", pid: int = None) -> None:
    """Write current heartbeat with stage information for monitoring."""
    try:
        pid = pid or os.getpid()
        data = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "pid": pid,
            "stage": hang_stage,
            "timestamp_epoch": time.time()
        }
        with open("heartbeat.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log("WARNING", f"Failed to write heartbeat: {e}")


def get_heartbeat() -> Optional[dict]:
    """Read and parse the heartbeat file."""
    try:
        if not os.path.exists("heartbeat.json"):
            return None
        with open("heartbeat.json", "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log("WARNING", f"Failed to read heartbeat: {e}")
        return None


def is_heartbeat_stale(timeout_seconds: float = DEFAULT_HANG_TIMEOUT_SECONDS) -> bool:
    """Check if the heartbeat is stale (indicating a hang)."""
    heartbeat = get_heartbeat()
    if heartbeat is None:
        return True  # No heartbeat means something is wrong
    
    try:
        last_update = heartbeat.get("timestamp_epoch")
        if last_update is None:
            return True  # Missing timestamp
        
        elapsed = time.time() - last_update
        return elapsed > timeout_seconds
    except Exception:
        return True


def get_heartbeat_stage() -> str:
    """Get the current stage from heartbeat."""
    heartbeat = get_heartbeat()
    if heartbeat:
        return heartbeat.get("stage", "unknown")
    return "unknown"


# === Context Manager for Global Timeout ===

@contextmanager
def global_timeout(timeout_seconds: float, stage: str = "unknown"):
    """
    Context manager that enforces a global timeout on operations.
    
    Usage:
        with global_timeout(30, "rubric_generation"):
            rubric = generate_rubric(question, expected)
    """
    start_time = time.time()
    
    def timeout_handler(signum, frame):
        elapsed = time.time() - start_time
        raise TimeoutError(
            f"Global timeout exceeded after {elapsed:.1f}s in stage '{stage}'. "
            f"Operation did not complete within {timeout_seconds}s limit."
        )
    
    # Only set up signal handler on Unix-like systems
    if sys.platform != 'win32':
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(int(timeout_seconds) + 1)  # Add 1s buffer
    
    try:
        yield
    finally:
        if sys.platform != 'win32':
            signal.alarm(0)  # Cancel the alarm
            signal.signal(signal.SIGALRM, old_handler)


# === Watchdog Monitoring Thread ===

class HangWatchdog:
    """Background thread that monitors for hung processes."""
    
    def __init__(
        self,
        timeout_seconds: float = DEFAULT_HANG_TIMEOUT_SECONDS,
        check_interval: float = DEFAULT_WATCHDOG_INTERVAL_SECONDS,
        max_restarts: int = DEFAULT_MAX_RESTARTS,
        on_hang_detected: Callable[[dict], None] = None,
        on_hang_recovered: Callable[[dict], None] = None
    ):
        self.timeout_seconds = timeout_seconds
        self.check_interval = check_interval
        self.max_restarts = max_restarts
        self.on_hang_detected = on_hang_detected or self._default_hang_handler
        self.on_hang_recovered = on_hang_recovered or self._default_recovery_handler
        
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._hang_count = 0
        self._last_check_time = 0
        
        log("INFO", f"HangWatchdog initialized: timeout={timeout_seconds}s, interval={check_interval}s")
    
    def _default_hang_handler(self, heartbeat_data: dict) -> None:
        """Default handler when a hang is detected."""
        pid = heartbeat_data.get("pid", "unknown")
        stage = heartbeat_data.get("stage", "unknown")
        last_update = heartbeat_data.get("last_update", "unknown")
        
        log("CRITICAL", f"HANG DETECTED! PID={pid}, Stage={stage}, LastUpdate={last_update}")
        self._hang_count += 1
        
        # Write to a separate hang log
        try:
            with open("hang_log.json", "a") as f:
                json.dump({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pid": pid,
                    "stage": stage,
                    "last_update": last_update,
                    "restart_number": self._hang_count
                }, f)
                f.write("\n")
        except Exception as e:
            log("WARNING", f"Failed to write hang log: {e}")
        
        # If we've exceeded max restarts, log a critical error
        if self._hang_count > self.max_restarts:
            log("CRITICAL", f"MAX HANG RESTARTS EXCEEDED ({self._hang_count}/{self.max_restarts}). Emergency shutdown required.")
    
    def _default_recovery_handler(self, heartbeat_data: dict) -> None:
        """Default handler when a hang is recovered."""
        pid = heartbeat_data.get("pid", "unknown")
        stage = heartbeat_data.get("stage", "unknown")
        log("INFO", f"Hang recovered for PID={pid}, Stage={stage}")
    
    def _check_for_hangs(self) -> bool:
        """Check if a hang has occurred. Returns True if hang detected."""
        heartbeat = get_heartbeat()
        if heartbeat is None:
            return False  # No heartbeat yet, not necessarily a hang
        
        try:
            last_update = heartbeat.get("timestamp_epoch")
            if last_update is None:
                return False
            
            elapsed = time.time() - last_update
            is_hung = elapsed > self.timeout_seconds

            if is_hung:
                log("WARNING", f"Hang detected: elapsed={elapsed:.1f}s > timeout={self.timeout_seconds}s")
                self._on_hang_handler(heartbeat)
                return True
            
            return False
        except Exception as e:
            log("WARNING", f"Error checking for hangs: {e}")
            return False
    
    def _on_hang_handler(self, heartbeat_data: dict) -> None:
        """Call the hang handler."""
        try:
            self.on_hang_detected(heartbeat_data)
        except Exception as e:
            log("ERROR", f"Error in hang handler: {e}")
    
    def start(self) -> None:
        """Start the watchdog monitoring thread."""
        if self._running:
            log("WARNING", "HangWatchdog already running")
            return
        
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        log("INFO", "HangWatchdog monitoring thread started")
    
    def stop(self) -> None:
        """Stop the watchdog monitoring thread."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        log("INFO", "HangWatchdog monitoring thread stopped")
    
    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while not self._stop_event.is_set():
            try:
                time.sleep(self.check_interval)
                
                if not self._running:
                    break
                
                self._check_for_hangs()
                
            except Exception as e:
                log("ERROR", f"Error in hang monitoring loop: {e}")
    
    def is_running(self) -> bool:
        """Check if the watchdog is currently running."""
        return self._running
    
    def get_hang_count(self) -> int:
        """Get the number of hangs detected since startup."""
        return self._hang_count


# === Process-Level Timeout Protection ===


def run_with_timeout(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    timeout_seconds: float = DEFAULT_HANG_TIMEOUT_SECONDS,
    stage: str = "unknown",
    fallback: Any = None
) -> Any:
    """
    Run a function with timeout protection using threading.
    
    Note: We use threading instead of multiprocessing because:
    1. multiprocessing on Windows requires picklable functions
    2. Threading is sufficient for most I/O-bound operations
    3. For CPU-bound operations, we can use thread-based timeouts with polling
    
    Args:
        func: Function to execute
        args: Positional arguments for func
        kwargs: Keyword arguments for func
        timeout_seconds: Maximum time to wait
        stage: Stage name for logging
        fallback: Value to return on timeout/exception
    
    Returns:
        Function result or fallback on timeout/error
    """
    kwargs = kwargs or {}
    result = [fallback]
    exception = [None]
    
    def worker():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exception[0] = e
    
    log("INFO", f"START run_with_timeout stage={stage} timeout={timeout_seconds}s")
    
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    
    # Poll for completion with timeout
    poll_interval = 0.1
    elapsed = 0
    while thread.is_alive() and elapsed < timeout_seconds:
        time.sleep(poll_interval)
        elapsed += poll_interval
    
    if thread.is_alive():
        log("WARNING", f"Thread timeout in stage '{stage}' after {timeout_seconds}s")
        return fallback
    
    if exception[0] is not None:
        log("WARNING", f"Exception in stage '{stage}': {exception[0]}")
        return fallback
    
    log("INFO", f"END run_with_timeout stage={stage} status=success")
    return result[0]


# === Decorator for Easy Timeout Protection ===

def timeout_protected(
    timeout_seconds: float = DEFAULT_HANG_TIMEOUT_SECONDS,
    stage: str = None,
    fallback: Any = None
):
    """
    Decorator to add timeout protection to a function.
    
    Usage:
        @timeout_protected(timeout_seconds=30, stage="rubric_generation")
        def generate_rubric(question, expected):
            ...
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            stage_name = stage or func.__name__
            return run_with_timeout(
                func, args, kwargs, timeout_seconds, stage_name, fallback
            )
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


# === Enhanced evaluation pipeline with global timeout ===

def evaluate_answers_with_global_timeout(
    evaluate_func: Callable,
    *args,
    timeout_seconds: float = None,
    stage: str = "evaluation",
    **kwargs
):
    """
    Wrap an evaluation function with global timeout protection.
    
    Args:
        evaluate_func: The evaluation function to wrap
        *args: Arguments to pass to the function
        timeout_seconds: Global timeout (default: from config or 30s)
        stage: Stage name for logging
        **kwargs: Keyword arguments to pass to the function
    
    Returns:
        Result from evaluate_func or empty list on timeout
    """
    if timeout_seconds is None:
        # Try to get from config, default to 30 seconds
        try:
            with open("config.json") as f:
                config = json.load(f)
            timeout_seconds = float(config.get("max_latency_per_answer_seconds", 30))
        except Exception:
            timeout_seconds = 30.0
    
    return run_with_timeout(
        evaluate_func,
        args,
        kwargs,
        timeout_seconds,
        stage,
        []  # Return empty list on timeout
    )


# === Simple Standalone Hang Monitor (for external use) ===

def standalone_hang_monitor(
    timeout_seconds: float = DEFAULT_HANG_TIMEOUT_SECONDS,
    check_interval: float = DEFAULT_WATCHDOG_INTERVAL_SECONDS
):
    """
    Run a simple standalone hang monitor.
    
    This can be used as a separate process:
        python -c "from hang_monitor import standalone_hang_monitor; standalone_hang_monitor()"
    """
    log("INFO", f"Starting standalone hang monitor: timeout={timeout_seconds}s, interval={check_interval}s")
    log("INFO", "Standalone monitor is watching heartbeat.json for hangs")
    
    while True:
        try:
            time.sleep(check_interval)
            
            heartbeat = get_heartbeat()
            if heartbeat is None:
                continue
            
            last_update = heartbeat.get("timestamp_epoch")
            if last_update is None:
                continue
            
            elapsed = time.time() - last_update
            
            if elapsed > timeout_seconds:
                pid = heartbeat.get("pid", "unknown")
                stage = heartbeat.get("stage", "unknown")
                
                log("CRITICAL", f"HANG DETECTED!")
                log("CRITICAL", f"  PID: {pid}")
                log("CRITICAL", f"  Stage: {stage}")
                log("CRITICAL", f"  Elapsed: {elapsed:.1f}s")
                log("CRITICAL", f"  Last Update: {heartbeat.get('last_update', 'unknown')}")
                
                # The calling process should handle termination
                # This monitor just reports the hang
                break
                
        except KeyboardInterrupt:
            log("INFO", "Standalone monitor stopped by user")
            break
        except Exception as e:
            log("ERROR", f"Error in standalone monitor: {e}")


# === Thread-based Timeout (Legacy, less reliable than multiprocessing) ===

def run_with_thread_timeout(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    timeout_seconds: float = DEFAULT_HANG_TIMEOUT_SECONDS,
    stage: str = "unknown",
    fallback: Any = None
) -> Any:
    """
    Run a function with timeout protection using threading.
    
    WARNING: This is less reliable than multiprocessing because:
    - The thread cannot be forcefully terminated in Python
    - If the function calls blocking C extensions, it may still hang
    - GIL contention can affect timing
    
    Use multiprocessing.run_with_timeout() instead when possible.
    
    This exists mainly for backwards compatibility with existing code.
    """
    kwargs = kwargs or {}
    result = [fallback]
    exception = [None]
    
    def worker():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exception[0] = e
    
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    
    if thread.is_alive():
        log("WARNING", f"Thread timeout in stage '{stage}' after {timeout_seconds}s")
        # Thread cannot be forcefully killed, just return fallback
        return fallback
    
    if exception[0] is not None:
        log("WARNING", f"Exception in stage '{stage}': {exception[0]}")
        return fallback
    
    return result[0]


# === Utility Functions ===

def clear_heartbeat_file() -> None:
    """Remove the heartbeat file (e.g., after successful completion)."""
    try:
        if os.path.exists("heartbeat.json"):
            os.remove("heartbeat.json")
            log("INFO", "Heartbeat file removed")
    except Exception as e:
        log("WARNING", f"Failed to remove heartbeat file: {e}")


def get_hang_statistics() -> dict:
    """Get statistics about hang monitoring."""
    stats = {
        "timeout_seconds": DEFAULT_HANG_TIMEOUT_SECONDS,
        "check_interval_seconds": DEFAULT_WATCHDOG_INTERVAL_SECONDS,
        "max_restarts": DEFAULT_MAX_RESTARTS
    }
    
    # Try to read hang log
    try:
        if os.path.exists("hang_log.json"):
            with open("hang_log.json", "r") as f:
                lines = f.readlines()
                stats["total_hangs_detected"] = len(lines)
                if lines:
                    last_hang = json.loads(lines[-1])
                    stats["last_hang"] = last_hang
    except Exception:
        stats["total_hangs_detected"] = 0
    
    return stats

# Hang Detection and Timeout Protection System

## Overview

This document describes the comprehensive hang detection and timeout protection system implemented in the Google Form Autograder.

## Problem Statement

The application was experiencing random hanging/freezing issues with no effective monitoring or recovery mechanism. The original "heartbeat" system only wrote timestamps to a file but had no actual monitoring or intervention capabilities.

## Root Causes

1. **`asyncio.run()` in `ai_judges.py`** - Creating new event loops on every judge call, causing deadlocks in GUI applications
2. **No global timeout wrapper** - Individual steps had timeouts, but the entire evaluation could run indefinitely
3. **Blocking `thread.join()` calls** - Could hang forever if Ollama was unresponsive
4. **No watchdog process** - No active monitoring of process health
5. **Synchronous Ollama calls** - No timeout on blocking I/O operations

## Solution Architecture

### 1. Enhanced Heartbeat System

The heartbeat now includes:
- `last_update`: ISO 8601 timestamp
- `pid`: Process ID
- `stage`: Current stage (e.g., "deterministic_checks", "jury_consensus", "embedding")
- `timestamp_epoch`: Unix timestamp for easy elapsed time calculation

### 2. Thread-Based Timeout Protection

All blocking operations use polling-based timeout detection:
```python
thread.start()
while thread.is_alive() and elapsed < timeout_seconds:
    time.sleep(poll_interval)
    elapsed += poll_interval
```

This approach:
- Works reliably on Windows
- Allows graceful interruption
- Provides better control than `thread.join(timeout)`

### 3. Event Loop Handling in `ai_judges.py`

The `run_judges()` function now:
- Checks for existing event loops first
- Uses `asyncio.get_event_loop()` when available
- Falls back to `asyncio.run()` only when needed
- Provides synchronous fallback when async execution fails

### 4. Comprehensive Watchdog System

The `HangWatchdog` class provides:
- Background monitoring thread
- Configurable timeout and check interval
- Customizable handlers for hang detection
- Automatic restart limiting
- Detailed logging of hang events

## Files Modified

### Core Files
- `ai_evaluator_semantic.py` - Added stage info to heartbeat
- `evaluation_pipeline.py` - Added stage tracking and improved timeouts
- `ai_judges.py` - Fixed event loop handling, added synchronous fallback
- `embeddings.py` - Polling-based timeout for Ollama calls
- `rubric_generator.py` - Polling-based timeout for Ollama calls
- `confidence_router.py` - Added timeout for reasoning fallback
- `deterministic_checks.py` - Improved elapsed time checks
- `main.py` - Enhanced heartbeat with stage info

### New Files
- `hang_monitor.py` - Comprehensive monitoring system with:
  - `_write_heartbeat_if_needed()` - Write heartbeat with stage
  - `get_heartbeat()` - Read and parse heartbeat file
  - `is_heartbeat_stale()` - Check if process is hung
  - `run_with_timeout()` - Thread-based timeout protection
  - `evaluate_answers_with_global_timeout()` - Global timeout wrapper
  - `HangWatchdog` - Background monitoring class
  - `timeout_protected()` - Decorator for easy timeout protection

## Usage Examples

### Basic Timeout Protection
```python
from hang_monitor import run_with_timeout

def expensive_operation():
    # Some operation that might hang
    return result

result = run_with_timeout(expensive_operation, timeout_seconds=30, fallback=None)
```

### Using the Decorator
```python
from hang_monitor import timeout_protected

@timeout_protected(timeout_seconds=30, stage="rubric_generation", fallback={})
def generate_rubric(question, expected):
    # Long-running operation
    return rubric
```

### Watchdog Monitoring
```python
from hang_monitor import HangWatchdog

def on_hang_detected(heartbeat_data):
    print(f"Process {heartbeat_data['pid']} hung at stage {heartbeat_data['stage']}")
    # Could send alert, restart process, etc.

watchdog = HangWatchdog(
    timeout_seconds=60,
    check_interval=5,
    max_restarts=3,
    on_hang_detected=on_hang_detected
)
watchdog.start()

# ... do work ...

watchdog.stop()
```

### Global Timeout Wrapper
```python
from hang_monitor import evaluate_answers_with_global_timeout

result = evaluate_answers_with_global_timeout(
    evaluate_answers,
    question, answers, expected,
    timeout_seconds=30,
    stage="evaluation"
)
```

## Configuration

### In `config.json`
```json
{
    "max_latency_per_answer_seconds": 30,
    "enable_async_judges": true,
    "ollama_options": {
        "judge_num_ctx": 2048,
        "judge_num_predict": 256
    }
}
```

## Monitoring the System

### Check Heartbeat Status
```python
from hang_monitor import get_heartbeat, is_heartbeat_stale

heartbeat = get_heartbeat()
if is_heartbeat_stale(timeout_seconds=60):
    print("Process appears to be hung!")
```

### Check Hang Statistics
```python
from hang_monitor import get_hang_statistics

stats = get_hang_statistics()
print(f"Hangs detected: {stats['total_hangs_detected']}")
```

### View Hang Log
The system writes detailed hang events to `hang_log.json`:
```json
{
    "timestamp": "2026-05-31T17:59:33+00:00",
    "pid": 12345,
    "stage": "watchdog_stale",
    "last_update": "2020-01-01T00:00:00+00:00",
    "restart_number": 1
}
```

## Best Practices

1. **Always write heartbeat before long operations**
   ```python
   _write_heartbeat_if_needed(hang_stage="operation_name")
   ```

2. **Use timeout protection for all blocking I/O**
   ```python
   result = run_with_timeout(expensive_call, timeout_seconds=30, fallback=[])
   ```

3. **Monitor the watchdog in production**
   ```python
   watchdog = HangWatchdog(timeout_seconds=60, check_interval=5)
   watchdog.start()
   ```

4. **Log important events with stage information**
   - Deterministic checks
   - Rubric generation
   - Embedding calculations
   - Jury consensus
   - Reasoning fallback

## Testing

Run the test suite:
```bash
python test_hang_monitor.py
```

All tests should pass:
- Heartbeat writing with stage info
- Stale heartbeat detection
- Timeout protection
- Watchdog monitoring
- Decorator-based protection

## Recovery Mechanisms

When a hang is detected:
1. First occurrence: Log warning, increment restart counter
2. Subsequent occurrences: Continue logging, up to max_restarts
3. Max restarts exceeded: Log critical error, require manual intervention

## Future Enhancements

Potential improvements:
1. Automatic process restart after hang detection
2. Email/SMS alerts for critical hangs
3. Webhook integration for external monitoring systems
4. Performance profiling to identify slow operations
5. Adaptive timeout based on historical performance

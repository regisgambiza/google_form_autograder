# Quick Reference: Hang/Fix Summary

## Problem Summary

**Issue**: Random hanging/freezing in the application with broken monitoring system.

**Root Cause**: Multiple blocking operations without proper timeout protection, plus a heartbeat system that only wrote timestamps but didn't monitor for hangs.

## What Was Fixed

### 1. asyncio Event Loop Issues (`ai_judges.py`)
- **Problem**: `asyncio.run()` creates new event loops on every call, causing deadlocks
- **Fix**: Check for existing loop first, use thread-based wrapper for running loops, fallback to sync execution

### 2. Blocking Thread.join() (`embeddings.py`, `rubric_generator.py`)
- **Problem**: `thread.join(timeout)` blocks indefinitely if thread hangs
- **Fix**: Poll with `time.sleep()` in a loop for proper timeout checking

### 3. Ollama Call Timeouts (`confidence_router.py`)
- **Problem**: Synchronous Ollama calls have no timeout protection
- **Fix**: Wrap in thread with polling-based timeout

### 4. No Global Timeout (`evaluation_pipeline.py`)
- **Problem**: Individual steps have timeouts, but no overall limit
- **Fix**: Stage tracking in heartbeats, monitoring system watching for stale heartbeats

### 5. Heartbeat Monitoring (`main.py`, `hang_monitor.py`)
- **Problem**: Heartbeat only wrote timestamp, no monitoring
- **Fix**: Enhanced heartbeat with stage info, watchdog system to detect stale heartbeats

### 6. Global Timeout Wrapper (`hang_monitor.py`)
- **Problem**: No way to set overall timeout on evaluation
- **Fix**: `evaluate_answers_with_global_timeout()` wrapper function

## New Files

| File | Purpose |
|------|---------|
| `hang_monitor.py` | Core monitoring system with timeout protection |
| `test_hang_monitor.py` | Test suite for monitoring system |
| `HANG_MONITOR_README.md` | Detailed documentation |
| `QUICK_REFERENCE.md` | This file |

## Modified Files

| File | Changes |
|------|---------|
| `evaluation_pipeline.py` | Stage tracking in heartbeats, improved timeouts |
| `ai_judges.py` | Fixed event loop handling, sync fallback |
| `embeddings.py` | Polling-based timeout for embeddings |
| `rubric_generator.py` | Polling-based timeout for rubric generation |
| `confidence_router.py` | Added timeout for reasoning fallback |
| `deterministic_checks.py` | Improved elapsed time checks |
| `main.py` | Enhanced heartbeat with stage info |
| `ai_evaluator_2.py` | Stage tracking in heartbeats |

## Key Functions in `hang_monitor.py`

```python
# Write heartbeat with stage
_write_heartbeat_if_needed(hang_stage="stage_name")

# Check if heartbeat is stale
is_heartbeat_stale(timeout_seconds=60)

# Get current stage from heartbeat
get_heartbeat_stage()

# Run with timeout
run_with_timeout(func, args, kwargs, timeout_seconds, stage, fallback)

# Global timeout wrapper
evaluate_answers_with_global_timeout(func, *args, timeout_seconds, stage, **kwargs)

# Hang watchdog
watchdog = HangWatchdog(timeout_seconds=60, check_interval=5)
watchdog.start()
watchdog.stop()
```

## Testing

Run the test suite:
```bash
python test_hang_monitor.py
```

Expected output: `Results: 6 passed, 0 failed`

## Configuration

No new configuration needed - system uses existing `config.json` settings.

## Monitoring Stages

The system now tracks these stages:
- `initialization` - Starting up
- `form_fetch` - Fetching form data
- `deterministic_checks` - Running deterministic checks
- `rubric_generation` - Generating rubric
- `concept_scoring` - Scoring concepts
- `misconception_detection` - Detecting misconceptions
- `jury_consensus` - Running judge jury
- `reasoning` - Reasoning fallback
- `answer_evaluation` - Evaluating answers
- `form_complete` - Form processing complete
- `complete` - All forms processed

## Benefits

1. **No more silent hangs** - System detects and logs hangs
2. **Better error recovery** - Timeout protection prevents indefinite blocking
3. **Detailed diagnostics** - Stage info helps identify where hangs occur
4. **Watchdog monitoring** - Background thread detects stale heartbeats
5. **Configurable timeouts** - Easy to adjust per operation

## Usage in Code

```python
# Before (vulnerable to hangs):
result = expensive_operation()

# After (protected):
from hang_monitor import run_with_timeout
result = run_with_timeout(expensive_operation, timeout_seconds=30, fallback=None)
```

## Next Steps

1. Run `python test_hang_monitor.py` to verify all fixes
2. Monitor `hang_log.json` for any hangs during production use
3. Adjust `max_latency_per_answer_seconds` in config if needed
4. Check `heartbeat.json` for current stage info

## Rollback

If issues occur, the fixes are backward compatible. The old `heartbeat.json` format will still work (just without stage info).

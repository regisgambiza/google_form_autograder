#!/usr/bin/env python
"""
test_hang_monitor.py - Test script for hang monitoring system

This script tests the various hang monitoring and timeout protection features.
"""

import time
import json
import os
import sys

# Add current directory to path
sys.path.insert(0, os.getcwd())

from hang_monitor import (
    write_heartbeat_file,
    get_heartbeat,
    is_heartbeat_stale,
    get_heartbeat_stage,
    run_with_timeout,
    evaluate_answers_with_global_timeout,
    HangWatchdog,
    timeout_protected
)


def test_heartbeat_writing():
    """Test writing heartbeat with stage info."""
    print("Testing heartbeat writing...")
    write_heartbeat_file(hang_stage="test_stage", pid=12345)
    
    time.sleep(0.1)
    
    heartbeat = get_heartbeat()
    assert heartbeat is not None, "Heartbeat should exist"
    assert heartbeat.get("stage") == "test_stage", f"Expected stage 'test_stage', got '{heartbeat.get('stage')}'"
    assert heartbeat.get("pid") == 12345, f"Expected pid 12345, got {heartbeat.get('pid')}"
    assert "timestamp_epoch" in heartbeat, "Should have timestamp_epoch"
    assert "last_update" in heartbeat, "Should have last_update"
    
    print("  ✓ Heartbeat writing test passed")


def test_stale_detection():
    """Test stale heartbeat detection."""
    print("Testing stale heartbeat detection...")
    
    # Write fresh heartbeat
    write_heartbeat_file(hang_stage="fresh")
    
    # Should not be stale
    assert not is_heartbeat_stale(timeout_seconds=60), "Fresh heartbeat should not be stale"
    
    # Manually create a stale heartbeat
    stale_data = {
        "last_update": "2020-01-01T00:00:00+00:00",
        "pid": 12345,
        "stage": "stale",
        "timestamp_epoch": time.time() - 120  # 2 minutes ago
    }
    with open("heartbeat.json", "w") as f:
        json.dump(stale_data, f)
    
    # Should be stale
    assert is_heartbeat_stale(timeout_seconds=60), "Old heartbeat should be stale"
    
    print("  ✓ Stale detection test passed")


def test_heartbeat_stage():
    """Test reading heartbeat stage."""
    print("Testing heartbeat stage reading...")
    
    write_heartbeat_file(hang_stage="test_stage_2")
    stage = get_heartbeat_stage()
    assert stage == "test_stage_2", f"Expected stage 'test_stage_2', got '{stage}'"
    
    print("  ✓ Heartbeat stage test passed")


def simple_function():
    """A simple function for timeout testing."""
    return {"result": "success"}


def slow_function():
    """A function that takes time to complete."""
    time.sleep(0.5)
    return {"result": "slow_success"}


def hanging_function():
    """A function that would hang forever."""
    while True:
        time.sleep(1)


def test_timeout_protection():
    """Test timeout protection using run_with_timeout."""
    print("Testing timeout protection...")
    
    # Test successful execution
    result = run_with_timeout(
        simple_function,
        timeout_seconds=10,
        stage="test_success"
    )
    assert result == {"result": "success"}, f"Expected success result, got {result}"
    
    # Test timeout with fast function
    result = run_with_timeout(
        slow_function,
        timeout_seconds=1,  # 1 second timeout
        stage="test_slow"
    )
    assert result == {"result": "slow_success"}, f"Expected slow success, got {result}"
    
    # Test timeout with slow function (should timeout)
    result = run_with_timeout(
        slow_function,
        timeout_seconds=0.2,  # 0.2 second timeout - should fail
        stage="test_timeout",
        fallback={"result": "timeout"}
    )
    assert result == {"result": "timeout"}, f"Expected timeout fallback, got {result}"
    
    print("  ✓ Timeout protection test passed")


def test_watchdog():
    """Test the hang watchdog."""
    print("Testing hang watchdog...")
    
    # Create a watchdog with short intervals for testing
    watchdog = HangWatchdog(
        timeout_seconds=2,  # 2 second timeout
        check_interval=0.5,  # Check every 0.5 seconds
        max_restarts=3
    )
    
    # Start the watchdog
    watchdog.start()
    time.sleep(0.5)
    
    # Write a fresh heartbeat
    write_heartbeat_file(hang_stage="watchdog_test")
    
    # Wait a bit - should not detect hang yet
    time.sleep(1)
    assert watchdog.get_hang_count() == 0, "Should not detect hang yet"
    
    # Write a stale heartbeat (should trigger hang detection)
    stale_data = {
        "last_update": "2020-01-01T00:00:00+00:00",
        "pid": 12345,
        "stage": "watchdog_stale",
        "timestamp_epoch": time.time() - 5  # 5 seconds ago
    }
    with open("heartbeat.json", "w") as f:
        json.dump(stale_data, f)
    
    # Wait for watchdog to detect hang
    time.sleep(2)
    
    # Stop the watchdog
    watchdog.stop()
    
    print(f"  ✓ Hang watchdog test passed (detected {watchdog.get_hang_count()} hang(s))")


def test_timeout_protected_decorator():
    """Test the timeout_protected decorator."""
    print("Testing timeout_protected decorator...")
    
    @timeout_protected(timeout_seconds=5, stage="decorator_test", fallback={"result": "fallback"})
    def test_func():
        return {"result": "decorator_success"}
    
    result = test_func()
    assert result == {"result": "decorator_success"}, f"Expected success, got {result}"
    
    print("  ✓ Timeout protected decorator test passed")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Hang Monitor Test Suite")
    print("=" * 60)
    
    tests = [
        test_heartbeat_writing,
        test_stale_detection,
        test_heartbeat_stage,
        test_timeout_protection,
        test_watchdog,
        test_timeout_protected_decorator,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
                print(f"  ✗ {test.__name__} failed")
        except Exception as e:
            failed += 1
            print(f"  ✗ {test.__name__} raised exception: {e}")
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    # Clean up
    if os.path.exists("heartbeat.json"):
        os.remove("heartbeat.json")
    
    if os.path.exists("hang_log.json"):
        os.remove("hang_log.json")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

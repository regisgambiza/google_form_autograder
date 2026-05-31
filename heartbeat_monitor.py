#!/usr/bin/env python3
"""
Heartbeat Monitor for Google Form Autograder

Monitors the autograder process and restarts it if it becomes unresponsive.
Uses a heartbeat file to detect hangs - if the file isn't updated within
the timeout period, the process is considered stuck and gets restarted.
"""

import os
import sys
import time
import subprocess
import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Configuration
HEARTBEAT_FILE = "heartbeat.json"
HEARTBEAT_TIMEOUT_SECONDS = 90  # Restart if no heartbeat for 90 seconds
CHECK_INTERVAL_SECONDS = 10     # Check heartbeat every 10 seconds
MAX_RESTARTS = 5                # Maximum restarts before giving up
RESTARTCooldown_SECONDS = 60    # Wait 60 seconds before restarting


class HeartbeatMonitor:
    def __init__(self, main_script="main.py"):
        self.main_script = main_script
        self.process = None
        self.restart_count = 0
        self.running = True
        self.last_heartbeat = None
        self.main_thread = None

    def get_current_heartbeat_time(self):
        """Read the last heartbeat timestamp from file."""
        if not os.path.exists(HEARTBEAT_FILE):
            return None
        try:
            with open(HEARTBEAT_FILE, "r") as f:
                data = json.load(f)
                timestamp_str = data.get("last_update")
                if timestamp_str:
                    return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except Exception as e:
            print(f"[{datetime.now()}] Warning: Could not read heartbeat file: {e}")
        return None

    def write_heartbeat(self):
        """Write current timestamp to heartbeat file."""
        try:
            data = {
                "last_update": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid()
            }
            with open(HEARTBEAT_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[{datetime.now()}] Error writing heartbeat: {e}")

    def start_main_process(self):
        """Start the main autograder process."""
        if self.restart_count >= MAX_RESTARTS:
            print(f"[{datetime.now()}] Maximum restarts ({MAX_RESTARTS}) reached. Exiting.")
            self.running = False
            return

        print(f"[{datetime.now()}] Starting autograder (attempt {self.restart_count + 1}/{MAX_RESTARTS})...")

        my_env = os.environ.copy()
        my_env["PYTHONIOENCODING"] = "utf-8"

        self.process = subprocess.Popen(
            [sys.executable, self.main_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8',
            env=my_env
        )

        print(f"[{datetime.now()}] Autograder started with PID {self.process.pid}")

    def stop_main_process(self):
        """Stop the main autograder process gracefully."""
        if self.process and self.process.poll() is None:
            print(f"[{datetime.now()}] Stopping autograder (PID {self.process.pid})...")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print(f"[{datetime.now()}] Process did not terminate, killing it...")
                self.process.kill()
            print(f"[{datetime.now()}] Autograder stopped.")

    def check_heartbeat(self):
        """Check if the autograder is still responsive."""
        # Get current heartbeat time from file
        current_heartbeat = self.get_current_heartbeat_time()

        if current_heartbeat is None:
            # No heartbeat yet - might be starting up
            if self.process and self.process.poll() is None:
                print(f"[{datetime.now()}] No heartbeat yet - process is starting up...")
                return True  # Still considered alive
            return False

        # Check if heartbeat is stale
        now = datetime.now(current_heartbeat.tzinfo)
        time_since_heartbeat = (now - current_heartbeat).total_seconds()

        if time_since_heartbeat > HEARTBEAT_TIMEOUT_SECONDS:
            print(f"[{datetime.now()}] CRITICAL: Heartbeat stale for {time_since_heartbeat:.1f}s!")
            print(f"[{datetime.now()}] Last heartbeat: {current_heartbeat}")
            print(f"[{datetime.now()}] Process (PID {self.process.pid}) appears hung!")
            return False

        # Update our tracking
        if self.last_heartbeat is None or current_heartbeat > self.last_heartbeat:
            print(f"[{datetime.now()}] Heartbeat OK - {time_since_heartbeat:.1f}s since last update")
            self.last_heartbeat = current_heartbeat

        return True

    def run(self):
        """Main monitoring loop."""
        print(f"[{datetime.now()}] Heartbeat Monitor Started")
        print(f"[{datetime.now()}] Timeout: {HEARTBEAT_TIMEOUT_SECONDS}s, Check interval: {CHECK_INTERVAL_SECONDS}s")

        # Start the main autograder process
        self.start_main_process()

        last_check_time = time.time()

        while self.running:
            try:
                current_time = time.time()

                # Only check heartbeat at the configured interval
                if current_time - last_check_time >= CHECK_INTERVAL_SECONDS:
                    last_check_time = current_time

                    # Check if process is still running
                    if self.process.poll() is not None:
                        print(f"[{datetime.now()}] Autograder process exited with code {self.process.returncode}")
                        break

                    # Check heartbeat
                    if not self.check_heartbeat():
                        print(f"[{datetime.now()}] Detected hang - restarting autograder...")
                        self.stop_main_process()
                        self.restart_count += 1
                        time.sleep(RESTARTCooldown_SECONDS)
                        self.start_main_process()

                # Small sleep to avoid busy-waiting
                time.sleep(1)

            except KeyboardInterrupt:
                print(f"\n[{datetime.now()}] Received shutdown signal.")
                self.running = False
                self.stop_main_process()

        print(f"[{datetime.now()}] Heartbeat Monitor stopped.")
        print(f"[{datetime.now()}] Total restarts: {self.restart_count}")


if __name__ == "__main__":
    import threading
    from datetime import timezone

    # Also patch main.py to write heartbeats
    # The monitor will write its own heartbeat periodically to show it's alive
    monitor = HeartbeatMonitor()
    monitor.run()

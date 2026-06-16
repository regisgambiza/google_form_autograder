# grader_thread.py - FIXED (no more "text" unbound error)
import sys
import os
import subprocess
from PyQt5.QtCore import QThread, pyqtSignal


class GraderThread(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(int, int)
    overall_progress = pyqtSignal(int, int)
    debug_message = pyqtSignal(str)
    current_form = pyqtSignal(str)
    finished_form = pyqtSignal(str)

    def __init__(self, grade_recent_only=False):
        super().__init__()
        self.grade_recent_only = grade_recent_only
        self.process = None
        self._stop_requested = False

    @staticmethod
    def _terminate_process_tree(pid):
        if not pid:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.kill(pid, 15)
        except Exception:
            pass

    @staticmethod
    def terminate_existing_graders():
        """Kill leftover main.py grader children from previous GUI runs."""
        if os.name != "nt":
            return
        current_pid = os.getpid()
        script = (
            f"$self={current_pid}; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "($_.Name -in @('python.exe','pythonw.exe')) -and "
            "$_.ProcessId -ne $self -and "
            "$_.CommandLine -match '(^|[\\\\/\\s\"''])(main\\.py)([\"''\\s]|$)' "
            "} | ForEach-Object { "
            "try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} "
            "}"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

    def stop_grading(self):
        self._stop_requested = True
        if self.process and self.process.poll() is None:
            self._terminate_process_tree(self.process.pid)

    def run(self):
        try:
            self._stop_requested = False
            self.terminate_existing_graders()
            my_env = os.environ.copy()
            my_env["PYTHONIOENCODING"] = "utf-8"

            # Pass grading mode to main.py via environment variable
            my_env["GRADE_RECENT_ONLY"] = str(self.grade_recent_only).lower()
            my_env["AUTOGRADER_GRADER"] = "1"

            main_path = os.path.abspath("main.py")
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(
                [sys.executable, main_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding='utf-8',
                env=my_env,
                creationflags=creationflags,
            )

            # Read stdout line by line (real-time
            for line in self.process.stdout:
                if self._stop_requested:
                    self.stop_grading()
                    break
                if not line:
                    continue
                ls = line.strip()
                self.debug_message.emit(ls)

                # Parse progress messages from main.py
                if ls.startswith("FormProgress:"):
                    try:
                        current, total = map(int, ls.split(":")[1].strip().split("/"))
                        self.progress.emit(current, total)
                    except:
                        pass

                if ls.startswith("Progress:"):
                    try:
                        current, total = map(int, ls.split(":")[1].strip().split("/"))
                        self.overall_progress.emit(current, total)
                    except:
                        pass

                if "Processing form ID:" in ls and "from URL:" in ls:
                    try:
                        url = ls.split("from URL:", 1)[1].strip()
                        self.current_form.emit(url)
                    except:
                        pass

                if "Finished processing form" in ls:
                    try:
                        form_id = ls.split("Finished processing form", 1)[1].strip().split()[0]
                        self.finished_form.emit(form_id)
                    except:
                        pass

                if "[FORM] FINISHED" in ls and "(" in ls and ")" in ls:
                    try:
                        form_id = ls.rsplit("(", 1)[1].split(")", 1)[0].strip()
                        if form_id:
                            self.finished_form.emit(form_id)
                    except:
                        pass

            # Wait for process to finish
            self.process.wait(timeout=10)

            # === SUCCESS / FAILURE HANDLING ===
            if self._stop_requested:
                self.finished.emit(False, "Grading stopped.")
            elif self.process.returncode == 0:
                self.finished.emit(True, "")
            else:
                # stderr is merged into stdout, so no separate stderr drain is needed.
                self.finished.emit(False, "Grader process exited with non-zero return code.")

        except Exception as e:
            self.finished.emit(False, f"Thread crashed: {str(e)}")
        finally:
            if self._stop_requested and self.process and self.process.poll() is None:
                self._terminate_process_tree(self.process.pid)
            self.process = None

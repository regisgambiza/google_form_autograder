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

    def run(self):
        try:
            my_env = os.environ.copy()
            my_env["PYTHONIOENCODING"] = "utf-8"

            # Pass grading mode to main.py via environment variable
            my_env["GRADE_RECENT_ONLY"] = str(self.grade_recent_only).lower()

            process = subprocess.Popen(
                [sys.executable, "main.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding='utf-8',
                env=my_env
            )

            # Read stdout line by line (real-time
            for line in process.stdout:
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

            # Wait for process to finish
            process.wait()

            # === SUCCESS / FAILURE HANDLING ===
            if process.returncode == 0:
                self.finished.emit(True, "")
            else:
                # Properly read stderr only when there is an error
                error_output = process.stderr.read() if process.stderr else ""
                self.finished.emit(False, error_output.strip() or "Unknown error (return code != 0)")

        except Exception as e:
            self.finished.emit(False, f"Thread crashed: {str(e)}")
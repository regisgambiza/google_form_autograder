# grader_thread.py (New file for modularization)
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

    def run(self):
        try:
            my_env = os.environ.copy()
            my_env["PYTHONIOENCODING"] = "utf-8"

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

            for line in iter(process.stdout.readline, ''):
                if not line:
                    continue
                ls = line.strip()
                self.debug_message.emit(ls)

                # Parse per-form progress (responses evaluated in current form)
                if ls.startswith("FormProgress:"):
                    try:
                        current, total = map(int, ls.split(":")[1].strip().split("/"))
                        self.progress.emit(current, total)
                    except ValueError:
                        pass

                # Parse overall progress (forms processed / total forms)
                if ls.startswith("Progress:"):
                    try:
                        current, total = map(int, ls.split(":")[1].strip().split("/"))
                        self.overall_progress.emit(current, total)
                    except ValueError:
                        pass

                if "Processing form ID:" in ls and "from URL:" in ls:
                    try:
                        url = ls.split("from URL:", 1)[1].strip()
                        self.current_form.emit(url)
                    except Exception:
                        pass

                if "Finished processing form" in ls:
                    try:
                        remainder = ls.split("Finished processing form", 1)[1].strip()
                        form_id = remainder.split()[0]
                        self.finished_form.emit(form_id)
                    except Exception:
                        pass

            process.wait()

            if process.returncode == 0:
                self.finished.emit(True, "")
            else:
                error = process.stderr.read()
                self.finished.emit(False, error)

        except Exception as e:
            self.finished.emit(False, str(e))
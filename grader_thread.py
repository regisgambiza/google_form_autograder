# grader_thread.py - FIXED (no more "text" unbound error)
import sys
import os
import subprocess
import json
import html
from PyQt5.QtCore import QThread, pyqtSignal


class GraderThread(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(int, int)
    overall_progress = pyqtSignal(int, int)
    form_metrics = pyqtSignal(int, int, int, int, int)
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
        main_path = os.path.abspath("main.py").replace("'", "''")
        script = (
            f"$self={current_pid}; $main='{main_path}'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "($_.Name -in @('python.exe','pythonw.exe')) -and "
            "$_.ProcessId -ne $self -and "
            "$_.CommandLine -like ('*' + $main + '*') "
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

    @staticmethod
    def _format_gui_event(event):
        kind = event.get("type")
        esc = lambda value: html.escape(str(value or ""))
        if kind == "run_start":
            return (
                f"<b>Grading: {esc(event.get('form_title'))}</b><br>"
                f"Answers to evaluate: {int(event.get('total', 0))}<br>"
                "────────────────────────────────────"
            )
        if kind == "answer_result":
            decision = str(event.get("decision", "REVIEW")).upper()
            icon = {"YES": "✓", "NO": "✗", "REVIEW": "?"}.get(decision, "?")
            label = {"YES": "ACCEPTED", "NO": "REJECTED", "REVIEW": "NEEDS REVIEW"}.get(decision, decision)
            lines = [
                f"<b>Answer {int(event.get('current', 0))} / {int(event.get('total', 0))}</b>",
                f"<b>Question {int(event.get('question_number', 0))}:</b> {esc(event.get('question'))}",
                f"Expected: {esc(event.get('expected'))}",
                f"Student answer: {esc(event.get('answer'))}",
            ]
            formatting = event.get("formatting") or {}
            if formatting:
                ficon = "✓" if formatting.get("proven") else "•"
                lines.extend(["", "<b>Formatting check:</b>", f"{ficon} {esc(formatting.get('reason'))}"])
                for detail in formatting.get("details", []):
                    lines.append(f"  {esc(detail)}")
            judges = event.get("judges") or []
            if judges:
                lines.extend(["", "<b>AI evaluation:</b>"])
                for judge in judges:
                    verdict = str(judge.get("decision", "ERROR")).upper()
                    jicon = "✓" if verdict == "YES" else "✗" if verdict == "NO" else "?"
                    confidence = float(judge.get("confidence", 0.0) or 0.0) * 100
                    lines.append(
                        f"{jicon} {esc(judge.get('model') or judge.get('role'))}: "
                        f"{esc(verdict)} ({confidence:.0f}%) — {esc(judge.get('reason'))}"
                    )
                    missing = judge.get("requirements_missing") or []
                    contradictions = judge.get("contradictions") or []
                    if missing:
                        lines.append(f"  Missing: {esc('; '.join(map(str, missing)))}")
                    if contradictions:
                        lines.append(f"  Contradictions: {esc('; '.join(map(str, contradictions)))}")
            lines.extend([
                "",
                f"<b>Final decision: {icon} {esc(label)}</b>",
                esc(event.get("action")),
                "",
                f"Progress: {int(event.get('current', 0))}/{int(event.get('total', 0))} | "
                f"Accepted: {int(event.get('accepted', 0))} | "
                f"Needs review: {int(event.get('review', 0))} | "
                f"Rejected: {int(event.get('rejected', 0))} | "
                f"Elapsed: {esc(event.get('elapsed'))}",
                "────────────────────────────────────",
            ])
            return "<br>".join(lines)
        if kind == "run_complete":
            return (
                f"<b>Grading finished</b><br>Accepted: {int(event.get('accepted', 0))} | "
                f"Needs review: {int(event.get('review', 0))} | Rejected: {int(event.get('rejected', 0))}<br>"
                f"Elapsed: {esc(event.get('elapsed'))}"
            )
        return esc(event.get("message", ""))

    def run(self):
        try:
            if self._stop_requested:
                self.finished.emit(False, "Grading stopped.")
                return
            self.terminate_existing_graders()
            if self._stop_requested:
                self.finished.emit(False, "Grading stopped.")
                return
            my_env = os.environ.copy()
            my_env["PYTHONIOENCODING"] = "utf-8"
            my_env["PYTHONUNBUFFERED"] = "1"

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
                if ls.startswith("GUI_EVENT:"):
                    try:
                        event = json.loads(ls.split(":", 1)[1])
                        self.debug_message.emit(self._format_gui_event(event))
                    except Exception:
                        pass
                else:
                    # Mirror detailed child diagnostics to the terminal that
                    # launched the GUI; do not send them to the teacher console.
                    try:
                        print(ls, flush=True)
                    except Exception:
                        pass

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

                if ls.startswith("FormMetrics:"):
                    try:
                        payload = ls.split(":", 1)[1].strip().split()
                        completed, total = map(int, payload[0].split("/", 1))
                        accepted, review_questions, elapsed = map(int, payload[1:4])
                        self.form_metrics.emit(completed, total, accepted, review_questions, elapsed)
                    except Exception:
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
            if self.process:
                if self.process.poll() is None:
                    self._terminate_process_tree(self.process.pid)
                    try:
                        self.process.wait(timeout=5)
                    except Exception:
                        pass
                try:
                    if self.process.stdout:
                        self.process.stdout.close()
                except Exception:
                    pass
            self.process = None

# grader_thread.py - FIXED (no more "text" unbound error)
import sys
import os
import subprocess
import json
import html
import re
from datetime import datetime, timezone
from PySide6.QtCore import QThread, Signal


class GraderThread(QThread):
    finished = Signal(bool, str)
    progress = Signal(int, int)
    overall_progress = Signal(int, int)
    form_metrics = Signal(int, int, int, int, int, int, int, int, float)
    debug_message = Signal(str)
    current_form = Signal(str)
    finished_form = Signal(str)
    skipped_form = Signal(str, str, str, str)

    def __init__(self, grade_recent_only=False, form_urls=None):
        super().__init__()
        self.grade_recent_only = grade_recent_only
        self.form_urls = form_urls or []
        self.process = None
        self._stop_requested = False
        self._gui_log_fh = None
        self._gui_jsonl_fh = None
        self._decision_log_fh = None
        self._decision_jsonl_fh = None
        self.gui_log_path = "logs/gui_terminal.log"
        self.gui_jsonl_path = "logs/gui_terminal.jsonl"
        self.gui_decision_log_path = "logs/gui_decisions.log"
        self.gui_decision_jsonl_path = "logs/gui_decisions.jsonl"

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
        exe_path = os.path.abspath(sys.executable).replace("'", "''")
        script = (
            f"$self={current_pid}; $main='{main_path}'; $exe='{exe_path}'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.ProcessId -ne $self -and "
            "(("
            "$_.Name -in @('python.exe','pythonw.exe') -and "
            "$_.CommandLine -like ('*' + $main + '*')"
            ") -or ("
            "$_.ExecutablePath -eq $exe -and "
            "$_.CommandLine -like '*--grader*'"
            ")) "
            "} | ForEach-Object { "
            "try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; "
            "Wait-Process -Id $_.ProcessId -Timeout 5 -ErrorAction SilentlyContinue } catch {} "
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

    def _open_gui_terminal_logs(self):
        cfg = {}
        try:
            with open("config.json", "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            pass
        self.gui_log_path = str(cfg.get("gui_terminal_log_path", self.gui_log_path))
        self.gui_jsonl_path = str(cfg.get("gui_terminal_jsonl_path", self.gui_jsonl_path))
        self.gui_decision_log_path = str(cfg.get("gui_decision_log_path", self.gui_decision_log_path))
        self.gui_decision_jsonl_path = str(cfg.get("gui_decision_jsonl_path", self.gui_decision_jsonl_path))
        for path in (
            self.gui_log_path,
            self.gui_jsonl_path,
            self.gui_decision_log_path,
            self.gui_decision_jsonl_path,
        ):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._gui_log_fh = open(self.gui_log_path, "w", encoding="utf-8")
        self._gui_jsonl_fh = open(self.gui_jsonl_path, "w", encoding="utf-8")
        self._decision_log_fh = open(self.gui_decision_log_path, "w", encoding="utf-8")
        self._decision_jsonl_fh = open(self.gui_decision_jsonl_path, "w", encoding="utf-8")
        started = datetime.now(timezone.utc).isoformat()
        self._gui_log_fh.write(f"GUI grading transcript started {started}\n\n")
        self._decision_log_fh.write(f"GUI decision audit started {started}\n\n")
        self._gui_log_fh.flush()
        self._decision_log_fh.flush()

    def _close_gui_terminal_logs(self):
        for fh in (self._gui_log_fh, self._gui_jsonl_fh, self._decision_log_fh, self._decision_jsonl_fh):
            try:
                if fh:
                    fh.flush()
                    fh.close()
            except Exception:
                pass
        self._gui_log_fh = None
        self._gui_jsonl_fh = None
        self._decision_log_fh = None
        self._decision_jsonl_fh = None

    @staticmethod
    def _plain_gui_event(rendered_html):
        text = re.sub(r"<br\s*/?>", "\n", str(rendered_html), flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text).strip()

    def _write_gui_terminal_event(self, event, rendered_html):
        if self._gui_jsonl_fh:
            self._gui_jsonl_fh.write(json.dumps(event, ensure_ascii=True) + "\n")
            self._gui_jsonl_fh.flush()
        if self._gui_log_fh:
            timestamp = str(event.get("timestamp", ""))
            self._gui_log_fh.write(f"[{timestamp}]\n{self._plain_gui_event(rendered_html)}\n\n")
            self._gui_log_fh.flush()
        if event.get("type") == "answer_result":
            self._write_decision_audit_event(event)

    @staticmethod
    def _decision_audit_record(event):
        judges = []
        for judge in event.get("judges") or []:
            judges.append({
                "role": judge.get("role", ""),
                "provider": judge.get("provider", ""),
                "model": judge.get("model", ""),
                "decision": str(judge.get("decision", "")).upper(),
                "confidence": float(judge.get("confidence", 0.0) or 0.0),
                "reason": judge.get("reason", ""),
                "requirements_missing": list(judge.get("requirements_missing") or []),
                "contradictions": list(judge.get("contradictions") or []),
            })
        return {
            "timestamp": event.get("timestamp", ""),
            "run_id": event.get("run_id", ""),
            "answer_number": int(event.get("current", 0) or 0),
            "total_answers": int(event.get("total", 0) or 0),
            "question_number": int(event.get("question_number", 0) or 0),
            "question": event.get("question", ""),
            "expected": event.get("expected", ""),
            "student_answer": event.get("answer", ""),
            "formatting": event.get("formatting") or {},
            "judges": judges,
            "final_decision": str(event.get("decision", "REVIEW")).upper(),
            "policy_reason": event.get("policy_reason", ""),
            "action": event.get("action", ""),
            "counts": {
                "accepted": int(event.get("accepted", 0) or 0),
                "needs_review": int(event.get("review", 0) or 0),
                "rejected": int(event.get("rejected", 0) or 0),
            },
            "elapsed": event.get("elapsed", ""),
        }

    @staticmethod
    def _display_judge_label(judge):
        provider = str(judge.get("provider") or "").strip()
        role = str(judge.get("role") or "").strip()
        model = str(judge.get("model") or "").strip()
        if model.startswith("provider-managed:"):
            model = role or model.split(":", 1)[1]
        model_label = model or role
        if provider and model_label:
            return f"{provider} / {model_label}"
        return model_label or provider or "judge"

    @staticmethod
    def _format_decision_audit_record(record):
        lines = [
            f"Answer {record['answer_number']} / {record['total_answers']}",
            f"Question {record['question_number']}: {record['question']}",
            f"Expected: {record['expected']}",
            f"Student answer: {record['student_answer']}",
        ]
        formatting = record.get("formatting") or {}
        if formatting:
            lines.extend([
                "",
                f"Formatting: {'PROVEN' if formatting.get('proven') else 'NOT PROVEN'} - {formatting.get('reason', '')}",
            ])
            for detail in formatting.get("details", []):
                lines.append(f"  - {detail}")
        if record.get("judges"):
            lines.extend(["", "Judge votes:"])
            for judge in record["judges"]:
                label = GraderThread._display_judge_label(judge)
                lines.append(
                    f"  - {label}: {judge['decision']} ({judge['confidence'] * 100:.0f}%) - {judge.get('reason', '')}"
                )
                if judge.get("requirements_missing"):
                    lines.append(f"    missing: {'; '.join(map(str, judge['requirements_missing']))}")
                if judge.get("contradictions"):
                    lines.append(f"    contradictions: {'; '.join(map(str, judge['contradictions']))}")
        lines.extend([
            "",
            f"Final decision: {record['final_decision']}",
            f"Why: {record.get('policy_reason') or 'not provided'}",
            f"Action: {record.get('action', '')}",
            (
                f"Counts: accepted={record['counts']['accepted']} "
                f"review={record['counts']['needs_review']} rejected={record['counts']['rejected']}"
            ),
            f"Elapsed: {record.get('elapsed', '')}",
            "-" * 72,
        ])
        return "\n".join(lines)

    def _write_decision_audit_event(self, event):
        record = self._decision_audit_record(event)
        if self._decision_jsonl_fh:
            self._decision_jsonl_fh.write(json.dumps(record, ensure_ascii=True) + "\n")
            self._decision_jsonl_fh.flush()
        if self._decision_log_fh:
            timestamp = str(record.get("timestamp", ""))
            self._decision_log_fh.write(f"[{timestamp}]\n{self._format_decision_audit_record(record)}\n\n")
            self._decision_log_fh.flush()

    @staticmethod
    def _format_gui_event(event):
        kind = event.get("type")
        esc = lambda value: html.escape(str(value or ""))
        if kind == "run_start":
            return (
                f"<b>Grading: {esc(event.get('form_title'))}</b><br>"
                f"Answers to evaluate: {int(event.get('total', 0))}<br>"
                f"Transcript: {esc(event.get('transcript_path'))}<br>"
                "────────────────────────────────────"
            )
        if kind == "answer_result":
            decision = str(event.get("decision", "REVIEW")).upper()
            icon = {"YES": "✓", "NO": "✗", "REVIEW": "?", "ERROR": "!"}.get(decision, "?")
            label = {"YES": "ACCEPTED", "NO": "REJECTED", "REVIEW": "NEEDS REVIEW", "ERROR": "FAILED"}.get(decision, decision)
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
                    model_label = GraderThread._display_judge_label(judge)
                    lines.append(
                        f"{jicon} {esc(model_label)}: "
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
        if kind == "form_skipped":
            lines = [
                f"<b>Partial form: {esc(event.get('form_title'))}</b>",
                esc(event.get("message") or event.get("reason") or "Form skipped."),
            ]
            missing = event.get("missing_questions") or []
            if missing:
                lines.extend(["", "<b>Missing teacher answers:</b>"])
                for item in missing[:10]:
                    if not isinstance(item, dict):
                        continue
                    lines.append(
                        f"Q{int(item.get('question_number', 0) or 0)}: "
                        f"{esc(item.get('title'))} "
                        f"({int(item.get('responses', 0) or 0)} response(s))"
                    )
                if len(missing) > 10:
                    lines.append(f"+{len(missing) - 10} more")
            lines.append("â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€")
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
            self._open_gui_terminal_logs()

            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            if getattr(sys, "frozen", False):
                args = [sys.executable, "--grader"]
            else:
                main_path = os.path.abspath("main.py")
                args = [sys.executable, main_path]
            if self.form_urls:
                args.extend(self.form_urls)

            self.process = subprocess.Popen(
                args,
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
                        rendered = self._format_gui_event(event)
                        self._write_gui_terminal_event(event, rendered)
                        self.debug_message.emit(rendered)
                        if event.get("type") == "form_skipped":
                            self.skipped_form.emit(
                                str(event.get("form_id") or ""),
                                str(event.get("url") or ""),
                                str(event.get("reason") or "Skipped"),
                                json.dumps(event.get("missing_questions") or [], ensure_ascii=True),
                            )
                    except Exception:
                        pass
                else:
                    # Mirror detailed child diagnostics to the terminal that
                    # launched the GUI; do not send them to the teacher console.
                    try:
                        print(ls, flush=True)
                    except Exception:
                        pass
                    provider_tags = (
                        "[PROVIDER METRICS]",
                        "[PROVIDER WORKER]",
                        "[PROVIDER ROUTE]",
                        "[PROVIDER RETRY]",
                        "[PROVIDER FAILOVER]",
                        "[PROVIDER RECOVERY]",
                        "[PROVIDER]",
                        "[APP WORKER]",
                    )
                    if "[DISPATCH METRICS]" in ls or "[HEARTBEAT]" in ls or any(tag in ls for tag in provider_tags):
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

                if ls.startswith("FormMetrics:"):
                    try:
                        payload = ls.split(":", 1)[1].strip().split()
                        completed, total = map(int, payload[0].split("/", 1))
                        accepted, review_questions, elapsed = map(int, payload[1:4])
                        rejected = 0
                        if len(payload) >= 5:
                            rejected = int(payload[4])
                        extras = {}
                        for token in payload[5:]:
                            if "=" not in token:
                                continue
                            key, value = token.split("=", 1)
                            extras[key] = value
                        det_decisions = int(float(extras.get("det", 0) or 0))
                        ai_decisions = int(float(extras.get("ai", 0) or 0))
                        avg_latency_ms = float(extras.get("avg_ms", 0.0) or 0.0)
                        self.form_metrics.emit(
                            completed,
                            total,
                            accepted,
                            review_questions,
                            elapsed,
                            rejected,
                            det_decisions,
                            ai_decisions,
                            avg_latency_ms,
                        )
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
            self._close_gui_terminal_logs()

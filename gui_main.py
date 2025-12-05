# gui_main.py
import sys
import os
import json
import subprocess
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QProgressBar, QTextEdit, QLabel, QComboBox, QCheckBox,
                             QDateEdit)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDate
from PyQt5.QtGui import QColor, QBrush, QFont
import datetime

# Import Drive helper and Forms service
from auth import get_service, get_drive_service, get_classroom_service


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


class ClassLoaderThread(QThread):
    courses_loaded = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            classroom = get_classroom_service()
            resp = classroom.courses().list(pageSize=200).execute()
            courses = resp.get('courses', [])
            out = [(c.get('name'), c.get('id')) for c in courses if c.get('name')]
            self.courses_loaded.emit(out)
        except Exception as e:
            self.error.emit(str(e))


class FormManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Form Autograder")
        self.setGeometry(100, 100, 1200, 900)

        self.grader_thread = None
        self.forms_data = {}
        self.service = None
        self.finished_forms = []

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Overall and per-form Progress
        progress_layout = QHBoxLayout()
        # Overall (forms)
        self.overall_progress_label = QLabel("Overall: 0%")
        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setMaximum(100)
        progress_layout.addWidget(self.overall_progress_label)
        progress_layout.addWidget(self.overall_progress_bar)
        # Per-form (responses)
        self.progress_label = QLabel("Form: 0%")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        layout.addLayout(progress_layout)

        # Status
        status_layout = QHBoxLayout()
        self.current_label = QLabel("Currently processing: -")
        self.finished_label = QLabel("Finished: 0")
        self.in_queue_label = QLabel("In queue: 0")
        status_layout.addWidget(self.current_label)
        status_layout.addWidget(self.finished_label)
        status_layout.addWidget(self.in_queue_label)
        layout.addLayout(status_layout)

        # Debug output
        self.debug_output = QTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setMinimumHeight(250)
        layout.addWidget(self.debug_output, 2)

        # URL input
        input_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter Google Form URL...")
        add_button = QPushButton("Add Form")
        add_button.clicked.connect(self.add_form)
        input_layout.addWidget(self.url_input)
        input_layout.addWidget(add_button)
        layout.addLayout(input_layout)

        # Classes + Find Forms
        classes_layout = QHBoxLayout()
        classes_label = QLabel("Classes:")
        self.classes_list = QListWidget()
        self.classes_list.setSelectionMode(QListWidget.MultiSelection)
        self.classes_list.setMaximumHeight(100)
        classes_layout.addWidget(classes_label)
        classes_layout.addWidget(self.classes_list)
        refresh_button = QPushButton("Refresh Classes")
        refresh_button.clicked.connect(self.load_classes)
        classes_layout.addWidget(refresh_button)
        layout.addLayout(classes_layout)

        date_layout = QHBoxLayout()
        date_from_label = QLabel("From:")
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addDays(-90))
        date_to_label = QLabel("To:")
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        find_button = QPushButton("Find Forms")
        find_button.clicked.connect(self.find_forms_for_classes)
        date_layout.addWidget(date_from_label)
        date_layout.addWidget(self.date_from)
        date_layout.addWidget(date_to_label)
        date_layout.addWidget(self.date_to)
        date_layout.addWidget(find_button)
        layout.addLayout(date_layout)

        # Form list
        self.form_list = QListWidget()
        self.form_list.setMaximumHeight(120)
        layout.addWidget(self.form_list, 1)
        self.load_forms()

        # Evaluator
        evaluator_layout = QHBoxLayout()
        evaluator_label = QLabel("Evaluator:")
        self.evaluator_combo = QComboBox()
        self.evaluator_combo.addItems([
            "ai_evaluator (Standard evaluation)",
            "ai_evaluator_2 (Alternative evaluation)"
        ])
        self.evaluator_combo.currentTextChanged.connect(self.update_evaluator)
        evaluator_layout.addWidget(evaluator_label)
        evaluator_layout.addWidget(self.evaluator_combo)
        evaluator_layout.addStretch()
        layout.addLayout(evaluator_layout)

        # Report
        report_layout = QHBoxLayout()
        report_label = QLabel("Generate Report:")
        self.report_checkbox = QCheckBox()
        self.report_checkbox.setChecked(True)
        self.report_checkbox.stateChanged.connect(self.update_report_option)
        report_layout.addWidget(report_label)
        report_layout.addWidget(self.report_checkbox)
        layout.addLayout(report_layout)

        # Leniency selector
        leniency_layout = QHBoxLayout()
        leniency_label = QLabel("Leniency:")
        self.leniency_combo = QComboBox()
        # Options should match config.json leniency_note
        self.leniency_combo.addItems(["extreme", "lenient", "balanced", "strict"])
        self.leniency_combo.currentTextChanged.connect(self.update_leniency)
        leniency_layout.addWidget(leniency_label)
        leniency_layout.addWidget(self.leniency_combo)
        layout.addLayout(leniency_layout)

        # Load config
        try:
            with open("config.json", "r") as f:
                config = json.load(f)
            evaluator = config.get("evaluator", "ai_evaluator")
            # Match evaluator to combo text (accounts for descriptive labels)
            for i in range(self.evaluator_combo.count()):
                if evaluator in self.evaluator_combo.itemText(i):
                    self.evaluator_combo.setCurrentIndex(i)
                    break
            self.report_checkbox.setChecked(config.get("generate_report", True))
            # Leniency
            leniency = config.get("leniency", "lenient")
            for i in range(self.leniency_combo.count()):
                if self.leniency_combo.itemText(i) == leniency:
                    self.leniency_combo.setCurrentIndex(i)
                    break
            # Tooltip / note
            note = config.get("leniency_note", "options: extreme, lenient, balanced, strict")
            self.leniency_combo.setToolTip(note)
        except Exception as e:
            self.debug_output.append(f"Failed to load config: {e}")

        # Buttons
        button_layout = QHBoxLayout()
        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self.remove_form)
        self.run_button = QPushButton("Run Grader")
        self.run_button.clicked.connect(self.run_grader)
        button_layout.addWidget(remove_button)
        button_layout.addWidget(self.run_button)
        layout.addLayout(button_layout)

        self.init_service()
        self.load_classes()

    def init_service(self):
        try:
            self.service = get_service()
            self.debug_output.append("Google Forms API ready.")
        except Exception as e:
            QMessageBox.critical(self, "Auth Failed", f"{e}")

    def load_classes(self):
        self.class_loader = ClassLoaderThread()
        self.class_loader.courses_loaded.connect(self.on_classes_loaded)
        self.class_loader.error.connect(self.on_classes_error)
        self.class_loader.start()

    def on_classes_loaded(self, courses):
        self.classes_list.clear()
        seen = set()
        for name, cid in courses:
            if name not in seen:
                seen.add(name)
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, cid)
                self.classes_list.addItem(item)
        self.debug_output.append(f"Loaded {len(seen)} classes from Classroom")

    def on_classes_error(self, err):
        self.debug_output.append(f"Classroom error: {err}")

    def extract_form_id(self, url: str):
        try:
            for part in ["/d/", "/d/e/", "/forms/d/"]:
                if part in url:
                    return url.split(part)[1].split("/")[0]
            return None
        except:
            return None

    def find_forms_for_classes(self):
        selected = self.classes_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "No Class", "Select at least one class first.")
            return

        try:
            classroom = get_classroom_service()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Classroom API failed:\n{e}\n\nRun: python auth_refresh.py")
            return

        found = 0
        existing = set(self.forms_data.keys())

        for item in selected:
            course_id = item.data(Qt.UserRole)
            name = item.text()
            self.debug_output.append(f"Scanning assignments in: {name}")

            try:
                request = classroom.courses().courseWork().list(courseId=course_id, pageSize=100)
                while request:
                    response = request.execute()
                    for assignment in response.get('courseWork', []):
                        for material in assignment.get('materials', []):
                            form = material.get('form')
                            if form and form.get('formUrl'):
                                url = form['formUrl']
                                form_id = self.extract_form_id(url)
                                if form_id:
                                    edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"
                                    title = form.get('title') or assignment.get('title', 'Form')
                                    if edit_url not in existing:
                                        self.forms_data[edit_url] = title
                                        list_item = QListWidgetItem(f"{title} — {edit_url}")
                                        list_item.setData(Qt.UserRole, edit_url)
                                        self.form_list.addItem(list_item)
                                        existing.add(edit_url)
                                        found += 1
                                        self.debug_output.append(f"  Found: {title}")
                    request = classroom.courses().courseWork().list_next(request, response)
            except Exception as e:
                self.debug_output.append(f"Error in {name}: {e}")

        if found:
            self.save_forms()
            self.form_list.sortItems()
            QMessageBox.information(self, "Success", f"Added {found} forms from Classroom!")
        else:
            QMessageBox.information(self, "None Found", "No forms attached to assignments.\n(Try adding forms directly in Classroom)")

    def get_form_title(self, form_id):
        try:
            data = self.service.forms().get(formId=form_id).execute()
            return data.get("info", {}).get("title", "Untitled")
        except:
            return "Untitled"

    def load_forms(self):
        try:
            with open("forms_to_grade.json", "r") as f:
                data = json.load(f)
                for item in data.get("forms", []):
                    if isinstance(item, dict):
                        url = item.get("url")
                        title = item.get("title", url)
                    else:
                        url = title = item
                    if url:
                        self.forms_data[url] = title
                        list_item = QListWidgetItem(f"{title} — {url}" if title != url else title)
                        list_item.setData(Qt.UserRole, url)
                        self.form_list.addItem(list_item)
        except:
            open("forms_to_grade.json", "w").write('{"forms": []}')

    def save_forms(self):
        data = {"forms": [{"url": u, "title": t} for u, t in self.forms_data.items()]}
        with open("forms_to_grade.json", "w") as f:
            json.dump(data, f, indent=2)

    def add_form(self):
        url = self.url_input.text().strip()
        if not url or "docs.google.com/forms" not in url:
            QMessageBox.warning(self, "Invalid", "Enter a valid Google Form URL")
            return
        form_id = self.extract_form_id(url)
        if not form_id:
            QMessageBox.warning(self, "Error", "Cannot extract Form ID")
            return
        edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"
        title = self.get_form_title(form_id)
        self.forms_data[edit_url] = title
        item = QListWidgetItem(f"{title} — {edit_url}")
        item.setData(Qt.UserRole, edit_url)
        self.form_list.addItem(item)
        self.url_input.clear()
        self.save_forms()

    def remove_form(self):
        row = self.form_list.currentRow()
        if row >= 0:
            item = self.form_list.item(row)
            url = item.data(Qt.UserRole)
            if url in self.forms_data:
                del self.forms_data[url]
            self.form_list.takeItem(row)
            self.save_forms()

    def update_evaluator(self, text):
        try:
            # Extract evaluator name (text is "ai_evaluator (description)" or "ai_evaluator_2 (description)")
            evaluator = text.split(" ")[0] if text else "ai_evaluator"
            with open("config.json", "r+") as f:
                c = json.load(f)
                c["evaluator"] = evaluator
                f.seek(0); json.dump(c, f, indent=4); f.truncate()
        except: pass

    def update_leniency(self, text):
        try:
            with open("config.json", "r+") as f:
                c = json.load(f)
                c["leniency"] = text
                f.seek(0); json.dump(c, f, indent=4); f.truncate()
        except: pass

    def update_report_option(self, state):
        try:
            with open("config.json", "r+") as f:
                c = json.load(f)
                c["generate_report"] = bool(state)
                f.seek(0); json.dump(c, f, indent=4); f.truncate()
        except: pass

    def run_grader(self):
        if self.grader_thread and self.grader_thread.isRunning():
            return
        self.run_button.setEnabled(False)
        self.debug_output.clear()
        self.progress_bar.setValue(0)
        self.grader_thread = GraderThread()
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.progress.connect(self.update_progress)
        self.grader_thread.overall_progress.connect(self.update_overall_progress)
        self.grader_thread.debug_message.connect(self.debug_output.append)
        self.grader_thread.current_form.connect(self.current_label.setText)
        self.grader_thread.finished_form.connect(self.update_finished_form)
        self.grader_thread.start()

    def update_progress(self, cur, tot):
        # If there are no items to process, treat as complete
        if not tot:
            pct = 100
            self.progress_bar.setValue(pct)
            self.progress_label.setText("Form: 100% (no responses)")
            return

        pct = int((cur / tot) * 100)
        self.progress_bar.setValue(pct)
        self.progress_label.setText(f"Form: {pct}% ({cur}/{tot} responses)")
        # Do not change in_queue_label here — it's used for overall forms remaining

    def update_finished_form(self, form_id):
        self.finished_forms.append(form_id)
        self.finished_label.setText(f"Finished: {len(self.finished_forms)}")
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            url = item.data(Qt.UserRole)
            if url and self.extract_form_id(url) == form_id:
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setForeground(QBrush(QColor("gray")))
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                if not item.text().startswith("Done "):
                    item.setText("Done " + item.text())
                break

    def update_overall_progress(self, cur, tot):
        # cur = number of forms processed, tot = total forms
        if not tot:
            pct = 100
            self.overall_progress_bar.setValue(pct)
            self.overall_progress_label.setText("Overall: 100% (no forms)")
            self.in_queue_label.setText("In queue: 0")
            return

        pct = int((cur / tot) * 100)
        self.overall_progress_bar.setValue(pct)
        self.overall_progress_label.setText(f"Overall: {pct}% ({cur}/{tot} forms)")
        remaining = max(0, tot - cur)
        self.in_queue_label.setText(f"In queue: {remaining} forms")

    def on_grading_finished(self, success, msg):
        self.run_button.setEnabled(True)
        if success:
            QMessageBox.information(self, "Done", "Grading completed!")
        else:
            QMessageBox.critical(self, "Failed", f"Error:\n{msg}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FormManager()
    window.show()
    sys.exit(app.exec_())
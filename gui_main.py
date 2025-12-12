# gui_main.py (Modified)
# gui_main.py
import sys
import os
import json
import subprocess
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QProgressBar, QTextEdit, QLabel, QComboBox, QCheckBox,
                             QDateEdit, QDialog, QFormLayout, QProgressDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDate
from PyQt5.QtGui import QColor, QBrush, QFont
import datetime

# Import Drive helper and Forms service
from auth import get_service, get_drive_service, get_classroom_service
from form_searcher import find_forms_with_submissions_in_range, load_predefined_folders, save_predefined_folders


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


class SearchThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, folder_identifiers, from_date, to_date):
        super().__init__()
        self.folder_identifiers = folder_identifiers
        self.from_date = from_date
        self.to_date = to_date

    def run(self):
        forms = find_forms_with_submissions_in_range(
            self.folder_identifiers, 
            self.from_date, 
            self.to_date, 
            progress_callback=self.progress.emit
        )
        self.finished.emit(forms)


class AutoAddDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto Add Forms")
        self.setGeometry(200, 200, 600, 400)

        layout = QVBoxLayout(self)

        # Predefined folders
        predefined_layout = QVBoxLayout()
        predefined_label = QLabel("Predefined Folders:")
        predefined_layout.addWidget(predefined_label)
        self.predefined_list = QListWidget()
        predefined_layout.addWidget(self.predefined_list)

        btn_layout = QHBoxLayout()
        add_predefined_btn = QPushButton("Add to Predefined")
        add_predefined_btn.clicked.connect(self.add_to_predefined)
        remove_predefined_btn = QPushButton("Remove Selected")
        remove_predefined_btn.clicked.connect(self.remove_from_predefined)
        btn_layout.addWidget(add_predefined_btn)
        btn_layout.addWidget(remove_predefined_btn)
        predefined_layout.addLayout(btn_layout)

        layout.addLayout(predefined_layout)

        # Temporary folders input
        self.temp_input = QLineEdit()
        self.temp_input.setPlaceholderText("Enter folder names/IDs/URLs separated by commas...")
        layout.addWidget(self.temp_input)

        # Date range
        date_layout = QHBoxLayout()
        from_label = QLabel("From:")
        self.from_date = QDateEdit()
        self.from_date.setDate(QDate.currentDate().addDays(-7))
        to_label = QLabel("To:")
        self.to_date = QDateEdit()
        self.to_date.setDate(QDate.currentDate())
        date_layout.addWidget(from_label)
        date_layout.addWidget(self.from_date)
        date_layout.addWidget(to_label)
        date_layout.addWidget(self.to_date)
        layout.addLayout(date_layout)

        # Search button
        search_btn = QPushButton("Search and Add Forms")
        search_btn.clicked.connect(self.search_and_add)
        layout.addWidget(search_btn)

        self.load_predefined()

    def load_predefined(self):
        self.predefined_list.clear()
        folders = load_predefined_folders()
        for folder in folders:
            self.predefined_list.addItem(folder)

    def add_to_predefined(self):
        text = self.temp_input.text().strip()
        if not text:
            return
        new_folders = [f.strip() for f in text.split(',') if f.strip()]
        existing = [self.predefined_list.item(i).text() for i in range(self.predefined_list.count())]
        folders = existing + [f for f in new_folders if f not in existing]
        save_predefined_folders(folders)
        self.load_predefined()
        self.temp_input.clear()

    def remove_from_predefined(self):
        selected = self.predefined_list.selectedItems()
        if not selected:
            return
        folders = [self.predefined_list.item(i).text() for i in range(self.predefined_list.count()) if not self.predefined_list.item(i).isSelected()]
        save_predefined_folders(folders)
        self.load_predefined()

    def search_and_add(self):
        # Get all folders: predefined + temporary
        predefined = [self.predefined_list.item(i).text() for i in range(self.predefined_list.count())]
        temp_text = self.temp_input.text().strip()
        temp = [f.strip() for f in temp_text.split(',') if f.strip()]
        all_folders = list(set(predefined + temp))

        if not all_folders:
            QMessageBox.warning(self, "No Folders", "Add at least one folder.")
            return

        from_dt = self.from_date.date().toPyDate()
        to_dt = self.to_date.date().toPyDate()

        # Show progress dialog
        self.progress_dialog = QProgressDialog("Initializing search...", "Cancel", 0, 0, self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.show()

        # Start search thread
        self.search_thread = SearchThread(all_folders, from_dt, to_dt)
        self.search_thread.progress.connect(self.progress_dialog.setLabelText)
        self.search_thread.finished.connect(self.on_search_finished)
        self.search_thread.start()

    def on_search_finished(self, forms):
        self.progress_dialog.close()

        if not forms:
            QMessageBox.information(self, "No Forms", "No forms found with submissions in the date range.")
            return

        # Add to parent's form list
        parent = self.parent()
        for form in forms:
            url = form['url']
            title = form['title']
            last_sub = form.get('last_submission')
            last_str = last_sub.strftime("%Y-%m-%d") if last_sub else "None"
            display_text = f"{title} (Last submission: {last_str}) — {url}"
            if url not in parent.forms_data:
                parent.forms_data[url] = title
                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, url)
                parent.form_list.addItem(item)
        parent.save_forms()
        self.accept()


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

        # Form list
        self.form_list = QListWidget()
        self.form_list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.form_list, 3)

        # Controls
        controls_layout = QHBoxLayout()
        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self.remove_form)
        controls_layout.addWidget(remove_button)

        clear_all_button = QPushButton("Clear All")
        clear_all_button.clicked.connect(self.clear_all_forms)
        controls_layout.addWidget(clear_all_button)

        auto_add_button = QPushButton("Auto Find and Add Forms")
        auto_add_button.clicked.connect(self.open_auto_add_dialog)
        controls_layout.addWidget(auto_add_button)

        self.run_button = QPushButton("Run Grader")
        self.run_button.clicked.connect(self.run_grader)
        controls_layout.addWidget(self.run_button)

        # Evaluator selection
        evaluator_label = QLabel("Evaluator:")
        self.evaluator_combo = QComboBox()
        self.evaluator_combo.addItems(["ai_evaluator (Basic)", "ai_evaluator_2 (Advanced)"])
        self.evaluator_combo.currentTextChanged.connect(self.update_evaluator)
        controls_layout.addWidget(evaluator_label)
        controls_layout.addWidget(self.evaluator_combo)

        # Leniency selection
        leniency_label = QLabel("Leniency:")
        self.leniency_combo = QComboBox()
        self.leniency_combo.addItems(["extreme", "lenient", "balanced", "strict"])
        self.leniency_combo.currentTextChanged.connect(self.update_leniency)
        controls_layout.addWidget(leniency_label)
        controls_layout.addWidget(self.leniency_combo)

        # Generate report checkbox
        self.report_checkbox = QCheckBox("Generate Report")
        self.report_checkbox.stateChanged.connect(self.update_report_option)
        controls_layout.addWidget(self.report_checkbox)

        layout.addLayout(controls_layout)

        self.load_forms()
        self.load_config()

    def open_auto_add_dialog(self):
        dialog = AutoAddDialog(self)
        dialog.exec_()

    def load_config(self):
        try:
            with open("config.json") as f:
                c = json.load(f)
                evaluator = c.get("evaluator", "ai_evaluator")
                idx = 0 if evaluator == "ai_evaluator" else 1
                self.evaluator_combo.setCurrentIndex(idx)
                leniency = c.get("leniency", "lenient")
                self.leniency_combo.setCurrentText(leniency)
                generate_report = c.get("generate_report", True)
                self.report_checkbox.setChecked(generate_report)
        except:
            pass

    def load_forms(self):
        self.form_list.clear()
        self.forms_data.clear()
        try:
            with open("forms_to_grade.json") as f:
                data = json.load(f)
            forms_list = data.get("forms", [])
            for item in forms_list:
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

    def remove_form(self):
        selected = self.form_list.selectedItems()
        if not selected:
            return
        for item in selected:
            url = item.data(Qt.UserRole)
            if url in self.forms_data:
                del self.forms_data[url]
            self.form_list.takeItem(self.form_list.row(item))
        self.save_forms()

    def clear_all_forms(self):
        confirm = QMessageBox.question(self, "Clear All", "Are you sure you want to clear all forms?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm == QMessageBox.Yes:
            self.form_list.clear()
            self.forms_data.clear()
            self.save_forms()

    def update_evaluator(self, text):
        try:
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

    def extract_form_id(self, url):
        try:
            if "/d/" in url:
                return url.split("/d/")[1].split("/")[0]
            elif "/d/e/" in url:
                return url.split("/d/e/")[1].split("/")[0]
            return None
        except:
            return None

    def get_form_title(self, form_id):
        try:
            service = get_service()
            form = service.forms().get(formId=form_id).execute()
            return form.get('info', {}).get('title', 'Untitled')
        except:
            return 'Untitled'


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FormManager()
    window.show()
    sys.exit(app.exec_())
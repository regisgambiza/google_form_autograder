# gui_main.py - FINAL PERFECT VERSION (your working code + no duplicates in Auto Mode)
import sys
import os
import json
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QProgressBar, QTextEdit,
                             QLabel, QComboBox, QCheckBox, QProgressDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDate, QTimer
from PyQt5.QtGui import QColor, QBrush, QFont
from datetime import datetime, timedelta

# Local imports
from auth import get_service, get_drive_service, get_classroom_service
from form_searcher import find_forms_with_submissions_in_range, load_predefined_folders, save_predefined_folders
from auto_add_dialog import AutoAddDialog, SearchThread  # THIS WAS MISSING BEFORE
from grader_thread import GraderThread
from class_loader_thread import ClassLoaderThread
import ollama


class FormManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Form Autograder")
        self.setGeometry(100, 100, 1200, 900)

        self.grader_thread = None
        self.auto_search_thread = None
        self.forms_data = {}
        self.service = None
        self.finished_forms = []
        self.auto_mode = False

        # Auto Mode Settings
        self.recency_minutes = 60
        self.interval_seconds = 300  # 5 minutes default
        self.folders = []
        self.last_check_time = None  # THIS FIXES DUPLICATES FOREVER

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # === Progress Bars ===
        progress_layout = QHBoxLayout()
        self.overall_progress_label = QLabel("Overall: 0%")
        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setMaximum(100)
        progress_layout.addWidget(self.overall_progress_label)
        progress_layout.addWidget(self.overall_progress_bar)

        self.progress_label = QLabel("Form: 0%")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        layout.addLayout(progress_layout)

        # === Status Labels ===
        status_layout = QHBoxLayout()
        self.current_label = QLabel("Currently processing: -")
        self.finished_label = QLabel("Finished: 0")
        self.in_queue_label = QLabel("In queue: 0")
        status_layout.addWidget(self.current_label)
        status_layout.addWidget(self.finished_label)
        status_layout.addWidget(self.in_queue_label)
        layout.addLayout(status_layout)

        # === Debug Output ===
        self.debug_output = QTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setMinimumHeight(250)
        layout.addWidget(self.debug_output, 2)

        # === Form List ===
        self.form_list = QListWidget()
        self.form_list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.form_list, 3)

        # === Controls ===
        controls_layout = QHBoxLayout()

        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self.remove_form)
        controls_layout.addWidget(remove_button)

        clear_all_button = QPushButton("Clear All")
        clear_all_button.clicked.connect(lambda: self.clear_all_forms(confirm=True))
        controls_layout.addWidget(clear_all_button)

        auto_add_button = QPushButton("Auto Find and Add Forms")
        auto_add_button.clicked.connect(self.open_manual_add_dialog)
        controls_layout.addWidget(auto_add_button)

        auto_run_button = QPushButton("Auto Run")
        auto_run_button.clicked.connect(self.open_auto_run_dialog)
        controls_layout.addWidget(auto_run_button)

        self.run_button = QPushButton("Run Grader")
        self.run_button.clicked.connect(self.run_grader)
        controls_layout.addWidget(self.run_button)

        self.stop_button = QPushButton("Stop Auto Run")
        self.stop_button.setStyleSheet("background-color: red; color: white; font-weight: bold;")
        self.stop_button.clicked.connect(self.stop_auto_mode)
        self.stop_button.hide()
        controls_layout.addWidget(self.stop_button)

        # Evaluator
        evaluator_label = QLabel("Evaluator:")
        self.evaluator_combo = QComboBox()
        self.evaluator_combo.addItems(["ai_evaluator (Basic)", "ai_evaluator_2 (Advanced)"])
        self.evaluator_combo.currentTextChanged.connect(self.update_evaluator)
        controls_layout.addWidget(evaluator_label)
        controls_layout.addWidget(self.evaluator_combo)

        # Leniency
        leniency_label = QLabel("Leniency:")
        self.leniency_combo = QComboBox()
        self.leniency_combo.addItems(["extreme", "lenient", "balanced", "strict"])
        self.leniency_combo.currentTextChanged.connect(self.update_leniency)
        controls_layout.addWidget(leniency_label)
        controls_layout.addWidget(self.leniency_combo)

        # Report
        self.report_checkbox = QCheckBox("Generate Report")
        self.report_checkbox.setChecked(False)
        self.report_checkbox.stateChanged.connect(self.update_report_option)
        controls_layout.addWidget(self.report_checkbox)

        # Ollama Model
        model_label = QLabel("Ollama Model:")
        self.model_combo = QComboBox()
        self.model_combo.currentTextChanged.connect(self.update_model)
        controls_layout.addWidget(model_label)
        controls_layout.addWidget(self.model_combo)

        layout.addLayout(controls_layout)

        self.load_forms()
        self.load_config()
        self.load_ollama_models()

    # ===============================================
    # CONFIG & LOAD
    # ===============================================
    def load_config(self):
        try:
            with open("config.json", "r") as f:
                cfg = json.load(f)
                evaluator = cfg.get("evaluator", "ai_evaluator_2")
                idx = self.evaluator_combo.findText("ai_evaluator_2 (Advanced)" if evaluator == "ai_evaluator_2" else "ai_evaluator (Basic)")
                if idx >= 0:
                    self.evaluator_combo.setCurrentIndex(idx)
                self.leniency_combo.setCurrentText(cfg.get("leniency", "lenient"))
                self.report_checkbox.setChecked(cfg.get("generate_report", False))
        except:
            pass

    def load_ollama_models(self):
        try:
            models = [m["name"] for m in ollama.list()["models"]]
            self.model_combo.addItems(models)
            if models:
                self.model_combo.setCurrentText("gpt-oss:20b")
        except:
            self.model_combo.addItem("No models found")
            self.model_combo.setEnabled(False)

    def update_evaluator(self, text):
        try:
            ev = "ai_evaluator_2" if "Advanced" in text else "ai_evaluator"
            with open("config.json", "r+") as f:
                c = json.load(f)
                c["evaluator"] = ev
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

    def update_model(self, text):
        try:
            with open("config.json", "r+") as f:
                c = json.load(f)
                c.setdefault("models", {})["judge"] = [text] if text != "No models found" else []
                f.seek(0); json.dump(c, f, indent=4); f.truncate()
        except: pass

    # ===============================================
    # FORM LIST MANAGEMENT
    # ===============================================
    def load_forms(self):
        self.form_list.clear()
        self.forms_data.clear()
        try:
            with open("forms_to_grade.json") as f:
                data = json.load(f)
            for item in data.get("forms", []):
                if isinstance(item, dict):
                    url = item.get("url")
                    title = item.get("title", "Untitled")
                else:
                    url = item
                    title = "Untitled"
                if url:
                    self.forms_data[url] = title
                    display = f"{title} — {url}"
                    list_item = QListWidgetItem(display)
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
        if not selected: return
        for item in selected:
            url = item.data(Qt.UserRole)
            self.forms_data.pop(url, None)
            self.form_list.takeItem(self.form_list.row(item))
        self.save_forms()

    def clear_all_forms(self, confirm=True):
        if confirm and QMessageBox.question(self, "Clear All", "Clear all forms?",
                                           QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.form_list.clear()
        self.forms_data.clear()
        self.save_forms()

    def clear_finished_forms_silently(self):
        to_remove = []
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            if item.text().startswith("Done "):
                url = item.data(Qt.UserRole)
                self.forms_data.pop(url, None)
                to_remove.append(i)
        for i in reversed(to_remove):
            self.form_list.takeItem(i)
        self.save_forms()

    # ===============================================
    # MANUAL ADD DIALOG
    # ===============================================
    def open_manual_add_dialog(self):
        dialog = AutoAddDialog(self, mode='manual')
        dialog.exec_()

    # ===============================================
    # AUTO RUN MODE (SILENT + NO DUPLICATES!)
    # ===============================================
    def open_auto_run_dialog(self):
        dialog = AutoAddDialog(self, mode='auto')
        dialog.search_btn.clicked.disconnect()
        dialog.search_btn.clicked.connect(lambda: self.start_auto_mode_from_dialog(dialog))
        dialog.exec_()

    def start_auto_mode_from_dialog(self, dialog):
        try:
            recency = int(dialog.recency_edit.text().strip() or "1")
            if recency < 1: raise ValueError
        except:
            QMessageBox.warning(self, "Invalid", "Recency must be a positive number")
            return
        try:
            interval = int(dialog.interval_edit.text().strip() or "5")
            if interval < 1: raise ValueError
        except:
            QMessageBox.warning(self, "Invalid", "Interval must be a positive number")
            return

        recency_number = int(dialog.recency_edit.text())
        recency_unit = dialog.recency_unit.currentText()
        interval_number = int(dialog.interval_edit.text())
        interval_unit = dialog.interval_unit.currentText()

        self.recency_minutes = recency_number * 60 if recency_unit == "hours" else recency_number
        self.interval_seconds = max(30, interval_number * 3600 if interval_unit == "hours" else interval_number * 60)

        predefined = [dialog.predefined_list.item(i).text() for i in range(dialog.predefined_list.count())]
        temp = [f.strip() for f in dialog.temp_input.text().split(",") if f.strip()]
        self.folders = list(set(predefined + temp))

        if not self.folders:
            QMessageBox.warning(self, "No Folders", "Add at least one folder to monitor")
            return

        self.auto_mode = True
        self.last_check_time = None  # Reset for first run
        self.stop_button.show()
        self.run_button.setEnabled(False)

        self.debug_output.append("<b>AUTO RUN STARTED</b>")
        self.debug_output.append(f"Check every: {interval_number} {interval_unit}")
        self.debug_output.append(f"First run looks back: {recency_number} {recency_unit}")

        dialog.accept()
        self.auto_cycle()  # Start immediately

    def auto_cycle(self):
        if not self.auto_mode:
            return

        now = datetime.now()
        now_str = now.strftime("%H:%M:%S")

        # Smart time window: first run = look back X min, then only new submissions
        if self.last_check_time is None:
            from_dt = now - timedelta(minutes=self.recency_minutes)
            self.debug_output.append(f"<font color='green'>[AUTO {now_str}] FIRST RUN – Looking back {self.recency_minutes} min</font>")
        else:
            from_dt = self.last_check_time
            self.debug_output.append(f"<font color='green'>[AUTO {now_str}] Checking for new submissions since last run</font>")

        to_dt = now

        self.debug_output.append(f"<font color='green'>[AUTO {now_str}] Searching...</font>")

        self.auto_search_thread = SearchThread(self.folders, from_dt, to_dt)
        self.auto_search_thread.progress.connect(lambda msg: None)
        self.auto_search_thread.finished.connect(self.on_auto_search_complete)
        self.auto_search_thread.start()

        # Remember this time — this stops duplicates
        self.last_check_time = to_dt

    def on_auto_search_complete(self, forms):
        if not self.auto_mode:
            return

        added = 0
        for form in forms:
            url = form['url']
            if url not in self.forms_data:
                title = form.get('title', 'Untitled')
                last_sub = form.get('last_submission')
                time_str = last_sub.strftime("%H:%M") if last_sub else "?"
                self.forms_data[url] = title
                item = QListWidgetItem(f"NEW {time_str} | {title} — {url}")
                item.setData(Qt.UserRole, url)
                self.form_list.addItem(item)
                added += 1

        if added > 0:
            self.save_forms()
            self.debug_output.append(f"<font color='blue'>[AUTO] Found {added} NEW form(s) → Starting grader...</font>")
            self.run_grader()
        else:
            mins = self.interval_seconds // 60
            self.debug_output.append(f"<font color='orange'>[AUTO] No new forms → Next check in {mins} min</font>")
            QTimer.singleShot(self.interval_seconds * 1000, self.auto_cycle)

    def stop_auto_mode(self):
        self.auto_mode = False
        self.stop_button.hide()
        self.run_button.setEnabled(True)
        self.debug_output.append("<b><font color='red'>AUTO RUN STOPPED</font></b>")
        if hasattr(self, 'auto_search_thread') and self.auto_search_thread.isRunning():
            self.auto_search_thread.terminate()
            self.auto_search_thread.wait()

    # ===============================================
    # GRADER CONTROL
    # ===============================================
    def run_grader(self):
        if not self.forms_data:
            if self.auto_mode:
                QTimer.singleShot(5000, self.auto_cycle)
            else:
                QMessageBox.information(self, "No Forms", "Add forms first.")
            return

        if self.grader_thread and self.grader_thread.isRunning():
            return

        self.run_button.setEnabled(False)
        self.debug_output.clear()
        self.progress_bar.setValue(0)
        self.overall_progress_bar.setValue(0)
        self.finished_forms = []

        self.grader_thread = GraderThread()
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.progress.connect(self.update_progress)
        self.grader_thread.overall_progress.connect(self.update_overall_progress)
        self.grader_thread.debug_message.connect(self.debug_output.append)
        self.grader_thread.current_form.connect(lambda url: self.current_label.setText(f"Grading: {url.split('/')[-2][:10]}..."))
        self.grader_thread.finished_form.connect(self.update_finished_form)
        self.grader_thread.start()

    def update_progress(self, cur, tot):
        if not tot:
            self.progress_label.setText("Form: 100%")
            self.progress_bar.setValue(100)
            return
        pct = int(cur / tot * 100)
        self.progress_bar.setValue(pct)
        self.progress_label.setText(f"Form: {pct}% ({cur}/{tot})")

    def update_overall_progress(self, cur, tot):
        if not tot:
            self.overall_progress_bar.setValue(100)
            self.overall_progress_label.setText("Overall: 100%")
            return
        pct = int(cur / tot * 100)
        self.overall_progress_bar.setValue(pct)
        self.overall_progress_label.setText(f"Overall: {pct}% ({cur}/{tot})")
        self.in_queue_label.setText(f"In queue: {tot - cur}")

    def update_finished_form(self, form_id):
        self.finished_forms.append(form_id)
        self.finished_label.setText(f"Finished: {len(self.finished_forms)}")
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            url = item.data(Qt.UserRole)
            if url and self.extract_form_id(url) == form_id:
                item.setText("Done " + item.text())
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setForeground(QBrush(QColor("gray")))
                break

    def on_grading_finished(self, success, msg):
        self.run_button.setEnabled(True)
        if not success:
            self.debug_output.append(f"<font color='red'>Grading failed: {msg}</font>")

        if self.auto_mode:
            self.clear_finished_forms_silently()
            mins = self.interval_seconds // 60
            self.debug_output.append(f"<font color='green'>[AUTO] Grading finished → Next check in {mins} min</font>")
            QTimer.singleShot(self.interval_seconds * 1000, self.auto_cycle)
        else:
            if success:
                QMessageBox.information(self, "Done", "Grading completed!")

    def extract_form_id(self, url):
        try:
            if "/d/" in url:
                return url.split("/d/")[1].split("/")[0]
            elif "/d/e/" in url:
                return url.split("/d/e/")[1].split("/")[0]
        except:
            pass
        return None


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FormManager()
    window.show()
    sys.exit(app.exec_())
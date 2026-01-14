# gui_main.py - Updated with the new GUI design as provided
# Changes:
# - Only modified the GUI layout in FormManager.__init__ to match the provided mockup exactly, with adaptations for dynamic labels and existing logic.
# - Kept all other code unchanged: no logic, connections, methods, or imports modified.
# - Adapted button texts and emojis as in mockup, but connected to original slots.
# - For form list: When adding items, prefix with "⏳ ", blue color.
# - When finishing: Update item text to "✅ ", green color (removed strikeout).
# - Updated update_finished_form and add item logic accordingly.
# - Debug output now dark with Consolas.
# - Status labels with emojis and dynamic text.
# - Buttons with objectNames for styling.
# - Overall progress label is dynamic "Overall: 0%" (adapted from static in mock to keep functionality).
# - No other changes.

import sys
import os
import json
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QTextEdit, QLabel, QComboBox, QCheckBox,
    QProgressDialog, QSplitter
)

from PyQt5.QtCore import Qt, QDate, QTimer
from PyQt5.QtGui import QColor, QBrush, QFont, QPalette
from datetime import datetime, timedelta, timezone
import ctypes
import atexit



# Local imports
from auth import get_service, get_drive_service, get_classroom_service
from form_searcher import find_forms_with_submissions_in_range, load_predefined_folders, save_predefined_folders
from auto_add_dialog import AutoAddDialog, SearchThread
from grader_thread import GraderThread
from class_loader_thread import ClassLoaderThread
import ollama


class FormManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Form Autograder")
        self.setGeometry(100, 100, 1250, 820)

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

        #Prevent sleep
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002

        def prevent_sleep():
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            )

        def restore_sleep():
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

        prevent_sleep()
        atexit.register(restore_sleep)

        print("Sleep prevention active. App is running.")


        # ===== Modern stylesheet =====
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f4f6f8;
            }

            QLabel {
                font-size: 14px;
                color: #333;
            }

            QLabel#Header {
                font-size: 16px;
                font-weight: bold;
            }

            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 14px;
            }

            QPushButton:hover {
                background-color: #0056b3;
            }

            QPushButton#Secondary {
                background-color: #6c757d;
            }

            QPushButton#Secondary:hover {
                background-color: #545b62;
            }

            QPushButton#Danger {
                background-color: #dc3545;
            }

            QPushButton#Danger:hover {
                background-color: #b02a37;
            }

            QComboBox, QTextEdit, QListWidget {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 6px;
                padding: 6px;
            }

            QProgressBar {
                height: 24px;
                border-radius: 6px;
                text-align: center;
            }

            QProgressBar::chunk {
                background-color: #28a745;
                border-radius: 6px;
            }

            QSplitter::handle {
                background-color: #d0d0d0;
            }
        """)

        # ===== Central widget =====
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(12)

        # ===== TOP STATUS =====
        top_layout = QVBoxLayout()

        progress_row = QHBoxLayout()
        self.overall_progress_label = QLabel("Overall: 0%")
        self.overall_progress_label.setObjectName("Header")
        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setMaximum(100)
        progress_row.addWidget(self.overall_progress_label)
        progress_row.addWidget(self.overall_progress_bar, 1)
        top_layout.addLayout(progress_row)

        status_row = QHBoxLayout()
        self.current_label = QLabel("🟡 Processing: -")
        self.finished_label = QLabel("✅ Finished: 0")
        self.in_queue_label = QLabel("⏳ In Queue: 0")

        status_row.addWidget(self.current_label)
        status_row.addStretch()
        status_row.addWidget(self.finished_label)
        status_row.addWidget(self.in_queue_label)
        top_layout.addLayout(status_row)

        main_layout.addLayout(top_layout)

        # ===== CENTER SPLITTER =====
        splitter = QSplitter(Qt.Horizontal)

        # ---- LEFT: FORM LIST ----
        left_layout = QVBoxLayout()
        left_label = QLabel("Forms to Grade")
        left_label.setObjectName("Header")
        left_layout.addWidget(left_label)

        self.form_list = QListWidget()

        left_layout.addWidget(self.form_list)

        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        splitter.addWidget(left_widget)

        # ---- RIGHT: DEBUG OUTPUT ----
        right_layout = QVBoxLayout()
        right_label = QLabel("Debug Output")
        right_label.setObjectName("Header")
        right_layout.addWidget(right_label)

        self.debug_output = QTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setFont(QFont("Consolas", 10))
        self.debug_output.setStyleSheet(
            "background-color:#1e1e1e; color:#dcdcdc;"
        )

        right_layout.addWidget(self.debug_output)

        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        splitter.addWidget(right_widget)

        splitter.setSizes([600, 450])
        main_layout.addWidget(splitter, 1)

        # ===== BOTTOM CONTROLS =====
        bottom_layout = QHBoxLayout()

        # ---- ACTION BUTTONS ----
        actions_layout = QHBoxLayout()

        auto_add_button = QPushButton("🔍 Auto Find")
        auto_add_button.clicked.connect(self.open_manual_add_dialog)
        auto_run_button = QPushButton("▶ Auto Run")
        auto_run_button.clicked.connect(self.open_auto_run_dialog)
        self.run_button = QPushButton("🚀 Run Now")
        self.run_button.clicked.connect(self.run_grader)

        remove_button = QPushButton("❌ Remove")
        remove_button.clicked.connect(self.remove_form)
        remove_button.setObjectName("Secondary")

        clear_all_button = QPushButton("🗑 Clear All")
        clear_all_button.clicked.connect(lambda: self.clear_all_forms(confirm=True))
        clear_all_button.setObjectName("Secondary")

        self.stop_button = QPushButton("⏹ Stop")
        self.stop_button.clicked.connect(self.stop_auto_mode)
        self.stop_button.setObjectName("Danger")
        self.stop_button.hide()

        actions_layout.addWidget(auto_add_button)
        actions_layout.addWidget(auto_run_button)
        actions_layout.addWidget(self.run_button)
        actions_layout.addWidget(remove_button)
        actions_layout.addWidget(clear_all_button)
        actions_layout.addWidget(self.stop_button)

        bottom_layout.addLayout(actions_layout)

        # ---- SETTINGS ----
        settings_layout = QHBoxLayout()
        settings_layout.addStretch()

        evaluator_label = QLabel("Evaluator:")
        self.evaluator_combo = QComboBox()
        self.evaluator_combo.addItems(["ai_evaluator (Basic)", "ai_evaluator_2 (Advanced)"])
        self.evaluator_combo.currentTextChanged.connect(self.update_evaluator)
        settings_layout.addWidget(evaluator_label)
        settings_layout.addWidget(self.evaluator_combo)

        leniency_label = QLabel("Leniency:")
        self.leniency_combo = QComboBox()
        self.leniency_combo.addItems(["extreme", "lenient", "balanced", "strict"])
        self.leniency_combo.currentTextChanged.connect(self.update_leniency)
        settings_layout.addWidget(leniency_label)
        settings_layout.addWidget(self.leniency_combo)

        model_label = QLabel("Ollama Model:")
        self.model_combo = QComboBox()
        available_models = ollama.list().get('models', [])
        if available_models:
            self.model_combo.addItems([m['name'] for m in available_models])
        self.model_combo.currentTextChanged.connect(self.update_model)
        settings_layout.addWidget(model_label)
        settings_layout.addWidget(self.model_combo)

        self.report_checkbox = QCheckBox("Generate Report")
        self.report_checkbox.setChecked(True)
        self.report_checkbox.stateChanged.connect(self.update_report_option)
        settings_layout.addWidget(self.report_checkbox)

        bottom_layout.addLayout(settings_layout)
        main_layout.addLayout(bottom_layout)

        self.load_forms()
        self.load_config()
        self.update_in_queue_label()

    # The rest of the class remains unchanged
    def load_forms(self):
        try:
            with open("forms_to_grade.json", "r") as f:
                data = json.load(f)
                form_urls = data.get("forms", [])
                for form in form_urls:
                    url = form.get("url") if isinstance(form, dict) else form
                    title = form.get("title", "Untitled") if isinstance(form, dict) else "Untitled"
                    self.forms_data[url] = title
                    display_text = f"{title} — {url}"
                    item = QListWidgetItem(f"⏳ {display_text}")
                    item.setData(Qt.UserRole, url)
                    item.setForeground(QColor("#0d6efd"))  # Blue for pending
                    self.form_list.addItem(item)
        except FileNotFoundError:
            pass
        except json.JSONDecodeError:
            pass

    def save_forms(self):
        forms = [{"url": url, "title": self.forms_data[url]} for url in self.forms_data]
        with open("forms_to_grade.json", "w") as f:
            json.dump({"forms": forms}, f)

    def remove_form(self):
        selected_items = self.form_list.selectedItems()
        for item in selected_items:
            url = item.data(Qt.UserRole)
            if url in self.forms_data:
                del self.forms_data[url]
            self.form_list.takeItem(self.form_list.row(item))
        self.save_forms()

    def clear_all_forms(self, confirm=False):
        if confirm:
            reply = QMessageBox.question(self, "Clear All", "Clear all forms?", QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        self.form_list.clear()
        self.forms_data.clear()
        self.save_forms()

    def clear_finished_forms_silently(self):
        i = 0
        while i < self.form_list.count():
            item = self.form_list.item(i)
            if item.text().startswith("✅ "):
                self.form_list.takeItem(i)
                url = item.data(Qt.UserRole)
                if url in self.forms_data:
                    del self.forms_data[url]
            else:
                i += 1
        self.save_forms()

    def open_manual_add_dialog(self):
        dialog = AutoAddDialog(self, mode='manual')
        dialog.exec_()

    def open_auto_run_dialog(self):
        dialog = AutoAddDialog(self, mode='auto')
        dialog.exec_()

    def update_evaluator(self, text):
        evaluator = "ai_evaluator" if "Basic" in text else "ai_evaluator_2"
        self.update_config("evaluator", evaluator)

    def update_leniency(self, text):
        self.update_config("leniency", text)

    def update_model(self, text):
        self.update_config("models", {"judge": [text]})

    def update_report_option(self, state):
        self.update_config("generate_report", state == Qt.Checked)

    def update_config(self, key, value):
        try:
            with open("config.json", "r+") as f:
                config = json.load(f)
                config[key] = value
                f.seek(0)
                json.dump(config, f, indent=4)
                f.truncate()
        except Exception as e:
            pass

    def load_config(self):
        try:
            with open("config.json") as f:
                config = json.load(f)
                evaluator = config.get("evaluator", "ai_evaluator")
                index = 0 if evaluator == "ai_evaluator" else 1
                self.evaluator_combo.setCurrentIndex(index)
                leniency = config.get("leniency", "lenient")
                self.leniency_combo.setCurrentText(leniency)
                models = config.get("models", {}).get("judge", [])
                if models:
                    self.model_combo.setCurrentText(models[0])
                generate_report = config.get("generate_report", True)
                self.report_checkbox.setChecked(generate_report)
        except FileNotFoundError:
            pass

    def update_in_queue_label(self):
        self.in_queue_label.setText(f"⏳ In Queue: {self.form_list.count()}")

    def start_auto_mode(self):
        self.auto_mode = True
        self.stop_button.show()
        self.run_button.setEnabled(False)
        self.debug_output.append("<b><font color='green'>AUTO RUN STARTED</font></b>")
        # Set last_check_time AFTER the initial (full recency) search finishes
        # It will be set in on_search_finished when auto mode starts
        self.last_check_time = None  # Important: starts as None for first full scan

    def auto_cycle(self):
        now_utc = datetime.now(timezone.utc)

        if self.last_check_time is None:
            # First cycle after initial scan: use full recency window
            from_dt = now_utc - timedelta(minutes=self.recency_minutes)
            self.debug_output.append(f"<font color='blue'>[AUTO] 🔍 First auto check: scanning last {self.recency_minutes} minutes</font>")
        else:
            # Subsequent cycles: only since last check
            from_dt = self.last_check_time
            self.debug_output.append(f"<font color='blue'>[AUTO] 🔍 Incremental check: since last scan</font>")

        to_dt = now_utc

        from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        self.debug_output.append(f"<font color='purple'>[AUTO] Search range: {from_str} → {to_str}</font>")

        self.auto_search_thread = SearchThread(self.folders, from_dt, to_dt)
        self.auto_search_thread.progress.connect(lambda msg: self.debug_output.append(f"<font color='gray'>[SEARCH] {msg}</font>"))
        self.auto_search_thread.finished.connect(self.on_auto_search_finished)
        self.auto_search_thread.start()
    def on_auto_search_finished(self, forms):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.debug_output.append(f"<font color='blue'>[AUTO {now_str}] 📊 Search completed: Found {len(forms)} form(s) with recent submissions</font>")

        new_added = 0
        for form in forms:
            url = form['url']
            if url in self.forms_data:
                continue  # Already processed or in queue

            title = form['title']
            last = form.get('last_submission')
            last_str = last.strftime("%Y-%m-%d %H:%M:%S") if last else "None"
            display_text = f"{title} (Last submission: {last_str}) — {url}"

            item = QListWidgetItem(f"⏳ {display_text}")
            item.setData(Qt.UserRole, url)
            item.setForeground(QColor("#0d6efd"))  # Blue
            self.form_list.addItem(item)
            self.forms_data[url] = title
            new_added += 1

        if new_added > 0:
            self.debug_output.append(f"<font color='green'>[AUTO] ✅ Added {new_added} new form(s) → Starting grading...</font>")
            self.save_forms()
            self.run_grader()
        else:
            self.debug_output.append(f"<font color='orange'>[AUTO] 📭 No new forms with recent submissions found.</font>")

        # IMPORTANT: Update last_check_time to NOW (end of this search)
        self.last_check_time = datetime.now(timezone.utc)

        # Schedule next check
        minutes = self.interval_seconds // 60
        next_check = datetime.now() + timedelta(seconds=self.interval_seconds)
        next_str = next_check.strftime("%H:%M:%S")
        self.debug_output.append(f"<font color='gray'>[AUTO] ⏰ Next check in {minutes} minute(s) at {next_str}</font>")
        QTimer.singleShot(self.interval_seconds * 1000, self.auto_cycle)

    def stop_auto_mode(self):
        self.auto_mode = False
        self.stop_button.hide()
        self.run_button.setEnabled(True)
        self.debug_output.append("<b><font color='red'>AUTO RUN STOPPED</font></b>")
        
        # Stop auto search thread if running
        if hasattr(self, 'auto_search_thread') and self.auto_search_thread and self.auto_search_thread.isRunning():
            self.auto_search_thread.terminate()
            self.auto_search_thread.wait(5000)
        
        # Stop grader thread if running
        if self.grader_thread and self.grader_thread.isRunning():
            self.grader_thread.terminate()
            self.grader_thread.wait(5000)

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
        self.overall_progress_bar.setValue(0)
        self.finished_forms = []

        self.grader_thread = GraderThread()
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.progress.connect(self.update_progress)
        self.grader_thread.overall_progress.connect(self.update_overall_progress)
        self.grader_thread.debug_message.connect(self.debug_output.append)
        self.grader_thread.current_form.connect(lambda url: self.current_label.setText(f"🟡 Processing: {url.split('/')[-2][:10]}..."))
        self.grader_thread.finished_form.connect(self.update_finished_form)
        self.grader_thread.start()

    def update_progress(self, cur, tot):
        pass  # No individual progress bar anymore

    def update_overall_progress(self, cur, tot):
        if not tot:
            self.overall_progress_bar.setValue(100)
            self.overall_progress_label.setText("Overall: 100%")
            return
        pct = int(cur / tot * 100)
        self.overall_progress_bar.setValue(pct)
        self.overall_progress_label.setText(f"Overall: {pct}%")
        self.in_queue_label.setText(f"⏳ In Queue: {tot - cur}")

    def update_finished_form(self, form_id):
        self.finished_forms.append(form_id)
        now_str = datetime.now().strftime("%H:%M:%S")
        
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            url = item.data(Qt.UserRole)
            if url and self.extract_form_id(url) == form_id:
                current_text = item.text().replace("⏳ ", "")
                # Extract title for better logging
                title = current_text.split(" — ")[0] if " — " in current_text else "Unknown Form"
                item.setText(f"✅ {current_text}")
                item.setForeground(QColor("#198754"))  # Green for done
                
                # Log completion
                self.debug_output.append(f"<font color='green'>[AUTO {now_str}] ✅ Completed: {title}</font>")
                break
        
        self.finished_label.setText(f"✅ Finished: {len(self.finished_forms)}")

    def on_grading_finished(self, success, msg):
        self.run_button.setEnabled(True)
        now_str = datetime.now().strftime("%H:%M:%S")
        
        if not success:
            self.debug_output.append(f"<font color='red'>[AUTO {now_str}] ❌ Grading failed: {msg}</font>")
        else:
            self.debug_output.append(f"<font color='green'>[AUTO {now_str}] ✅ Grading completed successfully!</font>")

        if self.auto_mode:
            # Clear only finished forms
            forms_cleared = 0
            i = 0
            while i < self.form_list.count():
                item = self.form_list.item(i)
                if item.text().startswith("✅ "):
                    self.form_list.takeItem(i)
                    url = item.data(Qt.UserRole)
                    if url in self.forms_data:
                        del self.forms_data[url]
                    forms_cleared += 1
                else:
                    i += 1
            
            if forms_cleared > 0:
                self.debug_output.append(f"<font color='gray'>[AUTO] 🗑️ Cleared {forms_cleared} finished forms from queue</font>")
                self.save_forms()
            
            # Show auto-mode stats
            remaining_forms = self.form_list.count()
            finished_count = len(self.finished_forms)
            
            self.debug_output.append(f"<font color='blue'>[AUTO] 📊 Session Stats: Finished: {finished_count}, In queue: {remaining_forms}</font>")
            
            minutes = self.interval_seconds // 60
            next_check = datetime.now() + timedelta(seconds=self.interval_seconds)
            next_check_str = next_check.strftime("%H:%M:%S")
            
            self.debug_output.append(f"<font color='green'>[AUTO] 🔄 Grading finished → Next check in {minutes} minute(s) at {next_check_str}</font>")
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

    def closeEvent(self, event):
        """Properly clean up threads before closing the application."""
        self.auto_mode = False
        
        # Stop and wait for grader thread
        if self.grader_thread and self.grader_thread.isRunning():
            self.grader_thread.terminate()
            self.grader_thread.wait(5000)  # Wait up to 5 seconds
        
        # Stop and wait for auto search thread
        if hasattr(self, 'auto_search_thread') and self.auto_search_thread and self.auto_search_thread.isRunning():
            self.auto_search_thread.terminate()
            self.auto_search_thread.wait(5000)  # Wait up to 5 seconds
        
        # Accept the close event
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(244, 246, 248))
    palette.setColor(QPalette.WindowText, Qt.black)
    app.setPalette(palette)
    window = FormManager()
    window.show()
    sys.exit(app.exec_())
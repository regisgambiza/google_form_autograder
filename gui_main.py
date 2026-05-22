# gui_main.py - FIXED: Thread safety, duplicate prevention, proper cleanup
import sys
import os
import json
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QTextEdit, QLabel, QComboBox, QCheckBox,
    QProgressDialog, QSplitter, QSpinBox
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

BANGKOK_TZ = timezone(timedelta(hours=7))

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
        self.auto_timer = None  # Track the QTimer for auto-cycle
        self.debug_lines = []

        # Auto Mode Settings
        self.recency_minutes = 60
        self.interval_seconds = 300
        self.folders = []
        self.last_check_time = None
        
        # Thread safety flags
        self.is_searching = False
        self.is_grading = False
        self.is_closing = False

        # Prevent sleep
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

        # Modern stylesheet
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

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(12)

        # TOP STATUS
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

        # CENTER SPLITTER
        splitter = QSplitter(Qt.Horizontal)

        # LEFT: FORM LIST
        left_layout = QVBoxLayout()
        left_label = QLabel("Forms to Grade")
        left_label.setObjectName("Header")
        left_layout.addWidget(left_label)

        self.form_list = QListWidget()
        left_layout.addWidget(self.form_list)

        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        splitter.addWidget(left_widget)

        # RIGHT: DEBUG OUTPUT
        right_layout = QVBoxLayout()
        right_label = QLabel("Debug Output")
        right_label.setObjectName("Header")
        right_layout.addWidget(right_label)

        timing_filter_row = QHBoxLayout()
        timing_filter_label = QLabel("Filter:")
        self.timing_only_checkbox = QCheckBox("Timing Only")
        self.timing_only_checkbox.stateChanged.connect(self.on_timing_filter_changed)
        timing_filter_row.addWidget(timing_filter_label)
        timing_filter_row.addWidget(self.timing_only_checkbox)
        timing_filter_row.addStretch()
        right_layout.addLayout(timing_filter_row)

        self.debug_output = QTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setFont(QFont("Consolas", 10))
        self.debug_output.setStyleSheet("background-color:#1e1e1e; color:#dcdcdc;")

        right_layout.addWidget(self.debug_output)

        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        splitter.addWidget(right_widget)

        splitter.setSizes([600, 450])
        main_layout.addWidget(splitter, 1)

        # BOTTOM CONTROLS
        bottom_layout = QHBoxLayout()

        # ACTION BUTTONS
        actions_layout = QHBoxLayout()

        auto_add_button = QPushButton("🔍 Auto Find")
        auto_add_button.clicked.connect(self.open_manual_add_dialog)
        auto_run_button = QPushButton("▶ Auto Run")
        auto_run_button.clicked.connect(self.open_auto_run_dialog)
        grade_now_button = QPushButton("⚡ Grade Folder/URL")
        grade_now_button.clicked.connect(self.open_quick_grade_dialog)
        grade_all_button = QPushButton("📚 Grade All Folders")
        grade_all_button.clicked.connect(self.grade_all_forms_in_all_folders)
        self.run_button = QPushButton("🚀 Run Now")
        self.run_button.clicked.connect(self.run_grader)

        remove_button = QPushButton("❌ Remove")
        remove_button.clicked.connect(self.remove_form)
        remove_button.setObjectName("Secondary")

        clear_all_button = QPushButton("🗑️ Clear All")
        clear_all_button.clicked.connect(lambda: self.clear_all_forms(confirm=True))
        clear_all_button.setObjectName("Secondary")

        self.stop_button = QPushButton("⏹ Stop")
        self.stop_button.clicked.connect(self.stop_auto_mode)
        self.stop_button.setObjectName("Danger")
        self.stop_button.hide()

        actions_layout.addWidget(auto_add_button)
        actions_layout.addWidget(auto_run_button)
        actions_layout.addWidget(grade_now_button)
        actions_layout.addWidget(grade_all_button)
        actions_layout.addWidget(self.run_button)
        actions_layout.addWidget(remove_button)
        actions_layout.addWidget(clear_all_button)
        actions_layout.addWidget(self.stop_button)

        bottom_layout.addLayout(actions_layout)

        # SETTINGS
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

        batch_label = QLabel("Batch Size:")
        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(1, 200)
        self.batch_size_spin.setSingleStep(1)
        self.batch_size_spin.valueChanged.connect(self.update_batch_size)
        self.batch_auto_checkbox = QCheckBox("Auto")
        self.batch_auto_checkbox.stateChanged.connect(self.update_batch_auto)
        settings_layout.addWidget(batch_label)
        settings_layout.addWidget(self.batch_size_spin)
        settings_layout.addWidget(self.batch_auto_checkbox)

        grading_mode_label = QLabel("Grade Mode:")
        self.grading_mode_combo = QComboBox()
        self.grading_mode_combo.addItems(["Whole Form", "Recent Only"])
        self.grading_mode_combo.setToolTip("Whole Form: Grade all submissions\nRecent Only: Grade only new submissions since last check")
        settings_layout.addWidget(grading_mode_label)
        settings_layout.addWidget(self.grading_mode_combo)

        bottom_layout.addLayout(settings_layout)
        main_layout.addLayout(bottom_layout)

        self.load_forms()
        self.load_config()
        self.update_in_queue_label()

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
                    item.setForeground(QColor("#0d6efd"))
                    self.form_list.addItem(item)
        except (FileNotFoundError, json.JSONDecodeError):
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

    def open_quick_grade_dialog(self):
        """Open a dialog to add a folder/form URL and grade immediately without checking submissions"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Grade Folder/Form Now")
        dialog.setGeometry(100, 100, 500, 150)
        
        layout = QVBoxLayout()
        
        label = QLabel("Enter a Google Drive folder URL or Google Form URL to grade immediately:")
        layout.addWidget(label)
        
        input_field = QLineEdit()
        input_field.setPlaceholderText("Paste folder URL or form URL here...")
        layout.addWidget(input_field)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("Grade")
        cancel_button = QPushButton("Cancel")
        
        ok_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
        
        dialog.setLayout(layout)
        
        if dialog.exec_() == QDialog.Accepted:
            url = input_field.text().strip()
            if not url:
                QMessageBox.warning(self, "Empty Input", "Please enter a URL")
                return
            
            # Grade immediately without checking last submissions
            self.grade_url_immediately(url)

    def update_evaluator(self, text):
        evaluator = "ai_evaluator" if "Basic" in text else "ai_evaluator_2"
        self.update_config("evaluator", evaluator)

    def update_leniency(self, text):
        self.update_config("leniency", text)

    def update_model(self, text):
        self.update_config("models", {"judge": [text]})

    def update_report_option(self, state):
        self.update_config("generate_report", state == Qt.Checked)

    def update_batch_size(self, value):
        if not self.batch_auto_checkbox.isChecked():
            self.update_config("batch_size", int(value))

    def update_batch_auto(self, state):
        is_auto = state == Qt.Checked
        self.batch_size_spin.setEnabled(not is_auto)
        if is_auto:
            self.update_config("batch_size", "auto")
        else:
            self.update_config("batch_size", int(self.batch_size_spin.value()))

    def update_config(self, key, value):
        try:
            with open("config.json", "r+") as f:
                config = json.load(f)
                config[key] = value
                f.seek(0)
                json.dump(config, f, indent=4)
                f.truncate()
        except Exception:
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
                batch_size = config.get("batch_size", 32)
                if isinstance(batch_size, str) and batch_size.lower() == "auto":
                    self.batch_auto_checkbox.setChecked(True)
                    self.batch_size_spin.setEnabled(False)
                    self.batch_size_spin.setValue(32)
                else:
                    if not isinstance(batch_size, int) or batch_size <= 0:
                        batch_size = 32
                    self.batch_auto_checkbox.setChecked(False)
                    self.batch_size_spin.setEnabled(True)
                    self.batch_size_spin.setValue(batch_size)
        except FileNotFoundError:
            self.batch_size_spin.setValue(32)
            self.update_config("batch_size", 32)

    def grade_url_immediately(self, url):
        """Grade a folder or form URL immediately without checking last submissions"""
        try:
            from datetime import datetime, timezone, timedelta
            
            # Check if it's a direct form URL or a folder URL
            if '/forms/d/' in url:
                # Direct form URL - extract form ID
                form_id = url.split('/forms/d/')[1].split('/')[0]
                form_url = f"https://docs.google.com/forms/d/{form_id}/edit"
                
                # Add directly without searching
                if form_url not in self.forms_data:
                    self.forms_data[form_url] = "Form"
                    display_text = f"Form — {form_url}"
                    item = QListWidgetItem(f"⏳ {display_text}")
                    item.setData(Qt.UserRole, form_url)
                    item.setForeground(QColor("#0d6efd"))
                    self.form_list.addItem(item)
                
                self.append_debug(f"✅ Added form: {form_id}")
                
            else:
                # Folder URL - search for forms
                from_dt = datetime.now(timezone.utc) - timedelta(days=365)
                to_dt = datetime.now(timezone.utc) + timedelta(days=1)
                
                self.append_debug(f"🔍 Searching folder: {url}")
                
                # Extract folder IDs or form IDs from the URL
                folder_ids = find_forms_with_submissions_in_range(
                    [url],  # Single folder/form
                    from_dt=from_dt,
                    to_dt=to_dt,
                    progress_callback=lambda msg: self.append_debug(msg)
                )
                
                if not folder_ids:
                    QMessageBox.warning(self, "No Forms Found", "Could not find any accessible forms at that URL")
                    return
                
                # Add all found forms to the grading queue
                for form_data in folder_ids:
                    form_url = form_data.get("url")
                    form_title = form_data.get("title", "Untitled")
                    
                    if form_url not in self.forms_data:
                        self.forms_data[form_url] = form_title
                        display_text = f"{form_title} — {form_url}"
                        item = QListWidgetItem(f"⏳ {display_text}")
                        item.setData(Qt.UserRole, form_url)
                        item.setForeground(QColor("#0d6efd"))
                        self.form_list.addItem(item)
                
                self.append_debug(f"✅ Found {len(folder_ids)} forms in folder")
            
            self.update_in_queue_label()
            self.save_forms()
            
            # Set to "Whole Form" mode for immediate grading
            self.grading_mode_combo.setCurrentText("Whole Form")
            
            # Start grading immediately
            self.run_grader()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to process URL: {str(e)}")
            self.append_debug(f"❌ Error: {str(e)}")

    def grade_all_forms_in_all_folders(self):
        """Find and grade all forms from all predefined folders, ignoring date windows."""
        try:
            folders = load_predefined_folders()
            if not folders:
                QMessageBox.warning(
                    self,
                    "No Predefined Folders",
                    "Add folders in Auto Find first, then use Grade All Folders.",
                )
                return

            self.append_debug(f"📚 Grade All: Searching all forms in {len(folders)} folder(s)")
            from_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
            to_dt = datetime.now(timezone.utc) + timedelta(days=1)

            forms = find_forms_with_submissions_in_range(
                folders,
                from_dt=from_dt,
                to_dt=to_dt,
                progress_callback=lambda msg: self.append_debug(f"[GRADE ALL] {msg}"),
            )

            if not forms:
                QMessageBox.information(
                    self,
                    "No Forms Found",
                    "No accessible forms with responses were found in your predefined folders.",
                )
                return

            new_added = 0
            for form in forms:
                form_url = form.get("url")
                form_title = form.get("title", "Untitled")
                if form_url and form_url not in self.forms_data:
                    self.forms_data[form_url] = form_title
                    display_text = f"{form_title} — {form_url}"
                    item = QListWidgetItem(f"⏳ {display_text}")
                    item.setData(Qt.UserRole, form_url)
                    item.setForeground(QColor("#0d6efd"))
                    self.form_list.addItem(item)
                    new_added += 1

            self.save_forms()
            self.update_in_queue_label()
            self.append_debug(
                f"✅ Grade All: Found {len(forms)} form(s), added {new_added} new form(s) to queue"
            )

            self.grading_mode_combo.setCurrentText("Whole Form")
            self.run_grader()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Grade All failed: {str(e)}")
            self.append_debug(f"❌ Grade All failed: {str(e)}")

    def update_in_queue_label(self):
        self.in_queue_label.setText(f"⏳ In Queue: {self.form_list.count()}")

    def start_auto_mode(self):
        """Start auto mode - only call this once from dialog"""
        if self.auto_mode:
            self.append_debug("<font color='orange'>[AUTO] ⚠️ Auto mode already running</font>")
            return
            
        self.auto_mode = True
        self.stop_button.show()
        self.run_button.setEnabled(False)
        self.append_debug("<b><font color='green'>AUTO RUN STARTED</font></b>")
        self.last_check_time = None
        
        # DON'T schedule auto_cycle here - let the dialog handle the first search
        # The dialog will call run_grader() after the initial search completes

    def auto_cycle(self):
        """Perform one auto-cycle: search for new forms, add them, and grade"""
        if not self.auto_mode or self.is_closing:
            return
            
        # Prevent overlapping searches
        if self.is_searching:
            self.append_debug("<font color='orange'>[AUTO] ⚠️ Search already in progress, skipping cycle</font>")
            self.schedule_next_cycle()
            return

        now_utc = datetime.now(timezone.utc)

        if self.last_check_time is None:
            from_dt = now_utc - timedelta(minutes=self.recency_minutes)
            self.append_debug(f"<font color='blue'>[AUTO] 🔍 First auto check: scanning last {self.recency_minutes} minutes</font>")
        else:
            from_dt = self.last_check_time
            self.append_debug(f"<font color='blue'>[AUTO] 🔍 Incremental check: since last scan</font>")

        to_dt = now_utc
        from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        self.append_debug(f"<font color='purple'>[AUTO] Search range: {from_str} → {to_str}</font>")

        self.is_searching = True
        self.auto_search_thread = SearchThread(self.folders, from_dt, to_dt)
        self.auto_search_thread.progress.connect(lambda msg: self.append_debug(f"<font color='gray'>[SEARCH] {msg}</font>"))
        self.auto_search_thread.finished.connect(self.on_auto_search_finished)
        self.auto_search_thread.start()

    def on_auto_search_finished(self, forms):
        """Handle completion of auto-search"""
        self.is_searching = False
        
        if self.is_closing:
            return
            
        now_str = datetime.now().strftime("%H:%M:%S")
        self.append_debug(f"<font color='blue'>[AUTO {now_str}] 📊 Search completed: Found {len(forms)} form(s) with recent submissions</font>")

        new_added = 0
        found_urls = set()
        for form in forms:
            url = form['url']
            found_urls.add(url)
            if url in self.forms_data:
                continue

            title = form['title']
            last = form.get('last_submission')
            if last:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                last_str = last.astimezone(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M:%S ICT")
            else:
                last_str = "None"
            display_text = f"{title} (Last submission: {last_str}) — {url}"

            item = QListWidgetItem(f"⏳ {display_text}")
            item.setData(Qt.UserRole, url)
            item.setForeground(QColor("#0d6efd"))
            self.form_list.addItem(item)
            self.forms_data[url] = title
            new_added += 1

        if found_urls:
            if new_added > 0:
                self.append_debug(f"<font color='green'>[AUTO] ??? Added {new_added} new form(s) ??? Starting grading (recent submissions only)...</font>")
            else:
                self.append_debug("<font color='green'>[AUTO] ??? Found recent submissions in existing queued form(s) ??? Starting grading (recent submissions only)...</font>")
            self.save_forms()
            self.run_grader(force_recent_only=True)
        else:
            self.append_debug(f"<font color='orange'>[AUTO] ???? No new forms with recent submissions found.</font>")
            self.schedule_next_cycle()

        # Update last check time
        self.last_check_time = datetime.now(timezone.utc)

    def schedule_next_cycle(self):
        """Schedule the next auto-cycle"""
        if not self.auto_mode or self.is_closing:
            return
            
        minutes = self.interval_seconds // 60
        next_check = datetime.now() + timedelta(seconds=self.interval_seconds)
        next_str = next_check.strftime("%H:%M:%S")
        self.append_debug(f"<font color='gray'>[AUTO] ⏰ Next check in {minutes} minute(s) at {next_str}</font>")
        
        # Cancel any existing timer
        if self.auto_timer:
            self.auto_timer.stop()
            self.auto_timer.deleteLater()
            
        self.auto_timer = QTimer()
        self.auto_timer.setSingleShot(True)
        self.auto_timer.timeout.connect(self.auto_cycle)
        self.auto_timer.start(self.interval_seconds * 1000)

    def stop_auto_mode(self):
        """Stop auto mode and clean up"""
        self.auto_mode = False
        self.stop_button.hide()
        self.run_button.setEnabled(True)
        self.append_debug("<b><font color='red'>AUTO RUN STOPPED</font></b>")
        
        # Cancel timer
        if self.auto_timer:
            self.auto_timer.stop()
            self.auto_timer.deleteLater()
            self.auto_timer = None
        
        # Stop search thread if running
        if self.auto_search_thread and self.auto_search_thread.isRunning():
            self.auto_search_thread.terminate()
            self.auto_search_thread.wait(5000)
        
        # Stop grader thread if running
        if self.grader_thread and self.grader_thread.isRunning():
            self.grader_thread.terminate()
            self.grader_thread.wait(5000)
            
        self.is_searching = False
        self.is_grading = False

    def run_grader(self, force_recent_only=False):
        """Start the grading process"""
        if not self.forms_data:
            if self.auto_mode:
                self.schedule_next_cycle()
            else:
                QMessageBox.information(self, "No Forms", "Add forms first.")
            return

        if self.is_grading or (self.grader_thread and self.grader_thread.isRunning()):
            self.append_debug("<font color='orange'>[GRADER] ⚠️ Grading already in progress</font>")
            return

        self.is_grading = True
        self.run_button.setEnabled(False)
        self.debug_output.clear()
        self.debug_lines = []
        self.overall_progress_bar.setValue(0)
        self.finished_forms = []

        grading_mode = self.grading_mode_combo.currentText()
        grade_recent_only = force_recent_only or (grading_mode == "Recent Only")

        self.grader_thread = GraderThread(grade_recent_only=grade_recent_only)
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.progress.connect(self.update_progress)
        self.grader_thread.overall_progress.connect(self.update_overall_progress)
        self.grader_thread.debug_message.connect(self.append_debug)
        self.grader_thread.current_form.connect(lambda url: self.current_label.setText(f"🟡 Processing: {url.split('/')[-2][:10]}..."))
        self.grader_thread.finished_form.connect(self.update_finished_form)
        self.grader_thread.start()

    def update_progress(self, cur, tot):
        pass

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
                title = current_text.split(" — ")[0] if " — " in current_text else "Unknown Form"
                item.setText(f"✅ {current_text}")
                item.setForeground(QColor("#198754"))
                self.append_debug(f"<font color='green'>[AUTO {now_str}] ✅ Completed: {title}</font>")
                break
        
        self.finished_label.setText(f"✅ Finished: {len(self.finished_forms)}")
    def is_timing_line(self, message):
        return "Timing " in message

    def append_debug(self, message):
        self.debug_lines.append(message)
        if not self.timing_only_checkbox.isChecked() or self.is_timing_line(message):
            self.debug_output.append(message)

    def on_timing_filter_changed(self, state):
        self.debug_output.clear()
        if not self.debug_lines:
            return
        if self.timing_only_checkbox.isChecked():
            lines = [m for m in self.debug_lines if self.is_timing_line(m)]
        else:
            lines = self.debug_lines
        for line in lines:
            self.debug_output.append(line)


    def on_grading_finished(self, success, msg):
        self.is_grading = False
        self.run_button.setEnabled(True)
        now_str = datetime.now().strftime("%H:%M:%S")
        
        if not success:
            self.append_debug(f"<font color='red'>[AUTO {now_str}] ❌ Grading failed: {msg}</font>")
        else:
            self.append_debug(f"<font color='green'>[AUTO {now_str}] ✅ Grading completed successfully!</font>")

        if self.auto_mode:
            # Clear finished forms
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
                self.append_debug(f"<font color='gray'>[AUTO] 🗑️ Cleared {forms_cleared} finished forms from queue</font>")
                self.save_forms()
            
            remaining_forms = self.form_list.count()
            finished_count = len(self.finished_forms)
            self.append_debug(f"<font color='blue'>[AUTO] 📊 Session Stats: Finished: {finished_count}, In queue: {remaining_forms}</font>")
            
            # Schedule next cycle
            self.schedule_next_cycle()
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
        """Properly clean up threads before closing"""
        self.is_closing = True
        self.auto_mode = False
        
        # Cancel timer
        if self.auto_timer:
            self.auto_timer.stop()
            self.auto_timer.deleteLater()
        
        # Stop and wait for grader thread
        if self.grader_thread and self.grader_thread.isRunning():
            self.grader_thread.quit()  # Safer than terminate
            if not self.grader_thread.wait(3000):
                self.grader_thread.terminate()
                self.grader_thread.wait(2000)
        
        # Stop and wait for auto search thread
        if self.auto_search_thread and self.auto_search_thread.isRunning():
            self.auto_search_thread.quit()
            if not self.auto_search_thread.wait(3000):
                self.auto_search_thread.terminate()
                self.auto_search_thread.wait(2000)
        
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




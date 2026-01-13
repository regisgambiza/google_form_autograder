# gui_main.py - Complete Modern Redesign from Scratch
import sys
import os
import json
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QTextEdit, QLabel, QComboBox, QCheckBox,
    QFrame, QScrollArea, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect, pyqtProperty
from PyQt5.QtGui import QColor, QFont, QPalette, QPainter, QLinearGradient, QPen
from datetime import datetime, timedelta, timezone

# Local imports
from auth import get_service, get_drive_service, get_classroom_service
from form_searcher import find_forms_with_submissions_in_range, load_predefined_folders, save_predefined_folders
from auto_add_dialog import AutoAddDialog, SearchThread
from grader_thread import GraderThread
from class_loader_thread import ClassLoaderThread
import ollama


class StatsCard(QFrame):
    """Modern card component for displaying statistics"""
    def __init__(self, icon, title, value="0", color="#667eea"):
        super().__init__()
        self.color = color
        self.setup_ui(icon, title, value)
        
    def setup_ui(self, icon, title, value):
        self.setObjectName("StatsCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        
        # Icon and value row
        top_row = QHBoxLayout()
        icon_label = QLabel(icon)
        icon_label.setStyleSheet(f"font-size: 28px; color: {self.color};")
        
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"font-size: 32px; font-weight: 700; color: {self.color};")
        
        top_row.addWidget(icon_label)
        top_row.addStretch()
        top_row.addWidget(self.value_label)
        
        # Title
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-size: 13px; color: #64748b; font-weight: 500;")
        
        layout.addLayout(top_row)
        layout.addWidget(self.title_label)
        
    def set_value(self, value):
        self.value_label.setText(str(value))


class ModernProgressBar(QFrame):
    """Custom animated progress bar"""
    def __init__(self):
        super().__init__()
        self._value = 0
        self.setFixedHeight(8)
        self.setStyleSheet("background-color: #e2e8f0; border-radius: 4px;")
        
    @pyqtProperty(int)
    def value(self):
        return self._value
        
    @value.setter
    def value(self, val):
        self._value = max(0, min(100, val))
        self.update()
        
    def paintEvent(self, event):
        super().paintEvent(event)
        if self._value > 0:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            
            gradient = QLinearGradient(0, 0, self.width(), 0)
            gradient.setColorAt(0, QColor("#667eea"))
            gradient.setColorAt(1, QColor("#764ba2"))
            
            painter.setBrush(gradient)
            painter.setPen(Qt.NoPen)
            
            width = int(self.width() * self._value / 100)
            painter.drawRoundedRect(0, 0, width, self.height(), 4, 4)
            

class FormManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Form Autograder")
        self.setGeometry(80, 80, 1400, 900)
        
        self.grader_thread = None
        self.auto_search_thread = None
        self.forms_data = {}
        self.service = None
        self.finished_forms = []
        self.auto_mode = False
        
        # Auto Mode Settings
        self.recency_minutes = 60
        self.interval_seconds = 300
        self.folders = []
        self.last_check_time = None
        
        self.setup_ui()
        self.apply_styles()
        self.load_forms()
        self.load_config()
        
    def setup_ui(self):
        """Setup the complete UI from scratch"""
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)
        
        # ========== HEADER ==========
        header = QHBoxLayout()
        title = QLabel("📝 Form Autograder")
        title.setObjectName("AppTitle")
        header.addWidget(title)
        header.addStretch()
        
        # Settings in header
        self.settings_btn = QPushButton("⚙️ Settings")
        self.settings_btn.setObjectName("HeaderButton")
        header.addWidget(self.settings_btn)
        
        main_layout.addLayout(header)
        
        # ========== STATS CARDS ==========
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)
        
        self.queue_card = StatsCard("📋", "In Queue", "0", "#3b82f6")
        self.processing_card = StatsCard("⚡", "Processing", "-", "#f59e0b")
        self.completed_card = StatsCard("✓", "Completed", "0", "#10b981")
        
        stats_layout.addWidget(self.queue_card, 1)
        stats_layout.addWidget(self.processing_card, 1)
        stats_layout.addWidget(self.completed_card, 1)
        
        main_layout.addLayout(stats_layout)
        
        # ========== PROGRESS SECTION ==========
        progress_frame = QFrame()
        progress_frame.setObjectName("ProgressFrame")
        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.setContentsMargins(24, 20, 24, 20)
        progress_layout.setSpacing(12)
        
        progress_header = QHBoxLayout()
        progress_title = QLabel("Overall Progress")
        progress_title.setStyleSheet("font-size: 16px; font-weight: 600; color: #1e293b;")
        
        self.progress_pct = QLabel("0%")
        self.progress_pct.setStyleSheet("font-size: 24px; font-weight: 700; color: #667eea;")
        
        progress_header.addWidget(progress_title)
        progress_header.addStretch()
        progress_header.addWidget(self.progress_pct)
        
        self.progress_bar = ModernProgressBar()
        
        progress_layout.addLayout(progress_header)
        progress_layout.addWidget(self.progress_bar)
        
        main_layout.addWidget(progress_frame)
        
        # ========== MAIN CONTENT AREA ==========
        content_layout = QHBoxLayout()
        content_layout.setSpacing(20)
        
        # LEFT: Forms List
        left_panel = QFrame()
        left_panel.setObjectName("ContentPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(20, 20, 20, 20)
        left_layout.setSpacing(16)
        
        list_header = QHBoxLayout()
        list_title = QLabel("Forms Queue")
        list_title.setStyleSheet("font-size: 18px; font-weight: 600; color: #1e293b;")
        list_header.addWidget(list_title)
        list_header.addStretch()
        
        # Action buttons for list
        self.add_btn = QPushButton("+ Add")
        self.add_btn.setObjectName("SmallButton")
        self.add_btn.clicked.connect(self.open_manual_add_dialog)
        
        self.remove_btn = QPushButton("✕ Remove")
        self.remove_btn.setObjectName("SmallButtonSecondary")
        self.remove_btn.clicked.connect(self.remove_form)
        
        list_header.addWidget(self.add_btn)
        list_header.addWidget(self.remove_btn)
        
        left_layout.addLayout(list_header)
        
        # Forms list
        self.form_list = QListWidget()
        self.form_list.setObjectName("FormList")
        left_layout.addWidget(self.form_list, 1)
        
        content_layout.addWidget(left_panel, 2)
        
        # RIGHT: Console Output
        right_panel = QFrame()
        right_panel.setObjectName("ContentPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(16)
        
        console_header = QHBoxLayout()
        console_title = QLabel("Console")
        console_title.setStyleSheet("font-size: 18px; font-weight: 600; color: #1e293b;")
        console_header.addWidget(console_title)
        console_header.addStretch()
        
        clear_console = QPushButton("Clear")
        clear_console.setObjectName("SmallButtonSecondary")
        clear_console.clicked.connect(lambda: self.debug_output.clear())
        console_header.addWidget(clear_console)
        
        right_layout.addLayout(console_header)
        
        self.debug_output = QTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setObjectName("Console")
        right_layout.addWidget(self.debug_output, 1)
        
        content_layout.addWidget(right_panel, 3)
        
        main_layout.addLayout(content_layout, 1)
        
        # ========== CONTROL BAR ==========
        control_frame = QFrame()
        control_frame.setObjectName("ControlBar")
        control_layout = QHBoxLayout(control_frame)
        control_layout.setContentsMargins(20, 16, 20, 16)
        control_layout.setSpacing(12)
        
        # Left side - Main actions
        self.auto_find_btn = QPushButton("🔍 Auto Find Forms")
        self.auto_find_btn.setObjectName("ActionButton")
        self.auto_find_btn.clicked.connect(self.open_manual_add_dialog)
        
        self.auto_run_btn = QPushButton("▶️ Start Auto Run")
        self.auto_run_btn.setObjectName("ActionButtonPrimary")
        self.auto_run_btn.clicked.connect(self.open_auto_run_dialog)
        
        self.run_btn = QPushButton("🚀 Grade Now")
        self.run_btn.setObjectName("ActionButtonSuccess")
        self.run_btn.clicked.connect(self.run_grader)
        
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setObjectName("ActionButtonDanger")
        self.stop_btn.clicked.connect(self.stop_auto_mode)
        self.stop_btn.hide()
        
        control_layout.addWidget(self.auto_find_btn)
        control_layout.addWidget(self.auto_run_btn)
        control_layout.addWidget(self.run_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addStretch()
        
        # Right side - Settings
        settings_group = QHBoxLayout()
        settings_group.setSpacing(12)
        
        # Evaluator
        eval_label = QLabel("Evaluator")
        eval_label.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 500;")
        self.evaluator_combo = QComboBox()
        self.evaluator_combo.setObjectName("SettingsCombo")
        self.evaluator_combo.addItems(["Basic", "Advanced"])
        self.evaluator_combo.currentTextChanged.connect(self.update_evaluator)
        
        # Leniency
        len_label = QLabel("Leniency")
        len_label.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 500;")
        self.leniency_combo = QComboBox()
        self.leniency_combo.setObjectName("SettingsCombo")
        self.leniency_combo.addItems(["extreme", "lenient", "balanced", "strict"])
        self.leniency_combo.currentTextChanged.connect(self.update_leniency)
        
        # Model
        model_label = QLabel("Model")
        model_label.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 500;")
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("SettingsCombo")
        available_models = ollama.list().get('models', [])
        if available_models:
            self.model_combo.addItems([m['name'] for m in available_models])
        self.model_combo.currentTextChanged.connect(self.update_model)
        
        self.report_check = QCheckBox("Generate Report")
        self.report_check.setChecked(True)
        self.report_check.setObjectName("SettingsCheck")
        self.report_check.stateChanged.connect(self.update_report_option)
        
        settings_group.addWidget(eval_label)
        settings_group.addWidget(self.evaluator_combo)
        settings_group.addWidget(len_label)
        settings_group.addWidget(self.leniency_combo)
        settings_group.addWidget(model_label)
        settings_group.addWidget(self.model_combo)
        settings_group.addWidget(self.report_check)
        
        control_layout.addLayout(settings_group)
        
        main_layout.addWidget(control_frame)
        
    def apply_styles(self):
        """Apply modern stylesheet"""
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f8fafc, stop:1 #e2e8f0);
            }
            
            QLabel#AppTitle {
                font-size: 28px;
                font-weight: 700;
                color: #0f172a;
                letter-spacing: -0.5px;
            }
            
            QPushButton#HeaderButton {
                background-color: white;
                color: #475569;
                border: 2px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 500;
                font-size: 13px;
            }
            
            QPushButton#HeaderButton:hover {
                background-color: #f8fafc;
                border-color: #cbd5e1;
            }
            
            QFrame#StatsCard {
                background-color: white;
                border-radius: 16px;
                border: 1px solid #e2e8f0;
            }
            
            QFrame#ProgressFrame {
                background-color: white;
                border-radius: 16px;
                border: 1px solid #e2e8f0;
            }
            
            QFrame#ContentPanel {
                background-color: white;
                border-radius: 16px;
                border: 1px solid #e2e8f0;
            }
            
            QFrame#ControlBar {
                background-color: white;
                border-radius: 16px;
                border: 1px solid #e2e8f0;
            }
            
            QListWidget#FormList {
                background-color: transparent;
                border: none;
                font-size: 13px;
                outline: none;
            }
            
            QListWidget#FormList::item {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 14px;
                margin-bottom: 8px;
                color: #334155;
            }
            
            QListWidget#FormList::item:hover {
                background-color: #f1f5f9;
                border-color: #cbd5e1;
            }
            
            QListWidget#FormList::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ede9fe, stop:1 #ddd6fe);
                border-color: #a78bfa;
                color: #5b21b6;
            }
            
            QTextEdit#Console {
                background-color: #0f172a;
                border: none;
                border-radius: 12px;
                color: #e2e8f0;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                padding: 16px;
                selection-background-color: #334155;
            }
            
            QPushButton#SmallButton, QPushButton#SmallButtonSecondary {
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
                border: none;
            }
            
            QPushButton#SmallButton {
                background-color: #667eea;
                color: white;
            }
            
            QPushButton#SmallButton:hover {
                background-color: #5a67d8;
            }
            
            QPushButton#SmallButtonSecondary {
                background-color: #f1f5f9;
                color: #475569;
            }
            
            QPushButton#SmallButtonSecondary:hover {
                background-color: #e2e8f0;
            }
            
            QPushButton#ActionButton, QPushButton#ActionButtonPrimary,
            QPushButton#ActionButtonSuccess, QPushButton#ActionButtonDanger {
                border: none;
                border-radius: 10px;
                padding: 12px 24px;
                font-size: 14px;
                font-weight: 600;
                min-width: 140px;
            }
            
            QPushButton#ActionButton {
                background-color: #64748b;
                color: white;
            }
            
            QPushButton#ActionButton:hover {
                background-color: #475569;
            }
            
            QPushButton#ActionButtonPrimary {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #667eea, stop:1 #764ba2);
                color: white;
            }
            
            QPushButton#ActionButtonPrimary:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #5a67d8, stop:1 #6b3fa0);
            }
            
            QPushButton#ActionButtonSuccess {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #10b981, stop:1 #059669);
                color: white;
            }
            
            QPushButton#ActionButtonSuccess:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #059669, stop:1 #047857);
            }
            
            QPushButton#ActionButtonDanger {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ef4444, stop:1 #dc2626);
                color: white;
            }
            
            QPushButton#ActionButtonDanger:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #dc2626, stop:1 #b91c1c);
            }
            
            QPushButton:pressed {
                transform: scale(0.98);
            }
            
            QPushButton:disabled {
                background-color: #e2e8f0;
                color: #94a3b8;
            }
            
            QComboBox#SettingsCombo {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 12px;
                min-width: 100px;
                color: #334155;
                font-size: 13px;
            }
            
            QComboBox#SettingsCombo:hover {
                border-color: #cbd5e1;
            }
            
            QComboBox#SettingsCombo::drop-down {
                border: none;
                width: 24px;
            }
            
            QComboBox#SettingsCombo::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #64748b;
                margin-right: 6px;
            }
            
            QComboBox#SettingsCombo QAbstractItemView {
                background-color: white;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                selection-background-color: #f1f5f9;
                selection-color: #334155;
                padding: 4px;
            }
            
            QCheckBox#SettingsCheck {
                color: #475569;
                font-size: 13px;
                font-weight: 500;
                spacing: 8px;
            }
            
            QCheckBox#SettingsCheck::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #cbd5e1;
                background-color: white;
            }
            
            QCheckBox#SettingsCheck::indicator:hover {
                border-color: #94a3b8;
            }
            
            QCheckBox#SettingsCheck::indicator:checked {
                background-color: #667eea;
                border-color: #667eea;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iOSIgdmlld0JveD0iMCAwIDEyIDkiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PHBhdGggZD0iTTEgNEw0LjUgNy41TDExIDEiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+PC9zdmc+);
            }
        """)

    # ========== DATA MANAGEMENT ==========
    def load_forms(self):
        try:
            with open("forms_to_grade.json", "r") as f:
                data = json.load(f)
                form_urls = data.get("forms", [])
                for form in form_urls:
                    url = form.get("url") if isinstance(form, dict) else form
                    title = form.get("title", "Untitled") if isinstance(form, dict) else "Untitled"
                    self.forms_data[url] = title
                    self.add_form_to_list(url, title, pending=True)
            self.update_queue_count()
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save_forms(self):
        forms = [{"url": url, "title": self.forms_data[url]} for url in self.forms_data]
        with open("forms_to_grade.json", "w") as f:
            json.dump({"forms": forms}, f)

    def add_form_to_list(self, url, title, pending=True):
        icon = "⏳" if pending else "✅"
        display = f"{icon}  {title}\n     {url}"
        item = QListWidgetItem(display)
        item.setData(Qt.UserRole, url)
        if pending:
            item.setForeground(QColor("#64748b"))
        else:
            item.setForeground(QColor("#10b981"))
        self.form_list.addItem(item)

    def remove_form(self):
        selected = self.form_list.selectedItems()
        for item in selected:
            url = item.data(Qt.UserRole)
            if url in self.forms_data:
                del self.forms_data[url]
            self.form_list.takeItem(self.form_list.row(item))
        self.save_forms()
        self.update_queue_count()

    def update_queue_count(self):
        self.queue_card.set_value(self.form_list.count())

    # ========== CONFIG ==========
    def load_config(self):
        try:
            with open("config.json") as f:
                config = json.load(f)
                evaluator = config.get("evaluator", "ai_evaluator")
                self.evaluator_combo.setCurrentIndex(0 if evaluator == "ai_evaluator" else 1)
                self.leniency_combo.setCurrentText(config.get("leniency", "lenient"))
                models = config.get("models", {}).get("judge", [])
                if models:
                    self.model_combo.setCurrentText(models[0])
                self.report_check.setChecked(config.get("generate_report", True))
        except FileNotFoundError:
            pass

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

    def update_evaluator(self, text):
        evaluator = "ai_evaluator" if text == "Basic" else "ai_evaluator_2"
        self.update_config("evaluator", evaluator)

    def update_leniency(self, text):
        self.update_config("leniency", text)

    def update_model(self, text):
        self.update_config("models", {"judge": [text]})

    def update_report_option(self, state):
        self.update_config("generate_report", state == Qt.Checked)

    # ========== DIALOGS ==========
    def open_manual_add_dialog(self):
        dialog = AutoAddDialog(self, mode='manual')
        dialog.exec_()

    def open_auto_run_dialog(self):
        dialog = AutoAddDialog(self, mode='auto')
        dialog.exec_()

    # ========== AUTO MODE ==========
    def start_auto_mode(self):
        self.auto_mode = True
        self.stop_btn.show()
        self.run_btn.setEnabled(False)
        self.auto_run_btn.setEnabled(False)
        self.log_console("🟢 AUTO RUN STARTED", "#10b981")
        self.last_check_time = None

    def auto_cycle(self):
        now_utc = datetime.now(timezone.utc)
        
        if self.last_check_time is None:
            from_dt = now_utc - timedelta(minutes=self.recency_minutes)
            self.log_console(f"🔍 First scan: checking last {self.recency_minutes} minutes", "#3b82f6")
        else:
            from_dt = self.last_check_time
            self.log_console("🔄 Incremental check since last scan", "#3b82f6")
        
        to_dt = now_utc
        
        self.auto_search_thread = SearchThread(self.folders, from_dt, to_dt)
        self.auto_search_thread.progress.connect(lambda msg: self.log_console(msg, "#64748b"))
        self.auto_search_thread.finished.connect(self.on_auto_search_finished)
        self.auto_search_thread.start()

    def on_auto_search_finished(self, forms):
        self.log_console(f"📊 Search complete: {len(forms)} forms found", "#3b82f6")
        
        new_added = 0
        for form in forms:
            url = form['url']
            if url in self.forms_data:
                continue
            
            title = form['title']
            self.forms_data[url] = title
            self.add_form_to_list(url, title, pending=True)
            new_added += 1
        
        if new_added > 0:
            self.log_console(f"✅ Added {new_added} new forms → Starting grading", "#10b981")
            self.save_forms()
            self.update_queue_count()
            self.run_grader()
        else:
            self.log_console("No new forms found", "#f59e0b")
        
        self.last_check_time = datetime.now(timezone.utc)
        
        minutes = self.interval_seconds // 60
        next_time = (datetime.now() + timedelta(seconds=self.interval_seconds)).strftime("%H:%M:%S")
        self.log_console(f"⏰ Next check in {minutes}min at {next_time}", "#64748b")
        QTimer.singleShot(self.interval_seconds * 1000, self.auto_cycle)

    def stop_auto_mode(self):
        self.auto_mode = False
        self.stop_btn.hide()
        self.run_btn.setEnabled(True)
        self.auto_run_btn.setEnabled(True)
        self.log_console("🔴 AUTO RUN STOPPED", "#ef4444")
        if hasattr(self, 'auto_search_thread') and self.auto_search_thread.isRunning():
            self.auto_search_thread.terminate()
            self.auto_search_thread.wait()

    # ========== GRADER ==========
    def run_grader(self):
        if not self.forms_data:
            if self.auto_mode:
                QTimer.singleShot(5000, self.auto_cycle)
            else:
                QMessageBox.information(self, "No Forms", "Add forms first.")
            return

        if self.grader_thread and self.grader_thread.isRunning():
            return

        self.run_btn.setEnabled(False)
        self.debug_output.clear()
        self.progress_bar.value = 0
        self.progress_pct.setText("0%")
        self.finished_forms = []

        self.grader_thread = GraderThread()
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.progress.connect(self.update_progress)
        self.grader_thread.overall_progress.connect(self.update_overall_progress)
        self.grader_thread.debug_message.connect(lambda msg: self.log_console(msg, "#64748b"))
        self.grader_thread.current_form.connect(self.update_current_form)
        self.grader_thread.finished_form.connect(self.update_finished_form)
        self.grader_thread.start()

    def update_progress(self, cur, tot):
        pass

    def update_overall_progress(self, cur, tot):
        if not tot:
            self.progress_bar.value = 100
            self.progress_pct.setText("100%")
            return
        pct = int(cur / tot * 100)
        self.progress_bar.value = pct
        self.progress_pct.setText(f"{pct}%")
        self.queue_card.set_value(tot - cur)

    def update_current_form(self, url):
        form_id = url.split('/')[-2][:12] if '/' in url else "Unknown"
        self.processing_card.set_value(form_id + "...")

    def update_finished_form(self, form_id):
        self.finished_forms.append(form_id)
        
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            url = item.data(Qt.UserRole)
            if url and self.extract_form_id(url) == form_id:
                title = self.forms_data.get(url, "Unknown")
                item.setText(f"✅  {title}\n     {url}")
                item.setForeground(QColor("#10b981"))
                self.log_console(f"✅ Completed: {title}", "#10b981")
                break
        
        self.completed_card.set_value(len(self.finished_forms))

    def on_grading_finished(self, success, msg):
        self.run_btn.setEnabled(True)
        
        if not success:
            self.log_console(f"❌ Grading failed: {msg}", "#ef4444")
        else:
            self.log_console("✅ Grading completed successfully!", "#10b981")

        if self.auto_mode:
            # Clear finished forms
            i = 0
            cleared = 0
            while i < self.form_list.count():
                item = self.form_list.item(i)
                if item.text().startswith("✅"):
                    self.form_list.takeItem(i)
                    url = item.data(Qt.UserRole)
                    if url in self.forms_data:
                        del self.forms_data[url]
                    cleared += 1
                else:
                    i += 1
            
            if cleared > 0:
                self.log_console(f"🗑️ Cleared {cleared} finished forms", "#64748b")
                self.save_forms()
                self.update_queue_count()
            
            minutes = self.interval_seconds // 60
            next_time = (datetime.now() + timedelta(seconds=self.interval_seconds)).strftime("%H:%M:%S")
            self.log_console(f"🔄 Next check in {minutes}min at {next_time}", "#64748b")
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

    # ========== CONSOLE LOGGING ==========
    def log_console(self, message, color="#e2e8f0"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f'<span style="color: #64748b;">[{timestamp}]</span> <span style="color: {color};">{message}</span>'
        self.debug_output.append(formatted)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = FormManager()
    window.show()
    sys.exit(app.exec_())
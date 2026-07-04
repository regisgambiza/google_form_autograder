# gui_main.py - FIXED: Thread safety, duplicate prevention, proper cleanup
import sys
import os
import json
import subprocess
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QTextEdit, QLabel, QComboBox, QCheckBox,
    QProgressDialog, QSplitter, QSpinBox, QDialog, QFormLayout, QTabWidget,
    QSystemTrayIcon, QMenu, QAction, QStyle, QFrame, QProgressBar, QDoubleSpinBox,
    QScrollArea
)

from PyQt5.QtCore import Qt, QDate, QTimer, QSize
from PyQt5.QtGui import QColor, QBrush, QFont, QPalette
from datetime import datetime, timedelta, timezone, time
import ctypes
import atexit

# Local imports
from auth import get_service, get_drive_service, get_classroom_service
from form_searcher import (
    find_all_forms_in_sources,
    find_forms_with_submissions_in_range,
    load_predefined_folders,
    save_predefined_folders,
)
from auto_add_dialog import AutoAddDialog, SearchThread
from grader_thread import GraderThread
from class_loader_thread import ClassLoaderThread
import ollama
from evaluator_config import DEFAULT_CONFIG
from scheduler import scheduler as auto_scheduler
from answer_key_dashboard import AnswerKeyDashboard
from app_theme import apply_application_theme, apply_widget_theme
from cache_manager import clear_grading_cache
from answer_key_manager import load_pending_review_records, keep_teacher_answers_only
import re

BANGKOK_TZ = timezone(timedelta(hours=7))

EXECUTION_MODE_PRESETS = {
    "Maximum accuracy: independent unanimous jury + review": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 1,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
        "active_judge_roles": ["semantic_judge", "factual_judge", "concept_judge", "strict_judge"],
        "adaptive_math_jury": {
            "enabled": True,
            "primary_roles": ["semantic_judge", "factual_judge", "concept_judge"],
            "adjudicator_role": "strict_judge",
            "minimum_primary_confidence": 0.90,
            "ambiguity_markers": ["ambiguous", "uncertain", "unclear", "insufficient", "depends"],
        },
        "early_exit": {"enabled": False, "min_judges": 3, "agreement_confidence": 0.90},
        "accuracy_policy": {
            "enabled": True,
            "minimum_judge_confidence": 0.90,
            "required_accept_roles": ["semantic_judge", "factual_judge", "concept_judge"],
            "require_distinct_models": True,
            "embeddings_can_accept": False,
            "ambiguous_outcome": "REVIEW",
        },
        "answer_key_auto_add_proven_equivalents": True,
        "patient_ai_mode": True,
        "enable_jury_circuit_breaker": False,
        "judge_timeout_seconds": 7200,
        "judge_http_timeout_seconds": 7200,
        "judge_total_hard_timeout_seconds": 21600,
        "answer_hard_timeout_seconds": 21600,
        "jury_semaphore_acquire_timeout_seconds": 21600,
        "max_latency_per_answer_seconds": 21600,
        "embedding_timeout_seconds": 1800,
        "rubric_timeout_seconds": 3600,
        "dispatcher_stall_timeout_seconds": 7200,
        "ai_stall_timeout_seconds": 900,
        "jury_circuit_break_seconds": 0,
    },
    "Math: deterministic checks + semantic judge only (recommended)": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 1,
        "worker_queue_size": 1200,
        "producer_det_queue_low_watermark": 450,
        "producer_det_queue_high_watermark": 900,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "max_concurrent_embedding_http": 1,
        "judge_timeout_seconds": 45,
        "judge_http_timeout_seconds": 65,
        "judge_total_hard_timeout_seconds": 55,
        "jury_circuit_break_seconds": 900,
        "max_latency_per_answer_seconds": 45,
        "dispatcher_stall_timeout_seconds": 420,
        "ai_stall_timeout_seconds": 300,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
        "active_judge_roles": ["semantic_judge"],
        "judge_prewarm_enabled": True,
        "judge_prewarm_timeout_seconds": 20,
        "confidence_thresholds": {
            "auto_accept": 0.90,
            "auto_reject": 0.35,
        },
        "embedding_thresholds": {
            "auto_accept": 0.90,
            "auto_reject": 0.42,
            "send_to_jury": [0.42, 0.90]
        },
        "consensus_weights": {
            "semantic_similarity": 0.45,
            "concept_coverage": 0.25,
            "factual_accuracy": 0.15,
            "strict_judge": 0.05,
            "language_noise": 0.0,
            "embedding": 0.10,
        },
    },
    "Bulk speed: all forms, high concurrency, less review": {
        "deterministic_worker_count": 7,
        "ai_worker_count": 4,
        "worker_queue_size": 3000,
        "producer_det_queue_low_watermark": 1200,
        "producer_det_queue_high_watermark": 2500,
        "max_concurrent_judge_http": 5,
        "max_concurrent_jury_answers": 4,
        "max_concurrent_embedding_http": 4,
        "judge_timeout_seconds": 25,
        "judge_http_timeout_seconds": 35,
        "max_latency_per_answer_seconds": 25,
        "dispatcher_stall_timeout_seconds": 120,
        "ai_stall_timeout_seconds": 120,
        "enable_async_judges": False,
        "sync_judge_parallelism": 6,
    },
    "Daily balanced: semantic/factual review with moderate concurrency": {
        "deterministic_worker_count": 5,
        "ai_worker_count": 3,
        "worker_queue_size": 2000,
        "producer_det_queue_low_watermark": 900,
        "producer_det_queue_high_watermark": 1700,
        "max_concurrent_judge_http": 4,
        "max_concurrent_jury_answers": 3,
        "max_concurrent_embedding_http": 3,
        "judge_timeout_seconds": 30,
        "judge_http_timeout_seconds": 45,
        "max_latency_per_answer_seconds": 30,
        "dispatcher_stall_timeout_seconds": 150,
        "ai_stall_timeout_seconds": 120,
        "enable_async_judges": False,
        "sync_judge_parallelism": 6,
    },
    "Slow-model safe: lower concurrency, longer timeouts": {
        "deterministic_worker_count": 5,
        "ai_worker_count": 2,
        "worker_queue_size": 1800,
        "producer_det_queue_low_watermark": 700,
        "producer_det_queue_high_watermark": 1400,
        "max_concurrent_judge_http": 2,
        "max_concurrent_jury_answers": 2,
        "max_concurrent_embedding_http": 2,
        "judge_timeout_seconds": 45,
        "judge_http_timeout_seconds": 65,
        "max_latency_per_answer_seconds": 45,
        "dispatcher_stall_timeout_seconds": 240,
        "ai_stall_timeout_seconds": 180,
        "enable_async_judges": False,
        "sync_judge_parallelism": 3,
    },
    "General accuracy: semantic + factual 2-judge review": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 1,
        "worker_queue_size": 1200,
        "producer_det_queue_low_watermark": 450,
        "producer_det_queue_high_watermark": 900,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "max_concurrent_embedding_http": 1,
        "judge_timeout_seconds": 80,
        "judge_http_timeout_seconds": 110,
        "judge_total_hard_timeout_seconds": 95,
        "jury_circuit_break_seconds": 1200,
        "max_latency_per_answer_seconds": 90,
        "dispatcher_stall_timeout_seconds": 420,
        "ai_stall_timeout_seconds": 300,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
        "active_judge_roles": ["semantic_judge", "factual_judge"],
        "judge_prewarm_enabled": True,
        "judge_prewarm_timeout_seconds": 20,
        "embedding_thresholds": {
            "auto_accept": 0.88,
            "auto_reject": 0.52,
            "send_to_jury": [0.52, 0.88]
        },
    },
    "Strict review: semantic + factual + strict judge": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 1,
        "worker_queue_size": 1200,
        "producer_det_queue_low_watermark": 450,
        "producer_det_queue_high_watermark": 900,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "max_concurrent_embedding_http": 1,
        "judge_timeout_seconds": 55,
        "judge_http_timeout_seconds": 75,
        "judge_total_hard_timeout_seconds": 50,
        "jury_circuit_break_seconds": 900,
        "max_latency_per_answer_seconds": 55,
        "dispatcher_stall_timeout_seconds": 420,
        "ai_stall_timeout_seconds": 300,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
        "active_judge_roles": ["semantic_judge", "factual_judge", "strict_judge"],
        "judge_prewarm_enabled": True,
        "judge_prewarm_timeout_seconds": 20,
        "embedding_thresholds": {
            "auto_accept": 0.90,
            "auto_reject": 0.45,
            "send_to_jury": [0.45, 0.90]
        },
    },
    "Recovery: lowest load, longest timeouts": {
        "deterministic_worker_count": 3,
        "ai_worker_count": 1,
        "worker_queue_size": 1000,
        "producer_det_queue_low_watermark": 350,
        "producer_det_queue_high_watermark": 700,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 1,
        "max_concurrent_embedding_http": 1,
        "judge_timeout_seconds": 90,
        "judge_http_timeout_seconds": 120,
        "max_latency_per_answer_seconds": 90,
        "dispatcher_stall_timeout_seconds": 600,
        "ai_stall_timeout_seconds": 420,
        "enable_async_judges": False,
        "sync_judge_parallelism": 1,
    },
}

EXECUTION_MODE_ALIASES = {
    "Max Speed": "Bulk speed: all forms, high concurrency, less review",
    "Balanced": "Daily balanced: semantic/factual review with moderate concurrency",
    "Stable": "Slow-model safe: lower concurrency, longer timeouts",
    "High Accuracy": "General accuracy: semantic + factual 2-judge review",
    "High Accuracy (Practical)": "Strict review: semantic + factual + strict judge",
    "Safe Mode": "Recovery: lowest load, longest timeouts",
    "Fastest: Bulk Grading": "Bulk speed: all forms, high concurrency, less review",
    "Standard: Daily Grading": "Daily balanced: semantic/factual review with moderate concurrency",
    "Reliable: Slow Model Safety": "Slow-model safe: lower concurrency, longer timeouts",
    "Conservative: 2-Judge Review": "General accuracy: semantic + factual 2-judge review",
    "Strict: 3-Judge Review": "Strict review: semantic + factual + strict judge",
    "Recovery: Low Load": "Recovery: lowest load, longest timeouts",
}

DEFAULT_EXECUTION_MODE = "Maximum accuracy: independent unanimous jury + review"


def normalize_execution_mode(mode_name):
    return EXECUTION_MODE_ALIASES.get(mode_name, mode_name)

class FormManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Form Autograder")
        self.setGeometry(100, 100, 1250, 820)
        self.setMinimumSize(1000, 700)
        self.grading_mode = "Whole Form"

        self.grader_thread = None
        self.auto_search_thread = None
        self.forms_data = {}
        self.service = None
        self.finished_forms = []
        self.current_form_url = None
        self.auto_mode = False
        self.auto_timer = None  # Track the QTimer for auto-cycle
        self.debug_lines = []

        # Auto Mode Settings
        self.recency_minutes = 60
        self.interval_seconds = 300
        self.folders = []
        self.last_check_time = None

        # Scheduler Settings
        self.use_time_schedule = False
        self.schedule_time_val = None
        self.selected_days = [True] * 7  # All days by default

        # Thread safety flags
        self.is_searching = False
        self.is_grading = False
        self.is_closing = False
        self._force_exit = False
        self._shutdown_complete = False
        self.tray_icon = None

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
            QListWidget#FormQueueList {
                background-color: #eef3f8;
                border: 1px solid #c9d5e2;
                border-radius: 8px;
                padding: 8px;
            }
            QListWidget#FormQueueList::item {
                border: none;
                margin: 4px 0;
            }
            QFrame#FormCard {
                background-color: #ffffff;
                border: 1px solid #d7e0ea;
                border-left: 5px solid #6c757d;
                border-radius: 8px;
            }
            QFrame#FormCard[status="queued"] {
                border-left-color: #0d6efd;
            }
            QFrame#FormCard[status="running"] {
                border-left-color: #f59f00;
                background-color: #fff9e8;
            }
            QFrame#FormCard[status="done"] {
                border-left-color: #198754;
                background-color: #f1fbf5;
            }
            QFrame#FormCard[status="failed"] {
                border-left-color: #dc3545;
                background-color: #fff3f3;
            }
            QLabel#FormTitle {
                font-size: 14px;
                font-weight: 700;
                color: #1f2937;
            }
            QLabel#FormMeta {
                font-size: 11px;
                color: #5b6775;
            }
            QLabel#FormUrl {
                font-size: 11px;
                color: #0d6efd;
            }
            QLabel#StatusBadge {
                font-size: 11px;
                font-weight: 700;
                color: white;
                background-color: #6c757d;
                border-radius: 10px;
                padding: 3px 8px;
            }
            QLabel#StatusBadge[status="queued"] {
                background-color: #0d6efd;
            }
            QLabel#StatusBadge[status="running"] {
                background-color: #f59f00;
                color: #342100;
            }
            QLabel#StatusBadge[status="done"] {
                background-color: #198754;
            }
            QLabel#StatusBadge[status="failed"] {
                background-color: #dc3545;
            }
            QSplitter::handle {
                background-color: #d0d0d0;
            }
        """)

        central_widget = QWidget()
        central_widget.setObjectName("AppShell")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("AppHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 9, 14, 9)
        brand_layout = QVBoxLayout()
        brand_layout.setSpacing(1)
        brand_title = QLabel("Google Form Autograder")
        brand_title.setObjectName("AppBrand")
        brand_subtitle = QLabel("Mathematics workspace")
        brand_subtitle.setObjectName("Muted")
        brand_layout.addWidget(brand_title)
        brand_layout.addWidget(brand_subtitle)
        header_layout.addLayout(brand_layout)
        header_layout.addStretch()

        self.current_label = QLabel("Processing: -")
        self.finished_label = QLabel("Finished: 0")
        self.in_queue_label = QLabel("In Queue: 0")
        self.pipeline_state_label = QLabel("Pipeline State: Idle")
        for hidden_label in (self.current_label, self.finished_label, self.in_queue_label, self.pipeline_state_label):
            hidden_label.hide()
        self.run_state_dot = QLabel()
        self.run_state_dot.setObjectName("RunStateDot")
        self.run_state_dot.setFixedSize(9, 9)
        self.run_state_label = QLabel("Ready")
        self.run_state_label.setObjectName("Muted")
        header_layout.addWidget(self.run_state_dot)
        header_layout.addWidget(self.run_state_label)

        terminal_top_button = QPushButton(">_")
        terminal_top_button.setObjectName("IconButton")
        terminal_top_button.setToolTip("Show terminal")
        terminal_top_button.setFixedSize(36, 36)
        terminal_top_button.setProperty("preserveText", True)
        terminal_top_button.setProperty("noAutoIcon", True)
        terminal_top_button.clicked.connect(lambda: self.set_terminal_state("open"))
        header_layout.addWidget(terminal_top_button)
        settings_button = QPushButton()
        settings_button.setObjectName("IconButton")
        settings_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        settings_button.setToolTip("Settings")
        settings_button.setFixedSize(36, 36)
        settings_button.clicked.connect(self.open_settings_dialog)
        header_layout.addWidget(settings_button)
        main_layout.addWidget(header)

        command_bar = QFrame()
        command_bar.setObjectName("CommandBar")
        command_layout = QHBoxLayout(command_bar)
        command_layout.setContentsMargins(14, 7, 14, 7)
        command_layout.setSpacing(8)
        add_sources_button = QPushButton("Add Sources")
        add_sources_button.setObjectName("Secondary")
        add_sources_button.setFixedWidth(145)
        add_sources_button.clicked.connect(self.open_manual_add_dialog)
        command_layout.addWidget(add_sources_button)
        scan_source_button = QPushButton("Scan Source")
        scan_source_button.setFixedWidth(145)
        scan_source_button.setObjectName("Secondary")
        scan_source_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView))
        scan_source_button.setProperty("noAutoIcon", True)
        scan_source_button.clicked.connect(self.open_quick_grade_dialog)
        command_layout.addWidget(scan_source_button)
        self.run_button = QPushButton("Run Grading")
        self.run_button.setFixedWidth(145)
        self.run_button.setObjectName("Secondary")
        self.run_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.run_button.setProperty("noAutoIcon", True)
        self.run_button.clicked.connect(self.run_grader)
        command_layout.addWidget(self.run_button)
        self.stop_button = QPushButton("Stop Grading")
        self.stop_button.setObjectName("Danger")
        self.stop_button.setMaximumWidth(150)
        self.stop_button.clicked.connect(self.stop_grading)
        self.stop_button.hide()
        command_layout.addWidget(self.stop_button)
        answer_keys_button = QPushButton("Answer Keys")
        answer_keys_button.setObjectName("Secondary")
        answer_keys_button.setFixedWidth(145)
        answer_keys_button.clicked.connect(self.open_answer_key_dashboard)
        command_layout.addWidget(answer_keys_button)
        command_layout.addStretch()
        self.command_summary = QLabel("0 forms")
        self.command_summary.setObjectName("Muted")
        command_layout.addWidget(self.command_summary)

        more_button = QPushButton("...")
        more_button.setObjectName("IconButton")
        more_button.setFixedSize(36, 36)
        more_button.setToolTip("More actions")
        more_menu = QMenu(more_button)
        auto_run_action = more_menu.addAction("Schedule Automatic Runs")
        auto_run_action.triggered.connect(self.open_auto_run_dialog)
        grade_all_action = more_menu.addAction("Grade All Queued Forms")
        grade_all_action.triggered.connect(self.grade_all_forms_in_all_folders)
        more_menu.addSeparator()
        remove_action = more_menu.addAction("Remove Selected Form")
        remove_action.triggered.connect(self.remove_form)
        clear_action = more_menu.addAction("Clear Completed Forms")
        clear_action.triggered.connect(self.clear_finished_forms_silently)
        more_menu.addSeparator()
        exit_action = more_menu.addAction("Exit")
        exit_action.triggered.connect(self.exit_app)
        more_button.setMenu(more_menu)
        command_layout.addWidget(more_button)
        main_layout.addWidget(command_bar)

        workspace = QSplitter(Qt.Horizontal)
        workspace.setObjectName("WorkspaceSplitter")
        queue_widget = QFrame()
        queue_widget.setObjectName("QueuePane")
        queue_layout = QVBoxLayout(queue_widget)
        queue_layout.setContentsMargins(14, 12, 10, 10)
        queue_header = QHBoxLayout()
        queue_title = QLabel("Forms")
        queue_title.setObjectName("Section")
        self.form_queue_summary = QLabel("0 in queue")
        self.form_queue_summary.setObjectName("Muted")
        queue_header.addWidget(queue_title)
        queue_header.addStretch()
        queue_header.addWidget(self.form_queue_summary)
        queue_layout.addLayout(queue_header)
        queue_filters = QHBoxLayout()
        self.form_search_input = QLineEdit()
        self.form_search_input.setPlaceholderText("Search forms")
        self.form_search_input.textChanged.connect(self._filter_form_queue)
        self.form_filter_combo = QComboBox()
        self.form_filter_combo.addItems(["All", "Running", "Queued", "Done", "Failed"])
        self.form_filter_combo.currentTextChanged.connect(self._filter_form_queue)
        queue_filters.addWidget(self.form_search_input, 1)
        queue_filters.addWidget(self.form_filter_combo)
        queue_layout.addLayout(queue_filters)
        self.form_list = QListWidget()
        self.form_list.setObjectName("FormQueueList")
        self.form_list.setSpacing(4)
        self.form_list.setUniformItemSizes(False)
        self.form_list.setWordWrap(True)
        self.form_list.setTextElideMode(Qt.ElideRight)
        self.form_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.form_list.currentItemChanged.connect(self._on_form_selection_changed)
        queue_layout.addWidget(self.form_list, 1)
        workspace.addWidget(queue_widget)

        detail_widget = QFrame()
        detail_widget.setObjectName("DetailPane")
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(26, 22, 26, 16)
        detail_header = QHBoxLayout()
        detail_titles = QVBoxLayout()
        self.detail_title = QLabel("Select a form")
        self.detail_title.setObjectName("DetailTitle")
        self.detail_meta = QLabel("No form selected")
        self.detail_meta.setObjectName("Muted")
        detail_titles.addWidget(self.detail_title)
        detail_titles.addWidget(self.detail_meta)
        detail_header.addLayout(detail_titles)
        detail_header.addStretch()
        self.detail_badge = QLabel("IDLE")
        self.detail_badge.setObjectName("DetailBadge")
        self.detail_badge.setProperty("status", "queued")
        detail_header.addWidget(self.detail_badge)
        detail_layout.addLayout(detail_header)

        progress_header = QHBoxLayout()
        self.detail_progress_text = QLabel("Waiting to start")
        self.detail_progress_text.setObjectName("Muted")
        self.detail_progress_value = QLabel("0%")
        self.detail_progress_value.setStyleSheet("font-weight:700;")
        progress_header.addWidget(self.detail_progress_text)
        progress_header.addStretch()
        progress_header.addWidget(self.detail_progress_value)
        detail_layout.addSpacing(20)
        detail_layout.addLayout(progress_header)
        self.detail_progress = QProgressBar()
        self.detail_progress.setRange(0, 100)
        self.detail_progress.setValue(0)
        self.detail_progress.setTextVisible(False)
        detail_layout.addWidget(self.detail_progress)

        metrics_row = QHBoxLayout()
        self.metric_responses = QLabel("0 / 0")
        self.metric_accepted = QLabel("0")
        self.metric_rejected = QLabel("0")
        self.metric_review = QLabel('<a href="review">0</a>')
        self.metric_elapsed = QLabel("00:00")
        self.metric_review.setTextFormat(Qt.RichText)
        self.metric_review.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.metric_review.setOpenExternalLinks(False)
        self.metric_review.linkActivated.connect(self.open_current_form_review)
        for metric_name, metric_value in (
            ("Responses", self.metric_responses),
            ("Accepted", self.metric_accepted),
            ("Rejected", self.metric_rejected),
            ("Needs review", self.metric_review),
            ("Elapsed time", self.metric_elapsed),
        ):
            metric = QFrame()
            metric.setObjectName("Metric")
            metric_layout = QVBoxLayout(metric)
            metric_layout.setContentsMargins(12, 12, 12, 12)
            label = QLabel(metric_name)
            label.setObjectName("Muted")
            metric_value.setObjectName("MetricValue")
            metric_layout.addWidget(label)
            metric_layout.addWidget(metric_value)
            metrics_row.addWidget(metric, 1)
        detail_layout.addLayout(metrics_row)

        pipeline_heading = QHBoxLayout()
        pipeline_title = QLabel("Current pipeline")
        pipeline_title.setObjectName("Section")
        self.pipeline_updated = QLabel("Ready")
        self.pipeline_updated.setObjectName("Muted")
        pipeline_heading.addWidget(pipeline_title)
        pipeline_heading.addStretch()
        pipeline_heading.addWidget(self.pipeline_updated)
        detail_layout.addSpacing(10)
        detail_layout.addLayout(pipeline_heading)
        self.pipeline_rows = {}

        def add_stage(key, icon, name, description, state):
            row = QFrame()
            row.setObjectName("PipelineRow")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(4, 9, 4, 9)
            icon_label = QLabel(icon)
            icon_label.setFixedWidth(22)
            name_label = QLabel(name)
            name_label.setStyleSheet("font-weight:700;")
            detail_label = QLabel(description)
            detail_label.setObjectName("Muted")
            state_label = QLabel(state)
            state_label.setObjectName("Muted")
            layout.addWidget(icon_label)
            layout.addWidget(name_label, 1)
            layout.addWidget(detail_label, 2)
            layout.addWidget(state_label)
            detail_layout.addWidget(row)
            self.pipeline_rows[key] = (icon_label, detail_label, state_label)

        add_stage("load", "○", "Load form", "Questions and responses", "Waiting")
        add_stage("validate", "○", "Validate answer keys", "Canonical answers and context", "Waiting")
        add_stage("evaluate", "○", "Evaluate responses", "Deterministic and semantic checks", "Waiting")
        add_stage("apply", "○", "Apply grades", "Save grading updates", "Waiting")
        detail_layout.addStretch()
        workspace.addWidget(detail_widget)
        workspace.setSizes([340, 900])
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        main_layout.addWidget(workspace, 1)

        self.terminal_frame = QFrame()
        self.terminal_frame.setObjectName("TerminalFrame")
        terminal_layout = QVBoxLayout(self.terminal_frame)
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.setSpacing(0)
        terminal_bar = QHBoxLayout()
        terminal_bar.setContentsMargins(12, 0, 10, 0)
        self.terminal_toggle_button = QPushButton("Terminal")
        self.terminal_toggle_button.setObjectName("TerminalToggle")
        self.terminal_toggle_button.setMaximumWidth(120)
        self.terminal_toggle_button.setProperty("noAutoIcon", True)
        self.terminal_toggle_button.clicked.connect(self.toggle_terminal)
        terminal_bar.addWidget(self.terminal_toggle_button)
        terminal_status = QLabel("AI grading - live")
        terminal_status.setObjectName("TerminalMuted")
        terminal_bar.addWidget(terminal_status)
        terminal_bar.addStretch()
        self.timing_only_checkbox = QCheckBox("Timing only")
        self.timing_only_checkbox.stateChanged.connect(self.on_timing_filter_changed)
        self.timing_only_checkbox.hide()
        clear_terminal_button = QPushButton("Clear")
        clear_terminal_button.setObjectName("TerminalAction")
        clear_terminal_button.setMaximumWidth(70)
        clear_terminal_button.setProperty("noAutoIcon", True)
        clear_terminal_button.clicked.connect(self.clear_logs)
        terminal_bar.addWidget(clear_terminal_button)
        expand_terminal_button = QPushButton("□")
        expand_terminal_button.setObjectName("TerminalAction")
        expand_terminal_button.setFixedSize(32, 32)
        expand_terminal_button.setProperty("noAutoIcon", True)
        expand_terminal_button.setToolTip("Expand terminal")
        expand_terminal_button.clicked.connect(self.expand_terminal)
        terminal_bar.addWidget(expand_terminal_button)
        hide_terminal_button = QPushButton("×")
        hide_terminal_button.setObjectName("TerminalAction")
        hide_terminal_button.setFixedSize(32, 32)
        hide_terminal_button.setProperty("noAutoIcon", True)
        hide_terminal_button.setToolTip("Hide terminal")
        hide_terminal_button.clicked.connect(lambda: self.set_terminal_state("collapsed"))
        terminal_bar.addWidget(hide_terminal_button)
        terminal_layout.addLayout(terminal_bar)

        self.log_tabs = QTabWidget()
        self.debug_output = self._make_log_textedit()
        self.producer_output = self._make_log_textedit()
        self.det_output = self._make_log_textedit()
        self.ai_output = self._make_log_textedit()
        self.agg_output = self._make_log_textedit()
        self.log_tabs.addTab(self.debug_output, "AI grading")
        self._reset_worker_tab_titles()
        terminal_layout.addWidget(self.log_tabs, 1)
        self.terminal_state = "collapsed"
        main_layout.addWidget(self.terminal_frame)
        self.set_terminal_state("collapsed")

        self.load_forms()
        self.load_config()
        self.update_in_queue_label()
        if self.form_list.count():
            self.form_list.setCurrentRow(0)
        self._setup_system_tray()
        apply_widget_theme(self)

    def _filter_form_queue(self, *_args):
        query = self.form_search_input.text().strip().lower() if hasattr(self, "form_search_input") else ""
        selected_status = self.form_filter_combo.currentText().strip().lower() if hasattr(self, "form_filter_combo") else "all"
        for index in range(self.form_list.count()):
            item = self.form_list.item(index)
            meta = item.data(Qt.UserRole + 1) or {}
            title = str(meta.get("title", "")).lower()
            status = str(meta.get("status", "queued")).lower()
            status_matches = selected_status == "all" or status == selected_status
            item.setHidden(query not in title or not status_matches)

    def _on_form_selection_changed(self, current, _previous=None):
        if not current:
            self.detail_title.setText("Select a form")
            self.detail_meta.setText("No form selected")
            self.detail_badge.setText("IDLE")
            return
        meta = current.data(Qt.UserRole + 1) or {}
        status = str(meta.get("status", "queued"))
        self.detail_title.setText(meta.get("title") or "Untitled")
        self.detail_meta.setText(
            f"{meta.get('source', 'Queue')} · {self.grading_mode}"
        )
        self.detail_badge.setText(status.upper())
        self.detail_badge.setProperty("status", status)
        self.detail_badge.style().unpolish(self.detail_badge)
        self.detail_badge.style().polish(self.detail_badge)
        detail = meta.get("detail") or "Waiting to start"
        self.detail_progress_text.setText(detail)
        if status == "done":
            progress = 100
        elif status == "running":
            progress = max(1, self.detail_progress.value())
        else:
            progress = 0
        self.detail_progress.setValue(progress)
        self.detail_progress_value.setText(f"{progress}%")
        self.pipeline_updated.setText(meta.get("finished_at") or meta.get("started_at") or "Ready")
        self._update_pipeline_rows_for_status(status)

        # Update metrics cards
        completed = meta.get("completed", 0)
        total = meta.get("total", 0)
        accepted = meta.get("accepted", 0)
        rejected = meta.get("rejected", 0)
        review_questions = meta.get("review_questions", 0)
        elapsed_seconds = meta.get("elapsed", 0)

        # For inactive forms, dynamically load the most up-to-date review count from disk
        form_id = self.extract_form_id(meta.get("url"))
        if form_id:
            try:
                from answer_key_manager import load_pending_reviews
                pending = load_pending_reviews(form_id)
                review_questions = len(pending)
            except Exception:
                pass

        self.metric_responses.setText(f"{completed} / {total}")
        self.metric_accepted.setText(str(accepted))
        self.metric_rejected.setText(str(rejected))
        self.metric_review.setText(f'<a href="review">{review_questions}</a>')
        if isinstance(elapsed_seconds, str):
            self.metric_elapsed.setText(elapsed_seconds)
        else:
            hours, remainder = divmod(max(0, int(elapsed_seconds)), 3600)
            minutes, seconds = divmod(remainder, 60)
            self.metric_elapsed.setText(
                f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
            )

    def _update_pipeline_rows_for_status(self, status):
        order = ["load", "validate", "evaluate", "apply"]
        if status == "done":
            completed = 4
        elif status == "running":
            completed = 2
        elif status == "failed":
            completed = 2
        else:
            completed = 0
        for position, key in enumerate(order):
            icon, _detail, state = self.pipeline_rows[key]
            if position < completed:
                icon.setText("✓")
                icon.setStyleSheet("color:#16845b; font-weight:700;")
                state.setText("Done")
            elif position == completed and status == "running":
                icon.setText("●")
                icon.setStyleSheet("color:#b36b00;")
                state.setText("Running")
            elif status == "failed" and position == completed:
                icon.setText("!")
                icon.setStyleSheet("color:#b42318; font-weight:700;")
                state.setText("Failed")
            else:
                icon.setText("○")
                icon.setStyleSheet("")
                state.setText("Waiting")

    def set_terminal_state(self, state):
        self.terminal_state = state
        if state == "collapsed":
            self.log_tabs.hide()
            self.terminal_frame.setFixedHeight(38)
            self.terminal_toggle_button.setText("Terminal ▲")
        elif state == "expanded":
            self.log_tabs.show()
            self.terminal_frame.setFixedHeight(max(280, int(self.height() * 0.46)))
            self.terminal_toggle_button.setText("Terminal ▼")
        else:
            self.terminal_state = "open"
            self.log_tabs.show()
            self.terminal_frame.setFixedHeight(230)
            self.terminal_toggle_button.setText("Terminal ▼")

    def toggle_terminal(self):
        self.set_terminal_state("collapsed" if self.terminal_state != "collapsed" else "open")

    def expand_terminal(self):
        self.set_terminal_state("open" if self.terminal_state == "expanded" else "expanded")

    def clear_logs(self):
        self.debug_lines = []
        for output in (self.debug_output, self.producer_output, self.det_output, self.ai_output, self.agg_output):
            output.clear()

    def _setup_system_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray_menu = QMenu(self)
        show_action = QAction("Show", self)
        show_action.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        show_action.triggered.connect(self.restore_from_tray)
        exit_action = QAction("Exit", self)
        exit_action.setIcon(self.style().standardIcon(QStyle.SP_DialogCloseButton))
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(exit_action)

        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Google Form Autograder")
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.restore_from_tray()

    def restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.setModal(True)
        dialog.resize(760, 520)
        dialog.setMinimumSize(680, 480)
        dialog.setSizeGripEnabled(True)

        main_layout = QVBoxLayout(dialog)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        scroll_area = QScrollArea(dialog)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        scroll_widget = QWidget()
        form = QFormLayout(scroll_widget)
        scroll_widget.setLayout(form)
        scroll_area.setWidget(scroll_widget)
        main_layout.addWidget(scroll_area)

        evaluator_combo = QComboBox(dialog)
        evaluator_combo.addItems([
            "ai_evaluator (Basic)",
            "ai_evaluator_2 (Advanced)",
            "ai_evaluator_semantic (Semantic Pipeline)",
        ])

        leniency_combo = QComboBox(dialog)
        leniency_combo.addItems(["extreme", "lenient", "balanced", "strict"])

        model_combo = QComboBox(dialog)
        embedding_model_combo = QComboBox(dialog)
        reasoning_model_combo = QComboBox(dialog)
        minimum_judge_confidence_spin = QDoubleSpinBox(dialog)
        minimum_judge_confidence_spin.setRange(0.50, 1.00)
        minimum_judge_confidence_spin.setSingleStep(0.01)
        minimum_judge_confidence_spin.setDecimals(2)
        distinct_models_checkbox = QCheckBox("Require different models for acceptance", dialog)
        key_auto_add_checkbox = QCheckBox("Append validated answers now; audit them in Answer Keys", dialog)
        patient_ai_checkbox = QCheckBox("Patient AI: wait for complete model responses", dialog)
        audit_path_edit = QLineEdit(dialog)
        benchmark_path_edit = QLineEdit(dialog)

        cfg = {}
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        def normalize_model_key(model_name):
            text = str(model_name or "").strip()
            return text[:-7] if text.endswith(":latest") else text

        def add_model_choice(model_names, seen_keys, model_name):
            text = str(model_name or "").strip()
            key = normalize_model_key(text)
            if text and key and key not in seen_keys:
                model_names.append(text)
                seen_keys.add(key)

        def read_ollama_model_name(model_info):
            if isinstance(model_info, dict):
                return model_info.get("name") or model_info.get("model")
            return getattr(model_info, "name", None) or getattr(model_info, "model", None)

        ollama_models = []
        try:
            ollama_models = [
                read_ollama_model_name(model_info)
                for model_info in ollama.list().get("models", [])
            ]
        except Exception as e:
            print(f"Error fetching Ollama models: {e}")

        ollama_keys = {
            normalize_model_key(model_name)
            for model_name in ollama_models
            if normalize_model_key(model_name)
        }

        available_models = []
        seen_model_keys = set()

        # Prefer configured spelling, then append locally installed Ollama models.
        models = cfg.get("models", {}).get("judge", [])
        embedding_model = cfg.get("embedding_model")
        reasoning_model = cfg.get("reasoning_model")
        if models:
            add_model_choice(available_models, seen_model_keys, models[0])
        add_model_choice(available_models, seen_model_keys, embedding_model)
        add_model_choice(available_models, seen_model_keys, reasoning_model)

        cfg_jury = cfg.get("jury_models", {}) if cfg else {}
        for configured_model in cfg_jury.values():
            add_model_choice(available_models, seen_model_keys, configured_model)
        for model_name in ollama_models:
            add_model_choice(available_models, seen_model_keys, model_name)

        extra_configured_models = sorted(
            key for key in seen_model_keys if key not in ollama_keys
        )
        installed_model_count = len(ollama_keys)
        model_status_label = QLabel(
            f"{len(available_models)} selectable models "
            f"({installed_model_count} installed"
            f"{', ' + str(len(extra_configured_models)) + ' configured only' if extra_configured_models else ''}).",
            dialog,
        )
        model_status_label.setWordWrap(True)
        if extra_configured_models:
            model_status_label.setToolTip(
                "Configured but not reported by Ollama: " + ", ".join(extra_configured_models)
            )

        if available_models:
            model_combo.addItems(available_models)
            embedding_model_combo.addItems(available_models)
            reasoning_model_combo.addItems(available_models)

        # Jury model selectors (one combobox per jury role)
        jury_combos = {}
        jury_role_labels = {}
        jury_defaults = DEFAULT_CONFIG.get("jury_models", {})
        for role, default_model in jury_defaults.items():
            combo = QComboBox(dialog)
            # Ensure the configured/default model is present in the list
            role_model = cfg_jury.get(role, default_model)
            role_models = list(available_models)
            if normalize_model_key(role_model) not in {normalize_model_key(m) for m in role_models}:
                role_models.insert(0, role_model)
            if role_models:
                combo.addItems(role_models)
                combo.setCurrentText(role_model)
            jury_combos[role] = combo
            jury_role_labels[role] = QLabel(role.replace('_', ' ').title() + ":", dialog)

        report_checkbox = QCheckBox("Generate Report", dialog)
        batch_size_spin = QSpinBox(dialog)
        batch_size_spin.setRange(1, 200)
        batch_auto_checkbox = QCheckBox("Auto", dialog)
        grading_mode_combo = QComboBox(dialog)
        grading_mode_combo.addItems(["Whole Form", "Recent Only"])
        execution_mode_combo = QComboBox(dialog)
        execution_mode_combo.addItems(list(EXECUTION_MODE_PRESETS.keys()))
        jury_status_label = QLabel(dialog)
        jury_status_label.setWordWrap(True)

        def active_roles_for_mode(mode_name):
            mode_name = normalize_execution_mode(mode_name)
            preset = EXECUTION_MODE_PRESETS.get(mode_name, {})
            roles = preset.get("active_judge_roles", cfg.get("active_judge_roles", []))
            if not isinstance(roles, list) or not roles:
                roles = list(jury_defaults.keys())
            return {role for role in roles if role in jury_defaults}

        def refresh_jury_status(mode_name=None):
            mode = mode_name or execution_mode_combo.currentText()
            active_roles = active_roles_for_mode(mode)
            preset = EXECUTION_MODE_PRESETS.get(normalize_execution_mode(mode), {})
            adaptive = preset.get("adaptive_math_jury", cfg.get("adaptive_math_jury", {}))
            primary_roles = list(adaptive.get("primary_roles", [])) if adaptive.get("enabled", False) else []
            adjudicator_role = str(adaptive.get("adjudicator_role", ""))
            visible_jury_roles = {"semantic_judge", "factual_judge", "concept_judge", "strict_judge"}
            status_text = (
                f"{len(active_roles & visible_jury_roles)} active jury roles."
            )
            if len(primary_roles) >= 3 and adjudicator_role:
                status_text += (
                    f" Flow: {jury_combos[primary_roles[0]].currentText()} evaluates; "
                    f"{jury_combos[primary_roles[1]].currentText()} verifies; "
                    f"{jury_combos[primary_roles[2]].currentText()} challenges completeness; "
                    f"{jury_combos[adjudicator_role].currentText()} adjudicates when needed."
                )
            jury_status_label.setText(status_text)
            for role, label in jury_role_labels.items():
                active = role in active_roles
                assignment = ""
                if role in primary_roles:
                    position = primary_roles.index(role)
                    assignment = (
                        "meaning evaluator" if position == 0 else
                        "independent verifier" if position == 1 else
                        "completeness challenge"
                    )
                elif role == adjudicator_role:
                    assignment = "conditional adjudicator"
                label.setText(
                    f"{role.replace('_', ' ').title()} "
                    f"({assignment or ('active' if active else 'inactive')}):"
                )
                label.setStyleSheet("" if active else "color: #777;")

        for combo in jury_combos.values():
            combo.currentTextChanged.connect(lambda _text: refresh_jury_status())

        # Heartbeat monitor settings
        heartbeat_timeout_spin = QSpinBox(dialog)
        heartbeat_timeout_spin.setRange(30, 21600)
        heartbeat_timeout_spin.setValue(cfg.get("heartbeat_timeout", 90))
        heartbeat_interval_spin = QSpinBox(dialog)
        heartbeat_interval_spin.setRange(5, 60)
        heartbeat_interval_spin.setValue(cfg.get("heartbeat_interval", 10))
        heartbeat_max_restarts_spin = QSpinBox(dialog)
        heartbeat_max_restarts_spin.setRange(1, 10)
        heartbeat_max_restarts_spin.setValue(cfg.get("heartbeat_max_restarts", 5))

        # Ollama options
        judge_num_ctx_spin = QSpinBox(dialog)
        judge_num_ctx_spin.setRange(512, 8192)
        judge_num_ctx_spin.setValue(cfg.get("ollama_options", {}).get("judge_num_ctx", 2048))
        judge_num_predict_spin = QSpinBox(dialog)
        judge_num_predict_spin.setRange(64, 4096)
        judge_num_predict_spin.setValue(cfg.get("ollama_options", {}).get("judge_num_predict", 256))

        ev = cfg.get("evaluator", "ai_evaluator")
        evaluator_combo.setCurrentIndex(0 if ev == "ai_evaluator" else (2 if ev == "ai_evaluator_semantic" else 1))
        leniency_combo.setCurrentText(cfg.get("leniency", "lenient"))
        if models:
            model_combo.setCurrentText(models[0])
        embedding_model_combo.setCurrentText(cfg.get("embedding_model", DEFAULT_CONFIG.get("embedding_model", "")))
        reasoning_model_combo.setCurrentText(cfg.get("reasoning_model", DEFAULT_CONFIG.get("reasoning_model", "")))
        accuracy_cfg = cfg.get("accuracy_policy", {})
        minimum_judge_confidence_spin.setValue(float(accuracy_cfg.get("minimum_judge_confidence", 0.90)))
        distinct_models_checkbox.setChecked(bool(accuracy_cfg.get("require_distinct_models", True)))
        key_auto_add_checkbox.setChecked(bool(cfg.get("answer_key_auto_add_proven_equivalents", True)))
        patient_ai_checkbox.setChecked(bool(cfg.get("patient_ai_mode", True)))
        audit_path_edit.setText(str(cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")))
        benchmark_path_edit.setText(str(cfg.get("teacher_benchmark_path", "teacher_benchmark.jsonl")))
        report_checkbox.setChecked(bool(cfg.get("generate_report", True)))
        batch_size = cfg.get("batch_size", 32)
        if isinstance(batch_size, str) and batch_size.lower() == "auto":
            batch_auto_checkbox.setChecked(True)
            batch_size_spin.setEnabled(False)
            batch_size_spin.setValue(32)
        else:
            batch_size_spin.setValue(int(batch_size) if isinstance(batch_size, int) and batch_size > 0 else 32)
        batch_auto_checkbox.stateChanged.connect(lambda s: batch_size_spin.setEnabled(s != Qt.Checked))

        # Set Grade Mode from config
        grading_mode_combo.setCurrentText(cfg.get("grading_mode", "Whole Form"))
        execution_mode_combo.setCurrentText(
            normalize_execution_mode(cfg.get("execution_mode", DEFAULT_EXECUTION_MODE))
        )

        form.addRow("Model Choices:", model_status_label)
        # Keep every active model assignment together; disabled/internal roles stay hidden.
        for role in ("semantic_judge", "factual_judge", "concept_judge", "strict_judge"):
            form.addRow(jury_role_labels[role], jury_combos[role])
        form.addRow("Jury Roles:", jury_status_label)
        form.addRow("Minimum Judge Confidence:", minimum_judge_confidence_spin)
        form.addRow("Answer-Key Automation:", key_auto_add_checkbox)
        form.addRow("Slow Model Handling:", patient_ai_checkbox)
        form.addRow("", report_checkbox)
        form.addRow("Grade Mode:", grading_mode_combo)
        execution_mode_combo.currentTextChanged.connect(refresh_jury_status)
        refresh_jury_status()

        ignore_cache_checkbox = QCheckBox("Always grade from fresh data (ignore previous-run cache)", dialog)
        ignore_cache_checkbox.setChecked(bool(cfg.get("ignore_grading_cache", True)))
        ignore_cache_checkbox.setToolTip(
            "Before every grading run, remove cached results, rubrics, embeddings, context, "
            "validation data, Recent Only history, and pending Answer Keys reviews. "
            "Caching is still allowed within that run."
        )
        form.addRow("Cache Reuse:", ignore_cache_checkbox)

        force_ai_checkbox = QCheckBox("Send every answer through the full AI jury", dialog)
        force_ai_checkbox.setChecked(bool(cfg.get("force_ai_jury_for_all_answers", True)))
        force_ai_checkbox.setToolTip(
            "Mistral NeMo evaluates meaning, Gemma verifies facts/mathematics, and Phi-4 "
            "challenges completeness. GPT-OSS adjudicates disagreements, ambiguity, invalid output, or low confidence."
        )
        form.addRow("AI Evaluation:", force_ai_checkbox)

        buttons = QWidget(dialog)
        b = QHBoxLayout(buttons)
        b.setContentsMargins(0, 0, 0, 0)
        save_btn = QPushButton("Save", dialog)
        cancel_btn = QPushButton("Cancel", dialog)
        clear_cache_btn = QPushButton("Clear Cache & Grading History", dialog)
        clear_cache_btn.setObjectName("Danger")

        def clear_cache_now():
            answer = QMessageBox.question(
                dialog,
                "Clear grading cache?",
                "This clears regenerated model/context caches, Recent Only grading history, and all "
                "pending Answer Keys review candidates. The next run will fetch and grade everything again. "
                "Credentials, teacher benchmarks, backups, configuration, and form lists are preserved.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                self.clear_all_forms(confirm=False)
                result = clear_grading_cache(reset_history=True)
                megabytes = result["removed_bytes"] / (1024 * 1024)
                QMessageBox.information(
                    dialog,
                    "Cache cleared",
                    f"Removed {result['removed_files']} cached files ({megabytes:.1f} MB). "
                    f"Removed {result['review_records_removed']} pending review records. "
                    "The next grading run will start completely fresh.",
                )
            except Exception as exc:
                QMessageBox.critical(dialog, "Could not clear cache", str(exc))

        clear_cache_btn.clicked.connect(clear_cache_now)
        save_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        b.addWidget(clear_cache_btn)
        b.addStretch()
        b.addWidget(save_btn)
        b.addWidget(cancel_btn)
        form.addRow("", buttons)

        if dialog.exec_() == QDialog.Accepted:
            # Read existing config first to preserve other fields
            config_data = {}
            if os.path.exists("config.json"):
                try:
                    with open("config.json", "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                except Exception as e:
                    print(f"Error loading config: {e}")

            # Update fields
            eval_text = evaluator_combo.currentText()
            if "Semantic Pipeline" in eval_text:
                config_data["evaluator"] = "ai_evaluator_semantic"
            elif "Basic" in eval_text:
                config_data["evaluator"] = "ai_evaluator"
            else:
                config_data["evaluator"] = "ai_evaluator_2"

            config_data["leniency"] = leniency_combo.currentText()

            if model_combo.currentText():
                config_data["models"] = {"judge": [model_combo.currentText()]}
            if embedding_model_combo.currentText():
                config_data["embedding_model"] = embedding_model_combo.currentText()
            if reasoning_model_combo.currentText():
                config_data["reasoning_model"] = reasoning_model_combo.currentText()
            for obsolete_key in (
                "rubric_model", "validate_expected_answers", "expected_answer_validation_optional",
                "expected_answer_validator_model", "expected_answer_validator_fallback_model",
                "expected_answer_validator_timeout_seconds", "expected_answer_validator_fallback_timeout_seconds",
                "expected_answer_validator_connect_timeout_seconds", "expected_answer_validator_min_confidence",
                "use_validated_expected_for_grading", "auto_replace_invalid_expected",
                "invalid_expected_blocks_updates", "rubric_timeout_seconds",
            ):
                config_data.pop(obsolete_key, None)
            accuracy_policy = dict(config_data.get("accuracy_policy", {}))
            accuracy_policy.update({
                "enabled": True,
                "minimum_judge_confidence": float(minimum_judge_confidence_spin.value()),
                "required_accept_roles": ["semantic_judge", "factual_judge", "concept_judge"],
                "require_distinct_models": distinct_models_checkbox.isChecked(),
                "embeddings_can_accept": False,
                "ambiguous_outcome": "REVIEW",
            })
            config_data["accuracy_policy"] = accuracy_policy
            config_data["answer_key_auto_add_proven_equivalents"] = key_auto_add_checkbox.isChecked()
            config_data["ignore_grading_cache"] = ignore_cache_checkbox.isChecked()
            config_data["force_ai_jury_for_all_answers"] = force_ai_checkbox.isChecked()
            config_data["patient_ai_mode"] = patient_ai_checkbox.isChecked()
            config_data["enable_jury_circuit_breaker"] = not patient_ai_checkbox.isChecked()
            config_data["decision_audit_path"] = audit_path_edit.text().strip() or "logs/grading_decisions.jsonl"
            config_data["teacher_benchmark_path"] = benchmark_path_edit.text().strip() or "teacher_benchmark.jsonl"

            # Save jury model selections
            selected_jury = {}
            for role, combo in jury_combos.items():
                try:
                    sel = combo.currentText()
                except Exception:
                    sel = jury_defaults.get(role)
                if sel:
                    selected_jury[role] = sel
            if selected_jury:
                config_data["jury_models"] = selected_jury

            config_data["generate_report"] = report_checkbox.isChecked()

            if batch_auto_checkbox.isChecked():
                config_data["batch_size"] = "auto"
            else:
                config_data["batch_size"] = int(batch_size_spin.value())

            config_data["grading_mode"] = grading_mode_combo.currentText()
            selected_mode = execution_mode_combo.currentText()
            config_data["execution_mode"] = selected_mode

            # Apply execution preset knobs that control concurrency/timeout behavior.
            preset = EXECUTION_MODE_PRESETS.get(selected_mode, EXECUTION_MODE_PRESETS[DEFAULT_EXECUTION_MODE])
            for key, value in preset.items():
                config_data[key] = value
            # User-facing accuracy controls override the preset defaults.
            config_data["accuracy_policy"]["minimum_judge_confidence"] = float(minimum_judge_confidence_spin.value())
            config_data["accuracy_policy"]["require_distinct_models"] = distinct_models_checkbox.isChecked()
            if isinstance(config_data.get("adaptive_math_jury"), dict):
                config_data["adaptive_math_jury"]["minimum_primary_confidence"] = float(minimum_judge_confidence_spin.value())
            config_data["answer_key_auto_add_proven_equivalents"] = key_auto_add_checkbox.isChecked()
            config_data["ignore_grading_cache"] = ignore_cache_checkbox.isChecked()
            config_data["force_ai_jury_for_all_answers"] = force_ai_checkbox.isChecked()
            config_data["patient_ai_mode"] = patient_ai_checkbox.isChecked()
            config_data["enable_jury_circuit_breaker"] = not patient_ai_checkbox.isChecked()
            # Prevent stale mode-only knobs from previous selection.
            if "active_judge_roles" not in preset:
                config_data["active_judge_roles"] = [
                    "semantic_judge",
                    "factual_judge",
                    "concept_judge",
                    "strict_judge",
                    "misconception_judge",
                    "language_filter",
                ]
            if "judge_prewarm_enabled" not in preset:
                config_data["judge_prewarm_enabled"] = False

            # Save Heartbeat monitor settings
            config_data["heartbeat_timeout"] = heartbeat_timeout_spin.value()
            config_data["heartbeat_interval"] = heartbeat_interval_spin.value()
            config_data["heartbeat_max_restarts"] = heartbeat_max_restarts_spin.value()

            # Save Ollama options
            ollama_options = config_data.get("ollama_options", {})
            ollama_options["judge_num_ctx"] = judge_num_ctx_spin.value()
            ollama_options["judge_num_predict"] = judge_num_predict_spin.value()
            ollama_options.pop("rubric_num_ctx", None)
            ollama_options.pop("rubric_num_predict", None)
            config_data["ollama_options"] = ollama_options

            # Save the updated grading mode to self
            self.grading_mode = config_data["grading_mode"]

            # Write config.json in a single atomic write operation
            try:
                with open("config.json", "w", encoding="utf-8") as f:
                    json.dump(config_data, f, indent=4)
            except Exception as e:
                QMessageBox.critical(self, "Error Saving Settings", f"Failed to save settings: {str(e)}")

    def load_forms(self):
        try:
            with open("forms_to_grade.json", "r") as f:
                data = json.load(f)
                form_urls = data.get("forms", [])
                for position, form in enumerate(form_urls, start=1):
                    url = form.get("url") if isinstance(form, dict) else form
                    title = form.get("title", "Untitled") if isinstance(form, dict) else "Untitled"
                    self._add_form_to_queue(url, title, position=position)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save_forms(self):
        forms = [{"url": url, "title": self.forms_data[url]} for url in self.forms_data]
        with open("forms_to_grade.json", "w") as f:
            json.dump({"forms": forms}, f)

    def _short_url(self, url):
        if not url:
            return "-"
        clean = url.replace("https://", "").replace("http://", "")
        return clean if len(clean) <= 78 else clean[:75] + "..."

    def _form_meta(self, url, title, status="queued", position=None, source=None, last_submission=None):
        form_id = self.extract_form_id(url) or "unknown"
        return {
            "url": url,
            "title": title or "Untitled",
            "form_id": form_id,
            "status": status,
            "position": position,
            "source": source or "Queue",
            "last_submission": last_submission,
            "started_at": None,
            "finished_at": None,
            "detail": "Waiting for its turn",
        }

    def _status_label(self, status):
        return {
            "queued": "QUEUED",
            "running": "RUNNING",
            "done": "DONE",
            "failed": "FAILED",
        }.get(status, str(status).upper())

    def _format_form_meta_line(self, meta):
        parts = []
        if meta.get("position"):
            parts.append(f"#{meta.get('position')}")
        parts.append(f"ID: {meta.get('form_id') or 'unknown'}")
        if meta.get("source"):
            parts.append(f"Source: {meta.get('source')}")
        if meta.get("last_submission"):
            parts.append(f"Last submission: {meta.get('last_submission')}")
        if meta.get("started_at"):
            parts.append(f"Started: {meta.get('started_at')}")
        if meta.get("finished_at"):
            parts.append(f"Finished: {meta.get('finished_at')}")
        return "  |  ".join(parts)

    def _make_form_row_widget(self, meta):
        card = QFrame()
        card.setObjectName("FormCard")
        card.setProperty("status", meta.get("status", "queued"))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(5)

        top = QHBoxLayout()
        title = QLabel(meta.get("title", "Untitled"))
        title.setObjectName("FormTitle")
        title.setWordWrap(True)
        badge = QLabel(self._status_label(meta.get("status", "queued")))
        badge.setObjectName("StatusBadge")
        badge.setProperty("status", meta.get("status", "queued"))
        badge.setAlignment(Qt.AlignCenter)
        top.addWidget(title, 1)
        top.addWidget(badge, 0, Qt.AlignTop)

        meta_label = QLabel(self._format_form_meta_line(meta))
        meta_label.setObjectName("FormMeta")
        meta_label.setWordWrap(True)
        detail = QLabel(meta.get("detail", "Waiting for its turn"))
        detail.setObjectName("FormMeta")
        detail.setWordWrap(True)
        url_label = QLabel(self._short_url(meta.get("url", "")))
        url_label.setObjectName("FormUrl")
        url_label.setWordWrap(True)

        layout.addLayout(top)
        layout.addWidget(meta_label)
        layout.addWidget(detail)
        layout.addWidget(url_label)
        card._title_label = title
        card._badge_label = badge
        card._meta_label = meta_label
        card._detail_label = detail
        card._url_label = url_label
        return card

    def _refresh_form_row(self, item):
        meta = item.data(Qt.UserRole + 1) or {}
        widget = self.form_list.itemWidget(item)
        if not widget:
            old_meta = meta
            url = meta.get("url") or item.data(Qt.UserRole) or ""
            title = meta.get("title") or self.forms_data.get(url) or self._title_from_legacy_item(item, url)
            meta = self._form_meta(
                url,
                title,
                status=old_meta.get("status", "queued"),
                position=old_meta.get("position"),
                source=old_meta.get("source", "Queue"),
                last_submission=old_meta.get("last_submission"),
            )
            meta["started_at"] = old_meta.get("started_at")
            meta["finished_at"] = old_meta.get("finished_at")
            meta["detail"] = old_meta.get("detail", "Waiting for its turn")
            item.setData(Qt.UserRole, url)
            item.setData(Qt.UserRole + 1, meta)
            item.setText("")
            widget = self._make_form_row_widget(meta)
            self.form_list.setItemWidget(item, widget)
        status = meta.get("status", "queued")
        widget.setProperty("status", status)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget._title_label.setText(meta.get("title", "Untitled"))
        widget._badge_label.setText(self._status_label(status))
        widget._badge_label.setProperty("status", status)
        widget._badge_label.style().unpolish(widget._badge_label)
        widget._badge_label.style().polish(widget._badge_label)
        widget._meta_label.setText(self._format_form_meta_line(meta))
        widget._detail_label.setText(meta.get("detail", "Waiting for its turn"))
        widget._url_label.setText(self._short_url(meta.get("url", "")))
        item.setSizeHint(QSize(0, max(104, widget.sizeHint().height())))

    def _title_from_legacy_item(self, item, url):
        text = (item.text() or "").strip()
        if text and not text[0].isalnum():
            text = text[1:].strip()
        if " (Last submission:" in text:
            text = text.split(" (Last submission:", 1)[0].strip()
        elif "http" in text:
            text = text[:text.find("http")].strip(" -")
        return text or self.forms_data.get(url) or "Untitled"

    def _is_placeholder_form_title(self, title):
        return str(title or "").strip().lower() in {"", "form", "untitled"}

    def _add_form_to_queue(self, url, title, source="Queue", last_submission=None, position=None):
        if not url:
            return None
        if url in self.forms_data:
            item = self._find_form_item_by_url(url)
            incoming_title = title or "Untitled"
            if item:
                meta = item.data(Qt.UserRole + 1) or {}
                current_title = meta.get("title") or self.forms_data.get(url)
                if not self._is_placeholder_form_title(incoming_title) and incoming_title != current_title:
                    meta["title"] = incoming_title
                    self.forms_data[url] = incoming_title
                if source:
                    meta["source"] = source
                if last_submission:
                    meta["last_submission"] = last_submission
                item.setData(Qt.UserRole + 1, meta)
                self._refresh_form_row(item)
            elif not self._is_placeholder_form_title(incoming_title):
                self.forms_data[url] = incoming_title
            return item
        self.forms_data[url] = title or "Untitled"
        meta = self._form_meta(
            url,
            title,
            position=position or self.form_list.count() + 1,
            source=source,
            last_submission=last_submission,
        )
        item = QListWidgetItem()
        item.setData(Qt.UserRole, url)
        item.setData(Qt.UserRole + 1, meta)
        item.setText("")
        widget = self._make_form_row_widget(meta)
        item.setSizeHint(QSize(0, 104))
        self.form_list.addItem(item)
        self.form_list.setItemWidget(item, widget)
        self._refresh_queue_positions()
        return item

    def _find_form_item_by_url(self, url):
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            if item.data(Qt.UserRole) == url:
                return item
        return None

    def _find_form_item_by_id(self, form_id):
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            meta = item.data(Qt.UserRole + 1) or {}
            if meta.get("form_id") == form_id:
                return item
        return None

    def _set_form_status(self, item, status, detail=None):
        if not item:
            return
        meta = item.data(Qt.UserRole + 1) or {}
        meta["status"] = status
        if detail:
            meta["detail"] = detail
        now = datetime.now().strftime("%H:%M:%S")
        if status == "running" and not meta.get("started_at"):
            meta["started_at"] = now
        if status in {"done", "failed"}:
            meta["finished_at"] = now
        item.setData(Qt.UserRole + 1, meta)
        self._refresh_form_row(item)
        self._refresh_queue_positions()

    def _refresh_queue_positions(self):
        counts = {"queued": 0, "running": 0, "done": 0, "failed": 0}
        total = self.form_list.count()
        for i in range(total):
            item = self.form_list.item(i)
            meta = item.data(Qt.UserRole + 1) or {}
            meta["position"] = i + 1
            status = meta.get("status", "queued")
            counts[status] = counts.get(status, 0) + 1
            item.setData(Qt.UserRole + 1, meta)
            self._refresh_form_row(item)
        if hasattr(self, "form_queue_summary"):
            active = counts.get("queued", 0) + counts.get("running", 0)
            self.form_queue_summary.setText(f"{active} in queue")
            self.form_queue_summary.setToolTip(
                f"{counts.get('queued', 0)} queued · {counts.get('running', 0)} running · "
                f"{counts.get('done', 0)} done · {counts.get('failed', 0)} failed"
            )
        if hasattr(self, "command_summary"):
            self.command_summary.setText(f"{total} form{'s' if total != 1 else ''} · {counts.get('done', 0)} completed")
        if hasattr(self, "in_queue_label"):
            self.in_queue_label.setText(f"In Queue: {counts.get('queued', 0)}")
        if hasattr(self, "form_filter_combo"):
            self._filter_form_queue()
        current = self.form_list.currentItem()
        if current:
            self._on_form_selection_changed(current)

    def remove_form(self):
        selected_items = self.form_list.selectedItems()
        for item in selected_items:
            url = item.data(Qt.UserRole)
            if url in self.forms_data:
                del self.forms_data[url]
            self.form_list.takeItem(self.form_list.row(item))
        self.save_forms()
        self._refresh_queue_positions()

    def clear_all_forms(self, confirm=False):
        if confirm:
            reply = QMessageBox.question(self, "Clear All", "Clear all forms?", QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        self.form_list.clear()
        self.forms_data.clear()
        self.save_forms()
        self._refresh_queue_positions()

    def clear_finished_forms_silently(self):
        i = 0
        while i < self.form_list.count():
            item = self.form_list.item(i)
            meta = item.data(Qt.UserRole + 1) or {}
            if meta.get("status") == "done":
                self.form_list.takeItem(i)
                url = item.data(Qt.UserRole)
                if url in self.forms_data:
                    del self.forms_data[url]
            else:
                i += 1
        self.save_forms()
        self._refresh_queue_positions()

    def open_manual_add_dialog(self):
        dialog = AutoAddDialog(self, mode='manual')
        dialog.exec_()

    def open_answer_key_dashboard(self, target_url=None, auto_scan=False):
        if isinstance(target_url, bool):
            target_url = None
        dialog = AnswerKeyDashboard(dict(self.forms_data), self)
        if target_url:
            index = dialog.form_combo.findData(target_url)
            if index >= 0:
                dialog.form_combo.setCurrentIndex(index)
        if auto_scan:
            QTimer.singleShot(0, dialog.scan)
        dialog.exec_()

        if self.form_list.currentItem():
            self._on_form_selection_changed(self.form_list.currentItem())

    def open_current_form_review(self, _link="review"):
        target = self.current_form_url
        if not target and self.form_list.currentItem():
            target = self.form_list.currentItem().data(Qt.UserRole)
        self.open_answer_key_dashboard(target_url=target, auto_scan=True)

    def open_auto_run_dialog(self):
        dialog = AutoAddDialog(self, mode='auto')
        dialog.exec_()

    def open_quick_grade_dialog(self):
        """Open a dialog to add folder/form URLs and grade immediately without checking submissions."""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel

        dialog = QDialog(self)
        dialog.setWindowTitle("Scan Source")
        dialog.setGeometry(100, 100, 620, 260)

        layout = QVBoxLayout()

        label = QLabel("Google Form or Drive folder URLs")
        layout.addWidget(label)

        input_field = QTextEdit()
        input_field.setPlaceholderText("One URL per line, or separate URLs with commas...")
        input_field.setFixedHeight(110)
        layout.addWidget(input_field)

        button_layout = QHBoxLayout()
        add_button = QPushButton("Scan and Add to Queue")
        grade_button = QPushButton("Scan and Grade")
        cancel_button = QPushButton("Cancel")

        button_layout.addWidget(add_button)
        button_layout.addWidget(grade_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)

        action = [None]

        def on_add():
            action[0] = "add"
            dialog.accept()

        def on_grade():
            action[0] = "grade"
            dialog.accept()

        add_button.clicked.connect(on_add)
        grade_button.clicked.connect(on_grade)
        cancel_button.clicked.connect(dialog.reject)

        if dialog.exec_() == QDialog.Accepted:
            sources_text = input_field.toPlainText().strip()
            if not sources_text:
                QMessageBox.warning(self, "Empty Input", "Please enter at least one URL")
                return

            # Split input into multiple source URLs (commas or newlines)
            parts = [p.strip() for p in re.split('[,\n\r]+', sources_text) if p.strip()]
            if not parts:
                QMessageBox.warning(self, "Empty Input", "Please enter at least one URL")
                return

            # Record existing forms to compute newly added ones
            before = set(self.forms_data.keys())
            for src in parts:
                try:
                    # Do not start grading yet for each; defer to a single run later
                    self.grade_url_immediately(src, start_grading=False)
                except Exception as e:
                    self.append_debug(f"[GRADE NOW] Failed to add source {src}: {e}")

            # Persist and update UI
            self.update_in_queue_label()
            self.save_forms()

            # Determine newly added form URLs
            after = set(self.forms_data.keys())
            new_urls = list(after - before)

            if action[0] == "grade":
                if not new_urls:
                    QMessageBox.information(self, "No New Forms", "No new forms were found to grade.")
                    return
                # Start grading only the newly added forms
                self.run_grader(target_urls=new_urls)
            else:
                self.append_debug(f"✅ Scan/Add: Added {len(new_urls)} new form(s) to queue")

    def update_evaluator(self, text):
        if "Semantic Pipeline" in text:
            evaluator = "ai_evaluator_semantic"
        elif "Basic" in text:
            evaluator = "ai_evaluator"
        else:
            evaluator = "ai_evaluator_2"
        self.update_config("evaluator", evaluator)

    def update_leniency(self, text):
        self.update_config("leniency", text)

    def update_model(self, text):
        self.update_config("models", {"judge": [text]})

    def update_report_option(self, state):
        self.update_config("generate_report", state == Qt.Checked)

    def update_batch_size(self, value):
        if hasattr(self, "batch_auto_checkbox") and not self.batch_auto_checkbox.isChecked():
            self.update_config("batch_size", int(value))

    def update_batch_auto(self, state):
        is_auto = state == Qt.Checked
        if hasattr(self, "batch_size_spin"):
            self.batch_size_spin.setEnabled(not is_auto)
        if is_auto:
            self.update_config("batch_size", "auto")
        else:
            if hasattr(self, "batch_size_spin"):
                self.update_config("batch_size", int(self.batch_size_spin.value()))

    def update_config(self, key, value):
        try:
            config = {}
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    config = json.load(f)
            config[key] = value
            with open("config.json", "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except Exception:
            pass

    def load_config(self):
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    config = json.load(f)
            else:
                config = {}
            
            # Ensure default settings are written if not present
            modified = False
            if "batch_size" not in config:
                config["batch_size"] = 32
                modified = True
            if "grading_mode" not in config:
                config["grading_mode"] = "Whole Form"
                modified = True
            
            if modified:
                with open("config.json", "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4)
                    
            self.grading_mode = config.get("grading_mode", "Whole Form")
        except Exception as e:
            print(f"Error loading config: {e}")
            self.grading_mode = "Whole Form"

    def grade_url_immediately(self, url, start_grading=True):
        """Grade a folder or form URL immediately without checking last submissions"""
        try:
            forms = find_all_forms_in_sources(
                url,
                progress_callback=lambda msg: self.append_debug(f"[GRADE NOW] {msg}")
            )

            if not forms:
                QMessageBox.warning(self, "No Forms Found", "Could not find any accessible forms at that URL")
                return

            new_added = 0
            for form_data in forms:
                form_url = form_data.get("url")
                form_title = form_data.get("title", "Untitled")
                if not form_url:
                    continue

                if form_url not in self.forms_data:
                    self._add_form_to_queue(form_url, form_title, source="Grade Now")
                    new_added += 1

            self.append_debug(
                f"✅ Grade Now: Found {len(forms)} form(s), added {new_added} new form(s) to queue"
            )

            self.update_in_queue_label()
            self.save_forms()
            self.grading_mode = "Whole Form"
            if start_grading:
                self.run_grader()
            return

            from datetime import datetime, timezone, timedelta
            
            # Check if it's a direct form URL or a folder URL
            if '/forms/d/' in url:
                # Direct form URL - extract form ID
                form_id = url.split('/forms/d/')[1].split('/')[0]
                form_url = f"https://docs.google.com/forms/d/{form_id}/edit"
                
                # Add directly without searching
                if form_url not in self.forms_data:
                    self._add_form_to_queue(form_url, "Form", source="Direct URL")
                
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
                        self._add_form_to_queue(form_url, form_title, source="Folder Search")
                
                self.append_debug(f"✅ Found {len(folder_ids)} forms in folder")
            
            self.update_in_queue_label()
            self.save_forms()
            
            # Set to "Whole Form" mode for immediate grading
            self.grading_mode = "Whole Form"
            
            # Start grading immediately
            self.run_grader()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to process URL: {str(e)}")
            self.append_debug(f"❌ Error: {str(e)}")

    def grade_all_forms_in_all_folders(self):
        """Find and grade all forms from all predefined folders/forms, ignoring date windows."""
        try:
            folders = load_predefined_folders()
            if not folders:
                QMessageBox.warning(
                    self,
                    "No Predefined Sources",
                    "Add folders or form URLs in Auto Find first, then use Grade All.",
                )
                return

            self.append_debug(f"📚 Grade All: Searching all forms in {len(folders)} source(s)")
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
                    "No accessible forms with responses were found in your predefined folders/forms.",
                )
                return

            new_added = 0
            for form in forms:
                form_url = form.get("url")
                form_title = form.get("title", "Untitled")
                if form_url and form_url not in self.forms_data:
                    self._add_form_to_queue(form_url, form_title, source="Grade All")
                    new_added += 1

            self.save_forms()
            self.update_in_queue_label()
            self.append_debug(
                f"✅ Grade All: Found {len(forms)} form(s), added {new_added} new form(s) to queue"
            )

            self.grading_mode = "Whole Form"
            self.run_grader()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Grade All failed: {str(e)}")
            self.append_debug(f"❌ Grade All failed: {str(e)}")

    def update_in_queue_label(self):
        queued = 0
        for i in range(self.form_list.count()):
            meta = self.form_list.item(i).data(Qt.UserRole + 1) or {}
            if meta.get("status", "queued") == "queued":
                queued += 1
        self.in_queue_label.setText(f"In Queue: {queued}")
        self._refresh_queue_positions()

    def _get_next_run_time(self):
        """Calculate the next run time based on schedule settings"""
        now = datetime.now(timezone.utc)
        current_day = now.weekday()  # 0=Monday, 6=Sunday
        
        # Get the target time
        if self.schedule_time_val:
            target_hour = self.schedule_time_val.hour()
            target_minute = self.schedule_time_val.minute()
        else:
            target_hour, target_minute = 9, 0  # Default to 9:00 AM
        
        # Find next day that matches the schedule
        for days_ahead in range(8):  # Check next 8 days
            test_day = (current_day + days_ahead) % 7
            if self.selected_days[test_day]:  # If this day is selected
                next_run = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
                # Add days_ahead to the date
                next_run = next_run + timedelta(days=days_ahead)
                if next_run > now:  # Only return if in the future
                    return next_run
        
        # Fallback to interval-based if no valid time found
        return now + timedelta(seconds=10)

    def _should_run_now(self):
        """Check if we should run based on current time and day"""
        now = datetime.now(timezone.utc)
        current_day = now.weekday()  # 0=Monday, 6=Sunday
        current_time = time(now.hour, now.minute)
        
        # Check if today is selected
        if not self.selected_days[current_day]:
            return False
        
        # Check if we're within the scheduled time window (within 5 minutes of target time)
        if self.schedule_time_val:
            target_hour = self.schedule_time_val.hour()
            target_minute = self.schedule_time_val.minute()
            target_time = time(target_hour, target_minute)
            
            # Allow a 5-minute window around the target time
            time_diff = abs((current_time.hour * 60 + current_time.minute) - 
                          (target_time.hour * 60 + target_time.minute))
            return time_diff <= 5
        
        # If no specific time set, allow running
        return True

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

        # Start the APScheduler-based scheduler
        if self.use_time_schedule:
            # Time-based scheduling
            self._start_time_scheduler()
        else:
            # Interval-based scheduling using QTimer
            self.append_debug("<font color='blue'>[AUTO] Using interval-based scheduling</font>")
            self.schedule_next_cycle()

    def _start_time_scheduler(self):
        """Start time-based scheduling with APScheduler"""
        self.append_debug(f"<font color='blue'>[AUTO] Starting time-based scheduler</font>")
        self.append_debug(f"<font color='blue'>[AUTO] Time: {self.schedule_time_val.toString('HH:mm')}, Days: {[i for i, d in enumerate(self.selected_days) if d]}</font>")
        
        # Calculate initial delay until next scheduled time
        next_run = self._get_next_run_time()
        delay_seconds = max(10, (next_run - datetime.now(timezone.utc)).total_seconds())
        
        self.append_debug(f"<font color='blue'>[AUTO] Next scheduled run in {delay_seconds:.0f} seconds</font>")

        # Start the scheduler with the time-based job
        auto_scheduler.start(
            interval_minutes=self.interval_seconds // 60,
            folders=self.folders,
            recency_minutes=self.recency_minutes,
            grade_recent_only=(self.grading_mode == "Recent Only")
        )

    def auto_cycle(self):
        """Perform one auto-cycle: search for new forms, add them, and grade"""
        if not self.auto_mode or self.is_closing:
            return

        # Check if we should run based on time/day schedule
        if self.use_time_schedule:
            if not self._should_run_now():
                next_run = self._get_next_run_time()
                delay = (next_run - datetime.now(timezone.utc)).total_seconds()
                self.append_debug(f"<font color='gray'>[AUTO] Skipping cycle - next scheduled run in {delay/60:.1f} minutes</font>")
                self.schedule_next_cycle()
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
            self._add_form_to_queue(url, title, source="Auto Find", last_submission=last_str)
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
        
        if self.use_time_schedule:
            # Time-based scheduling
            next_run = self._get_next_run_time()
            delay_seconds = max(10, (next_run - datetime.now(timezone.utc)).total_seconds())
            next_str = next_run.strftime("%a %H:%M:%S")
            self.append_debug(f"<font color='gray'>[AUTO] ⏰ Next scheduled run in {delay_seconds:.0f}s at {next_str}</font>")
            
            # Schedule using QTimer for the delay
            if self.auto_timer:
                self.auto_timer.stop()
                self.auto_timer.deleteLater()
            
            self.auto_timer = QTimer()
            self.auto_timer.setSingleShot(True)
            self.auto_timer.timeout.connect(self._on_scheduler_timeout)
            self.auto_timer.start(int(delay_seconds * 1000))
        else:
            # Interval-based scheduling using QTimer
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

    def _on_scheduler_timeout(self):
        """Called when scheduler delay completes - triggers the cycle"""
        if self.use_time_schedule:
            # For time-based scheduling, run the cycle directly
            self.append_debug("<font color='blue'>[AUTO] Scheduler timeout reached - starting cycle</font>")
            self.auto_cycle()
        else:
            # For interval-based, use the standard cycle
            self.auto_cycle()

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

        # Stop the APScheduler
        auto_scheduler.stop()

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

    def stop_grading(self):
        """Stop active grading without closing the app."""
        self.auto_mode = False
        self.stop_button.hide()
        self.run_button.setEnabled(True)
        self.append_debug("<b><font color='red'>STOPPING GRADING...</font></b>")

        if self.auto_timer:
            self.auto_timer.stop()
            self.auto_timer.deleteLater()
            self.auto_timer = None
        auto_scheduler.stop()

        if self.auto_search_thread and self.auto_search_thread.isRunning():
            self.auto_search_thread.terminate()
            self.auto_search_thread.wait(3000)

        if self.grader_thread and self.grader_thread.isRunning():
            try:
                self.grader_thread.stop_grading()
            except Exception:
                pass
            if not self.grader_thread.wait(5000):
                self.grader_thread.terminate()
                self.grader_thread.wait(2000)

        self.is_searching = False
        self.is_grading = False
        self.run_state_label.setText("Stopped")

    def _terminate_project_python_processes(self):
        """Terminate python.exe/pythonw.exe instances started from this project path."""
        try:
            current_pid = os.getpid()
            main_path = os.path.abspath("main.py").replace("'", "''")
            ps_script = (
                "$main='" + main_path + "'; "
                f"$self={current_pid}; "
                "Get-CimInstance Win32_Process | "
                "Where-Object {($_.Name -in @('python.exe','pythonw.exe')) -and $_.ProcessId -ne $self -and "
                "$_.CommandLine -like ('*' + $main + '*')} | "
                "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=False)
        except Exception:
            pass

    def exit_app(self):
        """Stop grading/search and close application."""
        self._force_exit = True
        self.close()

    def _shutdown_owned_work(self):
        """Idempotently stop timers, schedulers, threads, and grader children."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.is_closing = True
        self.auto_mode = False

        if self.auto_timer:
            self.auto_timer.stop()
            self.auto_timer.deleteLater()
            self.auto_timer = None
        auto_scheduler.stop()

        # QThread.quit() cannot stop GraderThread.run() while it is blocked reading
        # child stdout. Terminate the owned child tree first, then join the thread.
        if self.grader_thread:
            try:
                self.grader_thread.stop_grading()
            except Exception:
                pass
            if self.grader_thread.isRunning() and not self.grader_thread.wait(7000):
                self.grader_thread.terminate()
                self.grader_thread.wait(2000)

        if self.auto_search_thread and self.auto_search_thread.isRunning():
            self.auto_search_thread.requestInterruption()
            self.auto_search_thread.quit()
            if not self.auto_search_thread.wait(3000):
                self.auto_search_thread.terminate()
                self.auto_search_thread.wait(2000)

        # Final sweep catches a grader spawned during a close/start race.
        self._terminate_project_python_processes()
        if self.tray_icon:
            self.tray_icon.hide()

    def minimize_app(self):
        """Hide the app window to the system tray."""
        if self.tray_icon and self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage(
                "Google Form Autograder",
                "App is running in system tray. Double-click tray icon to restore.",
                QSystemTrayIcon.Information,
                2500,
            )
        else:
            self.showMinimized()

    def run_grader(self, force_recent_only=False, target_urls=None):
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
        self.run_state_label.setText("Running")
        self.run_button.setEnabled(False)
        self.stop_button.show()
        self.debug_output.clear()
        self.debug_lines = []
        self.finished_forms = []
        self.detail_progress.setValue(0)
        self.detail_progress_value.setText("0%")
        self.metric_responses.setText("0 / 0")
        self.metric_accepted.setText("0")
        self.metric_rejected.setText("0")
        self.metric_review.setText('<a href="review">0</a>')
        self.metric_elapsed.setText("00:00")
        self.detail_progress_text.setText("Preparing form and responses")
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            url = item.data(Qt.UserRole)
            if target_urls is not None and url not in target_urls:
                continue
            meta = item.data(Qt.UserRole + 1) or {}
            meta["status"] = "queued"
            meta["started_at"] = None
            meta["finished_at"] = None
            meta["detail"] = "Waiting for its turn"
            item.setData(Qt.UserRole + 1, meta)
            self._refresh_form_row(item)
        self._refresh_queue_positions()
        wp_enabled = False
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                _cfg = json.load(f)
                wp_enabled = bool(_cfg.get("enable_pipeline_workers", False))
        except Exception:
            wp_enabled = False
        self.append_debug(f"<font color='cyan'>[GRADER] Worker pipeline: {'ON' if wp_enabled else 'OFF'}</font>")

        # Optionally truncate answer variants before grading (destructive): keep only first teacher answer
        truncate_enabled = False
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                _cfg2 = json.load(f)
                truncate_enabled = bool(_cfg2.get("truncate_answers_before_grading", False))
        except Exception:
            truncate_enabled = False

        if truncate_enabled:
            try:
                service = get_service()
            except Exception as e:
                self.append_debug(f"<font color='orange'>[GRADER] Could not obtain service to truncate answers: {e}</font>")
                service = None

            # Build list of URLs to truncate: either the provided target_urls or queued items
            urls_to_truncate = list(target_urls) if target_urls else []
            if not urls_to_truncate:
                for i in range(self.form_list.count()):
                    item = self.form_list.item(i)
                    meta = item.data(Qt.UserRole + 1) or {}
                    if meta.get("status") == "queued":
                        urls_to_truncate.append(item.data(Qt.UserRole))

            for url in urls_to_truncate:
                fid = self.extract_form_id(url) or None
                if not fid or not service:
                    continue
                try:
                    result = keep_teacher_answers_only(service, fid, dry_run=False)
                    self.append_debug(f"<font color='cyan'>[GRADER] Truncated answers for {fid}: removed {result.get('removed', 0)} variants</font>")
                except Exception as e:
                    self.append_debug(f"<font color='orange'>[GRADER] Failed to truncate answers for {fid}: {e}</font>")

        grading_mode = self.grading_mode
        grade_recent_only = force_recent_only or (grading_mode == "Recent Only")

        self.grader_thread = GraderThread(grade_recent_only=grade_recent_only, form_urls=target_urls)
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.progress.connect(self.update_progress)
        self.grader_thread.overall_progress.connect(self.update_overall_progress)
        self.grader_thread.form_metrics.connect(self.update_form_metrics)
        self.grader_thread.debug_message.connect(self.append_debug)
        self.grader_thread.current_form.connect(self.update_current_form)
        self.grader_thread.finished_form.connect(self.update_finished_form)
        self.grader_thread.start()

    def update_progress(self, cur, tot):
        if not tot:
            self.detail_progress.setValue(0)
            self.detail_progress_value.setText("0%")
            self.metric_responses.setText("0 / 0")
            self.detail_progress_text.setText("No learner answers to evaluate")
            return
        # Round to the nearest whole percent so small real increments are visible.
        percent = max(0, min(100, int(round((cur / tot) * 100))))
        self.detail_progress.setValue(percent)
        self.detail_progress_value.setText(f"{percent}%")
        self.metric_responses.setText(f"{cur} / {tot}")
        self.detail_progress_text.setText("Evaluating learner answers")

    def update_overall_progress(self, cur, tot):
        if not tot:
            return
        self.in_queue_label.setText(f"In Queue: {max(0, tot - cur)}")
        self.command_summary.setText(f"{tot} forms · {cur} completed")

    def update_form_metrics(self, completed, total, accepted, review_questions, elapsed_seconds, rejected=0):
        self.metric_responses.setText(f"{completed} / {total}")
        self.metric_accepted.setText(str(accepted))
        self.metric_rejected.setText(str(rejected))
        self.metric_review.setText(f'<a href="review">{review_questions}</a>')
        hours, remainder = divmod(max(0, int(elapsed_seconds)), 3600)
        minutes, seconds = divmod(remainder, 60)
        self.metric_elapsed.setText(
            f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
        )

        item = self._find_form_item_by_url(self.current_form_url)
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            meta["completed"] = completed
            meta["total"] = total
            meta["accepted"] = accepted
            meta["rejected"] = rejected
            meta["review_questions"] = review_questions
            meta["elapsed"] = elapsed_seconds
            meta["review_questions"] = review_questions
            item.setData(Qt.UserRole + 1, meta)

    def refresh_review_counts(self, form_id: str = None):
        """Recompute pending review counts for a form and update GUI metrics.

        This is intended to be called after the answer-key dashboard resolves reviews
        so the main window immediately reflects the changed review queue size.
        """
        try:
            fid = form_id or getattr(self, "current_form_url", None)
            if not fid:
                return
            pending = load_pending_review_records(fid) or {}
            # pending is a mapping item_id -> list[records]
            review_count = sum(len(v) for v in pending.values())
            # If the current form matches, update the metrics display
            if getattr(self, "current_form_url", None) == fid:
                try:
                    cur = int(self.detail_progress.text().split("%", 1)[0].strip().rstrip('%'))
                except Exception:
                    cur = 0
                # preserve existing responses display if available
                # update only the review metric
                if hasattr(self, "metric_review"):
                    self.metric_review.setText(f'<a href="review">{review_count}</a>')
        except Exception:
            pass
            item.setData(Qt.UserRole + 1, meta)

    def update_current_form(self, url):
        # Progress belongs to the newly announced form, never the previously
        # selected/completed one.
        self.detail_progress.setValue(0)
        self.detail_progress_value.setText("0%")
        self.metric_responses.setText("0 / 0")
        self.metric_accepted.setText("0")
        self.metric_rejected.setText("0")
        self.metric_review.setText('<a href="review">0</a>')
        self.metric_elapsed.setText("00:00")
        self.detail_progress_text.setText("Preparing form and responses")
        self.current_form_url = url
        item = self._find_form_item_by_url(url)
        title = "Current form"
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            title = meta.get("title", title)
            self._set_form_status(
                item,
                "running",
                "Grading now: fetching responses, evaluating answers, and applying updates",
            )
            self.form_list.scrollToItem(item)
            self.form_list.setCurrentItem(item)
        self.current_label.setText(f"Processing: {title[:48]}")
        self.run_state_label.setText("Running")
        self.pipeline_updated.setText("Updated just now")

    def update_finished_form(self, form_id):
        self.finished_forms.append(form_id)
        now_str = datetime.now().strftime("%H:%M:%S")
        item = self._find_form_item_by_id(form_id)
        title = "Unknown Form"
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            title = meta.get("title", title)
            self._set_form_status(item, "done", "Finished and saved grading updates")
        self.append_debug(f"<font color='green'>[AUTO {now_str}] Completed: {title}</font>")
        self.finished_label.setText(f"Finished: {len(self.finished_forms)}")
        # After a form finishes, if the grader has become idle, start the next queued forms.
        QTimer.singleShot(800, self._maybe_start_next_after_finish)

    def _maybe_start_next_after_finish(self):
        # Only start next run if not currently grading and no grader thread running
        if self.is_grading:
            return
        if self.grader_thread and self.grader_thread.isRunning():
            return
        queued_urls = []
        seen_ids = set()
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            meta = item.data(Qt.UserRole + 1) or {}
            if meta.get("status") == "queued":
                url = (item.data(Qt.UserRole) or "").strip()
                fid = self.extract_form_id(url) or url
                if fid in seen_ids:
                    continue
                seen_ids.add(fid)
                queued_urls.append(url)
        if queued_urls:
            self.append_debug(f"<font color='cyan'>[GRADER] 🔄 Detected queued forms after finish. Starting next run...</font>")
            QTimer.singleShot(500, lambda: self.run_grader(target_urls=queued_urls))

    def is_timing_line(self, message):
        return "Timing " in message

    def _make_log_textedit(self):
        w = QTextEdit()
        w.setReadOnly(True)
        w.setFont(QFont("Consolas", 10))
        w.setStyleSheet("background-color:#1e1e1e; color:#dcdcdc;")
        return w

    def _route_worker_log(self, message):
        if "[Worker: Producer]" in message:
            self.producer_output.append(message)
        if "[Worker: Deterministic]" in message:
            self.det_output.append(message)
        if "[Worker: AI]" in message:
            self.ai_output.append(message)
        if "[Worker: Aggregator]" in message:
            self.agg_output.append(message)
        # Global dispatcher logs (fallback routing when worker tags are absent).
        if "[DISPATCH METRICS]" in message or "[DISPATCH]" in message:
            self.producer_output.append(message)
            self.det_output.append(message)
            self.ai_output.append(message)
            self.agg_output.append(message)


    def _update_worker_metrics_label(self, message):
        # Example: [Worker Metrics] done=12/40 det_done=10 ai_done=2 q_det=3 q_ai=2 q_result=0
        try:
            if "[Worker Metrics]" in message:
                payload = message.split("[Worker Metrics]", 1)[1].strip()
                self._update_worker_tab_queue_counts(payload)
                return
            # Global dispatcher metrics also carry queue data and producer pending buffer.
            if "[DISPATCH METRICS]" in message:
                payload = message.split("[DISPATCH METRICS]", 1)[1].strip()
                self._update_worker_tab_queue_counts(payload)
                return
        except Exception:
            pass

    def _reset_worker_tab_titles(self):
        self.log_tabs.setTabText(0, "All")
        self.log_tabs.setTabText(1, "Producer (q: -)")
        self.log_tabs.setTabText(2, "Det Workers (q: -)")
        self.log_tabs.setTabText(3, "AI Workers (q: -)")
        self.log_tabs.setTabText(4, "Aggregator (q: -)")

    def _extract_metric_int(self, payload, key):
        token = f"{key}="
        if token not in payload:
            return None
        try:
            tail = payload.split(token, 1)[1]
            raw = tail.split()[0].strip()
            return int(raw)
        except Exception:
            return None

    def _update_worker_tab_queue_counts(self, payload):
        q_fetch = self._extract_metric_int(payload, "q_fetch")
        q_pending = self._extract_metric_int(payload, "pending")
        q_det = self._extract_metric_int(payload, "q_det")
        q_ai = self._extract_metric_int(payload, "q_ai")
        q_ai_actual = self._extract_metric_int(payload, "q_ai_actual")
        q_result = self._extract_metric_int(payload, "q_result")
        done = None
        total = None
        if "done=" in payload:
            try:
                done_part = payload.split("done=", 1)[1].split()[0].strip()
                if "/" in done_part:
                    d_s, t_s = done_part.split("/", 1)
                    done = int(d_s)
                    total = int(t_s)
            except Exception:
                done = None
                total = None

        # Backward-compat: older worker metrics do not publish q_fetch/pending.
        if q_fetch is None:
            q_fetch = q_det
        p = "-" if q_fetch is None else str(q_fetch)
        pb = "-" if q_pending is None else str(q_pending)
        d = "-" if q_det is None else str(q_det)
        q_ai_display = q_ai_actual if q_ai_actual is not None else q_ai
        a = "-" if q_ai_display is None else str(q_ai_display)
        r = "-" if q_result is None else str(q_result)

        self.log_tabs.setTabText(1, f"Producer (q: {p}, buf: {pb})")
        self.log_tabs.setTabText(2, f"Det Workers (q: {d})")
        self.log_tabs.setTabText(3, f"AI Workers (q: {a})")
        self.log_tabs.setTabText(4, f"Aggregator (q: {r})")
        self._update_pipeline_state(q_fetch, q_pending, q_det, q_ai_display, q_result, done, total)

    def _update_pipeline_state(self, q_fetch, q_pending, q_det, q_ai, q_result, done, total):
        state = "Unknown"
        if total is not None and done is not None and total > 0 and done >= total:
            state = "Completed"
        elif (q_fetch or 0) > 0 or (q_pending or 0) > 0:
            # Producer has backlog ready to feed.
            if (q_det or 0) <= 1 and (q_ai or 0) <= 1:
                state = "Feeding"
            else:
                state = "Balanced"
        elif (q_ai or 0) > 0 and (q_det or 0) == 0 and (q_fetch or 0) == 0 and ((q_pending or 0) == 0):
            state = "AI-drain"
        elif (q_result or 0) > 0 and (q_det or 0) == 0 and (q_ai or 0) == 0:
            state = "Apply-drain"
        elif (q_fetch or 0) == 0 and (q_det or 0) == 0 and (q_ai or 0) == 0 and (q_result or 0) == 0:
            if total is not None and done is not None and total > 0 and done < total:
                state = "Stalled"
            else:
                state = "Idle"
        else:
            state = "Balanced"
        self.pipeline_state_label.setText(f"Pipeline State: {state}")
        if hasattr(self, "pipeline_updated"):
            self.pipeline_updated.setText(state)

    def append_debug(self, message):
        self.debug_lines.append(message)
        self._update_worker_metrics_label(message)
        # Always route worker-tagged logs to dedicated tabs.
        self._route_worker_log(message)
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
        self.producer_output.clear()
        self.det_output.clear()
        self.ai_output.clear()
        self.agg_output.clear()
        for line in lines:
            self.debug_output.append(line)
            self._route_worker_log(line)


    def on_grading_finished(self, success, msg):
        self.is_grading = False
        self.run_button.setEnabled(True)
        self.stop_button.hide()
        now_str = datetime.now().strftime("%H:%M:%S")
        
        if not success:
            self.run_state_label.setText("Failed")
            for i in range(self.form_list.count()):
                item = self.form_list.item(i)
                meta = item.data(Qt.UserRole + 1) or {}
                if meta.get("status") == "running":
                    self._set_form_status(item, "failed", msg or "Grading process failed")
            self.append_debug(f"<font color='red'>[AUTO {now_str}] ❌ Grading failed: {msg}</font>")
        else:
            self.run_state_label.setText("Completed")
            self.append_debug(f"<font color='green'>[AUTO {now_str}] ✅ Grading completed successfully!</font>")
            self.append_debug("<b><font color='green'>ALL FORMS FINISHED. Grading run complete.</font></b>")
            if not self.auto_mode:
                self.append_debug("<font color='gray'>[GRADER] Completed forms remain visible for review. Use Clear All when ready.</font>")

        # Check if there are any queued forms that were added while the grader was running
        queued_urls = []
        seen_ids = set()
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            meta = item.data(Qt.UserRole + 1) or {}
            if meta.get("status") == "queued":
                url = (item.data(Qt.UserRole) or "").strip()
                fid = self.extract_form_id(url) or url
                if fid in seen_ids:
                    continue
                seen_ids.add(fid)
                queued_urls.append(url)

        if queued_urls:
            self.append_debug(f"<font color='cyan'>[GRADER] 🔄 Found {len(queued_urls)} queued form(s) added during execution. Starting next run...</font>")
            QTimer.singleShot(1000, lambda: self.run_grader(target_urls=queued_urls))
            return

        if self.auto_mode:
            # Clear finished forms
            forms_cleared = 0
            finished_ids = set(self.finished_forms)
            i = 0
            while i < self.form_list.count():
                item = self.form_list.item(i)
                url = item.data(Qt.UserRole)
                form_id = self.extract_form_id(url) if url else None
                if form_id in finished_ids:
                    self.form_list.takeItem(i)
                    if url in self.forms_data:
                        del self.forms_data[url]
                    forms_cleared += 1
                else:
                    i += 1
            i = 0
            while i < self.form_list.count():
                item = self.form_list.item(i)
                meta = item.data(Qt.UserRole + 1) or {}
                if meta.get("status") == "done":
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
                self._refresh_queue_positions()
            
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
        """Closing the window exits; Minimize remains the explicit tray action."""
        self._force_exit = True
        self._shutdown_owned_work()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_application_theme(app)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(244, 246, 248))
    palette.setColor(QPalette.WindowText, Qt.black)
    app.setPalette(palette)
    window = FormManager()
    app.aboutToQuit.connect(window._shutdown_owned_work)
    window.show()
    sys.exit(app.exec_())




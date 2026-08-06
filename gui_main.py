# gui_main.py - FIXED: Thread safety, duplicate prevention, proper cleanup
import sys
import os
import json
import subprocess
import shutil
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QTextEdit, QLabel, QComboBox, QCheckBox,
    QProgressDialog, QSplitter, QSpinBox, QDialog, QFormLayout, QTabWidget,
    QSystemTrayIcon, QMenu, QAction, QStyle, QFrame, QProgressBar, QDoubleSpinBox,
    QScrollArea, QFileDialog, QGridLayout, QShortcut, QTableWidget,
    QTableWidgetItem, QHeaderView
)

from PyQt5.QtCore import Qt, QDate, QTimer, QSize, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QBrush, QFont, QIcon, QPalette, QKeySequence
from datetime import datetime, timedelta, timezone, time
import ctypes
import atexit
import time as time_module
from urllib.parse import urlparse

# Local imports
from auth import (
    clear_cached_credentials,
    get_service,
    get_drive_service,
    get_classroom_service,
    has_saved_login,
    sign_in,
    sign_out,
)
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
from evaluator_config import (
    DEFAULT_CONFIG,
    effective_ai_worker_count,
    effective_provider_worker_counts,
    is_llamacpp_only,
)
from scheduler import scheduler as auto_scheduler
from answer_key_dashboard import AnswerKeyDashboard
from app_theme import apply_application_theme, apply_widget_theme, set_dark_mode, is_dark_mode, current_stylesheet
from cache_manager import clear_grading_cache
from answer_key_manager import load_pending_review_records, keep_teacher_answers_only
from decision_audit_viewer import DecisionAuditViewer, load_audit_records
import re

BANGKOK_TZ = timezone(timedelta(hours=7))
APP_ID = "regis.google_form_autograder"
APP_DATA_DIR_NAME = "GoogleFormAutograder"
RUNTIME_DEFAULT_FILES = (
    "config.json",
    "forms_to_grade.json",
    "predefined_folders.json",
    "client_secrets.json",
)
RUNTIME_DIRS = (
    "logs",
    "cache",
    os.path.join("cache", "results"),
    os.path.join("cache", "embeddings"),
    os.path.join("cache", "form_context"),
    os.path.join("cache", "vision"),
    "backups",
    os.path.join("backups", "answer_keys"),
)


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, *parts)


def app_icon():
    return QIcon(resource_path("assets", "app_icon.ico"))


def _user_data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DATA_DIR_NAME)


def ensure_runtime_environment():
    """Prepare writable runtime files for packaged builds."""
    if getattr(sys, "frozen", False):
        target_dir = _user_data_dir()
        os.makedirs(target_dir, exist_ok=True)
        for filename in RUNTIME_DEFAULT_FILES:
            target = os.path.join(target_dir, filename)
            source = resource_path(filename)
            if not os.path.exists(target) and os.path.exists(source):
                shutil.copy2(source, target)
        os.chdir(target_dir)

    for directory in RUNTIME_DIRS:
        os.makedirs(directory, exist_ok=True)


class SourceScanThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, sources, mode="all_forms", from_dt=None, to_dt=None):
        super().__init__()
        self.sources = list(sources or [])
        self.mode = mode
        self.from_dt = from_dt
        self.to_dt = to_dt

    def run(self):
        try:
            self.progress.emit(f"Starting scan in {len(self.sources)} source(s)")
            if self.mode == "with_submissions":
                forms = find_forms_with_submissions_in_range(
                    self.sources,
                    from_dt=self.from_dt,
                    to_dt=self.to_dt,
                    progress_callback=lambda msg: self.progress.emit(str(msg)),
                )
            else:
                forms = find_all_forms_in_sources(
                    self.sources,
                    progress_callback=lambda msg: self.progress.emit(str(msg)),
                )
            self.progress.emit(f"Scan completed. Found {len(forms)} form(s)")
            self.finished.emit(forms)
        except Exception as exc:
            self.failed.emit(str(exc))


class SettingsModelDiscoveryThread(QThread):
    finished = pyqtSignal(object, object, str)

    def __init__(self, llamacpp_model_dir):
        super().__init__()
        self.llamacpp_model_dir = str(llamacpp_model_dir or "")

    def run(self):
        errors = []
        ollama_models = []
        llamacpp_models = []
        try:
            ollama_models = [
                self._read_ollama_model_name(model_info)
                for model_info in ollama.list().get("models", [])
            ]
            ollama_models = [m for m in ollama_models if m]
        except Exception as exc:
            errors.append(f"Ollama models unavailable: {exc}")
        try:
            llamacpp_models = self._find_llamacpp_models(self.llamacpp_model_dir)
        except Exception as exc:
            errors.append(f"llama.cpp model scan failed: {exc}")
        self.finished.emit(ollama_models, llamacpp_models, "; ".join(errors))

    @staticmethod
    def _read_ollama_model_name(model_info):
        if isinstance(model_info, dict):
            return model_info.get("name") or model_info.get("model")
        return getattr(model_info, "name", None) or getattr(model_info, "model", None)

    @staticmethod
    def _find_llamacpp_models(model_dir):
        root = os.path.expandvars(os.path.expanduser(str(model_dir or "")))
        found = []
        if not root or not os.path.isdir(root):
            return found
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                lower_name = filename.lower()
                if not lower_name.endswith(".gguf"):
                    continue
                if lower_name.startswith("mmproj-") or "mmproj" in lower_name:
                    continue
                path = os.path.join(dirpath, filename)
                found.append(os.path.relpath(path, root).replace("\\", "/"))
        return sorted(found, key=str.casefold)

AI_WORKER_DISPLAY_NAMES = [
    "Optimus Prime",
    "Bumblebee",
    "Ratchet",
    "Ironhide",
    "Arcee",
    "Jazz",
    "Wheeljack",
    "Mirage",
    "Prowl",
    "Sideswipe",
    "Hot Rod",
    "Ultra Magnus",
]

EXECUTION_MODE_PRESETS = {
    "Maximum accuracy: independent unanimous jury + review": {
        "deterministic_worker_count": 4,
        "ai_worker_count": 4,
        "max_concurrent_judge_http": 1,
        "max_concurrent_jury_answers": 4,
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
        self.setWindowIcon(app_icon())
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
        self.overall_forms_completed = 0
        self.overall_forms_total = 0
        self.auto_mode = False
        self.auto_timer = None  # Track the QTimer for auto-cycle
        self.max_gui_log_lines = 2500
        self.max_gui_visible_blocks = 1200
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
            QFrame#CommandBar QPushButton#CommandButton {
                min-height: 42px;
                max-height: 42px;
                padding: 0 14px;
            }
            QFrame#CommandBar QPushButton#CommandButton[variant="secondary"] {
                background-color: #6c757d;
            }
            QFrame#CommandBar QPushButton#CommandButton[variant="secondary"]:hover {
                background-color: #545b62;
            }
            QFrame#CommandBar QPushButton#CommandButton[variant="danger"] {
                background-color: #dc3545;
            }
            QFrame#CommandBar QPushButton#CommandButton[variant="danger"]:hover {
                background-color: #b02a37;
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
            QPushButton#Danger[compactControl="true"] {
                min-height: 42px;
                max-height: 42px;
                padding: 0 13px;
            }
            QComboBox, QTextEdit, QListWidget {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 6px;
                padding: 6px;
            }
            QListWidget#FormQueueList {
                background-color: #ffffff;
                border: 1px solid #c8d2dc;
                border-radius: 0;
                padding: 0;
            }
            QListWidget#FormQueueList::item {
                border: none;
                margin: 0;
                padding: 0;
            }
            QFrame#FormQueueHeader {
                background-color: #e8eef4;
                border: 1px solid #c8d2dc;
                border-bottom: 0;
            }
            QLabel#QueueColumnHeader {
                color: #263747;
                font-size: 11px;
                font-weight: 700;
            }
            QFrame#FormCard {
                background-color: #ffffff;
                border: 0;
                border-bottom: 1px solid #e1e7ed;
                border-left: 3px solid transparent;
            }
            QFrame#FormCard[rowParity="odd"] {
                background-color: #f7f9fb;
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
            }
            QFrame#FormCard[status="failed"] {
                border-left-color: #dc3545;
                background-color: #fff3f3;
            }
            QLabel#FormTitle {
                font-size: 12px;
                font-weight: 600;
                color: #0d6efd;
            }
            QLabel#FormMeta {
                font-size: 10px;
                color: #5b6775;
            }
            QLabel#FormUrl {
                font-size: 10px;
                color: #5b6775;
            }
            QLabel#StatusBadge {
                font-size: 11px;
                font-weight: 600;
                color: #405466;
                background-color: transparent;
                border-radius: 0;
                padding: 0;
            }
            QLabel#StatusBadge[status="queued"] {
                color: #0d6efd;
            }
            QLabel#StatusBadge[status="running"] {
                color: #7b4b00;
            }
            QLabel#StatusBadge[status="done"] {
                color: #198754;
            }
            QLabel#StatusBadge[status="failed"] {
                color: #dc3545;
            }
            QLabel#StatusBadge[status="skipped"] {
                color: #9a3412;
            }
            QLabel#StatusBadge[status="partial"] {
                color: #b45309;
            }
            QLabel#QueueEta {
                color: #405466;
                font-size: 11px;
            }
            QLabel#QueueGlyph {
                color: #5b8fd6;
                font-size: 14px;
                font-weight: 700;
            }
            QProgressBar#QueueProgress {
                background-color: #e7edf3;
                border: 1px solid #c5d0db;
                border-radius: 3px;
                min-height: 16px;
                max-height: 16px;
                text-align: center;
                color: #1f2937;
                font-size: 10px;
                font-weight: 700;
            }
            QProgressBar#QueueProgress::chunk {
                background-color: #198754;
                border-radius: 2px;
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
        self.auth_status_label = QLabel()
        self.auth_status_label.setObjectName("Muted")
        header_layout.addWidget(self.auth_status_label)

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
        command_button_height = 42
        add_sources_button = QPushButton("Add Sources")
        add_sources_button.setObjectName("CommandButton")
        add_sources_button.setProperty("variant", "secondary")
        add_sources_button.setFixedHeight(command_button_height)
        add_sources_button.clicked.connect(self.open_manual_add_dialog)
        command_layout.addWidget(add_sources_button)
        scan_source_button = QPushButton("Scan Source")
        scan_source_button.setObjectName("CommandButton")
        scan_source_button.setProperty("variant", "secondary")
        scan_source_button.setFixedHeight(command_button_height)
        scan_source_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView))
        scan_source_button.setProperty("noAutoIcon", True)
        scan_source_button.clicked.connect(self.open_quick_grade_dialog)
        command_layout.addWidget(scan_source_button)
        self.run_button = QPushButton("Run Grading")
        self.run_button.setObjectName("CommandButton")
        self.run_button.setProperty("variant", "secondary")
        self.run_button.setFixedHeight(command_button_height)
        self.run_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.run_button.setProperty("noAutoIcon", True)
        self.run_button.clicked.connect(self.run_grader)
        command_layout.addWidget(self.run_button)
        self.stop_button = QPushButton("Stop Grading")
        self.stop_button.setObjectName("CommandButton")
        self.stop_button.setProperty("variant", "danger")
        self.stop_button.setFixedHeight(command_button_height)
        self.stop_button.clicked.connect(self.stop_grading)
        self.stop_button.hide()
        command_layout.addWidget(self.stop_button)
        answer_keys_button = QPushButton("Answer Keys")
        answer_keys_button.setObjectName("CommandButton")
        answer_keys_button.setProperty("variant", "secondary")
        answer_keys_button.setFixedHeight(command_button_height)
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
        login_action = more_menu.addAction("Login to Google")
        login_action.triggered.connect(self.login_google)
        logout_action = more_menu.addAction("Logout Google Account")
        logout_action.triggered.connect(self.logout_google)
        more_menu.addSeparator()
        auto_run_action = more_menu.addAction("Schedule Automatic Runs")
        auto_run_action.triggered.connect(self.open_auto_run_dialog)
        grade_all_action = more_menu.addAction("Grade All Queued Forms")
        grade_all_action.triggered.connect(self.grade_all_forms_in_all_folders)
        more_menu.addSeparator()
        audit_action = more_menu.addAction("View Decision Audit")
        audit_action.triggered.connect(self.open_decision_audit_viewer)
        export_action = more_menu.addAction("Export Results (CSV)")
        export_action.triggered.connect(self.export_results_csv_dialog)
        report_action = more_menu.addAction("Generate Run Report")
        report_action.triggered.connect(self.generate_run_report)
        self.dark_mode_action = more_menu.addAction("Toggle Dark Mode")
        self.dark_mode_action.triggered.connect(self.toggle_dark_mode)
        more_menu.addSeparator()
        remove_action = more_menu.addAction("Remove Selected Form")
        remove_action.triggered.connect(self.remove_form)
        clear_action = more_menu.addAction("Clear Completed Forms")
        clear_action.triggered.connect(self.clear_finished_forms_silently)
        clear_all_action = more_menu.addAction("Clear All Forms")
        clear_all_action.triggered.connect(lambda: self.clear_all_forms(confirm=True))
        more_menu.addSeparator()
        exit_action = more_menu.addAction("Exit")
        exit_action.triggered.connect(self.exit_app)
        more_button.setMenu(more_menu)
        self.login_action = login_action
        self.logout_action = logout_action
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
        queue_control_height = 42
        self.form_search_input = QLineEdit()
        self.form_search_input.setPlaceholderText("Search forms")
        self.form_search_input.setFixedHeight(queue_control_height)
        self.form_search_input.textChanged.connect(self._filter_form_queue)
        self.form_filter_combo = QComboBox()
        self.form_filter_combo.addItems(["All", "Running", "Queued", "Done", "Partial", "Skipped", "Failed"])
        self.form_filter_combo.setFixedHeight(queue_control_height)
        self.form_filter_combo.setMinimumWidth(84)
        self.form_filter_combo.currentTextChanged.connect(self._filter_form_queue)
        self.clear_forms_button = QPushButton("Clear All")
        self.clear_forms_button.setObjectName("Danger")
        self.clear_forms_button.setProperty("compactControl", True)
        self.clear_forms_button.setFixedHeight(queue_control_height)
        self.clear_forms_button.setToolTip("Delete every form from the queue")
        self.clear_forms_button.clicked.connect(lambda: self.clear_all_forms(confirm=True))
        queue_filters.addWidget(self.form_search_input, 1)
        queue_filters.addWidget(self.form_filter_combo)
        queue_filters.addWidget(self.clear_forms_button)
        queue_layout.addLayout(queue_filters)
        queue_table_header = QFrame()
        queue_table_header.setObjectName("FormQueueHeader")
        queue_table_layout = QGridLayout(queue_table_header)
        queue_table_layout.setContentsMargins(10, 5, 10, 5)
        queue_table_layout.setHorizontalSpacing(8)
        for col, (text, stretch) in enumerate((
            ("Name", 5),
            ("Progress", 2),
            ("Status", 2),
            ("ETA", 1),
        )):
            label = QLabel(text)
            label.setObjectName("QueueColumnHeader")
            if col:
                label.setAlignment(Qt.AlignCenter)
            queue_table_layout.addWidget(label, 0, col)
            queue_table_layout.setColumnStretch(col, stretch)
        queue_layout.addWidget(queue_table_header)
        self.form_list = QListWidget()
        self.form_list.setObjectName("FormQueueList")
        self.form_list.setSpacing(4)
        self.form_list.setUniformItemSizes(False)
        self.form_list.setWordWrap(True)
        self.form_list.setTextElideMode(Qt.ElideRight)
        self.form_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.form_list.currentItemChanged.connect(self._on_form_selection_changed)
        self.form_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.form_list.customContextMenuRequested.connect(self._on_form_list_context_menu)
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

        self.detail_warning = QLabel("")
        self.detail_warning.setObjectName("Muted")
        self.detail_warning.setWordWrap(True)
        self.detail_warning.hide()
        detail_layout.addWidget(self.detail_warning)

        progress_header = QHBoxLayout()
        self.detail_progress_text = QLabel("Overall forms progress")
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
        self.metric_rate = QLabel("0/min")
        self.metric_ai_backlog = QLabel("0")
        self.metric_decision_paths = QLabel("0 / 0")
        self.metric_current_model = QLabel("Idle")
        self.metric_avg_latency = QLabel("0s")
        self.metric_eta = QLabel("--:--")
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

        live_metrics_row = QHBoxLayout()
        for metric_name, metric_value in (
            ("Answers / min", self.metric_rate),
            ("AI backlog", self.metric_ai_backlog),
            ("Current model", self.metric_current_model),
            ("Avg latency", self.metric_avg_latency),
            ("ETA", self.metric_eta),
        ):
            metric = QFrame()
            metric.setObjectName("Metric")
            metric_layout = QVBoxLayout(metric)
            metric_layout.setContentsMargins(12, 10, 12, 10)
            label = QLabel(metric_name)
            label.setObjectName("Muted")
            metric_value.setObjectName("MetricValue")
            metric_layout.addWidget(label)
            metric_layout.addWidget(metric_value)
            live_metrics_row.addWidget(metric, 1)
        detail_layout.addLayout(live_metrics_row)

        pipeline_heading = QHBoxLayout()
        pipeline_title = QLabel("Run activity")
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

        add_stage("forms", "F", "Forms", "0 completed", "Idle")
        add_stage("answers", "A", "Answers", "0 / 0 evaluated", "Waiting")
        add_stage("ai", "Q", "AI queue", "0 waiting", "Idle")
        add_stage("apply", "R", "Review/apply", "0 review questions", "Waiting")

        worker_heading = QHBoxLayout()
        worker_title = QLabel("Worker threads")
        worker_title.setObjectName("Section")
        self.worker_summary = QLabel("Waiting for workers")
        self.worker_summary.setObjectName("Muted")
        worker_heading.addWidget(worker_title)
        worker_heading.addStretch()
        worker_heading.addWidget(self.worker_summary)
        detail_layout.addSpacing(12)
        detail_layout.addLayout(worker_heading)

        app_worker_header = QHBoxLayout()
        app_worker_label = QLabel("Application workers")
        app_worker_label.setObjectName("Muted")
        self.app_worker_summary = QLabel("AI workers: -")
        self.app_worker_summary.setObjectName("Muted")
        app_worker_header.addWidget(app_worker_label)
        app_worker_header.addStretch()
        app_worker_header.addWidget(self.app_worker_summary)
        detail_layout.addLayout(app_worker_header)
        self.app_worker_list = QVBoxLayout()
        self.app_worker_list.setSpacing(0)
        detail_layout.addLayout(self.app_worker_list)

        self.provider_worker_summary = QLabel("OpenRouter: - | llama.cpp: - | Ollama: -")
        self.provider_worker_summary.setObjectName("Muted")
        provider_worker_label = QLabel("Provider workers")
        provider_worker_label.setObjectName("Muted")
        detail_layout.addSpacing(6)
        detail_layout.addWidget(provider_worker_label)
        detail_layout.addWidget(self.provider_worker_summary)
        self.provider_worker_list = QVBoxLayout()
        self.provider_worker_list.setSpacing(0)
        detail_layout.addLayout(self.provider_worker_list)

        model_health_label = QLabel("Model health")
        model_health_label.setObjectName("Muted")
        detail_layout.addSpacing(6)
        detail_layout.addWidget(model_health_label)
        self.model_health_rows = {}
        self.model_health_list = QVBoxLayout()
        self.model_health_list.setSpacing(0)
        detail_layout.addLayout(self.model_health_list)
        for key, title in (
            ("current", "Current model"),
            ("success", "Success / latency"),
            ("limits", "Rate limits / failures"),
            ("json", "JSON reliability"),
            ("quality", "Ollama quality"),
            ("cooldown", "Cooldown"),
            ("cost", "Cost"),
            ("reason", "Why chosen"),
        ):
            self._ensure_model_health_row(key, title)

        self.app_worker_cards = {}
        self.provider_worker_cards = {}
        self.provider_worker_states = {}
        self._initialize_worker_cards()
        detail_layout.addStretch()
        detail_scroll = QScrollArea()
        detail_scroll.setObjectName("DetailScroll")
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.NoFrame)
        detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        detail_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        detail_scroll.setWidget(detail_widget)
        workspace.addWidget(detail_scroll)
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
        self.provider_output = self._make_log_textedit()
        self.agg_output = self._make_log_textedit()
        self.log_tabs.addTab(self.debug_output, "AI grading")
        self.log_tabs.addTab(self.producer_output, "Producer (q: -)")
        self.log_tabs.addTab(self.det_output, "Det Workers (q: -)")
        self.log_tabs.addTab(self.ai_output, "AI Workers (q: -)")
        self.log_tabs.addTab(self.provider_output, "Providers (OR: - | OL: -)")
        self.log_tabs.addTab(self.agg_output, "Aggregator (q: -)")
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
        self.refresh_auth_status()
        self._setup_keyboard_shortcuts()
        self._notified_budget_warning = False
        QTimer.singleShot(500, self.prompt_login_if_needed)

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

    def refresh_auth_status(self):
        signed_in = has_saved_login()
        if hasattr(self, "auth_status_label"):
            self.auth_status_label.setText("Google: signed in" if signed_in else "Google: not signed in")
        if hasattr(self, "login_action"):
            self.login_action.setEnabled(not signed_in)
        if hasattr(self, "logout_action"):
            self.logout_action.setEnabled(signed_in)

    def prompt_login_if_needed(self):
        if has_saved_login() or self.is_closing:
            return
        reply = QMessageBox.question(
            self,
            "Login to Google?",
            "No saved Google login was found. Sign in now so the app can access Forms, Drive, and Classroom.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self.login_google()
        else:
            self.refresh_auth_status()

    def login_google(self):
        if self.is_grading or (self.grader_thread and self.grader_thread.isRunning()):
            QMessageBox.information(self, "Grading Running", "Stop grading before changing Google login.")
            return
        try:
            self.run_state_label.setText("Signing in")
            QApplication.processEvents()
            clear_cached_credentials()
            sign_in()
            self.service = None
            self.drive_service = None
            self.classroom_service = None
            self.refresh_auth_status()
            self.run_state_label.setText("Ready")
            QMessageBox.information(self, "Google Login", "Google account is signed in.")
        except Exception as exc:
            self.run_state_label.setText("Ready")
            self.refresh_auth_status()
            QMessageBox.critical(self, "Google Login Failed", str(exc))

    def logout_google(self):
        if self.is_grading or (self.grader_thread and self.grader_thread.isRunning()):
            QMessageBox.information(self, "Grading Running", "Stop grading before logging out.")
            return
        reply = QMessageBox.question(
            self,
            "Logout Google Account?",
            "This will remove the saved Google login token from this computer. "
            "The next login will open Google authentication again.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            sign_out(remove_token=True)
            self.service = None
            self.drive_service = None
            self.classroom_service = None
            self.refresh_auth_status()
            self.run_state_label.setText("Ready")
            QMessageBox.information(self, "Google Logout", "Signed out. The saved Google token was removed.")
        except Exception as exc:
            self.refresh_auth_status()
            QMessageBox.critical(self, "Google Logout Failed", str(exc))

    def _on_form_selection_changed(self, current, _previous=None):
        if not current:
            self.detail_title.setText("Select a form")
            self.detail_meta.setText("No form selected")
            self.detail_badge.setText("IDLE")
            self.detail_warning.hide()
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
        skipped_questions = meta.get("skipped_questions") or []
        if skipped_questions:
            lines = [
                "Some questions were skipped because teacher answer keys are missing:",
                *[
                    f"Q{int(entry.get('question_number', 0) or 0)}: {entry.get('title', 'Untitled')} "
                    f"({int(entry.get('responses', 0) or 0)} response(s))"
                    for entry in skipped_questions[:8]
                    if isinstance(entry, dict)
                ],
            ]
            if len(skipped_questions) > 8:
                lines.append(f"+{len(skipped_questions) - 8} more")
            self.detail_warning.setText("\n".join(lines))
            self.detail_warning.show()
        else:
            self.detail_warning.hide()
        self.detail_progress_text.setText("Overall forms progress")
        self._update_overall_progress_bar()
        self.pipeline_updated.setText(meta.get("finished_at") or meta.get("started_at") or "Ready")
        self._update_pipeline_rows_for_status(status)

        # Update metrics cards
        completed = meta.get("completed", 0)
        total = meta.get("total", 0)
        accepted = meta.get("accepted", 0)
        rejected = meta.get("rejected", 0)
        review_questions = meta.get("review_questions", 0)
        elapsed_seconds = meta.get("elapsed", 0)
        det_decisions = meta.get("det_decisions", 0)
        ai_decisions = meta.get("ai_decisions", 0)
        avg_latency_ms = meta.get("avg_latency_ms", 0.0)
        ai_backlog = meta.get("ai_backlog", 0)
        current_model = meta.get("current_model", "Idle")

        # For inactive forms, dynamically load the most up-to-date review count from disk
        form_id = self.extract_form_id(meta.get("url"))
        if form_id:
            try:
                from answer_key_manager import load_pending_reviews
                pending = load_pending_reviews(form_id)
                review_questions = len(pending)
            except Exception:
                pass

        self._update_metric_labels(
            completed,
            total,
            accepted,
            review_questions,
            elapsed_seconds,
            rejected,
            det_decisions,
            ai_decisions,
            avg_latency_ms,
            ai_backlog,
            current_model,
        )

    def _format_duration(self, seconds):
        if isinstance(seconds, str):
            return seconds
        seconds = max(0, int(seconds or 0))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"

    def _format_latency(self, avg_latency_ms):
        avg_latency_ms = max(0.0, float(avg_latency_ms or 0.0))
        if avg_latency_ms >= 1000:
            return f"{avg_latency_ms / 1000.0:.1f}s"
        return f"{avg_latency_ms:.0f}ms"

    def _estimate_eta(self, completed, total, elapsed_seconds):
        try:
            elapsed = float(elapsed_seconds)
        except Exception:
            return "--:--"
        if completed <= 0 or total <= 0 or completed >= total or elapsed <= 0:
            return "--:--"
        remaining = max(0, total - completed)
        seconds = remaining / (completed / elapsed)
        return self._format_duration(seconds)

    def _answers_per_minute(self, completed, elapsed_seconds):
        try:
            elapsed = float(elapsed_seconds)
        except Exception:
            return "0/min"
        if completed <= 0 or elapsed <= 0:
            return "0/min"
        return f"{(completed / elapsed) * 60.0:.1f}/min"

    def _update_metric_labels(
        self,
        completed,
        total,
        accepted,
        review_questions,
        elapsed_seconds,
        rejected=0,
        det_decisions=0,
        ai_decisions=0,
        avg_latency_ms=0.0,
        ai_backlog=0,
        current_model="Idle",
    ):
        self.metric_responses.setText(f"{int(completed)} / {int(total)}")
        self.metric_accepted.setText(str(int(accepted)))
        self.metric_rejected.setText(str(int(rejected)))
        self.metric_review.setText(f'<a href="review">{int(review_questions)}</a>')
        self.metric_elapsed.setText(self._format_duration(elapsed_seconds))
        self.metric_rate.setText(self._answers_per_minute(int(completed), elapsed_seconds))
        self.metric_ai_backlog.setText(str(int(ai_backlog or 0)))
        self.metric_decision_paths.setText(f"{int(det_decisions or 0)} / {int(ai_decisions or 0)}")
        model_text = str(current_model or "Idle")
        if model_text == "none":
            model_text = "Idle"
        short_model = model_text if len(model_text) <= 18 else model_text[:17] + "..."
        self.metric_current_model.setText(short_model)
        self.metric_current_model.setToolTip(model_text)
        self.metric_avg_latency.setText(self._format_latency(avg_latency_ms))
        self.metric_eta.setText(self._estimate_eta(int(completed), int(total), elapsed_seconds))

    def _reset_metric_labels(self):
        self._update_metric_labels(0, 0, 0, 0, 0, 0, 0, 0, 0.0, 0, "Idle")

    def _configured_worker_counts(self):
        try:
            with open("config.json", "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            cfg = {}
        provider_counts = effective_provider_worker_counts(cfg)
        return {
            "ai": effective_ai_worker_count(cfg),
            "openrouter": provider_counts["openrouter"],
            "llamacpp": provider_counts["llamacpp"],
            "ollama": provider_counts["ollama"],
        }

    def _ai_worker_display_name(self, worker_id):
        try:
            number = int(str(worker_id).rsplit("-", 1)[-1])
        except Exception:
            number = 0
        if 1 <= number <= len(AI_WORKER_DISPLAY_NAMES):
            return AI_WORKER_DISPLAY_NAMES[number - 1]
        return f"Autobot {number}" if number > 0 else "Autobot"

    def _sync_worker_cards_to_config(self):
        counts = self._configured_worker_counts()
        self._prune_worker_cards_to_counts(counts)
        for index in range(len(self.app_worker_cards), counts.get("ai", 4)):
            self._ensure_worker_card("app", f"ai-{index + 1}", "AI worker")
        for index in range(len([wid for wid in self.provider_worker_cards if wid.startswith("openrouter-")]), counts.get("openrouter", 4)):
            worker_id = f"openrouter-{index + 1}"
            self.provider_worker_states.setdefault(worker_id, {"state": "idle"})
            self._ensure_worker_card("provider", worker_id, "OpenRouter")
        for index in range(len([wid for wid in self.provider_worker_cards if wid.startswith("llamacpp-")]), counts.get("llamacpp", 0)):
            worker_id = f"llamacpp-{index + 1}"
            self.provider_worker_states.setdefault(worker_id, {"state": "idle"})
            self._ensure_worker_card("provider", worker_id, "llama.cpp")
        for index in range(len([wid for wid in self.provider_worker_cards if wid.startswith("ollama-")]), counts.get("ollama", 1)):
            worker_id = f"ollama-{index + 1}"
            self.provider_worker_states.setdefault(worker_id, {"state": "idle"})
            self._ensure_worker_card("provider", worker_id, "Ollama")
        self._refresh_worker_summaries()

    def _remove_worker_card(self, group, worker_id):
        cards = self.app_worker_cards if group == "app" else self.provider_worker_cards
        card = cards.pop(worker_id, None)
        if not card:
            return
        frame = card.get("frame")
        layout = self.app_worker_list if group == "app" else self.provider_worker_list
        if frame is not None:
            layout.removeWidget(frame)
            frame.setParent(None)
            frame.deleteLater()
        if group != "app":
            self.provider_worker_states.pop(worker_id, None)

    def _worker_number(self, worker_id):
        try:
            return int(str(worker_id).rsplit("-", 1)[-1])
        except Exception:
            return 0

    def _prune_worker_cards_to_counts(self, counts):
        for worker_id in list(self.app_worker_cards):
            if self._worker_number(worker_id) > counts.get("ai", 1):
                self._remove_worker_card("app", worker_id)
        for provider in ("openrouter", "llamacpp", "ollama"):
            limit = counts.get(provider, 0)
            for worker_id in list(self.provider_worker_cards):
                if worker_id.startswith(f"{provider}-") and self._worker_number(worker_id) > limit:
                    self._remove_worker_card("provider", worker_id)

    def _worker_allowed_by_config(self, group, worker_id):
        counts = self._configured_worker_counts()
        number = self._worker_number(worker_id)
        if group == "app":
            return number <= counts.get("ai", 1)
        for provider in ("openrouter", "llamacpp", "ollama"):
            if str(worker_id).startswith(f"{provider}-"):
                return number <= counts.get(provider, 0)
        return True

    def _initialize_worker_cards(self):
        counts = self._configured_worker_counts()
        for index in range(counts["ai"]):
            self._ensure_worker_card("app", f"ai-{index + 1}", "AI worker")
        for index in range(counts["openrouter"]):
            self.provider_worker_states[f"openrouter-{index + 1}"] = {"state": "idle"}
            self._ensure_worker_card("provider", f"openrouter-{index + 1}", "OpenRouter")
        for index in range(counts["llamacpp"]):
            self.provider_worker_states[f"llamacpp-{index + 1}"] = {"state": "idle"}
            self._ensure_worker_card("provider", f"llamacpp-{index + 1}", "llama.cpp")
        for index in range(counts["ollama"]):
            self.provider_worker_states[f"ollama-{index + 1}"] = {"state": "idle"}
            self._ensure_worker_card("provider", f"ollama-{index + 1}", "Ollama")
        self._refresh_worker_summaries()

    def _ensure_worker_card(self, group, worker_id, title_prefix):
        cards = self.app_worker_cards if group == "app" else self.provider_worker_cards
        if worker_id in cards:
            return cards[worker_id]
        row = QFrame()
        row.setObjectName("WorkerRow")
        row.setProperty("status", "idle")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        title_text = self._ai_worker_display_name(worker_id) if group == "app" else f"{title_prefix} {worker_id.split('-', 1)[-1]}"
        title = QLabel(title_text)
        title.setObjectName("WorkerTitle")
        title.setMinimumWidth(120 if group == "app" else 115)
        title.setToolTip(worker_id if group == "app" else "")
        status = QLabel("Idle")
        status.setObjectName("WorkerStatus")
        status.setProperty("status", "idle")
        primary = QLabel("Waiting")
        primary.setObjectName("WorkerPrimary")
        primary.setMinimumWidth(120 if group == "app" else 180)
        secondary = QLabel("No request")
        secondary.setObjectName("Muted")
        secondary.setMinimumWidth(180)
        stats = QLabel("latency - | wait -")
        stats.setObjectName("Muted")
        stats.setMinimumWidth(120)
        for label in (primary, secondary, stats):
            label.setWordWrap(False)
        layout.addWidget(title)
        layout.addWidget(status)
        layout.addWidget(primary, 1)
        layout.addWidget(secondary, 2)
        layout.addWidget(stats, 1)

        cards[worker_id] = {
            "frame": row,
            "title": title,
            "status": status,
            "primary": primary,
            "secondary": secondary,
            "stats": stats,
            "state": "idle",
        }
        if group == "app":
            self.app_worker_list.addWidget(row)
        else:
            self.provider_worker_list.addWidget(row)
            self.provider_worker_states.setdefault(worker_id, {"state": "idle"})
        return cards[worker_id]

    def _set_worker_card(self, group, worker_id, title_prefix, status, primary, secondary, stats):
        if not self._worker_allowed_by_config(group, worker_id):
            return
        card = self._ensure_worker_card(group, worker_id, title_prefix)
        state = str(status or "idle").lower()
        card["state"] = state
        card["status"].setText(state.title())
        card["status"].setProperty("status", state)
        card["frame"].setProperty("status", state)
        card["primary"].setText(str(primary or "Waiting"))
        card["secondary"].setText(str(secondary or "No request"))
        card["stats"].setText(str(stats or "latency - | wait -"))
        if group != "app":
            self.provider_worker_states[worker_id] = {
                "state": state,
                "provider": title_prefix,
                "primary": primary,
                "secondary": secondary,
                "stats": stats,
            }
        for widget in (card["status"], card["frame"]):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self._refresh_worker_summaries()

    def _ensure_model_health_row(self, key, title):
        if key in getattr(self, "model_health_rows", {}):
            return self.model_health_rows[key]
        row = QFrame()
        row.setObjectName("PipelineRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 8, 4, 8)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight:700;")
        title_label.setMinimumWidth(130)
        detail_label = QLabel("-")
        detail_label.setObjectName("Muted")
        detail_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(detail_label, 1)
        self.model_health_list.addWidget(row)
        self.model_health_rows[key] = {"frame": row, "title": title_label, "detail": detail_label}
        return self.model_health_rows[key]

    def _set_model_health_row(self, key, detail, tooltip=None):
        row = self._ensure_model_health_row(key, key.replace("_", " ").title())
        row["detail"].setText(str(detail or "-"))
        row["detail"].setToolTip(str(tooltip or detail or ""))

    def _refresh_worker_summaries(self):
        def counts(cards):
            running = sum(1 for card in cards.values() if card.get("state") == "running")
            done = sum(1 for card in cards.values() if card.get("state") == "done")
            failed = sum(1 for card in cards.values() if card.get("state") == "failed")
            total = len(cards)
            return total, running, done, failed

        app_total, app_running, app_done, app_failed = counts(getattr(self, "app_worker_cards", {}))
        provider_total, provider_running, provider_done, provider_failed = counts(getattr(self, "provider_worker_states", {}))
        self.app_worker_summary.setText(f"{app_running}/{app_total} running")
        self.provider_worker_summary.setText(
            getattr(self, "_provider_summary_text", "") or f"{provider_running}/{provider_total} running"
        )
        self.worker_summary.setText(
            f"App {app_running}/{app_total} active · Providers {provider_running}/{provider_total} active"
        )
        if app_failed or provider_failed:
            self.worker_summary.setText(
                f"App {app_running}/{app_total} active, {app_failed} failed · "
                f"Providers {provider_running}/{provider_total} active, {provider_failed} failed"
            )

    def _update_overall_progress_bar(self):
        total = max(0, int(self.overall_forms_total or 0))
        completed = max(0, min(total, int(self.overall_forms_completed or 0))) if total else 0
        percent = int(round((completed / total) * 100)) if total else 0
        self.detail_progress.setValue(percent)
        self.detail_progress_value.setText(f"{percent}%")
        self.detail_progress_text.setText("Overall forms progress")

    def _update_pipeline_rows_for_status(self, status):
        item = self._find_form_item_by_url(self.current_form_url)
        meta = item.data(Qt.UserRole + 1) if item else {}
        completed = int(meta.get("completed", 0) or 0)
        total = int(meta.get("total", 0) or 0)
        ai_backlog = int(meta.get("ai_backlog", 0) or 0)
        reviews = int(meta.get("review_questions", 0) or 0)
        self._set_activity_row(
            "forms",
            f"{self.overall_forms_completed} / {self.overall_forms_total} completed",
            "Running" if self.is_grading else "Idle",
        )
        self._set_activity_row(
            "answers",
            f"{completed} / {total} evaluated",
            "Running" if status == "running" and completed < total else "Done" if total and completed >= total else "Waiting",
        )
        self._set_activity_row(
            "ai",
            f"{ai_backlog} waiting",
            "Draining" if ai_backlog else "Idle",
        )
        self._set_activity_row(
            "apply",
            f"{reviews} review questions",
            "Pending" if reviews else "Waiting",
        )

    def _set_activity_row(self, key, detail_text, state_text):
        row = self.pipeline_rows.get(key)
        if not row:
            return
        icon, detail, state = row
        detail.setText(str(detail_text))
        state.setText(str(state_text))
        if state_text in {"Running", "Draining", "Pending"}:
            icon.setStyleSheet("color:#b36b00; font-weight:700;")
        elif state_text == "Done":
            icon.setStyleSheet("color:#16845b; font-weight:700;")
        else:
            icon.setStyleSheet("color:#637485; font-weight:700;")

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
        for output in (self.debug_output, self.producer_output, self.det_output, self.ai_output, self.provider_output, self.agg_output):
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

    def _notify(self, title, message, icon=None, timeout_ms=6000):
        """Show a system-tray notification when available, otherwise fall back to a status message."""
        tray = getattr(self, "tray_icon", None)
        if tray is not None and tray.isVisible():
            tray.showMessage(title, message, icon or QSystemTrayIcon.Information, timeout_ms)
            return True
        self.append_debug(f"<b>{title}</b> {message}")
        return False

    def _setup_keyboard_shortcuts(self):
        shortcuts = [
            ("Ctrl+R", self.run_grader),
            ("Ctrl+D", self.open_current_form_review),
            ("Ctrl+Shift+A", self.open_answer_key_dashboard),
            ("Ctrl+A", self.open_manual_add_dialog),
            ("Ctrl+K", self._focus_form_search),
            ("Ctrl+E", self.export_results_csv_dialog),
            ("Ctrl+Shift+S", self.stop_grading),
            ("Delete", self.remove_form),
        ]
        for key, handler in shortcuts:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(handler)

    def _notify_pending_reviews(self):
        total = 0
        try:
            from answer_key_manager import load_pending_review_records
            for i in range(self.form_list.count()):
                meta = self.form_list.item(i).data(Qt.UserRole + 1) or {}
                form_id = meta.get("form_id")
                if form_id:
                    pending = load_pending_review_records(form_id) or {}
                    total += sum(len(v) for v in pending.values())
        except Exception:
            return
        if total > 0:
            self._notify(
                "Answers Awaiting Review",
                f"{total} question(s) need review in the Answer Keys dashboard.",
                QSystemTrayIcon.Warning,
            )

    def _focus_form_search(self):
        if hasattr(self, "form_search_input"):
            self.form_search_input.setFocus()
            self.form_search_input.selectAll()

    def open_decision_audit_viewer(self):
        viewer = DecisionAuditViewer(self._get_audit_path(), self)
        viewer.exec_()

    def _get_audit_path(self):
        try:
            with open("config.json", "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            return cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")
        except Exception:
            return "logs/grading_decisions.jsonl"

    def export_results_csv_dialog(self):
        records = load_audit_records(self._get_audit_path())
        if not records:
            QMessageBox.information(self, "No Data", "No grading decision audit records were found to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Grading Results", "grading_results.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        import csv

        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["timestamp", "decision", "final_score", "confidence", "latency_ms", "stage_reached", "answer", "expected"])
            for record in records:
                writer.writerow([
                    record.get("timestamp", ""),
                    record.get("decision", ""),
                    record.get("final_score", ""),
                    record.get("confidence", ""),
                    record.get("latency_ms", ""),
                    record.get("stage_reached", ""),
                    record.get("answer", ""),
                    record.get("expected", ""),
                ])
        self.append_debug(f"<font color='green'>[EXPORT] Wrote {len(records)} results to {path}</font>")

    def generate_run_report(self):
        records = load_audit_records(self._get_audit_path())
        if not records:
            QMessageBox.information(self, "No Report Data", "No grading decision records were found to summarize.")
            return
        yes = sum(1 for r in records if str(r.get("decision", "")).upper() == "YES")
        no = sum(1 for r in records if str(r.get("decision", "")).upper() == "NO")
        review = sum(1 for r in records if str(r.get("decision", "")).upper() in ("REVIEW", "ABSTAIN"))
        scores = [r.get("final_score") for r in records if isinstance(r.get("final_score"), (int, float))]
        latencies = [r.get("latency_ms") for r in records if isinstance(r.get("latency_ms"), (int, float))]
        total = len(records)
        avg_score = sum(scores) / len(scores) if scores else 0
        avg_latency = sum(latencies) / len(latencies) if latencies else 0

        os.makedirs("Reports", exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = os.path.join("Reports", f"run_summary_{timestamp}.md")
        lines = [
            f"# Grading Run Summary — {datetime.now():%Y-%m-%d %H:%M}",
            "",
            f"- Answers evaluated: {total}",
            f"- Accepted (YES): {yes}",
            f"- Rejected (NO): {no}",
            f"- Awaiting review / abstained: {review}",
        ]
        if avg_score is not None:
            lines.append(f"- Average final score: {avg_score:.3f}")
        if avg_latency is not None:
            lines.append(f"- Average latency: {avg_latency:.0f} ms")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self._notify("Run Report Generated", f"Wrote {total} decisions to Reports/.")
        if os.path.exists(path):
            os.startfile(os.path.abspath("Reports")) if sys.platform == "win32" else None

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
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        scroll_widget = QWidget()
        scroll_widget.setMinimumWidth(0)
        settings_layout = QVBoxLayout(scroll_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(12)

        def make_settings_section(title, description=""):
            section = QFrame(scroll_widget)
            section.setObjectName("SettingsSection")
            section.setFrameShape(QFrame.StyledPanel)
            section.setStyleSheet(
                "QFrame#SettingsSection {"
                "background: #ffffff;"
                "border: 1px solid #d6dde5;"
                "border-radius: 8px;"
                "}"
                "QLabel#SettingsSectionTitle {"
                "font-weight: 700;"
                "font-size: 14px;"
                "color: #111827;"
                "}"
                "QLabel#SettingsSectionDescription {"
                "color: #5f6b7a;"
                "}"
            )
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(14, 12, 14, 14)
            section_layout.setSpacing(8)
            title_label = QLabel(title, section)
            title_label.setObjectName("SettingsSectionTitle")
            section_layout.addWidget(title_label)
            if description:
                description_label = QLabel(description, section)
                description_label.setObjectName("SettingsSectionDescription")
                description_label.setWordWrap(True)
                section_layout.addWidget(description_label)
            section_form = QFormLayout()
            section_form.setContentsMargins(0, 4, 0, 0)
            section_form.setSpacing(8)
            section_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            section_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            section_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            section_layout.addLayout(section_form)
            settings_layout.addWidget(section)
            return section_form

        global_form = make_settings_section(
            "Global Settings",
            "General grading behavior, cache policy, application workers, and run safety.",
        )
        openrouter_form = make_settings_section(
            "OpenRouter",
            "Cloud provider routing, concurrency, cost controls, and answer batching.",
        )
        llamacpp_form = make_settings_section(
            "llama.cpp",
            "Local GGUF provider settings, server location, cleanup behavior, and judge models.",
        )
        ollama_form = make_settings_section(
            "Ollama",
            "Local Ollama model choices, monitoring model, provider capacity, and generation limits.",
        )
        settings_layout.addStretch()
        scroll_widget.setLayout(settings_layout)
        scroll_area.setWidget(scroll_widget)
        main_layout.addWidget(scroll_area)

        evaluator_combo = QComboBox(dialog)
        evaluator_combo.addItems([
            "ai_evaluator (Basic)",
            "ai_evaluator_2 (Advanced)",
            "ai_evaluator_semantic (Semantic Pipeline)",
        ])

        strictness_combo = QComboBox(dialog)
        strictness_combo.addItems(["strict", "balanced", "lenient", "review-heavy", "practice"])
        strictness_combo.setToolTip(
            "Controls how the final AI votes become Accepted, Needs review, or Rejected. "
            "Strict requires stronger independent agreement; lenient/practice accept more high-confidence equivalent answers."
        )
        provider_strategy_combo = QComboBox(dialog)
        provider_strategy_combo.addItems([
            "free_first_ollama_fallback",
            "openrouter_llamacpp_ollama",
            "openrouter_llamacpp",
            "llamacpp_openrouter",
            "local_all",
            "custom_priority",
            "free_first_paid_fallback",
            "cheap_paid_only",
            "openrouter_only",
            "llamacpp_only",
            "ollama_only",
        ])
        provider_strategy_combo.setToolTip(
            "Controls provider routing. Paid strategies use the cheap paid fallback model list and respect the spend cap."
        )
        provider_priority_edit = QLineEdit(dialog)
        provider_priority_edit.setText("openrouter,llamacpp,ollama")
        provider_priority_edit.setToolTip(
            "Custom provider order used by custom_priority and legacy/default routing. Example: openrouter,llamacpp,ollama"
        )
        max_openrouter_spend_spin = QDoubleSpinBox(dialog)
        max_openrouter_spend_spin.setRange(0.0, 100.0)
        max_openrouter_spend_spin.setSingleStep(0.10)
        max_openrouter_spend_spin.setDecimals(2)
        max_openrouter_spend_spin.setToolTip("0 means no OpenRouter spend cap for the current app run.")

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
        dedup_checkbox = QCheckBox("Deduplicated mode: group equivalent responses before evaluation", dialog)
        dedup_checkbox.setToolTip(
            "On: normalize/group equivalent responses and evaluate one representative.\n"
            "Off: raw mode; take every response exactly as read from the form, with no pre-deduplication."
        )
        audit_path_edit = QLineEdit(dialog)
        benchmark_path_edit = QLineEdit(dialog)

        cfg = {}
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        provider_priority_edit.setText(",".join(str(x) for x in cfg.get("provider_priority", ["openrouter", "llamacpp", "ollama"])))

        def normalize_model_key(model_name):
            text = str(model_name or "").strip()
            return text[:-7] if text.endswith(":latest") else text

        def add_model_choice(model_names, seen_keys, model_name):
            text = str(model_name or "").strip()
            key = normalize_model_key(text)
            if text and key and key not in seen_keys:
                model_names.append(text)
                seen_keys.add(key)

        ollama_models = []
        llamacpp_model_dir = cfg.get("llamacpp_model_dir", r"C:\Users\regis\.lmstudio\models")
        llamacpp_models = []

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
        supervisor_model = cfg.get(
            "openrouter_supervisor_ollama_model",
            DEFAULT_CONFIG.get("openrouter_supervisor_ollama_model", "gpt-oss:latest"),
        )
        add_model_choice(available_models, seen_model_keys, supervisor_model)
        for model_name in ollama_models:
            add_model_choice(available_models, seen_model_keys, model_name)

        extra_configured_models = sorted(
            key for key in seen_model_keys if key not in ollama_keys
        )
        installed_model_count = len(ollama_keys)
        model_status_label = QLabel(
            f"{len(available_models)} selectable models "
            f"({installed_model_count} installed"
            f"{', ' + str(len(extra_configured_models)) + ' configured only' if extra_configured_models else ''}; "
            "llama.cpp scan pending).",
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
        visible_settings_jury_roles = ("semantic_judge", "factual_judge", "concept_judge", "strict_judge")
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
            if role not in visible_settings_jury_roles:
                combo.hide()
                jury_role_labels[role].hide()

        llamacpp_role_combos = {}
        llamacpp_role_labels = {}
        cfg_llamacpp = cfg.get("llamacpp_models", {}) if cfg else {}
        for role in jury_defaults:
            combo = QComboBox(dialog)
            configured = cfg_llamacpp.get(role, [])
            if isinstance(configured, str):
                configured = [configured]
            role_models = list(llamacpp_models)
            for configured_model in reversed([str(m).strip() for m in configured if str(m).strip()]):
                if configured_model not in role_models:
                    role_models.insert(0, configured_model)
            if role_models:
                combo.addItems(role_models)
            else:
                combo.addItem("No llama.cpp GGUF models found")
                combo.setEnabled(False)
            if configured:
                combo.setCurrentText(str(configured[0]))
            combo.setToolTip(
                "Select a GGUF model found under the llama.cpp model folder. "
                "Projector/mmproj files are hidden because they are not grading models."
            )
            llamacpp_role_combos[role] = combo
            llamacpp_role_labels[role] = QLabel("llama.cpp " + role.replace('_', ' ').title() + ":", dialog)
            if role not in visible_settings_jury_roles:
                combo.hide()
                llamacpp_role_labels[role].hide()

        def combo_contains(combo, text):
            target = normalize_model_key(text)
            return any(normalize_model_key(combo.itemText(i)) == target for i in range(combo.count()))

        def add_combo_choice(combo, text):
            text = str(text or "").strip()
            if text and not combo_contains(combo, text):
                combo.addItem(text)

        def apply_discovered_models(discovered_ollama, discovered_llamacpp, error_text):
            discovered_ollama = [str(m).strip() for m in discovered_ollama or [] if str(m).strip()]
            discovered_llamacpp = [str(m).strip() for m in discovered_llamacpp or [] if str(m).strip()]
            for model_name in discovered_ollama:
                for combo in [model_combo, embedding_model_combo, reasoning_model_combo, supervisor_model_combo, *jury_combos.values()]:
                    add_combo_choice(combo, model_name)
            for role, combo in llamacpp_role_combos.items():
                current = combo.currentText().strip()
                placeholder = current == "No llama.cpp GGUF models found"
                if placeholder:
                    combo.clear()
                for model_name in discovered_llamacpp:
                    add_combo_choice(combo, model_name)
                if placeholder and combo.count() == 0:
                    combo.addItem("No llama.cpp GGUF models found")
                    combo.setEnabled(False)
                else:
                    combo.setEnabled(True)
                    configured = cfg_llamacpp.get(role, [])
                    if isinstance(configured, str):
                        configured = [configured]
                    preferred = str((configured or [""])[0]).strip()
                    if preferred and combo_contains(combo, preferred):
                        combo.setCurrentText(preferred)
                    elif current and current != "No llama.cpp GGUF models found" and combo_contains(combo, current):
                        combo.setCurrentText(current)
            ollama_keys_now = {
                normalize_model_key(model_name)
                for model_name in discovered_ollama
                if normalize_model_key(model_name)
            }
            extra_now = sorted(key for key in seen_model_keys if key not in ollama_keys_now)
            model_status_label.setText(
                f"{model_combo.count()} selectable models "
                f"({len(ollama_keys_now)} installed"
                f"{', ' + str(len(extra_now)) + ' configured only' if extra_now else ''}; "
                f"{len(discovered_llamacpp)} llama.cpp GGUF models found)."
            )
            if error_text:
                model_status_label.setToolTip(str(error_text))
            refresh_jury_status()

        model_discovery_thread = SettingsModelDiscoveryThread(llamacpp_model_dir)
        dialog._model_discovery_thread = model_discovery_thread
        model_discovery_thread.finished.connect(apply_discovered_models)
        model_discovery_thread.start()

        report_checkbox = QCheckBox("Generate Report", dialog)
        dedup_checkbox.setChecked(cfg.get("enable_deduplication", True))
        legacy_judge_answer_batch_size = max(1, int(cfg.get("judge_answer_batch_size", 3)))
        ollama_judge_answer_batch_size_spin = QSpinBox(dialog)
        ollama_judge_answer_batch_size_spin.setRange(1, 20)
        ollama_judge_answer_batch_size_spin.setValue(
            max(1, int(cfg.get("ollama_judge_answer_batch_size", legacy_judge_answer_batch_size)))
        )
        ollama_judge_answer_batch_size_spin.setToolTip(
            "How many student answers are sent to each local Ollama judge call. "
            "Use 1 for best reliability on local models and limited hardware."
        )
        openrouter_judge_answer_batch_size_spin = QSpinBox(dialog)
        openrouter_judge_answer_batch_size_spin.setRange(1, 50)
        openrouter_judge_answer_batch_size_spin.setValue(
            max(1, int(cfg.get("openrouter_judge_answer_batch_size", legacy_judge_answer_batch_size)))
        )
        openrouter_judge_answer_batch_size_spin.setToolTip(
            "How many student answers are sent to each OpenRouter judge call. "
            "Higher values can improve throughput but may increase malformed JSON risk."
        )
        llamacpp_judge_answer_batch_size_spin = QSpinBox(dialog)
        llamacpp_judge_answer_batch_size_spin.setRange(1, 1)
        llamacpp_judge_answer_batch_size_spin.setValue(1)
        llamacpp_judge_answer_batch_size_spin.setToolTip(
            "llama.cpp is capped at 1 answer per judge call to avoid malformed local batch JSON."
        )
        legacy_ai_worker_count = max(1, int(cfg.get("ai_worker_count", 4) or 4))
        openrouter_ai_worker_count_spin = QSpinBox(dialog)
        openrouter_ai_worker_count_spin.setRange(1, 12)
        openrouter_ai_worker_count_spin.setValue(
            max(1, int(cfg.get("openrouter_ai_worker_count", legacy_ai_worker_count) or legacy_ai_worker_count))
        )
        openrouter_ai_worker_count_spin.setToolTip(
            "Application AI worker threads when OpenRouter is active. Higher values process more questions in parallel. "
            "Changes apply to the next grading run."
        )
        ollama_ai_worker_count_spin = QSpinBox(dialog)
        ollama_ai_worker_count_spin.setRange(1, 4)
        ollama_ai_worker_count_spin.setValue(max(1, int(cfg.get("ollama_ai_worker_count", 1) or 1)))
        ollama_ai_worker_count_spin.setToolTip(
            "Application AI worker threads when Ollama is active. Keep low unless your local hardware can handle parallel model work. "
            "Changes apply to the next grading run."
        )
        llamacpp_ai_worker_count_spin = QSpinBox(dialog)
        llamacpp_ai_worker_count_spin.setRange(1, 1)
        llamacpp_ai_worker_count_spin.setValue(1)
        llamacpp_ai_worker_count_spin.setToolTip(
            "llama.cpp is capped at 1 application AI worker so local GGUF grading stays serial and reliable."
        )
        openrouter_worker_count_spin = QSpinBox(dialog)
        openrouter_worker_count_spin.setRange(1, 12)
        openrouter_worker_count_spin.setValue(max(1, int(cfg.get("openrouter_worker_count", 4) or 4)))
        openrouter_worker_count_spin.setToolTip(
            "OpenRouter provider worker threads. Higher values allow more concurrent OpenRouter API calls. "
            "Changes apply to the next grading run."
        )
        ollama_worker_count_spin = QSpinBox(dialog)
        ollama_worker_count_spin.setRange(1, 4)
        ollama_worker_count_spin.setValue(max(1, int(cfg.get("ollama_worker_count", 1) or 1)))
        ollama_worker_count_spin.setToolTip(
            "Ollama provider worker threads. Keep this at 1 unless your local hardware can run multiple model requests efficiently. "
            "Changes apply to the next grading run."
        )
        llamacpp_worker_count_spin = QSpinBox(dialog)
        llamacpp_worker_count_spin.setRange(1, 1)
        llamacpp_worker_count_spin.setValue(1)
        llamacpp_worker_count_spin.setToolTip(
            "llama.cpp is capped at 1 provider worker because local GGUF models share one server/hardware lane."
        )
        llamacpp_enabled_checkbox = QCheckBox("Enable llama.cpp provider", dialog)
        llamacpp_enabled_checkbox.setChecked(bool(cfg.get("llamacpp_enabled", True)))
        llamacpp_require_server_checkbox = QCheckBox("Require running llama.cpp server", dialog)
        llamacpp_require_server_checkbox.setChecked(bool(cfg.get("llamacpp_require_server", True)))
        llamacpp_auto_start_checkbox = QCheckBox("Start llama.cpp server automatically when needed", dialog)
        llamacpp_auto_start_checkbox.setChecked(bool(cfg.get("llamacpp_auto_start_server", True)))
        llamacpp_auto_start_checkbox.setToolTip(
            "When llama.cpp-only grading is selected and no server is responding, start llama-server.exe using the selected local model."
        )
        llamacpp_stop_after_grading_checkbox = QCheckBox("Stop llama.cpp server after grading", dialog)
        llamacpp_stop_after_grading_checkbox.setChecked(bool(cfg.get("llamacpp_stop_server_after_grading", False)))
        llamacpp_stop_after_grading_checkbox.setToolTip(
            "When grading finishes, stop llama-server.exe to release RAM used by local GGUF models. "
            "Leave off if you use the same llama.cpp server in another app."
        )
        llamacpp_stop_on_close_checkbox = QCheckBox("Stop llama.cpp server when app closes", dialog)
        llamacpp_stop_on_close_checkbox.setChecked(bool(cfg.get("llamacpp_stop_server_on_app_close", False)))
        llamacpp_stop_on_close_checkbox.setToolTip(
            "When this app closes, stop llama-server.exe to release RAM used by local GGUF models. "
            "This does not close LM Studio itself."
        )
        llamacpp_base_url_edit = QLineEdit(dialog)
        llamacpp_base_url_edit.setText(str(cfg.get("llamacpp_api_base_url", "http://127.0.0.1:8080")))
        llamacpp_context_size_spin = QSpinBox(dialog)
        llamacpp_context_size_spin.setRange(512, 1048576)
        llamacpp_context_size_spin.setValue(max(512, int(cfg.get("llamacpp_server_context_size", 32768) or 32768)))
        llamacpp_gpu_layers_combo = QComboBox(dialog)
        llamacpp_gpu_layers_combo.setEditable(True)
        llamacpp_gpu_layers_combo.addItems(["auto", "all", "0"])
        llamacpp_gpu_layers_combo.setCurrentText(str(cfg.get("llamacpp_server_gpu_layers", "auto")))
        llamacpp_threads_spin = QSpinBox(dialog)
        llamacpp_threads_spin.setRange(1, 256)
        llamacpp_threads_spin.setValue(max(1, int(cfg.get("llamacpp_server_threads", 8) or 8)))
        llamacpp_threads_batch_spin = QSpinBox(dialog)
        llamacpp_threads_batch_spin.setRange(1, 256)
        llamacpp_threads_batch_spin.setValue(max(1, int(cfg.get("llamacpp_server_threads_batch", 8) or 8)))
        llamacpp_server_batch_size_spin = QSpinBox(dialog)
        llamacpp_server_batch_size_spin.setRange(1, 8192)
        llamacpp_server_batch_size_spin.setValue(max(1, int(cfg.get("llamacpp_server_batch_size", 1024) or 1024)))
        llamacpp_server_ubatch_size_spin = QSpinBox(dialog)
        llamacpp_server_ubatch_size_spin.setRange(1, 8192)
        llamacpp_server_ubatch_size_spin.setValue(max(1, int(cfg.get("llamacpp_server_ubatch_size", 512) or 512)))
        llamacpp_flash_attn_combo = QComboBox(dialog)
        llamacpp_flash_attn_combo.addItems(["auto", "on", "off"])
        llamacpp_flash_attn_combo.setCurrentText(str(cfg.get("llamacpp_server_flash_attn", "auto")).lower())
        llama_cache_types = ["q8_0", "f16", "bf16", "q4_0", "q4_1", "q5_0", "q5_1", "f32", "iq4_nl"]
        llamacpp_cache_type_k_combo = QComboBox(dialog)
        llamacpp_cache_type_k_combo.addItems(llama_cache_types)
        llamacpp_cache_type_k_combo.setCurrentText(str(cfg.get("llamacpp_server_cache_type_k", "q8_0")).lower())
        llamacpp_cache_type_v_combo = QComboBox(dialog)
        llamacpp_cache_type_v_combo.addItems(llama_cache_types)
        llamacpp_cache_type_v_combo.setCurrentText(str(cfg.get("llamacpp_server_cache_type_v", "q8_0")).lower())
        llamacpp_parallel_spin = QSpinBox(dialog)
        llamacpp_parallel_spin.setRange(1, 32)
        llamacpp_parallel_spin.setValue(max(1, int(cfg.get("llamacpp_server_parallel", 1) or 1)))
        llamacpp_mmap_checkbox = QCheckBox("Enable model memory mapping (--mmap)", dialog)
        llamacpp_mmap_checkbox.setChecked(bool(cfg.get("llamacpp_server_mmap", True)))
        llamacpp_jinja_checkbox = QCheckBox("Enable Jinja chat templates (--jinja)", dialog)
        llamacpp_jinja_checkbox.setChecked(bool(cfg.get("llamacpp_server_jinja", True)))
        llamacpp_server_exe_edit = QLineEdit(dialog)
        llamacpp_server_exe_edit.setText(str(cfg.get("llamacpp_server_executable", r"C:\Tools\llama.cpp\llama-server.exe")))
        llamacpp_server_exe_picker = QWidget(dialog)
        llamacpp_server_exe_picker_layout = QHBoxLayout(llamacpp_server_exe_picker)
        llamacpp_server_exe_picker_layout.setContentsMargins(0, 0, 0, 0)
        llamacpp_server_exe_picker_layout.setSpacing(6)
        llamacpp_server_exe_browse_btn = QPushButton("Browse...", dialog)
        llamacpp_server_exe_browse_btn.setToolTip("Choose llama-server.exe.")
        llamacpp_server_exe_picker_layout.addWidget(llamacpp_server_exe_edit, 1)
        llamacpp_server_exe_picker_layout.addWidget(llamacpp_server_exe_browse_btn)

        def browse_llamacpp_server_exe():
            current_exe = os.path.expandvars(os.path.expanduser(llamacpp_server_exe_edit.text().strip()))
            current_dir = os.path.dirname(current_exe) if current_exe else ""
            if not current_dir or not os.path.isdir(current_dir):
                current_dir = os.path.expanduser("~")
            selected_exe, _filter = QFileDialog.getOpenFileName(
                dialog,
                "Select llama-server.exe",
                current_dir,
                "Executable Files (*.exe);;All Files (*)",
            )
            if selected_exe:
                llamacpp_server_exe_edit.setText(selected_exe)

        llamacpp_server_exe_browse_btn.clicked.connect(browse_llamacpp_server_exe)
        llamacpp_model_dir_edit = QLineEdit(dialog)
        llamacpp_model_dir_edit.setText(str(llamacpp_model_dir))
        llamacpp_model_dir_picker = QWidget(dialog)
        llamacpp_model_dir_picker_layout = QHBoxLayout(llamacpp_model_dir_picker)
        llamacpp_model_dir_picker_layout.setContentsMargins(0, 0, 0, 0)
        llamacpp_model_dir_picker_layout.setSpacing(6)
        llamacpp_model_dir_browse_btn = QPushButton("Browse...", dialog)
        llamacpp_model_dir_browse_btn.setToolTip("Choose the root folder that contains llama.cpp GGUF models.")
        llamacpp_model_dir_picker_layout.addWidget(llamacpp_model_dir_edit, 1)
        llamacpp_model_dir_picker_layout.addWidget(llamacpp_model_dir_browse_btn)

        def browse_llamacpp_model_dir():
            current_dir = os.path.expandvars(os.path.expanduser(llamacpp_model_dir_edit.text().strip()))
            if not current_dir or not os.path.isdir(current_dir):
                current_dir = os.path.expanduser("~")
            selected_dir = QFileDialog.getExistingDirectory(
                dialog,
                "Select llama.cpp Model Folder",
                current_dir,
            )
            if selected_dir:
                llamacpp_model_dir_edit.setText(selected_dir)

        llamacpp_model_dir_browse_btn.clicked.connect(browse_llamacpp_model_dir)
        supervisor_model_combo = QComboBox(dialog)
        supervisor_model_combo.setToolTip(
            "Local Ollama model used to audit OpenRouter judge quality. "
            "This does not grade student answers directly unless OpenRouter falls back to Ollama."
        )
        if available_models:
            supervisor_model_combo.addItems(available_models)
        if supervisor_model and normalize_model_key(supervisor_model) not in {
            normalize_model_key(supervisor_model_combo.itemText(i))
            for i in range(supervisor_model_combo.count())
        }:
            supervisor_model_combo.insertItem(0, supervisor_model)
        supervisor_model_combo.setCurrentText(str(supervisor_model or "gpt-oss:latest"))
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
            visible_jury_roles = set(visible_settings_jury_roles)
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
        strictness_combo.setCurrentText(cfg.get("grading_strictness", cfg.get("leniency", "balanced")))
        provider_strategy_combo.setCurrentText(cfg.get("provider_strategy", "free_first_ollama_fallback"))
        max_openrouter_spend_spin.setValue(float(cfg.get("max_openrouter_spend_usd_per_run", 0.0) or 0.0))
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
        for retired_widget in (
            evaluator_combo,
            model_combo,
            embedding_model_combo,
            reasoning_model_combo,
            audit_path_edit,
            benchmark_path_edit,
            batch_size_spin,
            batch_auto_checkbox,
        ):
            retired_widget.hide()

        # Set Grade Mode from config
        grading_mode_combo.setCurrentText(cfg.get("grading_mode", "Whole Form"))
        execution_mode_combo.setCurrentText(
            normalize_execution_mode(cfg.get("execution_mode", DEFAULT_EXECUTION_MODE))
        )

        execution_mode_combo.currentTextChanged.connect(refresh_jury_status)
        refresh_jury_status()

        ignore_cache_checkbox = QCheckBox("Always grade from fresh data (ignore previous-run cache)", dialog)
        ignore_cache_checkbox.setChecked(bool(cfg.get("ignore_grading_cache", True)))
        ignore_cache_checkbox.setToolTip(
            "Before every grading run, remove cached results, rubrics, embeddings, context, "
            "validation data, Recent Only history, and pending Answer Keys reviews. "
            "Caching is still allowed within that run."
        )
        truncate_checkbox = QCheckBox(
            "Truncate answer variants before grading (keep only teacher's first answer)", dialog
        )
        truncate_checkbox.setChecked(bool(cfg.get("truncate_answers_before_grading", False)))
        truncate_checkbox.setToolTip(
            "DESTRUCTIVE: When enabled, before grading each targeted form the system will remove all answer-key variants\n"
            "leaving only the first teacher-provided answer. Backups are created automatically before changes."
        )

        force_ai_checkbox = QCheckBox("Send every answer through the full AI jury", dialog)
        force_ai_checkbox.setChecked(bool(cfg.get("force_ai_jury_for_all_answers", True)))
        force_ai_checkbox.setToolTip(
            "Mistral NeMo evaluates meaning, Gemma verifies facts/mathematics, and Phi-4 "
            "challenges completeness. GPT-OSS adjudicates disagreements, ambiguity, invalid output, or low confidence."
        )

        global_form.addRow("Grade Mode:", grading_mode_combo)
        global_form.addRow("Execution Mode:", execution_mode_combo)
        global_form.addRow("Grading Strictness:", strictness_combo)
        global_form.addRow("Minimum Judge Confidence:", minimum_judge_confidence_spin)
        global_form.addRow("Acceptance Diversity:", distinct_models_checkbox)
        global_form.addRow("Answer-Key Automation:", key_auto_add_checkbox)
        global_form.addRow("Slow Model Handling:", patient_ai_checkbox)
        global_form.addRow("AI Evaluation:", force_ai_checkbox)
        global_form.addRow("Answer Processing:", dedup_checkbox)
        global_form.addRow("Cache Reuse:", ignore_cache_checkbox)
        global_form.addRow("Truncate Answers:", truncate_checkbox)
        global_form.addRow("Reports:", report_checkbox)
        global_form.addRow("Heartbeat Timeout:", heartbeat_timeout_spin)
        global_form.addRow("Heartbeat Interval:", heartbeat_interval_spin)
        global_form.addRow("Heartbeat Restarts:", heartbeat_max_restarts_spin)

        openrouter_form.addRow("Provider Strategy:", provider_strategy_combo)
        openrouter_form.addRow("Provider Priority:", provider_priority_edit)
        openrouter_form.addRow("AI Worker Threads:", openrouter_ai_worker_count_spin)
        openrouter_form.addRow("Provider Workers:", openrouter_worker_count_spin)
        openrouter_form.addRow("Answers per Judge Call:", openrouter_judge_answer_batch_size_spin)
        openrouter_form.addRow("Spend Cap ($):", max_openrouter_spend_spin)

        llamacpp_form.addRow("Provider Enabled:", llamacpp_enabled_checkbox)
        llamacpp_form.addRow("AI Worker Threads:", llamacpp_ai_worker_count_spin)
        llamacpp_form.addRow("Provider Workers:", llamacpp_worker_count_spin)
        llamacpp_form.addRow("Answers per Judge Call:", llamacpp_judge_answer_batch_size_spin)
        llamacpp_form.addRow("Server URL:", llamacpp_base_url_edit)
        llamacpp_form.addRow("Auto-start Server:", llamacpp_auto_start_checkbox)
        llamacpp_form.addRow("Server Executable:", llamacpp_server_exe_picker)
        llamacpp_form.addRow("Model Folder:", llamacpp_model_dir_picker)
        llamacpp_form.addRow("Context Size:", llamacpp_context_size_spin)
        llamacpp_form.addRow("GPU Layers:", llamacpp_gpu_layers_combo)
        llamacpp_form.addRow("Generation Threads:", llamacpp_threads_spin)
        llamacpp_form.addRow("Batch Threads:", llamacpp_threads_batch_spin)
        llamacpp_form.addRow("Server Batch Size:", llamacpp_server_batch_size_spin)
        llamacpp_form.addRow("Server Micro-batch:", llamacpp_server_ubatch_size_spin)
        llamacpp_form.addRow("Flash Attention:", llamacpp_flash_attn_combo)
        llamacpp_form.addRow("K Cache Type:", llamacpp_cache_type_k_combo)
        llamacpp_form.addRow("V Cache Type:", llamacpp_cache_type_v_combo)
        llamacpp_form.addRow("Parallel Slots:", llamacpp_parallel_spin)
        llamacpp_form.addRow("Memory Mapping:", llamacpp_mmap_checkbox)
        llamacpp_form.addRow("Chat Templates:", llamacpp_jinja_checkbox)
        llamacpp_form.addRow("Server Check:", llamacpp_require_server_checkbox)
        llamacpp_form.addRow("After Grading:", llamacpp_stop_after_grading_checkbox)
        llamacpp_form.addRow("On App Close:", llamacpp_stop_on_close_checkbox)
        for role in visible_settings_jury_roles:
            llamacpp_form.addRow(llamacpp_role_labels[role], llamacpp_role_combos[role])

        ollama_form.addRow("Model Choices:", model_status_label)
        for role in visible_settings_jury_roles:
            ollama_form.addRow(jury_role_labels[role], jury_combos[role])
        ollama_form.addRow("Jury Roles:", jury_status_label)
        ollama_form.addRow("AI Worker Threads:", ollama_ai_worker_count_spin)
        ollama_form.addRow("Provider Workers:", ollama_worker_count_spin)
        ollama_form.addRow("Answers per Judge Call:", ollama_judge_answer_batch_size_spin)
        ollama_form.addRow("OpenRouter Monitor Model:", supervisor_model_combo)
        ollama_form.addRow("Judge Context:", judge_num_ctx_spin)
        ollama_form.addRow("Judge Output Tokens:", judge_num_predict_spin)

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
        main_layout.addWidget(buttons)

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

            config_data["grading_strictness"] = strictness_combo.currentText()
            config_data["leniency"] = strictness_combo.currentText()
            config_data["provider_strategy"] = provider_strategy_combo.currentText()
            priority = [
                part.strip().lower()
                for part in provider_priority_edit.text().split(",")
                if part.strip().lower() in {"openrouter", "llamacpp", "ollama"}
            ]
            if priority:
                config_data["provider_priority"] = list(dict.fromkeys(priority))
            config_data["max_openrouter_spend_usd_per_run"] = float(max_openrouter_spend_spin.value())

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
            config_data["truncate_answers_before_grading"] = truncate_checkbox.isChecked()
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
            config_data["openrouter_ai_worker_count"] = int(openrouter_ai_worker_count_spin.value())
            config_data["llamacpp_ai_worker_count"] = 1
            config_data["ollama_ai_worker_count"] = int(ollama_ai_worker_count_spin.value())
            config_data["openrouter_worker_count"] = int(openrouter_worker_count_spin.value())
            config_data["llamacpp_worker_count"] = 1
            config_data["ollama_worker_count"] = int(ollama_worker_count_spin.value())
            config_data["llamacpp_enabled"] = llamacpp_enabled_checkbox.isChecked()
            config_data["llamacpp_require_server"] = llamacpp_require_server_checkbox.isChecked()
            config_data["llamacpp_auto_start_server"] = llamacpp_auto_start_checkbox.isChecked()
            config_data["llamacpp_server_executable"] = llamacpp_server_exe_edit.text().strip() or "llama-server.exe"
            config_data["llamacpp_stop_server_after_grading"] = llamacpp_stop_after_grading_checkbox.isChecked()
            config_data["llamacpp_stop_server_on_app_close"] = llamacpp_stop_on_close_checkbox.isChecked()
            config_data["llamacpp_api_base_url"] = llamacpp_base_url_edit.text().strip() or "http://127.0.0.1:8080"
            config_data["llamacpp_model_dir"] = llamacpp_model_dir_edit.text().strip() or r"C:\Users\regis\.lmstudio\models"
            gpu_layers_text = llamacpp_gpu_layers_combo.currentText().strip().lower() or "auto"
            if gpu_layers_text not in {"auto", "all"}:
                try:
                    gpu_layers_text = str(max(0, int(gpu_layers_text)))
                except ValueError:
                    gpu_layers_text = "auto"
            config_data["llamacpp_server_context_size"] = int(llamacpp_context_size_spin.value())
            config_data["llamacpp_server_gpu_layers"] = gpu_layers_text
            config_data["llamacpp_server_threads"] = int(llamacpp_threads_spin.value())
            config_data["llamacpp_server_threads_batch"] = int(llamacpp_threads_batch_spin.value())
            config_data["llamacpp_server_batch_size"] = int(llamacpp_server_batch_size_spin.value())
            config_data["llamacpp_server_ubatch_size"] = int(llamacpp_server_ubatch_size_spin.value())
            config_data["llamacpp_server_flash_attn"] = llamacpp_flash_attn_combo.currentText()
            config_data["llamacpp_server_cache_type_k"] = llamacpp_cache_type_k_combo.currentText()
            config_data["llamacpp_server_cache_type_v"] = llamacpp_cache_type_v_combo.currentText()
            config_data["llamacpp_server_parallel"] = int(llamacpp_parallel_spin.value())
            config_data["llamacpp_server_mmap"] = llamacpp_mmap_checkbox.isChecked()
            config_data["llamacpp_server_jinja"] = llamacpp_jinja_checkbox.isChecked()
            selected_llamacpp = {}
            for role, combo in llamacpp_role_combos.items():
                try:
                    sel = combo.currentText().strip()
                except Exception:
                    sel = ""
                if sel == "No llama.cpp GGUF models found":
                    sel = ""
                selected_llamacpp[role] = [sel] if sel else []
            config_data["llamacpp_models"] = selected_llamacpp
            if supervisor_model_combo.currentText():
                config_data["openrouter_supervisor_ollama_model"] = supervisor_model_combo.currentText()
            # Keep provider-level capacity in ProviderManager; application workers may
            # process multiple questions while Ollama remains capped by ollama_worker_count.
            config_data["max_concurrent_judge_http"] = 1
            config_data["max_concurrent_jury_answers"] = max(
                1,
                effective_ai_worker_count(config_data),
            )
            config_data["enable_async_judges"] = False
            config_data["sync_judge_parallelism"] = 1
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

            config_data["enable_deduplication"] = dedup_checkbox.isChecked()
            config_data["ollama_judge_answer_batch_size"] = int(ollama_judge_answer_batch_size_spin.value())
            config_data["openrouter_judge_answer_batch_size"] = int(openrouter_judge_answer_batch_size_spin.value())
            config_data["llamacpp_judge_answer_batch_size"] = 1
            config_data["judge_answer_batch_size"] = int(openrouter_judge_answer_batch_size_spin.value())
            config_data["ai_worker_count"] = effective_ai_worker_count(config_data)
            if is_llamacpp_only(config_data):
                config_data["ai_worker_count"] = 1
                config_data["llamacpp_ai_worker_count"] = 1
                config_data["max_concurrent_jury_answers"] = 1

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
                self._sync_worker_cards_to_config()
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
            "completed": 0,
            "total": 0,
            "accepted": 0,
            "rejected": 0,
            "review_questions": 0,
            "elapsed": 0,
            "det_decisions": 0,
            "ai_decisions": 0,
            "avg_latency_ms": 0.0,
            "ai_backlog": 0,
            "current_model": "Idle",
        }

    def _status_label(self, status):
        return {
            "queued": "QUEUED",
            "running": "RUNNING",
            "done": "DONE",
            "failed": "FAILED",
            "skipped": "SKIPPED",
            "partial": "PARTIAL",
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

    def _queue_progress_percent(self, meta):
        status = str(meta.get("status", "queued"))
        if status == "done":
            return 100
        total = int(meta.get("total", 0) or 0)
        completed = int(meta.get("completed", 0) or 0)
        if status in {"failed", "skipped"}:
            return 0
        if status == "queued" and completed <= 0:
            return 0
        if total <= 0:
            return 0
        return max(0, min(100, int(round((completed / total) * 100))))

    def _queue_eta_text(self, meta):
        status = str(meta.get("status", "queued"))
        if status == "done":
            return "Done"
        if status == "failed":
            return "-"
        if status == "skipped":
            return "Skipped"
        if status == "partial":
            return "Partial"
        completed = int(meta.get("completed", 0) or 0)
        total = int(meta.get("total", 0) or 0)
        if status == "queued" and completed <= 0:
            return "--"
        eta = self._estimate_eta(
            completed,
            total,
            meta.get("elapsed", 0),
        )
        return eta if eta != "--:--" else "--"

    def _queue_detail_text(self, meta):
        detail = str(meta.get("detail") or "Waiting for its turn")
        source = str(meta.get("source") or "Queue")
        if detail and source:
            return f"{source} | {detail}"
        return detail or source

    def _make_form_row_widget(self, meta):
        card = QFrame()
        card.setObjectName("FormCard")
        card.setProperty("status", meta.get("status", "queued"))
        layout = QGridLayout(card)
        layout.setContentsMargins(8, 4, 10, 4)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(1)

        glyph = QLabel(">>")
        glyph.setObjectName("QueueGlyph")
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setFixedWidth(18)
        title = QLabel(meta.get("title", "Untitled"))
        title.setObjectName("FormTitle")
        title.setWordWrap(False)
        title.setToolTip(meta.get("title", "Untitled"))
        detail = QLabel(self._queue_detail_text(meta))
        detail.setObjectName("FormMeta")
        detail.setWordWrap(False)
        detail.setToolTip(self._format_form_meta_line(meta))
        progress = QProgressBar()
        progress.setObjectName("QueueProgress")
        progress.setRange(0, 100)
        progress.setValue(self._queue_progress_percent(meta))
        progress.setFormat("%p%")
        badge = QLabel(self._status_label(meta.get("status", "queued")))
        badge.setObjectName("StatusBadge")
        badge.setProperty("status", meta.get("status", "queued"))
        badge.setAlignment(Qt.AlignCenter)
        eta = QLabel(self._queue_eta_text(meta))
        eta.setObjectName("QueueEta")
        eta.setAlignment(Qt.AlignCenter)

        layout.addWidget(glyph, 0, 0, 2, 1)
        layout.addWidget(title, 0, 1)
        layout.addWidget(detail, 1, 1)
        layout.addWidget(progress, 0, 2, 2, 1)
        layout.addWidget(badge, 0, 3, 2, 1)
        layout.addWidget(eta, 0, 4, 2, 1)
        layout.setColumnStretch(1, 5)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(3, 2)
        layout.setColumnStretch(4, 1)
        layout.setColumnMinimumWidth(2, 74)
        layout.setColumnMinimumWidth(3, 68)
        layout.setColumnMinimumWidth(4, 44)
        card._title_label = title
        card._badge_label = badge
        card._detail_label = detail
        card._progress_bar = progress
        card._eta_label = eta
        card._glyph_label = glyph
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
        widget.setProperty("rowParity", "odd" if self.form_list.row(item) % 2 else "even")
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget._title_label.setText(meta.get("title", "Untitled"))
        widget._title_label.setToolTip(meta.get("title", "Untitled"))
        widget._badge_label.setText(self._status_label(status))
        widget._badge_label.setProperty("status", status)
        widget._badge_label.style().unpolish(widget._badge_label)
        widget._badge_label.style().polish(widget._badge_label)
        widget._detail_label.setText(self._queue_detail_text(meta))
        widget._detail_label.setToolTip(self._format_form_meta_line(meta))
        widget._progress_bar.setValue(self._queue_progress_percent(meta))
        widget._eta_label.setText(self._queue_eta_text(meta))
        item.setSizeHint(QSize(0, max(44, widget.sizeHint().height())))

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
        if status in {"done", "failed", "skipped", "partial"}:
            meta["finished_at"] = now
        item.setData(Qt.UserRole + 1, meta)
        self._refresh_form_row(item)
        self._refresh_queue_positions()

    def _refresh_queue_positions(self):
        counts = {"queued": 0, "running": 0, "done": 0, "partial": 0, "skipped": 0, "failed": 0}
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
                f"{counts.get('queued', 0)} queued | {counts.get('running', 0)} running | "
                f"{counts.get('done', 0)} done | {counts.get('partial', 0)} partial | "
                f"{counts.get('skipped', 0)} skipped | "
                f"{counts.get('failed', 0)} failed"
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

    def _item_at_pos(self, pos):
        item = self.form_list.itemAt(pos)
        if item is None:
            return None
        self.form_list.setCurrentItem(item)
        self.form_list.setCurrentRow(self.form_list.row(item))
        return item

    def _on_form_list_context_menu(self, pos):
        item = self._item_at_pos(pos)
        if item is None:
            return
        self._show_form_context_menu(item, pos)

    def _build_form_context_menu(self, item):
        meta = item.data(Qt.UserRole + 1) or {}
        url = item.data(Qt.UserRole)
        status = str(meta.get("status", "queued"))
        row = self.form_list.row(item)
        count = self.form_list.count()
        grading_busy = self.is_grading or (self.grader_thread and self.grader_thread.isRunning())

        menu = QMenu(self)
        act = menu.addAction("Grade Now")
        act.setEnabled(not grading_busy)
        act.triggered.connect(lambda: self._context_grade_now(url))
        act = menu.addAction("Open Answer Key Dashboard")
        act.triggered.connect(lambda: self._context_open_dashboard(url))
        menu.addSeparator()

        requeue = menu.addAction("Requeue (Reset to Queued)")
        requeue.setEnabled(status != "queued" and not grading_busy)
        requeue.triggered.connect(lambda: self._context_set_status(item, "queued"))
        done = menu.addAction("Mark as Done")
        done.setEnabled(status != "done")
        done.triggered.connect(lambda: self._context_set_status(item, "done"))
        skipped = menu.addAction("Mark as Skipped")
        skipped.setEnabled(status != "skipped")
        skipped.triggered.connect(lambda: self._context_set_status(item, "skipped"))
        menu.addSeparator()

        for label, where, enabled in (
            ("Move to Top", "top", row > 0),
            ("Move Up", "up", row > 0),
            ("Move Down", "down", row < count - 1),
            ("Move to Bottom", "bottom", row < count - 1),
        ):
            act = menu.addAction(label)
            act.setEnabled(enabled)
            act.triggered.connect(lambda _=False, w=where: self._context_move(item, w))
        menu.addSeparator()

        copy = menu.addAction("Copy URL")
        copy.triggered.connect(lambda: self._context_copy_url(url))
        browser = menu.addAction("Open in Browser")
        browser.triggered.connect(lambda: self._context_open_in_browser(url))
        menu.addSeparator()
        remove = menu.addAction("Remove from Queue")
        remove.triggered.connect(lambda: self._context_remove(item, url))
        return menu

    def _show_form_context_menu(self, item, widget_pos):
        menu = self._build_form_context_menu(item)
        # Keep a reference so the ephemeral menu isn't garbage-collected while open.
        self._active_context_menu = menu
        menu.exec_(self.form_list.viewport().mapToGlobal(widget_pos))
        self._active_context_menu = None

    def _context_grade_now(self, url):
        if self.is_grading or (self.grader_thread and self.grader_thread.isRunning()):
            self.append_debug("<font color='orange'>[QUEUE] Grading already in progress.</font>")
            return
        self.run_grader(target_urls=[url])

    def _context_open_dashboard(self, url):
        self.open_answer_key_dashboard(target_url=url)

    def _context_set_status(self, item, status):
        if status == "queued":
            meta = item.data(Qt.UserRole + 1) or {}
            for key in ("started_at", "finished_at", "completed", "total", "accepted", "rejected",
                        "review_questions", "elapsed", "det_decisions", "ai_decisions",
                        "avg_latency_ms", "ai_backlog", "current_model"):
                meta.pop(key, None)
            meta["detail"] = "Waiting for its turn"
            item.setData(Qt.UserRole + 1, meta)
            self._set_form_status(item, "queued", "Waiting for its turn")
            self.save_forms()
        else:
            self._set_form_status(item, status, f"Manually marked as {status}")
            self.save_forms()

    def _context_move(self, item, where):
        widget = self.form_list.itemWidget(item)
        row = self.form_list.row(item)
        count = self.form_list.count()
        if where == "top":
            target = 0
        elif where == "bottom":
            target = count - 1
        elif where == "up":
            target = row - 1
        else:
            target = row + 1
        if target < 0 or target >= count or target == row:
            return
        taken = self.form_list.takeItem(row)
        self.form_list.insertItem(target, taken)
        if widget is not None:
            self.form_list.setItemWidget(taken, widget)
        self._reorder_forms_data()
        self.save_forms()
        self._refresh_queue_positions()

    def _reorder_forms_data(self):
        ordered = {}
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            url = item.data(Qt.UserRole)
            if url:
                ordered[url] = self.forms_data.get(url)
        self.forms_data = {k: v for k, v in ordered.items() if v is not None}

    def _context_copy_url(self, url):
        from PyQt5.QtWidgets import QApplication
        QApplication.clipboard().setText(url)
        self.append_debug(f"<font color='gray'>[QUEUE] Copied URL to clipboard: {self._short_url(url)}</font>")

    def _context_open_in_browser(self, url):
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))
        self.append_debug(f"<font color='gray'>[QUEUE] Opening in browser: {self._short_url(url)}</font>")

    def _context_remove(self, item, url):
        meta = item.data(Qt.UserRole + 1) or {}
        title = meta.get("title") or item.text() or "this form"
        reply = QMessageBox.question(
            self,
            "Remove from Queue",
            f"Remove '{title}' from the queue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
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
        self._on_form_selection_changed(None)

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

            if action[0] == "grade":
                self._start_source_scan(parts, "grade_new", mode="all_forms")
            else:
                self._start_source_scan(parts, "add", mode="all_forms")

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
            if "dark_mode" not in config:
                config["dark_mode"] = False
                modified = True
            
            if modified:
                with open("config.json", "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4)
                    
            self.grading_mode = config.get("grading_mode", "Whole Form")
            set_dark_mode(bool(config.get("dark_mode", False)))
            self._apply_dark_mode_state()
        except Exception as e:
            print(f"Error loading config: {e}")
            self.grading_mode = "Whole Form"

    def toggle_dark_mode(self):
        set_dark_mode(not is_dark_mode())
        self._apply_dark_mode_state()
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    config = json.load(f)
            else:
                config = {}
            config["dark_mode"] = is_dark_mode()
            with open("config.json", "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            print(f"Error saving dark mode config: {e}")

    def _apply_dark_mode_state(self):
        apply_widget_theme(self)
        self.dark_mode_action.setText("Toggle Light Mode" if is_dark_mode() else "Toggle Dark Mode")
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(current_stylesheet())

    def _start_source_scan(self, sources, action, mode="all_forms", from_dt=None, to_dt=None):
        if hasattr(self, "source_scan_thread") and self.source_scan_thread and self.source_scan_thread.isRunning():
            QMessageBox.information(self, "Scan Running", "A source scan is already running.")
            return

        sources = list(sources or [])
        if not sources:
            QMessageBox.warning(self, "No Sources", "Add at least one folder or form URL.")
            return

        self.source_scan_action = action
        self.source_scan_before = set(self.forms_data.keys())
        self.source_scan_progress = QProgressDialog(
            "Scanning sources...", "", 0, 0, self
        )
        self.source_scan_progress.setWindowTitle("Scanning Sources")
        self.source_scan_progress.setWindowModality(Qt.WindowModal)
        self.source_scan_progress.setMinimumDuration(0)
        self.source_scan_progress.setCancelButton(None)
        self.source_scan_progress.setLabelText(
            f"Scanning {len(sources)} source(s). The app will stay responsive."
        )
        self.source_scan_progress.show()

        self.append_debug(f"[SCAN] Starting source scan for {len(sources)} source(s)")
        self.source_scan_thread = SourceScanThread(
            sources,
            mode=mode,
            from_dt=from_dt,
            to_dt=to_dt,
        )
        self.source_scan_thread.progress.connect(self._on_source_scan_progress)
        self.source_scan_thread.finished.connect(self._on_source_scan_finished)
        self.source_scan_thread.failed.connect(self._on_source_scan_failed)
        self.source_scan_thread.start()

    def _on_source_scan_progress(self, message):
        text = str(message)
        if hasattr(self, "source_scan_progress") and self.source_scan_progress:
            self.source_scan_progress.setLabelText(text)
        self.append_debug(f"[SCAN] {text}")

    def _on_source_scan_finished(self, forms):
        if hasattr(self, "source_scan_progress") and self.source_scan_progress:
            self.source_scan_progress.close()
        forms = list(forms or [])
        if not forms:
            QMessageBox.information(self, "No Forms Found", "No accessible forms were found in the selected source(s).")
            return

        new_added = 0
        for form_data in forms:
            form_url = form_data.get("url")
            form_title = form_data.get("title", "Untitled")
            if form_url and form_url not in self.forms_data:
                source = "Grade All" if self.source_scan_action == "grade_all" else "Scan Source"
                self._add_form_to_queue(form_url, form_title, source=source)
                new_added += 1

        self.save_forms()
        self.update_in_queue_label()
        self.grading_mode = "Whole Form"
        after = set(self.forms_data.keys())
        new_urls = list(after - getattr(self, "source_scan_before", set()))
        self.append_debug(
            f"[SCAN] Found {len(forms)} form(s), added {new_added} new form(s) to queue"
        )

        if self.source_scan_action == "grade_new":
            if not new_urls:
                QMessageBox.information(self, "No New Forms", "No new forms were found to grade.")
                return
            self.run_grader(target_urls=new_urls)
        elif self.source_scan_action == "grade_all":
            self.run_grader()

    def _on_source_scan_failed(self, error):
        if hasattr(self, "source_scan_progress") and self.source_scan_progress:
            self.source_scan_progress.close()
        QMessageBox.critical(self, "Scan Failed", str(error))
        self.append_debug(f"[SCAN] Failed: {error}")

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

            self._start_source_scan(
                folders,
                "grade_all",
                mode="with_submissions",
                from_dt=from_dt,
                to_dt=to_dt,
            )

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

    def _config_flag(self, key, default=False):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return bool(cfg.get(key, default))
        except Exception:
            return bool(default)

    def _config_flag_float(self, key, default=0.0):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return float(cfg.get(key, default) or 0.0)
        except Exception:
            return float(default)

    def _notify_budget_warning(self, cost_value, budget):
        self._notified_budget_warning = True
        self._notify(
            "OpenRouter Budget Reached",
            f"Current spend ${cost_value:.4f} has reached the budget of ${budget:.2f}.",
            QSystemTrayIcon.Warning,
        )

    def _stop_llamacpp_server_if_enabled(self, config_key, reason):
        if not self._config_flag(config_key, False):
            return
        stopped = self._stop_llamacpp_server_processes()
        if stopped > 0:
            try:
                self.append_debug(
                    f"<font color='gray'>[LLAMACPP] Stopped {stopped} llama-server process(es) {reason} to release RAM.</font>"
                )
            except Exception:
                pass
        else:
            try:
                self.append_debug(f"<font color='gray'>[LLAMACPP] No llama-server process found {reason}.</font>")
            except Exception:
                pass

    def _stop_llamacpp_server_processes(self):
        """Stop llama.cpp server processes to release local GGUF model memory."""
        try:
            if sys.platform == "win32":
                ps_script = (
                    "$procs = @(Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue); "
                    "$count = $procs.Count; "
                    "$procs | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }; "
                    "Write-Output $count"
                )
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                lines = str(result.stdout or "").strip().splitlines()
                count_text = lines[-1] if lines else "0"
                return max(0, int(count_text or "0"))
            result = subprocess.run(
                ["pkill", "-f", "llama-server"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            return 1 if result.returncode == 0 else 0
        except Exception:
            return 0

    def _llamacpp_selected_model_path(self, cfg):
        model_dir = os.path.expandvars(os.path.expanduser(str(
            cfg.get("llamacpp_model_dir", r"C:\Users\regis\.lmstudio\models")
        )))
        models_by_role = cfg.get("llamacpp_models", {}) if isinstance(cfg, dict) else {}
        selected = ""
        for role in ("semantic_judge", "factual_judge", "concept_judge", "strict_judge"):
            role_models = models_by_role.get(role, [])
            if isinstance(role_models, str):
                role_models = [role_models]
            for model in role_models or []:
                text = str(model or "").strip()
                if text and text != "No llama.cpp GGUF models found":
                    selected = text
                    break
            if selected:
                break
        if not selected:
            return ""
        expanded = os.path.expandvars(os.path.expanduser(selected))
        if os.path.isabs(expanded):
            return expanded
        return os.path.normpath(os.path.join(model_dir, expanded))

    def _llamacpp_server_executable(self, cfg):
        configured = os.path.expandvars(os.path.expanduser(str(cfg.get("llamacpp_server_executable", "") or "")))
        candidates = [
            configured,
            shutil.which("llama-server") or "",
            r"C:\Tools\llama.cpp\llama-server.exe",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return candidate
        return configured or "llama-server.exe"

    def _llamacpp_host_port(self, cfg):
        parsed = urlparse(str(cfg.get("llamacpp_api_base_url", "http://127.0.0.1:8081") or ""))
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8081
        return host, int(port)

    def _llamacpp_server_command(self, cfg, exe_path, model_path, host, port):
        gpu_layers = str(cfg.get("llamacpp_server_gpu_layers", "auto") or "auto").strip().lower()
        if gpu_layers not in {"auto", "all"}:
            try:
                gpu_layers = str(max(0, int(gpu_layers)))
            except ValueError:
                gpu_layers = "auto"
        flash_attn = str(cfg.get("llamacpp_server_flash_attn", "auto") or "auto").strip().lower()
        if flash_attn not in {"auto", "on", "off"}:
            flash_attn = "auto"
        command = [
            exe_path,
            "--model", model_path,
            "--host", host,
            "--port", str(port),
            "--ctx-size", str(max(512, int(cfg.get("llamacpp_server_context_size", 32768) or 32768))),
            "--gpu-layers", gpu_layers,
            "--threads", str(max(1, int(cfg.get("llamacpp_server_threads", 8) or 8))),
            "--threads-batch", str(max(1, int(cfg.get("llamacpp_server_threads_batch", 8) or 8))),
            "--batch-size", str(max(1, int(cfg.get("llamacpp_server_batch_size", 1024) or 1024))),
            "--ubatch-size", str(max(1, int(cfg.get("llamacpp_server_ubatch_size", 512) or 512))),
            "--flash-attn", flash_attn,
            "--cache-type-k", str(cfg.get("llamacpp_server_cache_type_k", "q8_0") or "q8_0"),
            "--cache-type-v", str(cfg.get("llamacpp_server_cache_type_v", "q8_0") or "q8_0"),
            "--parallel", str(max(1, int(cfg.get("llamacpp_server_parallel", 1) or 1))),
            "--mmap" if bool(cfg.get("llamacpp_server_mmap", True)) else "--no-mmap",
            "--jinja" if bool(cfg.get("llamacpp_server_jinja", True)) else "--no-jinja",
        ]
        return command

    def _start_llamacpp_server(self, cfg):
        try:
            from providers.llamacpp_provider import LlamaCppProvider

            provider = LlamaCppProvider()
            if provider.is_configured():
                return True
            exe_path = self._llamacpp_server_executable(cfg)
            model_path = self._llamacpp_selected_model_path(cfg)
            if not os.path.isfile(exe_path):
                self.append_debug(f"<font color='red'>[LLAMACPP] llama-server.exe not found: {exe_path}</font>")
                return False
            if not os.path.isfile(model_path):
                self.append_debug(f"<font color='red'>[LLAMACPP] Selected GGUF model not found: {model_path}</font>")
                return False
            host, port = self._llamacpp_host_port(cfg)
            os.makedirs("logs", exist_ok=True)
            log_path = os.path.abspath(os.path.join("logs", "llamacpp_server.log"))
            self.append_debug(
                f"<font color='cyan'>[LLAMACPP] Starting llama-server on {host}:{port} with {os.path.basename(model_path)}...</font>"
            )
            command = self._llamacpp_server_command(cfg, exe_path, model_path, host, port)
            stdout = open(log_path, "a", encoding="utf-8", errors="replace")
            creationflags = 0
            if sys.platform == "win32":
                creationflags = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            server_process = subprocess.Popen(
                command,
                cwd=os.path.dirname(exe_path) or None,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            timeout_s = max(10, int(cfg.get("llamacpp_startup_timeout_seconds", 300) or 300))
            progress = QProgressDialog(
                "Starting llama.cpp server...",
                "Cancel",
                0,
                timeout_s,
                self,
            )
            progress.setWindowTitle("Loading llama.cpp")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setValue(0)
            progress.show()
            QApplication.processEvents()
            started_at = time_module.monotonic()
            deadline = time_module.monotonic() + timeout_s
            while time_module.monotonic() < deadline:
                QApplication.processEvents()
                if provider.is_configured():
                    progress.setValue(timeout_s)
                    progress.close()
                    self.append_debug("<font color='green'>[LLAMACPP] llama.cpp server is ready.</font>")
                    return True
                elapsed = max(0, int(time_module.monotonic() - started_at))
                progress.setValue(min(elapsed, timeout_s))
                progress.setLabelText(
                    "Loading llama.cpp server...\n\n"
                    f"Model: {os.path.basename(model_path)}\n"
                    f"Server: {host}:{port}\n"
                    f"Elapsed: {elapsed}s / {timeout_s}s\n\n"
                    "Large GGUF models can take a few minutes to load."
                )
                if progress.wasCanceled():
                    try:
                        server_process.terminate()
                    except Exception:
                        pass
                    self._stop_llamacpp_server_processes()
                    self.append_debug("<font color='orange'>[LLAMACPP] llama.cpp server startup was cancelled.</font>")
                    return False
                time_module.sleep(1)
            progress.close()
            self.append_debug(
                f"<font color='red'>[LLAMACPP] llama-server did not become ready within {timeout_s}s. See {log_path}</font>"
            )
            return False
        except Exception as exc:
            self.append_debug(f"<font color='red'>[LLAMACPP] Could not start llama-server: {exc}</font>")
            return False

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

        try:
            with open("config.json", "r", encoding="utf-8") as f:
                preflight_cfg = json.load(f)
        except Exception:
            preflight_cfg = {}
        if is_llamacpp_only(preflight_cfg) and bool(preflight_cfg.get("llamacpp_require_server", True)):
            try:
                from providers.llamacpp_provider import LlamaCppProvider

                llamacpp_ready = LlamaCppProvider().is_configured()
            except Exception:
                llamacpp_ready = False
            if not llamacpp_ready and bool(preflight_cfg.get("llamacpp_auto_start_server", True)):
                llamacpp_ready = self._start_llamacpp_server(preflight_cfg)
            if not llamacpp_ready:
                message = (
                    "llama.cpp-only grading is selected, but no compatible llama.cpp server is responding.\n\n"
                    "The app tried to start llama-server.exe but it did not become ready. "
                    "Check Settings > llama.cpp > Server Executable and Model Folder, then run grading again."
                )
                self.run_state_label.setText("Waiting")
                self.append_debug(
                    "<font color='red'>[LLAMACPP] llama.cpp-only mode is selected, "
                    "but the local server is offline. Grading was not started.</font>"
                )
                QMessageBox.warning(self, "llama.cpp server offline", message)
                return

        self.is_grading = True
        self.run_state_label.setText("Running")
        self.run_button.setEnabled(False)
        self.stop_button.show()
        self.debug_output.clear()
        self.debug_lines = []
        self.finished_forms = []
        self.overall_forms_completed = 0
        if target_urls is not None:
            self.overall_forms_total = len(set(target_urls))
        else:
            self.overall_forms_total = sum(
                1
                for i in range(self.form_list.count())
                if (self.form_list.item(i).data(Qt.UserRole + 1) or {}).get("status", "queued") == "queued"
            )
        self.detail_progress.setValue(0)
        self.detail_progress_value.setText("0%")
        self._update_overall_progress_bar()
        self._reset_metric_labels()
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
            meta["completed"] = 0
            meta["total"] = 0
            meta["accepted"] = 0
            meta["rejected"] = 0
            meta["review_questions"] = 0
            meta["elapsed"] = 0
            meta["det_decisions"] = 0
            meta["ai_decisions"] = 0
            meta["avg_latency_ms"] = 0.0
            meta["ai_backlog"] = 0
            meta["current_model"] = "Idle"
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
        self.grader_thread.skipped_form.connect(self.update_skipped_form)
        self.grader_thread.start()

    def update_progress(self, cur, tot):
        if not tot:
            self.metric_responses.setText("0 / 0")
            self.pipeline_updated.setText("No learner answers")
            return
        self.metric_responses.setText(f"{cur} / {tot}")
        self.pipeline_updated.setText("Evaluating answers")

    def update_overall_progress(self, cur, tot):
        if not tot:
            return
        self.overall_forms_completed = cur
        self.overall_forms_total = tot
        self._update_overall_progress_bar()
        self.in_queue_label.setText(f"In Queue: {max(0, tot - cur)}")
        self.command_summary.setText(f"{tot} forms · {cur} completed")
        self._update_pipeline_rows_for_status("running" if self.is_grading else "queued")

    def update_form_metrics(
        self,
        completed,
        total,
        accepted,
        review_questions,
        elapsed_seconds,
        rejected=0,
        det_decisions=0,
        ai_decisions=0,
        avg_latency_ms=0.0,
    ):
        ai_backlog = 0
        current_model = "Idle"
        item = self._find_form_item_by_url(self.current_form_url)
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            ai_backlog = meta.get("ai_backlog", 0)
            current_model = meta.get("current_model", "Idle")
        self._update_metric_labels(
            completed,
            total,
            accepted,
            review_questions,
            elapsed_seconds,
            rejected,
            det_decisions,
            ai_decisions,
            avg_latency_ms,
            ai_backlog,
            current_model,
        )

        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            meta["completed"] = completed
            meta["total"] = total
            meta["accepted"] = accepted
            meta["rejected"] = rejected
            meta["review_questions"] = review_questions
            meta["elapsed"] = elapsed_seconds
            meta["det_decisions"] = det_decisions
            meta["ai_decisions"] = ai_decisions
            meta["avg_latency_ms"] = avg_latency_ms
            item.setData(Qt.UserRole + 1, meta)
            self._refresh_form_row(item)
            self._update_pipeline_rows_for_status(meta.get("status", "running"))

    def refresh_review_counts(self, form_id: str = None):
        """Recompute pending review counts for a form and update GUI metrics.

        This is intended to be called after the answer-key dashboard resolves reviews
        so the main window immediately reflects the changed review queue size.
        """
        try:
            current_url = getattr(self, "current_form_url", None)
            current_fid = self.extract_form_id(current_url) if current_url else None
            fid = form_id or current_fid
            if not fid:
                return
            pending = load_pending_review_records(fid) or {}
            # pending is a mapping item_id -> list[records]
            review_count = sum(len(v) for v in pending.values())
            # If the current form matches, update the metrics display
            if current_fid == fid:
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
        # The top progress bar tracks the whole run; answer progress lives in metrics.
        self._update_overall_progress_bar()
        self._reset_metric_labels()
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
        self.pipeline_updated.setText("Preparing form")

    def update_finished_form(self, form_id):
        self.finished_forms.append(form_id)
        now_str = datetime.now().strftime("%H:%M:%S")
        item = self._find_form_item_by_id(form_id)
        title = "Unknown Form"
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            if meta.get("status") in {"skipped", "partial"}:
                label = "Partial" if meta.get("status") == "partial" else "Skipped"
                self.append_debug(f"<font color='orange'>[AUTO {now_str}] {label}: {meta.get('title', title)}</font>")
                return
            title = meta.get("title", title)
            self._set_form_status(item, "done", "Finished and saved grading updates")
        self.append_debug(f"<font color='green'>[AUTO {now_str}] Completed: {title}</font>")
        self.finished_label.setText(f"Finished: {len(self.finished_forms)}")
        # After a form finishes, if the grader has become idle, start the next queued forms.
        QTimer.singleShot(800, self._maybe_start_next_after_finish)

    def update_skipped_form(self, form_id, url="", reason="Missing teacher answer key", missing_questions_json="[]"):
        item = self._find_form_item_by_id(form_id) if form_id else None
        if not item and url:
            item = self._find_form_item_by_url(url)
        if not item and self.current_form_url:
            current_id = self.extract_form_id(self.current_form_url)
            if current_id and form_id and current_id == form_id:
                item = self._find_form_item_by_url(self.current_form_url)
        if not item:
            self.append_debug(
                f"<font color='orange'>[GRADER] Skipped form could not be matched in queue: "
                f"{form_id or url or 'unknown'}</font>"
            )
            return
        detail = str(reason or "Skipped")
        try:
            skipped_questions = json.loads(str(missing_questions_json or "[]"))
        except Exception:
            skipped_questions = []
        if not isinstance(skipped_questions, list):
            skipped_questions = []
        meta = item.data(Qt.UserRole + 1) or {}
        meta["skipped_questions"] = skipped_questions
        item.setData(Qt.UserRole + 1, meta)
        self._set_form_status(item, "partial", detail)

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
        w.document().setMaximumBlockCount(self.max_gui_visible_blocks)
        return w

    def _route_worker_log(self, message):
        if "[Worker: Producer]" in message:
            self.producer_output.append(message)
        if "[Worker: Deterministic]" in message:
            self.det_output.append(message)
        if "[Worker: AI]" in message:
            self.ai_output.append(message)
        if "[APP WORKER]" in message:
            self.ai_output.append(message)
        if "[PROVIDER " in message or "[PROVIDER]" in message:
            self.provider_output.append(message)
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
            if "[HEARTBEAT]" in message:
                self._update_current_model_from_heartbeat(message)
                return
            if "[APP WORKER]" in message:
                payload = message.split("[APP WORKER]", 1)[1].strip()
                self._update_app_worker(payload)
                return
            if "[PROVIDER METRICS]" in message:
                payload = message.split("[PROVIDER METRICS]", 1)[1].strip()
                self._update_provider_metrics(payload)
                return
            if "[PROVIDER WORKER]" in message:
                payload = message.split("[PROVIDER WORKER]", 1)[1].strip()
                self._update_provider_worker(payload)
                return
        except Exception:
            pass

    def _update_current_model_from_heartbeat(self, message):
        model = None
        match = re.search(r"\bactive_model=([^\s]+)", str(message))
        if match:
            model = match.group(1).strip()
        if not model:
            return
        if model == "none":
            model = "Idle"
        item = self._find_form_item_by_url(self.current_form_url)
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            meta["current_model"] = model
            item.setData(Qt.UserRole + 1, meta)
        if hasattr(self, "metric_current_model"):
            current_completed = 0
            current_total = 0
            if item:
                meta = item.data(Qt.UserRole + 1) or {}
                current_completed = int(meta.get("completed", 0) or 0)
                current_total = int(meta.get("total", 0) or 0)
                self._update_metric_labels(
                    current_completed,
                    current_total,
                    int(meta.get("accepted", 0) or 0),
                    int(meta.get("review_questions", 0) or 0),
                    meta.get("elapsed", 0),
                    int(meta.get("rejected", 0) or 0),
                    int(meta.get("det_decisions", 0) or 0),
                    int(meta.get("ai_decisions", 0) or 0),
                    float(meta.get("avg_latency_ms", 0.0) or 0.0),
                    int(meta.get("ai_backlog", 0) or 0),
                    model,
                )

    def _reset_worker_tab_titles(self):
        self.log_tabs.setTabText(0, "All")
        self.log_tabs.setTabText(1, "Producer (q: -)")
        self.log_tabs.setTabText(2, "Det Workers (q: -)")
        self.log_tabs.setTabText(3, "AI Workers (q: -)")
        self.log_tabs.setTabText(4, "Providers (OR: - | LC: - | OL: -)")
        self.log_tabs.setTabText(5, "Aggregator (q: -)")

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
        self.log_tabs.setTabText(5, f"Aggregator (q: {r})")
        item = self._find_form_item_by_url(self.current_form_url)
        if item and q_ai_display is not None:
            meta = item.data(Qt.UserRole + 1) or {}
            meta["ai_backlog"] = q_ai_display
            item.setData(Qt.UserRole + 1, meta)
            self._refresh_form_row(item)
            self._update_metric_labels(
                int(meta.get("completed", 0) or 0),
                int(meta.get("total", 0) or 0),
                int(meta.get("accepted", 0) or 0),
                int(meta.get("review_questions", 0) or 0),
                meta.get("elapsed", 0),
                int(meta.get("rejected", 0) or 0),
                int(meta.get("det_decisions", 0) or 0),
                int(meta.get("ai_decisions", 0) or 0),
                float(meta.get("avg_latency_ms", 0.0) or 0.0),
                q_ai_display,
                meta.get("current_model", "Idle"),
            )
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
        self._set_activity_row(
            "forms",
            f"{self.overall_forms_completed} / {self.overall_forms_total} completed",
            "Running" if self.is_grading else "Idle",
        )
        if done is not None and total is not None:
            answer_state = "Done" if total > 0 and done >= total else "Running" if self.is_grading else "Waiting"
            self._set_activity_row("answers", f"{done} / {total} evaluated", answer_state)
        ai_waiting = int(q_ai or 0)
        self._set_activity_row("ai", f"{ai_waiting} waiting", "Draining" if ai_waiting else "Idle")
        apply_waiting = int(q_result or 0)
        self._set_activity_row("apply", f"{apply_waiting} result updates pending", "Pending" if apply_waiting else "Waiting")

    def _extract_metric_value(self, payload, key):
        token = f"{key}="
        if token not in payload:
            return None
        try:
            return payload.split(token, 1)[1].split()[0].strip()
        except Exception:
            return None

    def _update_provider_metrics(self, payload):
        provider_defs = [
            ("openrouter", "OR", "OpenRouter"),
            ("llamacpp", "LC", "llama.cpp"),
            ("ollama", "OL", "Ollama"),
        ]
        active_provider_names = [
            name for name, _short, _label in provider_defs
            if f"q_{name}=" in payload or f"{name}_health=" in payload
        ]
        q_openrouter = self._extract_metric_value(payload, "q_openrouter") or "-"
        q_llamacpp = self._extract_metric_value(payload, "q_llamacpp") or "-"
        q_ollama = self._extract_metric_value(payload, "q_ollama") or "-"
        or_health = self._extract_metric_value(payload, "openrouter_health") or "-"
        lc_health = self._extract_metric_value(payload, "llamacpp_health") or "-"
        ol_health = self._extract_metric_value(payload, "ollama_health") or "-"
        or_done = self._extract_metric_value(payload, "openrouter_done") or "0"
        lc_done = self._extract_metric_value(payload, "llamacpp_done") or "0"
        ol_done = self._extract_metric_value(payload, "ollama_done") or "0"
        or_failed = self._extract_metric_value(payload, "openrouter_failed") or "0"
        lc_failed = self._extract_metric_value(payload, "llamacpp_failed") or "0"
        ol_failed = self._extract_metric_value(payload, "ollama_failed") or "0"
        retries = self._extract_metric_value(payload, "retries") or "0"
        failovers = self._extract_metric_value(payload, "failovers") or "0"
        rpm = self._extract_metric_value(payload, "rpm") or "0"
        avg_ms = self._extract_metric_value(payload, "avg_ms") or "0"
        or_model = (self._extract_metric_value(payload, "openrouter_last_model") or "-").replace("_", " ")
        lc_model = (self._extract_metric_value(payload, "llamacpp_last_model") or "-").replace("_", " ")
        ol_model = (self._extract_metric_value(payload, "ollama_last_model") or "-").replace("_", " ")
        self._update_model_health_dashboard(payload, or_model, ol_model, avg_ms)

        queue_by_provider = {"openrouter": q_openrouter, "llamacpp": q_llamacpp, "ollama": q_ollama}
        health_by_provider = {"openrouter": or_health, "llamacpp": lc_health, "ollama": ol_health}
        done_by_provider = {"openrouter": or_done, "llamacpp": lc_done, "ollama": ol_done}
        failed_by_provider = {"openrouter": or_failed, "llamacpp": lc_failed, "ollama": ol_failed}
        if active_provider_names:
            tab_bits = [
                f"{short}: {queue_by_provider[name]}"
                for name, short, _label in provider_defs
                if name in active_provider_names
            ]
            summary_bits = [
                f"{short} {health_by_provider[name]} q:{queue_by_provider[name]} "
                f"ok/fail:{done_by_provider[name]}/{failed_by_provider[name]}"
                for name, short, _label in provider_defs
                if name in active_provider_names
            ]
        else:
            tab_bits = ["-"]
            summary_bits = ["No active provider metrics yet"]
        self.log_tabs.setTabText(4, f"Providers ({' | '.join(tab_bits)})")
        self._provider_summary_text = (
            f"{' | '.join(summary_bits)} | {rpm}/min avg {avg_ms}ms retry {retries} failover {failovers}"
        )
        self._refresh_worker_summaries()
        model_by_provider = {"openrouter": or_model, "llamacpp": lc_model, "ollama": ol_model}
        visible_models = [
            model_by_provider[name]
            for name, _short, _label in provider_defs
            if name in active_provider_names and model_by_provider[name] != "-"
        ]
        if visible_models:
            active_model = visible_models[0]
            if active_model and active_model != "-":
                self.metric_current_model.setText(active_model[:28] + ("..." if len(active_model) > 28 else ""))
                self.metric_current_model.setToolTip(
                    "\n".join(
                        f"{label}: {model_by_provider[name]}"
                        for name, _short, label in provider_defs
                        if name in active_provider_names
                    )
                )

    def _format_seconds_compact(self, raw_value):
        try:
            seconds = max(0, int(float(raw_value or 0)))
        except Exception:
            return "-"
        if seconds >= 3600:
            return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
        if seconds >= 60:
            return f"{seconds // 60}m {seconds % 60}s"
        return f"{seconds}s"

    def _update_model_health_dashboard(self, payload, or_model="-", ol_model="-", avg_ms="0"):
        total = self._extract_metric_value(payload, "or_models_total") or "0"
        available = self._extract_metric_value(payload, "or_models_available") or "0"
        rate_limited = self._extract_metric_value(payload, "or_models_rate_limited") or "0"
        failed = self._extract_metric_value(payload, "or_models_failed") or "0"
        json_failures = self._extract_metric_value(payload, "or_json_failures") or "0"
        last_json_failures = self._extract_metric_value(payload, "or_last_json_failures") or "0"
        success_rate = self._extract_metric_value(payload, "or_last_success_rate") or "0"
        avg_suspicion = self._extract_metric_value(payload, "or_avg_suspicion") or "0"
        last_suspicion = self._extract_metric_value(payload, "or_last_suspicion") or "0"
        max_cooldown = self._extract_metric_value(payload, "or_max_cooldown_s") or "0"
        last_cooldown = self._extract_metric_value(payload, "or_last_cooldown_s") or "0"
        cost = self._extract_metric_value(payload, "or_cost_usd") or "0"
        reason = (self._extract_metric_value(payload, "or_selection_reason") or "-").replace("_", " ")
        openrouter_error = (self._extract_metric_value(payload, "openrouter_last_error") or "-").replace("_", " ")
        try:
            success_percent = float(success_rate) * 100.0
        except Exception:
            success_percent = 0.0
        try:
            cost_value = float(cost or 0.0)
        except Exception:
            cost_value = 0.0

        remaining_cost = "-"
        item = self._find_form_item_by_url(self.current_form_url)
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            completed = int(meta.get("completed", 0) or 0)
            total_answers = int(meta.get("total", 0) or 0)
            if completed > 0 and total_answers > completed and cost_value > 0:
                remaining = (cost_value / completed) * (total_answers - completed)
                remaining_cost = f"${remaining:.4f}"

        self._set_model_health_row(
            "current",
            f"OpenRouter: {or_model} | Ollama: {ol_model}",
            f"OpenRouter current/last model: {or_model}\nOllama current/last model: {ol_model}",
        )
        self._set_model_health_row(
            "success",
            f"{success_percent:.1f}% success on current OpenRouter model | avg {avg_ms}ms",
        )
        self._set_model_health_row(
            "limits",
            f"{available}/{total} available | {rate_limited} rate-limited | {failed} failed",
            f"Last OpenRouter error: {openrouter_error}",
        )
        self._set_model_health_row(
            "json",
            f"{json_failures} JSON failures total | {last_json_failures} on current model",
        )
        self._set_model_health_row(
            "quality",
            f"Ollama suspicion avg {avg_suspicion} | current {last_suspicion}",
            "0.00 is trusted, 1.00 is highly suspicious according to the local Ollama monitor.",
        )
        self._set_model_health_row(
            "cooldown",
            f"current {self._format_seconds_compact(last_cooldown)} | max {self._format_seconds_compact(max_cooldown)}",
        )
        self._set_model_health_row(
            "cost",
            f"${cost_value:.4f} so far | est remaining {remaining_cost}",
        )
        budget = float(self._config_flag_float("max_openrouter_spend_usd_per_run", 0.0))
        if budget > 0 and cost_value >= budget:
            self._set_model_health_row(
                "cost",
                f"${cost_value:.4f} so far | est remaining {remaining_cost}  ⚠ OVER BUDGET (${budget:.2f})",
                f"Spending has reached the configured OpenRouter budget of ${budget:.2f}.",
            )
            if not getattr(self, "_notified_budget_warning", False):
                self._notify_budget_warning(cost_value, budget)
        self._set_model_health_row("reason", reason)

    def _update_app_worker(self, payload):
        worker_id = self._extract_metric_value(payload, "id") or "ai"
        status = self._extract_metric_value(payload, "status") or "idle"
        current = self._extract_metric_value(payload, "current") or "-"
        answers = self._extract_metric_value(payload, "answers") or "0"
        latency_ms = self._extract_metric_value(payload, "latency_ms") or "0"
        queue_wait_ms = self._extract_metric_value(payload, "queue_wait_ms") or "0"
        primary = f"{answers} answer{'s' if str(answers) != '1' else ''}"
        secondary = "Waiting" if current == "-" else f"Current: {current}"
        stats = f"latency {latency_ms}ms | wait {queue_wait_ms}ms"
        self._set_worker_card("app", worker_id, "AI worker", status, primary, secondary, stats)

    def _update_provider_worker(self, payload):
        worker_id = self._extract_metric_value(payload, "id") or "-"
        provider = self._extract_metric_value(payload, "provider") or "-"
        status = self._extract_metric_value(payload, "status") or "-"
        model = (self._extract_metric_value(payload, "model") or "-").replace("_", " ")
        request_id = self._extract_metric_value(payload, "request") or "-"
        latency_ms = self._extract_metric_value(payload, "latency_ms") or "0"
        queue_wait_ms = self._extract_metric_value(payload, "queue_wait_ms") or "0"
        if status == "running":
            detail = f"{provider}: {model} request {request_id}"
        else:
            detail = f"{provider}: {status} last {latency_ms}ms wait {queue_wait_ms}ms"
        title_prefix = (
            "OpenRouter" if provider == "openrouter"
            else "llama.cpp" if provider == "llamacpp"
            else "Ollama" if provider == "ollama"
            else provider.title()
        )
        primary = model if status == "running" else f"Last {latency_ms}ms"
        secondary = f"request {request_id}" if request_id != "-" else detail
        stats = f"latency {latency_ms}ms | wait {queue_wait_ms}ms"
        self._set_worker_card("provider", worker_id, title_prefix, status, primary, secondary, stats)

    def append_debug(self, message):
        self.debug_lines.append(message)
        if len(self.debug_lines) > self.max_gui_log_lines:
            del self.debug_lines[: len(self.debug_lines) - self.max_gui_log_lines]
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
        self.provider_output.clear()
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

        self._stop_llamacpp_server_if_enabled("llamacpp_stop_server_after_grading", "after grading")

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
                self._notify("Grading Completed", "All queued forms have been graded.")
                self._notify_pending_reviews()

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
        self._stop_llamacpp_server_if_enabled("llamacpp_stop_server_on_app_close", "on app close")
        event.accept()


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    ensure_runtime_environment()
    if os.environ.get("AUTOGRADER_GRADER") == "1" or "--grader" in sys.argv:
        sys.argv = [arg for arg in sys.argv if arg != "--grader"]
        from main import main as run_grader_main
        sys.exit(run_grader_main() or 0)
    app = QApplication(sys.argv)
    app.setApplicationName("Google Form Autograder")
    app.setOrganizationName("Regis")
    app.setWindowIcon(app_icon())
    apply_application_theme(app)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(244, 246, 248))
    palette.setColor(QPalette.WindowText, Qt.black)
    app.setPalette(palette)
    window = FormManager()
    app.aboutToQuit.connect(window._shutdown_owned_work)
    window.show()
    sys.exit(app.exec_())




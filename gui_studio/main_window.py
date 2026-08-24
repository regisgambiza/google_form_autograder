# gui_studio/main_window.py - Studio shell main window (pure frontend rebuild).
#
# The visual tree is brand new (nav rail + stacked pages + rail FAB). The
# behavioral contract with the untouched backend is preserved exactly:
#   * GraderThread's 8 signals are connected identically.
#   * Child dialogs keep their parent hooks: forms_data, _add_form_to_queue,
#     save_forms, _refresh_queue_positions, refresh_review_counts,
#     update_config, start_auto_mode, run_grader, schedule_next_cycle,
#     stop_grading, grading_mode, _sync_worker_cards_to_config,
#     clear_all_forms, _start_source_scan, auto-run settings attributes.
#   * config.json / forms_to_grade.json / auto_partial_forms.json handling
#     and the scheduler singleton lifecycle are unchanged.
import atexit
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time as time_module
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlparse

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, QThread, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from answer_key_dashboard import AnswerKeyDashboard
from auto_add_dialog import AutoAddDialog, SearchThread
from decision_audit_viewer import DecisionAuditViewer, load_audit_records
from evaluator_config import (
    configured_provider_names,
    effective_ai_worker_count,
    effective_provider_worker_counts,
    is_llamacpp_only,
)
from form_searcher import (
    find_all_forms_in_sources,
    load_predefined_folders,
)
from grader_thread import GraderThread
from scheduler import scheduler as auto_scheduler
from scan_source_dialog import run_scan_source_dialog
from settings_dialog import show_settings_dialog

from gui_studio import telemetry
from gui_studio import theme as T
from gui_studio.drive_folders import (
    DriveFolderScanThread,
    load_selected_folders,
    save_selected_folders,
)
from gui_studio.pages import (
    QUEUE_COLUMNS,
    ActivityPage,
    DashboardPage,
    DriveFoldersPage,
    ProvidersPage,
    QueueTablePage,
)
from gui_studio.widgets import QueueRow, repolish, status_label

# Colorful classic-utility toolbar icons (same painter the dialogs already use).
from app_theme import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_PURPLE,
    ACCENT_RED,
    ACCENT_SLATE,
    pictograph_icon,
)

BANGKOK_TZ = timezone(timedelta(hours=7))

AI_WORKER_DISPLAY_NAMES = [
    "Optimus Prime", "Bumblebee", "Ratchet", "Ironhide", "Arcee", "Jazz",
    "Wheeljack", "Mirage", "Prowl", "Sideswipe", "Hot Rod", "Ultra Magnus",
]

STATUS_TEXT_COLORS = {
    "queued": QColor("#3a3a90"),
    "running": QColor("#b54708"),
    "done": QColor("#067647"),
    "failed": QColor("#b42318"),
    "partial": QColor("#b42318"),
    "skipped": QColor("#585858"),
}

CATEGORY_KEYS = ["all", "queued", "running", "done", "partial", "failed", "skipped"]
PANEL_KEYS = ["dashboard", "providers", "activity", "drive"]


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    return os.path.join(base, *parts)


def app_icon():
    from PySide6.QtGui import QIcon

    return QIcon(resource_path("assets", "app_icon.ico"))


class SourceScanThread(QThread):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

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
                from form_searcher import find_forms_with_submissions_in_range

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


class _TakenRow:
    """Row record carried between takeItem() and insertItem() on reorder."""

    def __init__(self, url, meta):
        self.url = url
        self.meta = meta


class _FormTableAdapter:
    """QListWidget-flavored API over the dense form table so the queue
    orchestration code can stay row/item based (url in Qt.UserRole, meta in
    Qt.UserRole+1 on the column-0 item)."""

    def __init__(self, table, window):
        self._table = table
        self._win = window

    def count(self):
        return self._table.rowCount()

    def item(self, row):
        return self._table.item(row, 0)

    def clear(self):
        self._table.clearContents()
        self._table.setRowCount(0)
        self._win._row_bars.clear()

    def currentItem(self):
        current = self._table.currentItem()
        if current is not None:
            return self._table.item(current.row(), 0)
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if rows:
            return self._table.item(rows[0].row(), 0)
        return None

    def setCurrentItem(self, item):
        if item is not None:
            self._table.selectRow(item.row())

    def scrollToItem(self, item):
        if item is not None:
            self._table.scrollTo(self._table.model().index(item.row(), 0))

    def selectedItems(self):
        model = self._table.selectionModel()
        rows = sorted({index.row() for index in model.selectedRows()}) if model else []
        items = []
        for row in rows:
            item = self._table.item(row, 0)
            if item is not None:
                items.append(item)
        return items

    def itemAt(self, pos):
        cell = self._table.itemAt(pos)
        if cell is None:
            return None
        return self._table.item(cell.row(), 0)

    def row(self, item):
        return item.row() if item is not None else -1

    def viewport(self):
        return self._table.viewport()

    def takeItem(self, row):
        item = self._table.item(row, 0)
        record = None
        if item is not None:
            record = _TakenRow(item.data(Qt.UserRole), item.data(Qt.UserRole + 1) or {})
        if record is not None:
            self._win._row_bars.pop(record.url, None)
        self._table.removeRow(row)
        return record

    def insertItem(self, row, record):
        self._table.insertRow(row)
        self._win._create_table_row(row, record.url, record.meta)
        self._win._refresh_form_row(self._table.item(row, 0))


class AutograderWindow(QMainWindow):
    """Classic utility window: menu bar + icon toolbar + category tree +
    dense form table + status bar. Panels live in the stacked content area."""

    def __init__(self):
        super().__init__()
        self.setObjectName("AutograderWindow")
        self.setWindowTitle("Google Form Autograder")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1100, 700)
        self.resize(1360, 860)

        # ---- orchestration state (contract with dialogs + backend) -------
        self.grader_thread = None
        self.auto_search_thread = None
        self.source_scan_thread = None
        self.drive_scan_thread = None
        self.forms_data = {}
        self.service = None
        self.drive_service = None
        self.classroom_service = None
        self.finished_forms = []
        self.current_form_url = None
        self.overall_forms_completed = 0
        self.overall_forms_total = 0
        self.grading_mode = "Whole Form"
        self.auto_mode = False
        self.auto_timer = None
        self._metrics_cache = None
        self._metrics_last_elapsed = 0.0
        self._metrics_last_ts = None
        self._metrics_backlog = 0
        self._metrics_model = "Idle"
        self._provider_summary_text = ""
        self._notified_budget_warning = False
        self._shutdown_complete = False
        self._force_exit = False
        self.is_closing = False
        self.is_grading = False
        self.is_searching = False
        self._model_progress_seen = False
        self.tray_icon = None
        self.max_gui_log_lines = 2500
        self.debug_lines = []
        self.pipeline_stage_counts = {}
        self._row_bars = {}  # url -> QProgressBar cell widget in the form table
        self._category_status = "all"  # active tree category filter

        # Auto-run settings (set by the schedule dialog)
        self.recency_minutes = 60
        self.interval_seconds = 300
        self.folders = []
        self.last_check_time = None
        self.use_time_schedule = False
        self.schedule_time_val = None
        self.selected_days = [True] * 7
        self.auto_notify_on_new = True
        self.auto_spend_budget_usd = 0.0

        # Partial-form watcher (persists across restarts)
        self.auto_partial_forms = {}
        self.auto_partial_forms_path = "auto_partial_forms.json"
        self._partial_regrade_pending = set()

        # Worker observability
        self.app_worker_cards = {}
        self.provider_worker_cards = {}
        self.provider_worker_states = {}

        # Retired QThreads kept alive until Qt confirms they finished, so
        # replacing the active search thread can never destroy a running one.
        self._retired_search_threads = []

        self._elapsed_ticker = QTimer(self)
        self._elapsed_ticker.setInterval(1000)
        self._elapsed_ticker.timeout.connect(self._tick_elapsed)

        self.tailer = telemetry.JsonlTailer(self)
        self.tailer.answer_result.connect(self._on_answer_event)
        self.tailer.run_start.connect(self._on_feed_run_start)
        self.tailer.run_complete.connect(self._on_feed_run_complete)
        self.tailer.form_skipped.connect(self._on_feed_form_skipped)

        self._build_ui()
        self._setup_system_tray()
        self._setup_keyboard_shortcuts()

        self.load_forms()
        self.load_config()
        self.folders = load_selected_folders()
        self.drive_page.set_selected(self.folders)
        self._sync_worker_cards_to_config()
        self._refresh_queue_positions()
        self.update_in_queue_label()
        self.refresh_auth_status()
        self.dashboard.set_mode_chip(self.grading_mode)
        self._set_run_state("Ready")
        self._set_auto_status("Auto Run: Off", "off")
        self._set_activity("Idle", "idle")

        try:
            cfg_path = "config.json"
            cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as fh:
                    cfg = json.load(fh)
            self.tailer.start(str(cfg.get("gui_terminal_jsonl_path", "logs/gui_terminal.jsonl")))
        except Exception:
            pass

        QTimer.singleShot(500, self.prompt_login_if_needed)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        self._build_menu_bar()
        self._build_toolbar()

        self.stack = QStackedWidget()
        self.queue_page = QueueTablePage()
        self.dashboard = DashboardPage()
        self.providers_page = ProvidersPage()
        self.activity = ActivityPage()
        self.drive_page = DriveFoldersPage()
        self.stack.addWidget(self.queue_page)   # index 0 — default view
        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.providers_page)
        self.stack.addWidget(self.activity)
        self.stack.addWidget(self.drive_page)

        self.queue_table = self.queue_page.table
        self.form_list = _FormTableAdapter(self.queue_table, self)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_category_tree())
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 1100])
        self.category_tree.setMinimumWidth(160)
        self.setCentralWidget(splitter)

        self._build_status_bar()

        # wire pages
        self.dashboard.run_clicked.connect(self.run_grader)
        self.dashboard.stop_clicked.connect(self.stop_grading)
        self.dashboard.add_sources_clicked.connect(self.open_manual_add_dialog)
        self.dashboard.scan_clicked.connect(self.open_quick_grade_dialog)
        self.dashboard.schedule_clicked.connect(self.open_auto_run_dialog)
        self.dashboard.review_clicked.connect(self.open_current_form_review)
        self.dashboard.open_activity_clicked.connect(lambda: self._goto_page("activity"))
        self.queue_page.add_sources_clicked.connect(self.open_manual_add_dialog)
        self.queue_page.scan_clicked.connect(self.open_quick_grade_dialog)
        self.queue_page.clear_all_clicked.connect(lambda: self.clear_all_forms(confirm=True))
        self.queue_page.clear_done_clicked.connect(self.clear_finished_forms_silently)
        self.queue_page.search_input.textChanged.connect(self._filter_form_queue)
        self.queue_page.filter_combo.currentTextChanged.connect(self._filter_form_queue)
        self.queue_table.customContextMenuRequested.connect(self._on_form_table_context_menu)
        self.drive_page.scan_requested.connect(self.start_drive_folder_scan)
        self.drive_page.apply_clicked.connect(self.apply_drive_folder_selection)

    def _build_menu_bar(self):
        menu_bar = self.menuBar()

        tasks_menu = menu_bar.addMenu("Tasks")
        tasks_menu.addAction("Add Sources", self.open_manual_add_dialog)
        tasks_menu.addAction("Scan Source", self.open_quick_grade_dialog)
        tasks_menu.addAction("Grade All Forms", self.grade_all_forms_in_all_folders)
        tasks_menu.addSeparator()
        self.start_action = tasks_menu.addAction("Start Grading", self.run_grader)
        self.stop_action = tasks_menu.addAction("Stop Grading", self.stop_grading)
        self.stop_action.setEnabled(False)
        tasks_menu.addSeparator()
        tasks_menu.addAction("Schedule Automatic Runs", self.open_auto_run_dialog)

        file_menu = menu_bar.addMenu("File")
        self.login_action = file_menu.addAction("Login to Google", self.login_google)
        self.logout_action = file_menu.addAction("Logout Google Account", self.logout_google)
        file_menu.addSeparator()
        file_menu.addAction("Export Results (CSV)", self.export_results_csv_dialog)
        file_menu.addAction("Generate Run Report", self.generate_run_report)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.exit_app)

        grading_menu = menu_bar.addMenu("Grading")
        grading_menu.addAction("Answer Keys", self.open_answer_key_dashboard)
        grading_menu.addAction("Decision Audit", self.open_decision_audit_viewer)
        grading_menu.addSeparator()
        grading_menu.addAction("Requeue Selected Form", self._requeue_selected_form)
        grading_menu.addAction("Remove Selected Form", self.remove_form)
        grading_menu.addSeparator()
        grading_menu.addAction("Clear Completed Forms", self.clear_finished_forms_silently)
        grading_menu.addAction("Clear All Forms", lambda: self.clear_all_forms(confirm=True))

        view_menu = menu_bar.addMenu("View")
        self._view_actions = {}
        for key, label in (
            ("queue", "All Forms"),
            ("dashboard", "Dashboard"),
            ("providers", "Providers && Health"),
            ("activity", "Activity && Console"),
            ("drive", "Drive Folders"),
        ):
            self._view_actions[key] = view_menu.addAction(label, lambda k=key: self._goto_page(k))

        help_menu = menu_bar.addMenu("Help")
        help_menu.addAction("About", self._show_about_dialog)

    def _build_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        toolbar.setIconSize(QSize(34, 34))
        self.addToolBar(toolbar)

        def tool(text, glyph, accent, slot, tooltip=None):
            button = QToolButton()
            button.setObjectName("ToolButton")
            button.setText(text)
            button.setIcon(pictograph_icon(glyph, size=44, accent=accent))
            button.setIconSize(QSize(32, 32))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setToolTip(tooltip or text)
            button.setMinimumWidth(70)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
            return button

        tool("Add URL", "plus", ACCENT_GREEN, self.open_manual_add_dialog,
             "Add form sources (Ctrl+A)")
        tool("Scan", "search", ACCENT_ORANGE, self.open_quick_grade_dialog)
        toolbar.addSeparator()
        self.run_tool = tool("Start", "play", ACCENT_GREEN, self.run_grader,
                             "Start grading (Ctrl+R)")
        self.stop_tool = tool("Stop", "stop", ACCENT_RED, self.stop_grading,
                              "Stop grading (Ctrl+Shift+S)")
        self.stop_tool.setEnabled(False)
        tool("Grade All", "list", ACCENT_BLUE, self.grade_all_forms_in_all_folders,
             "Find and grade all forms from predefined sources")
        toolbar.addSeparator()
        tool("Answer Keys", "key", ACCENT_PURPLE, self.open_answer_key_dashboard,
             "Open the answer-key dashboard (Ctrl+Shift+A)")
        tool("Audit", "doc", ACCENT_BLUE, self.open_decision_audit_viewer)
        tool("Export", "tray", ACCENT_PURPLE, self.export_results_csv_dialog,
             "Export grading results to CSV (Ctrl+E)")
        tool("Report", "chart", ACCENT_ORANGE, self.generate_run_report)
        toolbar.addSeparator()
        tool("Auto Run", "calendar", ACCENT_PURPLE, self.open_auto_run_dialog,
             "Schedule automatic grading runs")
        tool("Settings", "gear", ACCENT_SLATE, self.open_settings_dialog)

    def _build_category_tree(self):
        self.category_tree = QTreeWidget()
        self.category_tree.setObjectName("CategoryTree")
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setRootIsDecorated(True)
        self.category_tree.itemSelectionChanged.connect(self._on_tree_selection)

        self._tree_items = {}

        def category_item(parent, key, label):
            item = QTreeWidgetItem(parent, [label])
            item.setData(0, Qt.UserRole, key)
            self._tree_items[key] = item
            return item

        all_root = category_item(self.category_tree, "all", "All Forms")
        all_root.setExpanded(True)
        for key, label in (
            ("queued", "Queued"),
            ("running", "Running"),
            ("done", "Finished"),
            ("partial", "Partial"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ):
            category_item(all_root, key, label)

        panels_root = QTreeWidgetItem(self.category_tree, ["Panels"])
        panels_root.setFlags(Qt.ItemIsEnabled)
        panels_root.setExpanded(True)
        for key, label in (
            ("dashboard", "Dashboard"),
            ("providers", "Providers && Health"),
            ("activity", "Activity && Console"),
            ("drive", "Drive Folders"),
        ):
            item = QTreeWidgetItem(panels_root, [label])
            item.setData(0, Qt.UserRole, key)
            self._tree_items[key] = item

        self._tree_items["all"].setSelected(True)
        return self.category_tree

    def _build_status_bar(self):
        bar = self.statusBar()
        bar.setSizeGripEnabled(True)

        self.status_run_dot = QLabel()
        self.status_run_dot.setObjectName("StatusDot")
        self.status_run_dot.setProperty("state", "ready")
        self.status_run_dot.setFixedSize(8, 8)
        self.status_run_label = QLabel("Ready")
        self.status_run_label.setObjectName("StatusPart")
        bar.addWidget(self.status_run_dot)
        bar.addWidget(self.status_run_label)

        self.status_activity_label = QLabel("Idle")
        self.status_activity_label.setObjectName("StatusPart")
        self.status_activity_label.setProperty("muted", "true")
        bar.addWidget(self.status_activity_label)

        self.status_auto_label = QLabel("Auto: Off")
        self.status_auto_label.setObjectName("StatusPart")
        self.status_auto_label.setProperty("muted", "true")
        bar.addWidget(self.status_auto_label)

        bar.addWidget(QLabel(""), 1)  # stretch

        self.status_model_label = QLabel("Model: idle")
        self.status_model_label.setObjectName("StatusPart")
        self.status_model_label.setProperty("muted", "true")
        bar.addWidget(self.status_model_label)

        self.status_queue_label = QLabel("0 forms")
        self.status_queue_label.setObjectName("StatusPart")
        self.status_queue_label.setProperty("muted", "true")
        bar.addPermanentWidget(self.status_queue_label)

    def _on_tree_selection(self):
        items = self.category_tree.selectedItems()
        if not items:
            return
        key = str(items[0].data(0, Qt.UserRole) or "")
        if not key:
            return
        if key in PANEL_KEYS:
            self.stack.setCurrentWidget({
                "dashboard": self.dashboard,
                "providers": self.providers_page,
                "activity": self.activity,
                "drive": self.drive_page,
            }[key])
        else:
            self._category_status = key if key in CATEGORY_KEYS else "all"
            self.stack.setCurrentWidget(self.queue_page)
            self._filter_form_queue()

    def _goto_page(self, key):
        if key in PANEL_KEYS:
            self.stack.setCurrentWidget({
                "dashboard": self.dashboard,
                "providers": self.providers_page,
                "activity": self.activity,
                "drive": self.drive_page,
            }[key])
            item = self._tree_items.get(key)
        else:
            self.stack.setCurrentWidget(self.queue_page)
            self._category_status = key if key in CATEGORY_KEYS else "all"
            item = self._tree_items.get(self._category_status)
            self._filter_form_queue()
        if item is not None:
            self.category_tree.blockSignals(True)
            self.category_tree.clearSelection()
            item.setSelected(True)
            self.category_tree.blockSignals(False)

    def _set_run_controls(self, running):
        self.run_tool.setEnabled(not running)
        self.stop_tool.setEnabled(running)
        self.start_action.setEnabled(not running)
        self.stop_action.setEnabled(running)
        self.dashboard.set_running(running)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------
    _RUN_STATE_MAP = {
        "Ready": ("ready", "Ready"),
        "Signing in": ("ready", "Signing in"),
        "Running": ("running", "Grading"),
        "Completed": ("completed", "Completed"),
        "Failed": ("failed", "Failed"),
        "Stopped": ("stopped", "Stopped"),
        "Waiting": ("stopped", "Waiting"),
    }

    def _set_run_state(self, text):
        state, label = self._RUN_STATE_MAP.get(text, ("ready", text))
        self.dashboard.set_run_pill(label, state)
        self.status_run_label.setText(str(label))
        self.status_run_dot.setProperty("state", state)
        repolish(self.status_run_dot)

    def _set_auto_status(self, text, state):
        self.status_auto_label.setText(text.replace("Auto Run: ", "Auto: "))
        self.status_auto_label.setToolTip(text)

    _ACTIVITY_DOT_MAP = {
        "idle": "off",
        "busy": "searching",
        "grading": "grading",
        "waiting": "active",
        "error": "failed",
    }

    def _activity_form_title(self):
        url = self.current_form_url
        if url:
            item = self._find_form_item_by_url(url)
            if item:
                meta = item.data(Qt.UserRole + 1) or {}
                title = meta.get("title")
                if title:
                    return str(title)
        return "Current form"

    def _set_activity(self, text, state="busy", tooltip=""):
        self.status_activity_label.setText(str(text))
        self.status_activity_label.setToolTip(str(tooltip or text))
        if state == "grading":
            self.dashboard.set_console_badge("live")
        elif state == "idle":
            self.dashboard.set_console_badge("idle")

    # ------------------------------------------------------------------
    # Shortcuts / tray
    # ------------------------------------------------------------------
    def _show_about_dialog(self):
        QMessageBox.about(
            self,
            "About Google Form Autograder",
            "Google Form Autograder\n\nStudio interface — AI jury grading pipeline.",
        )

    def _setup_keyboard_shortcuts(self):
        for key, handler in (
            ("Ctrl+R", self.run_grader),
            ("Ctrl+D", self.open_current_form_review),
            ("Ctrl+Shift+A", self.open_answer_key_dashboard),
            ("Ctrl+A", self.open_manual_add_dialog),
            ("Ctrl+K", self._focus_form_search),
            ("Ctrl+E", self.export_results_csv_dialog),
            ("Ctrl+Shift+S", self.stop_grading),
            ("Delete", self.remove_form),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(handler)

    def _focus_form_search(self):
        self._goto_page("queue")
        self.queue_page.search_input.setFocus()
        self.queue_page.search_input.selectAll()

    def _setup_system_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray_menu = QMenu(self)
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.restore_from_tray)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(exit_action)
        icon = self.windowIcon()
        if icon.isNull():
            icon = T.studio_icon("dashboard", 32, T.INDIGO)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Google Form Autograder")
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.restore_from_tray()

    def restore_from_tray(self):
        top = self.window()
        top.showNormal()
        top.raise_()
        top.activateWindow()

    def _notify(self, title, message, icon=None, timeout_ms=6000):
        tray = getattr(self, "tray_icon", None)
        if tray is not None and tray.isVisible():
            tray.showMessage(title, message, icon or QSystemTrayIcon.Information, timeout_ms)
            return True
        self.append_debug(f"<b>{title}</b> {message}")
        return False

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

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def refresh_auth_status(self):
        from auth import has_saved_login

        self._auth_signed_in = has_saved_login()
        signed = "signed in" if self._auth_signed_in else "not signed in"
        self.statusBar().showMessage(f"Google: {signed}", 8000)
        if hasattr(self, "login_action"):
            self.login_action.setEnabled(not self._auth_signed_in)
        if hasattr(self, "logout_action"):
            self.logout_action.setEnabled(self._auth_signed_in)

    def prompt_login_if_needed(self):
        from auth import has_saved_login

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
        from auth import clear_cached_credentials, sign_in

        if self.is_grading or (self.grader_thread and self.grader_thread.isRunning()):
            QMessageBox.information(self, "Grading Running", "Stop grading before changing Google login.")
            return
        try:
            self._set_run_state("Signing in")
            QApplication.processEvents()
            clear_cached_credentials()
            sign_in()
            self.service = None
            self.drive_service = None
            self.classroom_service = None
            self.refresh_auth_status()
            self._set_run_state("Ready")
            QMessageBox.information(self, "Google Login", "Google account is signed in.")
        except Exception as exc:
            self._set_run_state("Ready")
            self.refresh_auth_status()
            QMessageBox.critical(self, "Google Login Failed", str(exc))

    def logout_google(self):
        from auth import sign_out

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
            self._set_run_state("Ready")
            QMessageBox.information(self, "Google Logout", "Signed out. The saved Google token was removed.")
        except Exception as exc:
            self.refresh_auth_status()
            QMessageBox.critical(self, "Google Logout Failed", str(exc))

    # ------------------------------------------------------------------
    # Queue persistence + rows
    # ------------------------------------------------------------------
    def load_forms(self):
        try:
            with open("forms_to_grade.json", "r", encoding="utf-8") as fh:
                data = json.load(fh)
            form_urls = data.get("forms", [])
            for position, form in enumerate(form_urls, start=1):
                url = form.get("url") if isinstance(form, dict) else form
                title = form.get("title", "Untitled") if isinstance(form, dict) else "Untitled"
                self._add_form_to_queue(url, title, position=position)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        self._load_auto_partial_forms()

    def _load_auto_partial_forms(self):
        try:
            with open(self.auto_partial_forms_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.auto_partial_forms = {str(k): v for k, v in data.items() if isinstance(v, dict)}
        except (FileNotFoundError, json.JSONDecodeError):
            self.auto_partial_forms = {}

    def _save_auto_partial_forms(self):
        try:
            with open(self.auto_partial_forms_path, "w", encoding="utf-8") as fh:
                json.dump(self.auto_partial_forms, fh, indent=2, ensure_ascii=True)
        except Exception:
            pass

    def save_forms(self):
        forms = [{"url": url, "title": self.forms_data[url]} for url in self.forms_data]
        try:
            with open("forms_to_grade.json", "w", encoding="utf-8") as fh:
                json.dump({"forms": forms}, fh, indent=2, ensure_ascii=True)
        except Exception:
            pass

    def extract_form_id(self, url):
        try:
            if "/d/" in url:
                return url.split("/d/")[1].split("/")[0]
            if "/d/e/" in url:
                return url.split("/d/e/")[1].split("/")[0]
        except Exception:
            pass
        return None

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
        if status in {"failed", "skipped"}:
            return 0
        total = int(meta.get("total", 0) or 0)
        completed = int(meta.get("completed", 0) or 0)
        if total <= 0:
            return 0
        if status == "queued" and completed <= 0:
            return 0
        return max(0, min(100, int(round((completed / total) * 100))))

    def _queue_progress_text(self, meta):
        """Answers cell shows THIS form's graded/total, nothing else.

        Run-wide ModelProgress aggregates span every form in the run; using
        them here displayed one form another form's total (the /1748 bug).
        """
        total = int(meta.get("total", 0) or 0)
        completed = int(meta.get("completed", 0) or 0)
        if total <= 0:
            # No verified per-form total yet: never display a borrowed one.
            if str(meta.get("status", "")) == "done":
                return f"{max(0, completed)}/0"
            return "--"
        return f"{max(0, completed)}/{total}"

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
        eta = self._estimate_eta(completed, total, meta.get("elapsed", 0))
        return eta if eta != "--:--" else "--"

    def _queue_detail_text(self, meta):
        detail = str(meta.get("detail") or "Waiting for its turn")
        source = str(meta.get("source") or "Queue")
        if detail and source:
            return f"{source} · {detail}"
        return detail or source

    def _create_table_row(self, row, url, meta):
        """Populate a table row for a form (col-0 item carries url + meta)."""
        first = QTableWidgetItem(str(meta.get("title") or "Untitled"))
        first.setData(Qt.UserRole, url)
        first.setData(Qt.UserRole + 1, meta)
        first.setToolTip(self._format_form_meta_line(meta) + "\n" + str(meta.get("detail") or ""))
        self.queue_table.setItem(row, 0, first)
        for column in range(1, len(QUEUE_COLUMNS)):
            self.queue_table.setItem(row, column, QTableWidgetItem(""))
        self._row_bars.pop(url, None)
        bar = QProgressBar()
        bar.setObjectName("QueueProgress")
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(10)
        # Cell widgets fill the whole row; center the slim bar vertically
        # inside a transparent container so it isn't glued to the top edge.
        bar_host = QWidget()
        host_layout = QHBoxLayout(bar_host)
        host_layout.setContentsMargins(4, 0, 4, 0)
        host_layout.addWidget(bar)
        self.queue_table.setCellWidget(row, 2, bar_host)
        self._row_bars[url] = bar

    def _refresh_form_row(self, item):
        if item is None:
            return
        row = item.row()
        meta = item.data(Qt.UserRole + 1) or {}
        url = item.data(Qt.UserRole) or meta.get("url") or ""
        # Legacy builds cached run-wide model totals on row state; drop any
        # such keys so a borrowed denominator can never render again.
        if "model_total" in meta or "model_done" in meta:
            meta.pop("model_total", None)
            meta.pop("model_done", None)
            item.setData(Qt.UserRole + 1, meta)
        status = str(meta.get("status", "queued"))

        title_item = self.queue_table.item(row, 0)
        title_item.setText(str(meta.get("title") or "Untitled"))
        tooltip = self._format_form_meta_line(meta)
        detail = str(meta.get("detail") or "")
        if detail:
            tooltip += f"\n{detail}"
        skipped = meta.get("skipped_questions") or []
        if skipped:
            tooltip += f"\n{len(skipped)} skipped question(s) missing teacher answers"
        title_item.setToolTip(tooltip)

        status_item = self.queue_table.item(row, 1)
        status_item.setText(status_label(status))
        status_item.setForeground(STATUS_TEXT_COLORS.get(status, QColor("#000000")))

        bar = self._row_bars.get(url)
        percent = self._queue_progress_percent(meta)
        if bar is not None:
            bar.setValue(percent)

        def set_cell(column, text):
            cell = self.queue_table.item(row, column)
            if cell is not None:
                cell.setText(str(text))

        set_cell(3, self._queue_progress_text(meta))
        set_cell(4, str(int(meta.get("accepted", 0) or 0)))
        set_cell(5, str(int(meta.get("rejected", 0) or 0)))
        set_cell(6, str(int(meta.get("review_questions", 0) or 0)))
        set_cell(7, self._queue_eta_text(meta))
        set_cell(8, meta.get("finished_at") or meta.get("started_at") or "–")
        set_cell(9, str(meta.get("source") or "Queue"))
        self.queue_table.setRowHeight(row, 26)

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
            position=position or self.queue_table.rowCount() + 1,
            source=source,
            last_submission=last_submission,
        )
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        self._create_table_row(row, url, meta)
        self._refresh_form_row(self.queue_table.item(row, 0))
        self._refresh_queue_positions()
        return self.queue_table.item(row, 0)

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
        active = counts.get("queued", 0) + counts.get("running", 0)
        self.queue_page.set_summary(
            f"{total} form{'s' if total != 1 else ''} · {active} in queue",
            f"{counts.get('queued', 0)} queued | {counts.get('running', 0)} running | "
            f"{counts.get('done', 0)} done | {counts.get('partial', 0)} partial | "
            f"{counts.get('skipped', 0)} skipped | {counts.get('failed', 0)} failed",
        )
        if hasattr(self, "status_queue_label"):
            self.status_queue_label.setText(f"{total} form{'s' if total != 1 else ''} · {active} in queue")
        if hasattr(self, "_tree_items"):
            labels = {
                "all": f"All Forms ({total})",
                "queued": f"Queued ({counts.get('queued', 0)})",
                "running": f"Running ({counts.get('running', 0)})",
                "done": f"Finished ({counts.get('done', 0)})",
                "partial": f"Partial ({counts.get('partial', 0)})",
                "failed": f"Failed ({counts.get('failed', 0)})",
                "skipped": f"Skipped ({counts.get('skipped', 0)})",
            }
            for key, label in labels.items():
                item = self._tree_items.get(key)
                if item is not None:
                    item.setText(0, label)
        self._filter_form_queue()

    def update_in_queue_label(self):
        self._refresh_queue_positions()

    def _filter_form_queue(self, *_args):
        query = self.queue_page.search_input.text().strip().lower()
        selected_status = self.queue_page.filter_combo.currentText().strip().lower()
        category = getattr(self, "_category_status", "all")
        for index in range(self.form_list.count()):
            item = self.form_list.item(index)
            meta = item.data(Qt.UserRole + 1) or {}
            title = str(meta.get("title", "")).lower()
            status = str(meta.get("status", "queued")).lower()
            combo_matches = selected_status == "all" or status == selected_status
            category_matches = category == "all" or status == category
            self.queue_table.setRowHidden(index, not (query in title and combo_matches and category_matches))

    def _on_form_selection_changed(self, _current, _previous=None):
        return

    # -- queue context menu ------------------------------------------------
    def _on_form_table_context_menu(self, pos):
        item = self.form_list.itemAt(pos)
        if item is None:
            return
        self.form_list.setCurrentItem(item)
        menu = self._build_form_context_menu(item)
        self._active_context_menu = menu
        menu.exec(self.form_list.viewport().mapToGlobal(pos))
        self._active_context_menu = None

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
            act.triggered.connect(lambda _checked=False, w=where: self._context_move(item, w))
        menu.addSeparator()
        menu.addAction("Copy URL", lambda: self._context_copy_url(url))
        menu.addAction("Open in Browser", lambda: self._context_open_in_browser(url))
        menu.addSeparator()
        menu.addAction("Remove from Queue", lambda: self._context_remove(item, url))
        return menu

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
        row = self.form_list.row(item)
        count = self.form_list.count()
        target = {"top": 0, "bottom": count - 1, "up": row - 1}.get(where, row + 1)
        if target < 0 or target >= count or target == row:
            return
        taken = self.form_list.takeItem(row)
        self.form_list.insertItem(target, taken)
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
        QApplication.clipboard().setText(url)
        self.append_debug(f"<font color='gray'>[QUEUE] Copied URL: {self._short_url(url)}</font>")

    def _context_open_in_browser(self, url):
        from PySide6.QtGui import QDesktopServices, QUrl

        QDesktopServices.openUrl(QUrl(url))
        self.append_debug(f"<font color='gray'>[QUEUE] Opening: {self._short_url(url)}</font>")

    def _context_remove(self, item, url):
        meta = item.data(Qt.UserRole + 1) or {}
        title = meta.get("title") or "this form"
        reply = QMessageBox.question(
            self, "Remove from Queue", f"Remove '{title}' from the queue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if url in self.forms_data:
            del self.forms_data[url]
        self.form_list.takeItem(self.form_list.row(item))
        self.save_forms()
        self._refresh_queue_positions()

    def _requeue_selected_form(self):
        items = self.form_list.selectedItems()
        if not items:
            return
        self._context_set_status(items[0], "queued")

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

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    def update_config(self, key, value):
        try:
            config = {}
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as fh:
                    config = json.load(fh)
            config[key] = value
            with open("config.json", "w", encoding="utf-8") as fh:
                json.dump(config, fh, indent=4)
        except Exception:
            pass

    def load_config(self):
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as fh:
                    config = json.load(fh)
            else:
                config = {}
            modified = False
            if "batch_size" not in config:
                config["batch_size"] = 32
                modified = True
            if "grading_mode" not in config:
                config["grading_mode"] = "Whole Form"
                modified = True
            if modified:
                with open("config.json", "w", encoding="utf-8") as fh:
                    json.dump(config, fh, indent=4)
            self.grading_mode = config.get("grading_mode", "Whole Form")
        except Exception as exc:
            print(f"Error loading config: {exc}")
            self.grading_mode = "Whole Form"

    def _config_flag(self, key, default=False):
        try:
            with open("config.json", "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            return bool(cfg.get(key, default))
        except Exception:
            return bool(default)

    def _config_flag_float(self, key, default=0.0):
        try:
            with open("config.json", "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            return float(cfg.get(key, default) or 0.0)
        except Exception:
            return float(default)

    def _get_audit_path(self):
        try:
            with open("config.json", "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            return cfg.get("decision_audit_path", "logs/grading_decisions.jsonl")
        except Exception:
            return "logs/grading_decisions.jsonl"

    # ------------------------------------------------------------------
    # Dialogs / external tools
    # ------------------------------------------------------------------
    def open_manual_add_dialog(self):
        dialog = AutoAddDialog(self, mode="manual")
        dialog.exec()
        self._refresh_queue_positions()

    def open_auto_run_dialog(self):
        dialog = AutoAddDialog(self, mode="auto")
        dialog.exec()
        self.dashboard.set_mode_chip(self.grading_mode)

    def open_quick_grade_dialog(self):
        run_scan_source_dialog(self)

    def open_settings_dialog(self):
        show_settings_dialog(self)
        self.dashboard.set_mode_chip(self.grading_mode)

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
        dialog.exec()
        self._refresh_queue_positions()

    def open_current_form_review(self, _link="review"):
        target = self.current_form_url
        if not target and self.form_list.currentItem():
            target = self.form_list.currentItem().data(Qt.UserRole)
        self.open_answer_key_dashboard(target_url=target, auto_scan=True)

    def open_decision_audit_viewer(self):
        viewer = DecisionAuditViewer(self._get_audit_path(), self)
        viewer.exec()

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
            writer.writerow(["timestamp", "decision", "final_score", "confidence", "latency_ms",
                             "stage_reached", "answer", "expected"])
            for record in records:
                writer.writerow([
                    record.get("timestamp", ""), record.get("decision", ""),
                    record.get("final_score", ""), record.get("confidence", ""),
                    record.get("latency_ms", ""), record.get("stage_reached", ""),
                    record.get("answer", ""), record.get("expected", ""),
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
        if scores:
            lines.append(f"- Average final score: {avg_score:.3f}")
        if latencies:
            lines.append(f"- Average latency: {avg_latency:.0f} ms")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self._notify("Run Report Generated", f"Wrote {total} decisions to Reports/.")
        if os.path.exists(path) and sys.platform == "win32":
            os.startfile(os.path.abspath("Reports"))

    def grade_url_immediately(self, url, start_grading=True):
        try:
            forms = find_all_forms_in_sources(
                url,
                progress_callback=lambda msg: self.append_debug(f"[GRADE NOW] {msg}"),
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
                f"[GRADE NOW] Found {len(forms)} form(s), added {new_added} new form(s) to queue"
            )
            self.update_in_queue_label()
            self.save_forms()
            self.grading_mode = "Whole Form"
            if start_grading:
                self.run_grader()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to process URL: {exc}")
            self.append_debug(f"[GRADE NOW] Error: {exc}")

    def grade_all_forms_in_all_folders(self):
        try:
            folders = load_predefined_folders()
            if not folders:
                QMessageBox.warning(
                    self,
                    "No Predefined Sources",
                    "Add folders or form URLs in Auto Find first, then use Grade All.",
                )
                return
            self.append_debug(f"[GRADE ALL] Searching all forms in {len(folders)} source(s)")
            from_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
            to_dt = datetime.now(timezone.utc) + timedelta(days=1)
            self._start_source_scan(
                folders, "grade_all", mode="with_submissions", from_dt=from_dt, to_dt=to_dt
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Grade All failed: {exc}")
            self.append_debug(f"[GRADE ALL] failed: {exc}")

    def _start_source_scan(self, sources, action, mode="all_forms", from_dt=None, to_dt=None):
        if self.source_scan_thread and self.source_scan_thread.isRunning():
            QMessageBox.information(self, "Scan Running", "A source scan is already running.")
            return
        sources = list(sources or [])
        if not sources:
            QMessageBox.warning(self, "No Sources", "Add at least one folder or form URL.")
            return
        self.source_scan_action = action
        self.source_scan_before = set(self.forms_data.keys())
        self.source_scan_progress = QProgressDialog("Scanning sources...", "", 0, 0, self)
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
            sources, mode=mode, from_dt=from_dt, to_dt=to_dt
        )
        self.source_scan_thread.progress.connect(self._on_source_scan_progress)
        self.source_scan_thread.finished.connect(self._on_source_scan_finished)
        self.source_scan_thread.failed.connect(self._on_source_scan_failed)
        self.source_scan_thread.start()

    def _on_source_scan_progress(self, message):
        text = str(message)
        if getattr(self, "source_scan_progress", None):
            self.source_scan_progress.setLabelText(text)
        self.append_debug(f"[SCAN] {text}")

    def _on_source_scan_finished(self, forms):
        if getattr(self, "source_scan_progress", None):
            self.source_scan_progress.close()
        forms = list(forms or [])
        if not forms:
            QMessageBox.information(
                self, "No Forms Found", "No accessible forms were found in the selected source(s)."
            )
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
        new_urls = list(set(self.forms_data.keys()) - getattr(self, "source_scan_before", set()))
        self.append_debug(f"[SCAN] Found {len(forms)} form(s), added {new_added} new form(s) to queue")
        if self.source_scan_action == "grade_new":
            if not new_urls:
                QMessageBox.information(self, "No New Forms", "No new forms were found to grade.")
                return
            self.run_grader(target_urls=new_urls)
        elif self.source_scan_action == "grade_all":
            self.run_grader()

    def _on_source_scan_failed(self, error):
        if getattr(self, "source_scan_progress", None):
            self.source_scan_progress.close()
        QMessageBox.critical(self, "Scan Failed", str(error))
        self.append_debug(f"[SCAN] Failed: {error}")

    # ------------------------------------------------------------------
    # Drive Folders page (whole-Drive folder picker for auto-run)
    # ------------------------------------------------------------------
    def start_drive_folder_scan(self):
        if self.drive_scan_thread and self.drive_scan_thread.isRunning():
            self.drive_page.set_scan_state("Drive scan already running…", scanning=True)
            return
        self.drive_page.set_scan_state("Scanning Google Drive…", scanning=True)
        self.append_debug("[DRIVE] Scanning Google Drive for folders…")
        self.drive_scan_thread = DriveFolderScanThread(self)
        self.drive_scan_thread.progress.connect(
            lambda msg: self.drive_page.set_scan_state(msg, scanning=True)
        )
        self.drive_scan_thread.finished.connect(self._on_drive_scan_finished)
        self.drive_scan_thread.failed.connect(self._on_drive_scan_failed)
        self.drive_scan_thread.start()

    def _on_drive_scan_finished(self, nodes):
        nodes = list(nodes or [])
        self.drive_page.set_selected(load_selected_folders())
        self.drive_page.populate_tree(nodes)
        selected = len(self.drive_page.selected_urls())
        self.append_debug(
            f"[DRIVE] Folder scan complete: {len(nodes)} folder(s) found, {selected} selected"
        )

    def _on_drive_scan_failed(self, error):
        self.drive_page.set_scan_state(f"Drive scan failed: {error}")
        self.append_debug(f"[DRIVE] Folder scan failed: {error}")
        QMessageBox.warning(
            self, "Drive Scan Failed", f"Could not scan Google Drive folders:\n{error}"
        )

    def apply_drive_folder_selection(self, folder_urls):
        folder_urls = list(folder_urls or [])
        save_selected_folders(folder_urls)
        self.folders = list(folder_urls)
        saved = len(load_selected_folders())
        self.append_debug(
            f"[DRIVE] Auto-run scan scope updated: {saved} folder(s) selected"
        )
        self._notify(
            "Scan Sources Updated",
            f"Auto-run will now scan {saved} selected Drive folder(s).",
        )

    # ------------------------------------------------------------------
    # Grading run
    # ------------------------------------------------------------------
    def run_grader(self, force_recent_only=False, target_urls=None, force_whole_form=False):
        if not self.forms_data:
            if self.auto_mode:
                self.schedule_next_cycle()
            else:
                QMessageBox.information(self, "No Forms", "Add forms first.")
            return
        if self.is_grading or (self.grader_thread and self.grader_thread.isRunning()):
            self.append_debug("<font color='orange'>[GRADER] Grading already in progress</font>")
            return

        try:
            with open("config.json", "r", encoding="utf-8") as fh:
                preflight_cfg = json.load(fh)
        except Exception:
            preflight_cfg = {}
        if "llamacpp" in configured_provider_names(preflight_cfg) and bool(preflight_cfg.get("llamacpp_require_server", True)):
            llamacpp_only = is_llamacpp_only(preflight_cfg)
            try:
                from providers.llamacpp_provider import LlamaCppProvider

                llamacpp_ready = LlamaCppProvider().is_configured()
            except Exception:
                llamacpp_ready = False
            if not llamacpp_ready and bool(preflight_cfg.get("llamacpp_auto_start_server", True)):
                llamacpp_ready = self._start_llamacpp_server(preflight_cfg)
            if not llamacpp_ready and llamacpp_only:
                message = (
                    "llama.cpp-only grading is selected, but no compatible llama.cpp server is responding.\n\n"
                    "The app tried to start llama-server.exe but it did not become ready. "
                    "Check Settings > llama.cpp > Server Executable and Model Folder, then run grading again."
                )
                self._set_run_state("Waiting")
                self.append_debug(
                    "<font color='red'>[LLAMACPP] llama.cpp-only mode is selected, "
                    "but the local server is offline. Grading was not started.</font>"
                )
                QMessageBox.warning(self, "llama.cpp server offline", message)
                return
            if not llamacpp_ready:
                self.append_debug(
                    "<font color='orange'>[LLAMACPP] llama.cpp is configured but the local server is offline; "
                    "grading will fall back to the other enabled providers.</font>"
                )

        self.is_grading = True
        self._set_run_state("Running")
        self._set_run_controls(True)
        if self.auto_mode:
            self._set_auto_status("Auto Run: Grading", "grading")
        self.dashboard.clear_console()
        self.activity.clear_all()
        self.debug_lines = []
        self.finished_forms = []
        self.overall_forms_completed = 0
        self._metrics_cache = None
        self._metrics_last_elapsed = 0.0
        self._metrics_last_ts = time_module.monotonic()
        self._elapsed_ticker.start()

        if target_urls is not None:
            self.overall_forms_total = len(set(target_urls))
        else:
            self.overall_forms_total = sum(
                1
                for i in range(self.form_list.count())
                if (self.form_list.item(i).data(Qt.UserRole + 1) or {}).get("status", "queued") == "queued"
            )
        self.dashboard.set_forms_progress(0, self.overall_forms_total)
        self._reset_metric_labels()
        for i in range(self.form_list.count()):
            item = self.form_list.item(i)
            url = item.data(Qt.UserRole)
            if target_urls is not None and url not in target_urls:
                continue
            meta = item.data(Qt.UserRole + 1) or {}
            for key in ("status",):
                meta[key] = "queued"
            meta["started_at"] = None
            meta["finished_at"] = None
            meta["detail"] = "Waiting for its turn"
            for key in ("completed", "total", "accepted", "rejected", "review_questions",
                        "elapsed", "det_decisions", "ai_decisions", "ai_backlog"):
                meta[key] = 0
            meta["avg_latency_ms"] = 0.0
            meta["current_model"] = "Idle"
            item.setData(Qt.UserRole + 1, meta)
            self._refresh_form_row(item)
        self._refresh_queue_positions()

        try:
            with open("config.json", "r", encoding="utf-8") as fh:
                _cfg = json.load(fh)
            wp_enabled = bool(_cfg.get("enable_pipeline_workers", False))
            truncate_enabled = bool(_cfg.get("truncate_answers_before_grading", False))
        except Exception:
            wp_enabled = False
            truncate_enabled = False
        self.append_debug(f"<font color='cyan'>[GRADER] Worker pipeline: {'ON' if wp_enabled else 'OFF'}</font>")
        self.dashboard.set_mode_chip(
            "Recent Only" if (self.grading_mode == "Recent Only" or force_recent_only) and not force_whole_form
            else "Whole Form"
        )

        if truncate_enabled:
            try:
                from auth import get_service
                from answer_key_manager import keep_teacher_answers_only

                service = get_service()
            except Exception as exc:
                self.append_debug(f"<font color='orange'>[GRADER] Could not obtain service to truncate answers: {exc}</font>")
                service = None
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
                    self.append_debug(
                        f"<font color='cyan'>[GRADER] Truncated answers for {fid}: removed {result.get('removed', 0)} variants</font>"
                    )
                except Exception as exc:
                    self.append_debug(f"<font color='orange'>[GRADER] Failed to truncate answers for {fid}: {exc}</font>")

        grade_recent_only = force_recent_only or ((not force_whole_form) and self.grading_mode == "Recent Only")
        self.append_debug(
            f"<font color='blue'>[GRADER] Mode: {'RECENT_ONLY' if grade_recent_only else 'WHOLE_FORM'} · "
            f"forms={len(target_urls) if target_urls is not None else 'all queued'} · "
            f"recent_only={grade_recent_only} (recent window = since each form was last graded)</font>"
        )
        # Park the previous grader thread before replacing its reference: a
        # QThread destroyed while still finishing is fatal (qFatal -> fail-fast).
        old_grader = self.grader_thread
        if old_grader is not None:
            try:
                old_grader.finished.disconnect(self.on_grading_finished)
            except Exception:
                pass
            old_grader.finished.connect(lambda *_a, _t=old_grader: self._retire_search_thread(_t))
            self._retired_search_threads.append(old_grader)
        try:
            import crash_diagnostics

            crash_diagnostics.set_grading_state(
                phase="grading",
                mode=self.grading_mode,
                forms_total=int(self.overall_forms_total),
                subprocess="starting",
            )
            crash_diagnostics.record("grading_started", mode=self.grading_mode,
                                     forms_total=int(self.overall_forms_total))
        except Exception:
            pass
        self.grader_thread = GraderThread(grade_recent_only=grade_recent_only, form_urls=target_urls)
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.progress.connect(self.update_progress)
        self.grader_thread.model_progress.connect(self.update_model_progress)
        self.grader_thread.overall_progress.connect(self.update_overall_progress)
        self.grader_thread.form_metrics.connect(self.update_form_metrics)
        self.grader_thread.debug_message.connect(self.append_debug)
        self.grader_thread.current_form.connect(self.update_current_form)
        self.grader_thread.finished_form.connect(self.update_finished_form)
        self.grader_thread.skipped_form.connect(self.update_skipped_form)
        self.grader_thread.form_done.connect(self.update_form_done)
        self.grader_thread.form_row_progress.connect(self.update_form_row_progress)
        self.grader_thread.form_totals.connect(self.update_form_totals)
        self.grader_thread.start()

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
        self.dashboard.set_answer_progress(completed, total)
        self.dashboard.set_outcomes(accepted, rejected, review_questions)
        self.dashboard.set_elapsed(self._format_duration(elapsed_seconds))
        self.dashboard.set_eta(
            self._estimate_eta(int(completed), int(total), elapsed_seconds)
        )
        self.dashboard.set_metrics(
            rate=self._answers_per_minute(int(completed), elapsed_seconds),
            backlog=str(int(ai_backlog or 0)),
            latency=self._format_latency(avg_latency_ms),
            det=f"{int(det_decisions or 0)}",
            ai=f"{int(ai_decisions or 0)}",
        )
        model_text = str(current_model or "Idle")
        if model_text == "none":
            model_text = "Idle"
        self.dashboard.set_model(model_text, model_text)
        self._update_stage_stepper()

    def _reset_metric_labels(self):
        self._update_metric_labels(0, 0, 0, 0, 0, 0, 0, 0, 0.0, 0, "Idle")
        self.dashboard.set_metrics(pipeline="Idle")

    def _update_stage_stepper(self, q_det=None, q_ai=None, q_result=None, done=None, total=None):
        m = self._metrics_cache
        completed = int(m[0]) if m else 0
        answers_total = int(m[1]) if m else 0
        det_done = int(m[5]) if m else 0
        ai_done = int(m[6]) if m else 0
        if q_det is None:
            q_det = 0
        if q_ai is None:
            q_ai = getattr(self, "_metrics_backlog", 0)
        if q_result is None:
            q_result = 0
        if done is None:
            done = completed
        if total is None:
            total = answers_total
        judged = int(done or 0)
        waiting = max(0, int(total or 0) - judged) if total else 0
        in_flight = (q_det or 0) + (q_ai or 0)
        queued_busy = waiting > 0 or in_flight > 0
        states = {
            "queued": (
                "done" if total and judged >= total
                else ("active" if self.is_grading and queued_busy else "todo")
            ),
            "deterministic": ("active" if (q_det or 0) > 0 else ("done" if det_done else "todo")),
            "ai": ("active" if (q_ai or 0) > 0 else ("done" if ai_done else "todo")),
            "consensus": (
                "active" if (q_result or 0) > 0
                else ("done" if judged and not (q_ai or 0) and not (q_det or 0) else "todo")
            ),
            "applied": (
                "done" if total and judged >= total
                else ("active" if judged and self.is_grading else "todo")
            ),
        }
        counts = {
            "queued": waiting if waiting else (in_flight if in_flight else ("–" if not self.is_grading else 0)),
            "deterministic": (q_det or 0) if (q_det or 0) else det_done,
            "ai": (q_ai or 0) if (q_ai or 0) else ai_done,
            "consensus": judged,
            "applied": judged,
        }
        self.dashboard.set_stage_states(states)
        self.dashboard.set_stage_counts(counts)

    def update_progress(self, cur, tot):
        if not tot:
            self.dashboard.set_headline("No learner answers")
            self._set_activity("Evaluating answers…", "grading")
            return
        if not self._model_progress_seen:
            self.dashboard.set_answer_progress(cur, tot)
        self.dashboard.set_headline(f"Grading answer {cur} of {tot}")
        title = self._activity_form_title()
        self._set_activity(f"Grading “{title}” · {cur}/{tot} answers", "grading")
        self._update_stage_stepper(done=cur, total=tot)

    def update_model_progress(self, done, total):
        """Feed ONLY the dashboard ring with run-wide model progress.

        ModelProgress totals are combined across every form in the current
        grading run, so writing them onto a queue row would show one form
        another form's answer count. Per-row progress arrives exclusively
        via update_form_row_progress / update_form_totals.
        """
        self._model_progress_seen = True
        total = max(0, int(total or 0))
        done = max(0, min(int(done or 0), total))
        self.dashboard.set_answer_progress(done, total)

    def update_form_row_progress(self, form_id, done, total):
        """Update a single queue row's progress bar with ITS OWN form totals."""
        item = self._find_form_item_by_id(form_id) if form_id else None
        if not item:
            return
        meta = item.data(Qt.UserRole + 1) or {}
        meta["completed"] = int(done)
        meta["total"] = int(total)
        item.setData(Qt.UserRole + 1, meta)
        self._refresh_form_row(item)

    def update_form_totals(self, form_id, total):
        """Record THIS form's verified total as soon as its tasks are built,
        so the row shows "0/N" (never a borrowed denominator) before any
        grading results arrive."""
        item = self._find_form_item_by_id(form_id) if form_id else None
        if not item:
            return
        total = max(0, int(total or 0))
        if total <= 0:
            return
        meta = item.data(Qt.UserRole + 1) or {}
        meta["total"] = total
        item.setData(Qt.UserRole + 1, meta)
        self._refresh_form_row(item)

    def update_overall_progress(self, cur, tot):
        if not tot:
            return
        self.overall_forms_completed = cur
        self.overall_forms_total = tot
        self.dashboard.set_forms_progress(cur, tot)
        self._update_stage_stepper()

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
        self._metrics_last_elapsed = float(elapsed_seconds or 0.0)
        self._metrics_last_ts = time_module.monotonic()
        self._metrics_cache = (
            completed, total, accepted, review_questions,
            rejected, det_decisions, ai_decisions, avg_latency_ms,
        )
        item = self._find_form_item_by_url(self.current_form_url)
        ai_backlog = 0
        current_model = "Idle"
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            ai_backlog = meta.get("ai_backlog", 0)
            current_model = meta.get("current_model", "Idle")
        self._metrics_backlog = ai_backlog
        self._metrics_model = current_model
        self._update_metric_labels(
            completed, total, accepted, review_questions, elapsed_seconds,
            rejected, det_decisions, ai_decisions, avg_latency_ms,
            ai_backlog, current_model,
        )
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            # Answer-based metrics must NOT own the queue-row progress bar:
            # the bar's unit is individual judge calls (owned exclusively by
            # update_form_totals / update_form_row_progress). Keep the answer
            # counts under separate keys for tooltips/status text only.
            meta["metrics_completed"] = completed
            meta["metrics_total"] = total
            meta["accepted"] = accepted
            meta["rejected"] = rejected
            meta["review_questions"] = review_questions
            meta["elapsed"] = elapsed_seconds
            meta["det_decisions"] = det_decisions
            meta["ai_decisions"] = ai_decisions
            meta["avg_latency_ms"] = avg_latency_ms
            item.setData(Qt.UserRole + 1, meta)
            self._refresh_form_row(item)

    def _tick_elapsed(self):
        if not self.is_grading or self._metrics_cache is None or self._metrics_last_ts is None:
            return
        live_elapsed = self._metrics_last_elapsed + max(
            0.0, time_module.monotonic() - self._metrics_last_ts
        )
        (completed, total, accepted, review_questions,
         rejected, det_decisions, ai_decisions, avg_latency_ms) = self._metrics_cache
        self.dashboard.set_elapsed(self._format_duration(live_elapsed))
        self.dashboard.set_eta(self._estimate_eta(int(completed), int(total), live_elapsed))
        self.dashboard.set_metrics(rate=self._answers_per_minute(int(completed), live_elapsed))

    def update_current_form(self, url):
        self.current_form_url = url
        self._reset_metric_labels()
        item = self._find_form_item_by_url(url)
        title = "Current form"
        self.dashboard.set_headline("Preparing form…")
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            title = meta.get("title", title)
            self._set_form_status(
                item, "running",
                "Grading now: fetching responses, evaluating answers, applying updates",
            )
            self.form_list.scrollToItem(item)
            self.form_list.setCurrentItem(item)
        self.dashboard.set_subline(title)
        self._set_run_state("Running")
        self._set_activity(f"Grading “{title}”", "grading", tooltip=url)

    def update_finished_form(self, form_id):
        self.finished_forms.append(form_id)
        now_str = datetime.now().strftime("%H:%M:%S")
        item = self._find_form_item_by_id(form_id)
        title = "Unknown Form"
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            if meta.get("status") in {"skipped", "partial"}:
                label = "Partial" if meta.get("status") == "partial" else "Skipped"
                self.append_debug(f"<font color='orange'>[{now_str}] {label}: {meta.get('title', title)}</font>")
                return
            title = meta.get("title", title)
            self._set_form_status(item, "done", "Finished and saved grading updates")
        self.append_debug(f"<font color='green'>[{now_str}] Completed: {title}</font>")
        QTimer.singleShot(800, self._maybe_start_next_after_finish)

    def update_form_done(self, form_id, total, accepted, review, rejected):
        item = self._find_form_item_by_id(form_id) if form_id else None
        if not item:
            return
        meta = item.data(Qt.UserRole + 1) or {}
        # The bar's unit is judge calls: fill to the call-total already set by
        # update_form_totals instead of overwriting the denominator with the
        # answer count. Fall back to answers only when no call total exists.
        call_total = int(meta.get("total") or 0)
        meta["completed"] = call_total if call_total > 0 else int(total)
        meta["total"] = call_total if call_total > 0 else int(total)
        meta["metrics_completed"] = int(total)
        meta["metrics_total"] = int(total)
        meta["accepted"] = int(accepted)
        meta["rejected"] = int(rejected)
        meta["review_questions"] = int(review)
        item.setData(Qt.UserRole + 1, meta)
        self._refresh_form_row(item)

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

        if "missing teacher" in str(reason or "").lower():
            form_id = form_id or (self.extract_form_id(url) if url else None)
            if form_id:
                missing_qids = [
                    str(sq.get("question_id") or "") for sq in skipped_questions
                    if isinstance(sq, dict) and sq.get("question_id")
                ]
                current = self.auto_partial_forms.get(form_id)
                if current:
                    current.update({"url": url, "title": meta.get("title") or current.get("title")})
                    if missing_qids:
                        current["missing_question_ids"] = missing_qids
                    current["detected_at"] = datetime.now(timezone.utc).isoformat()
                else:
                    self.auto_partial_forms[form_id] = {
                        "url": url,
                        "title": meta.get("title") or "Untitled",
                        "missing_question_ids": missing_qids,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "last_check": None,
                    }
                self._save_auto_partial_forms()

    def _maybe_start_next_after_finish(self):
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
            self.append_debug("<font color='cyan'>[GRADER] Detected queued forms after finish. Starting next run…</font>")
            QTimer.singleShot(500, lambda: self.run_grader(target_urls=queued_urls))

    def on_grading_finished(self, success, msg):
        self.is_grading = False
        self._elapsed_ticker.stop()
        try:
            import crash_diagnostics

            crash_diagnostics.set_grading_state(phase="idle", subprocess="exited")
            crash_diagnostics.record("grading_finished", success=success, msg=msg)
        except Exception:
            pass
        self._set_run_controls(False)
        if self.auto_mode:
            self._set_auto_status("Auto Run: Waiting", "active")
            self._set_activity("Auto-run: cycle complete · preparing next check", "waiting")
        else:
            if success:
                self._set_activity("Grading completed", "idle")
            else:
                self._set_activity("Grading failed", "error")
        now_str = datetime.now().strftime("%H:%M:%S")
        if not success:
            self._set_run_state("Failed")
            for i in range(self.form_list.count()):
                item = self.form_list.item(i)
                meta = item.data(Qt.UserRole + 1) or {}
                if meta.get("status") == "running":
                    self._set_form_status(item, "failed", msg or "Grading process failed")
            self.append_debug(f"<font color='red'>[{now_str}] Grading failed: {msg}</font>")
        else:
            self._set_run_state("Completed")
            self.append_debug(f"<font color='green'>[{now_str}] Grading completed successfully!</font>")
            self.append_debug("<b><font color='green'>ALL FORMS FINISHED. Grading run complete.</font></b>")
            if not self.auto_mode:
                self.append_debug(
                    "<font color='gray'>[GRADER] Completed forms remain visible for review. Use Clear All when ready.</font>"
                )

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
            self.append_debug(
                f"<font color='cyan'>[GRADER] Found {len(queued_urls)} queued form(s) added during execution. Starting next run…</font>"
            )
            QTimer.singleShot(1000, lambda: self.run_grader(target_urls=queued_urls))
            return

        self._stop_llamacpp_server_if_enabled("llamacpp_stop_server_after_grading", "after grading")

        if self.auto_mode:
            if success and self._partial_regrade_pending:
                re_graded = []
                for form_id in list(self._partial_regrade_pending):
                    item = self._find_form_item_by_id(form_id)
                    if not item:
                        continue
                    meta = item.data(Qt.UserRole + 1) or {}
                    if meta.get("status") == "done":
                        re_graded.append(form_id)
                if re_graded:
                    for form_id in re_graded:
                        self._partial_regrade_pending.discard(form_id)
                        self.auto_partial_forms.pop(form_id, None)
                    self._save_auto_partial_forms()
                    self.append_debug(
                        f"<font color='green'>[{now_str}] Re-graded partial form(s) after teacher answers: "
                        f"{len(re_graded)} form(s)</font>"
                    )
                    if getattr(self, "auto_notify_on_new", True):
                        self._notify(
                            "Teacher Answers Added — Re-graded",
                            f"{len(re_graded)} form(s) that were missing teacher answers have now been fully graded.",
                        )
            remaining_forms = self.form_list.count()
            finished_count = len(self.finished_forms)
            self.append_debug(
                f"<font color='blue'>[AUTO] Session stats: finished {finished_count}, in queue {remaining_forms}</font>"
            )
            self.append_debug(
                "<font color='gray'>[AUTO] Finished forms are kept in the list for review. "
                "Use Clear Completed / Clear All to remove them manually.</font>"
            )
            self.schedule_next_cycle()
        else:
            if success:
                self._notify("Grading Completed", "All queued forms have been graded.")
                self._notify_pending_reviews()

    # ------------------------------------------------------------------
    # Auto mode
    # ------------------------------------------------------------------
    def start_auto_mode(self):
        if self.auto_mode:
            self.append_debug("<font color='orange'>[AUTO] Auto mode already running</font>")
            return
        self.auto_mode = True
        self._set_auto_status("Auto Run: Active", "active")
        self.append_debug("<b><font color='green'>AUTO RUN STARTED</font></b>")
        mode_text = "Recent Only" if self.grading_mode == "Recent Only" else "Whole Form"
        budget = getattr(self, "auto_spend_budget_usd", 0.0)
        budget_text = f" Budget set to ${budget:.2f}/run." if budget > 0 else ""
        self._notify(
            "Auto Run Started",
            f"Auto-run is running in {mode_text} mode. "
            f"{'Only new/recent submissions will be detected and graded.' if mode_text == 'Recent Only' else 'Any form with submissions will be detected and the whole form graded.'}{budget_text}",
        )
        self.last_check_time = None
        if self.use_time_schedule:
            self._start_time_scheduler()
        else:
            self.append_debug("<font color='blue'>[AUTO] Using interval-based scheduling</font>")
            self.schedule_next_cycle()

    def _start_time_scheduler(self):
        self.append_debug("<font color='blue'>[AUTO] Starting time-based scheduler</font>")
        try:
            time_text = self.schedule_time_val.toString("HH:mm")
        except Exception:
            time_text = "09:00"
        self.append_debug(
            f"<font color='blue'>[AUTO] Time: {time_text}, Days: {[i for i, d in enumerate(self.selected_days) if d]}</font>"
        )
        next_run = self._get_next_run_time()
        delay_seconds = max(10, (next_run - datetime.now(timezone.utc)).total_seconds())
        self.append_debug(f"<font color='blue'>[AUTO] Next scheduled run in {delay_seconds:.0f} seconds</font>")
        auto_scheduler.start(
            interval_minutes=self.interval_seconds // 60,
            folders=self.folders,
            recency_minutes=self.recency_minutes,
            grade_recent_only=(self.grading_mode == "Recent Only"),
        )

    def _start_auto_search_thread(self, from_dt, to_dt):
        """Start a SearchThread without ever dropping the last reference to a
        possibly-still-finishing QThread.

        Replacing self.auto_search_thread directly garbage-collects the
        previous cycle's QThread object; if its underlying C++ thread has not
        fully exited yet, Qt destroys a running QThread, which is fatal
        (qFatal -> abort -> 0xC0000409, the exact crash signature captured in
        the Windows Event Log). Retired threads are parked until Qt reports
        them finished, then released.
        """
        old = self.auto_search_thread
        if old is not None:
            try:
                old.finished.disconnect(self.on_auto_search_finished)
            except Exception:
                pass
            self._retired_search_threads.append(old)
            if len(self._retired_search_threads) > 8:
                self._retired_search_threads.pop(0)
        thread = SearchThread(self.folders, from_dt, to_dt)
        thread.progress.connect(
            lambda msg: self.append_debug(f"<font color='gray'>[SEARCH] {msg}</font>")
        )
        thread.finished.connect(self.on_auto_search_finished)
        thread.finished.connect(lambda t=thread: self._retire_search_thread(t))
        self.auto_search_thread = thread
        thread.start()

    def _retire_search_thread(self, thread):
        try:
            self._retired_search_threads = [
                t for t in self._retired_search_threads if t is not thread and t.isRunning()
            ]
        except Exception:
            pass

    def auto_cycle(self):
        if not self.auto_mode or self.is_closing:
            return
        if self.use_time_schedule and not self._should_run_now():
            next_run = self._get_next_run_time()
            delay = (next_run - datetime.now(timezone.utc)).total_seconds()
            self.append_debug(
                f"<font color='gray'>[AUTO] Skipping cycle — next scheduled run in {delay / 60:.1f} minutes</font>"
            )
            self.schedule_next_cycle()
            return
        if self.is_searching:
            self.append_debug("<font color='orange'>[AUTO] Search already in progress, skipping cycle</font>")
            self.schedule_next_cycle()
            return
        if self.is_grading or (self.grader_thread and self.grader_thread.isRunning()):
            # A Drive scan fired during an active grading run is pure churn:
            # anything it finds cannot start grading anyway (guarded), and the
            # scan+grade overlap is exactly where every recorded native GUI
            # abort happened. Defer the cycle until grading finishes.
            self.append_debug(
                "<font color='gray'>[AUTO] Grading in progress - deferring source scan to next cycle</font>"
            )
            self.schedule_next_cycle()
            return

        now_utc = datetime.now(timezone.utc)
        if self.last_check_time is None:
            if self.grading_mode == "Recent Only":
                from_dt = now_utc - timedelta(minutes=self.recency_minutes)
                self.append_debug(
                    f"<font color='blue'>[AUTO] First auto check: scanning last {self.recency_minutes} minutes (Recent Only)</font>"
                )
            else:
                from_dt = now_utc - timedelta(days=365 * 20)
                self.append_debug(
                    "<font color='blue'>[AUTO] First auto check: scanning entire form history (Whole Form)</font>"
                )
        else:
            from_dt = self.last_check_time
            self.append_debug("<font color='blue'>[AUTO] Incremental check: since last scan</font>")
        to_dt = now_utc
        self.append_debug(
            f"<font color='purple'>[AUTO] Search range: "
            f"{from_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} → {to_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}</font>"
        )
        self.is_searching = True
        self._set_auto_status("Auto Run: Searching", "searching")
        self._set_activity("Searching sources for new submissions…", "busy")
        self._start_auto_search_thread(from_dt, to_dt)

    def on_auto_search_finished(self, forms):
        self.is_searching = False
        if self.is_closing:
            return
        now_str = datetime.now().strftime("%H:%M:%S")
        self.append_debug(
            f"<font color='blue'>[AUTO {now_str}] Search completed: found {len(forms)} form(s) with recent submissions</font>"
        )
        new_added = 0
        found_urls = set()
        for form in forms:
            url = form["url"]
            found_urls.add(url)
            if url in self.forms_data:
                continue
            title = form["title"]
            last = form.get("last_submission")
            if last:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                last_str = last.astimezone(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M:%S ICT")
            else:
                last_str = "None"
            self._add_form_to_queue(url, title, source="Auto Find", last_submission=last_str)
            new_added += 1

        if found_urls:
            recent_only = self.grading_mode == "Recent Only"
            if new_added > 0:
                if getattr(self, "auto_notify_on_new", True):
                    self._notify(
                        "New Forms Found",
                        f"Added {new_added} new form(s). Starting grading "
                        f"({'recent submissions only' if recent_only else 'the entire form'}).",
                    )
                self.append_debug(
                    f"<font color='green'>[AUTO] Added {new_added} new form(s). Starting grading…</font>"
                )
            else:
                self.append_debug(
                    "<font color='green'>[AUTO] Found recent submissions in existing queued form(s). Starting grading…</font>"
                )
            self.save_forms()
            self.run_grader(force_recent_only=recent_only)
        else:
            self.append_debug("<font color='orange'>[AUTO] No new forms with recent submissions found.</font>")
            if getattr(self, "auto_notify_on_new", True):
                self._notify(
                    "No New Submissions",
                    "Auto-run did not detect any new answers with submissions this cycle.",
                )
            self.schedule_next_cycle()

        self.last_check_time = datetime.now(timezone.utc)
        self._recheck_partial_forms()

    def _recheck_partial_forms(self):
        if not self.auto_mode or self.is_closing:
            return
        if not self.auto_partial_forms:
            return
        if self.is_grading:
            return
        now_utc = datetime.now(timezone.utc)
        partial_forms_current = {}
        for form_id, info in list(self.auto_partial_forms.items()):
            url = info.get("url")
            if not url:
                continue
            try:
                from form_utils import get_form_structure
                from auth import get_service

                service = get_service()
                structure = get_form_structure(service, form_id)
            except Exception:
                continue
            if not structure:
                continue
            missing_qids = info.get("missing_question_ids") or []
            if missing_qids:
                try:
                    last_check = datetime.fromisoformat(info["last_check"]) if info.get("last_check") else None
                except Exception:
                    last_check = None
                if last_check and (now_utc - last_check).total_seconds() < 1800:
                    partial_forms_current[form_id] = info
                    continue
                still_missing = self._current_missing_qids(service, form_id, structure, missing_qids)
                info["last_check"] = now_utc.isoformat()
                if still_missing:
                    partial_forms_current[form_id] = info
                    continue
            else:
                from form_context_builder import get_effective_expected

                if not any(get_effective_expected(q) for q in structure):
                    info["last_check"] = now_utc.isoformat()
                    partial_forms_current[form_id] = info
                    continue
            self.auto_partial_forms.pop(form_id, None)
            self._partial_regrade_pending.add(form_id)
            self.append_debug(
                f"<font color='cyan'>[AUTO] Teacher answers found for previously-partial form "
                f"{info.get('title') or form_id} — scheduling whole-form re-grade</font>"
            )
        self.auto_partial_forms = partial_forms_current
        self._save_auto_partial_forms()
        if self._partial_regrade_pending:
            urls = []
            seen = set()
            for form_id in list(self._partial_regrade_pending):
                item = self._find_form_item_by_id(form_id)
                if item and item.data(Qt.UserRole):
                    url = item.data(Qt.UserRole)
                elif form_id in self.auto_partial_forms:
                    url = self.auto_partial_forms.get(form_id, {}).get("url")
                else:
                    url = None
                if not url or url in seen:
                    continue
                seen.add(url)
                urls.append(url)
            if urls:
                QTimer.singleShot(500, lambda: self.run_grader(target_urls=urls, force_whole_form=True))
            else:
                self._partial_regrade_pending.clear()

    def _current_missing_qids(self, service, form_id, structure, missing_qids):
        from form_context_builder import get_effective_expected

        try:
            expected_by_item_id = {}
            form_data = service.forms().get(formId=form_id).execute()
            for item in form_data.get("items", []):
                if "questionItem" not in item:
                    continue
                item_id = item.get("itemId")
                grading = item["questionItem"]["question"].get("grading", {})
                answers = grading.get("correctAnswers", {}).get("answers", [])
                expected_by_item_id[item_id] = [a["value"] for a in answers if "value" in a]
        except Exception:
            return None
        still_missing = []
        for q in structure:
            qid = q.get("questionId")
            if qid not in missing_qids:
                continue
            expected = get_effective_expected(q, expected_by_item_id.get(q.get("itemId"), []))
            if not expected:
                still_missing.append(qid)
        return still_missing

    def _get_next_run_time(self):
        now = datetime.now(timezone.utc)
        current_day = now.weekday()
        if self.schedule_time_val:
            target_hour = self.schedule_time_val.hour()
            target_minute = self.schedule_time_val.minute()
        else:
            target_hour, target_minute = 9, 0
        for days_ahead in range(8):
            test_day = (current_day + days_ahead) % 7
            if self.selected_days[test_day]:
                next_run = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
                next_run = next_run + timedelta(days=days_ahead)
                if next_run > now:
                    return next_run
        return now + timedelta(seconds=10)

    def _should_run_now(self):
        now = datetime.now(timezone.utc)
        current_day = now.weekday()
        current_time = time(now.hour, now.minute)
        if not self.selected_days[current_day]:
            return False
        if self.schedule_time_val:
            target_time = time(self.schedule_time_val.hour(), self.schedule_time_val.minute())
            time_diff = abs(
                (current_time.hour * 60 + current_time.minute)
                - (target_time.hour * 60 + target_time.minute)
            )
            return time_diff <= 5
        return True

    def schedule_next_cycle(self):
        if not self.auto_mode or self.is_closing:
            return
        if self.auto_timer:
            self.auto_timer.stop()
            self.auto_timer.deleteLater()
            self.auto_timer = None
        if self.use_time_schedule:
            next_run = self._get_next_run_time()
            delay_seconds = max(10, (next_run - datetime.now(timezone.utc)).total_seconds())
            next_str = next_run.strftime("%a %H:%M:%S")
            self.append_debug(f"<font color='gray'>[AUTO] Next scheduled run in {delay_seconds:.0f}s at {next_str}</font>")
            self._set_activity(f"Auto-run waiting · next check {next_str}", "waiting")
            self.auto_timer = QTimer()
            self.auto_timer.setSingleShot(True)
            self.auto_timer.timeout.connect(self._on_scheduler_timeout)
            self.auto_timer.start(int(delay_seconds * 1000))
        else:
            minutes = self.interval_seconds // 60
            next_check = datetime.now() + timedelta(seconds=self.interval_seconds)
            next_str = next_check.strftime("%H:%M:%S")
            self.append_debug(f"<font color='gray'>[AUTO] Next check in {minutes} minute(s) at {next_str}</font>")
            self._set_activity(f"Auto-run waiting · next check in {minutes} min", "waiting")
            self.auto_timer = QTimer()
            self.auto_timer.setSingleShot(True)
            self.auto_timer.timeout.connect(self.auto_cycle)
            self.auto_timer.start(self.interval_seconds * 1000)

    def _on_scheduler_timeout(self):
        self.auto_cycle()

    def stop_auto_mode(self):
        self.auto_mode = False
        self._set_auto_status("Auto Run: Off", "off")
        self._set_activity("Auto run stopped · app idle", "idle")
        self.append_debug("<b><font color='red'>AUTO RUN STOPPED</font></b>")
        if self.auto_timer:
            self.auto_timer.stop()
            self.auto_timer.deleteLater()
            self.auto_timer = None
        auto_scheduler.stop()
        if self.auto_search_thread and self.auto_search_thread.isRunning():
            self.auto_search_thread.terminate()
            self.auto_search_thread.wait(5000)
        if self.grader_thread and self.grader_thread.isRunning():
            self.grader_thread.terminate()
            self.grader_thread.wait(5000)
        self.is_searching = False
        self.is_grading = False

    def stop_grading(self):
        self.auto_mode = False
        self.append_debug("<b><font color='red'>STOPPING GRADING…</font></b>")
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
        self._set_run_controls(False)
        self._set_run_state("Stopped")
        if not self.auto_mode:
            self._set_auto_status("Auto Run: Off", "off")
            self._set_activity("Stopped", "idle")

    # ------------------------------------------------------------------
    # Telemetry ingestion (console lines -> structured state)
    # ------------------------------------------------------------------
    def append_debug(self, message):
        self.debug_lines.append(message)
        if len(self.debug_lines) > self.max_gui_log_lines:
            del self.debug_lines[: len(self.debug_lines) - self.max_gui_log_lines]
        try:
            import crash_diagnostics

            plain = message if "<" not in message else re.sub(r"<[^>]+>", "", str(message))
            crash_diagnostics.record("debug", text=plain)
        except Exception:
            pass
        if "<" not in message:
            self._ingest_telemetry(message)
            self.activity.route_raw(message)
        self.dashboard.append_console(message)
        self.activity.append_console(message)

    def _ingest_telemetry(self, message):
        try:
            if "[Worker Metrics]" in message:
                payload = message.split("[Worker Metrics]", 1)[1].strip()
                self._update_worker_metrics(payload)
            elif "[DISPATCH METRICS]" in message:
                payload = message.split("[DISPATCH METRICS]", 1)[1].strip()
                self._update_worker_metrics(payload)
            elif "[HEARTBEAT]" in message:
                self._update_current_model_from_heartbeat(message)
            elif "[APP WORKER]" in message:
                self._update_app_worker(message.split("[APP WORKER]", 1)[1].strip())
            elif "[PROVIDER METRICS]" in message:
                self._update_provider_metrics(message.split("[PROVIDER METRICS]", 1)[1].strip())
            elif "[PROVIDER WORKER]" in message:
                self._update_provider_worker(message.split("[PROVIDER WORKER]", 1)[1].strip())
        except Exception:
            pass

    def _update_worker_metrics(self, payload):
        parsed = telemetry.parse_worker_metrics(payload)
        q_fetch = parsed["q_fetch"]
        q_pending = parsed["pending"]
        q_det = parsed["q_det"]
        q_ai = parsed["q_ai"]
        q_ai_actual = parsed["q_ai_actual"]
        q_result = parsed["q_result"]
        done = parsed["done"]
        total = parsed["total"]
        q_ai_display = q_ai_actual if q_ai_actual is not None else q_ai

        self.activity.set_badge("pipeline", f"q: {'–' if q_fetch is None else q_fetch}, buf: {'–' if q_pending is None else q_pending}")
        self.activity.set_badge("det", f"q: {'–' if q_det is None else q_det}")
        self.activity.set_badge("ai", f"q: {'–' if q_ai_display is None else q_ai_display}")
        self.activity.set_badge("agg", f"q: {'–' if q_result is None else q_result}")

        item = self._find_form_item_by_url(self.current_form_url)
        if item and q_ai_display is not None:
            meta = item.data(Qt.UserRole + 1) or {}
            meta["ai_backlog"] = q_ai_display
            item.setData(Qt.UserRole + 1, meta)
            self._refresh_form_row(item)
            self._metrics_backlog = q_ai_display
            self.dashboard.set_metrics(backlog=str(int(q_ai_display or 0)))
        self._update_pipeline_state(q_fetch, q_pending, q_det, q_ai_display, q_result, done, total)

    def _update_pipeline_state(self, q_fetch, q_pending, q_det, q_ai, q_result, done, total):
        if total is not None and done is not None and total > 0 and done >= total:
            state = "Completed"
        elif (q_fetch or 0) > 0 or (q_pending or 0) > 0:
            if (q_det or 0) <= 1 and (q_ai or 0) <= 1:
                state = "Feeding"
            else:
                state = "Balanced"
        elif (q_ai or 0) > 0 and not (q_det or 0) and not (q_fetch or 0) and not (q_pending or 0):
            state = "AI-drain"
        elif (q_result or 0) > 0 and not (q_det or 0) and not (q_ai or 0):
            state = "Apply-drain"
        elif not (q_fetch or 0) and not (q_det or 0) and not (q_ai or 0) and not (q_result or 0):
            if total is not None and done is not None and total > 0 and done < total:
                state = "Stalled"
            else:
                state = "Idle"
        else:
            state = "Balanced"
        self.dashboard.set_metrics(pipeline=state)
        self._update_stage_stepper(
            q_det=q_det, q_ai=q_ai, q_result=q_result, done=done, total=total
        )

    def _update_current_model_from_heartbeat(self, message):
        model = telemetry.parse_active_model(message)
        if not model:
            return
        if model == "none":
            model = "Idle"
        item = self._find_form_item_by_url(self.current_form_url)
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            meta["current_model"] = model
            item.setData(Qt.UserRole + 1, meta)
        self._metrics_model = model
        self.providers_page.set_active_model(model, model)
        self.dashboard.set_model(model, model)

    def _update_app_worker(self, payload):
        data = telemetry.parse_app_worker(payload)
        worker_id = data["id"]
        answers = data["answers"]
        primary = f"{answers} answer{'s' if str(answers) != '1' else ''}"
        secondary = "Waiting" if data["current"] == "-" else f"Current: {data['current']}"
        stats = f"latency {data['latency_ms']}ms · wait {data['queue_wait_ms']}ms"
        self._set_worker_card("app", worker_id, "AI worker", data["status"], primary, secondary, stats)

    def _update_provider_worker(self, payload):
        data = telemetry.parse_provider_worker(payload)
        provider = data["provider"]
        title_prefix = (
            "OpenRouter" if provider == "openrouter"
            else "llama.cpp" if provider == "llamacpp"
            else "Ollama" if provider == "ollama"
            else provider.title()
        )
        if data["status"] == "running":
            primary = data["model"]
            secondary = f"request {data['request']}"
        else:
            primary = f"Last {data['latency_ms']}ms"
            secondary = f"{provider}: {data['status']}"
        stats = f"latency {data['latency_ms']}ms · wait {data['queue_wait_ms']}ms"
        self._set_worker_card("provider", data["id"], title_prefix, data["status"], primary, secondary, stats)

    def _update_provider_metrics(self, payload):
        parsed = telemetry.parse_provider_metrics(payload)
        providers = parsed["providers"]
        for name, info in providers.items():
            self.providers_page.set_provider(name, info)
        if providers:
            self.activity.set_badge(
                "providers",
                " | ".join(
                    f"{name[:2].upper()}: {info.get('queue', 0)}" for name, info in providers.items()
                ),
            )
            self._provider_summary_text = " | ".join(
                f"{name} {info.get('health', '-')} q:{info.get('queue', 0)} "
                f"ok/fail:{info.get('done', 0)}/{info.get('failed', 0)}"
                for name, info in providers.items()
            )
            self._refresh_worker_summaries()
            for name, info in providers.items():
                model = info.get("last_model")
                if model and model != "-":
                    self.providers_page.set_active_model_sub(
                        f"Last provider model: {model}"
                    )
                    break
        self._update_model_health(parsed)

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

    def _update_model_health(self, parsed):
        or_model = parsed.get("providers", {}).get("openrouter", {}).get("last_model", "-")
        ol_model = parsed.get("providers", {}).get("ollama", {}).get("last_model", "-")
        avg_ms = parsed.get("avg_ms", 0)
        success_rate = parsed.get("or_last_success_rate", 0.0) or 0.0
        try:
            success_percent = float(success_rate) * 100.0
        except Exception:
            success_percent = 0.0
        cost_value = float(parsed.get("or_cost_usd", 0.0) or 0.0)

        remaining_cost = "-"
        item = self._find_form_item_by_url(self.current_form_url)
        if item:
            meta = item.data(Qt.UserRole + 1) or {}
            completed = int(meta.get("completed", 0) or 0)
            total_answers = int(meta.get("total", 0) or 0)
            if completed > 0 and total_answers > completed and cost_value > 0:
                remaining = (cost_value / completed) * (total_answers - completed)
                remaining_cost = f"${remaining:.4f}"

        self.providers_page.set_model_health(
            "current",
            f"OpenRouter: {or_model} | Ollama: {ol_model}",
            f"OpenRouter current/last model: {or_model}\nOllama current/last model: {ol_model}",
        )
        self.providers_page.set_model_health(
            "success", f"{success_percent:.1f}% success on current OpenRouter model · avg {avg_ms}ms"
        )
        self.providers_page.set_model_health(
            "limits",
            f"{parsed.get('or_models_available', 0)}/{parsed.get('or_models_total', 0)} available | "
            f"{parsed.get('or_models_rate_limited', 0)} rate-limited | {parsed.get('or_models_failed', 0)} failed",
        )
        self.providers_page.set_model_health(
            "json",
            f"{parsed.get('or_json_failures', 0)} JSON failures total | "
            f"{parsed.get('or_last_json_failures', 0)} on current model",
        )
        self.providers_page.set_model_health(
            "quality",
            f"Ollama suspicion avg {parsed.get('or_avg_suspicion', 0):.3f} | "
            f"current {parsed.get('or_last_suspicion', 0):.3f}",
            "0.00 is trusted, 1.00 is highly suspicious according to the local Ollama monitor.",
        )
        self.providers_page.set_model_health(
            "cooldown",
            f"current {self._format_seconds_compact(parsed.get('or_last_cooldown_s', 0))} | "
            f"max {self._format_seconds_compact(parsed.get('or_max_cooldown_s', 0))}",
        )
        cost_line = f"${cost_value:.4f} so far · est remaining {remaining_cost}"
        budget = self._config_flag_float("max_openrouter_spend_usd_per_run", 0.0)
        if budget > 0 and cost_value >= budget:
            cost_line = f"${cost_value:.4f} · ⚠ OVER BUDGET (${budget:.2f})"
            if not getattr(self, "_notified_budget_warning", False):
                self._notify_budget_warning(cost_value, budget)
        self.providers_page.set_model_health("cost", cost_line)
        self.providers_page.set_model_health("reason", parsed.get("or_selection_reason", "-"))

    def _notify_budget_warning(self, cost_value, budget):
        self._notified_budget_warning = True
        self._notify(
            "OpenRouter Budget Reached",
            f"Current spend ${cost_value:.4f} has reached the budget of ${budget:.2f}.",
            QSystemTrayIcon.Warning,
        )

    # ------------------------------------------------------------------
    # Worker cards (names preserved for the settings dialog hook)
    # ------------------------------------------------------------------
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

    def _worker_number(self, worker_id):
        try:
            return int(str(worker_id).rsplit("-", 1)[-1])
        except Exception:
            return 0

    def _worker_allowed_by_config(self, group, worker_id):
        counts = self._configured_worker_counts()
        number = self._worker_number(worker_id)
        if group == "app":
            return number <= counts.get("ai", 1)
        for provider in ("openrouter", "llamacpp", "ollama"):
            if str(worker_id).startswith(f"{provider}-"):
                return number <= counts.get(provider, 0)
        return True

    def _ensure_worker_card(self, group, worker_id, title_prefix):
        cards = self.app_worker_cards if group == "app" else self.provider_worker_cards
        if worker_id in cards:
            return cards[worker_id]
        title = self._ai_worker_display_name(worker_id) if group == "app" else f"{title_prefix} {worker_id.split('-', 1)[-1]}"
        chip = self.providers_page.add_worker_chip(worker_id, title)
        cards[worker_id] = {
            "chip": chip,
            "title": title,
            "state": "idle",
            "group": group,
        }
        if group != "app":
            self.provider_worker_states.setdefault(worker_id, {"state": "idle"})
        return cards[worker_id]

    def _set_worker_card(self, group, worker_id, title_prefix, status, primary, secondary, stats):
        if not self._worker_allowed_by_config(group, worker_id):
            return
        card = self._ensure_worker_card(group, worker_id, title_prefix)
        state = str(status or "idle").lower()
        card["state"] = state
        detail = f"{primary} · {secondary} · {stats}"
        card["chip"].set_info(state, detail, f"{worker_id}\n{detail}")
        if group != "app":
            self.provider_worker_states[worker_id] = {
                "state": state,
                "provider": title_prefix,
                "primary": primary,
                "secondary": secondary,
                "stats": stats,
            }
        self._refresh_worker_summaries()

    def _remove_worker_card(self, group, worker_id):
        cards = self.app_worker_cards if group == "app" else self.provider_worker_cards
        card = cards.pop(worker_id, None)
        if not card:
            return
        self.providers_page.remove_worker_chip(worker_id)
        if group != "app":
            self.provider_worker_states.pop(worker_id, None)

    def _prune_worker_cards_to_counts(self, counts):
        for worker_id in list(self.app_worker_cards):
            if self._worker_number(worker_id) > counts.get("ai", 1):
                self._remove_worker_card("app", worker_id)
        for provider in ("openrouter", "llamacpp", "ollama"):
            limit = counts.get(provider, 0)
            for worker_id in list(self.provider_worker_cards):
                if worker_id.startswith(f"{provider}-") and self._worker_number(worker_id) > limit:
                    self._remove_worker_card("provider", worker_id)

    def _initialize_worker_cards(self):
        self._sync_worker_cards_to_config()

    def _sync_worker_cards_to_config(self):
        counts = self._configured_worker_counts()
        self._prune_worker_cards_to_counts(counts)
        for index in range(len(self.app_worker_cards), counts.get("ai", 4)):
            self._ensure_worker_card("app", f"ai-{index + 1}", "AI worker")
        provider_labels = {"openrouter": "OpenRouter", "llamacpp": "llama.cpp", "ollama": "Ollama"}
        for provider, default in (("openrouter", 4), ("llamacpp", 0), ("ollama", 1)):
            existing = len([wid for wid in self.provider_worker_cards if wid.startswith(f"{provider}-")])
            for index in range(existing, counts.get(provider, default)):
                worker_id = f"{provider}-{index + 1}"
                self.provider_worker_states.setdefault(worker_id, {"state": "idle"})
                self._ensure_worker_card("provider", worker_id, provider_labels[provider])
        self._refresh_worker_summaries()

    def _refresh_worker_summaries(self):
        def counts(cards):
            running = sum(1 for c in cards.values() if c.get("state") == "running")
            failed = sum(1 for c in cards.values() if c.get("state") == "failed")
            return len(cards), running, failed

        app_total, app_running, app_failed = counts(getattr(self, "app_worker_cards", {}))
        prov_total, prov_running, prov_failed = counts(getattr(self, "provider_worker_states", {}))
        app_text = f"App AI workers: {app_running}/{app_total} running"
        provider_text = self._provider_summary_text or f"Provider workers: {prov_running}/{prov_total} running"
        if app_failed or prov_failed:
            app_text += f", {app_failed} failed"
            provider_text += f", {prov_failed} failed"
        self.providers_page.set_worker_summaries(app_text, provider_text)

    # ------------------------------------------------------------------
    # Answer feed (structured JSONL events)
    # ------------------------------------------------------------------
    def _on_answer_event(self, event):
        self.activity.add_answer_row(event)

    def _on_feed_run_start(self, event):
        title = str(event.get("form_title", "Form"))
        total = int(event.get("total", 0) or 0)
        self.activity.add_info_row(
            "▶", f"Grading “{title}”", f"{total} answers to evaluate", tone="neutral"
        )

    def _on_feed_run_complete(self, event):
        self.activity.add_info_row(
            "✓",
            "Grading finished",
            f"Accepted {event.get('accepted', 0)} · Review {event.get('review', 0)} · "
            f"Rejected {event.get('rejected', 0)} · {event.get('elapsed', '')}",
            tone="good",
        )

    def _on_feed_form_skipped(self, event):
        self.activity.add_info_row(
            "!",
            f"Partial form: {event.get('form_title', 'Form')}",
            str(event.get("message") or event.get("reason") or "Form skipped."),
            tone="warn",
        )

    # ------------------------------------------------------------------
    # llama.cpp lifecycle
    # ------------------------------------------------------------------
    def _stop_llamacpp_server_if_enabled(self, config_key, reason):
        if not self._config_flag(config_key, False):
            return
        stopped = self._stop_llamacpp_server_processes()
        if stopped > 0:
            self.append_debug(
                f"<font color='gray'>[LLAMACPP] Stopped {stopped} llama-server process(es) {reason} to release RAM.</font>"
            )
        else:
            self.append_debug(f"<font color='gray'>[LLAMACPP] No llama-server process found {reason}.</font>")

    def _stop_llamacpp_server_processes(self):
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
                    capture_output=True, text=True, check=False, timeout=10,
                )
                lines = str(result.stdout or "").strip().splitlines()
                count_text = lines[-1] if lines else "0"
                return max(0, int(count_text or "0"))
            result = subprocess.run(
                ["pkill", "-f", "llama-server"],
                capture_output=True, text=True, check=False, timeout=10,
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
        candidates = [configured, shutil.which("llama-server") or "", r"C:\Tools\llama.cpp\llama-server.exe"]
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
        return [
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
                f"<font color='cyan'>[LLAMACPP] Starting llama-server on {host}:{port} "
                f"with {os.path.basename(model_path)}…</font>"
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
            progress = QProgressDialog("Starting llama.cpp server...", "Cancel", 0, timeout_s, self)
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

    # ------------------------------------------------------------------
    # Review counts (answer-key dashboard hook)
    # ------------------------------------------------------------------
    def refresh_review_counts(self, form_id: str = None):
        try:
            from answer_key_manager import load_pending_review_records

            current_url = getattr(self, "current_form_url", None)
            current_fid = self.extract_form_id(current_url) if current_url else None
            fid = form_id or current_fid
            if not fid:
                return
            pending = load_pending_review_records(fid) or {}
            review_count = sum(len(v) for v in pending.values())
            if current_fid == fid:
                self.dashboard.review_card.set_value(int(review_count))
            item = self._find_form_item_by_id(fid)
            if item:
                meta = item.data(Qt.UserRole + 1) or {}
                meta["review_questions"] = review_count
                item.setData(Qt.UserRole + 1, meta)
                self._refresh_form_row(item)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def _terminate_project_python_processes(self):
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

    def _shutdown_owned_work(self):
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
        self.tailer.stop()
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
        if self.source_scan_thread and self.source_scan_thread.isRunning():
            self.source_scan_thread.wait(2000)
        if self.drive_scan_thread and self.drive_scan_thread.isRunning():
            self.drive_scan_thread.cancel()
            self.drive_scan_thread.quit()
            if not self.drive_scan_thread.wait(3000):
                self.drive_scan_thread.terminate()
                self.drive_scan_thread.wait(2000)
        # Drain every parked (retired) QThread: destroying or finalizing the
        # process with a live QThread is a native fail-fast death.
        for retired in list(getattr(self, "_retired_search_threads", []) or []):
            try:
                if retired.isRunning():
                    if hasattr(retired, "stop_grading"):
                        retired.stop_grading()
                    if not retired.wait(3000):
                        retired.terminate()
                        retired.wait(2000)
            except Exception:
                pass
        self._retired_search_threads = []
        self._terminate_project_python_processes()
        if self.tray_icon:
            self.tray_icon.hide()

    def exit_app(self):
        self._force_exit = True
        self._shutdown_owned_work()
        self._stop_llamacpp_server_if_enabled("llamacpp_stop_server_on_app_close", "on app close")
        window = self.window()
        window.close()

    # ------------------------------------------------------------------
    # Window events (minimize-to-tray, close)
    # ------------------------------------------------------------------
    def changeEvent(self, event):
        super().changeEvent(event)
        if (
            event.type() == QEvent.WindowStateChange
            and self.isMinimized()
            and self.tray_icon is not None
            and self.tray_icon.isVisible()
        ):
            QTimer.singleShot(0, self.hide_to_tray)

    def hide_to_tray(self):
        if self.isMinimized() and (self.tray_icon is None or not self.tray_icon.isVisible()):
            return
        self.showMinimized()
        self.hide()
        self.tray_icon.showMessage(
            "Google Form Autograder",
            "App minimized to system tray. It will continue running in the background. "
            "Double-click the tray icon to restore.",
            QSystemTrayIcon.Information,
            2500,
        )

    def closeEvent(self, event):
        self._force_exit = True
        self._shutdown_owned_work()
        self._stop_llamacpp_server_if_enabled("llamacpp_stop_server_on_app_close", "on app close")
        event.accept()

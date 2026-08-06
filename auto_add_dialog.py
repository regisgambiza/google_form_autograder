# auto_add_dialog.py - FIXED: Prevent duplicate searches, proper auto-cycle initialization
import json
import os

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QListWidget, QLabel, QDateEdit, QMessageBox, QTextEdit,
    QProgressDialog, QComboBox, QListWidgetItem, QGroupBox, QCheckBox, QTimeEdit,
    QSpinBox, QDoubleSpinBox, QFormLayout, QFrame,
)
from PyQt5.QtCore import Qt, QDate, QTime, QThread, pyqtSignal
from form_searcher import (
    load_predefined_folders,
    save_predefined_folders,
    find_forms_with_submissions_in_range,
    split_identifiers,
)
from datetime import datetime, timedelta, time, timezone

from app_theme import apply_widget_theme

AUTO_RUN_CONFIG_KEY = "auto_run"


def count_identifiers(text):
    return len(split_identifiers(text))


def _load_auto_run_config():
    """Load persisted auto-run settings from config.json."""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get(AUTO_RUN_CONFIG_KEY, {}) or {}
    except Exception:
        return {}


def _save_auto_run_config(settings):
    """Persist auto-run settings into config.json under the auto_run key."""
    try:
        cfg = {}
        if os.path.exists("config.json"):
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg[AUTO_RUN_CONFIG_KEY] = settings
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception:
        pass


class SearchThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, folder_identifiers, from_dt, to_dt):
        super().__init__()
        self.folder_identifiers = folder_identifiers
        self.from_dt = from_dt
        self.to_dt = to_dt

    def run(self):
        # Ensure UTC timezone
        from_dt = self.from_dt
        to_dt = self.to_dt

        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=timezone.utc)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=timezone.utc)

        self.progress.emit(f"Starting search in {len(self.folder_identifiers)} source(s)")

        forms = find_forms_with_submissions_in_range(
            self.folder_identifiers,
            from_dt,
            to_dt,
            progress_callback=lambda msg: self.progress.emit(f"Search: {msg}")
        )

        self.progress.emit(f"Search completed. Found {len(forms)} form(s) with submissions")
        self.finished.emit(forms)


class AutoAddDialog(QDialog):
    def __init__(self, parent=None, mode='manual'):
        super().__init__(parent)
        self.mode = mode
        self.setWindowTitle("Schedule Automatic Runs" if mode == 'auto' else "Add Forms")
        self.resize(620, 560)
        self.setMinimumWidth(560)

        self.notify_on_new = True
        self.auto_spend_budget_usd = 0.0
        self._settings = _load_auto_run_config() if mode == 'auto' else {}

        self._build_ui()
        apply_widget_theme(self)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        title = QLabel("Schedule Automatic Runs" if self.mode == 'auto' else "Add Forms")
        title.setObjectName("Title")
        root.addWidget(title)

        if self.mode != 'auto':
            self._build_manual_sections(root)
        else:
            self._build_auto_sections(root)

        # Action bar
        actions = QHBoxLayout()
        actions.addStretch()
        if self.mode == 'auto':
            cancel_btn = QPushButton("Cancel")
            cancel_btn.setObjectName("Secondary")
            cancel_btn.clicked.connect(self.reject)
            actions.addWidget(cancel_btn)
        self.search_btn = QPushButton(
            "Start Auto Run" if self.mode == 'auto' else "Search and Add Forms"
        )
        self.search_btn.setObjectName("Primary")
        self.search_btn.setIcon(self.style().standardIcon(self.style().SP_DialogApplyButton))
        self.search_btn.clicked.connect(self.search_and_add)
        actions.addWidget(self.search_btn)
        root.addLayout(actions)

        self.load_predefined()
        if self.mode == 'auto':
            self._apply_saved_settings()
            self._refresh_preview()

    # ------------------------------------------------------------------ #
    #  Manual mode                                                       #
    # ------------------------------------------------------------------ #
    def _build_manual_sections(self, root):
        sources = self._build_sources_panel()
        root.addWidget(sources)

        panel = QFrame()
        panel.setObjectName("Panel")
        form = QFormLayout(panel)
        self.from_date = QDateEdit()
        self.from_date.setCalendarPopup(True)
        self.from_date.setDate(QDate.currentDate().addDays(-7))
        self.to_date = QDateEdit()
        self.to_date.setCalendarPopup(True)
        self.to_date.setDate(QDate.currentDate())

        range_row = QHBoxLayout()
        range_row.addWidget(self.from_date)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.to_date)
        form.addRow(QLabel("Submission date range"), range_row)
        root.addWidget(panel)

    # ------------------------------------------------------------------ #
    #  Auto mode                                                         #
    # ------------------------------------------------------------------ #
    def _build_auto_sections(self, root):
        # Sources
        sources = self._build_sources_panel()
        root.addWidget(sources)

        # Schedule
        schedule = QGroupBox("Schedule")
        sform = QFormLayout(schedule)

        # Grade mode determines whether recency applies
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Whole Form", "Recent Only"])
        sform.addRow(QLabel("Grading mode"), self.mode_combo)

        recency_row = QHBoxLayout()
        self.recency_edit = QSpinBox()
        self.recency_edit.setRange(1, 10000)
        self.recency_edit.setValue(1)
        self.recency_unit = QComboBox()
        self.recency_unit.addItems(["hours", "minutes"])
        recency_row.addWidget(self.recency_edit)
        recency_row.addWidget(self.recency_unit)
        self.recency_label = QLabel("Look for submissions in last")
        self.recency_label.setObjectName("Muted")
        sform.addRow(self.recency_label, recency_row)

        interval_row = QHBoxLayout()
        self.interval_edit = QSpinBox()
        self.interval_edit.setRange(1, 1000)
        self.interval_edit.setValue(5)
        self.interval_unit = QComboBox()
        self.interval_unit.addItems(["minutes", "hours"])
        interval_row.addWidget(self.interval_edit)
        interval_row.addWidget(self.interval_unit)
        sform.addRow(QLabel("Check every"), interval_row)

        self.notify_check = QCheckBox("Notify when new submissions are found")
        sform.addRow("", self.notify_check)

        budget_row = QHBoxLayout()
        self.budget_edit = QDoubleSpinBox()
        self.budget_edit.setRange(0, 100000)
        self.budget_edit.setDecimals(2)
        self.budget_edit.setSuffix(" $/run")
        self.budget_edit.setValue(0.0)
        budget_row.addWidget(self.budget_edit)
        sform.addRow(QLabel("OpenRouter spend budget"), budget_row)

        root.addWidget(schedule)

        # Time-of-day schedule
        time_group = QGroupBox("Daily schedule (optional)")
        tlayout = QVBoxLayout(time_group)
        trow = QHBoxLayout()
        self.schedule_time_check = QCheckBox("Run at specific time:")
        trow.addWidget(self.schedule_time_check)
        trow.addWidget(QLabel("Time:"))
        self.schedule_time = QTimeEdit()
        self.schedule_time.setTime(QTime(9, 0))
        trow.addWidget(self.schedule_time)
        tlayout.addLayout(trow)

        days_layout = QHBoxLayout()
        days_layout.addWidget(QLabel("Days:"))
        self.days_checkboxes = []
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for day in day_names:
            cb = QCheckBox(day)
            cb.setChecked(True)
            self.days_checkboxes.append(cb)
            days_layout.addWidget(cb)
        tlayout.addLayout(days_layout)
        root.addWidget(time_group)

        # Live preview
        preview = QLabel("")
        preview.setObjectName("Status")
        preview.setWordWrap(True)
        self.preview_label = preview
        root.addWidget(preview)

        # Reconnect field changes to refresh the preview
        self.mode_combo.currentTextChanged.connect(self._refresh_preview)
        self.recency_edit.valueChanged.connect(self._refresh_preview)
        self.recency_unit.currentTextChanged.connect(self._refresh_preview)
        self.interval_edit.valueChanged.connect(self._refresh_preview)
        self.interval_unit.currentTextChanged.connect(self._refresh_preview)
        self.schedule_time_check.toggled.connect(self._refresh_preview)
        self.schedule_time.timeChanged.connect(self._refresh_preview)
        for cb in self.days_checkboxes:
            cb.toggled.connect(self._refresh_preview)
        self.notify_check.toggled.connect(self._refresh_preview)
        self.budget_edit.valueChanged.connect(self._refresh_preview)

        self._refresh_recent_state()

    def _build_sources_panel(self):
        sources = QGroupBox("Sources")
        slayout = QVBoxLayout(sources)

        self.predefined_list = QListWidget()
        slayout.addWidget(self.predefined_list)

        btn_layout = QHBoxLayout()
        add_predefined_btn = QPushButton("Add to Predefined")
        add_predefined_btn.setObjectName("Secondary")
        add_predefined_btn.clicked.connect(self.add_to_predefined)
        remove_predefined_btn = QPushButton("Remove Selected")
        remove_predefined_btn.setObjectName("Secondary")
        remove_predefined_btn.clicked.connect(self.remove_from_predefined)
        btn_layout.addWidget(add_predefined_btn)
        btn_layout.addWidget(remove_predefined_btn)
        slayout.addLayout(btn_layout)

        self.temp_input = QTextEdit()
        self.temp_input.setPlaceholderText(
            "Paste Google Form URLs or Drive folder URLs, separated by commas or new lines..."
        )
        self.temp_input.setFixedHeight(60)
        slayout.addWidget(self.temp_input)
        return sources

    # ------------------------------------------------------------------ #
    #  Preview / recent-only state                                       #
    # ------------------------------------------------------------------ #
    def _refresh_recent_state(self):
        recent_only = self.mode_combo.currentText() == "Recent Only"
        self.recency_label.setEnabled(recent_only)
        self.recency_edit.setEnabled(recent_only)
        self.recency_unit.setEnabled(recent_only)

    def _refresh_preview(self):
        if not hasattr(self, "preview_label"):
            return
        recent_only = self.mode_combo.currentText() == "Recent Only"
        interval_val = int(self.interval_edit.value() or 0)
        interval_unit = self.interval_unit.currentText()
        interval_seconds = interval_val * 3600 if interval_unit == "hours" else interval_val * 60
        recency_val = int(self.recency_edit.value() or 0)
        recency_unit = self.recency_unit.currentText()
        recency_minutes = recency_val * 60 if recency_unit == "hours" else recency_val

        parts = []
        sources_count = self.predefined_list.count() + count_identifiers(self.temp_input.toPlainText())
        parts.append(f"Watching {sources_count} source(s)")
        parts.append(f"every {self._duration_text(interval_seconds)}")
        if recent_only:
            parts.append(f"scanning last {self._recency_text(recency_minutes)}")
        else:
            parts.append("grading entire forms")

        if self.schedule_time_check.isChecked():
            parts.append("on " + ", ".join(
                name for name, cb in zip(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], self.days_checkboxes)
                if cb.isChecked()
            ) or "no days")
            parts.append(f"at {self.schedule_time.time().toString('HH:mm')}")
        else:
            parts.append(f"next check in {self._duration_text(interval_seconds)}")

        if self.budget_edit.value() > 0:
            parts.append(f"budget ${self.budget_edit.value():.2f}/run")

        text = " · ".join(parts)
        self.preview_label.setText(text)
        self._refresh_recent_state()

    def _duration_text(self, seconds):
        return f"{seconds // 60} min" if seconds < 3600 else f"{seconds / 3600:g} h"

    def _recency_text(self, minutes):
        return f"{minutes} min" if minutes < 60 else f"{minutes / 60:g} h"

    def _apply_saved_settings(self):
        settings = self._settings
        if not settings:
            return
        mode = settings.get("grading_mode", "Whole Form")
        if mode in ("Whole Form", "Recent Only"):
            self.mode_combo.setCurrentText(mode)
        if "recency_value" in settings:
            self.recency_edit.setValue(int(settings["recency_value"]))
        if "recency_unit" in settings and settings["recency_unit"] in ("hours", "minutes"):
            self.recency_unit.setCurrentText(settings["recency_unit"])
        if "interval_value" in settings:
            self.interval_edit.setValue(int(settings["interval_value"]))
        if "interval_unit" in settings and settings["interval_unit"] in ("hours", "minutes"):
            self.interval_unit.setCurrentText(settings["interval_unit"])
        if "notify_on_new" in settings:
            self.notify_check.setChecked(bool(settings["notify_on_new"]))
        if "spend_budget_usd" in settings:
            self.budget_edit.setValue(float(settings["spend_budget_usd"]))
        if settings.get("use_time_schedule"):
            self.schedule_time_check.setChecked(True)
        if "schedule_time" in settings:
            try:
                hour, minute = settings["schedule_time"].split(":")
                self.schedule_time.setTime(QTime(int(hour), int(minute)))
            except Exception:
                pass
        saved_days = settings.get("selected_days")
        if isinstance(saved_days, list) and len(saved_days) == 7:
            for cb, val in zip(self.days_checkboxes, saved_days):
                cb.setChecked(bool(val))
        if "sources" in settings and isinstance(settings["sources"], list):
            self.temp_input.setPlainText("\n".join(settings["sources"]))

    def _collect_settings(self):
        return {
            "grading_mode": self.mode_combo.currentText(),
            "recency_value": int(self.recency_edit.value()),
            "recency_unit": self.recency_unit.currentText(),
            "interval_value": int(self.interval_edit.value()),
            "interval_unit": self.interval_unit.currentText(),
            "notify_on_new": self.notify_check.isChecked(),
            "spend_budget_usd": float(self.budget_edit.value()),
            "use_time_schedule": self.schedule_time_check.isChecked(),
            "schedule_time": self.schedule_time.time().toString("HH:mm"),
            "selected_days": [cb.isChecked() for cb in self.days_checkboxes],
            "sources": split_identifiers(self.temp_input.toPlainText()),
        }

    def load_predefined(self):
        self.predefined_list.clear()
        for folder in load_predefined_folders():
            self.predefined_list.addItem(folder)

    def add_to_predefined(self):
        text = self.temp_input.toPlainText().strip()
        if not text:
            return
        new_folders = split_identifiers(text)
        existing = [
            self.predefined_list.item(i).text()
            for i in range(self.predefined_list.count())
        ]
        folders = existing + [f for f in new_folders if f not in existing]
        save_predefined_folders(folders)
        self.load_predefined()
        self.temp_input.clear()

    def remove_from_predefined(self):
        folders = [
            self.predefined_list.item(i).text()
            for i in range(self.predefined_list.count())
            if not self.predefined_list.item(i).isSelected()
        ]
        save_predefined_folders(folders)
        self.load_predefined()

    def search_and_add(self):
        predefined = [
            self.predefined_list.item(i).text()
            for i in range(self.predefined_list.count())
        ]
        temp = split_identifiers(self.temp_input.toPlainText())
        all_folders = list(set(predefined + temp))

        if not all_folders:
            QMessageBox.warning(self, "No Sources", "Add at least one folder or form URL.")
            return

        if self.mode == 'auto':
            recency_value = int(self.recency_edit.value() or 0)
            recency_minutes = (
                recency_value * 60
                if self.recency_unit.currentText() == "hours"
                else recency_value
            )

            interval_value = int(self.interval_edit.value() or 0)
            interval_seconds = (
                interval_value * 3600
                if self.interval_unit.currentText() == "hours"
                else interval_value * 60
            )

            from_dt = datetime.now(timezone.utc) - timedelta(minutes=recency_minutes)
            to_dt = datetime.now(timezone.utc)

            self.recency_minutes = recency_minutes
            self.interval_seconds = interval_seconds
            self.all_folders = all_folders

            # Get scheduling options
            self.use_time_schedule = self.schedule_time_check.isChecked()
            self.schedule_time_val = self.schedule_time.time()
            self.selected_days = [cb.isChecked() for cb in self.days_checkboxes]

            # New options: grading mode, notifications, budget
            self.grading_mode = self.mode_combo.currentText()
            self.notify_on_new = self.notify_check.isChecked()
            self.auto_spend_budget_usd = float(self.budget_edit.value())

            # Persist settings so they are restored next time
            _save_auto_run_config(self._collect_settings())
        else:
            py_from_date = self.from_date.date().toPyDate()
            py_to_date = self.to_date.date().toPyDate()

            from_dt = datetime.combine(
                py_from_date, time.min, tzinfo=timezone.utc
            )
            to_dt = datetime.combine(
                py_to_date, time.max, tzinfo=timezone.utc
            )

        self.progress_dialog = QProgressDialog(
            "Initializing search...", "Cancel", 0, 0, self
        )
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.show()

        self.search_thread = SearchThread(all_folders, from_dt, to_dt)
        self.search_thread.progress.connect(self.progress_dialog.setLabelText)
        self.search_thread.finished.connect(self.on_search_finished)
        self.search_thread.start()

    def on_search_finished(self, forms):
        self.progress_dialog.close()

        if not forms:
            if self.mode != 'auto':
                QMessageBox.information(
                    self, "No Forms",
                    "No forms found with submissions in the date range."
                )
                return

        parent = self.parent()
        added = False

        for form in forms:
            url = form['url']
            title = form['title']
            last = form.get('last_submission')
            last_str = last.strftime("%Y-%m-%d %H:%M:%S") if last else "None"
            text = f"{title} (Last submission: {last_str}) - {url}"

            if url not in parent.forms_data:
                if hasattr(parent, "_add_form_to_queue"):
                    parent._add_form_to_queue(
                        url,
                        title,
                        source="Auto Find",
                        last_submission=last_str,
                    )
                    added = True
                    continue
                parent.forms_data[url] = title
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, url)
                from PyQt5.QtGui import QColor
                item.setForeground(QColor("#0d6efd"))
                parent.form_list.addItem(item)
                added = True

        parent.save_forms()
        if hasattr(parent, "_refresh_queue_positions"):
            parent._refresh_queue_positions()

        if self.mode == 'auto':
            # Store settings in parent
            parent.recency_minutes = self.recency_minutes
            parent.interval_seconds = self.interval_seconds
            parent.folders = self.all_folders

            # Store scheduling options in parent
            parent.use_time_schedule = getattr(self, 'use_time_schedule', False)
            parent.schedule_time_val = getattr(self, 'schedule_time_val', None)
            parent.selected_days = getattr(self, 'selected_days', [True]*7)

            # Store new auto-run options
            parent.grading_mode = getattr(self, 'grading_mode', parent.grading_mode)
            parent.auto_notify_on_new = getattr(self, 'notify_on_new', True)
            parent.auto_spend_budget_usd = getattr(self, 'auto_spend_budget_usd', 0.0)
            if hasattr(parent, "update_config"):
                parent.update_config(
                    "max_openrouter_spend_usd_per_run",
                    float(getattr(self, 'auto_spend_budget_usd', 0.0)),
                )

            # Start auto mode
            parent.start_auto_mode()

            # If forms were added, start grading immediately
            if added:
                parent.run_grader()
                # After grading completes, on_grading_finished will schedule the next cycle
            else:
                # No forms found, schedule first cycle anyway
                parent.schedule_next_cycle()

        self.accept()

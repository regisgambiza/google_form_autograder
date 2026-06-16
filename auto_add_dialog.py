# auto_add_dialog.py - FIXED: Prevent duplicate searches, proper auto-cycle initialization
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QListWidget, QLabel, QDateEdit, QMessageBox, QTextEdit,
    QProgressDialog, QComboBox, QListWidgetItem, QGroupBox, QCheckBox, QTimeEdit
)
from PyQt5.QtCore import Qt, QDate, QTime, QThread, pyqtSignal
from form_searcher import (
    load_predefined_folders,
    save_predefined_folders,
    find_forms_with_submissions_in_range,
    split_identifiers,
)
from datetime import datetime, timedelta, time, timezone


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
        self.setWindowTitle("Auto Add Forms")
        self.setGeometry(200, 200, 600, 400)

        layout = QVBoxLayout(self)

        # Predefined folders/forms
        predefined_layout = QVBoxLayout()
        predefined_label = QLabel("Predefined Folders / Forms:")
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

        # Temporary folders/forms
        self.temp_input = QTextEdit()
        self.temp_input.setPlaceholderText(
            "Paste Google Form URLs or Drive folder URLs, separated by commas or new lines..."
        )
        self.temp_input.setFixedHeight(72)
        layout.addWidget(self.temp_input)

        if self.mode != 'auto':
            # Manual date range
            date_layout = QHBoxLayout()

            from_label = QLabel("From:")
            self.from_date = QDateEdit()
            self.from_date.setCalendarPopup(True)
            self.from_date.setDate(QDate.currentDate().addDays(-7))

            to_label = QLabel("To:")
            self.to_date = QDateEdit()
            self.to_date.setCalendarPopup(True)
            self.to_date.setDate(QDate.currentDate())

            date_layout.addWidget(from_label)
            date_layout.addWidget(self.from_date)
            date_layout.addWidget(to_label)
            date_layout.addWidget(self.to_date)

            layout.addLayout(date_layout)
        else:
            # Auto mode recency
            recency_layout = QHBoxLayout()
            recency_layout.addWidget(QLabel("Look for submissions in last:"))
            self.recency_edit = QLineEdit("1")
            self.recency_unit = QComboBox()
            self.recency_unit.addItems(["hours", "minutes"])
            recency_layout.addWidget(self.recency_edit)
            recency_layout.addWidget(self.recency_unit)
            layout.addLayout(recency_layout)

            # Auto mode interval
            interval_layout = QHBoxLayout()
            interval_layout.addWidget(QLabel("Check every:"))
            self.interval_edit = QLineEdit("5")
            self.interval_unit = QComboBox()
            self.interval_unit.addItems(["minutes", "hours"])
            interval_layout.addWidget(self.interval_edit)
            interval_layout.addWidget(self.interval_unit)
            layout.addLayout(interval_layout)

            # Scheduling options
            schedule_layout = QVBoxLayout()
            schedule_label = QLabel("Schedule Options:")
            schedule_layout.addWidget(schedule_label)

            # Time of day scheduling
            time_layout = QHBoxLayout()
            self.schedule_time_check = QCheckBox("Run at specific time(s):")
            self.schedule_time_check.setChecked(False)
            time_layout.addWidget(self.schedule_time_check)

            time_layout.addWidget(QLabel("Time:"))
            self.schedule_time = QTimeEdit()
            self.schedule_time.setTime(QTime(9, 0))  # Default 9:00 AM
            time_layout.addWidget(self.schedule_time)
            schedule_layout.addLayout(time_layout)

            # Days of week scheduling
            days_layout = QHBoxLayout()
            days_layout.addWidget(QLabel("Days:"))
            self.days_checkboxes = []
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            for day in day_names:
                cb = QCheckBox(day)
                cb.setChecked(True)  # Default to all days
                self.days_checkboxes.append(cb)
                days_layout.addWidget(cb)
            schedule_layout.addLayout(days_layout)

            layout.addLayout(schedule_layout)

        self.search_btn = QPushButton(
            "Start Auto Run" if self.mode == 'auto' else "Search and Add Forms"
        )
        self.search_btn.clicked.connect(self.search_and_add)
        layout.addWidget(self.search_btn)

        self.load_predefined()

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
            recency_value = int(self.recency_edit.text() or 0)
            recency_minutes = (
                recency_value * 60
                if self.recency_unit.currentText() == "hours"
                else recency_value
            )

            interval_value = int(self.interval_edit.text() or 0)
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
            text = f"{title} (Last submission: {last_str}) — {url}"

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
                item = QListWidgetItem(f"⏳ {text}")
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

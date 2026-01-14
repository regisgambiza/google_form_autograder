from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QListWidget, QLabel, QDateEdit, QMessageBox,
    QProgressDialog, QComboBox, QListWidgetItem
)
from PyQt5.QtCore import Qt, QDate, QThread, pyqtSignal
from form_searcher import (
    load_predefined_folders,
    save_predefined_folders,
    find_forms_with_submissions_in_range
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
        # 🔒 HARDEN DATETIMES — GUARANTEE UTC
        from_dt = self.from_dt
        to_dt = self.to_dt

        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=timezone.utc)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=timezone.utc)

        self.progress.emit(f"Starting search in {len(self.folder_identifiers)} folder(s)")
        
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

        # Temporary folders
        self.temp_input = QLineEdit()
        self.temp_input.setPlaceholderText(
            "Enter folder names/IDs/URLs separated by commas..."
        )
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
        text = self.temp_input.text().strip()
        if not text:
            return
        new_folders = [f.strip() for f in text.split(',') if f.strip()]
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
        temp = [f.strip() for f in self.temp_input.text().split(',') if f.strip()]
        all_folders = list(set(predefined + temp))

        if not all_folders:
            QMessageBox.warning(self, "No Folders", "Add at least one folder.")
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
        else:
            py_from_date = self.from_date.date().toPyDate()
            py_to_date = self.to_date.date().toPyDate()

            # ✅ FIXED: Explicit UTC timezone
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
            # auto mode: continue silently

        parent = self.parent()
        added = False

        for form in forms:
            url = form['url']
            title = form['title']
            last = form.get('last_submission')
            last_str = last.strftime("%Y-%m-%d %H:%M:%S") if last else "None"
            text = f"{title} (Last submission: {last_str}) — {url}"

            if url not in parent.forms_data:
                parent.forms_data[url] = title
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, url)
                parent.form_list.addItem(item)
                added = True

        parent.save_forms()

        if self.mode == 'auto':
            parent.recency_minutes = self.recency_minutes
            parent.interval_seconds = self.interval_seconds
            parent.folders = self.all_folders
            parent.start_auto_mode()
            
            # ✅ FIX: Schedule the first auto_cycle after initial search
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(self.interval_seconds * 1000, parent.auto_cycle)
            
            if added:
                parent.run_grader()

        self.accept()
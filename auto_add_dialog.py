# auto_add_dialog.py (Modified)
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QListWidget, QLabel, QDateEdit, QMessageBox, QProgressDialog, QComboBox, QListWidgetItem)
from PyQt5.QtCore import Qt, QDate, QThread, pyqtSignal
from form_searcher import load_predefined_folders, save_predefined_folders, find_forms_with_submissions_in_range
from datetime import datetime, timedelta, time  # Updated import: added 'time'

class SearchThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, folder_identifiers, from_dt, to_dt):
        super().__init__()
        self.folder_identifiers = folder_identifiers
        self.from_dt = from_dt
        self.to_dt = to_dt

    def run(self):
        forms = find_forms_with_submissions_in_range(
            self.folder_identifiers, 
            self.from_dt, 
            self.to_dt, 
            progress_callback=self.progress.emit
        )
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

        # Temporary folders input
        self.temp_input = QLineEdit()
        self.temp_input.setPlaceholderText("Enter folder names/IDs/URLs separated by commas...")
        layout.addWidget(self.temp_input)

        if self.mode != 'auto':
            # Date range for manual
            self.date_layout = QHBoxLayout()
            from_label = QLabel("From:")
            self.from_date = QDateEdit()
            self.from_date.setDate(QDate.currentDate().addDays(-7))
            to_label = QLabel("To:")
            self.to_date = QDateEdit()
            self.to_date.setDate(QDate.currentDate())
            self.date_layout.addWidget(from_label)
            self.date_layout.addWidget(self.from_date)
            self.date_layout.addWidget(to_label)
            self.date_layout.addWidget(self.to_date)
            layout.addLayout(self.date_layout)
        else:
            # Recency for auto
            recency_layout = QHBoxLayout()
            recency_label = QLabel("Look for submissions in last:")
            recency_layout.addWidget(recency_label)
            self.recency_edit = QLineEdit("1")
            recency_layout.addWidget(self.recency_edit)
            self.recency_unit = QComboBox()
            self.recency_unit.addItems(["hours", "minutes"])
            recency_layout.addWidget(self.recency_unit)
            layout.addLayout(recency_layout)

            # Interval for auto
            interval_layout = QHBoxLayout()
            interval_label = QLabel("Check every:")
            interval_layout.addWidget(interval_label)
            self.interval_edit = QLineEdit("5")
            interval_layout.addWidget(self.interval_edit)
            self.interval_unit = QComboBox()
            self.interval_unit.addItems(["minutes", "hours"])
            interval_layout.addWidget(self.interval_unit)
            layout.addLayout(interval_layout)

        # Search button
        self.search_btn = QPushButton("Search and Add Forms")
        if self.mode == 'auto':
            self.search_btn.setText("Start Auto Run")
        self.search_btn.clicked.connect(self.search_and_add)
        layout.addWidget(self.search_btn)

        self.load_predefined()

    def load_predefined(self):
        self.predefined_list.clear()
        folders = load_predefined_folders()
        for folder in folders:
            self.predefined_list.addItem(folder)

    def add_to_predefined(self):
        text = self.temp_input.text().strip()
        if not text:
            return
        new_folders = [f.strip() for f in text.split(',') if f.strip()]
        existing = [self.predefined_list.item(i).text() for i in range(self.predefined_list.count())]
        folders = existing + [f for f in new_folders if f not in existing]
        save_predefined_folders(folders)
        self.load_predefined()
        self.temp_input.clear()

    def remove_from_predefined(self):
        selected = self.predefined_list.selectedItems()
        if not selected:
            return
        folders = [self.predefined_list.item(i).text() for i in range(self.predefined_list.count()) if not self.predefined_list.item(i).isSelected()]
        save_predefined_folders(folders)
        self.load_predefined()

    def search_and_add(self):
        # Get all folders: predefined + temporary
        predefined = [self.predefined_list.item(i).text() for i in range(self.predefined_list.count())]
        temp_text = self.temp_input.text().strip()
        temp = [f.strip() for f in temp_text.split(',') if f.strip()]
        all_folders = list(set(predefined + temp))

        if not all_folders:
            QMessageBox.warning(self, "No Folders", "Add at least one folder.")
            return

        if self.mode == 'auto':
            recency_number = int(self.recency_edit.text().strip() or '0')
            recency_unit = self.recency_unit.currentText()
            recency_minutes = recency_number * 60 if recency_unit == 'hours' else recency_number

            interval_number = int(self.interval_edit.text().strip() or '0')
            interval_unit = self.interval_unit.currentText()
            interval_seconds = interval_number * 3600 if interval_unit == 'hours' else interval_number * 60

            from_dt = datetime.now() - timedelta(minutes=recency_minutes)
            to_dt = datetime.now()
            self.recency_minutes = recency_minutes
            self.interval_seconds = interval_seconds
            self.all_folders = all_folders
        else:
            py_from_date = self.from_date.date().toPyDate()
            py_to_date = self.to_date.date().toPyDate()
            from_dt = datetime.combine(py_from_date, time.min)  # Fixed: use time.min
            to_dt = datetime.combine(py_to_date, time.max)      # Fixed: use time.max

        # Show progress dialog
        self.progress_dialog = QProgressDialog("Initializing search...", "Cancel", 0, 0, self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.show()

        # Start search thread
        self.search_thread = SearchThread(all_folders, from_dt, to_dt)
        self.search_thread.progress.connect(self.progress_dialog.setLabelText)
        self.search_thread.finished.connect(self.on_search_finished)
        self.search_thread.start()

    def on_search_finished(self, forms):
        self.progress_dialog.close()

        if not forms:
            QMessageBox.information(self, "No Forms", "No forms found with submissions in the date range.")
            if self.mode != 'auto':
                return

        # Add to parent's form list
        parent = self.parent()
        added = False
        for form in forms:
            url = form['url']
            title = form['title']
            last_sub = form.get('last_submission')
            last_str = last_sub.strftime("%Y-%m-%d %H:%M:%S") if last_sub else "None"
            display_text = f"{title} (Last submission: {last_str}) — {url}"
            if url not in parent.forms_data:
                parent.forms_data[url] = title
                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, url)
                parent.form_list.addItem(item)
                added = True
        parent.save_forms()

        if self.mode == 'auto':
            parent.recency_minutes = self.recency_minutes
            parent.interval_seconds = self.interval_seconds
            parent.folders = self.all_folders
            parent.start_auto_mode()
            if added:
                parent.run_grader()
        self.accept()
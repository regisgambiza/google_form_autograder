import sys
import json
import subprocess
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QPushButton, QLineEdit, QListWidget,
                            QMessageBox, QProgressDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

class GraderThread(QThread):
    finished = pyqtSignal(bool, str)  # Success flag and error message

    def run(self):
        try:
            subprocess.run([sys.executable, "main.py"], check=True)
            self.finished.emit(True, "")
        except subprocess.CalledProcessError as e:
            self.finished.emit(False, str(e))

class FormManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Form Manager")
        self.setGeometry(100, 100, 600, 400)
        
        # Initialize thread
        self.grader_thread = None
        self.progress_dialog = None
        
        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # URL input
        input_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter Google Form URL...")
        add_button = QPushButton("Add Form")
        add_button.clicked.connect(self.add_form)
        input_layout.addWidget(self.url_input)
        input_layout.addWidget(add_button)
        layout.addLayout(input_layout)
        
        # Form list
        self.form_list = QListWidget()
        self.load_forms()
        layout.addWidget(self.form_list)
        
        # Control buttons
        button_layout = QHBoxLayout()
        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self.remove_form)
        self.run_button = QPushButton("Run Grader")
        self.run_button.clicked.connect(self.run_grader)
        button_layout.addWidget(remove_button)
        button_layout.addWidget(self.run_button)
        layout.addLayout(button_layout)

    def load_forms(self):
        try:
            with open("forms_to_grade.json", "r") as f:
                data = json.load(f)
                forms = data.get("forms", [])
                self.form_list.clear()
                self.form_list.addItems(forms)
        except FileNotFoundError:
            with open("forms_to_grade.json", "w") as f:
                json.dump({"forms": []}, f)
        except json.JSONDecodeError:
            QMessageBox.critical(self, "Error", "Invalid JSON file format")

    def save_forms(self):
        forms = [self.form_list.item(i).text() 
                for i in range(self.form_list.count())]
        with open("forms_to_grade.json", "w") as f:
            json.dump({"forms": forms}, f, indent=2)

    def add_form(self):
        url = self.url_input.text().strip()
        if not url:
            return
            
        if not (url.startswith("https://docs.google.com/forms/") and 
                ("/d/" in url or "/d/e/" in url)):
            QMessageBox.warning(self, "Invalid URL", 
                              "Please enter a valid Google Form URL")
            return
            
        self.form_list.addItem(url)
        self.url_input.clear()
        self.save_forms()

    def remove_form(self):
        current = self.form_list.currentRow()
        if current >= 0:
            self.form_list.takeItem(current)
            self.save_forms()

    def run_grader(self):
        if self.grader_thread is not None and self.grader_thread.isRunning():
            return

        self.run_button.setEnabled(False)
        
        # Create and show progress dialog
        self.progress_dialog = QProgressDialog("Grading in progress...", "Cancel", 0, 0, self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setWindowTitle("Processing")
        self.progress_dialog.setCancelButton(None)  # Remove cancel button
        self.progress_dialog.show()

        # Create and start thread
        self.grader_thread = GraderThread()
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.start()

    def on_grading_finished(self, success, error_msg):
        self.progress_dialog.close()
        self.run_button.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "Success", "Grading completed successfully!")
        else:
            QMessageBox.critical(self, "Error", f"Grading failed: {error_msg}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FormManager()
    window.show()
    sys.exit(app.exec_())
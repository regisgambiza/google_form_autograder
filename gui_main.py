import sys
import os
import json
import subprocess
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QPushButton, QLineEdit, QListWidget,
                            QMessageBox, QProgressBar, QTextEdit, QLabel)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

class GraderThread(QThread):
    finished = pyqtSignal(bool, str)  # Success flag and error message
    progress = pyqtSignal(int, int)  # Current form number and total forms
    debug_message = pyqtSignal(str)  # Debug message signal
    current_form = pyqtSignal(str)  # URL or identifier of the form currently processing
    finished_form = pyqtSignal(str)  # Identifier of a finished form

    def run(self):
        try:
            # Set environment variable for UTF-8 output
            my_env = os.environ.copy()
            my_env["PYTHONIOENCODING"] = "utf-8"
            
            process = subprocess.Popen(
                [sys.executable, "main.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding='utf-8',
                env=my_env
            )
            
            # Read output line by line
            for line in iter(process.stdout.readline, ''):
                if not line:
                    continue
                ls = line.strip()
                self.debug_message.emit(ls)

                # Progress lines (format: "Progress: current/total")
                if ls.startswith("Progress:"):
                    try:
                        current, total = map(int, ls.split(":")[1].strip().split("/"))
                        self.progress.emit(current, total)
                    except ValueError:
                        pass

                # Detect which form is being processed from the logger output
                # Expected format from main.py via logger: "... Processing form ID: <id> from URL: <url>"
                if "Processing form ID:" in ls and "from URL:" in ls:
                    try:
                        url = ls.split("from URL:", 1)[1].strip()
                        self.current_form.emit(url)
                    except Exception:
                        pass

                # Detect finished form message
                # Expected: "... Finished processing form <form_id> successfully."
                if "Finished processing form" in ls:
                    try:
                        remainder = ls.split("Finished processing form", 1)[1].strip()
                        form_id = remainder.split()[0]
                        self.finished_form.emit(form_id)
                    except Exception:
                        pass
            
            # Wait for process to complete
            process.wait()
            
            if process.returncode == 0:
                self.finished.emit(True, "")
            else:
                error = process.stderr.read()
                self.finished.emit(False, error)
                
        except Exception as e:
            self.finished.emit(False, str(e))

class FormManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Form Manager")
        self.setGeometry(100, 100, 600, 400)
        
        # Initialize thread
        self.grader_thread = None
        
        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Progress section
        progress_layout = QHBoxLayout()
        self.progress_label = QLabel("Progress: 0%")
        self.progress_bar = QProgressBar()
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        layout.addLayout(progress_layout)

        # Status labels: currently processing, finished, in queue
        status_layout = QHBoxLayout()
        self.current_label = QLabel("Currently processing: -")
        self.finished_label = QLabel("Finished: 0")
        self.in_queue_label = QLabel("In queue: 0")
        status_layout.addWidget(self.current_label)
        status_layout.addWidget(self.finished_label)
        status_layout.addWidget(self.in_queue_label)
        layout.addLayout(status_layout)

        # Debug output
        self.debug_output = QTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setMaximumHeight(150)
        layout.addWidget(self.debug_output)

        # Finished forms list (small preview)
        self.finished_list = QListWidget()
        self.finished_list.setMaximumHeight(100)
        layout.addWidget(self.finished_list)
        
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
        
        # Internal state for finished forms
        self.finished_forms = []

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
        self.debug_output.clear()
        self.progress_bar.setValue(0)
        self.progress_label.setText("Progress: 0%")

        # Create and start thread
        self.grader_thread = GraderThread()
        self.grader_thread.finished.connect(self.on_grading_finished)
        self.grader_thread.progress.connect(self.update_progress)
        self.grader_thread.debug_message.connect(self.update_debug)
        self.grader_thread.current_form.connect(self.update_current_form)
        self.grader_thread.finished_form.connect(self.update_finished_form)
        self.grader_thread.start()

    def update_progress(self, current, total):
        percentage = int((current / total) * 100)
        self.progress_bar.setValue(percentage)
        self.progress_label.setText(f"Progress: {percentage}% ({current}/{total} forms)")
        # Update in-queue count (forms remaining after current)
        in_queue = max(0, total - current)
        self.in_queue_label.setText(f"In queue: {in_queue}")
        # Update finished count label
        self.finished_label.setText(f"Finished: {len(self.finished_forms)}")

    def update_debug(self, message):
        self.debug_output.append(message)
        # Scroll to bottom
        self.debug_output.verticalScrollBar().setValue(
            self.debug_output.verticalScrollBar().maximum()
        )

    def on_grading_finished(self, success, error_msg):
        self.run_button.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "Success", "Grading completed successfully!")
        else:
            QMessageBox.critical(self, "Error", f"Grading failed: {error_msg}")
            self.debug_output.append(f"Error: {error_msg}")

    def update_current_form(self, url):
        # Display the URL (or identifier) of the currently processing form
        self.current_label.setText(f"Currently processing: {url}")

    def update_finished_form(self, form_id):
        # Append to finished list and update labels
        self.finished_forms.append(form_id)
        self.finished_list.addItem(str(form_id))
        self.finished_label.setText(f"Finished: {len(self.finished_forms)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FormManager()
    window.show()
    sys.exit(app.exec_())
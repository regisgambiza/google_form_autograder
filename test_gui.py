import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QCheckBox, QLabel, QProgressBar,
    QTextEdit, QListWidget, QListWidgetItem, QSplitter
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette, QFont


class MockFormManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Google Form Autograder")
        self.setGeometry(100, 100, 1250, 820)

        # ===== Modern stylesheet =====
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

            QProgressBar {
                height: 24px;
                border-radius: 6px;
                text-align: center;
            }

            QProgressBar::chunk {
                background-color: #28a745;
                border-radius: 6px;
            }

            QSplitter::handle {
                background-color: #d0d0d0;
            }
        """)

        # ===== Central widget =====
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(12)

        # ===== TOP STATUS =====
        top_layout = QVBoxLayout()

        progress_row = QHBoxLayout()
        overall_label = QLabel("Overall Progress")
        overall_label.setObjectName("Header")
        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setValue(25)
        progress_row.addWidget(overall_label)
        progress_row.addWidget(self.overall_progress_bar, 1)
        top_layout.addLayout(progress_row)

        status_row = QHBoxLayout()
        self.current_label = QLabel("🟡 Processing: None")
        self.finished_label = QLabel("✅ Finished: 0")
        self.in_queue_label = QLabel("⏳ In Queue: 5")

        status_row.addWidget(self.current_label)
        status_row.addStretch()
        status_row.addWidget(self.finished_label)
        status_row.addWidget(self.in_queue_label)
        top_layout.addLayout(status_row)

        main_layout.addLayout(top_layout)

        # ===== CENTER SPLITTER =====
        splitter = QSplitter(Qt.Horizontal)

        # ---- LEFT: FORM LIST ----
        left_layout = QVBoxLayout()
        left_label = QLabel("Forms to Grade")
        left_label.setObjectName("Header")
        left_layout.addWidget(left_label)

        self.form_list = QListWidget()

        forms = [
            ("⏳ Form 1 — https://forms.google.com/form1", QColor("#0d6efd")),
            ("❌ Form 2 — https://forms.google.com/form2", QColor("#dc3545")),
            ("✅ Form 3 — https://forms.google.com/form3", QColor("#198754")),
        ]

        for text, color in forms:
            item = QListWidgetItem(text)
            item.setForeground(color)
            self.form_list.addItem(item)

        left_layout.addWidget(self.form_list)

        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        splitter.addWidget(left_widget)

        # ---- RIGHT: DEBUG OUTPUT ----
        right_layout = QVBoxLayout()
        right_label = QLabel("Debug Output")
        right_label.setObjectName("Header")
        right_layout.addWidget(right_label)

        self.debug_output = QTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setFont(QFont("Consolas", 10))
        self.debug_output.setStyleSheet(
            "background-color:#1e1e1e; color:#dcdcdc;"
        )

        self.debug_output.append("[INFO] Starting process...")
        self.debug_output.append("[SUCCESS] Form 1 graded.")
        self.debug_output.append("[ERROR] Issue with Form 2.")

        right_layout.addWidget(self.debug_output)

        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        splitter.addWidget(right_widget)

        splitter.setSizes([600, 450])
        main_layout.addWidget(splitter, 1)

        # ===== BOTTOM CONTROLS =====
        bottom_layout = QHBoxLayout()

        # ---- ACTION BUTTONS ----
        actions_layout = QHBoxLayout()

        auto_find_btn = QPushButton("🔍 Auto Find")
        auto_run_btn = QPushButton("▶ Auto Run")
        run_btn = QPushButton("🚀 Run Now")

        remove_btn = QPushButton("❌ Remove")
        remove_btn.setObjectName("Secondary")

        clear_btn = QPushButton("🗑 Clear All")
        clear_btn.setObjectName("Secondary")

        stop_btn = QPushButton("⏹ Stop")
        stop_btn.setObjectName("Danger")

        actions_layout.addWidget(auto_find_btn)
        actions_layout.addWidget(auto_run_btn)
        actions_layout.addWidget(run_btn)
        actions_layout.addWidget(remove_btn)
        actions_layout.addWidget(clear_btn)
        actions_layout.addWidget(stop_btn)

        bottom_layout.addLayout(actions_layout)

        # ---- SETTINGS ----
        settings_layout = QHBoxLayout()
        settings_layout.addStretch()

        settings_layout.addWidget(QLabel("Evaluator:"))
        evaluator_combo = QComboBox()
        evaluator_combo.addItems([
            "ai_evaluator (Basic)",
            "ai_evaluator_2 (Advanced)"
        ])
        settings_layout.addWidget(evaluator_combo)

        settings_layout.addWidget(QLabel("Leniency:"))
        leniency_combo = QComboBox()
        leniency_combo.addItems([
            "extreme", "lenient", "balanced", "strict"
        ])
        settings_layout.addWidget(leniency_combo)

        settings_layout.addWidget(QLabel("Model:"))
        model_combo = QComboBox()
        model_combo.addItems([
            "gpt-oss:20b", "llama2:7b", "mistral:7b"
        ])
        settings_layout.addWidget(model_combo)

        report_checkbox = QCheckBox("Generate Report")
        report_checkbox.setChecked(True)
        settings_layout.addWidget(report_checkbox)

        bottom_layout.addLayout(settings_layout)
        main_layout.addLayout(bottom_layout)


if __name__ == "__main__":
    app = QApplication(sys.argv)

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(244, 246, 248))
    palette.setColor(QPalette.WindowText, Qt.black)
    app.setPalette(palette)

    window = MockFormManager()
    window.show()
    sys.exit(app.exec_())

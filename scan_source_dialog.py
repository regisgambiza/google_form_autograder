# scan_source_dialog.py - extracted Scan-Source dialog for the Classic Desktop Utility GUI
import re

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)


def run_scan_source_dialog(owner):
    """Open the Scan Source dialog and dispatch the selected action to the owner."""
    dialog = QDialog(owner)
    dialog.setWindowTitle("Scan Source")
    dialog.resize(620, 260)
    # Center on the owner window (hard-coded screen coords would place the
    # dialog on the primary monitor, far away from the main window).
    parent = owner.window() if owner is not None else None
    if parent is not None:
        parent_frame = parent.frameGeometry()
        dialog.move(
            parent_frame.center().x() - dialog.width() // 2,
            parent_frame.center().y() - dialog.height() // 2,
        )

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

    if dialog.exec() == QDialog.Accepted:
        sources_text = input_field.toPlainText().strip()
        if not sources_text:
            QMessageBox.warning(owner, "Empty Input", "Please enter at least one URL")
            return

        parts = [p.strip() for p in re.split('[,\n\r]+', sources_text) if p.strip()]
        if not parts:
            QMessageBox.warning(owner, "Empty Input", "Please enter at least one URL")
            return

        if action[0] == "grade":
            owner._start_source_scan(parts, "grade_new", mode="all_forms")
        else:
            owner._start_source_scan(parts, "add", mode="all_forms")

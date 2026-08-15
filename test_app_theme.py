import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from app_theme import APP_STYLESHEET, apply_application_theme, apply_widget_theme


APP = QApplication.instance() or QApplication([])


def test_shared_theme_covers_core_widget_families():
    for selector in (
        "QMainWindow", "QDialog", "QPushButton", "QLineEdit", "QListWidget",
        "QTableWidget", "QTabWidget", "QProgressBar", "QMenu", "QScrollBar",
    ):
        assert selector in APP_STYLESHEET


def test_command_buttons_receive_icons_and_emoji_is_removed():
    apply_application_theme(APP)
    widget = QWidget()
    layout = QVBoxLayout(widget)
    labels = [
        "🔍 Auto Find", "▶ Auto Run", "Grade Sources Now", "Answer Keys",
        "Remove", "Clear All", "Stop", "Minimize", "Settings", "Exit",
    ]
    for label in labels:
        layout.addWidget(QPushButton(label))

    apply_widget_theme(widget)

    buttons = widget.findChildren(QPushButton)
    assert all(not button.icon().isNull() for button in buttons)
    assert [button.text() for button in buttons[:2]] == ["Auto Find", "Auto Run"]


def test_secondary_and_danger_roles_are_available():
    assert "QPushButton#Secondary" in APP_STYLESHEET
    assert "QPushButton#Danger" in APP_STYLESHEET

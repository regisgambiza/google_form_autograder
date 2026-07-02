import re

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QApplication, QPushButton, QStyle, QWidget


APP_STYLESHEET = """
QMainWindow, QDialog, QMessageBox {
    background: #f4f6f8;
    color: #1f2937;
}
QWidget {
    color: #1f2937;
    font-size: 13px;
}
QLabel#Header, QLabel#Title {
    color: #1f2937;
    font-size: 18px;
    font-weight: 700;
}
QLabel#Section {
    color: #1f2937;
    font-size: 15px;
    font-weight: 700;
}
QLabel#Status {
    color: #40546a;
    padding: 5px 0;
}
QFrame#AppHeader {
    background: #ffffff;
    border-bottom: 1px solid #cbd6df;
}
QLabel#AppBrand { color: #1d2a36; font-size: 17px; font-weight: 700; }
QLabel#Muted { color: #637485; font-size: 12px; }
QLabel#RunStateDot {
    background: #16845b;
    border-radius: 4px;
}
QFrame#CommandBar {
    background: #f5f8fa;
    border-bottom: 1px solid #cbd6df;
}
QPushButton#IconButton {
    min-width: 34px;
    max-width: 36px;
    min-height: 34px;
    padding: 0;
    background: white;
    color: #405466;
    border: 1px solid #cbd6df;
}
QPushButton#IconButton::menu-indicator { image: none; width: 0; }
QFrame#QueuePane { background: #f1f5f8; border-right: 1px solid #cbd6df; }
QFrame#DetailPane { background: #ffffff; }
QLabel#DetailTitle { color: #1d2a36; font-size: 21px; font-weight: 700; }
QLabel#DetailBadge {
    background: #e6edf3;
    color: #405466;
    border-radius: 4px;
    padding: 6px 9px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#DetailBadge[status="running"] { background: #fff3d8; color: #7b4b00; }
QLabel#DetailBadge[status="done"] { background: #e5f5ed; color: #126341; }
QLabel#DetailBadge[status="failed"] { background: #ffebe8; color: #8f1c13; }
QFrame#Metric { background: white; border-bottom: 1px solid #cbd6df; }
QLabel#MetricValue { color: #1d2a36; font-size: 20px; font-weight: 700; }
QFrame#PipelineRow { background: white; border-bottom: 1px solid #d9e1e7; }
QFrame#TerminalFrame { background: #172028; border-top: 1px solid #0d151b; }
QPushButton#TerminalToggle, QPushButton#TerminalAction {
    background: transparent;
    color: #d7e2e9;
    border: 0;
    min-height: 30px;
    padding: 0 7px;
}
QPushButton#TerminalToggle { font-weight: 700; }
QPushButton#TerminalToggle:hover, QPushButton#TerminalAction:hover { background: #263541; }
QLabel#TerminalMuted, QFrame#TerminalFrame QCheckBox { color: #92a5b2; }
QFrame#TerminalFrame QTabWidget::pane { background: #172028; border: 0; }
QFrame#TerminalFrame QTabBar::tab { background: #202d37; color: #9eb0bc; border-color: #30414e; }
QFrame#TerminalFrame QTabBar::tab:selected { background: #172028; color: white; }
QFrame#Panel, QGroupBox {
    background: #ffffff;
    border: 1px solid #d7e0ea;
    border-radius: 6px;
}
QGroupBox {
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}
QPushButton {
    min-height: 34px;
    padding: 0 13px;
    background: #1769aa;
    color: #ffffff;
    border: 1px solid #1769aa;
    border-radius: 5px;
    font-weight: 600;
}
QPushButton:hover { background: #12578d; border-color: #12578d; }
QPushButton:pressed { background: #0d4775; }
QPushButton:disabled { background: #d9e1e8; border-color: #d9e1e8; color: #7b8996; }
QPushButton#Primary { background: #1769aa; color: white; border-color: #1769aa; }
QPushButton#Secondary {
    background: #ffffff;
    color: #263747;
    border: 1px solid #b8c5d1;
}
QPushButton#Secondary:hover { background: #edf4fa; border-color: #8fa6ba; }
QPushButton#Danger { background: #b42318; color: white; border-color: #b42318; }
QPushButton#Danger:hover { background: #8f1c13; border-color: #8f1c13; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QDateEdit, QTimeEdit, QListWidget, QTableWidget, QTreeWidget {
    background: #ffffff;
    color: #1f2937;
    border: 1px solid #c8d2dc;
    border-radius: 4px;
    padding: 5px;
    selection-background-color: #dcecff;
    selection-color: #15324b;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDateEdit:focus, QTimeEdit:focus, QListWidget:focus,
QTableWidget:focus, QTreeWidget:focus {
    border: 1px solid #1769aa;
}
QComboBox, QSpinBox, QDateEdit, QTimeEdit { min-height: 28px; }
QComboBox::drop-down { border: 0; width: 24px; }
QListWidget::item, QTreeWidget::item { min-height: 30px; padding: 4px; }
QListWidget::item:selected, QTreeWidget::item:selected {
    background: #dcecff;
    color: #15324b;
}
QHeaderView::section {
    background: #e8eef4;
    color: #263747;
    border: 0;
    border-right: 1px solid #c8d2dc;
    border-bottom: 1px solid #c8d2dc;
    padding: 7px;
    font-weight: 700;
}
QTabWidget::pane { background: white; border: 1px solid #c8d2dc; border-radius: 4px; }
QTabBar::tab {
    background: #e8eef4;
    color: #40546a;
    padding: 8px 14px;
    border: 1px solid #c8d2dc;
    border-bottom: 0;
}
QTabBar::tab:selected { background: white; color: #1769aa; font-weight: 700; }
QCheckBox, QRadioButton { spacing: 7px; min-height: 24px; }
QProgressBar {
    background: #e5ebf0;
    border: 0;
    border-radius: 4px;
    min-height: 18px;
    text-align: center;
    color: #263747;
}
QProgressBar::chunk { background: #1f8a5b; border-radius: 4px; }
QMenu { background: white; border: 1px solid #c8d2dc; padding: 5px; }
QMenu::item { padding: 7px 26px 7px 10px; border-radius: 3px; }
QMenu::item:selected { background: #dcecff; color: #15324b; }
QToolTip { background: #263747; color: white; border: 0; padding: 5px; }
QSplitter::handle { background: #d7e0ea; width: 2px; height: 2px; }
QScrollBar:vertical { background: #eef2f6; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #aebdca; min-height: 28px; border-radius: 5px; }
QScrollBar:horizontal { background: #eef2f6; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #aebdca; min-width: 28px; border-radius: 5px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QFrame#FormCard { background: white; border: 1px solid #d7e0ea; border-left: 4px solid #6b7f91; border-radius: 6px; }
QFrame#FormCard[status="queued"] { border-left-color: #1769aa; }
QFrame#FormCard[status="running"] { border-left-color: #cc7a00; background: #fff9e8; }
QFrame#FormCard[status="done"] { border-left-color: #1f8a5b; background: #f2faf6; }
QFrame#FormCard[status="failed"] { border-left-color: #b42318; background: #fff4f2; }
QLabel#FormTitle { font-size: 14px; font-weight: 700; color: #1f2937; }
QLabel#FormMeta { font-size: 11px; color: #5b6775; }
QLabel#FormUrl { font-size: 11px; color: #1769aa; }
QLabel#StatusBadge { color: white; background: #6b7f91; border-radius: 9px; padding: 3px 8px; font-weight: 700; }
QLabel#StatusBadge[status="queued"] { background: #1769aa; }
QLabel#StatusBadge[status="running"] { background: #cc7a00; }
QLabel#StatusBadge[status="done"] { background: #1f8a5b; }
QLabel#StatusBadge[status="failed"] { background: #b42318; }
"""


_ICON_RULES = [
    (("clean", "save", "apply", "grade", "run now"), QStyle.SP_DialogApplyButton),
    (("auto run", "start"), QStyle.SP_MediaPlay),
    (("stop",), QStyle.SP_MediaStop),
    (("search", "find", "review"), QStyle.SP_FileDialogContentsView),
    (("add", "import"), QStyle.SP_FileDialogNewFolder),
    (("answer key", "settings"), QStyle.SP_FileDialogDetailedView),
    (("remove", "clear", "delete"), QStyle.SP_TrashIcon),
    (("undo", "back"), QStyle.SP_ArrowBack),
    (("skip", "next"), QStyle.SP_ArrowForward),
    (("ok", "done", "yes"), QStyle.SP_DialogYesButton),
    (("no",), QStyle.SP_DialogNoButton),
    (("minimize",), QStyle.SP_TitleBarMinButton),
    (("close", "cancel", "exit"), QStyle.SP_DialogCloseButton),
]


def _plain_button_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"^[^\w]+", "", value, flags=re.UNICODE)
    return value.strip()


def apply_button_icon(button: QPushButton) -> None:
    text = button.text() if button.property("preserveText") else _plain_button_text(button.text())
    if text and text != button.text():
        button.setText(text)
    if button.property("noAutoIcon"):
        return
    lowered = text.casefold()
    for terms, icon_id in _ICON_RULES:
        if any(term in lowered for term in terms):
            button.setIcon(button.style().standardIcon(icon_id))
            button.setIconSize(button.iconSize().expandedTo(button.minimumSizeHint() / 5))
            break


def apply_icons(root: QWidget) -> None:
    if isinstance(root, QPushButton):
        apply_button_icon(root)
    for button in root.findChildren(QPushButton):
        apply_button_icon(button)


class _ThemeEventFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Show and isinstance(watched, QWidget):
            apply_icons(watched)
        return False


def apply_widget_theme(widget: QWidget) -> None:
    widget.setStyleSheet(APP_STYLESHEET)
    apply_icons(widget)


def apply_application_theme(app: QApplication) -> None:
    app.setStyleSheet(APP_STYLESHEET)
    theme_filter = _ThemeEventFilter(app)
    app.installEventFilter(theme_filter)
    app._answer_key_theme_filter = theme_filter

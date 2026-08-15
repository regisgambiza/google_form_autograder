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
QLabel#ActivityDot, QLabel#AutoStatusDot {
    border-radius: 4px;
    background: #b6c1cc;
}
QLabel#ActivityDot[state="idle"], QLabel#AutoStatusDot[state="off"] { background: #b6c1cc; }
QLabel#ActivityDot[state="busy"], QLabel#AutoStatusDot[state="searching"] { background: #e0a03c; }
QLabel#ActivityDot[state="grading"], QLabel#AutoStatusDot[state="grading"] { background: #1f7cc4; }
QLabel#ActivityDot[state="waiting"], QLabel#AutoStatusDot[state="active"] { background: #16845b; }
QLabel#ActivityDot[state="error"] { background: #d0342c; }
QLabel#ActivityStatus, QLabel#AutoStatus {
    color: #405466;
    font-size: 11px;
    font-weight: 600;
}
QLabel#ActivityStatus[state="idle"] { color: #637485; }
QLabel#ActivityStatus[state="busy"], QLabel#AutoStatus[state="searching"] { color: #8a5200; }
QLabel#ActivityStatus[state="grading"], QLabel#AutoStatus[state="grading"] { color: #1769aa; }
QLabel#ActivityStatus[state="waiting"], QLabel#AutoStatus[state="active"] { color: #16845b; }
QLabel#ActivityStatus[state="error"] { color: #b42318; }
QFrame#CommandBar {
    background: #f5f8fa;
    border-bottom: 1px solid #cbd6df;
}
QFrame#CommandBar QPushButton#CommandButton {
    min-height: 42px;
    max-height: 42px;
    padding: 0 14px;
}
QFrame#CommandBar QPushButton#CommandButton[variant="secondary"] {
    background: #ffffff;
    color: #263747;
    border: 1px solid #b8c5d1;
}
QFrame#CommandBar QPushButton#CommandButton[variant="secondary"]:hover {
    background: #edf4fa;
    border-color: #8fa6ba;
}
QFrame#CommandBar QPushButton#CommandButton[variant="danger"] {
    background: #b42318;
    color: white;
    border-color: #b42318;
}
QFrame#CommandBar QPushButton#CommandButton[variant="danger"]:hover {
    background: #8f1c13;
    border-color: #8f1c13;
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
QScrollArea#DetailScroll { background: #ffffff; border: 0; }
QScrollArea#DetailScroll > QWidget > QWidget { background: #ffffff; }
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
QFrame#WorkerRow {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid #d9e1e7;
}
QFrame#WorkerRow[status="running"] { border-left: 3px solid #cc7a00; background: #fff9e8; }
QFrame#WorkerRow[status="failed"] { border-left: 3px solid #b42318; background: #fff4f2; }
QFrame#WorkerRow[status="done"] { border-left: 3px solid #1f8a5b; background: #f5fbf8; }
QLabel#WorkerTitle { color: #1d2a36; font-size: 12px; font-weight: 700; }
QLabel#WorkerPrimary { color: #1d2a36; font-size: 12px; font-weight: 600; }
QLabel#WorkerStatus {
    color: #405466;
    background: #eef3f7;
    border-radius: 3px;
    padding: 2px 5px;
    font-size: 10px;
    font-weight: 700;
}
QLabel#WorkerStatus[status="running"] { color: #7b4b00; background: #fff0cc; }
QLabel#WorkerStatus[status="failed"] { color: #8f1c13; background: #ffe1dc; }
QLabel#WorkerStatus[status="done"] { color: #126341; background: #dff3e9; }
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
QListWidget#SourceList {
    background: #ffffff;
    border: 1px solid #d7e0ea;
    border-radius: 8px;
    padding: 4px;
}
QListWidget#SourceList::item {
    margin: 1px;
    border-radius: 6px;
    padding: 6px 8px;
    color: #1a2c3e;
    font-size: 12px;
}
QListWidget#SourceList::item:hover { background: #f0f6fd; }
QListWidget#SourceList::item:selected { background: #dcecff; color: #15324b; }
QListWidget#SourceList::item:alternate { background: #f7fafc; }
QPushButton[danger="true"] { color: #b42318; }
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
QMenu::item:disabled { color: #a3b0bc; }
QToolTip { background: #263747; color: white; border: 0; padding: 5px; }
QSplitter::handle { background: #d7e0ea; width: 2px; height: 2px; }
QScrollBar:vertical { background: #eef2f6; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #aebdca; min-height: 28px; border-radius: 5px; }
QScrollBar:horizontal { background: #eef2f6; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #aebdca; min-width: 28px; border-radius: 5px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QFrame#FormQueueHeader {
    background: #e8eef4;
    border: 1px solid #c8d2dc;
    border-bottom: 0;
}
QLabel#QueueColumnHeader {
    color: #263747;
    font-size: 11px;
    font-weight: 700;
}
QListWidget#FormQueueList {
    background: #ffffff;
    border: 0;
    border-radius: 0;
    padding: 4px;
}
QListWidget#FormQueueList::item { border: 0; margin: 0 0 4px 0; padding: 0; border-radius: 8px; }
QListWidget#FormQueueList::item:selected { background: transparent; }
QFrame#FormCard {
    background: #ffffff;
    border: 1px solid #e4e9ef;
    border-radius: 8px;
    border-left: 3px solid #c9d4df;
}
QFrame#FormCard[rowParity="odd"] { background: #fafbfd; }
QFrame#FormCard[rowParity="odd"]:hover, QFrame#FormCard:hover { background: #f2f7ff; border-color: #cfe0f4; }
QFrame#FormCard[status="queued"] { border-left-color: #1f7cc4; }
QFrame#FormCard[status="running"] { border-left-color: #e0a03c; background: #fffaf0; }
QFrame#FormCard[status="done"] { border-left-color: #1f8a5b; }
QFrame#FormCard[status="failed"] { border-left-color: #d0342c; background: #fef3f2; }
QLabel#FormTitle { font-size: 12px; font-weight: 600; color: #1a2c3e; }
QLabel#FormMeta { font-size: 10px; color: #74859a; }
QLabel#FormUrl { font-size: 10px; color: #637485; }
QLabel#StatusBadge {
    color: #405466;
    background: #eef2f6;
    border: 1px solid #dce3ea;
    border-radius: 11px;
    padding: 2px 6px;
    font-size: 10px;
    font-weight: 600;
}
QLabel#StatusBadge[status="queued"] { color: #1769aa; background: #e7f1fb; border-color: #c9e0f5; }
QLabel#StatusBadge[status="running"] { color: #8a5200; background: #fff3da; border-color: #f5dfae; }
QLabel#StatusBadge[status="done"] { color: #1f8a5b; background: #e5f5ec; border-color: #c3e6d2; }
QLabel#StatusBadge[status="failed"] { color: #b42318; background: #fdebe8; border-color: #f6cdc7; }
QLabel#QueueEta { color: #5a6b7d; font-size: 10px; font-weight: 500; }
QLabel#QueueGlyph { color: #5b8fd6; font-size: 13px; font-weight: 700; }
QProgressBar#QueueProgress {
    background: #eef2f6;
    border: 0;
    border-radius: 4px;
    min-height: 8px;
    max-height: 8px;
    text-align: center;
    color: transparent;
    font-size: 1px;
}
QProgressBar#QueueProgress::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #1f8a5b, stop:1 #2ea875); border-radius: 4px; }
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


DARK_STYLESHEET = """
QMainWindow, QDialog, QMessageBox {
    background: #1e2530;
    color: #d8e0ea;
}
QWidget {
    color: #d8e0ea;
    font-size: 13px;
}
QLabel#Header, QLabel#Title {
    color: #eef3f8;
    font-size: 18px;
    font-weight: 700;
}
QLabel#Section {
    color: #eef3f8;
    font-size: 15px;
    font-weight: 700;
}
QLabel#Status {
    color: #aab6c4;
    padding: 5px 0;
}
QFrame#AppHeader {
    background: #232c3a;
    border-bottom: 1px solid #384454;
}
QLabel#AppBrand { color: #f0f5fa; font-size: 17px; font-weight: 700; }
QLabel#Muted { color: #8b98a8; font-size: 12px; }
QLabel#RunStateDot {
    background: #2fa878;
    border-radius: 4px;
}
QLabel#ActivityDot, QLabel#AutoStatusDot {
    border-radius: 4px;
    background: #46536a;
}
QLabel#ActivityDot[state="idle"], QLabel#AutoStatusDot[state="off"] { background: #46536a; }
QLabel#ActivityDot[state="busy"], QLabel#AutoStatusDot[state="searching"] { background: #e0a03c; }
QLabel#ActivityDot[state="grading"], QLabel#AutoStatusDot[state="grading"] { background: #1f7cc4; }
QLabel#ActivityDot[state="waiting"], QLabel#AutoStatusDot[state="active"] { background: #2fa878; }
QLabel#ActivityDot[state="error"] { background: #e5484d; }
QLabel#ActivityStatus, QLabel#AutoStatus {
    color: #aab6c4;
    font-size: 11px;
    font-weight: 600;
}
QLabel#ActivityStatus[state="idle"] { color: #7d8b9d; }
QLabel#ActivityStatus[state="busy"], QLabel#AutoStatus[state="searching"] { color: #f0c674; }
QLabel#ActivityStatus[state="grading"], QLabel#AutoStatus[state="grading"] { color: #7db4ea; }
QLabel#ActivityStatus[state="waiting"], QLabel#AutoStatus[state="active"] { color: #52c49a; }
QLabel#ActivityStatus[state="error"] { color: #f1767a; }
QFrame#CommandBar {
    background: #1a212c;
    border-bottom: 1px solid #384454;
}
QFrame#CommandBar QPushButton#CommandButton {
    min-height: 42px;
    max-height: 42px;
    padding: 0 14px;
}
QFrame#CommandBar QPushButton#CommandButton[variant="secondary"] {
    background: #2a3444;
    color: #d8e0ea;
    border: 1px solid #47556a;
}
QFrame#CommandBar QPushButton#CommandButton[variant="secondary"]:hover {
    background: #36455a;
    border-color: #5c6d86;
}
QFrame#CommandBar QPushButton#CommandButton[variant="danger"] {
    background: #e5484d;
    color: white;
    border-color: #e5484d;
}
QFrame#CommandBar QPushButton#CommandButton[variant="danger"]:hover {
    background: #c93a3f;
    border-color: #c93a3f;
}
QPushButton#IconButton {
    min-width: 34px;
    max-width: 36px;
    min-height: 34px;
    padding: 0;
    background: #2a3444;
    color: #c2ccd8;
    border: 1px solid #47556a;
}
QPushButton#IconButton::menu-indicator { image: none; width: 0; }
QFrame#QueuePane { background: #1a212c; border-right: 1px solid #384454; }
QScrollArea#DetailScroll { background: #232c3a; border: 0; }
QScrollArea#DetailScroll > QWidget > QWidget { background: #232c3a; }
QFrame#DetailPane { background: #232c3a; }
QLabel#DetailTitle { color: #f0f5fa; font-size: 21px; font-weight: 700; }
QLabel#DetailBadge {
    background: #2e3a4c;
    color: #c2ccd8;
    border-radius: 4px;
    padding: 6px 9px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#DetailBadge[status="running"] { background: #4a3d1d; color: #f0c674; }
QLabel#DetailBadge[status="done"] { background: #1d3f33; color: #7fe0b2; }
QLabel#DetailBadge[status="failed"] { background: #47201f; color: #ff8a85; }
QFrame#Metric { background: #232c3a; border-bottom: 1px solid #384454; }
QLabel#MetricValue { color: #f0f5fa; font-size: 20px; font-weight: 700; }
QFrame#PipelineRow { background: #232c3a; border-bottom: 1px solid #303b4b; }
QFrame#WorkerRow {
    background: #232c3a;
    border: 0;
    border-bottom: 1px solid #303b4b;
}
QFrame#WorkerRow[status="running"] { border-left: 3px solid #e0a03c; background: #2c2a20; }
QFrame#WorkerRow[status="failed"] { border-left: 3px solid #e5484d; background: #33211f; }
QFrame#WorkerRow[status="done"] { border-left: 3px solid #2fa878; background: #1e2f29; }
QLabel#WorkerTitle { color: #f0f5fa; font-size: 12px; font-weight: 700; }
QLabel#WorkerPrimary { color: #f0f5fa; font-size: 12px; font-weight: 600; }
QLabel#WorkerStatus {
    color: #c2ccd8;
    background: #2e3a4c;
    border-radius: 3px;
    padding: 2px 5px;
    font-size: 10px;
    font-weight: 700;
}
QLabel#WorkerStatus[status="running"] { color: #f0c674; background: #3d3118; }
QLabel#WorkerStatus[status="failed"] { color: #ff8a85; background: #3d211f; }
QLabel#WorkerStatus[status="done"] { color: #7fe0b2; background: #1d3f33; }
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
    background: #232c3a;
    border: 1px solid #384454;
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
    background: #1f7cc4;
    color: #ffffff;
    border: 1px solid #1f7cc4;
    border-radius: 5px;
    font-weight: 600;
}
QPushButton:hover { background: #2a8fd6; border-color: #2a8fd6; }
QPushButton:pressed { background: #1a69a8; }
QPushButton:disabled { background: #2e3a4c; border-color: #2e3a4c; color: #7b8996; }
QPushButton#Primary { background: #1f7cc4; color: white; border-color: #1f7cc4; }
QPushButton#Secondary {
    background: #2a3444;
    color: #d8e0ea;
    border: 1px solid #47556a;
}
QPushButton#Secondary:hover { background: #36455a; border-color: #5c6d86; }
QPushButton#Danger { background: #e5484d; color: white; border-color: #e5484d; }
QPushButton#Danger:hover { background: #c93a3f; border-color: #c93a3f; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QDateEdit, QTimeEdit, QListWidget, QTableWidget, QTreeWidget {
    background: #202836;
    color: #d8e0ea;
    border: 1px solid #3a4656;
    border-radius: 4px;
    padding: 5px;
    selection-background-color: #1f7cc4;
    selection-color: #ffffff;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDateEdit:focus, QTimeEdit:focus, QListWidget:focus,
QTableWidget:focus, QTreeWidget:focus {
    border: 1px solid #2a8fd6;
}
QComboBox, QSpinBox, QDateEdit, QTimeEdit { min-height: 28px; }
QComboBox::drop-down { border: 0; width: 24px; }
QListWidget::item, QTreeWidget::item { min-height: 30px; padding: 4px; }
QListWidget::item:selected, QTreeWidget::item:selected {
    background: #1f7cc4;
    color: #ffffff;
}
QListWidget#SourceList {
    background: #202836;
    border: 1px solid #3a4656;
    border-radius: 8px;
    padding: 4px;
}
QListWidget#SourceList::item {
    margin: 1px;
    border-radius: 6px;
    padding: 6px 8px;
    color: #cfe0ef;
    font-size: 12px;
}
QListWidget#SourceList::item:hover { background: #2a3648; }
QListWidget#SourceList::item:selected { background: #1f7cc4; color: #ffffff; }
QListWidget#SourceList::item:alternate { background: #242d3a; }
QPushButton[danger="true"] { color: #f1767a; }
QHeaderView::section {
    background: #2e3a4c;
    color: #d8e0ea;
    border: 0;
    border-right: 1px solid #3a4656;
    border-bottom: 1px solid #3a4656;
    padding: 7px;
    font-weight: 700;
}
QTabWidget::pane { background: #232c3a; border: 1px solid #3a4656; border-radius: 4px; }
QTabBar::tab {
    background: #2e3a4c;
    color: #aab6c4;
    padding: 8px 14px;
    border: 1px solid #3a4656;
    border-bottom: 0;
}
QTabBar::tab:selected { background: #232c3a; color: #2a8fd6; font-weight: 700; }
QCheckBox, QRadioButton { spacing: 7px; min-height: 24px; }
QProgressBar {
    background: #2e3a4c;
    border: 0;
    border-radius: 4px;
    min-height: 18px;
    text-align: center;
    color: #d8e0ea;
}
QProgressBar::chunk { background: #2fa878; border-radius: 4px; }
QMenu { background: #232c3a; border: 1px solid #3a4656; padding: 5px; }
QMenu::item { padding: 7px 26px 7px 10px; border-radius: 3px; }
QMenu::item:selected { background: #1f7cc4; color: #ffffff; }
QMenu::item:disabled { color: #7b8996; }
QToolTip { background: #263747; color: white; border: 0; padding: 5px; }
QSplitter::handle { background: #384454; width: 2px; height: 2px; }
QScrollBar:vertical { background: #1a212c; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #47556a; min-height: 28px; border-radius: 5px; }
QScrollBar:horizontal { background: #1a212c; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #47556a; min-width: 28px; border-radius: 5px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QFrame#FormQueueHeader {
    background: #2e3a4c;
    border: 1px solid #3a4656;
    border-bottom: 0;
}
QLabel#QueueColumnHeader {
    color: #d8e0ea;
    font-size: 11px;
    font-weight: 700;
}
QListWidget#FormQueueList {
    background: #202836;
    border: 0;
    border-radius: 0;
    padding: 4px;
}
QListWidget#FormQueueList::item { border: 0; margin: 0 0 4px 0; padding: 0; border-radius: 8px; }
QListWidget#FormQueueList::item:selected { background: transparent; }
QFrame#FormCard {
    background: #232c3a;
    border: 1px solid #384454;
    border-radius: 8px;
    border-left: 3px solid #4a5a6e;
}
QFrame#FormCard[rowParity="odd"] { background: #1e2530; }
QFrame#FormCard[rowParity="odd"]:hover, QFrame#FormCard:hover { background: #283648; border-color: #3f5a7a; }
QFrame#FormCard[status="queued"] { border-left-color: #1f7cc4; }
QFrame#FormCard[status="running"] { border-left-color: #e0a03c; background: #2c2a20; }
QFrame#FormCard[status="done"] { border-left-color: #2fa878; }
QFrame#FormCard[status="failed"] { border-left-color: #e5484d; background: #33211f; }
QLabel#FormTitle { font-size: 12px; font-weight: 600; color: #cfe0ef; }
QLabel#FormMeta { font-size: 10px; color: #8b98a8; }
QLabel#FormUrl { font-size: 10px; color: #8b98a8; }
QLabel#StatusBadge {
    color: #c2ccd8;
    background: #2e3a4c;
    border: 1px solid #3f4c5e;
    border-radius: 11px;
    padding: 2px 6px;
    font-size: 10px;
    font-weight: 600;
}
QLabel#StatusBadge[status="queued"] { color: #7db4ea; background: #23344d; border-color: #2e4a6b; }
QLabel#StatusBadge[status="running"] { color: #f0c674; background: #3a331f; border-color: #55492a; }
QLabel#StatusBadge[status="done"] { color: #52c49a; background: #1d3f33; border-color: #2a5d47; }
QLabel#StatusBadge[status="failed"] { color: #f1767a; background: #402224; border-color: #5d2a28; }
QLabel#QueueEta { color: #aab6c4; font-size: 10px; font-weight: 500; }
QLabel#QueueGlyph { color: #6aa3d9; font-size: 13px; font-weight: 700; }
QProgressBar#QueueProgress {
    background: #2e3a4c;
    border: 0;
    border-radius: 4px;
    min-height: 8px;
    max-height: 8px;
    text-align: center;
    color: transparent;
    font-size: 1px;
}
QProgressBar#QueueProgress::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #2fa878, stop:1 #3ec98d); border-radius: 4px; }
"""


def current_stylesheet() -> str:
    return DARK_STYLESHEET if _dark_mode_enabled() else APP_STYLESHEET


def _dark_mode_enabled() -> bool:
    return bool(_theme_state.get("dark", False))


_theme_state = {"dark": False}


def set_dark_mode(enabled: bool) -> None:
    _theme_state["dark"] = bool(enabled)


def is_dark_mode() -> bool:
    return _dark_mode_enabled()


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
    widget.setStyleSheet(current_stylesheet())
    apply_icons(widget)


def apply_application_theme(app: QApplication) -> None:
    app.setStyleSheet(current_stylesheet())
    theme_filter = _ThemeEventFilter(app)
    app.installEventFilter(theme_filter)
    app._answer_key_theme_filter = theme_filter

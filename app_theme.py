# app_theme.py - Classic Desktop Utility theme (light-only, IDM-style)
# Keywords: light UI, dense information layout, painted pictograph icons.
import re

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QWidget


APP_STYLESHEET = """
QMainWindow, QDialog, QMessageBox {
    background: #f5f5f5;
    color: #1c1c1c;
}
QWidget {
    color: #1c1c1c;
    font-size: 10pt;
}
QMenuBar {
    background: #fafafa;
    border-bottom: 1px solid #d0d0d0;
    padding: 1px 4px;
    color: #222222;
    font-size: 9pt;
}
QMenuBar::item {
    padding: 4px 10px;
    background: transparent;
    border-radius: 0;
}
QMenuBar::item:selected { background: #d6e4f5; }
QMenuBar::item:pressed { background: #b9d2ee; }

QToolBar {
    background: #fafafa;
    border: 0;
    border-bottom: 1px solid #d0d0d0;
    padding: 4px;
    spacing: 2px;
}
QToolBar::separator {
    width: 2px;
    background: #d0d0d0;
    margin: 6px 4px;
}

QToolButton#ToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px 10px;
    font-size: 9pt;
    color: #222222;
}
QToolButton#ToolButton:hover { background: #e8f0fa; border-color: #b9cfea; }
QToolButton#ToolButton:pressed { background: #d3e3f6; }
QToolButton#ToolButton:checked { background: #d6e4f5; border-color: #9dbae0; }
QToolButton#ToolButton::menu-indicator { image: none; }

QLabel#Header, QLabel#Title, QLabel#BrandTitle {
    color: #111111;
    font-size: 12pt;
    font-weight: 700;
}
QLabel#Section { color: #111111; font-size: 10pt; font-weight: 700; }
QLabel#Status { color: #444444; padding: 3px 0; }
QLabel#Muted { color: #666666; font-size: 9pt; }

QFrame#AppHeader {
    background: #ffffff;
    border-bottom: 1px solid #d0d0d0;
}
QLabel#AppBrand { color: #111111; font-size: 12pt; font-weight: 700; }
QLabel#RunStateDot, QLabel#ActivityDot, QLabel#AutoStatusDot {
    border-radius: 3px;
    background: #bfbfbf;
}
QLabel#ActivityDot[state="idle"], QLabel#AutoStatusDot[state="off"] { background: #bfbfbf; }
QLabel#ActivityDot[state="busy"], QLabel#AutoStatusDot[state="searching"] { background: #e8960c; }
QLabel#ActivityDot[state="grading"], QLabel#AutoStatusDot[state="grading"] { background: #2f6fb8; }
QLabel#ActivityDot[state="waiting"], QLabel#AutoStatusDot[state="active"] { background: #2e8b57; }
QLabel#ActivityDot[state="error"] { background: #c62828; }
QLabel#ActivityStatus, QLabel#AutoStatus {
    color: #444444;
    font-size: 9pt;
    font-weight: 600;
}
QLabel#ActivityStatus[state="idle"] { color: #666666; }
QLabel#ActivityStatus[state="busy"], QLabel#AutoStatus[state="searching"] { color: #8a5a00; }
QLabel#ActivityStatus[state="grading"], QLabel#AutoStatus[state="grading"] { color: #1f4e8a; }
QLabel#ActivityStatus[state="waiting"], QLabel#AutoStatus[state="active"] { color: #1f6b45; }
QLabel#ActivityStatus[state="error"] { color: #a02818; }

QFrame#IconToolbar {
    background: #ffffff;
    border-bottom: 1px solid #d0d0d0;
    padding: 6px 4px;
}

QPushButton#ToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 3px 6px;
    font-size: 8pt;
    color: #222222;
    min-width: 56px;
    min-height: 60px;
    max-width: 64px;
    max-height: 76px;
}
QPushButton#ToolButton:hover { background: #e8f0fa; border-color: #b9cfea; }
QPushButton#ToolButton:pressed { background: #d3e3f6; }
QPushButton#ToolButton[checked="true"] { background: #d6e4f5; border-color: #9dbae0; }

QFrame#CommandBar {
    background: #fafafa;
    border-bottom: 1px solid #d0d0d0;
}
QPushButton {
    min-height: 26px;
    padding: 0 14px;
    background: #f0f0f0;
    color: #111111;
    border: 1px solid #d0d0d0;
    border-radius: 2px;
    font-size: 9pt;
}
QPushButton:hover { background: #e6e6e6; }
QPushButton:pressed { background: #d9d9d9; }
QPushButton:disabled { background: #f5f5f5; color: #9a9a9a; border-color: #e0e0e0; }
QPushButton#Primary { background: #2f6fb8; color: #ffffff; border-color: #2a63a5; }
QPushButton#Primary:hover { background: #2a63a5; }
QPushButton#Primary:pressed { background: #24588f; }
QPushButton#Secondary { background: #f0f0f0; color: #111111; border-color: #d0d0d0; }
QPushButton#Secondary:hover { background: #e6e6e6; }
QPushButton#Danger { background: #b0392d; color: #ffffff; border-color: #9d3126; }
QPushButton#Danger:hover { background: #9d3126; }
QPushButton#Danger:pressed { background: #8a2a21; }

QPushButton#IconButton {
    min-width: 30px;
    max-width: 34px;
    min-height: 30px;
    padding: 0;
    background: #ffffff;
    color: #333333;
    border: 1px solid #d0d0d0;
    border-radius: 2px;
}
QPushButton#IconButton::menu-indicator { image: none; width: 0; }
QPushButton#IconButton:hover { background: #eef4fb; }

QFrame#NavSidebar {
    background: #fafafa;
    border-right: 1px solid #d0d0d0;
}
QTreeWidget#NavTree {
    background: #fafafa;
    border: 0;
    padding: 2px;
    font-size: 9pt;
    outline: 0;
}
QTreeWidget#NavTree::item {
    height: 24px;
    border: 0;
    margin: 1px 2px;
    padding: 2px 4px;
    color: #222222;
}
QTreeWidget#NavTree::item:hover { background: #eef4fb; }
QTreeWidget#NavTree::item:selected {
    background: #2f6fb8;
    color: #ffffff;
}
QTreeWidget#NavTree::branch {
    background: transparent;
}
QTreeWidget#NavTree::branch:has-children:closed { image: none; }
QTreeWidget#NavTree::branch:has-children:open { image: none; }
QTreeWidget#NavTree::item:selected:!active {
    background: #2f6fb8;
    color: #ffffff;
}

QFrame#QueuePane { background: #f5f5f5; border-right: 1px solid #d0d0d0; }
QScrollArea#DetailScroll { background: #ffffff; border: 0; }
QScrollArea#DetailScroll > QWidget > QWidget { background: #ffffff; }
QFrame#DetailPane { background: #ffffff; }
QLabel#DetailTitle { color: #111111; font-size: 13pt; font-weight: 700; }
QLabel#DetailBadge {
    background: #e9e9e9;
    color: #333333;
    border: 1px solid #cccccc;
    border-radius: 2px;
    padding: 3px 7px;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#DetailBadge[status="running"] { background: #fdf3dd; color: #8a5a00; border-color: #eccf8f; }
QLabel#DetailBadge[status="done"] { background: #e6f5ec; color: #1f6b45; border-color: #b5e0c8; }
QLabel#DetailBadge[status="failed"] { background: #fbe9e7; color: #a02818; border-color: #eac0ba; }

QFrame#Metric { background: #ffffff; border-bottom: 1px solid #d0d0d0; }
QLabel#MetricValue { color: #111111; font-size: 12pt; font-weight: 700; }
QFrame#PipelineRow { background: #ffffff; border-bottom: 1px solid #e2e2e2; }
QFrame#WorkerRow {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid #e2e2e2;
}
QFrame#WorkerRow[status="running"] { border-left: 3px solid #e8960c; background: #fffdf5; }
QFrame#WorkerRow[status="failed"] { border-left: 3px solid #b0392d; background: #fff7f6; }
QFrame#WorkerRow[status="done"] { border-left: 3px solid #2e8b57; background: #f6fbf8; }
QLabel#WorkerTitle { color: #111111; font-size: 10pt; font-weight: 700; }
QLabel#WorkerPrimary { color: #111111; font-size: 10pt; font-weight: 600; }
QLabel#WorkerStatus {
    color: #333333;
    background: #e9e9e9;
    border-radius: 2px;
    padding: 1px 5px;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#WorkerStatus[status="running"] { color: #8a5a00; background: #fdf3dd; }
QLabel#WorkerStatus[status="failed"] { color: #a02818; background: #fbe9e7; }
QLabel#WorkerStatus[status="done"] { color: #1f6b45; background: #e6f5ec; }

QFrame#TerminalFrame { background: #f5f5f5; border-top: 1px solid #c0c0c0; }
QPushButton#TerminalToggle, QPushButton#TerminalAction {
    background: transparent;
    color: #333333;
    border: 0;
    min-height: 26px;
    padding: 0 7px;
    border-radius: 2px;
}
QPushButton#TerminalToggle { font-weight: 700; }
QPushButton#TerminalToggle:hover, QPushButton#TerminalAction:hover { background: #e6e6e6; }
QLabel#TerminalMuted, QFrame#TerminalFrame QCheckBox { color: #555555; }
QFrame#TerminalFrame QTabWidget::pane { background: #f5f5f5; border: 0; }
QFrame#TerminalFrame QTabBar::tab { background: #e6e6e6; color: #444444; border-color: #cccccc; }
QFrame#TerminalFrame QTabBar::tab:selected { background: #ffffff; color: #111111; }

QFrame#Panel, QGroupBox {
    background: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 2px;
}
QGroupBox {
    margin-top: 10px;
    padding: 8px 6px 6px 6px;
    font-weight: 700;
    font-size: 9pt;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    background: #ffffff;
}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QDateEdit, QTimeEdit, QListWidget, QTableWidget, QTreeWidget {
    background: #ffffff;
    color: #1c1c1c;
    border: 1px solid #c8c8c8;
    border-radius: 2px;
    padding: 4px;
    spacing: 4px;
    selection-background-color: #d6e4f5;
    selection-color: #10263f;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDateEdit:focus, QTimeEdit:focus, QListWidget:focus,
QTableWidget:focus, QTreeWidget:focus {
    border: 1px solid #2f6fb8;
}
QComboBox, QSpinBox, QDateEdit, QTimeEdit { min-height: 22px; }
QComboBox::drop-down { border: 0; width: 20px; }
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #d0d0d0;
    selection-background-color: #d6e4f5;
    selection-color: #10263f;
}
QListWidget::item, QTreeWidget::item { min-height: 22px; padding: 2px; }
QListWidget::item:selected, QTreeWidget::item:selected {
    background: #d6e4f5;
    color: #10263f;
}

QListWidget#SourceList {
    background: #ffffff;
    border: 1px solid #d0d0d0;
    padding: 3px;
}
QListWidget#SourceList::item {
    margin: 1px;
    padding: 4px 6px;
    color: #1c1c1c;
    font-size: 9pt;
}
QListWidget#SourceList::item:hover { background: #f0f6fd; }
QListWidget#SourceList::item:selected { background: #d6e4f5; color: #10263f; }
QListWidget#SourceList::item:alternate { background: #f7f7f7; }

QHeaderView::section {
    background: #ececec;
    color: #222222;
    border: 0;
    border-right: 1px solid #d0d0d0;
    border-bottom: 1px solid #d0d0d0;
    padding: 5px;
    font-weight: 700;
    font-size: 9pt;
}
QTableCornerButton::section { background: #ececec; border: 0; border-bottom: 1px solid #d0d0d0; }

QTabWidget::pane { background: #ffffff; border: 1px solid #d0d0d0; }
QTabBar::tab {
    background: #e9e9e9;
    color: #333333;
    padding: 5px 12px;
    border: 1px solid #d0d0d0;
    border-bottom: 0;
    font-size: 9pt;
}
QTabBar::tab:selected { background: #ffffff; color: #1f4e8a; font-weight: 700; }
QTabBar::tab:hover:!selected { background: #f0f0f0; }

QCheckBox, QRadioButton { spacing: 6px; min-height: 20px; }

QProgressBar {
    background: #e5e5e5;
    border: 1px solid #d0d0d0;
    min-height: 16px;
    text-align: center;
    color: #222222;
    font-size: 9pt;
}
QProgressBar::chunk { background: #2e8b57; }

QMenu { background: #ffffff; border: 1px solid #c8c8c8; padding: 3px; font-size: 9pt; }
QMenu::item { padding: 5px 24px 5px 8px; border-radius: 0; }
QMenu::item:selected { background: #d6e4f5; color: #10263f; }
QMenu::item:disabled { color: #a0a0a0; }
QMenu::separator { height: 1px; background: #e0e0e0; margin: 3px 6px; }

QToolTip { background: #2b2b2b; color: #ffffff; border: 0; padding: 4px; font-size: 9pt; }
QSplitter::handle { background: #d0d0d0; width: 3px; height: 3px; }
QSplitter::handle:hover { background: #b9cfea; }

QScrollBar:vertical { background: #f0f0f0; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #b5b5b5; min-height: 24px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #9a9a9a; }
QScrollBar:horizontal { background: #f0f0f0; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #b5b5b5; min-width: 24px; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: #9a9a9a; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }

QFrame#FormQueueHeader {
    background: #ececec;
    border: 1px solid #d0d0d0;
    border-bottom: 0;
}
QLabel#QueueColumnHeader {
    color: #222222;
    font-size: 9pt;
    font-weight: 700;
}
QListWidget#FormQueueList {
    background: #ffffff;
    border: 0;
    border-top: 1px solid #d0d0d0;
    padding: 2px;
}
QListWidget#FormQueueList::item { border: 0; margin: 0 0 1px 0; padding: 0; }
QListWidget#FormQueueList::item:selected { background: transparent; }
QFrame#FormCard {
    background: #ffffff;
    border: 1px solid #e3e3e3;
    border-left: 3px solid #cccccc;
}
QFrame#FormCard[rowParity="odd"] { background: #fafafa; }
QFrame#FormCard[rowParity="odd"]:hover, QFrame#FormCard:hover { background: #f2f7fd; border-color: #c8d9ec; }
QFrame#FormCard[status="queued"] { border-left-color: #2f6fb8; }
QFrame#FormCard[status="running"] { border-left-color: #e8960c; background: #fffdf5; }
QFrame#FormCard[status="done"] { border-left-color: #2e8b57; }
QFrame#FormCard[status="failed"] { border-left-color: #b0392d; background: #fff7f6; }
QLabel#FormTitle { font-size: 9pt; font-weight: 600; color: #1c1c1c; }
QLabel#FormMeta { font-size: 8pt; color: #6a6a6a; }
QLabel#FormUrl { font-size: 8pt; color: #6a6a6a; }
QLabel#StatusBadge {
    color: #333333;
    background: #ececec;
    border: 1px solid #d0d0d0;
    border-radius: 8px;
    padding: 1px 6px;
    font-size: 8pt;
    font-weight: 600;
}
QLabel#StatusBadge[status="queued"] { color: #1f4e8a; background: #e6f0fa; border-color: #c2d7ee; }
QLabel#StatusBadge[status="running"] { color: #8a5a00; background: #fdf3dd; border-color: #eccf8f; }
QLabel#StatusBadge[status="done"] { color: #1f6b45; background: #e6f5ec; border-color: #b5e0c8; }
QLabel#StatusBadge[status="failed"] { color: #a02818; background: #fbe9e7; border-color: #eac0ba; }
QLabel#QueueEta { color: #5a5a5a; font-size: 8pt; font-weight: 500; }
QLabel#QueueGlyph { color: #2f6fb8; font-size: 10pt; font-weight: 700; }
QProgressBar#QueueProgress {
    background: #e7e7e7;
    border: 1px solid #d0d0d0;
    border-radius: 2px;
    min-height: 8px;
    max-height: 8px;
    text-align: center;
    color: transparent;
    font-size: 1px;
}
QProgressBar#QueueProgress::chunk { background: #2e8b57; border-radius: 2px; }

QTableWidget { gridline-color: #e0e0e0; }
QTableWidget::item { padding: 3px; }
QTableWidget::item:selected { background: #d6e4f5; color: #10263f; }
"""


# ---------------------------------------------------------------------------
# Painted pictograph icons (runtime QPainter, no asset files)
# ---------------------------------------------------------------------------
# Each icon: thin rounded-square outline in an accent color + a white/colored
# simple pictograph drawn on a flat light tile. Colors match the IDM-style
# spec: green, blue, orange, purple, red.

ICON_BACKGROUND = QColor("#f7fafd")
ICON_GLYPH = QColor("#ffffff")


def _glyph_setup(painter, color, size):
    painter.setPen(QPen(color))
    painter.setBrush(QBrush(color))
    painter.setRenderHint(QPainter.Antialiasing, True)


def _draw_plus(painter, color, size):
    _glyph_setup(painter, color, size)
    t = size * 0.16
    c = size / 2.0
    length = size * 0.34
    painter.drawRect(QRectF(c - t / 2.0, c - length / 2.0, t, length))
    painter.drawRect(QRectF(c - length / 2.0, c - t / 2.0, length, t))


def _draw_minus(painter, color, size):
    _glyph_setup(painter, color, size)
    t = size * 0.14
    length = size * 0.38
    c = size / 2.0
    painter.drawRect(QRectF(c - length / 2.0, c - t / 2.0, length, t))


def _draw_play(painter, color, size):
    pen = QPen(color)
    pen.setWidthF(size * 0.06)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    tri = QPolygonF([
        QPointF(size * 0.30, size * 0.24),
        QPointF(size * 0.72, size * 0.50),
        QPointF(size * 0.30, size * 0.76),
    ])
    painter.drawPolygon(tri)


def _draw_stop(painter, color, size):
    _glyph_setup(painter, color, size)
    inset = size * 0.26
    painter.drawRect(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))


def _draw_list(painter, color, size):
    _glyph_setup(painter, color, size)
    x0 = size * 0.16
    x1 = size * 0.84
    h = size * 0.11
    for row in (0.28, 0.50, 0.72):
        c = size * row
        painter.drawRect(QRectF(x0, c - h / 2.0, x1 - x0, h))
        painter.drawRect(QRectF(size * 0.70, c - h / 2.0, size * 0.10, h))


def _draw_magnifier(painter, color, size):
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(size * 0.08)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    c0 = size * 0.44
    r = size * 0.22
    painter.drawEllipse(QPointF(c0, c0), r, r)
    pen2 = QPen(color)
    pen2.setWidthF(size * 0.07)
    painter.setPen(pen2)
    painter.drawLine(QPointF(size * 0.60, size * 0.60), QPointF(size * 0.80, size * 0.80))


def _draw_key(painter, color, size):
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(size * 0.065)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QPointF(size * 0.34, size * 0.34), size * 0.16, size * 0.16)
    painter.drawLine(QPointF(size * 0.47, size * 0.47), QPointF(size * 0.78, size * 0.78))
    for off in (-0.07, -0.01, 0.05):
        y = size * (0.78 + off)
        painter.drawLine(QPointF(size * 0.66, y), QPointF(size * 0.84, y))


def _draw_gear(painter, color, size):
    import math

    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(size * 0.07)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    c = QPointF(size / 2.0, size / 2.0)
    painter.drawEllipse(c, size * 0.12, size * 0.12)
    painter.drawEllipse(c, size * 0.22, size * 0.22)
    for i in range(8):
        ang = math.pi * 2 * i / 8.0
        p1 = QPointF(c.x() + math.cos(ang) * size * 0.22,
                     c.y() + math.sin(ang) * size * 0.22)
        p2 = QPointF(c.x() + math.cos(ang) * size * 0.34,
                     c.y() + math.sin(ang) * size * 0.34)
        painter.drawLine(p1, p2)


def _draw_doc(painter, color, size):
    _glyph_setup(painter, color, size)
    x0 = size * 0.22
    x1 = size * 0.78
    y0 = size * 0.16
    y1 = size * 0.84
    painter.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))
    h = size * 0.07
    for row in (0.30, 0.42, 0.54, 0.66):
        c = size * row
        painter.drawRect(QRectF(x0 + size * 0.08, c - h / 2.0, (x1 - x0) * 0.55, h))


def _draw_chart(painter, color, size):
    _glyph_setup(painter, color, size)
    ground = size * 0.74
    bar_width = size * 0.14
    x0 = size * 0.22
    heights = (size * 0.30, size * 0.44, size * 0.20)
    for i, h in enumerate(heights):
        x = x0 + i * (bar_width + size * 0.10)
        painter.drawRect(QRectF(x, ground - h, bar_width, h))


def _draw_tray(painter, color, size):
    pen = QPen(color)
    pen.setWidthF(size * 0.06)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    x0 = size * 0.26
    x1 = size * 0.74
    painter.drawLine(QPointF(x0, size * 0.70), QPointF(x1, size * 0.70))
    painter.drawLine(QPointF(x0, size * 0.82), QPointF(x1, size * 0.82))
    px = QPointF(size * 0.42, size * 0.34)
    c = QPointF(size * 0.50, size * 0.58)
    painter.drawLine(px, c)
    painter.drawLine(QPointF(size * 0.58, size * 0.34), c)
    painter.drawLine(px, QPointF(size * 0.50, size * 0.30))


def _draw_terminal(painter, color, size):
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(size * 0.08)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    tri = QPolygonF([
        QPointF(size * 0.24, size * 0.28),
        QPointF(size * 0.44, size * 0.50),
        QPointF(size * 0.24, size * 0.72),
    ])
    painter.drawPolyline(tri)
    painter.drawLine(QPointF(size * 0.50, size * 0.70), QPointF(size * 0.78, size * 0.70))


def _draw_trash(painter, color, size):
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(size * 0.06)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    x0 = size * 0.30
    x1 = size * 0.70
    y0 = size * 0.26
    y1 = size * 0.78
    painter.drawLine(QPointF(x0, size * 0.26), QPointF(x0, y1))
    painter.drawLine(QPointF(x1, size * 0.26), QPointF(x1, y1))
    painter.drawLine(QPointF(y0 * 0.0 + size * 0.26, y0 * 0.0 + size * 0.20), QPointF(x1, y0 * 0.0 + size * 0.20))
    painter.drawLine(QPointF(size * 0.34, size * 0.20), QPointF(size * 0.66, size * 0.20))
    painter.drawLine(QPointF(size * 0.44, size * 0.14), QPointF(size * 0.56, size * 0.14))


def _draw_cross(painter, color, size):
    pen = QPen(color)
    pen.setWidthF(size * 0.09)
    pen.setCapStyle(Qt.RoundCap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(pen)
    a = size * 0.28
    b = size * 0.72
    painter.drawLine(QPointF(a, a), QPointF(b, b))
    painter.drawLine(QPointF(b, a), QPointF(a, b))


def _draw_arrow_left(painter, color, size):
    pen = QPen(color)
    pen.setWidthF(size * 0.08)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    c = QPointF(size * 0.50, size * 0.50)
    painter.drawLine(QPointF(size * 0.78, c.y()), QPointF(size * 0.30, c.y()))
    tri = QPolygonF([
        QPointF(size * 0.30, c.y()),
        QPointF(size * 0.44, c.y() - size * 0.14),
        QPointF(size * 0.44, c.y() + size * 0.14),
    ])
    painter.drawPolyline(tri)


def _draw_arrow_right(painter, color, size):
    pen = QPen(color)
    pen.setWidthF(size * 0.08)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    c = QPointF(size * 0.50, size * 0.50)
    painter.drawLine(QPointF(size * 0.22, c.y()), QPointF(size * 0.70, c.y()))
    tri = QPolygonF([
        QPointF(size * 0.70, c.y()),
        QPointF(size * 0.56, c.y() - size * 0.14),
        QPointF(size * 0.56, c.y() + size * 0.14),
    ])
    painter.drawPolyline(tri)


def _draw_bell(painter, color, size):
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(size * 0.07)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    x0 = size * 0.28
    x1 = size * 0.72
    y0 = size * 0.24
    y1 = size * 0.66
    path = QPainterPath(QPointF(x0, y1))
    path.quadTo(QPointF(size * 0.30, y0), QPointF(size * 0.50, y0))
    path.quadTo(QPointF(size * 0.70, y0), QPointF(x1, y1))
    painter.drawPath(path)
    painter.drawLine(QPointF(x0, y1), QPointF(x1, y1))
    painter.drawLine(QPointF(size * 0.50, y1), QPointF(size * 0.50, size * 0.74))
    painter.drawEllipse(QPointF(size * 0.50, size * 0.78), size * 0.045, size * 0.045)


def _draw_person(painter, color, size):
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(size * 0.06)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QPointF(size * 0.50, size * 0.32), size * 0.12, size * 0.12)
    body = QPainterPath(QPointF(size * 0.30, size * 0.78))
    body.quadTo(QPointF(size * 0.50, size * 0.50), QPointF(size * 0.70, size * 0.78))
    painter.drawPath(body)


def _draw_dashboard(painter, color, size):
    _glyph_setup(painter, color, size)
    cell = size * 0.24
    gap = size * 0.06
    start = size * 0.20
    for row in range(2):
        for col in range(2):
            painter.drawRect(QRectF(
                start + col * (cell + gap),
                start + row * (cell + gap),
                cell,
                cell,
            ))


# glyph name -> draw callable
_GLYPHS = {
    "plus": _draw_plus,
    "minus": _draw_minus,
    "play": _draw_play,
    "stop": _draw_stop,
    "list": _draw_list,
    "search": _draw_magnifier,
    "key": _draw_key,
    "gear": _draw_gear,
    "doc": _draw_doc,
    "chart": _draw_chart,
    "tray": _draw_tray,
    "terminal": _draw_terminal,
    "trash": _draw_trash,
    "cross": _draw_cross,
    "left": _draw_arrow_left,
    "right": _draw_arrow_right,
    "bell": _draw_bell,
    "person": _draw_person,
    "dashboard": _draw_dashboard,
}


# Tool accent colors (flat, saturated): green, blue, orange, purple, red.
ACCENT_GREEN = "#2e7d32"
ACCENT_BLUE = "#1565c0"
ACCENT_ORANGE = "#e65100"
ACCENT_PURPLE = "#7b1fa2"
ACCENT_RED = "#b0392d"
ACCENT_TEAL = "#00695c"
ACCENT_SLATE = "#37474f"

DEFAULT_ACCENT = ACCENT_BLUE


def pictograph_icon(glyph, size=48, accent=None):
    """Render a flat rounded-square pictograph icon with QPainter."""
    accent = accent or DEFAULT_ACCENT
    draw = _GLYPHS.get(glyph)
    if draw is None:
        draw = _draw_dashboard
    pm = QPixmap(size, size)
    pm.fill(QColor("#00000000"))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    color = QColor(accent)
    pen = QPen(color)
    pen.setWidthF(max(1.5, size * 0.05))
    painter.setPen(pen)
    painter.setBrush(QBrush(ICON_BACKGROUND))
    inset = pen.widthF() / 2.0 + 0.5
    painter.drawRoundedRect(
        QRectF(0, 0, float(size), float(size)).adjusted(inset, inset, -inset, -inset),
        size * 0.18,
        size * 0.18,
    )
    draw(painter, color, size)
    painter.end()
    return QIcon(pm)


def small_icon(glyph, size=16, accent=None):
    """Glyph-only transparent icon for small buttons / menu items."""
    accent = accent or DEFAULT_ACCENT
    draw = _GLYPHS.get(glyph)
    if draw is None:
        return QIcon()
    pm = QPixmap(size, size)
    pm.fill(QColor("#00000000"))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    draw(painter, QColor(accent), size)
    painter.end()
    return QIcon(pm)


_ICON_RULES = [
    (("clean", "save", "apply", "grade", "run now", "ok", "done", "yes"), ("play", ACCENT_GREEN)),
    (("auto run", "start", "grade all", "grade now"), ("play", ACCENT_GREEN)),
    (("stop",), ("stop", ACCENT_RED)),
    (("search", "find", "review", "scan"), ("search", ACCENT_ORANGE)),
    (("add", "import", "source"), ("plus", ACCENT_GREEN)),
    (("answer key", "answers"), ("key", ACCENT_PURPLE)),
    (("settings", "config"), ("gear", ACCENT_SLATE)),
    (("audit", "logs", "history"), ("doc", ACCENT_BLUE)),
    (("export", "report", "csv"), ("tray", ACCENT_PURPLE)),
    (("notify", "notification"), ("bell", ACCENT_ORANGE)),
    (("remove", "clear", "delete", "trash"), ("trash", ACCENT_RED)),
    (("undo", "back", "restore", "skip"), ("left", ACCENT_SLATE)),
    (("minimize",), ("minus", ACCENT_SLATE)),
    (("close", "cancel", "exit", "logout", "sign out"), ("cross", ACCENT_RED)),
    (("login", "sign in", "google"), ("person", ACCENT_BLUE)),
    (("terminal",), ("terminal", ACCENT_TEAL)),
    (("forms", "queue", "dashboard"), ("list", ACCENT_BLUE)),
    (("chart", "graph", "budget"), ("chart", ACCENT_ORANGE)),
]


def _match_rule(text: str):
    lowered = text.casefold()
    for terms, (glyph, accent) in _ICON_RULES:
        if any(term in lowered for term in terms):
            return glyph, accent
    return None


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
    match = _match_rule(text)
    if match is None:
        return
    glyph, accent = match
    is_tool = button.objectName() == "ToolButton"
    if is_tool:
        icon = pictograph_icon(glyph, size=44, accent=accent)
    else:
        icon = small_icon(glyph, size=16, accent=accent)
    button.setIcon(icon)


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


def current_stylesheet() -> str:
    return APP_STYLESHEET


_theme_state = {"dark": False}


def set_dark_mode(enabled: bool) -> None:
    # Light-only theme: dark mode is intentionally unsupported.
    _theme_state["dark"] = False


def is_dark_mode() -> bool:
    return False


def apply_widget_theme(widget: QWidget) -> None:
    widget.setStyleSheet(APP_STYLESHEET)
    apply_icons(widget)


def apply_application_theme(app: QApplication) -> None:
    app.setStyleSheet(APP_STYLESHEET)
    theme_filter = _ThemeEventFilter(app)
    app.installEventFilter(theme_filter)
    app._answer_key_theme_filter = theme_filter
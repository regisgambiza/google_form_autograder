# app_theme.py - Modern light theme for the autograder.
# Keywords: soft surfaces, rounded corners, indigo accent, card-based layout,
# painted pictograph icons (runtime QPainter, no asset files).
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
* { outline: none; }
QMainWindow, QDialog, QMessageBox {
    background: #eef1f6;
    color: #1c2430;
}
QWidget {
    color: #1c2430;
    font-size: 10pt;
    font-family: "Segoe UI", "Noto Sans", sans-serif;
}
QMenuBar {
    background: #ffffff;
    border-bottom: 1px solid #e3e8ef;
    padding: 2px 8px;
    color: #344054;
    font-size: 9pt;
}
QMenuBar::item {
    padding: 5px 12px;
    background: transparent;
    border-radius: 6px;
}
QMenuBar::item:selected { background: #eef1f6; color: #1c2430; }
QMenuBar::item:pressed { background: #e0e7ff; color: #4338ca; }

QToolBar {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid #e3e8ef;
    padding: 4px;
    spacing: 2px;
}
QToolBar::separator {
    width: 2px;
    background: #e3e8ef;
    margin: 8px 6px;
}

QFrame#AppHeader {
    background: #ffffff;
    border-bottom: 1px solid #e3e8ef;
}
QLabel#AppBrand { color: #111827; font-size: 13pt; font-weight: 700; letter-spacing: 0.2px; }
QLabel#Muted { color: #667085; font-size: 9pt; }
QLabel#Section { color: #111827; font-size: 10pt; font-weight: 700; }

QLabel#RunStateDot, QLabel#ActivityDot, QLabel#AutoStatusDot {
    border-radius: 4px;
    background: #98a2b3;
}
QLabel#ActivityDot[state="idle"], QLabel#AutoStatusDot[state="off"] { background: #98a2b3; }
QLabel#ActivityDot[state="busy"], QLabel#AutoStatusDot[state="searching"] { background: #f79009; }
QLabel#ActivityDot[state="grading"], QLabel#AutoStatusDot[state="grading"] { background: #4f46e5; }
QLabel#ActivityDot[state="waiting"], QLabel#AutoStatusDot[state="active"] { background: #12b76a; }
QLabel#ActivityDot[state="error"] { background: #f04438; }
QLabel#ActivityStatus, QLabel#AutoStatus {
    color: #344054;
    font-size: 9pt;
    font-weight: 600;
}
QLabel#ActivityStatus[state="idle"] { color: #667085; }
QLabel#ActivityStatus[state="busy"], QLabel#AutoStatus[state="searching"] { color: #b54708; }
QLabel#ActivityStatus[state="grading"], QLabel#AutoStatus[state="grading"] { color: #4f46e5; }
QLabel#ActivityStatus[state="waiting"], QLabel#AutoStatus[state="active"] { color: #067647; }
QLabel#ActivityStatus[state="error"] { color: #b42318; }

QFrame#IconToolbar {
    background: #ffffff;
    border-bottom: 1px solid #e3e8ef;
    padding: 8px 6px;
}

QToolButton#ToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 6px 8px;
    font-size: 8.5pt;
    color: #344054;
}
QToolButton#ToolButton:hover { background: #eef1f6; border-color: #d6dcea; }
QToolButton#ToolButton:pressed { background: #e0e7ff; }
QToolButton#ToolButton:checked { background: #e0e7ff; border-color: #c7d2fe; color: #4338ca; }
QToolButton#ToolButton::menu-indicator { image: none; }

QPushButton {
    min-height: 30px;
    padding: 0 16px;
    background: #ffffff;
    color: #344054;
    border: 1px solid #d0d7e2;
    border-radius: 8px;
    font-size: 9.5pt;
}
QPushButton:hover { background: #f4f6fa; border-color: #c2cad6; }
QPushButton:pressed { background: #e9edf4; }
QPushButton:disabled { background: #f5f7fa; color: #98a2b3; border-color: #e4e9f0; }
QPushButton#Primary { background: #4f46e5; color: #ffffff; border-color: #4f46e5; }
QPushButton#Primary:hover { background: #4338ca; border-color: #4338ca; }
QPushButton#Primary:pressed { background: #3730a3; }
QPushButton#Secondary { background: #ffffff; color: #344054; border-color: #d0d7e2; }
QPushButton#Secondary:hover { background: #f4f6fa; }
QPushButton#Danger { background: #f04438; color: #ffffff; border-color: #f04438; }
QPushButton#Danger:hover { background: #d92d20; border-color: #d92d20; }
QPushButton#Danger:pressed { background: #b42318; }

QPushButton#IconButton {
    min-width: 32px;
    max-width: 36px;
    min-height: 32px;
    padding: 0;
    background: #ffffff;
    color: #344054;
    border: 1px solid #d0d7e2;
    border-radius: 8px;
}
QPushButton#IconButton::menu-indicator { image: none; width: 0; }
QPushButton#IconButton:hover { background: #eef1f6; }

QFrame#QueuePane {
    background: #f7f9fc;
    border-right: 1px solid #e3e8ef;
}
QScrollArea#DetailScroll { background: #eef1f6; border: 0; }
QScrollArea#DetailScroll > QWidget > QWidget { background: #eef1f6; }
QFrame#DetailPane {
    background: #eef1f6;
    border: 0;
}
QFrame#DetailPane > QWidget { background: transparent; }
QLabel#DetailTitle { color: #111827; font-size: 14pt; font-weight: 700; }
QLabel#DetailBadge {
    background: #eef1f6;
    color: #344054;
    border: 1px solid #d0d7e2;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#DetailBadge[status="running"] { background: #fffaeb; color: #b54708; border-color: #fec84b; }
QLabel#DetailBadge[status="done"] { background: #ecfdf3; color: #067647; border-color: #a6f4c5; }
QLabel#DetailBadge[status="failed"] { background: #fef3f2; color: #b42318; border-color: #fecdca; }

QFrame#Metric {
    background: #ffffff;
    border: 1px solid #e3e8ef;
    border-radius: 12px;
}
QFrame#Metric:hover { border-color: #c7d2fe; }
QLabel#MetricValue { color: #111827; font-size: 12pt; font-weight: 700; }

QFrame#Panel, QGroupBox {
    background: #ffffff;
    border: 1px solid #e3e8ef;
    border-radius: 12px;
}
QFrame#Panel { padding: 6px; }
QGroupBox {
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    font-weight: 700;
    font-size: 9.5pt;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background: #ffffff;
    color: #344054;
}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QDateEdit, QTimeEdit, QListWidget, QTableWidget, QTreeWidget {
    background: #ffffff;
    color: #1c2430;
    border: 1px solid #d0d7e2;
    border-radius: 8px;
    padding: 5px 6px;
    spacing: 4px;
    selection-background-color: #e0e7ff;
    selection-color: #3730a3;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover,
QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover, QTimeEdit:hover {
    border-color: #c2cad6;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDateEdit:focus, QTimeEdit:focus, QListWidget:focus,
QTableWidget:focus, QTreeWidget:focus {
    border: 1px solid #4f46e5;
    background: #ffffff;
}
QComboBox, QSpinBox, QDateEdit, QTimeEdit { min-height: 24px; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #d0d7e2;
    border-radius: 8px;
    selection-background-color: #e0e7ff;
    selection-color: #3730a3;
}
QListWidget::item, QTreeWidget::item { min-height: 24px; padding: 2px 4px; border-radius: 6px; }
QListWidget::item:selected, QTreeWidget::item:selected {
    background: #e0e7ff;
    color: #3730a3;
}
QListWidget::item:hover:!selected, QTreeWidget::item:hover:!selected { background: #f4f6fa; }

QListWidget#SourceList {
    background: #ffffff;
    border: 1px solid #d0d7e2;
    border-radius: 8px;
    padding: 4px;
}
QListWidget#SourceList::item {
    margin: 1px 0;
    padding: 6px 8px;
    color: #1c2430;
    font-size: 9.5pt;
    border-radius: 6px;
}
QListWidget#SourceList::item:hover { background: #eef1f6; }
QListWidget#SourceList::item:selected { background: #e0e7ff; color: #3730a3; }
QListWidget#SourceList::item:alternate { background: #f7f9fc; }

QHeaderView::section {
    background: #f4f6fa;
    color: #475467;
    border: 0;
    border-right: 1px solid #e3e8ef;
    border-bottom: 1px solid #e3e8ef;
    padding: 6px;
    font-weight: 700;
    font-size: 9pt;
}
QTableCornerButton::section { background: #f4f6fa; border: 0; border-bottom: 1px solid #e3e8ef; }

QTabWidget::pane { background: #ffffff; border: 1px solid #e3e8ef; border-radius: 10px; top: -1px; }
QTabBar::tab {
    background: transparent;
    color: #667085;
    padding: 6px 16px;
    border: 0;
    font-size: 9.5pt;
}
QTabBar::tab:selected { color: #4f46e5; font-weight: 700; border-bottom: 2px solid #4f46e5; }
QTabBar::tab:hover:!selected { color: #344054; }

QCheckBox, QRadioButton { spacing: 6px; min-height: 20px; }
QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: #4f46e5; border-radius: 4px; }
QCheckBox::indicator:unchecked, QRadioButton::indicator:unchecked { background: #ffffff; border: 1px solid #d0d7e2; border-radius: 4px; }

QProgressBar {
    background: #e8ecf3;
    border: 0;
    border-radius: 6px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
    color: #344054;
    font-size: 9pt;
}
QProgressBar::chunk { background: #4f46e5; border-radius: 6px; }

QFrame#FormQueueHeader {
    background: #f4f6fa;
    border: 1px solid #e3e8ef;
    border-bottom: 0;
    border-radius: 10px 10px 0 0;
}
QLabel#QueueColumnHeader {
    color: #475467;
    font-size: 9pt;
    font-weight: 700;
}
QListWidget#FormQueueList {
    background: #f7f9fc;
    border: 0;
    border-top: 1px solid #e3e8ef;
    padding: 4px;
}
QListWidget#FormQueueList::item { border: 0; margin: 2px 0; padding: 0; border-radius: 8px; }
QListWidget#FormQueueList::item:selected { background: transparent; }
QFrame#FormCard {
    background: #ffffff;
    border: 1px solid #e3e8ef;
    border-radius: 10px;
}
QFrame#FormCard[rowParity="odd"] { background: #fbfcfe; }
QFrame#FormCard[rowParity="odd"]:hover, QFrame#FormCard:hover {
    background: #f0f4ff;
    border-color: #c7d2fe;
}
QFrame#FormCard[status="queued"] { border-left: 3px solid #4f46e5; }
QFrame#FormCard[status="running"] { border-left: 3px solid #f79009; background: #fffdf5; }
QFrame#FormCard[status="done"] { border-left: 3px solid #12b76a; }
QFrame#FormCard[status="failed"] { border-left: 3px solid #f04438; background: #fff7f6; }
QLabel#FormTitle { font-size: 9.5pt; font-weight: 600; color: #1c2430; }
QLabel#FormMeta { font-size: 8pt; color: #667085; }
QLabel#FormUrl { font-size: 8pt; color: #667085; }
QLabel#StatusBadge {
    color: #475467;
    background: #f4f6fa;
    border: 1px solid #d0d7e2;
    border-radius: 10px;
    padding: 1px 7px;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#StatusBadge[status="queued"] { color: #4338ca; background: #e0e7ff; border-color: #c7d2fe; }
QLabel#StatusBadge[status="running"] { color: #b54708; background: #fffaeb; border-color: #fec84b; }
QLabel#StatusBadge[status="done"] { color: #067647; background: #ecfdf3; border-color: #a6f4c5; }
QLabel#StatusBadge[status="failed"] { color: #b42318; background: #fef3f2; border-color: #fecdca; }
QLabel#QueueEta { color: #667085; font-size: 8pt; font-weight: 600; }
QLabel#QueueGlyph { color: #4f46e5; font-size: 10pt; font-weight: 700; }
QProgressBar#QueueProgress {
    background: #e8ecf3;
    border: 0;
    border-radius: 4px;
    min-height: 8px;
    max-height: 8px;
    text-align: center;
    color: transparent;
    font-size: 1px;
}
QProgressBar#QueueProgress::chunk { background: #12b76a; border-radius: 4px; }

QFrame#PipelineRow, QFrame#WorkerRow {
    background: #ffffff;
    border: 1px solid #e3e8ef;
    border-radius: 10px;
}
QFrame#WorkerRow { margin-bottom: 4px; }
QFrame#WorkerRow[status="running"] { border-left: 4px solid #f79009; background: #fffdf5; }
QFrame#WorkerRow[status="failed"] { border-left: 4px solid #f04438; background: #fff7f6; }
QFrame#WorkerRow[status="done"] { border-left: 4px solid #12b76a; background: #f6fef9; }
QLabel#WorkerTitle { color: #111827; font-size: 10pt; font-weight: 700; }
QLabel#WorkerPrimary { color: #111827; font-size: 10pt; font-weight: 600; }
QLabel#WorkerStatus {
    color: #344054;
    background: #eef1f6;
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#WorkerStatus[status="running"] { color: #b54708; background: #fffaeb; }
QLabel#WorkerStatus[status="failed"] { color: #b42318; background: #fef3f2; }
QLabel#WorkerStatus[status="done"] { color: #067647; background: #ecfdf3; }

QFrame#TerminalFrame { background: #1c2430; border-top: 1px solid #000000; }
QPushButton#TerminalToggle, QPushButton#TerminalAction {
    background: transparent;
    color: #d0d7e2;
    border: 0;
    min-height: 28px;
    padding: 0 10px;
    border-radius: 6px;
}
QPushButton#TerminalToggle { font-weight: 700; }
QPushButton#TerminalToggle:hover, QPushButton#TerminalAction:hover { background: #2b3446; }
QLabel#TerminalMuted, QFrame#TerminalFrame QCheckBox { color: #98a2b3; }
QFrame#TerminalFrame QTabWidget::pane { background: #1c2430; border: 0; }
QFrame#TerminalFrame QTabBar::tab { background: transparent; color: #98a2b3; border: 0; }
QFrame#TerminalFrame QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid #4f46e5; }

QMenu { background: #ffffff; border: 1px solid #e3e8ef; border-radius: 10px; padding: 6px; font-size: 9.5pt; }
QMenu::item { padding: 6px 24px 6px 10px; border-radius: 6px; }
QMenu::item:selected { background: #eef1f6; color: #1c2430; }
QMenu::item:disabled { color: #98a2b3; }
QMenu::separator { height: 1px; background: #e3e8ef; margin: 4px 8px; }

QToolTip { background: #1c2430; color: #ffffff; border: 0; border-radius: 6px; padding: 6px; font-size: 9pt; }
QSplitter::handle { background: #e3e8ef; width: 3px; height: 3px; }
QSplitter::handle:hover { background: #c7d2fe; }

QScrollBar:vertical { background: transparent; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #c2cad6; min-height: 24px; border-radius: 6px; }
QScrollBar::handle:vertical:hover { background: #98a2b3; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #c2cad6; min-width: 24px; border-radius: 6px; }
QScrollBar::handle:horizontal:hover { background: #98a2b3; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }

QTableWidget { gridline-color: #e8ecf3; }
QTableWidget::item { padding: 4px; }
QTableWidget::item:selected { background: #e0e7ff; color: #3730a3; }
"""


# ---------------------------------------------------------------------------
# Painted pictograph icons (runtime QPainter, no asset files)
# ---------------------------------------------------------------------------
# Each icon: thin rounded-square outline in an accent color + a white/colored
# simple pictograph drawn on a flat light tile.

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


# Tool accent colors (flat, saturated).
ACCENT_GREEN = "#12b76a"
ACCENT_BLUE = "#2563eb"
ACCENT_ORANGE = "#f79009"
ACCENT_PURPLE = "#7c3aed"
ACCENT_RED = "#f04438"
ACCENT_TEAL = "#0d9488"
ACCENT_SLATE = "#475467"
ACCENT_INDIGO = "#4f46e5"

DEFAULT_ACCENT = ACCENT_INDIGO


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

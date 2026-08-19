# app_theme.py - Shared classic-utility theme for the autograder.
# Keywords: light gray Windows chrome, white workspace, thin 1px borders,
# colorful painted toolbar icons (runtime QPainter, no asset files).
#
# The stylesheet itself lives in gui_studio/theme.qss so the main window and
# every dialog (settings, answer keys, audit viewer, auto-run, scan source)
# share one source of truth. APP_STYLESHEET remains a plain string for
# backwards compatibility with apply_widget_theme/apply_application_theme.
import os
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


_THEME_QSS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "gui_studio", "theme.qss"
)

# Minimal embedded fallback (only used if the shared QSS file is missing).
_FALLBACK_STYLESHEET = """
QMainWindow, QDialog, QMessageBox { background: #f0f0f0; color: #000000; }
QWidget { color: #000000; font-family: "Segoe UI", "Tahoma", sans-serif; font-size: 9pt; }
QPushButton { background: #f5f5f5; border: 1px solid #c8c8c8; padding: 2px 12px; min-height: 24px; }
QPushButton:hover { background: #ececec; }
QPushButton#Primary { background: #4f46e5; color: #ffffff; border: 1px solid #4f46e5; }
QPushButton#Secondary { background: #f5f5f5; color: #000000; border: 1px solid #c8c8c8; }
QPushButton#Danger { background: #f04438; color: #ffffff; border: 1px solid #f04438; }
QLineEdit, QComboBox, QSpinBox, QTextEdit, QListWidget, QTableWidget, QTreeWidget {
    background: #ffffff; border: 1px solid #c8c8c8; padding: 2px 6px;
}
QProgressBar, QMenu, QScrollBar { background: #f0f0f0; }
QTabWidget::pane { border: 1px solid #c8c8c8; background: #ffffff; }
"""


def _load_shared_stylesheet() -> str:
    try:
        with open(_THEME_QSS_PATH, "r", encoding="utf-8") as fh:
            text = fh.read()
        if text.strip():
            return text
    except OSError:
        pass
    return _FALLBACK_STYLESHEET


APP_STYLESHEET = _load_shared_stylesheet()


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

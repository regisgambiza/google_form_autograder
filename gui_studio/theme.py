# gui_studio/theme.py - Palette, stylesheet loading and painted icons for the
# Studio shell. Color identity is carried over from the classic app theme
# (indigo accent on soft light surfaces); the icon language is new: flat
# tinted tiles with stroke-drawn glyphs.
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

# ---------------------------------------------------------------------------
# Palette (same brand identity as the classic theme)
# ---------------------------------------------------------------------------
INDIGO = "#4f46e5"
INDIGO_DARK = "#4338ca"
INDIGO_DEEP = "#3730a3"
INDIGO_TINT = "#e0e7ff"
INDIGO_SOFT = "#c7d2fe"

GREEN = "#12b76a"
GREEN_DARK = "#0b8a50"
GREEN_TEXT = "#067647"
GREEN_TINT = "#ecfdf3"
GREEN_SOFT = "#a6f4c5"

ORANGE = "#f79009"
ORANGE_TEXT = "#b54708"
ORANGE_TINT = "#fffaeb"
ORANGE_SOFT = "#fec84b"

RED = "#f04438"
RED_TEXT = "#b42318"
RED_TINT = "#fef3f2"
RED_SOFT = "#fecdca"

BLUE = "#2563eb"
PURPLE = "#7c3aed"
TEAL = "#0d9488"
SLATE = "#475467"

TEXT = "#1c2430"
HEADING = "#111827"
MUTED = "#667085"
BG = "#eef1f6"
SURFACE = "#ffffff"
BORDER = "#e3e8ef"
TRACK = "#e8ecf3"
TERMINAL_BG = "#1c2430"
TERMINAL_FG = "#d0d7e2"

_THEME_DIR = os.path.dirname(os.path.abspath(__file__))


def load_stylesheet() -> str:
    """Load the Studio QSS file (kept as a separate .qss for maintainability)."""
    path = os.path.join(_THEME_DIR, "theme.qss")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Painted icons - stroke glyphs on soft tinted tiles
# ---------------------------------------------------------------------------

_TINTS = {
    INDIGO: INDIGO_TINT,
    GREEN: GREEN_TINT,
    ORANGE: ORANGE_TINT,
    RED: RED_TINT,
    BLUE: "#eff6ff",
    PURPLE: "#f5f3ff",
    TEAL: "#f0fdfa",
    SLATE: "#f4f6fa",
}


def _pen(color, width):
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _g_dashboard(p, c, s):
    p.setPen(_pen(c, s * 0.07))
    cell, gap, start = s * 0.24, s * 0.07, s * 0.20
    p.drawRoundedRect(QRectF(start, start, cell, cell), s * 0.05, s * 0.05)
    p.drawRoundedRect(QRectF(start + cell + gap, start, cell, cell), s * 0.05, s * 0.05)
    p.drawRoundedRect(QRectF(start, start + cell + gap, cell, cell), s * 0.05, s * 0.05)
    p.drawRoundedRect(QRectF(start + cell + gap, start + cell + gap, cell, cell), s * 0.05, s * 0.05)


def _g_queue(p, c, s):
    p.setPen(_pen(c, s * 0.07))
    for i, y in enumerate((0.26, 0.50, 0.74)):
        yy = s * y
        p.drawRoundedRect(QRectF(s * 0.22, yy - s * 0.05, s * 0.08, s * 0.08), s * 0.02, s * 0.02)
        p.drawLine(QPointF(s * 0.40, yy), QPointF(s * 0.78, yy))
        if i == 0:
            p.drawLine(QPointF(s * 0.62, yy - s * 0.09), QPointF(s * 0.62, yy + s * 0.09))
            p.drawLine(QPointF(s * 0.70, yy - s * 0.09), QPointF(s * 0.70, yy + s * 0.09))


def _g_providers(p, c, s):
    p.setPen(_pen(c, s * 0.07))
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.24, s * 0.56, s * 0.20), s * 0.04, s * 0.04)
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.56, s * 0.56, s * 0.20), s * 0.04, s * 0.04)
    p.setBrush(QBrush(QColor(c)))
    for y in (0.34, 0.66):
        p.drawEllipse(QPointF(s * 0.32, s * y), s * 0.03, s * 0.03)
    p.setBrush(Qt.NoBrush)


def _g_pulse(p, c, s):
    p.setPen(_pen(c, s * 0.08))
    path_pts = [
        (0.18, 0.50), (0.32, 0.50), (0.40, 0.30), (0.50, 0.70), (0.58, 0.42),
        (0.64, 0.50), (0.82, 0.50),
    ]
    pts = [QPointF(s * x, s * y) for x, y in path_pts]
    p.drawPolyline(QPolygonF(pts))


def _g_play(p, c, s):
    p.setPen(_pen(c, s * 0.09))
    p.setBrush(QBrush(QColor(c)))
    tri = QPolygonF([
        QPointF(s * 0.34, s * 0.26),
        QPointF(s * 0.72, s * 0.50),
        QPointF(s * 0.34, s * 0.74),
    ])
    p.drawPolygon(tri)
    p.setBrush(Qt.NoBrush)


def _g_stop(p, c, s):
    p.setPen(_pen(c, s * 0.09))
    p.setBrush(QBrush(QColor(c)))
    p.drawRoundedRect(QRectF(s * 0.30, s * 0.30, s * 0.40, s * 0.40), s * 0.06, s * 0.06)
    p.setBrush(Qt.NoBrush)


def _g_plus(p, c, s):
    p.setPen(_pen(c, s * 0.09))
    p.drawLine(QPointF(s * 0.50, s * 0.26), QPointF(s * 0.50, s * 0.74))
    p.drawLine(QPointF(s * 0.26, s * 0.50), QPointF(s * 0.74, s * 0.50))


def _g_search(p, c, s):
    p.setPen(_pen(c, s * 0.08))
    p.drawEllipse(QPointF(s * 0.44, s * 0.44), s * 0.20, s * 0.20)
    p.drawLine(QPointF(s * 0.59, s * 0.59), QPointF(s * 0.78, s * 0.78))


def _g_key(p, c, s):
    p.setPen(_pen(c, s * 0.075))
    p.drawEllipse(QPointF(s * 0.36, s * 0.36), s * 0.15, s * 0.15)
    p.drawLine(QPointF(s * 0.47, s * 0.47), QPointF(s * 0.78, s * 0.78))
    p.drawLine(QPointF(s * 0.64, s * 0.78), QPointF(s * 0.64, s * 0.66))
    p.drawLine(QPointF(s * 0.73, s * 0.78), QPointF(s * 0.73, s * 0.66))


def _g_doc(p, c, s):
    p.setPen(_pen(c, s * 0.07))
    p.drawRoundedRect(QRectF(s * 0.26, s * 0.18, s * 0.48, s * 0.64), s * 0.05, s * 0.05)
    for y in (0.34, 0.48, 0.62):
        p.drawLine(QPointF(s * 0.35, s * y), QPointF(s * 0.65, s * y))


def _g_tray(p, c, s):
    p.setPen(_pen(c, s * 0.075))
    p.drawLine(QPointF(s * 0.28, s * 0.62), QPointF(s * 0.28, s * 0.76))
    p.drawLine(QPointF(s * 0.72, s * 0.62), QPointF(s * 0.72, s * 0.76))
    p.drawLine(QPointF(s * 0.28, s * 0.76), QPointF(s * 0.72, s * 0.76))
    p.drawLine(QPointF(s * 0.50, s * 0.24), QPointF(s * 0.50, s * 0.58))
    p.drawLine(QPointF(s * 0.38, s * 0.47), QPointF(s * 0.50, s * 0.58))
    p.drawLine(QPointF(s * 0.62, s * 0.47), QPointF(s * 0.50, s * 0.58))


def _g_chart(p, c, s):
    p.setPen(_pen(c, s * 0.08))
    p.drawLine(QPointF(s * 0.24, s * 0.76), QPointF(s * 0.78, s * 0.76))
    for x, h in ((0.34, 0.24), (0.50, 0.40), (0.66, 0.18)):
        p.drawLine(QPointF(s * x, s * 0.76), QPointF(s * x, s * (0.76 - h)))


def _g_gear(p, c, s):
    import math

    p.setPen(_pen(c, s * 0.065))
    center = QPointF(s * 0.5, s * 0.5)
    p.drawEllipse(center, s * 0.13, s * 0.13)
    p.drawEllipse(center, s * 0.23, s * 0.23)
    for i in range(8):
        ang = math.pi * 2 * i / 8.0
        p1 = QPointF(center.x() + math.cos(ang) * s * 0.23, center.y() + math.sin(ang) * s * 0.23)
        p2 = QPointF(center.x() + math.cos(ang) * s * 0.34, center.y() + math.sin(ang) * s * 0.34)
        p.drawLine(p1, p2)


def _g_person(p, c, s):
    p.setPen(_pen(c, s * 0.075))
    p.drawEllipse(QPointF(s * 0.50, s * 0.34), s * 0.13, s * 0.13)
    p.drawArc(QRectF(s * 0.28, s * 0.52, s * 0.44, s * 0.40), 180 * 16, 180 * 16)


def _g_clock(p, c, s):
    p.setPen(_pen(c, s * 0.075))
    p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.27, s * 0.27)
    p.drawLine(QPointF(s * 0.50, s * 0.34), QPointF(s * 0.50, s * 0.52))
    p.drawLine(QPointF(s * 0.50, s * 0.52), QPointF(s * 0.63, s * 0.60))


def _g_bell(p, c, s):
    p.setPen(_pen(c, s * 0.07))
    p.drawArc(QRectF(s * 0.28, s * 0.22, s * 0.44, s * 0.48), 0 * 16, 180 * 16)
    p.drawLine(QPointF(s * 0.24, s * 0.70), QPointF(s * 0.76, s * 0.70))
    p.drawEllipse(QPointF(s * 0.50, s * 0.79), s * 0.04, s * 0.04)


def _g_more(p, c, s):
    p.setPen(_pen(c, s * 0.05))
    p.setBrush(QBrush(QColor(c)))
    for x in (0.30, 0.50, 0.70):
        p.drawEllipse(QPointF(s * x, s * 0.50), s * 0.045, s * 0.045)
    p.setBrush(Qt.NoBrush)


def _g_terminal(p, c, s):
    p.setPen(_pen(c, s * 0.08))
    p.drawPolyline(QPolygonF([
        QPointF(s * 0.26, s * 0.32),
        QPointF(s * 0.42, s * 0.50),
        QPointF(s * 0.26, s * 0.68),
    ]))
    p.drawLine(QPointF(s * 0.50, s * 0.68), QPointF(s * 0.74, s * 0.68))


def _g_calendar(p, c, s):
    p.setPen(_pen(c, s * 0.07))
    p.drawRoundedRect(QRectF(s * 0.24, s * 0.26, s * 0.52, s * 0.50), s * 0.05, s * 0.05)
    p.drawLine(QPointF(s * 0.24, s * 0.40), QPointF(s * 0.76, s * 0.40))
    p.drawLine(QPointF(s * 0.38, s * 0.20), QPointF(s * 0.38, s * 0.32))
    p.drawLine(QPointF(s * 0.62, s * 0.20), QPointF(s * 0.62, s * 0.32))


def _g_arrow_right(p, c, s):
    p.setPen(_pen(c, s * 0.08))
    p.drawLine(QPointF(s * 0.26, s * 0.50), QPointF(s * 0.68, s * 0.50))
    p.drawPolyline(QPolygonF([
        QPointF(s * 0.56, s * 0.38),
        QPointF(s * 0.68, s * 0.50),
        QPointF(s * 0.56, s * 0.62),
    ]))


GLYPHS = {
    "dashboard": _g_dashboard,
    "queue": _g_queue,
    "providers": _g_providers,
    "pulse": _g_pulse,
    "play": _g_play,
    "stop": _g_stop,
    "plus": _g_plus,
    "search": _g_search,
    "key": _g_key,
    "doc": _g_doc,
    "tray": _g_tray,
    "chart": _g_chart,
    "gear": _g_gear,
    "person": _g_person,
    "clock": _g_clock,
    "bell": _g_bell,
    "more": _g_more,
    "terminal": _g_terminal,
    "calendar": _g_calendar,
    "right": _g_arrow_right,
}


def studio_icon(glyph, size=48, accent=INDIGO, tile=True):
    """Render a Studio icon: optional soft tinted tile + stroke glyph."""
    draw = GLYPHS.get(glyph)
    if draw is None:
        return QIcon()
    pm = QPixmap(size, size)
    pm.fill(QColor("#00000000"))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    color = QColor(accent)
    if tile:
        tint = QColor(_TINTS.get(accent, "#f2f5fb"))
        inset = size * 0.04
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(tint))
        painter.drawRoundedRect(
            QRectF(inset, inset, size - 2 * inset, size - 2 * inset),
            size * 0.22, size * 0.22,
        )
    draw(painter, color, size)
    painter.end()
    return QIcon(pm)


def brand_pixmap(size=44):
    """Indigo rounded tile with a white stroke checkmark — brand mark."""
    pm = QPixmap(size, size)
    pm.fill(QColor("#00000000"))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(INDIGO)))
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.28, size * 0.28)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(size * 0.09)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline(QPolygonF([
        QPointF(size * 0.28, size * 0.52),
        QPointF(size * 0.44, size * 0.68),
        QPointF(size * 0.74, size * 0.34),
    ]))
    painter.end()
    return pm

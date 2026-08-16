# modern_widgets.py - Modern painted widgets (circular gauges, donut charts)
# Runtime QPainter widgets with no external dependencies.
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class GaugeMetric(QLabel):
    """Circular progress gauge that renders a ring behind the label text.

    Subclasses QLabel so all existing metric-label behavior (setText, rich
    text links, signal wiring) keeps working unchanged. The ring fill is
    driven separately with set_value(fraction).
    """

    RING_WIDTH = 10

    def __init__(self, caption="", accent="#4f46e5", parent=None):
        super().__init__(parent)
        self._fraction = 0.0
        self._accent = QColor(accent)
        self._track = QColor("#e8ecf3")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(104, 92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(94)

    def set_value(self, fraction):
        self._fraction = max(0.0, min(1.0, float(fraction or 0.0)))
        self.update()

    def value(self):
        return self._fraction

    def set_track_color(self, color):
        self._track = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(7, 7, -7, -7)
        side = min(rect.width(), rect.height())
        rect.setLeft(rect.center().x() - side / 2.0)
        rect.setRight(rect.center().x() + side / 2.0)
        rect.setTop(rect.center().y() - side / 2.0)
        rect.setBottom(rect.center().y() + side / 2.0)

        track = QPen(self._track)
        track.setWidthF(self.RING_WIDTH)
        painter.setPen(track)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(rect)

        if self._fraction > 0.0:
            fill = QPen(self._accent)
            fill.setWidthF(self.RING_WIDTH)
            fill.setCapStyle(Qt.RoundCap)
            painter.setPen(fill)
            span = -int(360.0 * 16 * self._fraction)
            painter.drawArc(rect, 90 * 16, span)
        painter.end()
        super().paintEvent(event)


class DonutChart(QWidget):
    """Segmented donut chart used to visualize answer outcome distribution."""

    RING_WIDTH = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments = []
        self._center = "0"
        self.setMinimumSize(160, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(152)

    def set_data(self, segments, center_text):
        self._segments = [
            (max(0.0, float(value)), QColor(color))
            for value, color in segments
            if float(value or 0.0) > 0.0
        ]
        self._center = str(center_text)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        side = min(rect.width(), rect.height())
        rect.setLeft(rect.center().x() - side / 2.0)
        rect.setRight(rect.center().x() + side / 2.0)
        rect.setTop(rect.center().y() - side / 2.0)
        rect.setBottom(rect.center().y() + side / 2.0)

        total = sum(value for value, _color in self._segments)
        if total <= 0.0:
            track = QPen(QColor("#e8ecf3"))
            track.setWidthF(self.RING_WIDTH)
            painter.setPen(track)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(rect)
        else:
            start = 90 * 16
            for value, color in self._segments:
                span = -int(360.0 * 16 * (value / total))
                pen = QPen(color)
                pen.setWidthF(self.RING_WIDTH)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(rect, start, span)
                start += span

        center_font = QFont()
        center_font.setPointSize(13)
        center_font.setBold(True)
        painter.setFont(center_font)
        painter.setPen(QColor("#1c2430"))
        painter.drawText(rect, Qt.AlignCenter, self._center)
        painter.end()
        super().paintEvent(event)


def legend_dot(color):
    """Return a small colored QLabel swatch used in chart legends."""
    dot = QLabel()
    dot.setFixedSize(10, 10)
    dot.setStyleSheet(f"background:{color}; border-radius:5px;")
    return dot

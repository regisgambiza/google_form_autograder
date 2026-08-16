# gui_studio/widgets.py - Custom painted and animated widgets for the Studio
# shell: progress ring, stage stepper, stat cards, pills, outcome bar, feed
# rows and queue rows.
from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from gui_studio import theme as T


def repolish(widget):
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class Pill(QLabel):
    """Small status pill driven by a `status` dynamic property."""

    def __init__(self, text="", status="queued", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Pill")
        self.setAlignment(Qt.AlignCenter)
        self.set_status(status)

    def set_status(self, status):
        self.setProperty("status", str(status))
        repolish(self)


class RingProgress(QWidget):
    """Circular progress meter with animated sweep and centered percent text."""

    def __init__(self, diameter=148, accent=T.INDIGO, parent=None):
        super().__init__(parent)
        self._diameter = diameter
        self._accent = accent
        self._fraction = 0.0
        self._display = 0.0
        self._label = ""
        self._sublabel = ""
        self.setFixedSize(diameter, diameter)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(450)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value):
        self._display = float(value)
        self.update()

    def get_fraction(self):
        return self._fraction

    def set_fraction(self, value):
        value = max(0.0, min(1.0, float(value or 0.0)))
        if abs(value - self._fraction) < 1e-6:
            return
        self._fraction = value
        self._anim.stop()
        self._anim.setStartValue(self._display)
        self._anim.setEndValue(value)
        self._anim.start()

    fraction = Property(float, get_fraction, set_fraction)

    def set_labels(self, label, sublabel=""):
        self._label = str(label or "")
        self._sublabel = str(sublabel or "")
        self.update()

    def set_accent(self, color):
        self._accent = color
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        h = self.height()
        stroke = max(8.0, self._diameter * 0.075)
        rect = QRectF(stroke / 2 + 2, stroke / 2 + 2, w - stroke - 4, h - stroke - 4)

        pen = QPen(QColor(T.TRACK))
        pen.setWidthF(stroke)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        if self._display > 0.001:
            pen.setColor(QColor(self._accent))
            painter.setPen(pen)
            span = int(self._display * 360 * 16)
            painter.drawArc(rect, 90 * 16, -span)

        painter.setPen(QColor(T.HEADING))
        font = QFont(self.font())
        font.setPointSizeF(max(13.0, self._diameter * 0.135))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect.adjusted(0, -self._diameter * 0.10, 0, 0),
                         Qt.AlignCenter, self._label)
        if self._sublabel:
            painter.setPen(QColor(T.MUTED))
            sfont = QFont(self.font())
            sfont.setPointSizeF(max(7.5, self._diameter * 0.075))
            sfont.setBold(True)
            painter.setFont(sfont)
            painter.drawText(rect.adjusted(0, self._diameter * 0.16, 0, 0),
                             Qt.AlignCenter, self._sublabel)
        painter.end()


class StageDot(QLabel):
    """Pulsing stage node dot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StageDot")
        self.setFixedSize(16, 16)
        self._pulse_on = False
        self._state = "todo"
        self._timer = QTimer(self)
        self._timer.setInterval(620)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        if self._state != "active":
            return
        self._pulse_on = not self._pulse_on
        self.setStyleSheet(
            f"background: {'#818cf8' if self._pulse_on else T.INDIGO};"
        )

    def set_state(self, state):
        if state == self._state:
            return
        self._state = str(state)
        self._pulse_on = False
        self.setStyleSheet("")
        if state == "active":
            self._timer.start()
        else:
            self._timer.stop()
        self.setProperty("state", state)
        repolish(self)


class StageStepper(QWidget):
    """Answer pipeline stepper: queued -> deterministic -> AI jury -> consensus -> applied."""

    STAGES = [
        ("queued", "Queued"),
        ("deterministic", "Deterministic"),
        ("ai", "AI jury"),
        ("consensus", "Consensus"),
        ("applied", "Applied"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)
        self._dots = {}
        self._names = {}
        self._counts = {}
        self._lines = {}
        for index, (key, label) in enumerate(self.STAGES):
            if index:
                line = QFrame()
                line.setObjectName("StageLine")
                line.setFrameShape(QFrame.HLine)
                line.setFixedHeight(3)
                line.setProperty("state", "todo")
                layout.addWidget(line, 1)
                self._lines[key] = line
            column = QVBoxLayout()
            column.setSpacing(2)
            dot = StageDot()
            name = QLabel(label)
            name.setObjectName("StageName")
            name.setAlignment(Qt.AlignHCenter)
            name.setProperty("state", "todo")
            count = QLabel("–")
            count.setObjectName("StageCount")
            count.setAlignment(Qt.AlignHCenter)
            count.setProperty("state", "todo")
            column.addWidget(dot, 0, Qt.AlignHCenter)
            column.addWidget(name)
            column.addWidget(count)
            layout.addLayout(column)
            self._dots[key] = dot
            self._names[key] = name
            self._counts[key] = count

    def set_states(self, states):
        """states: dict stage_key -> 'todo' | 'active' | 'done'."""
        for key, _label in self.STAGES:
            state = str(states.get(key, "todo"))
            self._dots[key].set_state(state)
            for widget in (self._names[key], self._counts[key]):
                widget.setProperty("state", state)
                repolish(widget)
            if key in self._lines:
                self._lines[key].setProperty("state", "done" if state == "done" else "todo")
                repolish(self._lines[key])

    def set_counts(self, counts):
        for key, _label in self.STAGES:
            value = counts.get(key)
            self._counts[key].setText("–" if value is None else str(value))


class StatCard(QFrame):
    def __init__(self, caption, value="–", sub="", accent=T.INDIGO, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.caption_label = QLabel(str(caption).upper())
        self.caption_label.setObjectName("CardCaption")
        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("StatValue")
        self.sub_label = QLabel(str(sub))
        self.sub_label.setObjectName("StatSub")
        layout.addWidget(self.caption_label)
        layout.addWidget(self.value_label)
        if sub:
            layout.addWidget(self.sub_label)
        else:
            self.sub_label.hide()
        self.value_label.setStyleSheet(f"color: {accent};")

    def set_value(self, value, sub=None):
        self.value_label.setText(str(value))
        if sub is not None:
            if self.sub_label.isHidden():
                self.layout().addWidget(self.sub_label)
                self.sub_label.show()
            self.sub_label.setText(str(sub))


class OutcomeBar(QWidget):
    """Animated stacked outcome bar (accepted / review / rejected)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(26)
        self._parts = []
        self._start_fracs = [0.0, 0.0, 0.0]
        self._target_fracs = [0.0, 0.0, 0.0]
        self._progress = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(500)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value):
        self._progress = float(value)
        self.update()

    def set_data(self, accepted, rejected, review):
        total = int(accepted + rejected + review)
        if total <= 0:
            self._parts = []
            self.update()
            return
        self._parts = [(accepted, T.GREEN), (rejected, T.RED), (review, T.ORANGE)]
        self._start_fracs = list(self._target_fracs)
        self._target_fracs = [accepted / total, rejected / total, review / total]
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        width = float(self.width())
        height = float(self.height())
        painter.setBrush(QColor(T.TRACK))
        painter.drawRoundedRect(QRectF(0, 4, width, height - 8), 6, 6)
        if not self._parts:
            painter.end()
            return
        x = 0.0
        for index, (_count, color) in enumerate(self._parts):
            start = self._start_fracs[index]
            end = self._target_fracs[index]
            frac = start + (end - start) * self._progress
            if frac <= 0.0001:
                continue
            seg_width = frac * width
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(x, 4, seg_width, height - 8), 6, 6)
            x += seg_width
        painter.end()


def legend_chip(color, text, parent=None):
    chip = QFrame(parent)
    chip.setStyleSheet("background: transparent; border: 0;")
    layout = QHBoxLayout(chip)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    dot = QLabel()
    dot.setFixedSize(8, 8)
    dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
    label = QLabel(text)
    label.setObjectName("LegendText")
    layout.addWidget(dot)
    layout.addWidget(label)
    return chip


def status_label(status):
    return {
        "queued": "QUEUED",
        "running": "RUNNING",
        "done": "DONE",
        "failed": "FAILED",
        "skipped": "SKIPPED",
        "partial": "PARTIAL",
    }.get(str(status), str(status).upper())


class QueueRow(QFrame):
    """Reworked queue board row: avatar chip, two-line text, slim progress, pill, ETA."""

    def __init__(self, meta, parent=None):
        super().__init__(parent)
        self.setObjectName("QueueRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        title = str(meta.get("title") or "Untitled")
        self.avatar = QLabel((title.strip()[:1] or "?").upper())
        self.avatar.setObjectName("QueueAvatar")
        self.avatar.setFixedSize(32, 32)
        self.avatar.setAlignment(Qt.AlignCenter)

        text_column = QVBoxLayout()
        text_column.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("QueueTitle")
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("QueueMeta")
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.meta_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("QueueProgress")
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(110)

        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("EtaLabel")
        self.percent_label.setFixedWidth(34)
        self.percent_label.setAlignment(Qt.AlignCenter)

        self.pill = Pill()
        self.eta_label = QLabel("--")
        self.eta_label.setObjectName("EtaLabel")
        self.eta_label.setFixedWidth(56)
        self.eta_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.avatar)
        layout.addLayout(text_column, 1)
        layout.addWidget(self.progress)
        layout.addWidget(self.percent_label)
        layout.addWidget(self.pill)
        layout.addWidget(self.eta_label)

    def update_meta(self, meta, percent, eta_text, detail_text, tooltip_text):
        status = str(meta.get("status", "queued"))
        self.setProperty("status", status)
        repolish(self)
        title = str(meta.get("title") or "Untitled")
        self.title_label.setText(title)
        self.title_label.setToolTip(title)
        self.avatar.setText((title.strip()[:1] or "?").upper())
        self.avatar.setProperty("status", status)
        repolish(self.avatar)
        self.meta_label.setText(detail_text)
        self.meta_label.setToolTip(tooltip_text)
        self.progress.setValue(int(percent))
        self.percent_label.setText(f"{int(percent)}%")
        self.pill.setText(status_label(status))
        self.pill.set_status(status)
        self.eta_label.setText(eta_text)


class FeedRow(QFrame):
    """Live answer feed row (structured from gui_terminal.jsonl events)."""

    def __init__(self, event, parent=None):
        super().__init__(parent)
        self.setObjectName("FeedRow")
        decision = str(event.get("decision", "REVIEW")).upper()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        badge_text = {
            "YES": "✓ ACCEPTED",
            "NO": "✗ REJECTED",
            "REVIEW": "? REVIEW",
            "ERROR": "! ERROR",
        }.get(decision, decision)
        badge = QLabel(badge_text)
        badge.setObjectName("FeedBadge")
        badge.setProperty("decision", decision)
        repolish(badge)

        column = QVBoxLayout()
        column.setSpacing(1)
        current = int(event.get("current", 0) or 0)
        total = int(event.get("total", 0) or 0)
        qnum = int(event.get("question_number", 0) or 0)
        confidence = float(event.get("confidence", 0.0) or 0.0) * 100
        head = QLabel(f"Answer {current}/{total} · Q{qnum} · {confidence:.0f}%")
        head.setObjectName("FeedTitle")
        question = str(event.get("question", ""))
        answer = str(event.get("answer", ""))
        sub = QLabel((question[:90] + "…") if len(question) > 90 else question)
        sub.setObjectName("FeedMeta")
        sub2 = QLabel((answer[:90] + "…") if len(answer) > 90 else answer)
        sub2.setObjectName("QueueMeta")
        column.addWidget(head)
        column.addWidget(sub)
        column.addWidget(sub2)

        judges = event.get("judges") or []
        side_text = f"{len(judges)} judge{'s' if len(judges) != 1 else ''}"
        elapsed = str(event.get("elapsed", "") or "")
        side = QLabel(side_text + (f"\n{elapsed}" if elapsed else ""))
        side.setObjectName("QueueMeta")
        side.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(badge)
        layout.addLayout(column, 1)
        layout.addWidget(side)
        self.setProperty("decision", decision)
        repolish(self)
        self.setToolTip(self._tooltip(event))

    @staticmethod
    def _tooltip(event):
        lines = [
            f"Q{event.get('question_number', '?')}: {event.get('question', '')}",
            f"Expected: {event.get('expected', '')}",
            f"Answer: {event.get('answer', '')}",
            f"Decision: {str(event.get('decision', '')).upper()} "
            f"({event.get('policy_reason', '')})",
        ]
        for judge in event.get("judges") or []:
            try:
                confidence = float(judge.get("confidence", 0.0) or 0.0) * 100
            except (TypeError, ValueError):
                confidence = 0.0
            lines.append(
                f"  • {judge.get('role', '?')} ({judge.get('model', '?')}): "
                f"{str(judge.get('decision', '')).upper()} "
                f"{confidence:.0f}% — {judge.get('reason', '')}"
            )
        return "\n".join(lines)


class SegmentedControl(QWidget):
    """Pill segmented control used by the Activity page."""

    def __init__(self, tabs, parent=None):
        super().__init__(parent)
        self.setObjectName("SegmentBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        self.buttons = {}
        for index, (key, label) in enumerate(tabs.items()):
            button = QPushButton(label)
            button.setObjectName("Segment")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            layout.addWidget(button)
            self.buttons[key] = button
            if index == 0:
                button.setChecked(True)

    def set_active(self, key):
        for button_key, button in self.buttons.items():
            button.setChecked(button_key == key)

    def get(self, key):
        return self.buttons.get(key)

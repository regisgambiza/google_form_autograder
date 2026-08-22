# gui_studio/pages.py - Content pages for the Studio shell. Pages are pure
# widgets; the main window owns all orchestration and drives them through
# the small public APIs defined here.
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_studio import theme as T
from gui_studio.widgets import (
    FeedRow,
    OutcomeBar,
    Pill,
    RingProgress,
    SegmentedControl,
    StageStepper,
    StatCard,
    legend_chip,
    repolish,
)

HEALTH_MAP = {
    "HEALTHY": ("online", "Online"),
    "DEGRADED": ("degraded", "Degraded"),
    "RATE_LIMITED": ("degraded", "Rate limited"),
    "RECOVERING": ("degraded", "Recovering"),
    "OUT_OF_CREDITS": ("offline", "Out of credits"),
    "OFFLINE": ("offline", "Offline"),
    "DISABLED": ("offline", "Disabled"),
}

MAX_FEED_ROWS = 400


def _card(margins=(16, 14, 16, 14), spacing=8):
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def _caption(text):
    label = QLabel(str(text).upper())
    label.setObjectName("CardCaption")
    return label


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class DashboardPage(QWidget):
    review_clicked = Signal()
    run_clicked = Signal()
    stop_clicked = Signal()
    add_sources_clicked = Signal()
    scan_clicked = Signal()
    schedule_clicked = Signal()
    open_activity_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)

        hero = QHBoxLayout()
        hero.setSpacing(14)
        hero.addWidget(self._build_run_card(), 3)
        hero.addLayout(self._build_side_column(), 2)
        root.addLayout(hero, 0)

        root.addWidget(self._build_console_card(), 1)

    # -- run card ----------------------------------------------------------
    def _build_run_card(self):
        card, layout = _card((20, 16, 20, 16), 10)
        self.run_card = card

        header = QHBoxLayout()
        header.addWidget(_caption("Grading run"))
        header.addStretch()
        self.run_pill = QFrame()
        self.run_pill.setObjectName("RunPill")
        self.run_pill.setProperty("state", "ready")
        pill_layout = QHBoxLayout(self.run_pill)
        pill_layout.setContentsMargins(10, 3, 12, 3)
        pill_layout.setSpacing(6)
        self.run_pill_dot = QLabel()
        self.run_pill_dot.setObjectName("StateDot")
        self.run_pill_dot.setProperty("state", "ready")
        self.run_pill_dot.setFixedSize(8, 8)
        self.run_pill_text = QLabel("Ready")
        self.run_pill_text.setObjectName("RunPillText")
        self.run_pill_text.setProperty("state", "ready")
        pill_layout.addWidget(self.run_pill_dot)
        pill_layout.addWidget(self.run_pill_text)
        header.addWidget(self.run_pill)
        layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(18)
        self.ring = RingProgress(diameter=150)
        self.ring.set_labels("0%", "ANSWERS")
        body.addWidget(self.ring, 0, Qt.AlignTop | Qt.AlignHCenter)

        info = QVBoxLayout()
        info.setSpacing(6)
        self.headline = QLabel("Ready to grade")
        self.headline.setObjectName("Headline")
        self.subline = QLabel("Add forms to the queue, then press Start grading.")
        self.subline.setObjectName("Subline")
        self.subline.setWordWrap(True)
        info.addWidget(self.headline)
        info.addWidget(self.subline)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.mode_chip = QFrame()
        self.mode_chip.setObjectName("ModeChip")
        mode_layout = QHBoxLayout(self.mode_chip)
        mode_layout.setContentsMargins(10, 2, 10, 2)
        self.mode_chip_text = QLabel("Whole Form")
        self.mode_chip_text.setObjectName("ModeChipText")
        mode_layout.addWidget(self.mode_chip_text)
        self.model_label = QLabel("Model: idle")
        self.model_label.setObjectName("Subline")
        chips.addWidget(self.mode_chip)
        chips.addWidget(self.model_label)
        chips.addStretch()
        info.addLayout(chips)

        forms_row = QVBoxLayout()
        forms_row.setSpacing(2)
        self.forms_progress_label = QLabel("Forms 0 of 0 · 0%")
        self.forms_progress_label.setObjectName("Subline")
        self.forms_progress = QProgressBar()
        self.forms_progress.setRange(0, 100)
        self.forms_progress.setTextVisible(False)
        forms_row.addWidget(self.forms_progress_label)
        forms_row.addWidget(self.forms_progress)
        info.addLayout(forms_row)

        eta_row = QHBoxLayout()
        eta_row.setSpacing(16)
        self.eta_label = QLabel("ETA --:--")
        self.elapsed_label = QLabel("Elapsed 00:00")
        for label in (self.eta_label, self.elapsed_label):
            label.setObjectName("Subline")
            eta_row.addWidget(label)
        eta_row.addStretch()
        info.addLayout(eta_row)
        info.addStretch()
        body.addLayout(info, 1)
        layout.addLayout(body)

        self.stepper = StageStepper()
        layout.addSpacing(4)
        layout.addWidget(self.stepper)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.run_button = QPushButton("Start grading")
        self.run_button.setObjectName("Primary")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("Danger")
        self.stop_button.hide()
        self.add_button = QPushButton("Add sources")
        self.scan_button = QPushButton("Scan source")
        self.schedule_button = QPushButton("Schedule runs")
        controls.addWidget(self.run_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.add_button)
        controls.addWidget(self.scan_button)
        controls.addWidget(self.schedule_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.run_button.clicked.connect(self.run_clicked)
        self.stop_button.clicked.connect(self.stop_clicked)
        self.add_button.clicked.connect(self.add_sources_clicked)
        self.scan_button.clicked.connect(self.scan_clicked)
        self.schedule_button.clicked.connect(self.schedule_clicked)
        return card

    # -- side column ---------------------------------------------------------
    def _build_side_column(self):
        column = QVBoxLayout()
        column.setSpacing(14)

        outcome, outcome_layout = _card((18, 14, 18, 14), 8)
        head = QHBoxLayout()
        head.addWidget(_caption("Outcome mix"))
        head.addStretch()
        self.outcome_legends = QHBoxLayout()
        self.outcome_legends.setSpacing(12)
        head.addLayout(self.outcome_legends)
        outcome_layout.addLayout(head)
        self.outcome_bar = OutcomeBar()
        outcome_layout.addWidget(self.outcome_bar)
        counts = QHBoxLayout()
        counts.setSpacing(10)
        self.accepted_card = StatCard("Accepted", "0", accent=T.GREEN)
        self.rejected_card = StatCard("Rejected", "0", accent=T.RED)
        self.review_card = StatCard("Needs review", "0", accent=T.ORANGE)
        self.review_card.setCursor(Qt.PointingHandCursor)
        self.review_card.setToolTip("Open the answer-key review queue")
        counts.addWidget(self.accepted_card)
        counts.addWidget(self.rejected_card)
        counts.addWidget(self.review_card)
        outcome_layout.addLayout(counts)
        self.review_card.mousePressEvent = self._review_pressed
        column.addWidget(outcome)

        metrics, metrics_layout = _card((18, 14, 18, 14), 10)
        metrics_layout.addWidget(_caption("Live metrics"))
        grid = QGridLayout()
        grid.setSpacing(10)
        self.rate_card = StatCard("Answers / min", "0.0", accent=T.INDIGO)
        self.backlog_card = StatCard("AI backlog", "0", accent=T.PURPLE)
        self.latency_card = StatCard("Avg latency", "–", accent=T.BLUE)
        self.pipeline_card = StatCard("Pipeline", "Idle", accent=T.TEAL)
        self.det_card = StatCard("Deterministic", "0", accent=T.SLATE)
        self.ai_card = StatCard("AI decisions", "0", accent=T.INDIGO)
        grid.addWidget(self.rate_card, 0, 0)
        grid.addWidget(self.backlog_card, 0, 1)
        grid.addWidget(self.latency_card, 1, 0)
        grid.addWidget(self.pipeline_card, 1, 1)
        grid.addWidget(self.det_card, 0, 2)
        grid.addWidget(self.ai_card, 1, 2)
        metrics_layout.addLayout(grid)
        column.addWidget(metrics)
        column.addStretch()
        return column

    def _review_pressed(self, _event):
        self.review_clicked.emit()

    # -- console card ---------------------------------------------------------
    def _build_console_card(self):
        card, layout = _card((16, 12, 16, 12), 8)
        self.console_card = card
        head = QHBoxLayout()
        head.addWidget(_caption("Live console"))
        head.addStretch()
        self.console_badge = QLabel("idle")
        self.console_badge.setObjectName("CardCaption")
        head.addWidget(self.console_badge)
        self.console_open_button = QPushButton("Open activity →")
        self.console_open_button.setObjectName("Ghost")
        self.console_open_button.setProperty("noAutoIcon", True)
        head.addWidget(self.console_open_button)
        layout.addLayout(head)
        self.console = QTextEdit()
        self.console.setObjectName("ConsoleEdit")
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(900)
        layout.addWidget(self.console, 1)
        self.console_open_button.clicked.connect(self.open_activity_clicked)
        return card

    # -- public API -----------------------------------------------------------
    def set_run_pill(self, text, state):
        self.run_pill_text.setText(str(text))
        self.run_pill.setProperty("state", state)
        self.run_pill_dot.setProperty("state", state)
        self.run_pill_text.setProperty("state", state)
        repolish(self.run_pill)
        repolish(self.run_pill_dot)
        repolish(self.run_pill_text)

    def set_headline(self, text):
        self.headline.setText(str(text))

    def set_subline(self, text):
        self.subline.setText(str(text))

    def set_mode_chip(self, text):
        self.mode_chip_text.setText(str(text))

    def set_model(self, text, tooltip=""):
        self.model_label.setText(f"Model: {text}")
        self.model_label.setToolTip(tooltip or text)

    def set_answer_progress(self, current, total):
        fraction = (current / total) if total else 0.0
        self.ring.set_fraction(fraction)
        self.ring.set_labels(f"{int(round(fraction * 100))}%", "ANSWERS")

    def set_forms_progress(self, done, total):
        percent = int(round((done / total) * 100)) if total else 0
        self.forms_progress.setValue(percent)
        self.forms_progress_label.setText(f"Forms {done} of {total} · {percent}%")

    def set_eta(self, text):
        self.eta_label.setText(f"ETA {text}")

    def set_elapsed(self, text):
        self.elapsed_label.setText(f"Elapsed {text}")

    def set_stage_states(self, states):
        self.stepper.set_states(states)

    def set_stage_counts(self, counts):
        self.stepper.set_counts(counts)

    def set_outcomes(self, accepted, rejected, review):
        self.outcome_bar.set_data(accepted, rejected, review)
        self.accepted_card.set_value(int(accepted))
        self.rejected_card.set_value(int(rejected))
        self.review_card.set_value(int(review))
        while self.outcome_legends.count():
            item = self.outcome_legends.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for label, count, color in (
            ("Accepted", accepted, T.GREEN),
            ("Rejected", rejected, T.RED),
            ("Review", review, T.ORANGE),
        ):
            self.outcome_legends.addWidget(legend_chip(color, f"{label} {int(count)}"))

    def set_metrics(self, rate=None, backlog=None, latency=None, pipeline=None,
                    det=None, ai=None):
        """Update live-metric cards. Only the values explicitly passed are
        changed; None leaves a card untouched (partial updates are frequent)."""
        if rate is not None:
            self.rate_card.set_value(rate)
        if backlog is not None:
            self.backlog_card.set_value(backlog)
        if latency is not None:
            self.latency_card.set_value(latency)
        if pipeline is not None:
            self.pipeline_card.set_value(pipeline)
        if det is not None:
            self.det_card.set_value(det)
        if ai is not None:
            self.ai_card.set_value(ai)

    def set_running(self, running):
        self.run_button.setVisible(not running)
        self.stop_button.setVisible(running)

    def append_console(self, html):
        self.console.append(html)

    def clear_console(self):
        self.console.clear()

    def set_console_badge(self, text):
        self.console_badge.setText(str(text))


# ---------------------------------------------------------------------------
# Queue (dense classic table)
# ---------------------------------------------------------------------------
QUEUE_COLUMNS = ["Form", "Status", "Progress", "Answers", "Accepted",
                 "Rejected", "Review", "Time Left", "Last Activity", "Source"]


class QueueTablePage(QWidget):
    add_sources_clicked = Signal()
    scan_clicked = Signal()
    clear_all_clicked = Signal()
    clear_done_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search forms…  (Ctrl+K)")
        self.search_input.setFixedWidth(240)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Running", "Queued", "Done", "Partial", "Skipped", "Failed"])
        self.filter_combo.setFixedWidth(110)
        self.summary_label = QLabel("0 forms")
        self.summary_label.setObjectName("Subline")
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.filter_combo)
        toolbar.addWidget(self.summary_label)
        toolbar.addStretch()
        add_button = QPushButton("Add Sources")
        add_button.clicked.connect(self.add_sources_clicked)
        scan_button = QPushButton("Scan Source")
        scan_button.clicked.connect(self.scan_clicked)
        clear_done_button = QPushButton("Clear Completed")
        clear_done_button.clicked.connect(self.clear_done_clicked)
        clear_all_button = QPushButton("Clear All")
        clear_all_button.setObjectName("Danger")
        clear_all_button.clicked.connect(self.clear_all_clicked)
        for button in (add_button, scan_button, clear_done_button, clear_all_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(QUEUE_COLUMNS))
        self.table.setObjectName("QueueTable")
        self.table.setHorizontalHeaderLabels(QUEUE_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setShowGrid(True)
        self.table.setSortingEnabled(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        for column, width in ((0, 340), (1, 90), (2, 130), (3, 90), (4, 80),
                              (5, 80), (6, 70), (7, 90), (8, 120), (9, 110)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

    def set_summary(self, text, tooltip=""):
        self.summary_label.setText(str(text))
        self.summary_label.setToolTip(tooltip)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class ProviderCard(QFrame):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setObjectName("ProviderCard")
        self.setProperty("health", "unknown")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel(name)
        title.setObjectName("ProviderName")
        head.addWidget(title)
        head.addStretch()
        self.tag = QLabel("Unknown")
        self.tag.setObjectName("HealthTag")
        self.tag.setProperty("state", "unknown")
        head.addWidget(self.tag)
        layout.addLayout(head)
        self.rows = {}
        for key in ("Health", "Circuit", "Queue", "Done / failed",
                    "Last model", "Last latency", "Last error"):
            row = QHBoxLayout()
            key_label = QLabel(key)
            key_label.setObjectName("ModelKey")
            value = QLabel("–")
            value.setObjectName("ModelVal")
            value.setWordWrap(False)
            row.addWidget(key_label, 0)
            row.addStretch()
            row.addWidget(value, 1)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            layout.addLayout(row)
            self.rows[key] = value

    def set_info(self, info):
        health_raw = str(info.get("health", "-"))
        state, label = HEALTH_MAP.get(health_raw, ("unknown", health_raw.title()))
        self.tag.setText(label)
        self.tag.setProperty("state", state)
        repolish(self.tag)
        self.setProperty("health", state)
        repolish(self)
        self.rows["Health"].setText(health_raw.title() if health_raw != "-" else "–")
        self.rows["Circuit"].setText(str(info.get("circuit", "–")))
        self.rows["Queue"].setText(str(info.get("queue", "–")))
        done = info.get("done", 0)
        failed = info.get("failed", 0)
        self.rows["Done / failed"].setText(f"{done} / {failed}")
        model = str(info.get("last_model", "–"))
        self.rows["Last model"].setText(model if model != "-" else "–")
        self.rows["Last model"].setToolTip(model)
        self.rows["Last latency"].setText(f"{info.get('last_ms', 0)} ms")
        error = str(info.get("last_error", "–"))
        self.rows["Last error"].setText(error if error != "-" else "–")
        self.rows["Last error"].setToolTip(error)


class WorkerChip(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkerChip")
        self.setProperty("status", "idle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        head = QHBoxLayout()
        name = QLabel(title)
        name.setObjectName("WorkerTitle")
        head.addWidget(name)
        head.addStretch()
        self.state = QLabel("Idle")
        self.state.setObjectName("WorkerState")
        self.state.setProperty("status", "idle")
        head.addWidget(self.state)
        layout.addLayout(head)
        self.detail = QLabel("Waiting")
        self.detail.setObjectName("WorkerDetail")
        self.detail.setWordWrap(False)
        layout.addWidget(self.detail)

    def set_info(self, status, detail, tooltip=""):
        status = str(status or "idle").lower()
        self.state.setText(status.title())
        self.state.setProperty("status", status)
        repolish(self.state)
        self.setProperty("status", status)
        repolish(self)
        self.detail.setText(str(detail))
        self.detail.setToolTip(tooltip or str(detail))


class ProvidersPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        host = QWidget()
        scroll.setWidget(host)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        providers_row = QHBoxLayout()
        providers_row.setSpacing(14)
        self.provider_cards = {}
        for name, label in (
            ("openrouter", "OpenRouter"),
            ("llamacpp", "llama.cpp"),
            ("ollama", "Ollama"),
        ):
            card = ProviderCard(label)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            providers_row.addWidget(card, 1)
            self.provider_cards[name] = card
        layout.addLayout(providers_row)

        model_card, model_layout = _card((18, 14, 18, 14), 6)
        model_layout.addWidget(_caption("Active model"))
        self.active_model = QLabel("Idle")
        self.active_model.setObjectName("Headline")
        self.active_model.setWordWrap(True)
        model_layout.addWidget(self.active_model)
        self.model_sub = QLabel("Reported by the grading pipeline heartbeat.")
        self.model_sub.setObjectName("Subline")
        model_layout.addWidget(self.model_sub)
        layout.addWidget(model_card)

        workers_card, workers_layout = _card((18, 14, 18, 14), 10)
        workers_head = QHBoxLayout()
        workers_head.addWidget(_caption("Workers"))
        workers_head.addStretch()
        self.app_summary = QLabel("App AI workers: –")
        self.app_summary.setObjectName("Subline")
        self.provider_summary = QLabel("Providers: –")
        self.provider_summary.setObjectName("Subline")
        workers_head.addWidget(self.app_summary)
        workers_head.addSpacing(14)
        workers_head.addWidget(self.provider_summary)
        workers_layout.addLayout(workers_head)
        # Worker chips are grouped into per-provider sections (provider pools
        # first, then app lanes) instead of one flat grid, so different worker
        # families never interleave regardless of telemetry arrival order.
        self._worker_sections_host = QVBoxLayout()
        self._worker_sections_host.setSpacing(10)
        workers_layout.addLayout(self._worker_sections_host)
        self._worker_columns = 3
        self._worker_chips = {}
        self._worker_groups = {}
        self._sections = {}
        layout.addWidget(workers_card)

        health_card, health_layout = _card((18, 14, 18, 14), 6)
        health_layout.addWidget(_caption("Model health"))
        self.model_rows = {}
        for key, title in (
            ("current", "Current models"),
            ("success", "Success / latency"),
            ("limits", "Rate limits / failures"),
            ("json", "JSON reliability"),
            ("quality", "Ollama quality"),
            ("cooldown", "Cooldown"),
            ("cost", "Cost"),
            ("reason", "Why chosen"),
        ):
            row = QHBoxLayout()
            key_label = QLabel(title)
            key_label.setObjectName("ModelKey")
            key_label.setMinimumWidth(140)
            value = QLabel("–")
            value.setObjectName("ModelVal")
            value.setWordWrap(True)
            row.addWidget(key_label)
            row.addWidget(value, 1)
            health_layout.addLayout(row)
            self.model_rows[key] = value
        layout.addWidget(health_card)
        layout.addStretch()

    # Worker sections in fixed display order; empty sections stay hidden.
    WORKER_GROUP_ORDER = (
        ("openrouter", "OpenRouter"),
        ("llamacpp", "llama.cpp"),
        ("ollama", "Ollama"),
        ("app_openrouter", "App · OpenRouter lane"),
        ("app_llamacpp", "App · llama.cpp lane"),
        ("app_generic", "App AI workers"),
        ("other", "Other"),
    )

    @staticmethod
    def _group_for(worker_id):
        wid = str(worker_id)
        for lane in ("openrouter", "llamacpp", "ollama"):
            if wid.startswith(f"ai-{lane}-"):
                return f"app_{lane}"
        if wid.startswith("ai-"):
            return "app_generic"
        for provider in ("openrouter", "llamacpp", "ollama"):
            if wid.startswith(f"{provider}-"):
                return provider
        return "other"

    @staticmethod
    def _worker_sort_key(worker_id):
        try:
            return (0, int(str(worker_id).rsplit("-", 1)[-1]))
        except (TypeError, ValueError):
            return (1, str(worker_id))

    def _section_title(self, group):
        return dict(self.WORKER_GROUP_ORDER).get(group, str(group).title())

    def _ensure_section(self, group):
        section = self._sections.get(group)
        if section:
            return section
        host = QWidget()
        inner = QVBoxLayout(host)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(6)
        caption = _caption(self._section_title(group))
        grid = QGridLayout()
        grid.setSpacing(8)
        inner.addWidget(caption)
        inner.addLayout(grid)
        self._worker_sections_host.addWidget(host)
        section = {"caption": caption, "grid": grid, "host": host}
        self._sections[group] = section
        # Keep sections in WORKER_GROUP_ORDER even when created lazily.
        ordered = sorted(
            self._sections.items(),
            key=lambda kv: [name for name, _ in self.WORKER_GROUP_ORDER].index(kv[0]),
        )
        for _, other in ordered:
            self._worker_sections_host.removeWidget(other["host"])
        for _, other in ordered:
            self._worker_sections_host.addWidget(other["host"])
        return section

    def _refresh_group_caption(self, group):
        section = self._sections.get(group)
        if not section:
            return
        count = sum(1 for g in self._worker_groups.values() if g == group)
        suffix = f" · {count}" if count else ""
        section["caption"].setText(f"{self._section_title(group)}{suffix}".upper())

    # -- API --------------------------------------------------------------
    def set_provider(self, name, info):
        card = self.provider_cards.get(name)
        if card:
            card.set_info(info)

    def set_active_model(self, text, tooltip=""):
        self.active_model.setText(str(text))
        self.active_model.setToolTip(tooltip or str(text))

    def set_active_model_sub(self, text):
        self.model_sub.setText(str(text))

    def add_worker_chip(self, worker_id, title):
        if worker_id in self._worker_chips:
            return self._worker_chips[worker_id]
        group = self._group_for(worker_id)
        section = self._ensure_section(group)
        chip = WorkerChip(title)
        row, col = divmod(section["grid"].count(), self._worker_columns)
        section["grid"].addWidget(chip, row, col)
        section["host"].setVisible(True)
        self._worker_chips[worker_id] = chip
        self._worker_groups[worker_id] = group
        self._refresh_group_caption(group)
        return chip

    def set_worker_chip(self, worker_id, status, detail, tooltip=""):
        chip = self._worker_chips.get(worker_id)
        if chip:
            chip.set_info(status, detail, tooltip)

    def remove_worker_chip(self, worker_id):
        chip = self._worker_chips.pop(worker_id, None)
        group = self._worker_groups.pop(worker_id, None)
        if not chip:
            return
        section = self._sections.get(group or "")
        if section:
            section["grid"].removeWidget(chip)
        chip.setParent(None)
        chip.deleteLater()
        self._rebuild_worker_grid()

    def _rebuild_worker_grid(self):
        for group, section in list(self._sections.items()):
            members = sorted(
                (wid for wid, g in self._worker_groups.items() if g == group),
                key=self._worker_sort_key,
            )
            grid = section["grid"]
            while grid.count():
                item = grid.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    grid.removeWidget(widget)
                    widget.setParent(None)
            for index, wid in enumerate(members):
                row, col = divmod(index, self._worker_columns)
                grid.addWidget(self._worker_chips[wid], row, col)
            section["host"].setVisible(bool(members))
            self._refresh_group_caption(group)

    def set_worker_summaries(self, app_text, provider_text):
        self.app_summary.setText(app_text)
        self.provider_summary.setText(provider_text)

    def set_model_health(self, key, text, tooltip=None):
        row = self.model_rows.get(key)
        if row:
            row.setText(str(text))
            row.setToolTip(str(tooltip or text or ""))


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------
class ActivityPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.segments = SegmentedControl({
            "feed": "Answer feed",
            "console": "AI grading",
            "pipeline": "Pipeline (–)",
            "det": "Deterministic (–)",
            "ai": "AI workers (–)",
            "providers": "Providers (–)",
            "agg": "Aggregator (–)",
        })
        layout.addWidget(self.segments)

        self.stack = QStackedWidget()

        self.feed_list = QListWidget()
        self.feed_list.setObjectName("FeedList")
        self.stack.addWidget(self.feed_list)

        self.console = self._make_console()
        self.pipeline_output = self._make_console()
        self.det_output = self._make_console()
        self.ai_output = self._make_console()
        self.provider_output = self._make_console()
        self.agg_output = self._make_console()
        for widget in (self.console, self.pipeline_output, self.det_output,
                       self.ai_output, self.provider_output, self.agg_output):
            self.stack.addWidget(widget)

        layout.addWidget(self.stack, 1)

        order = ["feed", "console", "pipeline", "det", "ai", "providers", "agg"]
        for index, key in enumerate(order):
            self.segments.get(key).clicked.connect(
                lambda _checked=False, idx=index: self.stack.setCurrentIndex(idx)
            )

    @staticmethod
    def _make_console():
        widget = QTextEdit()
        widget.setObjectName("ConsoleEdit")
        widget.setReadOnly(True)
        widget.document().setMaximumBlockCount(1200)
        return widget

    # -- API --------------------------------------------------------------
    def append_console(self, html):
        self.console.append(html)

    def append_pipeline(self, text):
        self.pipeline_output.append(text)

    def append_det(self, text):
        self.det_output.append(text)

    def append_ai(self, text):
        self.ai_output.append(text)

    def append_provider(self, text):
        self.provider_output.append(text)

    def append_agg(self, text):
        self.agg_output.append(text)

    def route_raw(self, text):
        """Route worker-tagged diagnostic lines (same rules as the old tabs)."""
        if "[Worker: Producer]" in text:
            self.append_pipeline(text)
        if "[Worker: Deterministic]" in text:
            self.append_det(text)
        if "[Worker: AI]" in text or "[APP WORKER]" in text:
            self.append_ai(text)
        if "[PROVIDER " in text or "[PROVIDER]" in text:
            self.append_provider(text)
        if "[Worker: Aggregator]" in text:
            self.append_agg(text)
        if "[DISPATCH METRICS]" in text or "[DISPATCH]" in text:
            self.append_pipeline(text)
            self.append_det(text)
            self.append_ai(text)
            self.append_agg(text)

    def clear_all(self):
        for widget in (self.console, self.pipeline_output, self.det_output,
                       self.ai_output, self.provider_output, self.agg_output):
            widget.clear()
        self.feed_list.clear()

    def set_badge(self, key, text):
        labels = {
            "pipeline": "Pipeline",
            "det": "Deterministic",
            "ai": "AI workers",
            "providers": "Providers",
            "agg": "Aggregator",
        }
        if key in labels:
            self.segments.get(key).setText(f"{labels[key]} ({text})")

    def add_answer_row(self, event):
        row = FeedRow(event)
        item = QListWidgetItem(self.feed_list)
        item.setSizeHint(row.sizeHint())
        self.feed_list.insertItem(0, item)
        self.feed_list.setItemWidget(item, row)
        while self.feed_list.count() > MAX_FEED_ROWS:
            self.feed_list.takeItem(self.feed_list.count() - 1)

    def add_info_row(self, glyph, title, sub="", tone="neutral"):
        """Generic feed row for run lifecycle events (start/complete/skipped)."""
        colors = {
            "good": (T.GREEN, T.GREEN_TINT, T.GREEN_TEXT),
            "warn": (T.ORANGE, T.ORANGE_TINT, T.ORANGE_TEXT),
            "bad": (T.RED, T.RED_TINT, T.RED_TEXT),
            "neutral": (T.INDIGO, T.INDIGO_TINT, T.INDIGO_DARK),
        }
        accent, tint, text_color = colors.get(tone, colors["neutral"])
        row = QFrame()
        row.setObjectName("FeedRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        badge = QLabel(str(glyph))
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(72, 22)
        badge.setStyleSheet(
            f"background: {tint}; color: {text_color}; border-radius: 10px;"
            "font-size: 8pt; font-weight: 800;"
        )
        column = QVBoxLayout()
        column.setSpacing(1)
        head = QLabel(title)
        head.setObjectName("FeedTitle")
        detail = QLabel(sub)
        detail.setObjectName("FeedMeta")
        column.addWidget(head)
        if sub:
            column.addWidget(detail)
        layout.addWidget(badge)
        layout.addLayout(column, 1)
        item = QListWidgetItem(self.feed_list)
        item.setSizeHint(row.sizeHint())
        self.feed_list.insertItem(0, item)
        self.feed_list.setItemWidget(item, row)
        while self.feed_list.count() > MAX_FEED_ROWS:
            self.feed_list.takeItem(self.feed_list.count() - 1)

    def goto_feed(self):
        self.segments.set_active("feed")
        self.stack.setCurrentIndex(0)

    def goto_console(self):
        self.segments.set_active("console")
        self.stack.setCurrentIndex(1)


# ---------------------------------------------------------------------------
# Drive folders
# ---------------------------------------------------------------------------
def _extract_folder_id(url):
    """Pull the folder id out of a Drive folder URL (query params ignored)."""
    if not isinstance(url, str) or "/folders/" not in url:
        return None
    tail = url.split("/folders/", 1)[1]
    return tail.split("/")[0].split("?")[0].split("#")[0].strip() or None


class DriveFoldersPage(QWidget):
    """Whole-Drive folder picker: scan Drive, tick folders, apply to auto-run."""

    scan_requested = Signal()
    apply_clicked = Signal(list)  # list of Drive folder URLs

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checked_ids = set()
        self._populated = False
        self._building = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        card, layout = _card((20, 16, 20, 16), 10)
        layout.addWidget(_caption("Google Drive folders"))
        head = QHBoxLayout()
        head.setSpacing(8)
        intro = QLabel(
            "Scan your whole Drive, then tick the folders and subfolders that "
            "auto-run should watch. Applying also updates the predefined sources "
            "used by Grade All."
        )
        intro.setObjectName("Subline")
        intro.setWordWrap(True)
        head.addWidget(intro, 1)
        self.scan_button = QPushButton("Scan Drive")
        self.scan_button.setObjectName("Primary")
        self.scan_button.clicked.connect(self.scan_requested)
        head.addWidget(self.scan_button)
        layout.addLayout(head)

        self.status_label = QLabel("Not scanned yet — click Scan Drive.")
        self.status_label.setObjectName("Subline")
        layout.addWidget(self.status_label)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter folders…")
        self.filter_input.setFixedWidth(220)
        self.filter_input.textChanged.connect(self._apply_filter)
        self.select_all_button = QPushButton("Select All")
        self.select_all_button.clicked.connect(self._select_all)
        self.clear_button = QPushButton("Clear Selection")
        self.clear_button.clicked.connect(self._clear_selection)
        self.apply_button = QPushButton("Apply to Auto Run")
        self.apply_button.setObjectName("Primary")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._emit_apply)
        self.count_label = QLabel("0 of 0 folders selected")
        self.count_label.setObjectName("Subline")
        toolbar.addWidget(self.filter_input)
        toolbar.addWidget(self.select_all_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addStretch()
        toolbar.addWidget(self.count_label)
        toolbar.addWidget(self.apply_button)
        layout.addLayout(toolbar)
        root.addWidget(card)

        self.folder_tree = QTreeWidget()
        self.folder_tree.setObjectName("FolderTree")
        self.folder_tree.setHeaderLabel("Folder")
        self.folder_tree.setColumnCount(1)
        self.folder_tree.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.folder_tree, 1)

    # -- API --------------------------------------------------------------
    def set_scan_state(self, text, scanning=False):
        self.status_label.setText(str(text))
        self.scan_button.setText("Rescan Drive" if self._populated else "Scan Drive")
        self.scan_button.setEnabled(not scanning)
        self.apply_button.setEnabled(self._populated and not scanning)

    def populate_tree(self, nodes):
        """Rebuild the checkbox tree from scan nodes ({id,name,parent_id,root})."""
        self._building = True
        try:
            self.filter_input.blockSignals(True)
            self.filter_input.clear()
            self.filter_input.blockSignals(False)
            self.folder_tree.clear()
            by_id = {node["id"]: node for node in nodes}
            children = {}
            for node in nodes:
                children.setdefault(node.get("parent_id"), []).append(node)

            group_order = ["My Drive", "Shared with me"]
            groups = {}
            roots = [node for node in nodes if not node.get("parent_id")]
            for node in roots:
                label = node.get("root") or "Shared with me"
                groups.setdefault(label, []).append(node)
            group_order += sorted(label for label in groups if label not in group_order)

            for label in group_order:
                members = groups.get(label)
                if not members:
                    continue
                group_item = QTreeWidgetItem(self.folder_tree, [label])
                group_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                group_item.setCheckState(0, Qt.Unchecked)
                for node in members:
                    self._add_node(group_item, node, children)
                self.folder_tree.expandItem(group_item)
        finally:
            self._building = False
        self._populated = True
        self._refresh_counts()
        self.set_scan_state(f"{len(by_id)} folder(s) found. Tick the ones auto-run should scan.")

    def set_selected(self, urls):
        """Pre-check folders from a list of folder URLs (matched by id)."""
        self._checked_ids = {
            fid for fid in (_extract_folder_id(url) for url in (urls or [])) if fid
        }
        if not self._populated:
            return
        self._building = True
        try:
            for item in self._iter_folder_items():
                fid = item.data(0, Qt.UserRole)
                item.setCheckState(0, Qt.Checked if fid in self._checked_ids else Qt.Unchecked)
        finally:
            self._building = False
        self._refresh_counts()

    def selected_urls(self):
        from gui_studio.drive_folders import folder_url

        return [
            folder_url(item.data(0, Qt.UserRole))
            for item in self._iter_folder_items()
            if item.checkState(0) == Qt.Checked
        ]

    # -- internals ----------------------------------------------------------
    def _add_node(self, parent_item, node, children):
        item = QTreeWidgetItem(parent_item, [node.get("name", "Untitled")])
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setData(0, Qt.UserRole, node["id"])
        item.setCheckState(0, Qt.Checked if node["id"] in self._checked_ids else Qt.Unchecked)
        for child in children.get(node["id"], []):
            self._add_node(item, child, children)
        return item

    def _iter_folder_items(self):
        stack = []
        root = self.folder_tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                stack.append(group.child(j))
        while stack:
            item = stack.pop()
            yield item
            for i in range(item.childCount()):
                stack.append(item.child(i))

    def _on_item_changed(self, item, column):
        if self._building or column != 0:
            return
        # Parent toggled -> push state to every descendant.
        if item.data(0, Qt.UserRole) is None:
            state = item.checkState(0)
            self._building = True
            try:
                for i in range(item.childCount()):
                    self._set_subtree(item.child(i), state)
            finally:
                self._building = False
            self._refresh_counts()
            return
        # Folder toggled -> recompute ancestor group check states.
        self._building = True
        try:
            parent = item.parent()
            while parent is not None:
                total = parent.childCount()
                checked = sum(
                    1 for i in range(total) if parent.child(i).checkState(0) == Qt.Checked
                )
                parent.setCheckState(
                    0, Qt.Checked if checked == total else (Qt.PartiallyChecked if checked else Qt.Unchecked)
                )
                parent = parent.parent()
        finally:
            self._building = False
        self._refresh_counts()

    def _set_subtree(self, item, state):
        item.setCheckState(0, state)
        for i in range(item.childCount()):
            self._set_subtree(item.child(i), state)

    def _apply_filter(self, text):
        """Hide folders that don't match the filter (ancestors stay visible)."""
        query = str(text or "").strip().lower()
        root = self.folder_tree.invisibleRootItem()
        if not query:
            for i in range(root.childCount()):
                group = root.child(i)
                group.setHidden(False)
                for j in range(group.childCount()):
                    self._set_subtree_hidden(group.child(j), False)
            return

        def set_visible(item):
            visible = query in item.text(0).lower()
            for i in range(item.childCount()):
                if set_visible(item.child(i)):
                    visible = True
            item.setHidden(not visible)
            return visible

        for i in range(root.childCount()):
            group = root.child(i)
            group_visible = False
            for j in range(group.childCount()):
                if set_visible(group.child(j)):
                    group_visible = True
            group.setHidden(not group_visible)

    def _set_subtree_hidden(self, item, hidden):
        item.setHidden(hidden)
        for i in range(item.childCount()):
            self._set_subtree_hidden(item.child(i), hidden)

    def _select_all(self):
        if not self._populated:
            return
        self._building = True
        try:
            root = self.folder_tree.invisibleRootItem()
            for i in range(root.childCount()):
                root.child(i).setCheckState(0, Qt.Checked)
                for j in range(root.child(i).childCount()):
                    self._set_subtree(root.child(i).child(j), Qt.Checked)
        finally:
            self._building = False
        self._refresh_counts()

    def _clear_selection(self):
        if not self._populated:
            return
        self._building = True
        try:
            root = self.folder_tree.invisibleRootItem()
            for i in range(root.childCount()):
                root.child(i).setCheckState(0, Qt.Unchecked)
                for j in range(root.child(i).childCount()):
                    self._set_subtree(root.child(i).child(j), Qt.Unchecked)
        finally:
            self._building = False
        self._refresh_counts()

    def _refresh_counts(self):
        items = list(self._iter_folder_items())
        checked = sum(1 for item in items if item.checkState(0) == Qt.Checked)
        self.count_label.setText(f"{checked} of {len(items)} folders selected")

    def _emit_apply(self):
        self.apply_clicked.emit(self.selected_urls())

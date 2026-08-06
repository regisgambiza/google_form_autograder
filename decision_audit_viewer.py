# decision_audit_viewer.py - Browse grading decision audit records
import json
import os
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QTextEdit,
    QFileDialog,
    QMessageBox,
    QHeaderView,
)

from app_theme import apply_widget_theme

DEFAULT_AUDIT_PATH = "logs/grading_decisions.jsonl"


def load_audit_records(path=DEFAULT_AUDIT_PATH):
    """Load grading decision audit records from a JSONL file (newest first)."""
    records = []
    target = Path(path)
    if not target.exists():
        return records
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    records.reverse()
    return records


def _truncate(value, limit=80):
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _question_label(record):
    evidence = record.get("evidence") or {}
    question = evidence.get("question") or ""
    if not question:
        return "-"
    first = question.splitlines()[0] if question else ""
    return _truncate(first, 90)


class DecisionAuditViewer(QDialog):
    def __init__(self, path=DEFAULT_AUDIT_PATH, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Decision Audit Viewer")
        self.resize(980, 640)
        self.setMinimumSize(760, 480)
        self.path = path
        self.records = load_audit_records(path)
        self._build_ui()
        self._reload_table()
        apply_widget_theme(self)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        toolbar = QHBoxLayout()
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All decisions", "YES (accepted)", "NO (rejected)", "REVIEW"])
        self.filter_combo.currentTextChanged.connect(self._reload_table)
        toolbar.addWidget(self.filter_combo)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search answers or questions...")
        self.search_input.textChanged.connect(self._reload_table)
        toolbar.addWidget(self.search_input, 1)

        count_label = QLabel("")
        count_label.setObjectName("Muted")
        self.count_label = count_label
        toolbar.addWidget(count_label)

        export_btn = QPushButton("Export CSV...")
        export_btn.clicked.connect(self._export_csv)
        toolbar.addWidget(export_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(refresh_btn)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Vertical)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Decision", "Score", "Confidence", "Answer", "Question"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_details)
        splitter.addWidget(self.table)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(180)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, alignment=Qt.AlignRight)

    def _filtered_records(self):
        decision_filter = self.filter_combo.currentText()
        if decision_filter == "YES (accepted)":
            decision_filter = "YES"
        elif decision_filter == "NO (rejected)":
            decision_filter = "NO"
        query = self.search_input.text().strip().lower()
        out = []
        for record in self.records:
            decision = str(record.get("decision", "")).upper()
            if decision_filter == "REVIEW":
                if decision not in ("REVIEW", "ABSTAIN") and record.get("domain_validation", {}).get("status") != "REVIEW":
                    continue
            elif decision_filter != "All decisions" and decision_filter != "REVIEW":
                if decision != decision_filter:
                    continue
            if query:
                haystack = " ".join(
                    [
                        str(record.get("answer", "")),
                        str((record.get("evidence") or {}).get("question", "")),
                        str(record.get("expected", "")),
                    ]
                ).lower()
                if query not in haystack:
                    continue
            out.append(record)
        return out

    def _reload_table(self):
        filtered = self._filtered_records()
        self.table.setRowCount(0)
        for record in filtered:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(record.get("timestamp", ""))[:19]))
            self.table.setItem(row, 1, QTableWidgetItem(str(record.get("decision", "")).upper()))
            self.table.setItem(row, 2, QTableWidgetItem(f"{record.get('final_score', 0):.3f}" if isinstance(record.get("final_score"), (int, float)) else "-"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{record.get('confidence', 0):.2f}" if isinstance(record.get("confidence"), (int, float)) else "-"))
            self.table.setItem(row, 4, QTableWidgetItem(_truncate(record.get("answer", ""))))
            self.table.setItem(row, 5, QTableWidgetItem(_question_label(record)))
        self.count_label.setText(f"{len(filtered)} / {len(self.records)}")
        self.detail.clear()

    def _refresh(self):
        self.records = load_audit_records(self.path)
        self._reload_table()

    def _show_details(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.detail.clear()
            return
        row = rows[0].row()
        filtered = self._filtered_records()
        if row >= len(filtered):
            return
        record = filtered[row]
        self.detail.setPlainText(self._format_record(record))

    def _format_record(self, record):
        lines = []
        lines.append(f"Timestamp: {record.get('timestamp', '-')}")
        lines.append(f"Decision: {record.get('decision', '-')}  |  Score: {record.get('final_score', '-')}  |  Confidence: {record.get('confidence', '-')}")
        latency = record.get("latency_ms")
        latency_text = f"{latency:.0f} ms" if isinstance(latency, (int, float)) else "-"
        lines.append(f"Latency: {latency_text}  |  Stage: {record.get('stage_reached', '-')}  |  Model agreement: {record.get('model_agreement', '-')}")
        lines.append("")
        lines.append("Answer: " + str(record.get("answer", "-")))
        lines.append("Expected: " + str(record.get("expected", "-")))
        evidence = record.get("evidence") or {}
        if evidence.get("question"):
            lines.append("")
            lines.append("Question:")
            lines.append(str(evidence["question"]))
        policy = record.get("policy") or {}
        judges = policy.get("judge_decisions") or {}
        if judges:
            lines.append("")
            lines.append("Judge verdicts:")
            for role, verdict in judges.items():
                if not isinstance(verdict, dict):
                    continue
                lines.append(
                    f"  - {role}: {verdict.get('decision', '-')} "
                    f"(confidence {verdict.get('confidence', '-')}, model {verdict.get('model', '-')})"
                )
                if verdict.get("reason"):
                    lines.append(f"      {verdict['reason']}")
        domain = record.get("domain_validation") or {}
        if domain:
            lines.append("")
            lines.append(f"Domain validation: {domain.get('status', '-')} ({domain.get('domain', '-')})")
            if domain.get("reason"):
                lines.append("  " + str(domain["reason"]))
        return "\n".join(lines)

    def _export_csv(self):
        filtered = self._filtered_records()
        if not filtered:
            QMessageBox.information(self, "Nothing to Export", "No audit records match the current filter.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Audit Records", "grading_audit.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        import csv

        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["timestamp", "decision", "final_score", "confidence", "latency_ms", "stage_reached", "answer", "expected"]
            )
            for record in filtered:
                writer.writerow(
                    [
                        record.get("timestamp", ""),
                        record.get("decision", ""),
                        record.get("final_score", ""),
                        record.get("confidence", ""),
                        record.get("latency_ms", ""),
                        record.get("stage_reached", ""),
                        record.get("answer", ""),
                        record.get("expected", ""),
                    ]
                )
        QMessageBox.information(self, "Export Complete", f"Exported {len(filtered)} record(s) to:\n{path}")

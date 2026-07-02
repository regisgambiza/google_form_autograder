from dataclasses import replace
from typing import Dict, List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from answer_key_manager import (
    HealthFinding, backup_form_grading, list_backups, resolve_reviews, restore_backup, scan_form_data,
)
from answer_key_policy import prepare_answer_key
from auth import get_service
from updater import update_correct_answers


def _form_id(url: str) -> str:
    if "/d/e/" in url:
        return url.split("/d/e/", 1)[1].split("/", 1)[0].split("?", 1)[0]
    if "/d/" in url:
        return url.split("/d/", 1)[1].split("/", 1)[0].split("?", 1)[0]
    raise ValueError("The selected queue entry is not a Google Forms edit URL.")


class AnswerKeyDashboard(QDialog):
    COLUMNS = ["Apply", "Route", "Question", "Canonical", "Current", "Proposed", "Issues", "Confidence"]

    def __init__(self, forms_data: Dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Answer-key review")
        self.resize(1180, 720)
        self.forms_data = forms_data
        self.findings: List[HealthFinding] = []
        self.form_data = {}
        self.form_id = ""
        self.service = None

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Form"))
        self.form_combo = QComboBox()
        for url, title in forms_data.items():
            self.form_combo.addItem(title or url, url)
        toolbar.addWidget(self.form_combo, 1)
        self.scan_button = QPushButton("Scan")
        self.scan_button.clicked.connect(self.scan)
        toolbar.addWidget(self.scan_button)
        layout.addLayout(toolbar)

        self.summary = QLabel("Choose a queued form and scan its answer keys.")
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        for column, width in enumerate([55, 70, 250, 150, 190, 190, 260, 90]):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.dry_run_button = QPushButton("Dry run")
        self.dry_run_button.clicked.connect(self.dry_run)
        actions.addWidget(self.dry_run_button)
        self.reject_button = QPushButton("Reject selected")
        self.reject_button.clicked.connect(self.reject_selected)
        actions.addWidget(self.reject_button)
        self.apply_button = QPushButton("Apply approved")
        self.apply_button.clicked.connect(self.apply_approved)
        actions.addWidget(self.apply_button)
        actions.addStretch()
        self.restore_button = QPushButton("Restore latest backup")
        self.restore_button.clicked.connect(self.restore_latest)
        actions.addWidget(self.restore_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def scan(self):
        url = self.form_combo.currentData()
        if not url:
            QMessageBox.warning(self, "No form", "Add a form to the queue first.")
            return
        try:
            self.form_id = _form_id(url)
            self.service = get_service()
            self.form_data = self.service.forms().get(formId=self.form_id).execute()
            self.findings = scan_form_data(self.form_id, self.form_data)
            self._populate()
        except Exception as exc:
            QMessageBox.critical(self, "Scan failed", str(exc))

    def _populate(self):
        self.table.setRowCount(len(self.findings))
        changed = review = 0
        for row, finding in enumerate(self.findings):
            apply_item = QTableWidgetItem()
            apply_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            should_apply = finding.route == "auto" and finding.current_answers != finding.proposed_answers
            apply_item.setCheckState(Qt.Checked if should_apply else Qt.Unchecked)
            self.table.setItem(row, 0, apply_item)
            self._readonly(row, 1, finding.route.upper())
            self._readonly(row, 2, f"{finding.index + 1}. {finding.title}")
            self.table.setItem(row, 3, QTableWidgetItem(finding.canonical))
            self._readonly(row, 4, " | ".join(finding.current_answers))
            proposed = list(finding.proposed_answers)
            for candidate in finding.review_candidates:
                if candidate not in proposed:
                    proposed.append(candidate)
            self.table.setItem(row, 5, QTableWidgetItem("\n".join(proposed)))
            self._readonly(row, 6, "; ".join(finding.issues) or "Clean")
            self._readonly(row, 7, f"{finding.confidence:.0%}")
            changed += int(finding.current_answers != finding.proposed_answers)
            review += int(finding.route == "review")
        self.summary.setText(f"{len(self.findings)} text questions | {changed} proposed changes | {review} require review")

    def _readonly(self, row: int, column: int, text: str):
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, column, item)

    def _approved_plans(self):
        plans = []
        for row, finding in enumerate(self.findings):
            if self.table.item(row, 0).checkState() != Qt.Checked:
                continue
            canonical = self.table.item(row, 3).text().strip()
            proposed = [value.strip() for value in self.table.item(row, 5).text().splitlines() if value.strip()]
            plan = prepare_answer_key(finding.current_answers, proposed, [canonical] if canonical else [])
            plans.append((replace(finding, canonical=canonical, proposed_answers=proposed), plan))
        return plans

    def dry_run(self):
        plans = self._approved_plans()
        if not plans:
            QMessageBox.information(self, "Dry run", "No questions are approved.")
            return
        lines = [f"Q{f.index + 1} {f.title}: {len(f.current_answers)} -> {len(f.proposed_answers)} answers" for f, _ in plans]
        QMessageBox.information(self, "Dry run - no changes made", "\n".join(lines[:30]))

    def reject_selected(self):
        for index in self.table.selectionModel().selectedRows():
            row = index.row()
            self.table.item(row, 0).setCheckState(Qt.Unchecked)
            finding = self.findings[row]
            if finding.review_candidates:
                resolve_reviews(self.form_id, finding.item_id, "rejected")
        if self.form_id:
            self.scan()

    def apply_approved(self):
        plans = self._approved_plans()
        if not plans:
            QMessageBox.information(self, "Apply", "No questions are approved.")
            return
        reply = QMessageBox.question(self, "Apply answer-key changes", f"Back up the form and apply {len(plans)} approved question changes?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            backup = backup_form_grading(self.service, self.form_id, reason="answer-key dashboard apply")
            for finding, _ in plans:
                update_correct_answers(
                    self.service, self.form_id, finding.item_id, finding.proposed_answers,
                    finding.index, [finding.canonical], dry_run=False, create_backup=False,
                    manual_approval=True,
                )
                resolve_reviews(self.form_id, finding.item_id, "approved")
            QMessageBox.information(self, "Applied", f"Changes applied. Backup: {backup}")
            self.scan()
        except Exception as exc:
            QMessageBox.critical(self, "Apply failed", str(exc))

    def restore_latest(self):
        backups = list_backups(self.form_id) if self.form_id else []
        if not backups:
            QMessageBox.information(self, "Restore", "No backup exists for this form.")
            return
        latest = backups[0]
        reply = QMessageBox.question(self, "Restore answer keys", f"Restore the latest backup?\n{latest}", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            backup_form_grading(self.service, self.form_id, reason="before rollback")
            result = restore_backup(self.service, latest)
            QMessageBox.information(self, "Restored", f"Restored {result['request_count']} grading records.")
            self.scan()
        except Exception as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))

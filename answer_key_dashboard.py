from typing import Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from answer_key_manager import (
    HealthFinding,
    backup_form_grading,
    list_backups,
    remove_form_duplicates,
    resolve_reviews,
    restore_backup,
    scan_form_data,
)
from answer_key_policy import identity_key
from auth import get_service
from updater import update_correct_answers


def _form_id(url: str) -> str:
    if "/d/e/" in url:
        return url.split("/d/e/", 1)[1].split("/", 1)[0].split("?", 1)[0]
    if "/d/" in url:
        return url.split("/d/", 1)[1].split("/", 1)[0].split("?", 1)[0]
    raise ValueError("The selected entry is not a Google Forms edit URL.")


class AnswerKeyDashboard(QDialog):
    def __init__(self, forms_data: Dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Answer Keys")
        self.resize(920, 650)
        self.setMinimumSize(760, 540)
        self.forms_data = forms_data
        self.service = None
        self.form_id = ""
        self.form_data = {}
        self.findings: List[HealthFinding] = []
        self.active_finding: Optional[HealthFinding] = None
        self.backup_path = None

        self.setStyleSheet("""
            QDialog { background: #f4f6f8; }
            QFrame#Panel { background: white; border: 1px solid #d7e0ea; border-radius: 6px; }
            QLabel#Title { font-size: 18px; font-weight: 700; color: #1f2937; }
            QLabel#Section { font-size: 15px; font-weight: 700; color: #1f2937; }
            QLabel#Status { color: #40546a; padding: 6px 0; }
            QPushButton { min-height: 34px; padding: 0 14px; }
            QPushButton#Primary { background: #1769aa; color: white; border: 0; border-radius: 5px; font-weight: 700; }
            QPushButton#Primary:hover { background: #12578d; }
            QPushButton#Secondary { background: white; color: #263747; border: 1px solid #b8c5d1; border-radius: 5px; }
            QListWidget, QLineEdit, QComboBox { background: white; border: 1px solid #c8d2dc; border-radius: 4px; padding: 5px; }
            QListWidget::item { min-height: 32px; padding: 4px; }
            QListWidget::item:selected { background: #dcecff; color: #15324b; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Answer Keys")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch()
        self.form_combo = QComboBox()
        self.form_combo.setMinimumWidth(360)
        for url, form_title in forms_data.items():
            self.form_combo.addItem(form_title or url, url)
        self.form_combo.currentIndexChanged.connect(self._form_changed)
        header.addWidget(self.form_combo)
        root.addLayout(header)

        quick = QFrame()
        quick.setObjectName("Panel")
        quick_layout = QHBoxLayout(quick)
        quick_label = QLabel("Duplicate answers")
        quick_label.setObjectName("Section")
        quick_layout.addWidget(quick_label)
        quick_layout.addStretch()
        self.clean_button = QPushButton("Clean Duplicates")
        self.clean_button.setObjectName("Primary")
        self.clean_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.clean_button.clicked.connect(self.clean_duplicates)
        quick_layout.addWidget(self.clean_button)
        root.addWidget(quick)

        review_bar = QHBoxLayout()
        review_title = QLabel("Possible mistakes")
        review_title.setObjectName("Section")
        review_bar.addWidget(review_title)
        review_bar.addStretch()
        self.scan_button = QPushButton("Review Possible Mistakes")
        self.scan_button.setObjectName("Secondary")
        self.scan_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView))
        self.scan_button.clicked.connect(self.scan)
        review_bar.addWidget(self.scan_button)
        root.addLayout(review_bar)

        splitter = QSplitter(Qt.Horizontal)
        self.question_list = QListWidget()
        self.question_list.currentItemChanged.connect(self._show_question)
        splitter.addWidget(self.question_list)

        detail = QFrame()
        detail.setObjectName("Panel")
        detail_layout = QVBoxLayout(detail)
        self.question_title = QLabel("Select Review Possible Mistakes")
        self.question_title.setObjectName("Section")
        self.question_title.setWordWrap(True)
        detail_layout.addWidget(self.question_title)

        detail_layout.addWidget(QLabel("Correct answer"))
        self.canonical_input = QLineEdit()
        self.canonical_input.setPlaceholderText("Enter the correct answer")
        detail_layout.addWidget(self.canonical_input)

        detail_layout.addWidget(QLabel("Accepted answers"))
        self.answer_list = QListWidget()
        detail_layout.addWidget(self.answer_list, 1)

        detail_actions = QHBoxLayout()
        self.skip_button = QPushButton("Skip")
        self.skip_button.setObjectName("Secondary")
        self.skip_button.clicked.connect(self.skip_question)
        detail_actions.addWidget(self.skip_button)
        detail_actions.addStretch()
        self.save_button = QPushButton("Save Answer Key")
        self.save_button.setObjectName("Primary")
        self.save_button.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.save_button.clicked.connect(self.save_question)
        detail_actions.addWidget(self.save_button)
        detail_layout.addLayout(detail_actions)
        splitter.addWidget(detail)
        splitter.setSizes([330, 550])
        root.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Ready")
        self.status.setObjectName("Status")
        footer.addWidget(self.status, 1)
        self.undo_button = QPushButton("Undo Last Change")
        self.undo_button.setObjectName("Secondary")
        self.undo_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self.undo_button.clicked.connect(self.undo_last_change)
        footer.addWidget(self.undo_button)
        close_button = QPushButton("Close")
        close_button.setObjectName("Secondary")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)
        self._set_detail_enabled(False)

    def _form_changed(self):
        self.service = None
        self.form_id = ""
        self.form_data = {}
        self.findings = []
        self.active_finding = None
        self.backup_path = None
        self.question_list.clear()
        self.answer_list.clear()
        self.status.setText("Ready")
        self._set_detail_enabled(False)

    def _connect(self):
        url = self.form_combo.currentData()
        if not url:
            raise ValueError("Add a form to the queue first.")
        self.form_id = _form_id(url)
        self.service = self.service or get_service()

    def clean_duplicates(self):
        try:
            self._connect()
            self.clean_button.setEnabled(False)
            self.status.setText("Cleaning duplicate answers...")
            result = remove_form_duplicates(self.service, self.form_id)
            if result["removed"]:
                self.backup_path = result["backup"]
                self.status.setText(
                    f"Removed {result['removed']} duplicates from {result['changed_questions']} questions"
                )
            else:
                self.status.setText("No duplicate answers found")
            if self.question_list.count():
                self.scan()
        except Exception as exc:
            QMessageBox.critical(self, "Could not clean duplicates", str(exc))
            self.status.setText("Duplicate cleanup failed")
        finally:
            self.clean_button.setEnabled(True)

    def scan(self):
        try:
            self._connect()
            self.scan_button.setEnabled(False)
            self.status.setText("Checking answer keys...")
            self.form_data = self.service.forms().get(formId=self.form_id).execute()
            all_findings = scan_form_data(self.form_id, self.form_data)
            self.findings = [
                finding for finding in all_findings
                if finding.route in {"review", "reject"}
            ]
            self.question_list.clear()
            for index, finding in enumerate(self.findings):
                item = QListWidgetItem(f"Q{finding.index + 1}  {finding.title}")
                item.setData(Qt.UserRole, index)
                self.question_list.addItem(item)
            if self.findings:
                self.question_list.setCurrentRow(0)
                self.status.setText(f"{len(self.findings)} questions need review")
            else:
                self._set_detail_enabled(False)
                self.question_title.setText("No possible mistakes found")
                self.status.setText("Answer keys look clean")
        except Exception as exc:
            QMessageBox.critical(self, "Could not review answer keys", str(exc))
            self.status.setText("Answer-key review failed")
        finally:
            self.scan_button.setEnabled(True)

    def _show_question(self, current, _previous):
        if not current:
            self._set_detail_enabled(False)
            return
        index = current.data(Qt.UserRole)
        if index is None or index >= len(self.findings):
            return
        finding = self.findings[index]
        self.active_finding = finding
        self.question_title.setText(f"Q{finding.index + 1}. {finding.title}")
        self.canonical_input.setText(finding.canonical)
        self.answer_list.clear()

        proposed_keys = {identity_key(value) for value in finding.proposed_answers}
        values = []
        seen = set()
        for value in finding.current_answers + finding.review_candidates:
            key = identity_key(value)
            if key not in seen:
                seen.add(key)
                values.append(value)
        for value in values:
            item = QListWidgetItem(value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if identity_key(value) in proposed_keys else Qt.Unchecked)
            self.answer_list.addItem(item)
        self._set_detail_enabled(True)

    def _set_detail_enabled(self, enabled: bool):
        self.canonical_input.setEnabled(enabled)
        self.answer_list.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.skip_button.setEnabled(enabled)

    def _checked_answers(self) -> List[str]:
        answers = []
        for row in range(self.answer_list.count()):
            item = self.answer_list.item(row)
            if item.checkState() == Qt.Checked:
                answers.append(item.text())
        return answers

    def save_question(self):
        finding = self.active_finding
        canonical = self.canonical_input.text().strip()
        if not finding or not canonical:
            QMessageBox.warning(self, "Correct answer required", "Enter the correct answer first.")
            return
        try:
            if not self.backup_path:
                self.backup_path = str(
                    backup_form_grading(self.service, self.form_id, reason="before answer-key review")
                )
            answers = self._checked_answers()
            update_correct_answers(
                self.service,
                self.form_id,
                finding.item_id,
                answers,
                finding.index,
                [canonical],
                dry_run=False,
                create_backup=False,
                manual_approval=True,
            )
            resolve_reviews(self.form_id, finding.item_id, "approved")
            self.status.setText(f"Saved Q{finding.index + 1}")
            self.scan()
        except Exception as exc:
            QMessageBox.critical(self, "Could not save answer key", str(exc))

    def skip_question(self):
        item = self.question_list.currentItem()
        if not item:
            return
        row = self.question_list.row(item)
        self.question_list.takeItem(row)
        if self.question_list.count():
            self.question_list.setCurrentRow(min(row, self.question_list.count() - 1))
        else:
            self.active_finding = None
            self._set_detail_enabled(False)
        self.status.setText("Question skipped")

    def undo_last_change(self):
        try:
            self._connect()
            backups = list_backups(self.form_id)
            if not backups:
                self.status.setText("No change to undo")
                return
            latest = backups[0]
            backup_form_grading(self.service, self.form_id, reason="before undo")
            result = restore_backup(self.service, latest)
            self.status.setText(f"Restored {result['request_count']} answer keys")
            if self.question_list.count():
                self.scan()
        except Exception as exc:
            QMessageBox.critical(self, "Could not undo change", str(exc))

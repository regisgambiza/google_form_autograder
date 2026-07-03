from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
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
    keep_teacher_answers_only,
    load_pending_review_records,
    remove_form_duplicates,
    resolve_reviews,
    restore_backup,
    scan_form_data,
)
from answer_key_policy import identity_key
from auth import get_service
from app_theme import apply_widget_theme
from form_searcher import find_all_forms_in_sources
from updater import update_correct_answers


class _AnswerKeySaveWorker(QThread):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, service, form_id, item_id, answers, index, canonical, parent=None):
        super().__init__(parent)
        self.service = service
        self.form_id = form_id
        self.item_id = item_id
        self.answers = answers
        self.index = index
        self.canonical = canonical

    def run(self):
        try:
            update_correct_answers(
                self.service,
                self.form_id,
                self.item_id,
                self.answers,
                self.index,
                [self.canonical],
                dry_run=False,
                create_backup=False,
                manual_approval=True,
            )
            self.finished.emit((self.form_id, self.item_id))
        except Exception as exc:  # pragma: no cover - exercised via UI path
            self.failed.emit(str(exc))


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
        self.processed_item_ids = set()
        self.backup_path = None

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
        self.add_source_button = QPushButton("Add Form or Folder")
        self.add_source_button.setObjectName("Secondary")
        self.add_source_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogNewFolder))
        self.add_source_button.clicked.connect(self.add_source)
        header.addWidget(self.add_source_button)
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
        self.keep_teacher_only_button = QPushButton("Keep Teacher Answers Only")
        self.keep_teacher_only_button.setObjectName("Danger")
        self.keep_teacher_only_button.setToolTip("Delete every answer variant except the first teacher answer for each question")
        self.keep_teacher_only_button.clicked.connect(self.keep_teacher_answers_only)
        quick_layout.addWidget(self.keep_teacher_only_button)
        root.addWidget(quick)

        review_bar = QHBoxLayout()
        review_title = QLabel("Possible mistakes")
        review_title.setObjectName("Section")
        review_bar.addWidget(review_title)
        review_bar.addStretch()
        self.review_filter = QComboBox()
        self.review_filter.addItems(["Needs review", "All questions"])
        self.review_filter.setToolTip("Show only questions awaiting a decision, or every text question")
        self.review_filter.currentIndexChanged.connect(self._review_filter_changed)
        review_bar.addWidget(self.review_filter)
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
        self.canonical_input.setReadOnly(True)
        self.canonical_input.setToolTip("Protected teacher answer. The app never edits or deletes this first answer.")
        detail_layout.addWidget(self.canonical_input)

        detail_layout.addWidget(QLabel("Answer classifications"))
        self.answer_list = QListWidget()
        detail_layout.addWidget(self.answer_list, 1)

        detail_actions = QHBoxLayout()
        self.skip_button = QPushButton("Leave for Later")
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
        apply_widget_theme(self)

    def _form_changed(self):
        self.service = None
        self.form_id = ""
        self.form_data = {}
        self.findings = []
        self.active_finding = None
        self.processed_item_ids.clear()
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

    def _review_filter_changed(self):
        if self.form_data:
            self.scan()

    def add_source(self):
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "Add Form or Folder",
            "Google Form or Drive folder URL",
        )
        if not accepted or not text.strip():
            return
        try:
            self.add_source_button.setEnabled(False)
            self.status.setText("Finding forms...")
            forms = find_all_forms_in_sources(
                text,
                progress_callback=lambda message: self.status.setText(message),
            )
            if not forms:
                self.status.setText("No accessible forms found")
                return
            first_result_index = None
            added = 0
            parent = self.parent()
            for form in forms:
                url = form.get("url")
                title = form.get("title") or "Untitled"
                if not url:
                    continue
                existing_index = self.form_combo.findData(url)
                if existing_index >= 0:
                    if first_result_index is None:
                        first_result_index = existing_index
                    continue
                self.forms_data[url] = title
                self.form_combo.addItem(title, url)
                if first_result_index is None:
                    first_result_index = self.form_combo.count() - 1
                added += 1
                if parent and hasattr(parent, "_add_form_to_queue"):
                    parent._add_form_to_queue(url, title, source="Answer Keys")
            if parent and hasattr(parent, "save_forms") and added:
                parent.save_forms()
            if first_result_index is not None:
                self.form_combo.setCurrentIndex(first_result_index)
            self.status.setText(
                f"Added {added} form{'s' if added != 1 else ''}"
                if added else "Form already available"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not add source", str(exc))
            self.status.setText("Could not add form or folder")
        finally:
            self.add_source_button.setEnabled(True)

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

    def keep_teacher_answers_only(self):
        try:
            self._connect()
            confirmation = QMessageBox.question(
                self,
                "Keep Teacher Answers Only?",
                "This will remove every added answer variant from this form and keep only "
                "the first teacher answer for each text question. A backup will be created first.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirmation != QMessageBox.Yes:
                return
            self.keep_teacher_only_button.setEnabled(False)
            self.status.setText("Removing added answer variants...")
            result = keep_teacher_answers_only(self.service, self.form_id)
            if result["removed"]:
                self.backup_path = result["backup"]
                self.status.setText(
                    f"Removed {result['removed']} variants from {result['changed_questions']} questions"
                )
            else:
                self.status.setText("Every question already contains only its teacher answer")
            if self.question_list.count():
                self.scan()
        except Exception as exc:
            QMessageBox.critical(self, "Could not clean answer keys", str(exc))
            self.status.setText("Teacher-answer cleanup failed")
        finally:
            self.keep_teacher_only_button.setEnabled(True)

    def scan(self):
        try:
            self._connect()
            self.scan_button.setEnabled(False)
            self.status.setText("Checking answer keys...")
            self.form_data = self.service.forms().get(formId=self.form_id).execute()
            all_findings = scan_form_data(self.form_id, self.form_data)
            pending_by_item = load_pending_review_records(self.form_id)
            show_all = self.review_filter.currentText() == "All questions"
            self.findings = []
            for finding in all_findings:
                if finding.item_id in self.processed_item_ids:
                    continue
                records = pending_by_item.get(str(finding.item_id), [])
                categories = {}
                for value in finding.current_answers:
                    categories[identity_key(value)] = "Accepted"
                for record in records:
                    for value in record.get("accepted", []):
                        categories[identity_key(value)] = "Accepted"
                    for value in record.get("needs_approval", []):
                        categories[identity_key(value)] = "Needs approval"
                    for value in record.get("rejected", []):
                        categories[identity_key(value)] = "Rejected"
                    if not any(key in record for key in ("accepted", "needs_approval", "rejected")):
                        legacy_category = "Needs approval" if record.get("source") == "grading_review" else "Accepted"
                        for value in record.get("candidates", []):
                            categories[identity_key(value)] = legacy_category
                finding.answer_categories = categories
                finding.review_records = records
                needs_review = finding.route in {"review", "reject"} or bool(records)
                if show_all or needs_review:
                    self.findings.append(finding)
            reject_count = sum(1 for finding in self.findings if finding.route == "reject")
            self.question_list.clear()
            for index, finding in enumerate(self.findings):
                item = QListWidgetItem(f"Q{finding.index + 1}  {finding.title}")
                item.setData(Qt.UserRole, index)
                self.question_list.addItem(item)
            if self.findings:
                self.question_list.setCurrentRow(0)
                if reject_count:
                    self.status.setText(
                        f"{len(self.findings)} questions need review, {reject_count} questions clearly wrong"
                    )
                else:
                    self.status.setText(f"{len(self.findings)} questions need review")
            else:
                self._set_detail_enabled(False)
                self.question_title.setText("No possible mistakes found")
                if reject_count:
                    self.status.setText(f"{reject_count} questions were rejected as obviously wrong")
                else:
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

        current_keys = {identity_key(value) for value in finding.current_answers}
        canonical_key = identity_key(finding.canonical)
        categories = getattr(finding, "answer_categories", {})
        values = []
        seen = set()
        for value in finding.current_answers + finding.review_candidates:
            key = identity_key(value)
            if key not in seen:
                seen.add(key)
                values.append(value)
        for record in getattr(finding, "review_records", []):
            for value in record.get("candidates", []):
                key = identity_key(value)
                if key not in seen:
                    seen.add(key)
                    values.append(value)
        for value in values:
            key = identity_key(value)
            category = categories.get(key, "Accepted" if key in current_keys else "Needs approval")
            label = "Accepted (teacher)" if key == canonical_key else category
            item = QListWidgetItem(f"{label} — {value}")
            item.setData(Qt.UserRole + 1, key == canonical_key)
            item.setData(Qt.UserRole + 2, label)
            if key == canonical_key:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsUserCheckable)
                item.setToolTip("Protected teacher canonical answer")
            else:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
                item.setCheckState(Qt.Unchecked if category == "Rejected" else Qt.Checked)
                if category == "Rejected":
                    item.setToolTip("Rejected by grading and not entered in the form. Check to approve it manually.")
                else:
                    item.setToolTip("Currently entered in the form. Uncheck to remove; double-click to edit.")
            self.answer_list.addItem(item)
        self._set_detail_enabled(True)

    def _remove_review_item(self, finding: HealthFinding, status_text: str):
        self.processed_item_ids.add(str(finding.item_id))
        row_to_remove = None
        for row in range(self.question_list.count()):
            item = self.question_list.item(row)
            if item.data(Qt.UserRole) == self.findings.index(finding):
                row_to_remove = row
                break
        if row_to_remove is not None:
            self.question_list.takeItem(row_to_remove)
        if self.question_list.count():
            self.question_list.setCurrentRow(min(row_to_remove or 0, self.question_list.count() - 1))
        else:
            self.active_finding = None
            self._set_detail_enabled(False)
        self.status.setText(status_text)

    def _set_detail_enabled(self, enabled: bool):
        self.canonical_input.setEnabled(enabled)
        self.answer_list.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.skip_button.setEnabled(enabled)

    def _checked_answers(self) -> List[str]:
        answers = []
        for row in range(self.answer_list.count()):
            item = self.answer_list.item(row)
            if not bool(item.data(Qt.UserRole + 1)) and item.checkState() == Qt.Checked:
                text = item.text().strip()
                prefix = f"{item.data(Qt.UserRole + 2)} — "
                answers.append(text[len(prefix):].strip() if text.startswith(prefix) else text)
        return answers

    def save_question(self):
        finding = self.active_finding
        canonical = finding.canonical.strip() if finding else ""
        if not finding or not canonical:
            QMessageBox.warning(self, "Correct answer required", "Enter the correct answer first.")
            return
        try:
            if not self.backup_path:
                self.backup_path = str(
                    backup_form_grading(self.service, self.form_id, reason="before answer-key review")
                )
            answers = self._checked_answers()
            self._set_detail_enabled(False)
            progress = QProgressDialog("Saving answer key…", "Cancel", 0, 0, self)
            progress.setWindowTitle("Saving")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            progress.show()

            worker = _AnswerKeySaveWorker(
                self.service,
                self.form_id,
                finding.item_id,
                answers,
                finding.index,
                canonical,
                self,
            )
            worker.finished.connect(lambda _: self._on_save_finished(progress, finding))
            worker.failed.connect(lambda error: self._on_save_failed(progress, error))
            worker.start()
        except Exception as exc:
            QMessageBox.critical(self, "Could not save answer key", str(exc))

    def _on_save_finished(self, progress, finding):
        progress.close()
        resolve_reviews(self.form_id, finding.item_id, "approved")
        self._remove_review_item(finding, f"Saved Q{finding.index + 1}")

    def _on_save_failed(self, progress, error):
        progress.close()
        QMessageBox.critical(self, "Could not save answer key", error)
        self._set_detail_enabled(True)

    def skip_question(self):
        item = self.question_list.currentItem()
        if not item:
            return
        finding = self.findings[item.data(Qt.UserRole)] if item.data(Qt.UserRole) is not None else None
        if finding:
            self._remove_review_item(finding, "Question left for later")

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

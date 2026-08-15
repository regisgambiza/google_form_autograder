import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

import answer_key_dashboard as dashboard

APP = QApplication.instance() or QApplication([])


def test_form_id_accepts_edit_and_published_urls():
    assert dashboard._form_id("https://docs.google.com/forms/d/abc123/edit") == "abc123"
    assert dashboard._form_id("https://docs.google.com/forms/d/e/pub123/viewform") == "pub123"


def test_add_form_or_folder_populates_selector(monkeypatch):
    dialog = dashboard.AnswerKeyDashboard({})
    monkeypatch.setattr(
        dashboard.QInputDialog,
        "getMultiLineText",
        lambda *_args, **_kwargs: ("https://drive.google.com/drive/folders/folder", True),
    )
    monkeypatch.setattr(
        dashboard,
        "find_all_forms_in_sources",
        lambda *_args, **_kwargs: [
            {"url": "https://docs.google.com/forms/d/form1/edit", "title": "Quiz One"},
            {"url": "https://docs.google.com/forms/d/form2/edit", "title": "Quiz Two"},
        ],
    )

    dialog.add_source()

    assert dialog.form_combo.count() == 2
    assert dialog.form_combo.currentText() == "Quiz One"
    assert dialog.status.text() == "Added 2 forms"


def test_leave_for_later_keeps_review_pending(monkeypatch):
    dialog = dashboard.AnswerKeyDashboard({})
    dialog.form_id = "form-1"
    dialog.findings = [
        SimpleNamespace(
            index=0,
            item_id="item-1",
            title="Question 1",
            canonical="",
            proposed_answers=[],
            current_answers=[],
            review_candidates=[],
        )
    ]

    item = QListWidgetItem("Q1  Question 1")
    item.setData(Qt.UserRole, 0)
    dialog.question_list.addItem(item)
    dialog.question_list.setCurrentItem(item)

    calls = []
    monkeypatch.setattr(
        dashboard,
        "resolve_reviews",
        lambda form_id, item_id, status: calls.append((form_id, item_id, status)) or 1,
    )
    monkeypatch.setattr(dialog, "_set_detail_enabled", lambda enabled: None)

    dialog.skip_question()

    assert calls == []
    assert dialog.question_list.count() == 0
    assert dialog.active_finding is None


def test_scan_omits_processed_questions(monkeypatch):
    dialog = dashboard.AnswerKeyDashboard({})
    dialog.form_id = "form-1"
    dialog.processed_item_ids.add("item-1")

    class _Forms:
        def get(self, formId):
            return self

        def execute(self):
            return {"items": []}

    class _Service:
        def forms(self):
            return _Forms()

    dialog.service = _Service()
    monkeypatch.setattr(dialog, "_connect", lambda: None)
    monkeypatch.setattr(
        dashboard,
        "scan_form_data",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                index=0,
                item_id="item-1",
                title="Question 1",
                route="review",
                canonical="",
                proposed_answers=[],
                current_answers=[],
                review_candidates=[],
            ),
            SimpleNamespace(
                index=1,
                item_id="item-2",
                title="Question 2",
                route="review",
                canonical="",
                proposed_answers=[],
                current_answers=[],
                review_candidates=[],
            ),
        ],
    )

    dialog.scan()

    assert dialog.question_list.count() == 1
    assert dialog.question_list.item(0).text() == "Q2  Question 2"


def test_review_locks_canonical_and_allows_variant_edit_delete():
    dialog = dashboard.AnswerKeyDashboard({})
    finding = SimpleNamespace(
        index=0, item_id="item-1", title="Question 1",
        canonical="teacher answer",
        proposed_answers=["teacher answer"],
        current_answers=["teacher answer", "AI variant", "wrong variant"],
        review_candidates=["AI variant", "wrong variant"],
    )
    dialog.findings = [finding]
    row = QListWidgetItem("Q1 Question 1")
    row.setData(Qt.UserRole, 0)
    dialog.question_list.addItem(row)

    dialog._show_question(row, None)

    assert dialog.canonical_input.isReadOnly()
    canonical_item = dialog.answer_list.item(0)
    assert not bool(canonical_item.flags() & Qt.ItemIsEditable)
    assert dialog.answer_list.item(1).checkState() == Qt.Checked
    assert bool(dialog.answer_list.item(1).flags() & Qt.ItemIsEditable)
    dialog.answer_list.item(2).setCheckState(Qt.Unchecked)
    assert dialog._checked_answers() == ["AI variant"]


def test_answer_categories_are_labelled_and_rejected_is_not_selected():
    dialog = dashboard.AnswerKeyDashboard({})
    finding = SimpleNamespace(
        index=0, item_id="item-1", title="Question 1", canonical="teacher",
        current_answers=["teacher", "accepted", "approval"], review_candidates=[],
        answer_categories={
            "teacher": "Accepted",
            "accepted": "Accepted",
            "approval": "Needs approval",
            "wrong": "Rejected",
        },
        review_records=[{"candidates": ["accepted", "approval", "wrong"]}],
    )
    dialog.findings = [finding]
    row = QListWidgetItem("Q1 Question 1")
    row.setData(Qt.UserRole, 0)

    dialog._show_question(row, None)

    assert dialog.answer_list.item(0).text().startswith("Accepted (teacher) —")
    assert dialog.answer_list.item(1).text() == "Accepted — accepted"
    assert dialog.answer_list.item(2).text() == "Needs approval — approval"
    assert dialog.answer_list.item(3).text() == "Rejected — wrong"
    assert dialog.answer_list.item(2).checkState() == Qt.Unchecked
    assert dialog.answer_list.item(3).checkState() == Qt.Unchecked
    assert dialog._checked_answers() == ["accepted"]


def test_filter_switches_between_review_and_all_questions(monkeypatch):
    dialog = dashboard.AnswerKeyDashboard({})
    dialog.form_id = "form-1"
    dialog.form_data = {}

    class _Forms:
        def get(self, formId):
            return self
        def execute(self):
            return {"items": []}

    dialog.service = SimpleNamespace(forms=lambda: _Forms())
    monkeypatch.setattr(dialog, "_connect", lambda: None)
    monkeypatch.setattr(dashboard, "load_pending_review_records", lambda _form_id: {})
    monkeypatch.setattr(dashboard, "scan_form_data", lambda *_a, **_k: [
        SimpleNamespace(index=0, item_id="clean", title="Clean", route="clean", canonical="a", current_answers=["a"], review_candidates=[]),
        SimpleNamespace(index=1, item_id="review", title="Review", route="review", canonical="b", current_answers=["b"], review_candidates=[]),
    ])

    dialog.scan()
    assert dialog.question_list.count() == 1
    dialog.review_filter.setCurrentText("All questions")
    assert dialog.question_list.count() == 2


def test_keep_teacher_only_button_confirms_and_runs_cleanup(monkeypatch):
    dialog = dashboard.AnswerKeyDashboard({})
    dialog.form_id = "form-1"
    dialog.service = object()
    monkeypatch.setattr(dialog, "_connect", lambda: None)
    monkeypatch.setattr(dashboard.QMessageBox, "question", lambda *_a, **_k: dashboard.QMessageBox.Yes)
    calls = []
    monkeypatch.setattr(
        dashboard,
        "keep_teacher_answers_only",
        lambda service, form_id: calls.append((service, form_id)) or {
            "removed": 3, "changed_questions": 2, "backup": "backup.json"
        },
    )

    dialog.keep_teacher_answers_only()

    assert calls == [(dialog.service, "form-1")]
    assert "Removed 3 variants" in dialog.status.text()

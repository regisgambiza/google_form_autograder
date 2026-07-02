import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

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

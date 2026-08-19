import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app_theme import apply_application_theme
from gui_studio import drive_folders
from gui_studio.main_window import AutograderWindow
from gui_studio.pages import DriveFoldersPage

APP = QApplication.instance() or QApplication([])
apply_application_theme(APP)


# ---------------------------------------------------------------------------
# Fake Drive service
# ---------------------------------------------------------------------------
class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeFilesResource:
    def __init__(self, service):
        self._service = service

    def list(self, q=None, fields=None, pageSize=None, pageToken=None, **kwargs):
        if kwargs.get("driveId"):
            pages = self._service.shared_drive_pages
        else:
            pages = self._service.user_pages
        page = pages[pageToken or 0]
        return _FakeRequest(page)

    def get(self, fileId=None, fields=None):
        return _FakeRequest({"id": self._service.root_id})


class _FakeDrivesResource:
    def __init__(self, service):
        self._service = service

    def list(self, pageSize=None, pageToken=None):
        return _FakeRequest({"drives": self._service.shared_drives})


class FakeDriveService:
    def __init__(self, user_pages, shared_drives=None, shared_drive_pages=None, root_id="MYROOT"):
        self.user_pages = user_pages
        self.shared_drives = shared_drives or []
        self.shared_drive_pages = shared_drive_pages or []
        self.root_id = root_id

    def files(self):
        return _FakeFilesResource(self)

    def drives(self):
        return _FakeDrivesResource(self)


def _patch_drive(monkeypatch, service):
    monkeypatch.setattr("auth.get_drive_service", lambda: service)
    monkeypatch.setattr(
        "form_searcher._execute_with_retries",
        lambda request, context="", progress_callback=None, **kwargs: request.execute(),
    )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def test_save_selected_folders_preserves_form_urls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("predefined_folders.json", "w", encoding="utf-8") as fh:
        json.dump({"folders": [
            "https://drive.google.com/drive/folders/OLD?usp=drive_link",
            "https://docs.google.com/forms/d/FORMA/edit",
        ]}, fh)

    merged = drive_folders.save_selected_folders([
        "https://drive.google.com/drive/folders/AAA",
        "https://drive.google.com/drive/folders/BBB",
    ])
    assert merged == [
        "https://docs.google.com/forms/d/FORMA/edit",
        "https://drive.google.com/drive/folders/AAA",
        "https://drive.google.com/drive/folders/BBB",
    ]
    assert drive_folders.load_selected_folders() == [
        "https://drive.google.com/drive/folders/AAA",
        "https://drive.google.com/drive/folders/BBB",
    ]

    # Empty selection clears folders but never drops form URLs.
    drive_folders.save_selected_folders([])
    assert drive_folders.load_predefined_entries() == ["https://docs.google.com/forms/d/FORMA/edit"]


# ---------------------------------------------------------------------------
# scan_whole_drive tree building
# ---------------------------------------------------------------------------
def test_scan_whole_drive_builds_my_drive_tree_and_shared_roots(monkeypatch):
    service = FakeDriveService(user_pages=[{
        "files": [
            {"id": "top", "name": "Math", "parents": ["MYROOT"]},
            {"id": "g8", "name": "Grade 8", "parents": ["top"]},
            {"id": "g8a", "name": "Algebra", "parents": ["g8"]},
            {"id": "shared", "name": "From Bob", "parents": ["INVISIBLE"]},
        ]
    }])
    _patch_drive(monkeypatch, service)

    nodes = drive_folders.scan_whole_drive()
    by_id = {node["id"]: node for node in nodes}

    assert set(by_id) == {"top", "g8", "g8a", "shared"}
    assert by_id["top"]["parent_id"] is None and by_id["top"]["root"] == "My Drive"
    assert by_id["g8"]["parent_id"] == "top" and by_id["g8"]["root"] == "My Drive"
    assert by_id["g8a"]["parent_id"] == "g8"
    # Parents that are not visible mean the folder was shared with the account.
    assert by_id["shared"]["parent_id"] is None and by_id["shared"]["root"] == "Shared with me"


def test_scan_whole_drive_handles_pagination(monkeypatch):
    service = FakeDriveService(user_pages=[
        {"files": [{"id": "p1", "name": "First", "parents": ["MYROOT"]}], "nextPageToken": 1},
        {"files": [{"id": "p2", "name": "Second", "parents": ["MYROOT"]}]},
    ])
    _patch_drive(monkeypatch, service)

    nodes = drive_folders.scan_whole_drive()
    assert {node["id"] for node in nodes} == {"p1", "p2"}


def test_scan_whole_drive_includes_shared_drives(monkeypatch):
    service = FakeDriveService(
        user_pages=[{"files": [{"id": "mine", "name": "Mine", "parents": ["MYROOT"]}]}],
        shared_drives=[{"id": "SD1", "name": "Team Drive"}],
        shared_drive_pages=[
            {"files": [
                {"id": "sdroot", "name": "Team Drive", "parents": ["SD1"]},
                {"id": "unit1", "name": "Unit 1", "parents": ["sdroot"]},
            ]},
        ],
    )
    _patch_drive(monkeypatch, service)

    nodes = drive_folders.scan_whole_drive()
    by_id = {node["id"]: node for node in nodes}
    assert set(by_id) == {"mine", "sdroot", "unit1"}
    assert by_id["sdroot"]["parent_id"] is None and by_id["sdroot"]["root"] == "Team Drive"
    assert by_id["unit1"]["parent_id"] == "sdroot" and by_id["unit1"]["root"] == "Team Drive"


def test_scan_dedupes_folders_visible_in_drive_and_shared_with_me(monkeypatch):
    """A drive folder shared with the account directly must appear once,
    nested under its real drive parent (not flattened or duplicated)."""
    service = FakeDriveService(
        user_pages=[{"files": [
            {"id": "mine", "name": "Mine", "parents": ["MYROOT"]},
            # Directly shared with the account, but really lives in the drive:
            {"id": "unit1", "name": "Unit 1", "parents": ["top"]},
            {"id": "top", "name": "Top", "parents": ["SD1"]},
        ]}],
        shared_drives=[{"id": "SD1", "name": "Team Drive"}],
        shared_drive_pages=[
            {"files": [
                {"id": "top", "name": "Top", "parents": ["SD1"]},
                {"id": "unit1", "name": "Unit 1", "parents": ["top"]},
            ]},
        ],
    )
    _patch_drive(monkeypatch, service)

    nodes = drive_folders.scan_whole_drive()
    assert len(nodes) == 3  # no duplicates
    by_id = {node["id"]: node for node in nodes}
    assert by_id["top"]["parent_id"] is None and by_id["top"]["root"] == "Team Drive"
    assert by_id["unit1"]["parent_id"] == "top" and by_id["unit1"]["root"] == "Team Drive"
    assert by_id["mine"]["parent_id"] is None and by_id["mine"]["root"] == "My Drive"


def test_scan_preserves_deep_shared_drive_nesting(monkeypatch):
    service = FakeDriveService(
        user_pages=[{"files": []}],
        shared_drives=[{"id": "SD1", "name": "Deep"}],
        shared_drive_pages=[
            {"files": [
                {"id": "a", "name": "A", "parents": ["SD1"]},
                {"id": "b", "name": "B", "parents": ["a"]},
                {"id": "c", "name": "C", "parents": ["b"]},
                {"id": "d", "name": "D", "parents": ["c"]},
            ]},
        ],
    )
    _patch_drive(monkeypatch, service)

    nodes = drive_folders.scan_whole_drive()
    by_id = {node["id"]: node for node in nodes}
    assert by_id["a"]["parent_id"] is None
    assert by_id["b"]["parent_id"] == "a"
    assert by_id["c"]["parent_id"] == "b"
    assert by_id["d"]["parent_id"] == "c"
    # Exactly one root: no flat dump of subfolders.
    assert [n for n in nodes if n["parent_id"] is None][0]["id"] == "a"


# ---------------------------------------------------------------------------
# DriveFoldersPage widget
# ---------------------------------------------------------------------------
NODES = [
    {"id": "root1", "name": "Math", "parent_id": None, "root": "My Drive"},
    {"id": "f1", "name": "Grade 8", "parent_id": "root1", "root": "My Drive"},
    {"id": "f2", "name": "Grade 9", "parent_id": "root1", "root": "My Drive"},
    {"id": "shared1", "name": "From Bob", "parent_id": None, "root": "Shared with me"},
    {"id": "sd1", "name": "Team", "parent_id": None, "root": "Team Drive"},
]


def _make_page():
    return DriveFoldersPage()


def test_page_populates_tree_and_restores_selection_by_folder_id():
    page = _make_page()
    page.set_selected(["https://drive.google.com/drive/folders/f1?usp=drive_link"])
    page.populate_tree(NODES)

    assert page.selected_urls() == ["https://drive.google.com/drive/folders/f1"]
    assert page.count_label.text() == "1 of 5 folders selected"
    assert page.apply_button.isEnabled()


def test_page_group_toggle_propagates_to_children():
    page = _make_page()
    page.populate_tree(NODES)

    group = page.folder_tree.topLevelItem(0)  # "My Drive" group
    group.setCheckState(0, Qt.Checked)
    assert sorted(page.selected_urls()) == [
        "https://drive.google.com/drive/folders/f1",
        "https://drive.google.com/drive/folders/f2",
        "https://drive.google.com/drive/folders/root1",
    ]

    group.setCheckState(0, Qt.Unchecked)
    assert page.selected_urls() == []


def test_page_select_all_and_clear():
    page = _make_page()
    page.populate_tree(NODES)

    page._select_all()
    assert len(page.selected_urls()) == 5
    page._clear_selection()
    assert page.selected_urls() == []
    assert page.count_label.text() == "0 of 5 folders selected"


def test_page_scan_state_controls_buttons():
    page = _make_page()
    assert not page.apply_button.isEnabled()
    page.set_scan_state("Scanning…", scanning=True)
    assert not page.scan_button.isEnabled()
    page.populate_tree(NODES)
    page.set_scan_state("Done", scanning=False)
    assert page.scan_button.isEnabled()
    assert page.scan_button.text() == "Rescan Drive"
    assert page.apply_button.isEnabled()


def test_page_apply_signal_emits_folder_urls():
    page = _make_page()
    page.populate_tree(NODES)
    page._select_all()

    emitted = []
    page.apply_clicked.connect(emitted.extend)
    page._emit_apply()
    assert len(emitted) == 5
    assert all(url.startswith("https://drive.google.com/drive/folders/") for url in emitted)


def test_page_group_order_is_deterministic():
    page = _make_page()
    # Nodes may arrive drive-first; groups must still render in a fixed order.
    page.populate_tree([
        {"id": "z1", "name": "Z Drive root", "parent_id": None, "root": "Z Drive"},
        {"id": "s1", "name": "Shared", "parent_id": None, "root": "Shared with me"},
        {"id": "m1", "name": "Mine", "parent_id": None, "root": "My Drive"},
        {"id": "a1", "name": "A Drive root", "parent_id": None, "root": "A Drive"},
    ])
    labels = [
        page.folder_tree.topLevelItem(i).text(0)
        for i in range(page.folder_tree.topLevelItemCount())
    ]
    assert labels == ["My Drive", "Shared with me", "A Drive", "Z Drive"]


def test_page_filter_hides_non_matching_folders():
    page = _make_page()
    page.populate_tree(NODES)

    def visible_names():
        names = []

        def walk(item):
            if not item.isHidden():
                names.append(item.text(0))
            for i in range(item.childCount()):
                walk(item.child(i))

        root = page.folder_tree.invisibleRootItem()
        for i in range(root.childCount()):
            walk(root.child(i))
        return names

    page._apply_filter("grade 8")
    # "Grade 8" matches; its ancestor "Math" stays visible; others are hidden.
    assert visible_names() == ["My Drive", "Math", "Grade 8"]

    page._apply_filter("")
    assert sorted(visible_names()) == [
        "From Bob", "Grade 8", "Grade 9", "Math", "My Drive", "Shared with me", "Team", "Team Drive",
    ]


# ---------------------------------------------------------------------------
# Window wiring
# ---------------------------------------------------------------------------
def _make_window():
    window = AutograderWindow()
    window.save_forms = lambda: None
    return window


def test_drive_page_registered_in_tree_and_view_menu():
    window = _make_window()
    assert "drive" in window._tree_items
    assert window.stack.currentWidget() is not window.drive_page

    window._goto_page("drive")
    assert window.stack.currentWidget() is window.drive_page

    view_actions = [action.text() for action in window._view_actions.values()]
    assert "Drive Folders" in view_actions


def test_window_apply_updates_folders_and_predefined(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    window = _make_window()

    emitted = [
        "https://drive.google.com/drive/folders/AAA",
        "https://drive.google.com/drive/folders/BBB",
    ]
    window.apply_drive_folder_selection(emitted)

    assert window.folders == emitted
    with open("predefined_folders.json", "r", encoding="utf-8") as fh:
        saved = json.load(fh).get("folders", [])
    assert saved == emitted


def test_window_startup_loads_saved_folder_scope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("predefined_folders.json", "w", encoding="utf-8") as fh:
        json.dump({"folders": [
            "https://drive.google.com/drive/folders/AAA?usp=drive_link",
            "https://docs.google.com/forms/d/FORMA/edit",
        ]}, fh)

    window = _make_window()
    assert window.folders == ["https://drive.google.com/drive/folders/AAA?usp=drive_link"]
    # The page pre-checks those folders (matched by id) for the next scan.
    page_ids = {fid for fid in (
        item.data(0, Qt.UserRole) for item in window.drive_page._iter_folder_items()
    ) if fid}
    assert page_ids == set()  # empty until a scan populates the tree
    assert "AAA" in window.drive_page._checked_ids


def test_window_drive_scan_finished_populates_page(monkeypatch):
    window = _make_window()
    monkeypatch.setattr(AutograderWindow, "_notify", lambda self, *a, **k: None)
    monkeypatch.setattr(
        "gui_studio.main_window.load_selected_folders",
        lambda: ["https://drive.google.com/drive/folders/AAA?usp=drive_link"],
    )

    window._on_drive_scan_finished([
        {"id": "AAA", "name": "Math", "parent_id": None, "root": "My Drive"},
    ])
    urls = window.drive_page.selected_urls()
    assert "https://drive.google.com/drive/folders/AAA" in urls

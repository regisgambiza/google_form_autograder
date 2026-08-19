# gui_studio/drive_folders.py - Whole-Drive folder scan for the Drive Folders page.
#
# Lists every folder visible to the signed-in account (My Drive, folders
# shared with the account, and Shared Drives) so the user can pick the ones
# auto-run should scan. Selections are persisted into predefined_folders.json
# (form-URL entries are preserved) so Grade All and the schedule dialog share
# the same source of truth.
import json

from PySide6.QtCore import QThread, Signal

from logger import log

DRIVE_FOLDER_URL_TEMPLATE = "https://drive.google.com/drive/folders/{folder_id}"


def folder_url(folder_id):
    return DRIVE_FOLDER_URL_TEMPLATE.format(folder_id=folder_id)


def is_folder_url(url):
    return isinstance(url, str) and "/folders/" in url


def load_predefined_entries():
    try:
        with open("predefined_folders.json", "r", encoding="utf-8") as fh:
            return json.load(fh).get("folders", []) or []
    except Exception:
        return []


def save_predefined_entries(folders):
    with open("predefined_folders.json", "w", encoding="utf-8") as fh:
        json.dump({"folders": folders}, fh, indent=2)


def save_selected_folders(folder_urls):
    """Replace folder entries in predefined_folders.json with the selection.

    Non-folder (Google Form URL) entries are preserved so manually added
    single-form sources are never lost.
    """
    folder_urls = [url for url in (folder_urls or []) if url]
    existing = load_predefined_entries()
    kept = [entry for entry in existing if not is_folder_url(entry)]
    merged = kept + [url for url in folder_urls if url not in kept]
    save_predefined_entries(merged)
    return merged


def load_selected_folders():
    """Folder-URL entries currently stored in predefined_folders.json."""
    return [entry for entry in load_predefined_entries() if is_folder_url(entry)]


class DriveFolderScanThread(QThread):
    """Walk the whole Drive and emit the folder hierarchy once, as a flat
    list of nodes: {"id", "name", "parent_id", "root"}.

    parent_id is None for top-level nodes; "root" names the container the
    folder lives under ("My Drive", "Shared with me", or a Shared Drive name).
    """

    progress = Signal(str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            nodes = scan_whole_drive(
                progress_callback=self.progress.emit,
                cancel_check=lambda: self._cancelled,
            )
            if self._cancelled:
                return
            self.finished.emit(nodes)
        except Exception as exc:
            log("ERROR", f"Drive folder scan failed: {exc}")
            if not self._cancelled:
                self.failed.emit(str(exc))


def _list_pages(drive_service, request_builder, context, progress_callback=None, items_key="files"):
    """Iterate a paged Drive list request, retrying transient errors."""
    from form_searcher import _execute_with_retries

    page_token = None
    items = []
    while True:
        request = request_builder(page_token)
        result = _execute_with_retries(
            request,
            context=context,
            progress_callback=progress_callback,
        )
        items.extend(result.get(items_key, []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return items


def scan_whole_drive(progress_callback=None, cancel_check=None):
    """Return every visible folder as flat node dicts (see DriveFolderScanThread)."""
    from auth import get_drive_service

    drive_service = get_drive_service()
    nodes = []
    seen_ids = set()

    def add_folder(fid, name, parent_id, root_label):
        if not fid or fid in seen_ids:
            return
        seen_ids.add(fid)
        nodes.append({"id": fid, "name": name, "parent_id": parent_id, "root": root_label})

    # --- My Drive + folders shared with the account -----------------------
    if progress_callback:
        progress_callback("Scanning My Drive folders…")
    user_folders = _list_pages(
        drive_service,
        lambda token: drive_service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="nextPageToken, files(id, name, parents)",
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ),
        context="Drive folder scan (My Drive)",
        progress_callback=progress_callback,
    )

    user_ids = {folder.get("id") for folder in user_folders if folder.get("id")}
    by_parent = {}
    for folder in user_folders:
        fid = folder.get("id")
        if not fid:
            continue
        for parent in folder.get("parents", []) or [None]:
            by_parent.setdefault(parent, []).append(folder)

    try:
        root_id = (
            drive_service.files().get(fileId="root", fields="id").execute().get("id")
        )
    except Exception:
        root_id = None

    # Top-level My Drive folders list the My Drive root as their parent;
    # shared-with-me folders reference parents that are not visible at all.
    def is_root_folder(folder):
        parents = folder.get("parents", []) or []
        if not parents:
            return True
        if root_id and root_id in parents:
            return True
        return not any(parent in user_ids for parent in parents)

    def walk_children(parent_id, root_label):
        for folder in by_parent.get(parent_id, []):
            fid = folder.get("id")
            add_folder(fid, folder.get("name", "Untitled"), parent_id, root_label)
            if cancel_check and cancel_check():
                return
            walk_children(fid, root_label)

    for folder in user_folders:
        fid = folder.get("id")
        if not fid or fid in seen_ids:
            continue
        if is_root_folder(folder):
            parents = folder.get("parents", []) or []
            root_label = "My Drive" if (root_id and root_id in parents) or not parents \
                else "Shared with me"
            add_folder(fid, folder.get("name", "Untitled"), None, root_label)
            walk_children(fid, root_label)
            if cancel_check and cancel_check():
                return nodes

    # Orphaned folders (parents unreachable) still get shown as shared roots.
    for folder in user_folders:
        fid = folder.get("id")
        if fid and fid not in seen_ids:
            add_folder(fid, folder.get("name", "Untitled"), None, "Shared with me")

    # --- Shared Drives ------------------------------------------------------
    if progress_callback:
        progress_callback("Scanning Shared Drives…")
    try:
        drives = _list_pages(
            drive_service,
            lambda token: drive_service.drives().list(pageSize=100, pageToken=token),
            context="Shared Drives list",
            progress_callback=progress_callback,
            items_key="drives",
        )
    except Exception as exc:
        log("WARNING", f"Shared Drives unavailable: {exc}")
        drives = []

    for drive in drives:
        if cancel_check and cancel_check():
            break
        drive_id = drive.get("id")
        drive_name = drive.get("name", "Shared Drive")
        if not drive_id:
            continue
        if progress_callback:
            progress_callback(f"Scanning Shared Drive “{drive_name}”…")
        try:
            drive_folders = _list_pages(
                drive_service,
                lambda token, did=drive_id: drive_service.files().list(
                    q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                    corpora="drive",
                    driveId=did,
                    fields="nextPageToken, files(id, name, parents)",
                    pageSize=1000,
                    pageToken=token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ),
                context=f"Drive folder scan ({drive_name})",
                progress_callback=progress_callback,
            )
        except Exception as exc:
            log("WARNING", f"Could not scan Shared Drive '{drive_name}': {exc}")
            continue

        drive_ids = {folder.get("id") for folder in drive_folders if folder.get("id")}
        drive_parents = {}
        for folder in drive_folders:
            fid = folder.get("id")
            if not fid:
                continue
            for parent in folder.get("parents", []) or [None]:
                drive_parents.setdefault(parent, []).append(folder)

        def walk_drive_children(parent_id):
            for folder in drive_parents.get(parent_id, []):
                fid = folder.get("id")
                add_folder(fid, folder.get("name", "Untitled"), parent_id, drive_name)
                if cancel_check and cancel_check():
                    return
                walk_drive_children(fid)

        # Top-level folders in a shared drive have the drive itself (or the
        # drive root folder, itself a folder) as parent.
        for folder in drive_folders:
            fid = folder.get("id")
            if not fid or fid in seen_ids:
                continue
            parents = folder.get("parents", []) or []
            if not any(parent in drive_ids and parent != fid for parent in parents):
                add_folder(fid, folder.get("name", "Untitled"), None, drive_name)
                walk_drive_children(fid)
                if cancel_check and cancel_check():
                    return nodes

        # Orphaned shared-drive folders still get shown under the drive name.
        for folder in drive_folders:
            fid = folder.get("id")
            if fid and fid not in seen_ids:
                add_folder(fid, folder.get("name", "Untitled"), None, drive_name)

    if progress_callback:
        progress_callback(f"Found {len(nodes)} folder(s)")
    return nodes

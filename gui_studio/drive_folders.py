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
    Folders visible through two paths (e.g. in a Shared Drive and shared
    with the account directly) appear exactly once, nested in their real
    container.
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
    """Return every visible folder as flat node dicts (see DriveFolderScanThread).

    Shared Drives are scanned first so folders that are also shared with the
    account directly still nest under their real Drive parent instead of
    surfacing twice or collapsing into a flat list.
    """
    from auth import get_drive_service

    drive_service = get_drive_service()
    nodes = []
    seen_ids = set()

    def add_node(fid, name, parent_id, root_label):
        if not fid or fid in seen_ids:
            return False
        seen_ids.add(fid)
        nodes.append({"id": fid, "name": name, "parent_id": parent_id, "root": root_label})
        return True

    def attach_tree(folder_records, default_label, root_label_fn=None):
        """Nest folder_records following their real Drive hierarchy.

        A folder is a root when none of its parents is another folder from
        the same container (My Drive root / drive root / invisible parents
        all count as "no parent here"). Roots are labelled via root_label_fn
        when given, descendants inherit that label. Folders already placed
        by an earlier container are skipped, so items visible through two
        paths appear exactly once, in their real container.
        """
        ids = {f.get("id") for f in folder_records if f.get("id")}
        by_parent = {}
        for folder in folder_records:
            fid = folder.get("id")
            if not fid:
                continue
            for parent in folder.get("parents") or [None]:
                by_parent.setdefault(parent, []).append(folder)

        def walk(folder, parent_id, label):
            fid = folder.get("id")
            if not add_node(fid, folder.get("name", "Untitled"), parent_id, label):
                return  # already placed in an earlier container
            for child in by_parent.get(fid, []):
                walk(child, fid, label)

        for folder in folder_records:
            fid = folder.get("id")
            if not fid:
                continue
            if any(p in ids and p != fid for p in folder.get("parents") or []):
                continue  # not a root: its parent is in this container
            if cancel_check and cancel_check():
                return
            parents = folder.get("parents") or []
            label = root_label_fn(parents) if root_label_fn else default_label
            walk(folder, None, label)

        # Safety net for unreachable chains/cycles: keep them visible.
        for folder in folder_records:
            fid = folder.get("id")
            if fid and fid not in seen_ids:
                add_node(fid, folder.get("name", "Untitled"), None, default_label)

    # --- Shared Drives first (their folders win the nesting) ---------------
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
        attach_tree(drive_folders, drive_name)

    # --- My Drive + folders shared with the account ------------------------
    if progress_callback:
        progress_callback("Scanning My Drive folders…")
    user_folders = _list_pages(
        drive_service,
        lambda token: drive_service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            corpora="user",
            fields="nextPageToken, files(id, name, parents)",
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ),
        context="Drive folder scan (My Drive)",
        progress_callback=progress_callback,
    )

    try:
        root_id = (
            drive_service.files().get(fileId="root", fields="id").execute().get("id")
        )
    except Exception:
        root_id = None

    def user_root_label(parents):
        if root_id and root_id in parents:
            return "My Drive"
        return "Shared with me"

    attach_tree(user_folders, "Shared with me", root_label_fn=user_root_label)

    if progress_callback:
        progress_callback(f"Found {len(nodes)} folder(s)")
    return nodes

# form_searcher.py - OPTIMIZED FOR AUTO MODE: USE FILTER FOR RECENT RESPONSES ONLY
import json
from datetime import datetime, timezone
from auth import get_drive_service, get_service
from logger import log


def parse_folder_identifier(identifier):
    identifier = identifier.strip()
    drive_service = get_drive_service()

    if identifier.startswith('http'):
        if '/folders/' in identifier:
            folder_id = identifier.split('/folders/')[1].split('?')[0]
        elif '/d/' in identifier:
            folder_id = identifier.split('/d/')[1].split('/')[0]
        else:
            log("WARNING", f"Invalid folder URL: {identifier}")
            return []
        return [folder_id]

    if len(identifier) > 20 and all(c.isalnum() or c in '-_' for c in identifier):
        return [identifier]

    query = (
        f"name='{identifier}' and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    folders = results.get('files', [])

    if not folders:
        log("WARNING", f"No folder found with name: {identifier}")
        return []

    return [f['id'] for f in folders]


def get_last_submission_time(form_id, from_dt=None, progress_callback=None):
    """
    Get the latest submission time.
    If from_dt is provided (UTC aware), use filter to fetch only recent responses for efficiency.
    """
    if progress_callback:
        progress_callback(f"Checking submissions for form {form_id}")

    forms_service = get_service()

    filter_str = None
    if from_dt:
        # Use >= to include submissions exactly at from_dt (safe for incremental)
        ts_str = from_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        filter_str = f"timestamp >= {ts_str}"

    try:
        times = []
        page_token = None
        while True:
            result = forms_service.forms().responses().list(
                formId=form_id,
                filter=filter_str,
                pageToken=page_token
            ).execute()

            responses = result.get('responses', [])
            for resp in responses:
                ts_str = resp.get('lastSubmittedTime')
                if ts_str:
                    times.append(datetime.fromisoformat(ts_str.replace('Z', '+00:00')))

            page_token = result.get('nextPageToken')
            if not page_token:
                break

        return max(times) if times else None

    except Exception as e:
        log("ERROR", f"Error fetching responses for form {form_id}: {e}")
        return None


def find_forms_in_folder(folder_id, from_dt, to_dt, visited=None, progress_callback=None):
    if visited is None:
        visited = set()
    if folder_id in visited:
        return []

    visited.add(folder_id)

    drive_service = get_drive_service()

    form_query = (
        f"'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.form' and trashed=false"
    )
    forms = drive_service.files().list(
        q=form_query, fields="files(id, name)"
    ).execute().get('files', [])

    matching = []

    for form in forms:
        form_id = form['id']
        title = form.get('name', 'Untitled')

        if progress_callback:
            progress_callback(f"Checking form: {title}")

        last_ts = get_last_submission_time(form_id, from_dt=from_dt, progress_callback=progress_callback)
        if last_ts and from_dt <= last_ts <= to_dt:
            matching.append({
                "url": f"https://docs.google.com/forms/d/{form_id}/edit",
                "title": title,
                "last_submission": last_ts.replace(tzinfo=None)
            })

    # Recurse into subfolders
    folder_query = (
        f"'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    subfolders = drive_service.files().list(
        q=folder_query, fields="files(id)"
    ).execute().get('files', [])

    for sub in subfolders:
        matching.extend(
            find_forms_in_folder(
                sub['id'], from_dt, to_dt, visited, progress_callback
            )
        )

    return matching


def find_forms_with_submissions_in_range(
    folder_identifiers, from_dt, to_dt, progress_callback=None
):
    if from_dt.tzinfo is None or to_dt.tzinfo is None:
        raise ValueError(
            "from_dt and to_dt must be timezone-aware (UTC) datetimes"
        )

    all_folder_ids = set()
    for ident in folder_identifiers:
        all_folder_ids.update(parse_folder_identifier(ident))

    all_forms = []
    for folder_id in all_folder_ids:
        if progress_callback:
            progress_callback(f"Searching folder {folder_id}")
        all_forms.extend(
            find_forms_in_folder(folder_id, from_dt, to_dt, progress_callback=progress_callback)
        )

    # Deduplicate
    return list({f['url']: f for f in all_forms}.values())


def load_predefined_folders():
    try:
        with open("predefined_folders.json", "r") as f:
            return json.load(f).get("folders", [])
    except Exception:
        return []


def save_predefined_folders(folders):
    with open("predefined_folders.json", "w") as f:
        json.dump({"folders": folders}, f, indent=2)
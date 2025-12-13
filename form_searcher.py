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


def get_last_submission_time(form_id, progress_callback=None):
    if progress_callback:
        progress_callback(f"Checking submissions for form {form_id}")

    forms_service = get_service()

    try:
        result = forms_service.forms().responses().list(
            formId=form_id
        ).execute()

        responses = result.get('responses', [])
        if not responses:
            return None

        times = []
        for resp in responses:
            ts = resp.get('lastSubmittedTime')
            if ts:
                times.append(
                    datetime.fromisoformat(ts.replace('Z', '+00:00'))
                )

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

        last_ts = get_last_submission_time(form_id, progress_callback)
        if last_ts and from_dt <= last_ts <= to_dt:
            matching.append({
                "url": f"https://docs.google.com/forms/d/{form_id}/edit",
                "title": title,
                "last_submission": last_ts.replace(tzinfo=None)
            })

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

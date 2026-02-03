# form_searcher.py - OPTIMIZED FOR AUTO MODE: USE FILTER FOR RECENT RESPONSES ONLY
import json
import random
import time
from datetime import datetime, timezone
from auth import get_drive_service, get_service
from logger import log
from googleapiclient.errors import HttpError


def _is_retryable_error(err):
    if isinstance(err, HttpError):
        status = getattr(err, "resp", None)
        status = status.status if status else None
        return status in {429, 500, 502, 503, 504}
    return isinstance(err, OSError)


def _execute_with_retries(request, context="", progress_callback=None, max_retries=5):
    attempt = 0
    while True:
        try:
            return request.execute()
        except Exception as e:
            retryable = _is_retryable_error(e)
            if not retryable or attempt >= max_retries:
                log("ERROR", f"{context} failed after {attempt + 1} attempt(s): {e}")
                raise

            delay = min(8.0, 0.5 * (2 ** attempt))
            delay = delay * (0.5 + random.random())
            attempt += 1

            log("WARNING", f"{context} transient error: {e}. Retrying in {delay:.2f}s")
            if progress_callback:
                progress_callback(
                    f"Transient error. Retrying ({attempt}/{max_retries + 1})"
                )
            time.sleep(delay)


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
    try:
        results = _execute_with_retries(
            drive_service.files().list(q=query, fields="files(id)"),
            context="Drive folder lookup",
        )
    except Exception as e:
        log("ERROR", f"Folder lookup failed for '{identifier}': {e}")
        return []
    folders = results.get('files', [])

    if not folders:
        log("WARNING", f"No folder found with name: {identifier}")
        return []

    return [f['id'] for f in folders]


def get_last_submission_time(form_id, from_dt=None, progress_callback=None):
    """
    Get the latest submission time.
    WARNING: Google Forms API filter support is unreliable.
    We fetch ALL responses and filter in Python for reliability.
    """
    if progress_callback:
        progress_callback(f"Checking submissions for form {form_id}")

    forms_service = get_service()

    try:
        times = []
        page_token = None
        
        # Fetch ALL responses (no filter - Google's filter is unreliable)
        while True:
            result = _execute_with_retries(
                forms_service.forms().responses().list(
                    formId=form_id,
                    pageToken=page_token
                ),
                context=f"Forms responses list for {form_id}",
                progress_callback=progress_callback,
            )

            responses = result.get('responses', [])
            for resp in responses:
                ts_str = resp.get('lastSubmittedTime')
                if ts_str:
                    dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    
                    # Filter in Python (reliable)
                    if from_dt is None or dt >= from_dt:
                        times.append(dt)

            page_token = result.get('nextPageToken')
            if not page_token:
                break

        result = max(times) if times else None
        log("DEBUG", f"Form {form_id}: Found {len(times)} submissions in range, latest: {result}")
        return result

    except Exception as e:
        if isinstance(e, HttpError):
            status = getattr(e, "resp", None)
            status = status.status if status else None
            if status == 403:
                log(
                    "WARNING",
                    f"Skipping form {form_id}: not authorized to read responses.",
                )
                return None
        log("ERROR", f"Error fetching responses for form {form_id}: {e}")
        return None


def find_forms_in_folder(folder_id, from_dt, to_dt, visited=None, progress_callback=None, seen_forms=None):
    if visited is None:
        visited = set()
    if seen_forms is None:
        seen_forms = set()
    if folder_id in visited:
        return []

    visited.add(folder_id)

    drive_service = get_drive_service()

    form_query = (
        f"'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.form' and trashed=false"
    )
    try:
        forms = _execute_with_retries(
            drive_service.files().list(q=form_query, fields="files(id, name)"),
            context=f"Drive forms list in folder {folder_id}",
            progress_callback=progress_callback,
        ).get('files', [])
    except Exception as e:
        log("ERROR", f"Drive forms list failed in folder {folder_id}: {e}")
        return []

    matching = []

    for form in forms:
        form_id = form['id']
        title = form.get('name', 'Untitled')
        
        # Skip if we've already processed this form
        if form_id in seen_forms:
            log("DEBUG", f"Skipping duplicate form check: {title} (already checked)")
            continue
        seen_forms.add(form_id)

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
    try:
        subfolders = _execute_with_retries(
            drive_service.files().list(q=folder_query, fields="files(id)"),
            context=f"Drive subfolder list in folder {folder_id}",
            progress_callback=progress_callback,
        ).get('files', [])
    except Exception as e:
        log("ERROR", f"Drive subfolder list failed in folder {folder_id}: {e}")
        return matching

    for sub in subfolders:
        matching.extend(
            find_forms_in_folder(
                sub['id'], from_dt, to_dt, visited, progress_callback, seen_forms
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
    seen_forms = set()
    for folder_id in all_folder_ids:
        if progress_callback:
            progress_callback(f"Searching folder {folder_id}")
        all_forms.extend(
            find_forms_in_folder(folder_id, from_dt, to_dt, progress_callback=progress_callback, seen_forms=seen_forms)
        )

    # Deduplicate (extra safety)
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

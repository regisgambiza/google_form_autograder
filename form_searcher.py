# form_searcher.py - OPTIMIZED FOR AUTO MODE: USE FILTER FOR RECENT RESPONSES ONLY
import json
import random
import re
import time
from datetime import datetime, timezone, timedelta
from auth import get_drive_service, get_service
from logger import log
from googleapiclient.errors import HttpError

BANGKOK_TZ = timezone(timedelta(hours=7))


def split_identifiers(identifiers):
    if isinstance(identifiers, str):
        raw_items = re.split(r"[\n,]+", identifiers)
    else:
        raw_items = []
        for identifier in identifiers:
            raw_items.extend(re.split(r"[\n,]+", str(identifier)))
    return [item.strip() for item in raw_items if item.strip()]


def extract_form_id(identifier):
    identifier = identifier.strip()
    match = re.search(r"docs\.google\.com/forms/d/(?:e/)?([^/?#]+)", identifier)
    if match:
        return match.group(1)
    return None


def normalize_form_url(form_id):
    return f"https://docs.google.com/forms/d/{form_id}/edit"


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


def parse_folder_identifier(identifier, drive_service=None):
    identifier = identifier.strip()
    if identifier.startswith('http'):
        if '/folders/' in identifier:
            folder_id = identifier.split('/folders/')[1].split('?')[0]
        else:
            log("WARNING", f"Invalid folder URL: {identifier}")
            return []
        return [folder_id]

    if len(identifier) > 20 and all(c.isalnum() or c in '-_' for c in identifier):
        return [identifier]

    # Only URL-shaped/plain-id identifiers short-circuit above without any
    # Drive access. Building an API client here used to happen for EVERY
    # identifier (even those that need no Drive lookup), producing multi-build
    # bursts on GUI worker threads at each auto-run cycle.
    if drive_service is None:
        drive_service = get_drive_service()

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


def get_form_title(form_id, progress_callback=None, fallback_title="Untitled", forms_service=None):
    if forms_service is None:
        forms_service = get_service()
    try:
        form = _execute_with_retries(
            forms_service.forms().get(formId=form_id),
            context=f"Forms metadata get for {form_id}",
            progress_callback=progress_callback,
        )
        return form.get("info", {}).get("title") or form.get("title") or fallback_title
    except Exception as e:
        log("WARNING", f"Could not fetch title for form {form_id}: {e}")
        return fallback_title


def get_last_submission_time(form_id, from_dt=None, progress_callback=None, forms_service=None):
    """
    Get the latest submission time.
    WARNING: Google Forms API filter support is unreliable.
    We fetch ALL responses and filter in Python for reliability.
    """
    if progress_callback:
        progress_callback(f"Checking submissions for form {form_id}")

    if forms_service is None:
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
                # Prefer submitTime (used by main grading flow), then fall back.
                ts_str = (
                    resp.get("submitTime")
                    or resp.get("lastSubmittedTime")
                    or resp.get("createTime")
                )
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


def find_forms_in_folder(folder_id, from_dt, to_dt, visited=None, progress_callback=None,
                         seen_forms=None, drive_service=None, forms_service=None):
    if visited is None:
        visited = set()
    if seen_forms is None:
        seen_forms = set()
    if folder_id in visited:
        return []

    visited.add(folder_id)

    # One Drive client per scan (shared through recursion) instead of one per
    # folder: repeated client construction on GUI worker threads was both
    # wasteful and the dominant activity in every recorded GUI crash.
    if drive_service is None:
        drive_service = get_drive_service()
    if forms_service is None:
        forms_service = get_service()

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
        
        # Skip if we've already processed this form
        if form_id in seen_forms:
            log("DEBUG", f"Skipping duplicate form check: {form_id} (already checked)")
            continue
        seen_forms.add(form_id)
        drive_title = form.get('name', 'Untitled')
        title = get_form_title(form_id, progress_callback=progress_callback,
                               fallback_title=drive_title, forms_service=forms_service)

        if progress_callback:
            progress_callback(f"Checking form: {title}")

        last_ts = get_last_submission_time(form_id, from_dt=from_dt,
                                           progress_callback=progress_callback,
                                           forms_service=forms_service)
        if last_ts and from_dt <= last_ts <= to_dt:
            matching.append({
                "url": f"https://docs.google.com/forms/d/{form_id}/edit",
                "title": title,
                "last_submission": last_ts
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
                sub['id'], from_dt, to_dt, visited, progress_callback, seen_forms,
                drive_service=drive_service, forms_service=forms_service,
            )
        )

    return matching


def find_all_forms_in_folder(folder_id, visited=None, progress_callback=None,
                             seen_forms=None, drive_service=None, forms_service=None):
    if visited is None:
        visited = set()
    if seen_forms is None:
        seen_forms = set()
    if folder_id in visited:
        return []

    visited.add(folder_id)
    if drive_service is None:
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
        if form_id in seen_forms:
            continue
        seen_forms.add(form_id)
        matching.append({
            "url": normalize_form_url(form_id),
            "title": get_form_title(
                form_id,
                progress_callback=progress_callback,
                fallback_title=form.get('name', 'Untitled'),
                forms_service=forms_service,
            ),
            "last_submission": None,
        })

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
            find_all_forms_in_folder(sub['id'], visited, progress_callback, seen_forms,
                                     drive_service=drive_service, forms_service=forms_service)
        )

    return matching


def find_all_forms_in_sources(sources, progress_callback=None):
    all_folder_ids = set()
    all_form_ids = set()
    # Build each service at most once for the whole scan.
    drive_service = None
    forms_service = None
    for ident in split_identifiers(sources):
        form_id = extract_form_id(ident)
        if form_id:
            all_form_ids.add(form_id)
            continue
        if drive_service is None and not str(ident).strip().startswith('http'):
            drive_service = get_drive_service()
        all_folder_ids.update(parse_folder_identifier(ident, drive_service=drive_service))

    if all_form_ids and forms_service is None:
        forms_service = get_service()
    if all_folder_ids and drive_service is None:
        drive_service = get_drive_service()

    all_forms = []
    seen_forms = set()
    for form_id in all_form_ids:
        if form_id in seen_forms:
            continue
        seen_forms.add(form_id)
        if progress_callback:
            progress_callback(f"Adding form URL {form_id}")
        all_forms.append({
            "url": normalize_form_url(form_id),
            "title": get_form_title(form_id, progress_callback=progress_callback,
                                    forms_service=forms_service),
            "last_submission": None,
        })

    for folder_id in all_folder_ids:
        if progress_callback:
            progress_callback(f"Finding forms in folder {folder_id}")
        all_forms.extend(
            find_all_forms_in_folder(
                folder_id, progress_callback=progress_callback, seen_forms=seen_forms,
                drive_service=drive_service, forms_service=forms_service,
            )
        )

    return list({f['url']: f for f in all_forms}.values())


def find_forms_with_submissions_in_range(
    folder_identifiers, from_dt, to_dt, progress_callback=None
):
    if from_dt.tzinfo is None or to_dt.tzinfo is None:
        raise ValueError(
            "from_dt and to_dt must be timezone-aware (UTC) datetimes"
        )

    all_folder_ids = set()
    all_form_ids = set()
    # Build each service at most once for the whole scan. Previously every
    # identifier/folder/form constructed fresh API clients; on the GUI's auto
    # -run worker thread this produced rapid client-construction bursts that
    # correlated with every recorded native abort of the GUI process.
    drive_service = None
    forms_service = None
    for ident in split_identifiers(folder_identifiers):
        form_id = extract_form_id(ident)
        if form_id:
            all_form_ids.add(form_id)
            continue
        if drive_service is None and not str(ident).strip().startswith('http'):
            drive_service = get_drive_service()
        all_folder_ids.update(parse_folder_identifier(ident, drive_service=drive_service))

    if (all_form_ids or all_folder_ids) and forms_service is None:
        forms_service = get_service()
    if all_folder_ids and drive_service is None:
        drive_service = get_drive_service()

    all_forms = []
    seen_forms = set()
    for form_id in all_form_ids:
        if form_id in seen_forms:
            continue
        seen_forms.add(form_id)

        if progress_callback:
            progress_callback(f"Checking form URL {form_id}")

        title = get_form_title(form_id, progress_callback=progress_callback,
                               forms_service=forms_service)
        last_ts = get_last_submission_time(
            form_id, from_dt=from_dt, progress_callback=progress_callback,
            forms_service=forms_service,
        )
        if last_ts and from_dt <= last_ts <= to_dt:
            all_forms.append({
                "url": normalize_form_url(form_id),
                "title": title,
                "last_submission": last_ts,
            })

    for folder_id in all_folder_ids:
        if progress_callback:
            progress_callback(f"Searching folder {folder_id}")
        all_forms.extend(
            find_forms_in_folder(folder_id, from_dt, to_dt, progress_callback=progress_callback,
                                 seen_forms=seen_forms, drive_service=drive_service,
                                 forms_service=forms_service)
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

# form_searcher.py (Modified)
import json
from datetime import datetime
from auth import get_drive_service, get_service
from logger import log

def parse_folder_identifier(identifier):
    """Parse folder name, ID, or URL to get folder ID(s)."""
    identifier = identifier.strip()
    drive_service = get_drive_service()

    if identifier.startswith('http'):
        # Extract ID from URL
        if '/folders/' in identifier:
            folder_id = identifier.split('/folders/')[1].split('?')[0]
        elif '/d/' in identifier:
            folder_id = identifier.split('/d/')[1].split('/')[0]
        else:
            log("WARNING", f"Invalid folder URL: {identifier}")
            return []
        return [folder_id]
    elif len(identifier) > 20 and all(c.isalnum() or c in '-_' for c in identifier):
        # Assume it's an ID
        return [identifier]
    else:
        # Search by name
        query = f"name='{identifier}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = drive_service.files().list(q=query, fields="files(id)").execute()
        folders = results.get('files', [])
        if not folders:
            log("WARNING", f"No folder found with name: {identifier}")
            return []
        ids = [f['id'] for f in folders]
        log("DEBUG", f"Found {len(ids)} folders with name '{identifier}': {ids}")
        return ids

def get_last_submission_time(form_id):
    """Get the latest lastSubmittedTime for a form's responses."""
    forms_service = get_service()
    try:
        result = forms_service.forms().responses().list(formId=form_id).execute()
        responses = result.get('responses', [])
        if not responses:
            return None
        times = []
        for resp in responses:
            ts_str = resp.get('lastSubmittedTime')
            if ts_str:
                times.append(datetime.fromisoformat(ts_str.replace('Z', '+00:00')))
        if not times:
            return None
        return max(times)
    except Exception as e:
        log("ERROR", f"Error fetching responses for form {form_id}: {e}")
        return None

def find_forms_in_folder(folder_id, from_date, to_date, visited=None):
    """Recursively find forms in a folder and subfolders that had submissions in the date range."""
    if visited is None:
        visited = set()
    if folder_id in visited:
        return []
    visited.add(folder_id)

    drive_service = get_drive_service()
    
    # Find forms
    form_query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.form' and trashed=false"
    form_results = drive_service.files().list(q=form_query, fields="files(id, name)").execute()
    forms = form_results.get('files', [])
    
    matching_forms = []
    for form in forms:
        form_id = form['id']
        last_ts = get_last_submission_time(form_id)
        if last_ts and from_date <= last_ts.date() <= to_date:
            title = form.get('name', 'Untitled')
            edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"
            matching_forms.append({'url': edit_url, 'title': title, 'last_submission': last_ts})

    # Find subfolders and recurse
    folder_query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    folder_results = drive_service.files().list(q=folder_query, fields="files(id)").execute()
    subfolders = folder_results.get('files', [])
    for subfolder in subfolders:
        sub_id = subfolder['id']
        sub_forms = find_forms_in_folder(sub_id, from_date, to_date, visited)
        matching_forms.extend(sub_forms)

    return matching_forms

def find_forms_with_submissions_in_range(folder_identifiers, from_date, to_date):
    """Main function to find forms based on folders and date range."""
    all_folder_ids = set()
    for ident in folder_identifiers:
        ids = parse_folder_identifier(ident)
        all_folder_ids.update(ids)
    
    all_forms = []
    for folder_id in all_folder_ids:
        log("INFO", f"Searching forms in folder {folder_id} and subfolders")
        forms = find_forms_in_folder(folder_id, from_date, to_date)
        all_forms.extend(forms)
    
    # Deduplicate by URL
    unique_forms = {f['url']: f for f in all_forms}.values()
    return list(unique_forms)

def load_predefined_folders():
    """Load predefined folders from JSON."""
    try:
        with open("predefined_folders.json", "r") as f:
            return json.load(f).get("folders", [])
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        log("ERROR", "Invalid predefined_folders.json")
        return []

def save_predefined_folders(folders):
    """Save predefined folders to JSON."""
    data = {"folders": folders}
    with open("predefined_folders.json", "w") as f:
        json.dump(data, f, indent=2)
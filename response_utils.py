# response_utils.py - Submission selection + response fetching utilities.
#
# SINGLE SOURCE OF TRUTH FOR MODE SELECTION
# -----------------------------------------
# Every grading path (global dispatcher, global prefetch, hybrid) must derive
# its submission set through ``select_responses_for_mode`` so that:
#   RECENT ONLY  -> exactly the submissions newer than the form's last graded
#                   timestamp (falling back to the latest submission batch when
#                   the form has never been graded)
#   WHOLE FORM   -> every submission of the form
# The selected collection is what feeds task building / AI calls; totals and
# progress therefore always describe the same set that gets graded.
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from logger import log

# Track grading timestamps to know which submissions are "recent"
GRADING_TIMESTAMPS_FILE = ".grading_timestamps.json"


def get_last_grading_time(form_id):
    """Get the last time this form was graded."""
    try:
        if os.path.exists(GRADING_TIMESTAMPS_FILE):
            with open(GRADING_TIMESTAMPS_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
                timestamp_str = data.get(form_id)
                if timestamp_str:
                    dt = datetime.fromisoformat(timestamp_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
    except Exception as e:
        log("DEBUG", f"Could not load grading timestamp for {form_id}: {e}")
    return None


def save_grading_time(form_id, timestamp):
    """Save the time this form was graded."""
    try:
        data = {}
        if os.path.exists(GRADING_TIMESTAMPS_FILE):
            # utf-8-sig tolerates a BOM left behind by external editors/shells.
            with open(GRADING_TIMESTAMPS_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        data[form_id] = timestamp.isoformat()
        with open(GRADING_TIMESTAMPS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log("WARNING", f"Could not save grading timestamp for {form_id}: {e}")


def parse_submission_time(resp) -> Optional[datetime]:
    """Extract a timezone-aware submission datetime from a Forms API response."""
    submission_time_str = (
        resp.get("submitTime")
        or resp.get("lastSubmittedTime")
        or resp.get("createTime")
    )
    if not submission_time_str:
        return None
    try:
        dt = datetime.fromisoformat(str(submission_time_str).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def select_responses_for_mode(
    responses: List[Dict],
    grade_recent_only: bool,
    cutoff: Optional[datetime] = None,
    form_id: str = "",
) -> Tuple[List[Dict], Dict]:
    """Select the exact submission set a grading pipeline may process.

    Returns ``(selected_responses, stats)``. ``stats`` is intended for logs and
    defensive checks so a mode/filter mismatch can never pass silently:

        mode             : WHOLE_FORM | RECENT_ONLY
        policy           : all | since_last_graded | latest_batch_fallback |
                           none_in_window
        total_available  : submissions fetched for the form
        selected         : submissions the grading pipeline will receive
        filtered_out     : available - selected
        cutoff           : ISO boundary used (RECENT_ONLY with an anchor)

    Semantics (documented contract):
      * WHOLE_FORM              -> every submission.
      * RECENT_ONLY + cutoff    -> every submission strictly NEWER than the
                                   cutoff (the period is "since last graded";
                                   multiple newer batches are all included).
      * RECENT_ONLY, no cutoff  -> the form has never been graded, so no real
                                   window exists; fall back to the LATEST
                                   submission batch only (conservative).
      * Submissions without any parseable time are excluded in RECENT_ONLY
        mode and counted in ``filtered_out``.
    """
    total_available = len(responses)

    if not grade_recent_only:
        stats = {
            "mode": "WHOLE_FORM",
            "policy": "all",
            "total_available": total_available,
            "selected": total_available,
            "filtered_out": 0,
            "cutoff": None,
            "window_start": None,
            "window_end": None,
        }
        return list(responses), stats

    timed: List[Tuple[Dict, datetime]] = []
    unparseable = 0
    for resp in responses:
        ts = parse_submission_time(resp)
        if ts is None:
            unparseable += 1
            continue
        timed.append((resp, ts))

    if cutoff is not None:
        selected_pairs = [(r, ts) for r, ts in timed if ts > cutoff]
        policy = "since_last_graded" if selected_pairs else "none_in_window"
    else:
        if timed:
            latest = max(ts for _, ts in timed)
            selected_pairs = [(r, ts) for r, ts in timed if ts == latest]
            policy = "latest_batch_fallback"
        else:
            selected_pairs = []
            policy = "none_in_window"

    selected = [r for r, _ts in selected_pairs]
    window_start = min((ts for _, ts in selected_pairs), default=None)
    window_end = max((ts for _, ts in selected_pairs), default=None)
    stats = {
        "mode": "RECENT_ONLY",
        "policy": policy,
        "total_available": total_available,
        "selected": len(selected),
        "filtered_out": total_available - len(selected),
        "cutoff": cutoff.isoformat() if cutoff else None,
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": window_end.isoformat() if window_end else None,
        "unparseable_times": unparseable,
        "form_id": form_id,
    }
    return selected, stats


def log_submission_selection(form_id: str, stats: Dict) -> None:
    """One defensive log line proving which submissions entered the pipeline."""
    try:
        log(
            "INFO",
            "[SUBMISSION SELECT] form_id={form} mode={mode} policy={policy} "
            "available={avail} selected={sel} filtered_out={out} "
            "cutoff={cut} window=[{ws} .. {we}]".format(
                form=form_id or stats.get("form_id", ""),
                mode=stats.get("mode"),
                policy=stats.get("policy"),
                avail=stats.get("total_available"),
                sel=stats.get("selected"),
                out=stats.get("filtered_out"),
                cut=stats.get("cutoff"),
                ws=(stats.get("window_start") or "-"),
                we=(stats.get("window_end") or "-"),
            ),
        )
    except Exception:
        pass


def _extract_answer_values(resp: Dict, question_id: str) -> List[str]:
    """Answer values of ONE response for ONE question (text + choice)."""
    values: List[str] = []
    ans_dict = resp.get("answers", {})
    if question_id not in ans_dict:
        return values
    q_ans = ans_dict[question_id]
    if "textAnswers" in q_ans:
        for ans in q_ans["textAnswers"].get("answers", []):
            value = ans.get("value")
            if value is not None:
                values.append(str(value))
    if "choiceAnswers" in q_ans:
        for ans in q_ans["choiceAnswers"].get("answers", []):
            value = ans.get("value")
            if value is not None:
                values.append(str(value))
    return values


def get_responses(service, form_id, question_id, grade_recent_only=False):
    """
    Fetch all responses for a given form and extract answers for a specific
    question_id, honouring the mode selection contract above.

    Returns a list of answer values (strings) belonging ONLY to the selected
    submissions.
    """
    log("DEBUG", f"Fetching responses for form {form_id} and question {question_id}")

    cutoff = None
    if grade_recent_only:
        cutoff = get_last_grading_time(form_id)
        if cutoff:
            log("INFO", f"RECENT-ONLY MODE: window = submissions after {cutoff.isoformat()}")
        else:
            log("INFO", "RECENT-ONLY MODE: no previous grading found - using latest submission batch only")

    answers: List[str] = []
    next_page_token = None
    all_responses: List[Dict] = []

    try:
        while True:
            result = service.forms().responses().list(
                formId=form_id,
                pageToken=next_page_token
            ).execute()

            page = result.get("responses", [])
            all_responses.extend(page)
            next_page_token = result.get("nextPageToken")
            if not next_page_token:
                break

        selected, stats = select_responses_for_mode(
            all_responses, grade_recent_only, cutoff=cutoff, form_id=form_id,
        )
        log_submission_selection(form_id, stats)

        for resp in selected:
            answers.extend(_extract_answer_values(resp, question_id))

        log(
            "INFO",
            f"FILTERING SUMMARY: Total submissions: {stats['total_available']} | "
            f"Selected: {stats['selected']} | Filtered out: {stats['filtered_out']} | "
            f"Returned answers: {len(answers)} for QID {question_id}",
        )
        return answers

    except Exception as e:
        log("ERROR", f"Failed to fetch responses for form {form_id}, question {question_id}: {str(e)}")
        return []

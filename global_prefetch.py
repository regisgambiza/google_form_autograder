import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List

from logger import log
from form_utils import get_form_structure
from form_context_builder import apply_question_context, build_form_context, get_effective_expected
from response_utils import get_responses
from auth import get_service


@dataclass
class PrefetchedQuestion:
    question: Dict
    responses: List[str]
    expected: List[str]


@dataclass
class PrefetchedForm:
    idx: int
    form_url: str
    form_id: str
    form_title: str
    questions: List[PrefetchedQuestion]
    skip: bool = False
    reason: str = ""


def _extract_form_id(form_url: str) -> str:
    if "/d/" in form_url:
        return form_url.split("/d/")[1].split("/")[0].split("?")[0]
    if "/d/e/" in form_url:
        return form_url.split("/d/e/")[1].split("/")[0].split("?")[0]
    raise ValueError(f"Invalid form URL: {form_url}")


def _fetch_single_form(idx: int, total: int, form_url: str, grade_recent_only: bool) -> PrefetchedForm:
    service = get_service()
    text_types = {"SHORT_ANSWER"}
    form_id = _extract_form_id(form_url)
    log("INFO", f"[GLOBAL PREFETCH] START {idx}/{total} form_id={form_id}")
    form_structure = get_form_structure(service, form_id)
    if not form_structure:
        return PrefetchedForm(idx, form_url, form_id, f"Form_{form_id}", [], skip=True, reason="No gradable questions")

    try:
        form_data = service.forms().get(formId=form_id).execute()
        form_title = form_data.get("info", {}).get("title", f"Untitled_{form_id}")
    except Exception as e:
        log("WARNING", f"[GLOBAL PREFETCH] title fetch failed for {form_id}: {e}")
        form_data = {"items": []}
        form_title = f"Form_{form_id}"

    # Fetch all responses once per form to avoid quota blowups.
    all_responses = _fetch_all_form_responses_with_backoff(service, form_id)

    expected_by_item_id: Dict[str, List[str]] = {}
    try:
        for item in form_data.get("items", []):
            if "questionItem" not in item:
                continue
            item_id = item.get("itemId")
            grading = item["questionItem"]["question"].get("grading", {})
            answers = grading.get("correctAnswers", {}).get("answers", [])
            expected_by_item_id[item_id] = [a["value"] for a in answers if "value" in a]
    except Exception:
        expected_by_item_id = {}

    form_context = build_form_context(form_id, form_title, form_data, form_structure, expected_by_item_id)
    form_structure = apply_question_context(form_structure, form_context)

    out_questions: List[PrefetchedQuestion] = []
    for q in form_structure:
        responses = _extract_answers_for_question(
            all_responses=all_responses,
            question_id=q["questionId"],
            grade_recent_only=grade_recent_only,
        )
        expected = get_effective_expected(q, expected_by_item_id.get(q["itemId"], []))
        if q["type"] in text_types:
            out_questions.append(PrefetchedQuestion(question=q, responses=responses, expected=expected))

    log("INFO", f"[GLOBAL PREFETCH] DONE {idx}/{total} form_id={form_id} questions={len(out_questions)}")
    return PrefetchedForm(idx, form_url, form_id, form_title, out_questions)


def _fetch_all_form_responses_with_backoff(service, form_id: str, max_retries: int = 6) -> List[Dict]:
    all_responses: List[Dict] = []
    next_page_token = None
    while True:
        attempt = 0
        while True:
            try:
                result = service.forms().responses().list(
                    formId=form_id,
                    pageToken=next_page_token
                ).execute()
                break
            except Exception as ex:
                msg = str(ex)
                if "429" in msg or "RATE_LIMIT_EXCEEDED" in msg or "Quota exceeded" in msg:
                    if attempt >= max_retries:
                        log("ERROR", f"[GLOBAL PREFETCH] responses fetch failed (quota) form_id={form_id}: {ex}")
                        return all_responses
                    sleep_s = min(2 ** attempt, 20)
                    log("WARNING", f"[GLOBAL PREFETCH] quota hit form_id={form_id}, retrying in {sleep_s}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(sleep_s)
                    attempt += 1
                    continue
                log("ERROR", f"[GLOBAL PREFETCH] responses fetch failed form_id={form_id}: {ex}")
                return all_responses
        page = result.get("responses", [])
        all_responses.extend(page)
        next_page_token = result.get("nextPageToken")
        if not next_page_token:
            return all_responses


def _extract_answers_for_question(all_responses: List[Dict], question_id: str, grade_recent_only: bool) -> List[str]:
    answers: List[str] = []
    if not all_responses:
        return answers

    # For recent-only mode here, keep a simple latest-submission-batch policy.
    candidate_responses = all_responses
    if grade_recent_only:
        timestamps = []
        for r in all_responses:
            ts = r.get("submitTime") or r.get("lastSubmittedTime") or r.get("createTime")
            if ts:
                timestamps.append((r, ts))
        if timestamps:
            latest = max(ts for _, ts in timestamps)
            candidate_responses = [r for r, ts in timestamps if ts == latest]

    for resp in candidate_responses:
        ans_dict = resp.get("answers", {})
        if question_id not in ans_dict:
            continue
        q_ans = ans_dict[question_id]
        if "textAnswers" in q_ans:
            for ans in q_ans["textAnswers"].get("answers", []):
                v = ans.get("value")
                if v is not None:
                    answers.append(str(v))
        if "choiceAnswers" in q_ans:
            for ans in q_ans["choiceAnswers"].get("answers", []):
                v = ans.get("value")
                if v is not None:
                    answers.append(str(v))
    return answers


def prefetch_all_forms(form_urls: List[str], grade_recent_only: bool, workers: int = 6) -> List[PrefetchedForm]:
    total = len(form_urls)
    by_index: Dict[int, PrefetchedForm] = {}
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {
            ex.submit(_fetch_single_form, idx, total, url, grade_recent_only): idx
            for idx, url in enumerate(form_urls, 1)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                pf = fut.result()
            except Exception as exx:
                log("ERROR", f"[GLOBAL PREFETCH] failed idx={idx}: {exx}")
                pf = PrefetchedForm(idx, form_urls[idx - 1], "", "unknown", [], skip=True, reason=str(exx))
            with lock:
                by_index[idx] = pf
    return [by_index[i] for i in sorted(by_index.keys())]

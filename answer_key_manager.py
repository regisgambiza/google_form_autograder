import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from answer_key_policy import clean_display, identity_key, prepare_answer_key, safely_equivalent


BACKUP_DIR = Path("backups") / "answer_keys"
REVIEW_QUEUE_PATH = Path("answer_key_review_queue.json")
_REVIEW_LOCK = threading.Lock()


@dataclass
class HealthFinding:
    form_id: str
    item_id: str
    question_id: str
    index: int
    title: str
    points: int
    canonical: str
    current_answers: List[str]
    proposed_answers: List[str]
    review_candidates: List[str]
    additions: List[str]
    removals: List[str]
    issues: List[str]
    confidence: float
    route: str


def _answers(question: Dict) -> List[str]:
    grading = question.get("grading", {})
    return [
        str(answer.get("value", ""))
        for answer in grading.get("correctAnswers", {}).get("answers", [])
        if clean_display(answer.get("value", ""))
    ]


def _sign_contradiction(value: str, canonical: str) -> bool:
    value = re.sub(r"\s+", "", clean_display(value))
    canonical = re.sub(r"\s+", "", clean_display(canonical))
    return bool(value and canonical and value.lstrip("+-") == canonical.lstrip("+-") and value[:1] != canonical[:1])


def analyze_question(
    form_id: str,
    item: Dict,
    index: int,
    canonical_override: Optional[str] = None,
    review_candidates: Optional[Sequence[str]] = None,
    max_variants: int = 5,
    unreasonable_count: int = 12,
) -> Optional[HealthFinding]:
    question = item.get("questionItem", {}).get("question", {})
    if "textQuestion" not in question:
        return None
    current = _answers(question)
    canonical_source = clean_display(canonical_override if canonical_override is not None else (current[0] if current else ""))
    canonical = clean_display(canonical_source.split("|", 1)[0]) if canonical_source else ""
    issues: List[str] = []
    if not current:
        issues.append("missing answer key")
    if not canonical:
        issues.append("missing canonical answer")

    trusted = [canonical_source] if canonical_source else []
    queued = [clean_display(value) for value in (review_candidates or []) if clean_display(value)]
    plan = prepare_answer_key(current, queued, trusted, max_variants)
    if plan.duplicates:
        issues.append(f"{len(plan.duplicates)} duplicate entries")
    if len(current) > unreasonable_count:
        issues.append(f"unreasonable answer count ({len(current)})")
    if any("|" in value for value in current):
        issues.append("pipe-delimited variants stored as one answer")
    sign_errors = [value for value in current if _sign_contradiction(value, canonical)]
    if sign_errors:
        issues.append(f"{len(sign_errors)} possible sign contradiction(s)")
    rejected = [value for value in plan.rejected if clean_display(value)]
    if rejected:
        issues.append(f"{len(rejected)} unverified or contradictory entries")
    if queued:
        issues.append(f"{len(queued)} queued candidate(s) awaiting decision")

    current_clean = [clean_display(value) for value in current]
    proposed = plan.answers if canonical else current_clean
    current_keys = set(current_clean)
    proposed_keys = set(proposed)
    additions = [value for value in proposed if value not in current_keys]
    removals = [value for value in current_clean if value not in proposed_keys]

    if not canonical:
        confidence, route = 0.0, "reject"
    elif queued or rejected or sign_errors:
        confidence, route = 0.60, "review"
    elif plan.changed:
        confidence, route = 0.99, "auto"
    else:
        confidence, route = 1.0, "clean"

    grading = question.get("grading", {})
    return HealthFinding(
        form_id=form_id,
        item_id=str(item.get("itemId", "")),
        question_id=str(question.get("questionId", "")),
        index=index,
        title=str(item.get("title", "")),
        points=int(grading.get("pointValue", 0) or 0),
        canonical=canonical,
        current_answers=current_clean,
        proposed_answers=proposed,
        review_candidates=queued,
        additions=additions,
        removals=removals,
        issues=issues,
        confidence=confidence,
        route=route,
    )


def load_pending_reviews(form_id: str) -> Dict[str, List[str]]:
    if not REVIEW_QUEUE_PATH.exists():
        return {}
    try:
        data = json.loads(REVIEW_QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    result: Dict[str, List[str]] = {}
    for item in data.get("items", []):
        if item.get("form_id") != form_id or item.get("status", "pending") != "pending":
            continue
        result.setdefault(str(item.get("item_id", "")), []).extend(item.get("candidates", []))
    return result


def scan_form_data(
    form_id: str,
    form_data: Dict,
    canonical_overrides: Optional[Dict[str, str]] = None,
    pending_reviews: Optional[Dict[str, List[str]]] = None,
) -> List[HealthFinding]:
    overrides = canonical_overrides or {}
    reviews = pending_reviews if pending_reviews is not None else load_pending_reviews(form_id)
    findings: List[HealthFinding] = []
    for index, item in enumerate(form_data.get("items", [])):
        item_id = str(item.get("itemId", ""))
        finding = analyze_question(form_id, item, index, overrides.get(item_id), reviews.get(item_id, []))
        if finding:
            findings.append(finding)
    return findings


def backup_form_grading(service, form_id: str, reason: str = "manual") -> Path:
    form = service.forms().get(formId=form_id).execute()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target_dir = BACKUP_DIR / form_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{timestamp}.json"
    payload = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "form_id": form_id,
        "title": form.get("info", {}).get("title", ""),
        "items": [],
    }
    for index, item in enumerate(form.get("items", [])):
        question = item.get("questionItem", {}).get("question")
        if not question or "grading" not in question:
            continue
        payload["items"].append(
            {
                "index": index,
                "item_id": item.get("itemId"),
                "question_id": question.get("questionId"),
                "grading": question.get("grading", {}),
            }
        )
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return path


def list_backups(form_id: str) -> List[Path]:
    directory = BACKUP_DIR / form_id
    return sorted(directory.glob("*.json"), reverse=True) if directory.exists() else []


def restore_backup(service, backup_path: os.PathLike, dry_run: bool = False) -> Dict:
    payload = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    requests = []
    for saved in payload.get("items", []):
        requests.append(
            {
                "updateItem": {
                    "item": {
                        "itemId": saved["item_id"],
                        "questionItem": {
                            "question": {
                                "questionId": saved["question_id"],
                                "grading": saved["grading"],
                            }
                        },
                    },
                    "location": {"index": saved["index"]},
                    "updateMask": "questionItem.question.grading",
                }
            }
        )
    if requests and not dry_run:
        service.forms().batchUpdate(formId=payload["form_id"], body={"requests": requests}).execute()
    return {"form_id": payload["form_id"], "request_count": len(requests), "dry_run": dry_run}


def remove_form_duplicates(service, form_id: str, dry_run: bool = False) -> Dict:
    """Remove duplicate answer strings without judging correctness."""
    form = service.forms().get(formId=form_id).execute()
    requests = []
    removed = 0
    changed_questions = 0
    for index, item in enumerate(form.get("items", [])):
        question = item.get("questionItem", {}).get("question", {})
        if "textQuestion" not in question:
            continue
        grading = question.get("grading", {})
        current = _answers(question)
        if not current:
            continue
        unique = []
        seen = set()
        for raw in current:
            value = clean_display(raw)
            key = identity_key(value)
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            unique.append(value)
        if len(unique) == len(current):
            continue
        changed_questions += 1
        updated_grading = json.loads(json.dumps(grading))
        updated_grading["correctAnswers"] = {"answers": [{"value": value} for value in unique]}
        requests.append(
            {
                "updateItem": {
                    "item": {
                        "itemId": item.get("itemId"),
                        "questionItem": {
                            "question": {
                                "questionId": question.get("questionId"),
                                "grading": updated_grading,
                            }
                        },
                    },
                    "location": {"index": index},
                    "updateMask": "questionItem.question.grading",
                }
            }
        )
    backup = None
    if requests and not dry_run:
        backup = backup_form_grading(service, form_id, reason="before one-click deduplication")
        service.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()
    return {
        "form_id": form_id,
        "removed": removed,
        "changed_questions": changed_questions,
        "request_count": len(requests),
        "dry_run": dry_run,
        "backup": str(backup) if backup else None,
    }


def enqueue_review(record: Dict) -> None:
    with _REVIEW_LOCK:
        data = {"version": 1, "items": []}
        if REVIEW_QUEUE_PATH.exists():
            try:
                data = json.loads(REVIEW_QUEUE_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        key = (record.get("form_id"), record.get("item_id"), tuple(record.get("candidates", [])))
        existing = {
            (item.get("form_id"), item.get("item_id"), tuple(item.get("candidates", [])))
            for item in data.get("items", [])
            if item.get("status", "pending") == "pending"
        }
        if key not in existing:
            saved = dict(record)
            saved.setdefault("status", "pending")
            saved.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            data.setdefault("items", []).append(saved)
            REVIEW_QUEUE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")


def resolve_reviews(form_id: str, item_id: str, status: str) -> int:
    if status not in {"approved", "rejected"} or not REVIEW_QUEUE_PATH.exists():
        return 0
    with _REVIEW_LOCK:
        try:
            data = json.loads(REVIEW_QUEUE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        changed = 0
        for item in data.get("items", []):
            if (
                item.get("form_id") == form_id
                and str(item.get("item_id", "")) == str(item_id)
                and item.get("status", "pending") == "pending"
            ):
                item["status"] = status
                item["resolved_at"] = datetime.now(timezone.utc).isoformat()
                changed += 1
        if changed:
            REVIEW_QUEUE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
        return changed


def finding_dicts(findings: Sequence[HealthFinding]) -> List[Dict]:
    return [asdict(finding) for finding in findings]

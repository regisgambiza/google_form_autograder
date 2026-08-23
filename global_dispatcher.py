import queue
import threading
import time
import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from auth import get_service
from ai_judges import (
    configure_model_progress,
    estimate_form_model_calls,
)
from deterministic_checks import run_deterministic_checks
from evaluation_pipeline import EvaluationResult, evaluate_answer, evaluate_answers_model_first
from evaluator_config import (
    effective_ai_worker_count,
    effective_jury_concurrency,
    effective_lane_workers,
    is_dual_lane,
    load_config,
)
from feedback import generate_form_feedback
from form_context_builder import (
    apply_question_context,
    build_form_context,
    get_effective_expected,
    get_question_context,
)
from form_utils import get_form_structure
from logger import gui_event, log, runtime_snapshot, stage_banner, update_runtime_state
from response_utils import save_grading_time
from updater import update_correct_answers
from answer_key_manager import (
    enqueue_review,
    format_learning_profile_for_prompt,
    lookup_similar_teacher_memory,
    lookup_teacher_memory,
    question_learning_profile,
)


def _progress_print(text: str) -> None:
    """Emit a machine-readable progress line without ever raising.

    On Windows a dead GUI-side stdout pipe surfaces as OSError [Errno 22]
    Invalid argument instead of BrokenPipeError; an unguarded print() here
    turned that into batch_worker_error results (whole question batches
    requeued) and metrics-reporter crashes. Protocol lines must tolerate a
    dead console.
    """
    try:
        print(text, flush=True)
    except Exception:
        pass


@dataclass
class Task:
    form_idx: int
    form_id: str
    form_title: str
    question: Dict
    answer_idx: int
    answer: str
    expected: List[str]
    queued_monotonic: float = field(default_factory=time.monotonic)

    @property
    def raw_answer(self) -> str:
        """The exact Google Forms response; never normalize this value."""
        return self.answer


@dataclass
class QuestionBatch:
    form_idx: int
    form_id: str
    form_title: str
    question: Dict
    tasks: List[Task]
    queued_monotonic: float = field(default_factory=time.monotonic)


def remove_exact_duplicate_answers(answers: List[str]) -> List[str]:
    """Preserve fetch order while keeping one copy of each exact answer string."""
    return list(dict.fromkeys(answers))


def missing_answer_key_questions(
    structure: List[Dict],
    expected_by_item_id: Dict[str, List[str]],
    answers_by_qid: Dict[str, List[str]],
) -> List[Dict[str, object]]:
    missing: List[Dict[str, object]] = []
    for q in structure:
        if q.get("type", "SHORT_ANSWER") != "SHORT_ANSWER":
            continue
        qid = str(q.get("questionId") or "")
        if not qid or not answers_by_qid.get(qid):
            continue
        expected = get_effective_expected(q, expected_by_item_id.get(q.get("itemId"), []))
        canonical = str(expected[0]).strip() if expected else ""
        if canonical:
            continue
        missing.append({
            "question_id": qid,
            "question_number": int(q.get("index", 0)) + 1,
            "title": str(q.get("title") or "Untitled Question"),
            "responses": len(answers_by_qid.get(qid, [])),
        })
    return missing


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: int):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last = time.time()
        self.lock = threading.Lock()

    def acquire(self, n: float = 1.0):
        while True:
            with self.lock:
                now = time.time()
                elapsed = now - self.last
                self.last = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                if self.tokens >= n:
                    self.tokens -= n
                    return
            time.sleep(0.05)


def run_global_dispatcher(form_urls: List[str], grade_recent_only: bool, generate_report: bool):
    form_started_ts = time.time()
    cfg = load_config()
    deduplicate_answers = bool(cfg.get("enable_deduplication", True))
    staged_startup = bool(cfg.get("staged_thread_startup", True))
    fetch_workers = max(1, int(cfg.get("global_prefetch_workers", 4)))
    det_workers = max(1, int(cfg.get("deterministic_worker_count", 5)))
    # Dual-lane strategy: dedicated per-provider worker pools pulling from one
    # shared queue; every other strategy keeps the single generic pool.
    lane_specs: List[tuple[str, int]] = [
        (name, count) for name, count in effective_lane_workers(cfg).items() if count > 0
    ]
    total_ai_workers = (
        sum(count for _, count in lane_specs) if lane_specs else effective_ai_worker_count(cfg)
    )
    ai_workers = total_ai_workers
    model_first_batching = (
        bool(cfg.get("model_first_question_batching", False))
        and bool(cfg.get("force_ai_jury_for_all_answers", False))
    )
    max_latency = float(cfg.get("max_latency_per_answer_seconds", 30.0))
    patient_mode = bool(cfg.get("patient_ai_mode", False))
    read_rate_per_min = float(cfg.get("forms_expensive_reads_per_minute", 160))
    stall_timeout_s = float(cfg.get("dispatcher_stall_timeout_seconds", 90))
    google_api_timeout_s = float(cfg.get("google_api_timeout_seconds", 25))
    google_api_retries = max(0, int(cfg.get("google_api_retries", 1)))
    bucket = TokenBucket(rate_per_sec=read_rate_per_min / 60.0, capacity=max(5, int(read_rate_per_min / 4)))

    queue_size = max(200, int(cfg.get("worker_queue_size", 4000)))
    det_q_high_wm = min(
        queue_size - 1,
        int(cfg.get("producer_det_queue_high_watermark", max(100, int(queue_size * 0.85)))),
    )
    det_q_low_wm = max(
        1,
        min(det_q_high_wm - 1, int(cfg.get("producer_det_queue_low_watermark", max(50, int(queue_size * 0.45))))),
    )

    fetch_out: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=max(100, min(1000, queue_size // 2)))
    # In staged startup mode we intentionally allow task_builder to enqueue all
    # work before consumers start, so the whole-queue run never overflows the
    # batch slots mid-build — and requeue injections can always land.
    det_q: "queue.Queue[Optional[Task]]" = queue.Queue(maxsize=0 if staged_startup else queue_size)
    ai_q: "queue.Queue[Optional[Task]]" = queue.Queue(
        maxsize=0 if staged_startup else max(200, int(queue_size * 0.5))
    )
    ai_batch_q: "queue.Queue[Optional[QuestionBatch]]" = queue.Queue(
        maxsize=0 if staged_startup else max(100, int(queue_size * 0.2))
    )
    # SIMPLE shared queue: all lanes (openrouter + llamacpp) compete for the
    # same QuestionBatch stream. a gets q1, b gets q2, whoever finishes first
    # gets q3. No per-lane queues, no partitioning, no floor logic.
    # Kept for reference: old per-lane queues removed for simplicity.
    lane_batch_qs: Dict[str, "queue.Queue"] = {}
    lane_answer_shares: Dict[str, int] = {}
    result_q: "queue.Queue[Optional[tuple[Task, EvaluationResult]]]" = queue.Queue(maxsize=max(200, int(queue_size * 0.75)))
    apply_q: "queue.Queue[Optional[tuple[int, str]]]" = queue.Queue()
    stop = threading.Event()
    failed = threading.Event()

    # Failed-answer requeue: ERROR results get another grading pass (with
    # backoff) instead of being silently dropped. Attempts are tracked per
    # logical task; exhaustion falls back to the legacy drop behavior.
    requeue_enabled = bool(cfg.get("requeue_failed_answers", False))
    try:
        requeue_max_attempts = max(0, int(cfg.get("requeue_max_attempts", 2)))
    except (TypeError, ValueError):
        requeue_max_attempts = 2
    try:
        requeue_base_delay_s = max(1.0, float(cfg.get("requeue_base_delay_seconds", 30)))
    except (TypeError, ValueError):
        requeue_base_delay_s = 30.0
    requeue_attempts: Dict[str, int] = {}
    retry_schedule_q: "queue.Queue[Optional[tuple[float, Task]]]" = queue.Queue()
    # Number of tasks currently held by the requeue scheduler (gate -> inject).
    # det_worker uses this to keep its shutdown sentinel behind every possible
    # future requeue injection; otherwise a retried task lands after the
    # sentinel in ai_q FIFO order and its worker exits before seeing it.
    requeue_state = {"scheduled": 0}

    forms_results: Dict[int, Dict] = {}
    # Independent per-form progress slots: {form_idx: {"total": int, "done": int}}.
    # Populated by task_builder (total) and incremented by result_aggregator
    # (done); no code path reads another form's slot.
    form_progress: Dict[int, Dict[str, int]] = {}
    forms_total = len(form_urls)
    model_plan_answers: Dict[str, List[str]] = {}

    def announce_model_plan():
        nonlocal model_plan_answers
        plan_total = estimate_form_model_calls(
            model_plan_answers,
            cfg=cfg,
            model_first_batching=model_first_batching,
        )
        configure_model_progress(plan_total, scope="global_dispatcher")
    metrics_lock = threading.Lock()
    counters = {"fetch": 0, "det": 0, "ai": 0, "apply": 0}
    progress = {
        "expected_tasks": 0,
        "completed": 0,
        "accepted": 0,
        "review_answers": 0,
        "rejected": 0,
        "last_progress_ts": time.time(),
        "pending_buffer": 0,
        "ai_backlog": 0,
        "det_decisions": 0,
        "ai_decisions": 0,
        "latency_ms_total": 0.0,
    }
    review_question_ids = set()
    queued_for_apply = set()
    queue_progress = {"last_any_work_ts": time.time(), "last_snapshot": (0, 0, 0, 0)}
    ai_progress = {"last_ai_done_ts": time.time(), "last_warning_ts": 0.0}
    task_builder_metrics = {"built": 0, "enqueued": 0, "last_emit": time.time()}
    task_builder_log_path = str(cfg.get("task_builder_log_path", "task_builder_metrics.jsonl"))
    task_builder_log_enabled = bool(cfg.get("task_builder_log_enabled", True))
    stage_lock = threading.Lock()
    stage_state = {"current": "init", "fetch_done": False, "build_done": False}

    def task_id(t: Task) -> str:
        return f"f{t.form_idx}:q{t.question.get('questionId', 'unknown')}:a{t.answer_idx}"

    def safe_text(value: object, limit: int = 500) -> str:
        text = str(value or "")
        text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[redacted-email]", text)
        return text if len(text) <= limit else text[:limit] + "…"

    def elapsed_text(seconds: float) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"

    def form_metrics_line(completed: int, expected: int, accepted: int, review: int, elapsed: int,
                          rejected: int, det_decisions: int, ai_decisions: int, avg_latency_ms: float) -> str:
        return (
            f"FormMetrics: {completed}/{expected} {accepted} {review} {elapsed} {rejected} "
            f"det={det_decisions} ai={ai_decisions} avg_ms={avg_latency_ms:.0f}"
        )

    def announce_stage(stage_no: int, title: str, status: str):
        line = "=" * 90
        color = "green" if status.upper() == "DONE" else "cyan"
        stage_banner(f"Stage {stage_no}: {title}", status, color=color)
        log("INFO", line)
        log("INFO", f"[STAGE {stage_no}] {status}: {title}")
        log("INFO", line)

    def set_stage(name: str):
        with stage_lock:
            stage_state["current"] = name

    def mark_fetch_done():
        with stage_lock:
            stage_state["fetch_done"] = True

    def mark_build_done():
        with stage_lock:
            stage_state["build_done"] = True

    def validate_stage_transition(next_stage: str):
        with stage_lock:
            if next_stage == "build" and not stage_state["fetch_done"]:
                raise RuntimeError("Stage transition denied: build cannot start before fetch completes")
            if next_stage == "workers" and (not stage_state["fetch_done"] or not stage_state["build_done"]):
                raise RuntimeError("Stage transition denied: workers cannot start before fetch+build complete")

    def emit_task_builder_metric(event: str, force: bool = False):
        if not task_builder_log_enabled:
            return
        now = time.time()
        with metrics_lock:
            built = int(task_builder_metrics["built"])
            enq = int(task_builder_metrics["enqueued"])
            last_emit = float(task_builder_metrics["last_emit"])
            pending = int(progress.get("pending_buffer", 0))
            expected = int(progress.get("expected_tasks", 0))
            ai_backlog = int(progress.get("ai_backlog", 0))
        dt = max(0.001, now - last_emit)
        if (not force) and dt < 1.0:
            return
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "built_total": built,
            "enqueued_total": enq,
            "build_rate_per_s": built / dt,
            "enqueue_rate_per_s": enq / dt,
            "pending_buffer": pending,
            "expected_tasks": expected,
            "q_fetch": fetch_out.qsize(),
            "q_det": det_q.qsize(),
            "q_ai": ai_batch_q.qsize() if model_first_batching else ai_q.qsize(),
            "q_ai_actual": ai_backlog,
            "q_result": result_q.qsize(),
            "wm_low": det_q_low_wm,
            "wm_high": det_q_high_wm,
        }
        try:
            with open(task_builder_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=True) + "\n")
        except Exception as ex:
            log("WARNING", f"[DISPATCH] could not write task_builder metrics log: {ex}")
        with metrics_lock:
            task_builder_metrics["built"] = 0
            task_builder_metrics["enqueued"] = 0
            task_builder_metrics["last_emit"] = now

    def _call_with_timeout(fn, timeout_s: float, label: str):
        attempts = google_api_retries + 1
        for attempt in range(1, attempts + 1):
            holder = {}
            err_holder = {}

            def _runner():
                try:
                    holder["result"] = fn()
                except Exception as exx:
                    err_holder["error"] = exx

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            t.join(timeout=timeout_s)
            if t.is_alive():
                log("WARNING", f"[DISPATCH] timeout in {label} (attempt {attempt}/{attempts})")
                if attempt < attempts:
                    time.sleep(min(2.0, 0.3 * attempt))
                    continue
                raise TimeoutError(f"timeout in {label}")
            if "error" in err_holder:
                if attempt < attempts:
                    time.sleep(min(2.0, 0.3 * attempt))
                    continue
                raise err_holder["error"]
            return holder["result"]
        raise RuntimeError(f"unreachable timeout wrapper for {label}")

    def _extract_structure_from_form(form_data: Dict) -> List[Dict]:
        structure: List[Dict] = []
        for form_index, item in enumerate(form_data.get("items", [])):
            if "pageBreakItem" in item:
                continue
            if "questionItem" not in item:
                continue
            question = item["questionItem"].get("question", {})
            q = {
                "itemId": item.get("itemId"),
                "questionId": question.get("questionId"),
                "index": form_index + 1,
                "title": item.get("title", ""),
                "description": item.get("description", ""),
            }
            if "textQuestion" in question:
                q["type"] = "LONG_ANSWER" if question["textQuestion"].get("paragraph", False) else "SHORT_ANSWER"
            elif "choiceQuestion" in question:
                cq_type = question["choiceQuestion"].get("type", "RADIO")
                if cq_type == "CHECKBOX":
                    q["type"] = "CHECKBOX"
                elif cq_type == "DROP_DOWN":
                    q["type"] = "DROPDOWN"
                else:
                    q["type"] = "MULTIPLE_CHOICE"
            elif "scaleQuestion" in question:
                q["type"] = "SCALE"
            else:
                q["type"] = "OTHER"
            if q.get("questionId") and q.get("itemId"):
                structure.append(q)
        return structure

    def fetch_form(i: int, url: str):
        service = get_service()
        form_id = url.split("/d/")[1].split("/")[0] if "/d/" in url else url
        bucket.acquire()
        form = _call_with_timeout(
            lambda: service.forms().get(formId=form_id).execute(),
            google_api_timeout_s,
            f"forms.get form_id={form_id}",
        )
        title = form.get("info", {}).get("title", f"Form_{form_id}")
        structure = _extract_structure_from_form(form)
        if not structure:
            try:
                structure = get_form_structure(service, form_id)
            except Exception as ex:
                log("WARNING", f"[DISPATCH] fallback get_form_structure failed for {form_id}: {ex}")
        responses = []
        page = None
        while True:
            bucket.acquire()
            resp = _call_with_timeout(
                lambda: service.forms().responses().list(formId=form_id, pageToken=page).execute(),
                google_api_timeout_s,
                f"forms.responses.list form_id={form_id}",
            )
            responses.extend(resp.get("responses", []))
            page = resp.get("nextPageToken")
            if not page:
                break
        return {"idx": i, "url": url, "form_id": form_id, "title": title, "structure": structure, "form_data": form, "responses": responses}

    def fetch_stage():
        log("INFO", "[Worker: Producer] START fetch_stage")
        try:
            with ThreadPoolExecutor(max_workers=fetch_workers) as ex:
                futs = [ex.submit(fetch_form, i + 1, u) for i, u in enumerate(form_urls)]
                for f in as_completed(futs):
                    if stop.is_set():
                        return
                    try:
                        item = f.result()
                        fetch_out.put(item, timeout=2)
                        with metrics_lock:
                            counters["fetch"] += 1
                    except Exception as exx:
                        log("ERROR", f"[DISPATCH] fetch failed: {exx}")
            fetch_out.put(None, timeout=2)
            log("INFO", "[Worker: Producer] DONE fetch_stage")
            mark_fetch_done()
        except Exception as ex:
            log("ERROR", f"[DISPATCH] fetch_stage crashed: {ex}")
            failed.set()
            stop.set()

    def task_builder():
        log("INFO", "[Worker: Producer] START task_builder")
        try:
            pending_tasks = deque()
            fetch_done = False

            def refill_det_queue(force: bool = False):
                if model_first_batching:
                    return
                while pending_tasks and not stop.is_set():
                    task = pending_tasks.popleft()
                    # Prefer immediate burst enqueue; stop only when queue is truly full.
                    try:
                        if not force:
                            det_q.put_nowait(task)
                        else:
                            det_q.put(task, timeout=2)
                        with metrics_lock:
                            task_builder_metrics["enqueued"] += 1
                    except queue.Full:
                        pending_tasks.appendleft(task)
                        break

            while not stop.is_set():
                # Keep the deterministic queue full enough so downstream workers don't idle.
                if (not model_first_batching) and pending_tasks and det_q.qsize() <= det_q_low_wm:
                    refill_det_queue(force=True)

                if fetch_done:
                    if (not model_first_batching) and pending_tasks:
                        refill_det_queue(force=True)
                        if pending_tasks:
                            time.sleep(0.02)
                            continue
                    break
                try:
                    item = fetch_out.get(timeout=0.5)
                except queue.Empty:
                    if (not model_first_batching) and pending_tasks:
                        refill_det_queue(force=True)
                    continue
                if item is None:
                    fetch_done = True
                    continue
                i = item["idx"]
                form_id = item["form_id"]
                title = item["title"]
                structure = item["structure"] or []
                form_data = item["form_data"] or {"items": []}
                all_responses = item["responses"] or []
                forms_results[i] = {"meta": item, "question_answers": {}, "question_reviews": {}, "question_rejected": {}, "counts": {}}
                # Per-form progress slot: totals come solely from THIS form's
                # own question loop; no other form can touch it.
                form_total_answers = 0
                form_plan_answers: Dict[str, List[str]] = {}

                # Build per-question answer buckets once to avoid O(questions * responses) scans.
                answers_by_qid: Dict[str, List[str]] = {}
                for r in all_responses:
                    ad = r.get("answers", {})
                    for resp_qid, qa in ad.items():
                        bucket = answers_by_qid.setdefault(resp_qid, [])
                        for a in qa.get("textAnswers", {}).get("answers", []):
                            if a.get("value") is not None:
                                bucket.append(str(a["value"]))
                        for a in qa.get("choiceAnswers", {}).get("answers", []):
                            if a.get("value") is not None:
                                bucket.append(str(a["value"]))

                expected_by_item_id: Dict[str, List[str]] = {}
                try:
                    for it in form_data.get("items", []):
                        item_id = it.get("itemId")
                        if not item_id or "questionItem" not in it:
                            continue
                        grading = it["questionItem"]["question"].get("grading", {})
                        ans = grading.get("correctAnswers", {}).get("answers", [])
                        expected_by_item_id[item_id] = [a["value"] for a in ans if "value" in a]
                except Exception:
                    expected_by_item_id = {}

                if bool(cfg.get("enable_form_context", True)):
                    form_context = build_form_context(form_id, title, form_data, structure, expected_by_item_id)
                    structure = apply_question_context(structure, form_context)
                item["structure"] = structure

                missing_keys = missing_answer_key_questions(structure, expected_by_item_id, answers_by_qid)
                missing_qids = {str(entry.get("question_id") or "") for entry in missing_keys}
                if missing_keys:
                    forms_results[i]["partial"] = True
                    forms_results[i]["partial_reason"] = "Missing teacher answer key"
                    forms_results[i]["missing_keys"] = missing_keys
                    detail = "; ".join(
                        f"Q{entry['question_number']} {entry['title']!r} ({entry['responses']} response(s))"
                        for entry in missing_keys[:8]
                    )
                    if len(missing_keys) > 8:
                        detail += f"; +{len(missing_keys) - 8} more"
                    log(
                        "WARNING",
                        f"[FORM PARTIAL] form_id={form_id} title={title!r} "
                        f"missing_teacher_answer_keys={len(missing_keys)} details={detail}",
                    )
                    gui_event(
                        "form_skipped",
                        form_title=title,
                        form_id=form_id,
                        url=item.get("url", ""),
                        reason="Missing teacher answer key",
                        message=(
                            "Some questions were skipped because they have learner responses but no teacher canonical answer. "
                            "Questions with teacher answers will still be graded."
                        ),
                        missing_questions=missing_keys,
                    )

                for q in structure:
                    qid = q.get("questionId")
                    q_type = q.get("type", "OTHER")
                    if q_type != "SHORT_ANSWER":
                        log(
                            "INFO",
                            f"[QUESTION SKIPPED] form_id={form_id} question_id={qid} "
                            f"reason=non_short_answer_type (type={q_type}) title={q.get('title')!r}",
                        )
                        continue
                    expected = get_effective_expected(q, expected_by_item_id.get(q.get("itemId"), []))
                    q["trusted_expected"] = expected[:1]
                    fetched_answers = answers_by_qid.get(qid, [])
                    if str(qid or "") in missing_qids:
                        forms_results[i]["counts"][qid] = 0
                        log(
                            "WARNING",
                            f"[QUESTION SKIPPED] form_id={form_id} question_id={qid} "
                            f"reason=missing_teacher_answer_key responses={len(fetched_answers)}",
                        )
                        continue
                    if deduplicate_answers:
                        answers = remove_exact_duplicate_answers(fetched_answers)
                        removed_duplicates = len(fetched_answers) - len(answers)
                    else:
                        answers = list(fetched_answers)
                        removed_duplicates = 0
                    if removed_duplicates:
                        log(
                            "INFO",
                            f"[DEDUP] question_id={qid} fetched={len(fetched_answers)} "
                            f"unique={len(answers)} removed={removed_duplicates}",
                        )
                    elif not deduplicate_answers:
                        log(
                            "INFO",
                            f"[DEDUP] disabled; question_id={qid} using {len(answers)} raw form responses",
                        )
                    if str(qid or "") not in missing_qids and answers:
                        form_plan_answers[str(qid)] = list(answers)
                        model_plan_answers[f"{i}:{qid}"] = list(answers)
                    forms_results[i]["counts"][qid] = len(answers)
                    form_total_answers += len(answers)
                    question_tasks: List[Task] = []
                    for ai, ans in enumerate(answers):
                        task = Task(i, form_id, title, q, ai, ans, expected)
                        if model_first_batching:
                            question_tasks.append(task)
                        else:
                            pending_tasks.append(task)
                        with metrics_lock:
                            progress["expected_tasks"] += 1
                            progress["pending_buffer"] = len(pending_tasks)
                            task_builder_metrics["built"] += 1

                    if model_first_batching and question_tasks:
                        _enqueue_question_batches(i, form_id, title, q, question_tasks)
                        with metrics_lock:
                            progress["ai_backlog"] += len(question_tasks)
                            task_builder_metrics["enqueued"] += len(question_tasks)

                    # Burst-fill up to high watermark while we build tasks.
                    refill_det_queue(force=False)
                    # If producer buffer gets very large, force-drain until queue blocks.
                    if (not model_first_batching) and len(pending_tasks) >= det_q_high_wm:
                        refill_det_queue(force=True)
                    with metrics_lock:
                        progress["pending_buffer"] = len(pending_tasks)
                    emit_task_builder_metric(event="form_chunk")

                # Close out THIS form's independent progress slot, announce it,
                # and publish its verified per-form total calls so queue rows show
                # real "0/N" calls state instead of waiting for the first result.
                form_model_calls = estimate_form_model_calls(
                    form_plan_answers,
                    cfg=cfg,
                    model_first_batching=model_first_batching,
                )
                form_total_units = max(0, int(form_model_calls if form_model_calls > 0 else form_total_answers))
                with metrics_lock:
                    form_progress[i] = {"total": form_total_units, "done": 0, "form_id": form_id}
                log(
                    "INFO",
                    f"[FORM TOTALS] form_id={form_id} title={title!r} answers={form_total_answers} calls={form_total_units}",
                )
                _progress_print(
                    f"Processing form ID: {form_id} from URL: {item.get('url', '')}"
                )
                _progress_print(f"FormTotals: {form_id} {form_total_units}")

            # Drain any remaining buffered tasks before shutdown sentinels.
            while (not model_first_batching) and pending_tasks and not stop.is_set():
                refill_det_queue(force=True)
                with metrics_lock:
                    progress["pending_buffer"] = len(pending_tasks)
                if pending_tasks:
                    time.sleep(0.05)
                emit_task_builder_metric(event="drain")

            if not model_first_batching:
                for _ in range(det_workers):
                    det_q.put(None, timeout=2)
            log("INFO", "[Worker: Producer] DONE task_builder")
            emit_task_builder_metric(event="complete", force=True)
            mark_build_done()
        except Exception as ex:
            log("ERROR", f"[DISPATCH] task_builder crashed: {ex}")
            emit_task_builder_metric(event="error", force=True)
            failed.set()
            stop.set()

    def _available_lane(exclude: Optional[str] = None) -> Optional[str]:
        """First lane whose provider currently reports available (circuit up)."""
        from provider_manager import is_provider_available

        for name, _count in lane_specs:
            if name == exclude:
                continue
            try:
                if is_provider_available(name):
                    return name
            except Exception:
                return name
        return None

    def _shortest_lane_queue() -> "queue.Queue":
        """Kept for compatibility; now always the shared queue."""
        return ai_batch_q

    def _partition_question_tasks(question_tasks: List[Task]) -> List[tuple]:
        """Simple: keep whole question together for shared-queue race.
        Returns single slice with lane_name=None so caller puts to shared queue.
        Whoever finishes first (openrouter or llamacpp) pulls next question.
        """
        return [(None, list(question_tasks))]

    def _enqueue_question_batches(form_idx: int, form_id: str, title: str, q: Dict, question_tasks: List[Task]) -> None:
        # One QuestionBatch per question -> shared queue
        batch = QuestionBatch(form_idx, form_id, title, q, list(question_tasks))
        try:
            ai_batch_q.put(batch, timeout=2)
        except Exception:
            ai_batch_q.put(batch, timeout=2)

    def enqueue_ai_task(t: Task):
        # Count logical AI backlog separately from the bounded queue buffer so
        # metrics do not plateau at ai_q.maxsize when the AI worker falls behind.
        with metrics_lock:
            progress["ai_backlog"] += 1
        try:
            ai_q.put(t)
        except Exception:
            with metrics_lock:
                progress["ai_backlog"] = max(0, progress["ai_backlog"] - 1)
            raise

    def get_question_context_with_learning(form_id: str, question: Dict) -> str:
        context = get_question_context(question)
        if not bool(cfg.get("teacher_learning_prompt_enabled", True)):
            return context
        profile = question_learning_profile(
            form_id,
            str(question.get("itemId", "")),
            limit=max(1, int(cfg.get("teacher_learning_prompt_examples", 8) or 8)),
        )
        learning_context = format_learning_profile_for_prompt(profile)
        if not learning_context:
            return context
        return f"{context}\n\n{learning_context}"

    def teacher_memory_result(t: Task, memory: Dict, stage: str) -> EvaluationResult:
        decision = "YES" if str(memory.get("decision", "")).upper() == "YES" else "NO"
        confidence = 1.0 if stage == "teacher_memory" else float(memory.get("similarity", 0.97) or 0.97)
        evidence = {
            "question": get_question_context_with_learning(t.form_id, t.question),
            "expected": t.expected,
            "answer": t.answer,
            "teacher_memory": memory,
            "policy": {"policy_reason": stage},
            "key_eligible": decision == "YES",
        }
        return EvaluationResult(
            answer=t.answer,
            decision=decision,
            final_score=confidence if decision == "YES" else 0.0,
            semantic_score=confidence if decision == "YES" else 0.0,
            concept_score=confidence if decision == "YES" else 0.0,
            factual_score=confidence if decision == "YES" else 0.0,
            misconception_detected=False,
            misconception_description="",
            missing_concepts=[],
            accepted_concepts=[],
            model_agreement=1.0,
            confidence=confidence,
            fast_path_used=True,
            latency_ms=0.0,
            stage_reached=stage,
            evidence=evidence,
        )

    def lookup_task_memory(t: Task) -> tuple[Optional[Dict], str]:
        memory = lookup_teacher_memory(t.form_id, str(t.question.get("itemId", "")), t.answer)
        if memory:
            return memory, "teacher_memory"
        if bool(cfg.get("teacher_memory_similar_accept_enabled", True)):
            memory = lookup_similar_teacher_memory(
                t.form_id,
                str(t.question.get("itemId", "")),
                t.answer,
                min_similarity=float(cfg.get("teacher_memory_similarity_threshold", 0.94)),
                decision="YES",
            )
            if memory:
                return memory, "teacher_memory_similar"
        return None, ""

    def evaluate_answer_bounded(t: Task, provider_hint: Optional[str] = None) -> EvaluationResult:
        hard_timeout_s = max(
            float(cfg.get("max_latency_per_answer_seconds", 45.0)) + 45.0,
            float(cfg.get("answer_hard_timeout_seconds", 120.0)),
        )
        result_holder: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=1)

        def _runner():
            try:
                memory, memory_stage = lookup_task_memory(t)
                if memory:
                    result_holder.put(("ok", teacher_memory_result(t, memory, memory_stage)))
                    return
                result_holder.put((
                    "ok",
                    evaluate_answer(
                        t.answer,
                        t.expected,
                        get_question_context_with_learning(t.form_id, t.question),
                        provider_hint=provider_hint,
                    ),
                ))
            except Exception as ex:
                result_holder.put(("error", ex))

        worker = threading.Thread(target=_runner, name="evaluate-answer", daemon=True)
        worker.start()
        worker.join(timeout=hard_timeout_s)
        if worker.is_alive():
            log(
                "ERROR",
                f"[DISPATCH] answer hard timeout after {hard_timeout_s:.1f}s "
                f"form_id={t.form_id} question_id={t.question.get('questionId')} answer_idx={t.answer_idx}",
            )
            return EvaluationResult(
                answer=t.answer,
                decision="ERROR",
                final_score=0.0,
                semantic_score=0.0,
                concept_score=0.0,
                factual_score=0.0,
                misconception_detected=False,
                misconception_description="answer_hard_timeout",
                missing_concepts=[],
                accepted_concepts=[],
                model_agreement=0.0,
                confidence=0.0,
                fast_path_used=False,
                latency_ms=hard_timeout_s * 1000.0,
                stage_reached="answer_hard_timeout",
            )

        status, payload = result_holder.get()
        if status == "ok":
            return payload
        raise payload

    def evaluate_question_batch_bounded(
        batch: QuestionBatch,
        provider_hint: Optional[str] = None,
    ) -> List[tuple[Task, EvaluationResult]]:
        hard_timeout_s = max(
            (float(cfg.get("answer_hard_timeout_seconds", 120.0)) * max(1, len(batch.tasks))),
            float(cfg.get("question_batch_hard_timeout_seconds", 3600.0)),
        )
        result_holder: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=1)

        def _runner():
            try:
                def on_model_progress(units: int = 1):
                    with metrics_lock:
                        fi_slot = form_progress.get(batch.form_idx)
                        if fi_slot is not None:
                            fi_slot["done"] = min(int(fi_slot["total"]), int(fi_slot["done"]) + max(0, int(units)))
                            row_done = int(fi_slot["done"])
                            row_total = int(fi_slot["total"])
                            if row_total > 0:
                                _progress_print(
                                    f"FormRowProgress: {batch.form_id} {row_done}/{row_total}"
                                )

                remembered: Dict[int, EvaluationResult] = {}
                ai_tasks: List[Task] = []
                for index, task in enumerate(batch.tasks):
                    memory, memory_stage = lookup_task_memory(task)
                    if memory:
                        remembered[index] = teacher_memory_result(task, memory, memory_stage)
                    else:
                        ai_tasks.append(task)

                if remembered:
                    roles_count = max(1, len(_selected_roles(cfg)))
                    on_model_progress(len(remembered) * roles_count)

                ai_results_by_task: Dict[int, EvaluationResult] = {}
                if ai_tasks:
                    try:
                        results = evaluate_answers_model_first(
                            [task.answer for task in ai_tasks],
                            ai_tasks[0].expected,
                            get_question_context_with_learning(batch.form_id, batch.question),
                            provider_hint=provider_hint,
                            progress_callback=on_model_progress,
                        )
                    except TypeError:
                        results = evaluate_answers_model_first(
                            [task.answer for task in ai_tasks],
                            ai_tasks[0].expected,
                            get_question_context_with_learning(batch.form_id, batch.question),
                            provider_hint=provider_hint,
                        )
                    ai_results_by_task = {
                        id(task): result
                        for task, result in zip(ai_tasks, results)
                    }
                ordered: List[tuple[Task, EvaluationResult]] = []
                for index, task in enumerate(batch.tasks):
                    if index in remembered:
                        ordered.append((task, remembered[index]))
                    else:
                        ordered.append((task, ai_results_by_task[id(task)]))
                result_holder.put(("ok", ordered))
            except Exception as ex:
                result_holder.put(("error", ex))

        worker = threading.Thread(target=_runner, name="evaluate-question-batch", daemon=True)
        worker.start()
        worker.join(timeout=hard_timeout_s)
        if worker.is_alive():
            log(
                "ERROR",
                f"[DISPATCH] question batch hard timeout after {hard_timeout_s:.1f}s "
                f"form_id={batch.form_id} question_id={batch.question.get('questionId')} answers={len(batch.tasks)}",
            )
            failed_results = []
            for task in batch.tasks:
                failed_results.append((task, EvaluationResult(
                    answer=task.answer,
                    decision="ERROR",
                    final_score=0.0,
                    semantic_score=0.0,
                    concept_score=0.0,
                    factual_score=0.0,
                    misconception_detected=False,
                    misconception_description="question_batch_hard_timeout",
                    missing_concepts=[],
                    accepted_concepts=[],
                    model_agreement=0.0,
                    confidence=0.0,
                    fast_path_used=False,
                    latency_ms=hard_timeout_s * 1000.0,
                    stage_reached="question_batch_hard_timeout",
                )))
            return failed_results

        status, payload = result_holder.get()
        if status == "ok":
            return payload
        raise payload

    def det_worker():
        log("INFO", "[Worker: Deterministic] START det_worker")
        force_ai_for_all = bool(cfg.get("force_ai_jury_for_all_answers", False))
        while not stop.is_set():
            try:
                t = det_q.get(timeout=1)
            except queue.Empty:
                continue
            if t is None:
                # Hold the shutdown sentinel until the AI pipeline has fully
                # drained AND no requeue retries are scheduled; a retried task
                # must never land behind this None in ai_q FIFO order.
                while not stop.is_set():
                    with metrics_lock:
                        inflight = int(progress.get("ai_backlog", 0))
                        scheduled = int(requeue_state.get("scheduled", 0))
                    if inflight <= 0 and scheduled <= 0:
                        break
                    time.sleep(0.1)
                ai_q.put(None)
                log("INFO", "[Worker: Deterministic] DONE det_worker")
                return
            try:
                if force_ai_for_all:
                    enqueue_ai_task(t)
                    continue
                det = run_deterministic_checks(
                    t.answer, t.expected, float(cfg.get("numeric_tolerance", 0.01))
                )
                if det.accepted and det.confidence >= 0.95:
                    r = EvaluationResult(
                        answer=t.answer, decision="YES", final_score=det.confidence, semantic_score=det.confidence,
                        concept_score=det.confidence, factual_score=det.confidence, misconception_detected=False,
                        misconception_description="", missing_concepts=[], accepted_concepts=[], model_agreement=1.0,
                        confidence=det.confidence, fast_path_used=True, latency_ms=0.0, stage_reached="deterministic",
                        evidence={"proof": getattr(det, "method", "deterministic"), "key_eligible": True},
                    )
                    result_q.put((t, r), timeout=2)
                    with metrics_lock:
                        counters["det"] += 1
                else:
                    enqueue_ai_task(t)
            except Exception as exx:
                log("WARNING", f"[DISPATCH] deterministic worker failed: {exx}")
                try:
                    enqueue_ai_task(t)
                except Exception:
                    pass

    def ai_worker(worker_id: str, lane_provider: Optional[str] = None):
        log("INFO", f"[Worker: AI] START ai_worker id={worker_id} lane={lane_provider or 'generic'}")
        log("INFO", f"[APP WORKER] id={worker_id} type=ai status=idle current=- answers=0 latency_ms=0 queue_wait_ms=0")
        if model_first_batching:
            my_q = ai_batch_q
            while not stop.is_set():
                # If this lane's provider is down, don't steal work from healthy lanes,
                # but still need to exit when shared work is done (otherwise sentinel
                # never consumed and dispatcher hangs).
                if lane_provider:
                    try:
                        from provider_manager import is_provider_available

                        if not is_provider_available(lane_provider):
                            time.sleep(0.5)
                            with metrics_lock:
                                _exp = int(progress.get("expected_tasks", 0))
                                _comp = int(progress.get("completed", 0))
                                _back = int(progress.get("ai_backlog", 0))
                            if _exp > 0 and _comp >= _exp and _back <= 0:
                                log("INFO", f"[Worker: AI] DONE ai_worker id={worker_id} (provider unavailable, work complete)")
                                return
                            # Also drain sentinel if it arrived while sleeping
                            try:
                                _peek = my_q.get(timeout=0.1)
                                if _peek is None:
                                    log("INFO", f"[Worker: AI] DONE ai_worker id={worker_id}")
                                    return
                                # Put back real work for healthy workers
                                my_q.put(_peek, timeout=1)
                            except queue.Empty:
                                pass
                            continue
                    except Exception:
                        pass
                try:
                    batch = my_q.get(timeout=1)
                except queue.Empty:
                    with metrics_lock:
                        expected = int(progress.get("expected_tasks", 0))
                        completed = int(progress.get("completed", 0))
                        backlog = int(progress.get("ai_backlog", 0))
                    if expected > 0 and completed >= expected and backlog <= 0:
                        log("INFO", f"[Worker: AI] DONE ai_worker id={worker_id} (all batched work complete)")
                        log("INFO", f"[APP WORKER] id={worker_id} type=ai status=done current=- answers=0 latency_ms=0 queue_wait_ms=0")
                        return
                    continue
                if batch is None:
                    log("INFO", f"[Worker: AI] DONE ai_worker id={worker_id}")
                    log("INFO", f"[APP WORKER] id={worker_id} type=ai status=done current=- answers=0 latency_ms=0 queue_wait_ms=0")
                    return

                hint_for_run = lane_provider

                qid = batch.question.get("questionId")
                queue_wait_s = max(0.0, time.monotonic() - batch.queued_monotonic)
                queue_wait_ms = int(queue_wait_s * 1000)
                update_runtime_state(
                    active_task=f"f{batch.form_idx}:q{qid}:batch",
                    active_form=batch.form_id,
                    active_question=qid,
                    active_model="model-first-jury",
                    active_since=time.time(),
                )
                log(
                    "INFO",
                    f"[APP WORKER] id={worker_id} type=ai status=running "
                    f"current=f{batch.form_idx}:q{qid} answers={len(batch.tasks)} "
                    f"latency_ms=0 queue_wait_ms={queue_wait_ms}",
                )
                log(
                    "INFO",
                    f"[BATCH START] form_id={batch.form_id} question_id={qid} "
                    f"answers={len(batch.tasks)} queue_wait_s={queue_wait_s:.2f}",
                )
                started = time.perf_counter()
                try:
                    batch_results = evaluate_question_batch_bounded(batch, provider_hint=hint_for_run)
                except Exception as exx:
                    log("ERROR", f"[DISPATCH] batch ai worker error: {exx}")
                    batch_results = []
                    for task in batch.tasks:
                        batch_results.append((task, EvaluationResult(
                            answer=task.answer, decision="ERROR", final_score=0.0, semantic_score=0.0,
                            concept_score=0.0, factual_score=0.0, misconception_detected=False,
                            misconception_description="batch_worker_error", missing_concepts=[],
                            accepted_concepts=[], model_agreement=0.0, confidence=0.0,
                            fast_path_used=False, latency_ms=0.0, stage_reached="batch_worker_error",
                        )))

                for task, result in batch_results:
                    try:
                        result_q.put((task, result), timeout=2)
                    except Exception:
                        pass
                with metrics_lock:
                    counters["ai"] += len(batch_results)
                    ai_progress["last_ai_done_ts"] = time.time()
                    progress["ai_backlog"] = max(0, progress["ai_backlog"] - len(batch.tasks))
                elapsed_s = time.perf_counter() - started
                latency_ms = int(elapsed_s * 1000)
                log(
                    "INFO",
                    f"[BATCH END] form_id={batch.form_id} question_id={qid} "
                    f"answers={len(batch.tasks)} duration_s={elapsed_s:.2f}",
                )
                log(
                    "INFO",
                    f"[APP WORKER] id={worker_id} type=ai status=idle current=f{batch.form_idx}:q{qid} "
                    f"answers={len(batch.tasks)} latency_ms={latency_ms} queue_wait_ms={queue_wait_ms}",
                )
                update_runtime_state(active_task="", active_model="idle", active_since=0.0)
            return

        while not stop.is_set():
            try:
                t = ai_q.get(timeout=1)
            except queue.Empty:
                with metrics_lock:
                    expected = int(progress.get("expected_tasks", 0))
                    completed = int(progress.get("completed", 0))
                    backlog = int(progress.get("ai_backlog", 0))
                if expected > 0 and completed >= expected and backlog <= 0:
                    log("INFO", f"[Worker: AI] DONE ai_worker id={worker_id} (all work complete)")
                    log("INFO", f"[APP WORKER] id={worker_id} type=ai status=done current=- answers=0 latency_ms=0 queue_wait_ms=0")
                    return
                continue
            if t is None:
                log("INFO", f"[Worker: AI] DONE ai_worker id={worker_id}")
                log("INFO", f"[APP WORKER] id={worker_id} type=ai status=done current=- answers=0 latency_ms=0 queue_wait_ms=0")
                return

            started = time.perf_counter()
            tid = task_id(t)
            queue_wait_s = max(0.0, time.monotonic() - t.queued_monotonic)
            queue_wait_ms = int(queue_wait_s * 1000)
            update_runtime_state(
                active_task=tid, active_form=t.form_id,
                active_question=t.question.get("questionId", ""),
                active_model="jury", active_since=time.time(),
            )
            log(
                "INFO",
                f"[APP WORKER] id={worker_id} type=ai status=running current={tid} "
                f"answers=1 latency_ms=0 queue_wait_ms={queue_wait_ms}",
            )
            answer_for_log = safe_text(t.answer) if bool(cfg.get("external_log_student_answers", True)) else "[hidden]"
            log(
                "INFO",
                f"[TASK START] task={tid} form_id={t.form_id} question_id={t.question.get('questionId')} "
                f"answer_index={t.answer_idx} queue_wait_s={queue_wait_s:.2f} "
                f"answer={answer_for_log!r} expected={safe_text((t.expected or [''])[0])!r}",
            )
            try:
                r = evaluate_answer_bounded(t, provider_hint=lane_provider)
            except Exception as exx:
                log("ERROR", f"[DISPATCH] ai worker error: {exx}")
                r = EvaluationResult(
                    answer=t.answer, decision="ERROR", final_score=0.0, semantic_score=0.0, concept_score=0.0,
                    factual_score=0.0, misconception_detected=False, misconception_description="error",
                    missing_concepts=[], accepted_concepts=[], model_agreement=0.0, confidence=0.0,
                    fast_path_used=False, latency_ms=0.0, stage_reached="worker_error"
                )
            elapsed_s = time.perf_counter() - started
            hard_budget_s = max_latency + 30
            if (not patient_mode) and elapsed_s > hard_budget_s:
                log("WARNING", f"[DISPATCH] evaluate_answer exceeded budget: {elapsed_s:.1f}s > {hard_budget_s:.1f}s")
                r = EvaluationResult(
                    answer=t.answer, decision="ERROR", final_score=0.0, semantic_score=0.0, concept_score=0.0,
                    factual_score=0.0, misconception_detected=False, misconception_description="timeout",
                    missing_concepts=[], accepted_concepts=[], model_agreement=0.0, confidence=0.0,
                    fast_path_used=False, latency_ms=hard_budget_s * 1000.0, stage_reached="timeout"
                )

            try:
                result_q.put((t, r), timeout=2)
                with metrics_lock:
                    counters["ai"] += 1
                    ai_progress["last_ai_done_ts"] = time.time()
                    progress["ai_backlog"] = max(0, progress["ai_backlog"] - 1)
            except Exception:
                pass
            policy_reason = str((r.evidence or {}).get("policy", {}).get("policy_reason", ""))
            log(
                "INFO",
                f"[TASK END] task={tid} decision={r.decision} confidence={r.confidence:.3f} "
                f"stage={r.stage_reached} policy={policy_reason or 'n/a'} duration_s={elapsed_s:.2f}",
            )
            log(
                "INFO",
                f"[APP WORKER] id={worker_id} type=ai status=idle current={tid} "
                f"answers=1 latency_ms={int(elapsed_s * 1000)} queue_wait_ms={queue_wait_ms}",
            )
            update_runtime_state(active_task="", active_model="idle", active_since=0.0)

    def _finalize_requeue_error(t: Task, stage: str) -> None:
        """Close accounting for a task that can no longer be retried."""
        result = EvaluationResult(
            answer=t.answer,
            decision="ERROR",
            final_score=0.0,
            semantic_score=0.0,
            concept_score=0.0,
            factual_score=0.0,
            misconception_detected=False,
            misconception_description="requeue_abandoned",
            missing_concepts=[],
            accepted_concepts=[],
            model_agreement=0.0,
            confidence=0.0,
            fast_path_used=False,
            latency_ms=0.0,
            stage_reached=stage,
        )
        try:
            result_q.put((t, result), timeout=2)
        except Exception:
            pass

    def _inject_requeued_task(t: Task) -> None:
        """Put a requeued task back into the active grading pipeline."""
        try:
            t.queued_monotonic = time.monotonic()
            if model_first_batching:
                ai_batch_q.put(
                    QuestionBatch(t.form_idx, t.form_id, t.form_title, t.question, [t]),
                    timeout=2,
                )
                with metrics_lock:
                    progress["ai_backlog"] += 1
            else:
                enqueue_ai_task(t)
            log(
                "INFO",
                f"[REQUEUE] re-injected task={task_id(t)} form_id={t.form_id} "
                f"question_id={t.question.get('questionId')}",
            )
        except Exception as ex:
            log("ERROR", f"[REQUEUE] injection failed task={task_id(t)}: {ex}; finalizing as ERROR")
            # The ERROR result below will pass through the aggregator's requeue
            # gate; exhaust this task's attempts first so a finalized task can
            # never be scheduled for another retry.
            requeue_attempts[task_id(t)] = requeue_max_attempts
            _finalize_requeue_error(t, "requeue_injection_failed")
        finally:
            with metrics_lock:
                requeue_state["scheduled"] = max(0, requeue_state["scheduled"] - 1)

    def retry_scheduler():
        log("INFO", "[Worker: Requeue] START retry_scheduler")
        pending = []  # entries of [ready_monotonic, Task]
        while True:
            now = time.monotonic()
            due = [entry for entry in pending if entry[0] <= now]
            for _ready, task in due:
                _inject_requeued_task(task)
            if due:
                pending = [entry for entry in pending if entry[0] > now]
            try:
                item = retry_schedule_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                # Drain-on-stop: inject leftovers immediately at shutdown.
                for _ready, task in pending:
                    _inject_requeued_task(task)
                pending = []
                break
            pending.append(list(item))
        log("INFO", "[Worker: Requeue] DONE retry_scheduler")

    def result_aggregator():
        log("INFO", "[Worker: Aggregator] START result_aggregator")
        while not stop.is_set():
            with metrics_lock:
                expected = progress["expected_tasks"]
                completed = progress["completed"]
            if completed >= expected and expected > 0 and result_q.empty():
                log("INFO", "[Worker: Aggregator] DONE result_aggregator")
                return
            try:
                item = result_q.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                log("INFO", "[Worker: Aggregator] DONE result_aggregator")
                return
            t, r = item
            # Requeue gate: hold ERROR results out of accounting while retry
            # attempts remain. Skipping the placeholder append and completed
            # increment keeps per-question apply and run termination exact.
            if r.decision == "ERROR":
                attempt_key = task_id(t)
                attempts_used = requeue_attempts.get(attempt_key, 0)
                if requeue_enabled and attempts_used < requeue_max_attempts and not failed.is_set():
                    with metrics_lock:
                        requeue_state["scheduled"] += 1
                    requeue_attempts[attempt_key] = attempts_used + 1
                    delay_s = requeue_base_delay_s * (2 ** attempts_used)
                    log(
                        "WARNING",
                        f"[REQUEUE] grading failed for task={attempt_key} "
                        f"(stage={r.stage_reached}); scheduling retry "
                        f"{attempts_used + 1}/{requeue_max_attempts} in {delay_s:.0f}s",
                    )
                    gui_event(
                        "answer_requeued",
                        question_number=int(t.question.get("index", 0)) + 1,
                        question=safe_text(t.question.get("title", "Untitled Question"), 1000),
                        answer=safe_text(t.answer) if bool(cfg.get("gui_show_student_answers", True)) else "[hidden]",
                        attempt=attempts_used + 1,
                        max_attempts=requeue_max_attempts,
                        delay_seconds=int(delay_s),
                    )
                    try:
                        retry_schedule_q.put((time.monotonic() + delay_s, t), timeout=2)
                        continue
                    except Exception:
                        with metrics_lock:
                            requeue_state["scheduled"] = max(0, requeue_state["scheduled"] - 1)
                        log("ERROR", f"[REQUEUE] schedule queue full; dropping task={attempt_key}")
            fi = t.form_idx
            qid = t.question["questionId"]
            forms_results.setdefault(fi, {"meta": {}, "question_answers": {}, "counts": {}})
            key_eligible = bool((r.evidence or {}).get("key_eligible", False))
            accepted_variant = (
                r.decision == "YES"
                and key_eligible
                and all(r.raw_answer != str(value) for value in (t.expected or []))
            )
            # Write YES and REVIEW answers to Google Form; NO answers are not written
            forms_results[fi]["question_answers"].setdefault(qid, []).append(
                r.raw_answer if r.decision in ("YES", "REVIEW") else None
            )
            if r.decision == "REVIEW":
                forms_results[fi].setdefault("question_reviews", {}).setdefault(qid, []).append({
                    "answer": r.raw_answer, "confidence": r.confidence, "stage": r.stage_reached,
                    "evidence": r.evidence,
                })
            if r.decision == "NO":
                forms_results[fi].setdefault("question_rejected", {}).setdefault(qid, []).append({
                    "answer": r.raw_answer, "confidence": r.confidence, "stage": r.stage_reached,
                    "evidence": r.evidence,
                })
            question_done = len(forms_results[fi]["question_answers"].get(qid, []))
            question_total = int(forms_results[fi].get("counts", {}).get(qid, 0))
            apply_key = (fi, qid)
            if question_total > 0 and question_done >= question_total and apply_key not in queued_for_apply:
                queued_for_apply.add(apply_key)
                apply_q.put(apply_key)
            with metrics_lock:
                progress["completed"] += 1
                if bool(getattr(r, "fast_path_used", False)) or str(getattr(r, "stage_reached", "")) == "deterministic":
                    progress["det_decisions"] += 1
                else:
                    progress["ai_decisions"] += 1
                progress["latency_ms_total"] += max(0.0, float(getattr(r, "latency_ms", 0.0) or 0.0))
                if r.decision == "YES":
                    progress["accepted"] += 1
                elif r.decision == "REVIEW":
                    progress["review_answers"] += 1
                elif r.decision == "NO":
                    progress["rejected"] += 1
                completed_now = int(progress["completed"])
                expected_now = int(progress["expected_tasks"])
                accepted_now = int(progress["accepted"])
                review_answers_now = int(progress["review_answers"])
                rejected_now = int(progress["rejected"])
                # The GUI badge counts only persisted, clickable REVIEW
                # questions. Rejections and accepted audit variants do not
                # belong in "Needs review".
                review_now = len(review_question_ids)
                det_now = int(progress["det_decisions"])
                ai_now = int(progress["ai_decisions"])
                avg_latency_now = (
                    float(progress["latency_ms_total"]) / max(1, completed_now)
                )
                progress["last_progress_ts"] = time.time()
            # Machine-readable real-time progress consumed by GraderThread.
            # In staged mode task construction is complete before this worker starts,
            # so the denominator is stable and the percentage cannot move backwards.
            _progress_print(f"FormProgress: {completed_now}/{expected_now}")
            # Per-form row progress for the GUI queue: scoped to this form only.
            fi_slot = form_progress.get(fi)
            if fi_slot is not None:
                with metrics_lock:
                    if not model_first_batching:
                        fi_slot["done"] += 1
                    row_total = int(fi_slot["total"])
                    row_done = int(fi_slot["done"])
                if row_total > 0 and not model_first_batching:
                    _progress_print(
                        f"FormRowProgress: {t.form_id} {row_done}/{row_total}"
                    )
            _progress_print(
                form_metrics_line(
                    completed_now,
                    expected_now,
                    accepted_now,
                    review_now,
                    int(time.time() - form_started_ts),
                    rejected_now,
                    det_now,
                    ai_now,
                    avg_latency_now,
                )
            )
            evidence = r.evidence or {}
            policy = evidence.get("policy", {}) if isinstance(evidence, dict) else {}
            judge_map = policy.get("judge_decisions", {}) if isinstance(policy, dict) else {}
            judges = [
                {"role": role, **details}
                for role, details in judge_map.items()
                if isinstance(details, dict)
            ]
            domain = evidence.get("domain_validation", {}) if isinstance(evidence, dict) else {}
            formatting = {}
            if isinstance(domain, dict) and domain:
                domain_evidence = domain.get("evidence", {}) if isinstance(domain.get("evidence"), dict) else {}
                format_data = domain_evidence.get("formatting", {}) if isinstance(domain_evidence, dict) else {}
                format_evidence = format_data.get("evidence", {}) if isinstance(format_data, dict) else {}
                details = []
                if format_evidence:
                    if format_evidence.get("candidate_value") is not None:
                        details.append(
                            f"Value {format_evidence.get('candidate_value')} compared with {format_evidence.get('expected_value')}"
                        )
                    if format_evidence.get("expected_unit"):
                        details.append(
                            f"Unit {format_evidence.get('candidate_unit') or '(implied)'} compared with {format_evidence.get('expected_unit')}"
                        )
                    if format_evidence.get("required_decimal_places") is not None:
                        details.append(
                            f"Required precision: {format_evidence.get('required_decimal_places')} decimal place(s)"
                        )
                formatting = {
                    "proven": domain.get("status") == "PROVEN",
                    "reason": domain.get("reason", ""),
                    "details": details,
                }
            if r.decision == "YES":
                action = "Answer accepted; queued for the answer key audit." if accepted_variant else "Answer accepted; it matches an existing accepted form."
            elif r.decision == "REVIEW":
                action = "Answer added to Google Forms; pending teacher review."
            elif r.decision == "ERROR":
                attempts_used = requeue_attempts.get(task_id(t), 0)
                if requeue_enabled and requeue_max_attempts > 0:
                    action = (
                        f"Grading failed after {attempts_used + 1} attempt(s); "
                        "answer was not added to Google Forms or teacher review."
                    )
                else:
                    action = "Grading failed after retries; answer was not added to Google Forms or teacher review."
            else:
                action = "Rejected and not added to Google Forms."
            shown_answer = safe_text(t.answer) if bool(cfg.get("gui_show_student_answers", True)) else "[hidden]"
            gui_event(
                "answer_result",
                current=completed_now, total=expected_now,
                question_number=int(t.question.get("index", 0)) + 1,
                question=safe_text(t.question.get("title", "Untitled Question"), 1000),
                expected=safe_text(" | ".join(t.expected or [])),
                answer=shown_answer,
                formatting=formatting,
                judges=judges,
                decision=r.decision,
                confidence=r.confidence,
                policy_reason=policy.get("policy_reason", ""),
                action=action,
                accepted=accepted_now,
                review=review_answers_now,
                rejected=rejected_now,
                elapsed=elapsed_text(time.time() - form_started_ts),
            )

    def incremental_apply_worker():
        """Serialize Google Forms writes as each question finishes evaluation."""
        log("INFO", "[Worker: Apply] START incremental_apply_worker")
        service = get_service()
        while True:
            item = apply_q.get()
            if item is None:
                apply_q.task_done()
                log("INFO", "[Worker: Apply] DONE incremental_apply_worker")
                return
            fi, qid = item
            try:
                data = forms_results[fi]
                meta = data["meta"]
                form_id = meta.get("form_id", "")
                question = next(q for q in meta.get("structure", []) if q.get("questionId") == qid)
                reviews = data.get("question_reviews", {}).get(qid, [])
                rejected = data.get("question_rejected", {}).get(qid, [])
                approval_answers = list(dict.fromkeys(x["answer"] for x in reviews))
                rejected_answers = list(dict.fromkeys(x["answer"] for x in rejected))
                trusted_expected = question.get("trusted_expected", get_effective_expected(question, [])[:1])
                expected_values = {str(value) for value in trusted_expected}
                accepted = list(dict.fromkeys(
                    answer for answer in data["question_answers"].get(qid, [])
                    if answer is not None and answer != "" and answer not in expected_values
                ))
                # REVIEW answers are now written to Google Form and will be reviewed later.
                # Only confident YES answers may be auto-applied without teacher approval.
                categorized_candidates = list(dict.fromkeys(accepted))
                if accepted or approval_answers or rejected_answers:
                    enqueue_review({
                        "form_id": form_id,
                        "item_id": question["itemId"],
                        "question_id": qid,
                        "canonical": trusted_expected[0] if trusted_expected else "",
                        "candidates": list(dict.fromkeys(accepted + approval_answers + rejected_answers)),
                        "accepted": list(dict.fromkeys(accepted)),
                        "needs_approval": approval_answers,
                        "rejected": rejected_answers,
                        "confidence": max((float(x["confidence"]) for x in reviews), default=0.0),
                        "route": "grading_review",
                        "evidence": reviews,
                    })
                if approval_answers:
                    with metrics_lock:
                        review_question_ids.add((fi, qid))
                        completed_metric = int(progress["completed"])
                        expected_metric = int(progress["expected_tasks"])
                        accepted_metric = int(progress["accepted"])
                        rejected_metric = int(progress["rejected"])
                        det_metric = int(progress["det_decisions"])
                        ai_metric = int(progress["ai_decisions"])
                        avg_latency_metric = float(progress["latency_ms_total"]) / max(1, completed_metric)
                        review_metric = len(review_question_ids)
                    # Refresh the GUI only after the review queue is durable,
                    # so clicking the badge cannot lead to an empty screen.
                    _progress_print(
                        form_metrics_line(
                            completed_metric,
                            expected_metric,
                            accepted_metric,
                            review_metric,
                            int(time.time() - form_started_ts),
                            rejected_metric,
                            det_metric,
                            ai_metric,
                            avg_latency_metric,
                        )
                    )
                if categorized_candidates and question.get("type") == "SHORT_ANSWER":
                    update_correct_answers(
                        service, form_id, question["itemId"], categorized_candidates, question["index"], trusted_expected,
                        enqueue_added_review=False,
                    )
                with metrics_lock:
                    counters["apply"] += 1
                _progress_print(f"QuestionAvailableForReview: {form_id} {qid}")
                log("INFO", f"[APPLY] Question ready for review form_id={form_id} question_id={qid}")
            except Exception as ex:
                log("ERROR", f"[APPLY] Incremental question update failed fi={fi} qid={qid}: {ex}")
            finally:
                apply_q.task_done()

    def metrics_reporter():
        last = time.time()
        ai_stall_timeout_s = float(cfg.get("ai_stall_timeout_seconds", 90))
        heartbeat_interval_s = max(10.0, float(cfg.get("external_heartbeat_interval_seconds", 20)))
        first_report = True
        log("INFO", "[Worker: Metrics] START metrics_reporter")
        while not stop.is_set():
            try:
                if first_report:
                    first_report = False
                elif stop.wait(heartbeat_interval_s):
                    break
                now = time.time()
                dt = max(0.001, now - last)
                last = now
                with metrics_lock:
                    f = counters["fetch"]; d = counters["det"]; a = counters["ai"]; ap = counters["apply"]
                    counters["fetch"] = counters["det"] = counters["ai"] = counters["apply"] = 0
                    exp = progress["expected_tasks"]; comp = progress["completed"]; lp = progress["last_progress_ts"]; pb = progress["pending_buffer"]; ai_backlog = progress["ai_backlog"]
                    accepted_total = int(progress["accepted"]); review_total = len(review_question_ids)
                    rejected_total = int(progress["rejected"])
                    det_total = int(progress["det_decisions"])
                    ai_total = int(progress["ai_decisions"])
                    avg_latency_total = float(progress["latency_ms_total"]) / max(1, int(comp))
                _progress_print(
                    form_metrics_line(
                        int(comp),
                        int(exp),
                        accepted_total,
                        review_total,
                        int(time.time() - form_started_ts),
                        rejected_total,
                        det_total,
                        ai_total,
                        avg_latency_total,
                    )
                )
                _q_ai = ai_batch_q.qsize() if model_first_batching else ai_q.qsize()
                log(
                    "INFO",
                    f"[DISPATCH METRICS] fetch/s={f/dt:.2f} det/s={d/dt:.2f} ai/s={a/dt:.2f} apply/s={ap/dt:.2f} "
                    f"q_fetch={fetch_out.qsize()} q_det={det_q.qsize()} "
                    f"q_ai={_q_ai} "
                    f"q_ai_actual={ai_backlog} q_result={result_q.qsize()} "
                    f"pending={pb} wm={det_q_low_wm}/{det_q_high_wm} done={comp}/{exp}",
                )
                runtime = runtime_snapshot()
                active_for = max(0.0, now - float(runtime.get("active_since", 0.0) or now))
                resource_text = ""
                try:
                    import psutil
                    proc = psutil.Process()
                    resource_text = f" rss_mb={proc.memory_info().rss / (1024 * 1024):.0f} cpu_pct={proc.cpu_percent():.1f}"
                except Exception:
                    pass
                log(
                    "INFO",
                    f"[HEARTBEAT] stage={stage_state.get('current')} producer={'done' if stage_state.get('build_done') else 'running'} "
                    f"deterministic={'done' if det_q.empty() else 'running'} ai={'running' if ai_backlog else 'idle'} "
                    f"active_task={runtime.get('active_task') or 'none'} active_model={runtime.get('active_model') or 'none'} "
                    f"active_for_s={active_for:.1f} aggregator={'waiting' if result_q.empty() else 'draining'} "
                    f"apply={'idle' if apply_q.empty() else 'pending'} progress={comp}/{exp} q_ai={ai_backlog}"
                    f"{resource_text}",
                )
                snapshot = (
                    fetch_out.qsize(),
                    det_q.qsize(),
                    ai_batch_q.qsize() if model_first_batching else ai_q.qsize(),
                    result_q.qsize(),
                )
                with metrics_lock:
                    if snapshot != queue_progress["last_snapshot"] or (f + d + a + ap) > 0:
                        queue_progress["last_any_work_ts"] = time.time()
                        queue_progress["last_snapshot"] = snapshot
                if exp > 0 and (time.time() - lp) > stall_timeout_s:
                    # Soft-stall handling: do not kill the whole run. Judge timeouts can make
                    # progress bursty; hard-failing here aborts otherwise recoverable runs.
                    log("WARNING", f"[DISPATCH] no completed answer for {stall_timeout_s}s; patient mode continues waiting for AI")
                    with metrics_lock:
                        progress["last_progress_ts"] = time.time()
                with metrics_lock:
                    idle_for = time.time() - queue_progress["last_any_work_ts"]
                if exp > 0 and idle_for > max(20.0, stall_timeout_s / 2):
                    log("WARNING", f"[DISPATCH] queue movement stalled for {idle_for:.1f}s (done={comp}/{exp})")
                # AI stall reporting only. Do not drain queued work here: doing so can remove
                # shutdown sentinels and can mark valid queued work as failed while the first
                # slow rubric/jury call is still running.
                with metrics_lock:
                    ai_idle_for = time.time() - ai_progress["last_ai_done_ts"]
                    since_ai_warning = time.time() - ai_progress["last_warning_ts"]
                active_ai_qsize = ai_batch_q.qsize() if model_first_batching else ai_q.qsize()
                if active_ai_qsize > 0 and ai_idle_for > ai_stall_timeout_s and since_ai_warning >= 300.0:
                    log(
                        "WARNING",
                        f"[DISPATCH] AI queue has waited {ai_idle_for:.1f}s without a completed AI result "
                        f"(q_ai={active_ai_qsize} q_ai_actual={ai_backlog}); patient mode is still waiting without fallback",
                    )
                    with metrics_lock:
                        ai_progress["last_warning_ts"] = time.time()
            except Exception as ex:
                log("ERROR", f"[Worker: Metrics] reporter loop exception: {ex}")
                # Keep reporter alive even after transient errors.
                continue

    tf = threading.Thread(target=fetch_stage, daemon=False)
    tb = threading.Thread(target=task_builder, daemon=False)
    da = [] if model_first_batching else [threading.Thread(target=det_worker, daemon=False) for _ in range(det_workers)]
    aw: List[threading.Thread] = []
    for provider_name, lane_count in (lane_specs or [("", total_ai_workers)]):
        for i in range(lane_count):
            wid = f"ai-{provider_name}-{i + 1}" if provider_name else f"ai-{i + 1}"
            aw.append(threading.Thread(
                target=ai_worker,
                args=(wid, provider_name or None),
                daemon=False,
                name=wid,
            ))
    if lane_specs:
        jury_now = effective_jury_concurrency(cfg)
        if jury_now < total_ai_workers:
            log(
                "WARNING",
                f"[DISPATCH] dual_lane jury concurrency {jury_now} < total workers {total_ai_workers}; "
                f"lanes may serialize on the jury semaphore",
            )
        else:
            log(
                "INFO",
                f"[DISPATCH] dual_lane lanes={dict(lane_specs)} "
                f"total_ai_workers={total_ai_workers} jury_concurrency={jury_now}",
            )
    ag = threading.Thread(target=result_aggregator, daemon=False)
    ap = threading.Thread(target=incremental_apply_worker, daemon=False)
    mr = threading.Thread(target=metrics_reporter, daemon=False)
    rs = threading.Thread(target=retry_scheduler, daemon=False)

    if staged_startup:
        log("INFO", "[DISPATCH] Staged startup ON: phase1(fetch) -> phase2(task_builder) -> phase3(workers)")
        set_stage("fetch")
        announce_stage(1, "Fetch All Forms/Responses", "START")
        tf.start()
        tf.join()
        if failed.is_set():
            raise RuntimeError("Global dispatcher failed in fetch phase")
        announce_stage(1, "Fetch All Forms/Responses", "DONE")

        validate_stage_transition("build")
        set_stage("build")
        announce_stage(2, "Build/Distribute Tasks", "START")
        tb.start()
        tb.join()
        if failed.is_set():
            raise RuntimeError("Global dispatcher failed in task-builder phase")
        announce_model_plan()
        announce_stage(2, "Build/Distribute Tasks", "DONE")

        validate_stage_transition("workers")
        set_stage("workers")
        announce_stage(3, "Run Deterministic + AI + Aggregation", "START")
        with metrics_lock:
            expected_at_start = int(progress["expected_tasks"])
        _progress_print(f"FormProgress: 0/{expected_at_start}")
        first_meta = next((data.get("meta", {}) for data in forms_results.values()), {})
        gui_event(
            "run_start", form_title=first_meta.get("title", "Google Form"), total=expected_at_start,
            transcript_path=str(cfg.get("gui_terminal_log_path", "logs/gui_terminal.log")),
            jsonl_path=str(cfg.get("gui_terminal_jsonl_path", "logs/gui_terminal.jsonl")),
        )
        [t.start() for t in da]
        [t.start() for t in aw]
        ag.start()
        ap.start()
        mr.start()
        rs.start()
        if expected_at_start == 0:
            result_q.put(None)
    else:
        tf.start(); tb.start(); [t.start() for t in da]; [t.start() for t in aw]; ag.start(); ap.start(); mr.start(); rs.start()
        tf.join(); tb.join()
        announce_model_plan()
        with metrics_lock:
            expected_at_start = int(progress["expected_tasks"])
        first_meta = next((data.get("meta", {}) for data in forms_results.values()), {})
        gui_event(
            "run_start", form_title=first_meta.get("title", "Google Form"), total=expected_at_start,
            transcript_path=str(cfg.get("gui_terminal_log_path", "logs/gui_terminal.log")),
            jsonl_path=str(cfg.get("gui_terminal_jsonl_path", "logs/gui_terminal.jsonl")),
        )
        if expected_at_start == 0:
            result_q.put(None)

    [t.join() for t in da]
    # Flush requeue leftovers BEFORE releasing the AI sentinels. Injecting a
    # retried task after an AI worker consumed its None sentinel strands that
    # task in the queue forever and can silently end the run with work
    # unprocessed (observed: run "finished" at 69/225 with 120 queued).
    try:
        retry_schedule_q.put(None, timeout=2)
        rs.join(timeout=15)
    except Exception:
        pass
    if rs.is_alive():
        log("WARNING", "[REQUEUE] retry_scheduler did not stop cleanly")
    if model_first_batching and lane_batch_qs:
        # Per-lane sentinels: each lane's workers exit on their OWN queue.
        for _lane_name, _lane_count in lane_specs:
            if _lane_name not in lane_batch_qs:
                continue
            for _ in range(_lane_count):
                try:
                    lane_batch_qs[_lane_name].put(None, timeout=1)
                except Exception:
                    pass
    else:
        for _ in range(ai_workers):
            try:
                if model_first_batching:
                    ai_batch_q.put(None, timeout=1)
                else:
                    ai_q.put(None, timeout=1)
            except Exception:
                pass
    [t.join() for t in aw]
    ag.join(timeout=30)
    if ag.is_alive():
        result_q.put(None)
        ag.join(timeout=10)
    apply_q.put(None)
    apply_q.join()
    ap.join(timeout=10)
    stop.set()
    mr.join(timeout=6)

    if failed.is_set():
        raise RuntimeError("Global dispatcher failed due to stall/crash")

    # Surface incomplete accounting loudly instead of reporting success while
    # answers were dropped or stranded.
    with metrics_lock:
        final_completed = int(progress["completed"])
        final_expected = int(progress["expected_tasks"])
        gui_accepted = int(progress["accepted"])
        gui_review = int(progress["review_answers"])
        gui_rejected = int(progress["rejected"])
    if final_expected > 0 and final_completed < final_expected:
        log(
            "ERROR",
            f"[DISPATCH] Run finished INCOMPLETE: {final_completed}/{final_expected} answers "
            "accounted for; check [REQUEUE]/judge errors above.",
        )
        gui_event(
            "run_incomplete",
            completed=final_completed,
            expected=final_expected,
            elapsed=elapsed_text(time.time() - form_started_ts),
        )
    gui_event(
        "run_complete", accepted=gui_accepted, review=gui_review, rejected=gui_rejected,
        elapsed=elapsed_text(time.time() - form_started_ts),
    )

    # All answer-key writes are already complete; finalize reports/timestamps.
    for i in sorted(forms_results.keys()):
        data = forms_results[i]
        meta = data["meta"]
        form_id = meta.get("form_id", "")
        title = meta.get("title", f"Form_{form_id}")
        if data.get("partial"):
            log("INFO", f"[FORM] PARTIAL '{title}' ({form_id}) reason={data.get('partial_reason', 'Partial')}")
        structure = meta.get("structure", [])
        log("INFO", f"[FORM] START {i}/{forms_total} | form_id={form_id}")
        all_questions = []
        for q in structure:
            qid = q["questionId"]
            accepted = [a for a in data["question_answers"].get(qid, []) if a]
            all_questions.append({"question": q, "responses": [], "correct_answers": accepted})
        if generate_report:
            generate_form_feedback(form_id, title, all_questions)
        save_grading_time(form_id, datetime.now(timezone.utc))
        log("INFO", f"[FORM] FINISHED '{title}' ({form_id})")
        counts = data.get("counts", {}) or {}
        answers = data.get("question_answers", {}) or {}
        rejected = data.get("question_rejected", {}) or {}
        total = int(sum(counts.get(qid, 0) for qid in counts))
        accepted = int(
            sum(len([a for a in answers.get(qid, []) if a]) for qid in answers)
        )
        review = int(sum(1 for (fi, qid) in review_question_ids if fi == i))
        rejected_count = int(sum(len(rejected.get(qid, [])) for qid in rejected))
        fi_slot = form_progress.get(i)
        if fi_slot is not None:
            with metrics_lock:
                row_total = int(fi_slot["total"])
                fi_slot["done"] = row_total
            if row_total > 0:
                _progress_print(f"FormRowProgress: {form_id} {row_total}/{row_total}")
        _progress_print(
            f"FormDone: {form_id} total={total} accepted={accepted} "
            f"review={review} rejected={rejected_count}"
        )

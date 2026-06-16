import queue
import threading
import time
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from auth import get_service
from deterministic_checks import run_deterministic_checks
from evaluation_pipeline import EvaluationResult, evaluate_answer
from evaluator_config import load_config
from feedback import generate_form_feedback
from form_context_builder import (
    apply_question_context,
    build_form_context,
    get_effective_expected,
    get_question_context,
    should_block_answer_updates,
)
from form_utils import get_form_structure
from logger import log, stage_banner
from response_utils import save_grading_time
from updater import update_correct_answers


@dataclass
class Task:
    form_idx: int
    form_id: str
    form_title: str
    question: Dict
    answer_idx: int
    answer: str
    expected: List[str]


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
    cfg = load_config()
    staged_startup = bool(cfg.get("staged_thread_startup", True))
    fetch_workers = max(1, int(cfg.get("global_prefetch_workers", 4)))
    det_workers = max(1, int(cfg.get("deterministic_worker_count", 5)))
    ai_workers = max(1, int(cfg.get("ai_worker_count", 3)))
    max_latency = float(cfg.get("max_latency_per_answer_seconds", 30.0))
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
    # In staged startup mode we intentionally allow task_builder to enqueue all work
    # before consumers start.
    det_q: "queue.Queue[Optional[Task]]" = queue.Queue(maxsize=0 if staged_startup else queue_size)
    ai_q: "queue.Queue[Optional[Task]]" = queue.Queue(maxsize=max(200, int(queue_size * 0.5)))
    result_q: "queue.Queue[Optional[tuple[Task, EvaluationResult]]]" = queue.Queue(maxsize=max(200, int(queue_size * 0.75)))
    stop = threading.Event()
    failed = threading.Event()

    forms_results: Dict[int, Dict] = {}
    forms_total = len(form_urls)
    metrics_lock = threading.Lock()
    counters = {"fetch": 0, "det": 0, "ai": 0, "apply": 0}
    progress = {"expected_tasks": 0, "completed": 0, "last_progress_ts": time.time(), "pending_buffer": 0, "ai_backlog": 0}
    queue_progress = {"last_any_work_ts": time.time(), "last_snapshot": (0, 0, 0, 0)}
    ai_progress = {"last_ai_done_ts": time.time()}
    task_builder_metrics = {"built": 0, "enqueued": 0, "last_emit": time.time()}
    task_builder_log_path = str(cfg.get("task_builder_log_path", "task_builder_metrics.jsonl"))
    task_builder_log_enabled = bool(cfg.get("task_builder_log_enabled", True))
    stage_lock = threading.Lock()
    stage_state = {"current": "init", "fetch_done": False, "build_done": False}

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
            "q_ai": ai_q.qsize(),
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
                q["type"] = "MULTIPLE_CHOICE"
            elif "checkboxQuestion" in question:
                q["type"] = "CHECKBOX"
            elif "dropdownQuestion" in question:
                q["type"] = "DROPDOWN"
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
                if pending_tasks and det_q.qsize() <= det_q_low_wm:
                    refill_det_queue(force=True)

                if fetch_done:
                    if pending_tasks:
                        refill_det_queue(force=True)
                        if pending_tasks:
                            time.sleep(0.02)
                            continue
                    break
                try:
                    item = fetch_out.get(timeout=0.5)
                except queue.Empty:
                    if pending_tasks:
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
                forms_results[i] = {"meta": item, "question_answers": {}, "counts": {}}

                # Build per-question answer buckets once to avoid O(questions * responses) scans.
                answers_by_qid: Dict[str, List[str]] = {}
                for r in all_responses:
                    ad = r.get("answers", {})
                    for resp_qid, qa in ad.items():
                        bucket = answers_by_qid.setdefault(resp_qid, [])
                        for a in qa.get("textAnswers", {}).get("answers", []):
                            if a.get("value") is not None:
                                bucket.append(str(a["value"]).strip())
                        for a in qa.get("choiceAnswers", {}).get("answers", []):
                            if a.get("value") is not None:
                                bucket.append(str(a["value"]).strip())

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

                for q in structure:
                    qid = q.get("questionId")
                    expected = get_effective_expected(q, expected_by_item_id.get(q.get("itemId"), []))
                    answers = answers_by_qid.get(qid, [])
                    forms_results[i]["counts"][qid] = len(answers)
                    for ai, ans in enumerate(answers):
                        pending_tasks.append(Task(i, form_id, title, q, ai, ans, expected))
                        with metrics_lock:
                            progress["expected_tasks"] += 1
                            progress["pending_buffer"] = len(pending_tasks)
                            task_builder_metrics["built"] += 1

                    # Burst-fill up to high watermark while we build tasks.
                    refill_det_queue(force=False)
                    # If producer buffer gets very large, force-drain until queue blocks.
                    if len(pending_tasks) >= det_q_high_wm:
                        refill_det_queue(force=True)
                    with metrics_lock:
                        progress["pending_buffer"] = len(pending_tasks)
                    emit_task_builder_metric(event="form_chunk")

            # Drain any remaining buffered tasks before shutdown sentinels.
            while pending_tasks and not stop.is_set():
                refill_det_queue(force=True)
                with metrics_lock:
                    progress["pending_buffer"] = len(pending_tasks)
                if pending_tasks:
                    time.sleep(0.05)
                emit_task_builder_metric(event="drain")

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

    def evaluate_answer_bounded(t: Task) -> EvaluationResult:
        hard_timeout_s = max(
            float(cfg.get("max_latency_per_answer_seconds", 45.0)) + 45.0,
            float(cfg.get("answer_hard_timeout_seconds", 120.0)),
        )
        result_holder: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=1)

        def _runner():
            try:
                result_holder.put((
                    "ok",
                    evaluate_answer(t.answer, t.expected, get_question_context(t.question)),
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
                decision="NO",
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

    def det_worker():
        log("INFO", "[Worker: Deterministic] START det_worker")
        while not stop.is_set():
            try:
                t = det_q.get(timeout=1)
            except queue.Empty:
                continue
            if t is None:
                ai_q.put(None)
                log("INFO", "[Worker: Deterministic] DONE det_worker")
                return
            try:
                det = run_deterministic_checks(
                    t.answer, t.expected, float(cfg.get("numeric_tolerance", 0.01))
                )
                if det.accepted and det.confidence >= 0.95:
                    r = EvaluationResult(
                        answer=t.answer, decision="YES", final_score=det.confidence, semantic_score=det.confidence,
                        concept_score=det.confidence, factual_score=det.confidence, misconception_detected=False,
                        misconception_description="", missing_concepts=[], accepted_concepts=[], model_agreement=1.0,
                        confidence=det.confidence, fast_path_used=True, latency_ms=0.0, stage_reached="deterministic"
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

    def ai_worker():
        log("INFO", "[Worker: AI] START ai_worker")
        while not stop.is_set():
            try:
                t = ai_q.get(timeout=1)
            except queue.Empty:
                with metrics_lock:
                    expected = int(progress.get("expected_tasks", 0))
                    completed = int(progress.get("completed", 0))
                    backlog = int(progress.get("ai_backlog", 0))
                if expected > 0 and completed >= expected and backlog <= 0:
                    log("INFO", "[Worker: AI] DONE ai_worker (all work complete)")
                    return
                continue
            if t is None:
                log("INFO", "[Worker: AI] DONE ai_worker")
                return

            started = time.perf_counter()
            try:
                r = evaluate_answer_bounded(t)
            except Exception as exx:
                log("ERROR", f"[DISPATCH] ai worker error: {exx}")
                r = EvaluationResult(
                    answer=t.answer, decision="NO", final_score=0.0, semantic_score=0.0, concept_score=0.0,
                    factual_score=0.0, misconception_detected=False, misconception_description="error",
                    missing_concepts=[], accepted_concepts=[], model_agreement=0.0, confidence=0.0,
                    fast_path_used=False, latency_ms=0.0, stage_reached="worker_error"
                )
            elapsed_s = time.perf_counter() - started
            hard_budget_s = max_latency + 30
            if elapsed_s > hard_budget_s:
                log("WARNING", f"[DISPATCH] evaluate_answer exceeded budget: {elapsed_s:.1f}s > {hard_budget_s:.1f}s")
                r = EvaluationResult(
                    answer=t.answer, decision="NO", final_score=0.0, semantic_score=0.0, concept_score=0.0,
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

    def result_aggregator():
        log("INFO", "[Worker: Aggregator] START result_aggregator")
        while not stop.is_set():
            with metrics_lock:
                expected = progress["expected_tasks"]
                completed = progress["completed"]
            if completed >= expected and expected > 0 and det_q.empty() and ai_q.empty() and result_q.empty():
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
            fi = t.form_idx
            qid = t.question["questionId"]
            forms_results.setdefault(fi, {"meta": {}, "question_answers": {}, "counts": {}})
            forms_results[fi]["question_answers"].setdefault(qid, []).append(r.answer if r.decision == "YES" else None)
            with metrics_lock:
                progress["completed"] += 1
                progress["last_progress_ts"] = time.time()

    def metrics_reporter():
        last = time.time()
        ai_stall_timeout_s = float(cfg.get("ai_stall_timeout_seconds", 90))
        log("INFO", "[Worker: Metrics] START metrics_reporter")
        while not stop.is_set():
            try:
                time.sleep(5.0)
                now = time.time()
                dt = max(0.001, now - last)
                last = now
                with metrics_lock:
                    f = counters["fetch"]; d = counters["det"]; a = counters["ai"]; ap = counters["apply"]
                    counters["fetch"] = counters["det"] = counters["ai"] = counters["apply"] = 0
                    exp = progress["expected_tasks"]; comp = progress["completed"]; lp = progress["last_progress_ts"]; pb = progress["pending_buffer"]; ai_backlog = progress["ai_backlog"]
                log(
                    "INFO",
                    f"[DISPATCH METRICS] fetch/s={f/dt:.2f} det/s={d/dt:.2f} ai/s={a/dt:.2f} apply/s={ap/dt:.2f} "
                    f"q_fetch={fetch_out.qsize()} q_det={det_q.qsize()} q_ai={ai_q.qsize()} q_ai_actual={ai_backlog} q_result={result_q.qsize()} "
                    f"pending={pb} wm={det_q_low_wm}/{det_q_high_wm} done={comp}/{exp}",
                )
                log("INFO", f"[Worker: Producer] heartbeat q_fetch={fetch_out.qsize()} pending={pb}")
                log("INFO", f"[Worker: Deterministic] heartbeat q_det={det_q.qsize()} det_s={d/dt:.2f}")
                log("INFO", f"[Worker: AI] heartbeat q_ai_actual={ai_backlog} q_ai_buffer={ai_q.qsize()} ai_s={a/dt:.2f}")
                log("INFO", f"[Worker: Aggregator] heartbeat q_result={result_q.qsize()} done={comp}/{exp}")
                snapshot = (fetch_out.qsize(), det_q.qsize(), ai_q.qsize(), result_q.qsize())
                with metrics_lock:
                    if snapshot != queue_progress["last_snapshot"] or (f + d + a + ap) > 0:
                        queue_progress["last_any_work_ts"] = time.time()
                        queue_progress["last_snapshot"] = snapshot
                if exp > 0 and (time.time() - lp) > stall_timeout_s:
                    # Soft-stall handling: do not kill the whole run. Judge timeouts can make
                    # progress bursty; hard-failing here aborts otherwise recoverable runs.
                    log("WARNING", f"[DISPATCH] stall detected: no progress for {stall_timeout_s}s (continuing with timeout fallbacks)")
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
                if ai_q.qsize() > 0 and ai_idle_for > ai_stall_timeout_s:
                    log(
                        "WARNING",
                        f"[DISPATCH] AI queue has waited {ai_idle_for:.1f}s without a completed AI result "
                        f"(q_ai={ai_q.qsize()} q_ai_actual={ai_backlog}); active answer timeout remains responsible for fallback",
                    )
            except Exception as ex:
                log("ERROR", f"[Worker: Metrics] reporter loop exception: {ex}")
                # Keep reporter alive even after transient errors.
                continue

    tf = threading.Thread(target=fetch_stage, daemon=False)
    tb = threading.Thread(target=task_builder, daemon=False)
    da = [threading.Thread(target=det_worker, daemon=False) for _ in range(det_workers)]
    aw = [threading.Thread(target=ai_worker, daemon=False) for _ in range(ai_workers)]
    ag = threading.Thread(target=result_aggregator, daemon=False)
    mr = threading.Thread(target=metrics_reporter, daemon=False)

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
        announce_stage(2, "Build/Distribute Tasks", "DONE")

        validate_stage_transition("workers")
        set_stage("workers")
        announce_stage(3, "Run Deterministic + AI + Aggregation", "START")
        [t.start() for t in da]
        [t.start() for t in aw]
        ag.start()
        mr.start()
    else:
        tf.start(); tb.start(); [t.start() for t in da]; [t.start() for t in aw]; ag.start(); mr.start()
        tf.join(); tb.join()

    [t.join() for t in da]
    for _ in range(ai_workers):
        try:
            ai_q.put(None, timeout=1)
        except Exception:
            pass
    [t.join() for t in aw]
    stop.set()
    ag.join(timeout=15); mr.join(timeout=6)

    if failed.is_set():
        raise RuntimeError("Global dispatcher failed due to stall/crash")

    # Apply sequentially
    service = get_service()
    for i in sorted(forms_results.keys()):
        data = forms_results[i]
        meta = data["meta"]
        form_id = meta.get("form_id", "")
        title = meta.get("title", f"Form_{form_id}")
        structure = meta.get("structure", [])
        log("INFO", f"[FORM] START {i}/{forms_total} | form_id={form_id}")
        all_questions = []
        for q in structure:
            qid = q["questionId"]
            accepted = [a for a in data["question_answers"].get(qid, []) if a]
            all_questions.append({"question": q, "responses": [], "correct_answers": accepted})
            if should_block_answer_updates(q):
                validation = q.get("expected_validation") or {}
                log(
                    "WARNING",
                    f"[EXPECTED VALIDATOR] blocking updates for Q{q.get('index')} "
                    f"{q.get('title')} reason={validation.get('reason', '')}",
                )
                continue
            if accepted and q["type"] in {"SHORT_ANSWER", "LONG_ANSWER"}:
                update_correct_answers(service, form_id, q["itemId"], accepted, q["index"])
        if generate_report:
            generate_form_feedback(form_id, title, all_questions)
        save_grading_time(form_id, datetime.now(timezone.utc))
        with metrics_lock:
            counters["apply"] += 1
        log("INFO", f"[FORM] FINISHED '{title}' ({form_id})")

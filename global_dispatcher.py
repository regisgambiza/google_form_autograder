import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from auth import get_service
from deterministic_checks import run_deterministic_checks
from evaluation_pipeline import EvaluationResult, evaluate_answer
from evaluator_config import load_config
from feedback import generate_form_feedback
from form_utils import get_form_structure
from logger import log
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
    fetch_workers = max(1, int(cfg.get("global_prefetch_workers", 4)))
    det_workers = max(1, int(cfg.get("deterministic_worker_count", 5)))
    ai_workers = max(1, int(cfg.get("ai_worker_count", 3)))
    max_latency = float(cfg.get("max_latency_per_answer_seconds", 30.0))
    read_rate_per_min = float(cfg.get("forms_expensive_reads_per_minute", 160))
    stall_timeout_s = float(cfg.get("dispatcher_stall_timeout_seconds", 90))
    bucket = TokenBucket(rate_per_sec=read_rate_per_min / 60.0, capacity=max(5, int(read_rate_per_min / 4)))

    fetch_out: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=200)
    det_q: "queue.Queue[Optional[Task]]" = queue.Queue(maxsize=4000)
    ai_q: "queue.Queue[Optional[Task]]" = queue.Queue(maxsize=2000)
    result_q: "queue.Queue[Optional[tuple[Task, EvaluationResult]]]" = queue.Queue(maxsize=3000)
    stop = threading.Event()
    failed = threading.Event()

    forms_results: Dict[int, Dict] = {}
    forms_total = len(form_urls)
    metrics_lock = threading.Lock()
    counters = {"fetch": 0, "det": 0, "ai": 0, "apply": 0}
    progress = {"expected_tasks": 0, "completed": 0, "last_progress_ts": time.time()}

    def fetch_form(i: int, url: str):
        service = get_service()
        form_id = url.split("/d/")[1].split("/")[0] if "/d/" in url else url
        bucket.acquire()
        form = service.forms().get(formId=form_id).execute()
        title = form.get("info", {}).get("title", f"Form_{form_id}")
        structure = get_form_structure(service, form_id)
        responses = []
        page = None
        while True:
            bucket.acquire()
            resp = service.forms().responses().list(formId=form_id, pageToken=page).execute()
            responses.extend(resp.get("responses", []))
            page = resp.get("nextPageToken")
            if not page:
                break
        return {"idx": i, "url": url, "form_id": form_id, "title": title, "structure": structure, "form_data": form, "responses": responses}

    def fetch_stage():
        try:
            with ThreadPoolExecutor(max_workers=fetch_workers) as ex:
                futs = [ex.submit(fetch_form, i + 1, u) for i, u in enumerate(form_urls)]
                for f in futs:
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
        except Exception as ex:
            log("ERROR", f"[DISPATCH] fetch_stage crashed: {ex}")
            failed.set()
            stop.set()

    def task_builder():
        try:
            while not stop.is_set():
                item = fetch_out.get(timeout=1)
                if item is None:
                    break
                i = item["idx"]
                form_id = item["form_id"]
                title = item["title"]
                structure = item["structure"] or []
                form_data = item["form_data"] or {"items": []}
                all_responses = item["responses"] or []
                forms_results[i] = {"meta": item, "question_answers": {}, "counts": {}}
                for q in structure:
                    qid = q.get("questionId")
                    expected = []
                    try:
                        for it in form_data.get("items", []):
                            if it.get("itemId") == q.get("itemId") and "questionItem" in it:
                                grading = it["questionItem"]["question"].get("grading", {})
                                ans = grading.get("correctAnswers", {}).get("answers", [])
                                expected = [a["value"] for a in ans if "value" in a]
                                break
                    except Exception:
                        pass
                    answers = []
                    for r in all_responses:
                        ad = r.get("answers", {})
                        if qid in ad:
                            qa = ad[qid]
                            for a in qa.get("textAnswers", {}).get("answers", []):
                                if a.get("value") is not None:
                                    answers.append(str(a["value"]).strip())
                            for a in qa.get("choiceAnswers", {}).get("answers", []):
                                if a.get("value") is not None:
                                    answers.append(str(a["value"]).strip())
                    forms_results[i]["counts"][qid] = len(answers)
                    for ai, ans in enumerate(answers):
                        det_q.put(Task(i, form_id, title, q, ai, ans, expected), timeout=2)
                        with metrics_lock:
                            progress["expected_tasks"] += 1
            for _ in range(det_workers):
                det_q.put(None, timeout=2)
        except Exception as ex:
            log("ERROR", f"[DISPATCH] task_builder crashed: {ex}")
            failed.set()
            stop.set()

    def det_worker():
        while not stop.is_set():
            try:
                t = det_q.get(timeout=1)
            except queue.Empty:
                continue
            if t is None:
                ai_q.put(None)
                return
            try:
                det = run_deterministic_checks(t.answer, t.expected, float(cfg.get("numeric_tolerance", 0.01)))
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
                    ai_q.put(t, timeout=2)
            except Exception as ex:
                log("WARNING", f"[DISPATCH] deterministic worker failed: {ex}")
                try:
                    ai_q.put(t, timeout=2)
                except Exception:
                    pass

    def ai_worker():
        with ThreadPoolExecutor(max_workers=1) as ex:
            while not stop.is_set():
                try:
                    t = ai_q.get(timeout=1)
                except queue.Empty:
                    continue
                if t is None:
                    return
                fut = ex.submit(evaluate_answer, t.answer, t.expected, str(t.question.get("title", "")))
                try:
                    r = fut.result(timeout=max_latency + 30)
                except FuturesTimeoutError:
                    r = EvaluationResult(
                        answer=t.answer, decision="NO", final_score=0.0, semantic_score=0.0, concept_score=0.0,
                        factual_score=0.0, misconception_detected=False, misconception_description="timeout",
                        missing_concepts=[], accepted_concepts=[], model_agreement=0.0, confidence=0.0,
                        fast_path_used=False, latency_ms=(max_latency + 30) * 1000, stage_reached="timeout"
                    )
                except Exception as exx:
                    log("ERROR", f"[DISPATCH] ai worker error: {exx}")
                    r = EvaluationResult(
                        answer=t.answer, decision="NO", final_score=0.0, semantic_score=0.0, concept_score=0.0,
                        factual_score=0.0, misconception_detected=False, misconception_description="error",
                        missing_concepts=[], accepted_concepts=[], model_agreement=0.0, confidence=0.0,
                        fast_path_used=False, latency_ms=0.0, stage_reached="worker_error"
                    )
                try:
                    result_q.put((t, r), timeout=2)
                    with metrics_lock:
                        counters["ai"] += 1
                except Exception:
                    pass

    def result_aggregator():
        while not stop.is_set():
            with metrics_lock:
                expected = progress["expected_tasks"]
                completed = progress["completed"]
            if completed >= expected and expected > 0 and det_q.empty() and ai_q.empty() and result_q.empty():
                return
            try:
                item = result_q.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
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
        while not stop.is_set():
            time.sleep(5.0)
            now = time.time()
            dt = max(0.001, now - last)
            last = now
            with metrics_lock:
                f = counters["fetch"]; d = counters["det"]; a = counters["ai"]; ap = counters["apply"]
                counters["fetch"] = counters["det"] = counters["ai"] = counters["apply"] = 0
                exp = progress["expected_tasks"]; comp = progress["completed"]; lp = progress["last_progress_ts"]
            log(
                "INFO",
                f"[DISPATCH METRICS] fetch/s={f/dt:.2f} det/s={d/dt:.2f} ai/s={a/dt:.2f} apply/s={ap/dt:.2f} "
                f"q_fetch={fetch_out.qsize()} q_det={det_q.qsize()} q_ai={ai_q.qsize()} q_result={result_q.qsize()} done={comp}/{exp}",
            )
            if exp > 0 and (time.time() - lp) > stall_timeout_s:
                log("ERROR", f"[DISPATCH] stall detected: no progress for {stall_timeout_s}s")
                failed.set()
                stop.set()
                return

    tf = threading.Thread(target=fetch_stage, daemon=False)
    tb = threading.Thread(target=task_builder, daemon=False)
    da = [threading.Thread(target=det_worker, daemon=False) for _ in range(det_workers)]
    aw = [threading.Thread(target=ai_worker, daemon=False) for _ in range(ai_workers)]
    ag = threading.Thread(target=result_aggregator, daemon=False)
    mr = threading.Thread(target=metrics_reporter, daemon=False)

    tf.start(); tb.start(); [t.start() for t in da]; [t.start() for t in aw]; ag.start(); mr.start()
    tf.join(); tb.join(); [t.join() for t in da]
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
            if accepted and q["type"] in {"SHORT_ANSWER", "LONG_ANSWER"}:
                update_correct_answers(service, form_id, q["itemId"], accepted, q["index"])
        if generate_report:
            generate_form_feedback(form_id, title, all_questions)
        save_grading_time(form_id, datetime.now(timezone.utc))
        with metrics_lock:
            counters["apply"] += 1
        log("INFO", f"[FORM] FINISHED '{title}' ({form_id})")

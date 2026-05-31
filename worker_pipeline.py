import queue
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from deterministic_checks import run_deterministic_checks
from evaluation_pipeline import EvaluationResult, evaluate_answer, get_or_generate_rubric
from evaluator_config import load_config
from logger import log


@dataclass
class AnswerTask:
    index: int
    answer: str


@dataclass
class TaskResult:
    index: int
    result: EvaluationResult


def evaluate_answers_worker_pipeline(
    answers: List[str],
    expected: Union[str, List[str]],
    question: str,
) -> List[EvaluationResult]:
    cfg = load_config()
    det_workers = max(1, int(cfg.get("deterministic_worker_count", 6)))
    ai_workers = max(1, int(cfg.get("ai_worker_count", 2)))
    qsize = max(100, int(cfg.get("worker_queue_size", 2000)))
    numeric_tolerance = float(cfg.get("numeric_tolerance", 0.01))

    ingress_q: "queue.Queue[Optional[AnswerTask]]" = queue.Queue(maxsize=qsize)
    ai_q: "queue.Queue[Optional[AnswerTask]]" = queue.Queue(maxsize=qsize)
    results_q: "queue.Queue[TaskResult]" = queue.Queue(maxsize=qsize)

    total = len(answers)
    results_by_index: Dict[int, EvaluationResult] = {}
    det_done = 0
    ai_done = 0
    det_stopped = 0
    last_det_activity = time.time()
    last_ai_activity = time.time()
    lock = threading.Lock()
    stop_event = threading.Event()
    pipeline_id = f"{int(time.time() * 1000)}-{threading.get_ident()}"

    # Warm rubric cache once for this question before workers begin.
    try:
        log("INFO", f"[Worker Pipeline {pipeline_id}] Rubric warm-up started.")
        _ = get_or_generate_rubric(question, expected)
        log("INFO", f"[Worker Pipeline {pipeline_id}] Rubric warm-up complete.")
    except Exception as ex:
        log("WARNING", f"[Worker Pipeline {pipeline_id}] Rubric warm-up failed, continuing with lazy generation: {ex}")

    def producer() -> None:
        if stop_event.is_set():
            return
        log("INFO", f"[Worker: Producer] Started. Sending {total} answers into pipeline.")
        for i, ans in enumerate(answers):
            ingress_q.put(AnswerTask(i, ans))
        for _ in range(det_workers):
            ingress_q.put(None)
        log("INFO", "[Worker: Producer] Finished sending all answers.")

    def det_worker() -> None:
        nonlocal det_done, det_stopped, last_det_activity
        name = threading.current_thread().name
        log("INFO", f"[Worker: Deterministic] {name} started.")
        while True:
            if stop_event.is_set():
                log("INFO", f"[Worker: Deterministic] {name} received stop signal.")
                return
            task = ingress_q.get()
            if task is None:
                ingress_q.task_done()
                with lock:
                    det_stopped += 1
                    # Only the last deterministic worker sends AI shutdown signals.
                    if det_stopped == det_workers:
                        for _ in range(ai_workers):
                            ai_q.put(None)
                log("INFO", f"[Worker: Deterministic] {name} stopped.")
                return
            try:
                det = run_deterministic_checks(task.answer, expected, numeric_tolerance)
                if det.accepted and det.confidence >= 0.95:
                    lat_ms = 0.0
                    res = EvaluationResult(
                        answer=task.answer,
                        decision="YES",
                        final_score=det.confidence,
                        semantic_score=det.confidence,
                        concept_score=det.confidence,
                        factual_score=det.confidence,
                        misconception_detected=False,
                        misconception_description="",
                        missing_concepts=[],
                        accepted_concepts=[],
                        model_agreement=1.0,
                        confidence=det.confidence,
                        fast_path_used=True,
                        latency_ms=lat_ms,
                        stage_reached="deterministic",
                    )
                    results_q.put(TaskResult(task.index, res))
                    with lock:
                        det_done += 1
                        last_det_activity = time.time()
                    log("DEBUG", f"[Worker: Deterministic] {name} accepted answer #{task.index + 1} using fast rules.")
                else:
                    ai_q.put(task)
                    with lock:
                        last_det_activity = time.time()
                    log("DEBUG", f"[Worker: Deterministic] {name} sent answer #{task.index + 1} to AI queue.")
            finally:
                ingress_q.task_done()

    def ai_worker() -> None:
        nonlocal ai_done, last_ai_activity
        name = threading.current_thread().name
        log("INFO", f"[Worker: AI] {name} started.")
        while True:
            if stop_event.is_set():
                log("INFO", f"[Worker: AI] {name} received stop signal.")
                return
            task = ai_q.get()
            if task is None:
                ai_q.task_done()
                log("INFO", f"[Worker: AI] {name} stopped.")
                return
            try:
                try:
                    res = evaluate_answer(task.answer, expected, question)
                except Exception as ex:
                    # Keep worker alive and return a safe fallback result instead of crashing thread.
                    log("ERROR", f"[Worker: AI] {name} failed on answer #{task.index + 1}: {ex}")
                    res = EvaluationResult(
                        answer=task.answer,
                        decision="NO",
                        final_score=0.0,
                        semantic_score=0.0,
                        concept_score=0.0,
                        factual_score=0.0,
                        misconception_detected=False,
                        misconception_description="worker_error_fallback",
                        missing_concepts=[],
                        accepted_concepts=[],
                        model_agreement=0.0,
                        confidence=0.0,
                        fast_path_used=False,
                        latency_ms=0.0,
                        stage_reached="worker_error",
                    )
                results_q.put(TaskResult(task.index, res))
                with lock:
                    ai_done += 1
                    last_ai_activity = time.time()
                log("DEBUG", f"[Worker: AI] {name} finished answer #{task.index + 1}: decision={res.decision}, stage={res.stage_reached}.")
            finally:
                ai_q.task_done()

    def metrics_worker() -> None:
        while not stop_event.is_set():
            with lock:
                done = len(results_by_index)
                det_local = det_done
                ai_local = ai_done
                det_idle_s = time.time() - last_det_activity
                ai_idle_s = time.time() - last_ai_activity
            log(
                "INFO",
                f"[Worker Metrics] pipeline={pipeline_id} done={done}/{total} det_done={det_local} ai_done={ai_local} "
                f"q_det={ingress_q.qsize()} q_ai={ai_q.qsize()} q_result={results_q.qsize()} "
                f"det_idle_s={det_idle_s:.1f} ai_idle_s={ai_idle_s:.1f}",
            )
            if det_idle_s > 5.0:
                log("WARNING", f"[Worker Idle] Deterministic workers idle for {det_idle_s:.1f}s (pipeline={pipeline_id})")
            if ai_idle_s > 5.0:
                log("WARNING", f"[Worker Idle] AI workers idle for {ai_idle_s:.1f}s (pipeline={pipeline_id})")
            if done >= total:
                return
            time.sleep(5.0)

    start = time.perf_counter()
    prod_t = threading.Thread(target=producer, daemon=False, name="wp-producer")
    det_threads = [threading.Thread(target=det_worker, daemon=False, name=f"wp-det-{i}") for i in range(det_workers)]
    ai_threads = [threading.Thread(target=ai_worker, daemon=False, name=f"wp-ai-{i}") for i in range(ai_workers)]
    metrics_t = threading.Thread(target=metrics_worker, daemon=False, name="wp-metrics")

    log("INFO", f"[Worker Pipeline {pipeline_id}] Enabled. Deterministic workers={det_workers}, AI workers={ai_workers}, total answers={total}.")
    prod_t.start()
    for t in det_threads:
        t.start()
    for t in ai_threads:
        t.start()
    metrics_t.start()

    try:
        while len(results_by_index) < total:
            try:
                tr = results_q.get(timeout=5.0)
                results_by_index[tr.index] = tr.result
                if len(results_by_index) % 10 == 0 or len(results_by_index) == total:
                    elapsed = time.perf_counter() - start
                    log("INFO", f"[Worker: Aggregator] Progress {len(results_by_index)}/{total} answers done | fast-path={det_done} | AI-complete={ai_done} | elapsed={elapsed:.1f}s")
            except queue.Empty:
                if stop_event.is_set():
                    break
                continue
    finally:
        stop_event.set()
        prod_t.join(timeout=5)
        for t in det_threads:
            t.join(timeout=10)
        for t in ai_threads:
            t.join(timeout=30)
        metrics_t.join(timeout=5)
    log("INFO", "[Worker: Producer] Job complete.")
    log("INFO", "[Worker: Deterministic] Job complete.")
    log("INFO", "[Worker: AI] Job complete.")
    log("INFO", "[Worker: Aggregator] Job complete.")
    log("INFO", f"[Worker Pipeline {pipeline_id}] Job complete. Processed {len(results_by_index)}/{total} answers.")

    return [results_by_index[i] for i in range(total)]

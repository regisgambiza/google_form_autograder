import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _safe_print_line(prefix: str, line: str) -> None:
    try:
        print(f"{prefix} {line}")
    except UnicodeEncodeError:
        sanitized = line.encode("cp1252", errors="replace").decode("cp1252", errors="replace")
        print(f"{prefix} {sanitized}")


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def update_config(base: Dict[str, Any], workers: int, jury: int) -> Dict[str, Any]:
    cfg = dict(base)
    cfg["max_parallel_workers"] = workers
    cfg["adaptive_max_workers"] = max(workers, int(cfg.get("adaptive_max_workers", workers)))
    cfg["max_concurrent_jury_answers"] = jury
    return cfg


def run_trial(command: str, gpu_index: int, interval: float, threshold: float, out_jsonl: Path) -> Tuple[int, Dict[str, Any]]:
    py = shutil.which("python") or "python"
    monitor_cmd = (
        f'"{py}" gpu_util_monitor.py --command "{command}" --gpu-index {gpu_index} '
        f'--interval {interval} --threshold {threshold} --out "{out_jsonl}"'
    )
    print(f"[TUNER] Launching monitor command:\n{monitor_cmd}")
    proc = subprocess.Popen(
        monitor_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        universal_newlines=True,
    )
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    print("[TUNER] ---- live monitor stdout ----")
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        stdout_lines.append(line)
        _safe_print_line("[MONITOR]", line)
    assert proc.stderr is not None
    for line in proc.stderr:
        line = line.rstrip("\n")
        stderr_lines.append(line)
        _safe_print_line("[MONITOR-ERR]", line)
    proc.wait()
    print(f"[TUNER] Trial command exited with code {proc.returncode}")
    # last JSON object printed by monitor
    summary = {}
    for line in stdout_lines:
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                summary = json.loads(line)
            except Exception:
                pass
    return proc.returncode, summary


def score(summary: Dict[str, Any]) -> float:
    if not summary:
        return -1e9
    # prioritize sustained high usage, then p90, then avg
    return (
        float(summary.get("time_above_threshold_pct", 0.0)) * 3.0
        + float(summary.get("p90_gpu_util", 0.0)) * 2.0
        + float(summary.get("avg_gpu_util", 0.0))
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Auto-tune config for higher sustained GPU utilization.")
    p.add_argument("--config", default="config.json")
    p.add_argument("--command", default="python main.py")
    p.add_argument("--gpu-index", type=int, default=0)
    p.add_argument("--interval", type=float, default=0.5)
    p.add_argument("--threshold", type=float, default=90.0)
    p.add_argument("--workers", default="6,8,10,12")
    p.add_argument("--jury", default="1,2")
    p.add_argument("--report", default="gpu_tune_report.json")
    args = p.parse_args()

    cfg_path = Path(args.config)
    base = load_json(cfg_path)
    backup_path = cfg_path.with_suffix(".json.bak_autotune")
    save_json(backup_path, base)

    worker_vals = [int(x.strip()) for x in args.workers.split(",") if x.strip()]
    jury_vals = [int(x.strip()) for x in args.jury.split(",") if x.strip()]
    print(f"[TUNER] Starting auto-tune")
    print(f"[TUNER] Config file: {cfg_path}")
    print(f"[TUNER] Worker candidates: {worker_vals}")
    print(f"[TUNER] Jury candidates: {jury_vals}")
    print(f"[TUNER] Threshold target: {args.threshold}%")

    trials: List[Dict[str, Any]] = []
    best_trial: Dict[str, Any] = {}
    best_score = -1e18

    try:
        for w in worker_vals:
            for j in jury_vals:
                print(f"[TUNER]============================================================")
                print(f"[TUNER] Running trial workers={w}, jury={j}")
                trial_cfg = update_config(base, w, j)
                save_json(cfg_path, trial_cfg)
                out_jsonl = Path(f"gpu_samples_w{w}_j{j}_{int(time.time())}.jsonl")
                rc, s = run_trial(args.command, args.gpu_index, args.interval, args.threshold, out_jsonl)
                sc = score(s)
                trial = {
                    "workers": w,
                    "jury": j,
                    "return_code": rc,
                    "summary": s,
                    "score": sc,
                    "samples_file": str(out_jsonl),
                }
                trials.append(trial)
                print("[TUNER] Trial summary:")
                print(json.dumps(trial, indent=2))
                if rc == 0 and sc > best_score:
                    best_score = sc
                    best_trial = trial
                    print(f"[TUNER] New best trial: workers={w}, jury={j}, score={sc:.2f}")
                else:
                    print(f"[TUNER] Best unchanged. Current best score={best_score:.2f}")
    finally:
        # restore base first; then apply best if present
        save_json(cfg_path, base)

    report = {"trials": trials, "best": best_trial}
    save_json(Path(args.report), report)

    if best_trial:
        best_cfg = update_config(base, int(best_trial["workers"]), int(best_trial["jury"]))
        save_json(cfg_path, best_cfg)
        print(f"[TUNER] Best config applied: workers={best_trial['workers']} jury={best_trial['jury']}")
        print(f"[TUNER] Full report: {args.report}")
        return 0

    print("[TUNER] No successful trial found. Original config restored.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

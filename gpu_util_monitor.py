import argparse
import json
import shutil
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class GPUSample:
    ts: float
    util: float
    mem_util: float
    mem_used_mb: float
    mem_total_mb: float


def _require_nvidia_smi() -> None:
    if shutil.which("nvidia-smi") is None:
        raise RuntimeError("nvidia-smi not found. Install NVIDIA driver tools first.")


def _parse_csv_line(line: str) -> Optional[GPUSample]:
    # util.gpu,utilization.memory,memory.used,memory.total
    try:
        parts = [p.strip().replace("%", "") for p in line.split(",")]
        if len(parts) < 4:
            return None
        util = float(parts[0])
        mem_util = float(parts[1])
        mem_used = float(parts[2])
        mem_total = float(parts[3])
        return GPUSample(time.time(), util, mem_util, mem_used, mem_total)
    except Exception:
        return None


def sample_once(gpu_index: int) -> Optional[GPUSample]:
    cmd = [
        "nvidia-smi",
        f"--id={gpu_index}",
        "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    return _parse_csv_line(line)


def monitor(gpu_index: int, interval: float, out_jsonl: Path, stop_event: threading.Event, samples: List[GPUSample]) -> None:
    last_status_print = 0.0
    while not stop_event.is_set():
        s = sample_once(gpu_index)
        if s:
            samples.append(s)
            out_jsonl.parent.mkdir(parents=True, exist_ok=True)
            with out_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(s.__dict__) + "\n")
            if time.time() - last_status_print >= 5.0:
                print(
                    f"[GPU-MON] samples={len(samples)} util={s.util:.1f}% mem_util={s.mem_util:.1f}% "
                    f"vram={s.mem_used_mb:.0f}/{s.mem_total_mb:.0f}MB",
                    flush=True,
                )
                last_status_print = time.time()
        time.sleep(interval)


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = int(round((pct / 100.0) * (len(vals) - 1)))
    return vals[idx]


def summary(samples: List[GPUSample], threshold: float) -> dict:
    utils = [s.util for s in samples]
    mem = [s.mem_util for s in samples]
    if not utils:
        return {"count": 0}
    above = sum(1 for u in utils if u >= threshold)
    return {
        "count": len(utils),
        "avg_gpu_util": round(statistics.mean(utils), 2),
        "p50_gpu_util": round(percentile(utils, 50), 2),
        "p90_gpu_util": round(percentile(utils, 90), 2),
        "p95_gpu_util": round(percentile(utils, 95), 2),
        "max_gpu_util": round(max(utils), 2),
        "avg_mem_util": round(statistics.mean(mem), 2),
        "time_above_threshold_pct": round((above / len(utils)) * 100.0, 2),
        "threshold": threshold,
    }


def run_workload(command: str) -> int:
    return subprocess.call(command, shell=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Monitor NVIDIA GPU utilization while running a workload.")
    p.add_argument("--command", required=True, help='Workload command, e.g. "python main.py"')
    p.add_argument("--gpu-index", type=int, default=0)
    p.add_argument("--interval", type=float, default=0.5)
    p.add_argument("--threshold", type=float, default=90.0)
    p.add_argument("--out", default="gpu_monitor_samples.jsonl")
    args = p.parse_args()

    _require_nvidia_smi()

    out_path = Path(args.out)
    stop = threading.Event()
    samples: List[GPUSample] = []
    t = threading.Thread(target=monitor, args=(args.gpu_index, args.interval, out_path, stop, samples), daemon=True)
    t.start()

    rc = 1
    try:
        rc = run_workload(args.command)
    finally:
        stop.set()
        t.join(timeout=2)

    report = summary(samples, args.threshold)
    print(json.dumps(report, indent=2))
    if report.get("count", 0) == 0:
        print("No GPU samples collected.")
        return 2

    if report["time_above_threshold_pct"] < 80.0:
        print("GPU is not sustained near target. Tune concurrency/model settings and rerun.")
    else:
        print("GPU utilization sustained near target.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

import os
import shutil
from typing import Any, Dict, List

import ollama

from logger import log


_DIAGNOSTICS_LOGGED = False
_POST_INFERENCE_LOGGED = False


def _safe_get_running_models() -> List[Dict[str, Any]]:
    try:
        resp = ollama.ps()
        if isinstance(resp, dict):
            return list(resp.get("models", []) or [])
    except Exception:
        pass
    return []


def log_ollama_gpu_diagnostics_once() -> None:
    global _DIAGNOSTICS_LOGGED
    if _DIAGNOSTICS_LOGGED:
        return
    _DIAGNOSTICS_LOGGED = True

    cuda_visible = os.getenv("CUDA_VISIBLE_DEVICES", "<unset>")
    nvidia_smi_present = shutil.which("nvidia-smi") is not None

    log("INFO", "[DIAGNOSTICS] Ollama acceleration probe")
    log("INFO", f"[DIAGNOSTICS]   CUDA_VISIBLE_DEVICES={cuda_visible}")
    log("INFO", f"[DIAGNOSTICS]   nvidia-smi available={nvidia_smi_present}")

    running = _safe_get_running_models()
    if not running:
        log("INFO", "[DIAGNOSTICS]   No running Ollama models yet (this is normal before first inference).")
        log("WARNING", "[DIAGNOSTICS]   If performance is CPU-like, verify Ollama is installed with GPU support and drivers are working.")
        return

    gpu_signals = 0
    for m in running:
        proc = str(m.get("processor", "")).lower()
        size_vram = m.get("size_vram", 0) or 0
        name = m.get("name", "<unknown>")
        if "gpu" in proc or (isinstance(size_vram, (int, float)) and size_vram > 0):
            gpu_signals += 1
        log("INFO", f"[DIAGNOSTICS]   model={name} processor={proc or 'unknown'} size_vram={size_vram}")

    if gpu_signals == 0:
        log("WARNING", "[DIAGNOSTICS]   No GPU usage indicators detected for running models. Inference may be CPU-bound.")
    else:
        log("INFO", f"[DIAGNOSTICS]   GPU indicators detected for {gpu_signals} running model(s).")


def log_post_inference_gpu_probe_once(source: str) -> None:
    """Log a one-time probe after first successful inference call."""
    global _POST_INFERENCE_LOGGED
    if _POST_INFERENCE_LOGGED:
        return
    _POST_INFERENCE_LOGGED = True

    running = _safe_get_running_models()
    if not running:
        log("WARNING", f"[DIAGNOSTICS] Post-inference probe ({source}): no running models visible from ollama.ps().")
        return

    gpu_signals = 0
    for m in running:
        proc = str(m.get("processor", "")).lower()
        size_vram = m.get("size_vram", 0) or 0
        if "gpu" in proc or (isinstance(size_vram, (int, float)) and size_vram > 0):
            gpu_signals += 1

    if gpu_signals > 0:
        log("INFO", f"[DIAGNOSTICS] Post-inference probe ({source}): GPU indicators detected for {gpu_signals} running model(s).")
    else:
        log("WARNING", f"[DIAGNOSTICS] Post-inference probe ({source}): still no GPU indicators; runtime likely CPU-bound.")

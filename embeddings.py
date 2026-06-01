import json
import math
import os
import time
import threading
from datetime import datetime, timezone
from typing import List

import requests

from evaluator_config import load_config, sha256_text
from logger import log
from ollama_diagnostics import log_post_inference_gpu_probe_once
from ollama_options import build_ollama_options


def _write_heartbeat_if_needed():
    """Write heartbeat to file for hang monitoring."""
    try:
        data = {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "stage": "embedding_generation",
            "timestamp_epoch": time.time(),
        }
        with open("heartbeat.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


PREFERRED_MODELS = ["mxbai-embed-large", "nomic-embed-text", "all-minilm"]
_EMBED_KEY_LOCKS = {}
_EMBED_KEY_LOCKS_GUARD = threading.Lock()
_EMBED_HTTP_SEM_LOCK = threading.Lock()
_EMBED_HTTP_SEM = None


def _get_key_lock(key: str) -> threading.Lock:
    with _EMBED_KEY_LOCKS_GUARD:
        lock = _EMBED_KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _EMBED_KEY_LOCKS[key] = lock
        return lock


def _ollama_base_url(cfg: dict) -> str:
    return str(cfg.get("ollama_api_base_url", "http://127.0.0.1:11434")).rstrip("/")


def _get_embed_http_semaphore():
    global _EMBED_HTTP_SEM
    if _EMBED_HTTP_SEM is not None:
        return _EMBED_HTTP_SEM
    with _EMBED_HTTP_SEM_LOCK:
        if _EMBED_HTTP_SEM is None:
            cfg = load_config()
            max_inflight = max(1, int(cfg.get("max_concurrent_embedding_http", 3)))
            _EMBED_HTTP_SEM = threading.Semaphore(max_inflight)
            log("INFO", f"[EMBED] HTTP concurrency limit enabled (max_concurrent_embedding_http={max_inflight})")
    return _EMBED_HTTP_SEM


def _extract_embedding(data: dict) -> List[float]:
    emb = data.get("embedding")
    if emb is None:
        embs = data.get("embeddings")
        if isinstance(embs, list) and embs:
            emb = embs[0]
    if not isinstance(emb, list):
        raise ValueError("Invalid embedding response from Ollama")
    return emb


def get_embedding(text: str, model: str) -> List[float]:
    # Write heartbeat before expensive operations
    _write_heartbeat_if_needed()
    cfg = load_config()
    embedding_options = build_ollama_options(ctx_key="embedding_num_ctx", default_ctx=1024)
    os.makedirs("cache/embeddings", exist_ok=True)
    key = sha256_text(model + text)
    path = os.path.join("cache/embeddings", f"{key}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)["embedding"]

    # Collapse concurrent misses for same key so one request populates cache.
    key_lock = _get_key_lock(key)
    with key_lock:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)["embedding"]

        start = time.perf_counter()
        log("DEBUG", f"START embedding_generate (model={model})")
        base_url = _ollama_base_url(cfg)
        read_timeout_s = max(10, int(cfg.get("embedding_timeout_seconds", 45)))
        connect_timeout_s = max(2, int(cfg.get("embedding_connect_timeout_seconds", 10)))

        payload = {
            "model": model,
            "prompt": text,
            "options": embedding_options,
            "keep_alive": cfg.get("ollama_options", {}).get("keep_alive", "30m"),
        }

        sem = _get_embed_http_semaphore()
        sem_wait_s = max(3, int(cfg.get("embedding_semaphore_wait_seconds", read_timeout_s)))
        if not sem.acquire(timeout=sem_wait_s):
            raise TimeoutError(f"embedding semaphore wait timeout ({sem_wait_s}s)")
        try:
            resp = requests.post(
                f"{base_url}/api/embeddings",
                json=payload,
                timeout=(connect_timeout_s, read_timeout_s),
            )
            if resp.status_code == 404:
                # Backward-compatible endpoint for older Ollama servers.
                resp = requests.post(
                    f"{base_url}/api/embed",
                    json={
                        "model": model,
                        "input": text,
                        "options": embedding_options,
                        "keep_alive": cfg.get("ollama_options", {}).get("keep_alive", "30m"),
                    },
                    timeout=(connect_timeout_s, read_timeout_s),
                )
        finally:
            try:
                sem.release()
            except Exception:
                pass
        resp.raise_for_status()
        emb = _extract_embedding(resp.json())

        duration_ms = (time.perf_counter() - start) * 1000
        log_post_inference_gpu_probe_once("embedding")
        log("DEBUG", f"END embedding_generate duration_ms={duration_ms:.0f} (model={model})")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"embedding": emb}, f)
        return emb


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def semantic_similarity(answer: str, expected: str) -> float:
    cfg = load_config()
    model = cfg.get("embedding_model")
    if model:
        try:
            return cosine_similarity(get_embedding(answer, model), get_embedding(expected, model))
        except Exception:
            if not bool(cfg.get("embedding_fallback_enabled", False)):
                return 0.0
    for fallback_model in PREFERRED_MODELS:
        try:
            return cosine_similarity(get_embedding(answer, fallback_model), get_embedding(expected, fallback_model))
        except Exception:
            continue
    return 0.0

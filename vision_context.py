import base64
import json
import os
from typing import Dict, Optional

import requests

from evaluator_config import load_config, sha256_text
from logger import log


VISION_PROMPT = (
    "You are helping grade a Google Form based on a textbook exercise image. "
    "Extract only information useful for grading learner answers: visible question text, "
    "exercise numbers, labels like 2a/2b, diagram or table facts, and relationships "
    "between questions. Build an explicit question_map for every visible question that can "
    "be answered in the Google Form. Keys should match Google Form labels by combining the "
    "exercise/question number and part letter, such as 1a, 1b, 2a, 2b, 3a, 4d, and values "
    "are the exact prompt/calculation for that part. For standalone numbered questions, "
    "use keys like 1, 7, 9. "
    "For example, if the page shows Exercise 2 question 2 part a as '-4 × 4', return "
    '"question_map": {"2a": "-4 × 4"}. Do not grade student answers. '
    "Return concise JSON with keys: summary, question_map, question_links, diagram_facts, "
    "visible_text, confidence."
)
VISION_CACHE_VERSION = "v2"


def _ollama_chat_url() -> str:
    return os.environ.get("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")


def _cache_path(image_hash: str) -> str:
    return os.path.join("cache", "vision", f"{image_hash}.json")


def _extract_json(content: str) -> Dict:
    if not content:
        return {}
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {"summary": content.strip(), "confidence": 0.5}


def _call_ollama_vision(model: str, image_b64: str, prompt: str, connect_timeout: int, timeout: int) -> Dict:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            }
        ],
        "format": "json",
    }
    resp = requests.post(_ollama_chat_url(), json=payload, timeout=(connect_timeout, timeout))
    resp.raise_for_status()
    data = resp.json()
    content = data.get("message", {}).get("content", "")
    return _extract_json(content)


def analyze_image_bytes(image_bytes: bytes, prompt: Optional[str] = None) -> Dict:
    """Analyze one image with primary/fallback Ollama vision models.

    This helper is intentionally independent from Google Forms extraction. The
    form context builder can call it once image bytes are available.
    """
    cfg = load_config()
    if not bool(cfg.get("enable_vision_context", False)):
        return {"vision_status": "disabled"}

    image_hash = sha256_text(VISION_CACHE_VERSION + "||" + base64.b64encode(image_bytes).decode("ascii"))
    path = _cache_path(image_hash)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    models = [
        str(cfg.get("vision_model", "qwen3-vl:8b")),
        str(cfg.get("vision_fallback_model", "minicpm-v4.5")),
    ]
    connect_timeout = max(2, int(cfg.get("vision_connect_timeout_seconds", 10)))
    timeout = max(5, int(cfg.get("vision_timeout_seconds", 90)))
    errors = []

    for model in [m for i, m in enumerate(models) if m and m not in models[:i]]:
        try:
            context = _call_ollama_vision(model, image_b64, prompt or VISION_PROMPT, connect_timeout, timeout)
            out = {
                "vision_status": "ok",
                "model_used": model,
                "image_hash": image_hash,
                "context": context,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=True)
            return out
        except Exception as ex:
            errors.append(f"{model}: {ex}")
            log("WARNING", f"[VISION] model failed ({model}): {ex}")

    out = {
        "vision_status": "failed",
        "image_hash": image_hash,
        "errors": errors,
    }
    if bool(cfg.get("vision_context_optional", True)):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=True)
        except Exception:
            pass
        return out
    raise RuntimeError("; ".join(errors) or "vision analysis failed")


def analyze_image_uri(uri: str, prompt: Optional[str] = None) -> Dict:
    cfg = load_config()
    connect_timeout = max(2, int(cfg.get("vision_connect_timeout_seconds", 10)))
    timeout = max(5, int(cfg.get("vision_download_timeout_seconds", 45)))
    try:
        resp = requests.get(uri, timeout=(connect_timeout, timeout))
        resp.raise_for_status()
        return analyze_image_bytes(resp.content, prompt=prompt)
    except Exception as ex:
        log("WARNING", f"[VISION] image download/analyze failed: {ex}")
        if bool(cfg.get("vision_context_optional", True)):
            return {"vision_status": "failed", "errors": [str(ex)]}
        raise

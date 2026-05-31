from typing import Dict, Optional

from evaluator_config import load_config


def build_ollama_options(
    *,
    ctx_key: str,
    default_ctx: int,
    predict_key: Optional[str] = None,
    default_predict: Optional[int] = None,
) -> Dict[str, object]:
    """Build shared Ollama runtime options with GPU-first defaults."""
    cfg = load_config()
    opts = cfg.get("ollama_options", {})

    out: Dict[str, object] = {
        "num_ctx": int(opts.get(ctx_key, default_ctx)),
        "num_gpu": int(opts.get("num_gpu", -1)),
    }

    if predict_key is not None and default_predict is not None:
        out["num_predict"] = int(opts.get(predict_key, default_predict))

    if "num_batch" in opts:
        out["num_batch"] = int(opts["num_batch"])
    if "num_thread" in opts:
        out["num_thread"] = int(opts["num_thread"])
    if "keep_alive" in opts:
        out["keep_alive"] = str(opts["keep_alive"])

    return out

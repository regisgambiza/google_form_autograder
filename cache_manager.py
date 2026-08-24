"""Safe cleanup for regenerated grading caches and recent-run history."""
import json
import shutil
from pathlib import Path
from typing import Dict


CACHE_GROUPS = ("embeddings", "form_context", "vision", "results")


def clear_grading_cache(
    root: Path = Path("."),
    reset_history: bool = True,
    preserve_grading_timestamps: bool = False,
) -> Dict[str, int]:
    root = root.resolve()
    cache_root = (root / "cache").resolve()
    removed_files = 0
    removed_bytes = 0
    for group in CACHE_GROUPS:
        target = (cache_root / group).resolve()
        if target.parent != cache_root:
            raise ValueError("Unsafe cache target")
        if target.exists():
            files = [path for path in target.rglob("*") if path.is_file()]
            removed_files += len(files)
            removed_bytes += sum(path.stat().st_size for path in files)
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

    history_removed = 0
    review_records_removed = 0

    def _remove_review_queue():
        nonlocal review_records_removed
        review_queue = (root / "answer_key_review_queue.json").resolve()
        if review_queue.parent == root and review_queue.exists():
            try:
                payload = json.loads(review_queue.read_text(encoding="utf-8"))
                review_records_removed = len(payload.get("items", []))
            except (OSError, ValueError, TypeError):
                review_records_removed = 1
            review_queue.unlink()

    if reset_history:
        # Explicit user reset (Settings UI): forget everything, including the
        # per-form "last graded" anchors that define RECENT_ONLY windows.
        history = (root / ".grading_timestamps.json").resolve()
        if history.parent == root and history.exists():
            history.unlink()
            history_removed = 1
        _remove_review_queue()
    elif preserve_grading_timestamps:
        # Automatic fresh-run cleanup: regenerated data + pending review queue
        # go away, but the RECENT_ONLY anchors MUST survive (wiping them would
        # silently degrade Recent Only into latest-batch-only grading).
        _remove_review_queue()

    # Clear process-local caches when this action is invoked in a process that
    # has imported the evaluator. New grader subprocesses start empty anyway.
    try:
        from evaluation_pipeline import RESULT_CACHE
        RESULT_CACHE.clear()
    except Exception:
        pass

    return {
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "history_removed": history_removed,
        "review_records_removed": review_records_removed,
    }


def prepare_fresh_grading_run(config: Dict, root: Path = Path(".")) -> Dict[str, int]:
    """Discard data from earlier runs when cache reuse is disabled.

    Deliberately PRESERVES ``.grading_timestamps.json``: that file is the
    per-form "last graded" anchor which defines the RECENT_ONLY selection
    window. Wiping it on every fresh run would silently turn Recent Only into
    an anchorless mode (latest-batch fallback). Explicit history resets remain
    available via ``clear_grading_cache(reset_history=True)`` (Settings UI).
    """
    if not bool(config.get("ignore_grading_cache", False)):
        return {"removed_files": 0, "removed_bytes": 0, "history_removed": 0, "review_records_removed": 0}
    return clear_grading_cache(root=root, reset_history=False, preserve_grading_timestamps=True)

"""Safe cleanup for regenerated grading caches and recent-run history."""
import shutil
from pathlib import Path
from typing import Dict


CACHE_GROUPS = ("embeddings", "expected_validation", "form_context", "rubrics", "vision", "results")


def clear_grading_cache(root: Path = Path("."), reset_history: bool = True) -> Dict[str, int]:
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
    if reset_history:
        history = (root / ".grading_timestamps.json").resolve()
        if history.parent == root and history.exists():
            history.unlink()
            history_removed = 1

    # Clear process-local caches when this action is invoked in a process that
    # has imported the evaluator. New grader subprocesses start empty anyway.
    try:
        from evaluation_pipeline import QUESTION_RUBRIC_CACHE, RESULT_CACHE
        RESULT_CACHE.clear()
        QUESTION_RUBRIC_CACHE.clear()
    except Exception:
        pass

    return {
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "history_removed": history_removed,
    }


def prepare_fresh_grading_run(config: Dict, root: Path = Path(".")) -> Dict[str, int]:
    """Discard data from earlier runs when cache reuse is disabled."""
    if not bool(config.get("ignore_grading_cache", False)):
        return {"removed_files": 0, "removed_bytes": 0, "history_removed": 0}
    return clear_grading_cache(root=root, reset_history=True)

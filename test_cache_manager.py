from cache_manager import CACHE_GROUPS, clear_grading_cache


def test_clear_grading_cache_removes_only_regenerated_data(tmp_path):
    for group in CACHE_GROUPS:
        directory = tmp_path / "cache" / group
        directory.mkdir(parents=True)
        (directory / "cached.json").write_text("cached", encoding="utf-8")
    (tmp_path / ".grading_timestamps.json").write_text("{}", encoding="utf-8")
    (tmp_path / "token.json").write_text("credential", encoding="utf-8")
    (tmp_path / "answer_key_review_queue.json").write_text("review", encoding="utf-8")

    result = clear_grading_cache(tmp_path, reset_history=True)

    assert result["removed_files"] == len(CACHE_GROUPS)
    assert result["history_removed"] == 1
    assert not (tmp_path / ".grading_timestamps.json").exists()
    assert (tmp_path / "token.json").read_text(encoding="utf-8") == "credential"
    assert (tmp_path / "answer_key_review_queue.json").read_text(encoding="utf-8") == "review"
    assert all((tmp_path / "cache" / group).is_dir() for group in CACHE_GROUPS)


def test_clear_cache_can_preserve_recent_grading_history(tmp_path):
    history = tmp_path / ".grading_timestamps.json"
    history.write_text("{}", encoding="utf-8")
    clear_grading_cache(tmp_path, reset_history=False)
    assert history.exists()

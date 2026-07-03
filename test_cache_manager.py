from cache_manager import CACHE_GROUPS, clear_grading_cache, prepare_fresh_grading_run


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
    assert not (tmp_path / "answer_key_review_queue.json").exists()
    assert result["review_records_removed"] == 1
    assert all((tmp_path / "cache" / group).is_dir() for group in CACHE_GROUPS)


def test_clear_cache_can_preserve_recent_grading_history(tmp_path):
    history = tmp_path / ".grading_timestamps.json"
    history.write_text("{}", encoding="utf-8")
    clear_grading_cache(tmp_path, reset_history=False)
    assert history.exists()


def test_fresh_run_removes_pending_answer_key_reviews(tmp_path):
    queue = tmp_path / "answer_key_review_queue.json"
    queue.write_text('{"items": [{"item_id": "1"}, {"item_id": "2"}]}', encoding="utf-8")

    result = prepare_fresh_grading_run({"ignore_grading_cache": True}, tmp_path)

    assert not queue.exists()
    assert result["review_records_removed"] == 2


def test_fresh_run_mode_automatically_discards_previous_run_cache(tmp_path):
    cached = tmp_path / "cache" / "results" / "old.json"
    cached.parent.mkdir(parents=True)
    cached.write_text("{}", encoding="utf-8")
    history = tmp_path / ".grading_timestamps.json"
    history.write_text("{}", encoding="utf-8")

    result = prepare_fresh_grading_run({"ignore_grading_cache": True}, tmp_path)

    assert result["removed_files"] == 1
    assert not cached.exists()
    assert not history.exists()


def test_cache_is_preserved_when_fresh_run_mode_is_disabled(tmp_path):
    cached = tmp_path / "cache" / "results" / "old.json"
    cached.parent.mkdir(parents=True)
    cached.write_text("{}", encoding="utf-8")

    prepare_fresh_grading_run({"ignore_grading_cache": False}, tmp_path)

    assert cached.exists()

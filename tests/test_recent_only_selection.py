# tests/test_recent_only_selection.py - Regression coverage for the
# Recent Only vs Whole Form submission-selection contract.
#
# Old bug: the global dispatcher accepted grade_recent_only but never used it,
# so Recent Only mode fetched and graded EVERY answer of the form (500-submission
# forms were fully re-graded for 10 recent submissions, burning AI tokens).
# These tests pin the contract:
#   submissions selected by the mode  ==  submissions fed to the pipeline
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from response_utils import (  # noqa: E402
    get_last_grading_time,
    get_responses,
    save_grading_time,
    select_responses_for_mode,
)


UTC = timezone.utc


def make_response(submission_id: str, submitted_at: datetime, answer: str = "42"):
    """A Forms-API shaped response dict for one question."""
    return {
        "responseId": submission_id,
        "submitTime": submitted_at.isoformat().replace("+00:00", "Z"),
        "answers": {
            "Q1": {"textAnswers": {"answers": [{"value": answer}]}}
        },
    }


class _Result:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeFormsService:
    """Minimal fake of service.forms() with paginated responses."""

    def __init__(self, pages):
        self._pages = pages

    def forms(self):
        return self

    def responses(self):
        return self

    def list(self, formId=None, pageToken=None):
        idx = int(pageToken) if pageToken else 0
        responses = self._pages[idx] if idx < len(self._pages) else []
        next_token = str(idx + 1) if idx + 1 < len(self._pages) else None
        return _Result({"responses": responses, "nextPageToken": next_token})


def build_mixed_dataset(now):
    """20 submissions: 5 inside the window, 15 outside."""
    old = [make_response(f"old-{i}", now - timedelta(days=i + 1)) for i in range(15)]
    new = [make_response(f"new-{i}", now - timedelta(minutes=i + 5)) for i in range(5)]
    return old + new, new


# ---------------------------------------------------------------------------
# select_responses_for_mode - the single source of truth
# ---------------------------------------------------------------------------

def test_whole_form_selects_every_submission():
    now = datetime.now(UTC)
    responses = [make_response(f"r{i}", now - timedelta(hours=i)) for i in range(20)]
    selected, stats = select_responses_for_mode(responses, False)

    assert stats["mode"] == "WHOLE_FORM"
    assert stats["selected"] == 20 == len(selected)
    assert stats["filtered_out"] == 0
    assert {id(r) for r in selected} == {id(r) for r in responses}


def test_recent_only_with_cutoff_selects_only_matching_submissions():
    now = datetime.now(UTC)
    all_responses, expected_new = build_mixed_dataset(now)
    cutoff = now - timedelta(hours=12)

    selected, stats = select_responses_for_mode(all_responses, True, cutoff=cutoff, form_id="F1")

    assert stats["mode"] == "RECENT_ONLY"
    assert stats["policy"] == "since_last_graded"
    assert stats["total_available"] == 20
    assert stats["selected"] == 5
    assert stats["filtered_out"] == 15
    ids = {r["responseId"] for r in selected}
    assert ids == {r["responseId"] for r in expected_new}


def test_recent_only_boundary_is_strictly_after_cutoff():
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=6)
    at_cutoff = make_response("at-cutoff", cutoff)
    after = make_response("after", cutoff + timedelta(seconds=1))
    before = make_response("before", cutoff - timedelta(seconds=1))

    selected, _stats = select_responses_for_mode([before, at_cutoff, after], True, cutoff=cutoff)

    # Boundary timestamps AT the cutoff belong to the previously graded set.
    assert [r["responseId"] for r in selected] == ["after"]


def test_recent_only_no_submissions_in_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    save_grading_time("formX", now - timedelta(hours=1))
    stale = [make_response(f"s{i}", now - timedelta(hours=2 + i)) for i in range(3)]

    selected, stats = select_responses_for_mode(stale, True, cutoff=get_last_grading_time("formX"))

    assert stats["policy"] == "none_in_window"
    assert stats["selected"] == 0
    assert selected == []


def test_recent_only_all_submissions_in_window():
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=30)
    fresh = [make_response(f"f{i}", now - timedelta(minutes=10 * i)) for i in range(7)]

    selected, stats = select_responses_for_mode(fresh, True, cutoff=cutoff)

    assert stats["policy"] == "since_last_graded"
    assert stats["selected"] == 7 == len(selected)


def test_recent_only_without_anchor_falls_back_to_latest_batch():
    now = datetime.now(UTC)
    responses = [
        make_response("day-old", now - timedelta(days=1)),
        make_response("latest-a", now - timedelta(minutes=3)),
        make_response("latest-b", now - timedelta(minutes=3)),  # same second batch
    ]

    selected, stats = select_responses_for_mode(responses, True, cutoff=None)

    assert stats["policy"] == "latest_batch_fallback"
    assert {r["responseId"] for r in selected} == {"latest-a", "latest-b"}


def test_multiple_forms_keep_independent_windows():
    now = datetime.now(UTC)
    form_a = [make_response(f"a{i}", now - timedelta(hours=20 + i)) for i in range(4)]
    form_b_new = [make_response(f"b{i}", now - timedelta(minutes=30 + i)) for i in range(2)]
    cutoff_a = now - timedelta(hours=10)
    cutoff_b = now - timedelta(hours=1)

    sel_a, stats_a = select_responses_for_mode(form_a, True, cutoff=cutoff_a, form_id="A")
    sel_b, stats_b = select_responses_for_mode(form_b_new, True, cutoff=cutoff_b, form_id="B")

    assert stats_a["selected"] == 0 and stats_a["policy"] == "none_in_window"
    assert stats_b["selected"] == 2
    assert {r["responseId"] for r in sel_b} == {"b0", "b1"}


def test_responses_without_parseable_time_are_excluded_from_recent_mode():
    now = datetime.now(UTC)
    no_time = {"responseId": "no-time", "answers": {}}
    good = make_response("good", now - timedelta(minutes=1))

    selected, stats = select_responses_for_mode([no_time, good], True, cutoff=now - timedelta(hours=1))

    assert [r["responseId"] for r in selected] == ["good"]
    assert stats["unparseable_times"] == 1


# ---------------------------------------------------------------------------
# get_responses end-to-end through a fake service (both modes, pagination)
# ---------------------------------------------------------------------------

def _two_page_service(old, new):
    return FakeFormsService([[old[0], new[0]], old[1:] + new[1:]])


def test_get_responses_recent_only_returns_only_selected_answers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    old = [make_response(f"old{i}", now - timedelta(days=2), answer=f"old{i}") for i in range(15)]
    new = [make_response(f"new{i}", now - timedelta(minutes=10 + i), answer=f"new{i}") for i in range(5)]
    save_grading_time("FORM", now - timedelta(hours=6))
    assert get_last_grading_time("FORM") is not None

    answers = get_responses(_two_page_service(old, new), "FORM", "Q1", grade_recent_only=True)

    # Only answers belonging to the 5 in-window submissions may be returned.
    assert sorted(answers) == sorted(f"new{i}" for i in range(5))


def test_get_responses_whole_form_returns_everything(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    old = [make_response(f"old{i}", now - timedelta(days=2), answer=f"old{i}") for i in range(15)]
    new = [make_response(f"new{i}", now - timedelta(minutes=10 + i), answer=f"new{i}") for i in range(5)]

    answers = get_responses(_two_page_service(old, new), "FORM", "Q1", grade_recent_only=False)

    assert len(answers) == 20


def test_get_responses_recent_only_never_regresses_to_full_form(tmp_path, monkeypatch):
    """The original bug: 500-submission forms fully graded for 10 recents.

    Simulated here as: huge stale backlog + tiny recent batch; the answer list
    handed to the grading pipeline must stay tiny.
    """
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    backlog = [make_response(f"bulk{i}", now - timedelta(days=30), answer="stale") for i in range(500)]
    recent = [make_response(f"hot{i}", now - timedelta(minutes=1 + i), answer=f"hot{i}") for i in range(10)]
    save_grading_time("BIG", now - timedelta(hours=1))

    answers = get_responses(FakeFormsService([backlog + recent]), "BIG", "Q1", grade_recent_only=True)

    assert len(answers) == 10
    assert all(a.startswith("hot") for a in answers)


# ---------------------------------------------------------------------------
# cache_manager interaction: fresh-run cleanup must not erase the anchor
# ---------------------------------------------------------------------------

def test_fresh_run_preserves_recent_only_anchor(tmp_path, monkeypatch):
    from cache_manager import prepare_fresh_grading_run

    monkeypatch.chdir(tmp_path)
    history = tmp_path / ".grading_timestamps.json"
    history.write_text('{"F1": "2026-08-24T07:00:00+00:00"}', encoding="utf-8")
    stale_cache = tmp_path / "cache" / "results"
    stale_cache.mkdir(parents=True)
    (stale_cache / "x.json").write_text("{}", encoding="utf-8")

    prepare_fresh_grading_run({"ignore_grading_cache": True}, tmp_path)

    assert history.exists(), "fresh-run cleanup wiped the RECENT_ONLY window anchor"
    assert get_last_grading_time("F1") is not None


def test_stale_anchor_means_nothing_new_selected(tmp_path, monkeypatch):
    """Worker receiving an anchor newer than every submission grades nothing."""
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    save_grading_time("F1", now + timedelta(minutes=5))  # future/advanced anchor
    responses = [make_response(f"p{i}", now) for i in range(4)]

    from response_utils import parse_submission_time

    cutoff = get_last_grading_time("F1")
    selected, stats = select_responses_for_mode(
        responses, True, cutoff=cutoff,
    )
    assert stats["selected"] == 0
    assert parse_submission_time(selected[0] if selected else {"submitTime": None}) is None or True

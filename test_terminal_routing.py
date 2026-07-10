import json

from grader_thread import GraderThread
from logger import gui_event


def test_gui_answer_event_renders_teacher_facing_narrative():
    rendered = GraderThread._format_gui_event({
        "type": "answer_result",
        "current": 3,
        "total": 10,
        "question_number": 2,
        "question": "Find the diameter",
        "expected": "50.9 cm",
        "answer": "50,9 cm",
        "formatting": {"proven": True, "reason": "decimal comma normalized", "details": ["Unit cm matches"]},
        "judges": [
            {"provider": "openrouter", "model": "mistral-nemo:12b", "decision": "YES", "confidence": 0.99, "reason": "same value"},
            {"model": "gemma3:12b", "decision": "YES", "confidence": 0.98, "reason": "verified"},
        ],
        "decision": "YES",
        "action": "Answer accepted",
        "accepted": 2,
        "review": 0,
        "rejected": 1,
        "elapsed": "00:01:20",
    })

    assert "Student answer: 50,9 cm" in rendered
    assert "AI evaluation:" in rendered
    assert "openrouter / mistral-nemo:12b" in rendered
    assert "Final decision: ✓ ACCEPTED" in rendered
    assert "q_ai" not in rendered
    assert "heartbeat" not in rendered.casefold()


def test_gui_event_is_machine_readable(capsys):
    gui_event("run_start", form_title="Quiz", total=12)
    line = capsys.readouterr().out.strip()
    assert line.startswith("GUI_EVENT:")
    event = json.loads(line.split(":", 1)[1])
    assert event["type"] == "run_start"
    assert event["form_title"] == "Quiz"
    assert event["total"] == 12
    assert event["timestamp"]
    assert "run_id" in event


def test_gui_terminal_events_are_persisted_as_text_and_jsonl(tmp_path):
    worker = GraderThread()
    text_path = tmp_path / "gui.log"
    jsonl_path = tmp_path / "gui.jsonl"
    worker._gui_log_fh = text_path.open("w", encoding="utf-8")
    worker._gui_jsonl_fh = jsonl_path.open("w", encoding="utf-8")
    event = {"type": "run_complete", "timestamp": "2026-07-03T12:00:00Z", "accepted": 8, "review": 1, "rejected": 2, "elapsed": "00:10:00"}
    rendered = worker._format_gui_event(event)

    worker._write_gui_terminal_event(event, rendered)
    worker._close_gui_terminal_logs()

    assert "Grading finished" in text_path.read_text(encoding="utf-8")
    assert "<b>" not in text_path.read_text(encoding="utf-8")
    assert json.loads(jsonl_path.read_text(encoding="utf-8"))["accepted"] == 8


def test_external_console_keeps_compact_heartbeat_and_detailed_task_logs():
    source = __import__("pathlib").Path("global_dispatcher.py").read_text(encoding="utf-8")
    assert "[HEARTBEAT]" in source
    assert "[TASK START]" in source
    assert "[TASK END]" in source
    assert "external_heartbeat_interval_seconds" in source

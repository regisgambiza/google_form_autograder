import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from evaluator_config import load_config, sha256_text
from logger import log, stage_banner


TEXTBOOK_MARKERS = (
    "textbook context",
    "exercise context",
    "use this image",
    "use for questions",
    "source material",
)


@dataclass
class SectionContext:
    title: str = ""
    description: str = ""
    item_indexes: List[int] = field(default_factory=list)
    image_notes: List[str] = field(default_factory=list)
    vision_notes: List[str] = field(default_factory=list)
    question_map: Dict[str, str] = field(default_factory=dict)
    text_notes: List[str] = field(default_factory=list)


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split())


def _is_enabled() -> bool:
    return bool(load_config().get("enable_form_context", True))


def _write_heartbeat(stage: str) -> None:
    try:
        with open("heartbeat.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "last_update": datetime.now(timezone.utc).isoformat(),
                    "pid": os.getpid(),
                    "stage": stage,
                },
                f,
                indent=2,
            )
    except Exception:
        pass


def _item_text(item: Dict) -> str:
    bits = [_clean(item.get("title")), _clean(item.get("description"))]
    return "\n".join(b for b in bits if b)


def _has_textbook_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in TEXTBOOK_MARKERS)


def _extract_image_data(item: Dict, index: int) -> Optional[Dict[str, str]]:
    image_item = item.get("imageItem") or {}
    image = image_item.get("image") or item.get("questionItem", {}).get("image")
    if not image:
        return None

    alt_text = _clean(image.get("altText") or image.get("alt_text"))
    title = _clean(item.get("title"))
    description = _clean(item.get("description"))
    source_uri = _clean(image.get("sourceUri") or image.get("contentUri") or image.get("imageUri"))
    parts = [f"Image item #{index + 1}"]
    if title:
        parts.append(f"title: {title}")
    if description:
        parts.append(f"description: {description}")
    if alt_text:
        parts.append(f"alt text: {alt_text}")
    if source_uri:
        parts.append(f"uri: {source_uri}")
    return {"note": "; ".join(parts), "uri": source_uri, "title": title, "description": description}


def _format_vision_note(image_data: Dict[str, str], vision: Dict) -> Optional[str]:
    if vision.get("vision_status") != "ok":
        return None
    context = vision.get("context", {})
    if not isinstance(context, dict):
        context = {"summary": str(context)}
    bits = []
    title = image_data.get("title")
    if title:
        bits.append(f"Image title: {title}")
    summary = _clean(context.get("summary"))
    if summary:
        bits.append(f"Summary: {summary}")
    visible_text = context.get("visible_text")
    if isinstance(visible_text, list):
        visible_text = "; ".join(str(x) for x in visible_text)
    visible_text = _clean(visible_text)
    if visible_text:
        bits.append(f"Visible text: {visible_text}")
    question_links = context.get("question_links")
    if question_links:
        bits.append(f"Question links: {json.dumps(question_links, ensure_ascii=True)}")
    question_map = context.get("question_map")
    if question_map:
        bits.append(f"Question map: {json.dumps(question_map, ensure_ascii=True)}")
    diagram_facts = context.get("diagram_facts")
    if diagram_facts:
        bits.append(f"Diagram/table facts: {json.dumps(diagram_facts, ensure_ascii=True)}")
    model_used = vision.get("model_used")
    if model_used:
        bits.append(f"Vision model: {model_used}")
    return " | ".join(bits) if bits else None


def _normalize_question_label(value: object) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _extract_question_map(vision: Dict) -> Dict[str, str]:
    if vision.get("vision_status") != "ok":
        return {}
    context = vision.get("context", {})
    if not isinstance(context, dict):
        return {}
    raw = context.get("question_map") or {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        norm = _normalize_question_label(key)
        text = _clean(value)
        if norm and text:
            out[norm] = text
    return out


def _section_label(section: SectionContext) -> str:
    if section.title and section.description:
        return f"{section.title} - {section.description}"
    return section.title or section.description or "Form start"


def _compose_question_context(
    form_title: str,
    section: SectionContext,
    q: Dict,
    nearby: List[Dict],
    expected: List[str],
) -> str:
    mapped_question = section.question_map.get(_normalize_question_label(q.get("title")))
    lines = [
        f"Form title: {_clean(form_title)}",
        f"Section context: {_section_label(section)}",
    ]

    if section.text_notes:
        lines.append("Shared text/context in this section:")
        lines.extend(f"- {note}" for note in section.text_notes[:5])

    if section.image_notes:
        lines.append("Shared image context available in this section:")
        lines.extend(f"- {note}" for note in section.image_notes[:5])

    if section.vision_notes:
        lines.append("Vision-extracted textbook/image context for this section:")
        lines.extend(f"- {note}" for note in section.vision_notes[:5])

    lines.extend(
        [
            f"Current question label/title: {_clean(q.get('title')) or 'Untitled'}",
            f"Mapped textbook question: {mapped_question or 'Not found'}",
            f"Current question description: {_clean(q.get('description')) or 'Not provided'}",
            f"Current question type: {_clean(q.get('type')) or 'Unknown'}",
        ]
    )

    if nearby:
        lines.append("Nearby questions in order:")
        for nq in nearby:
            title = _clean(nq.get("title")) or "Untitled"
            desc = _clean(nq.get("description"))
            if desc:
                lines.append(f"- Q{nq.get('index')}: {title} - {desc}")
            else:
                lines.append(f"- Q{nq.get('index')}: {title}")

    if expected:
        lines.append("Existing expected/correct answer(s):")
        lines.extend(f"- {_clean(ans)}" for ans in expected if _clean(ans))

    return "\n".join(line for line in lines if line)


def _cache_path(form_id: str) -> str:
    return os.path.join("cache", "form_context", f"{form_id}.json")


def build_form_context(
    form_id: str,
    form_title: str,
    form_data: Dict,
    structure: List[Dict],
    expected_by_item_id: Optional[Dict[str, List[str]]] = None,
) -> Dict:
    """Build text-first, section-aware context for every gradable question.

    Images are intentionally optional at this stage. When Google Forms exposes
    image metadata, it is recorded as context; later vision extraction can
    replace these notes with model-generated descriptions.
    """
    if not _is_enabled():
        return {"enabled": False, "questions": {}}

    _write_heartbeat("form_context")
    stage_banner("Building Form Context", f"{form_title} ({len(structure)} questions)", color="magenta")
    expected_by_item_id = expected_by_item_id or {}
    os.makedirs(os.path.dirname(_cache_path(form_id)), exist_ok=True)
    items = form_data.get("items", []) if isinstance(form_data, dict) else []
    q_by_item = {q.get("itemId"): q for q in structure}
    qids_in_order = [q.get("questionId") for q in structure]
    q_index_by_id = {qid: i for i, qid in enumerate(qids_in_order) if qid}

    sections: Dict[str, SectionContext] = {}
    question_section: Dict[str, str] = {}
    current_key = "section-0"
    sections[current_key] = SectionContext(title=_clean(form_title))

    for idx, item in enumerate(items):
        text = _item_text(item)
        if "pageBreakItem" in item:
            current_key = f"section-{idx}"
            sections[current_key] = SectionContext(
                title=_clean(item.get("title")),
                description=_clean(item.get("description")),
            )
            continue

        section = sections[current_key]
        section.item_indexes.append(idx)
        image_data = _extract_image_data(item, idx)
        if image_data:
            section.image_notes.append(image_data["note"])
            if bool(load_config().get("enable_vision_context", False)) and image_data.get("uri"):
                try:
                    from vision_context import analyze_image_uri

                    stage_banner(
                        "Extracting Textbook Image",
                        image_data.get("title") or f"image item #{idx + 1}",
                        color="yellow",
                    )
                    _write_heartbeat("vision_context")
                    vision = analyze_image_uri(image_data["uri"])
                    _write_heartbeat("vision_context_done")
                    vision_note = _format_vision_note(image_data, vision)
                    if vision_note:
                        section.vision_notes.append(vision_note)
                    section.question_map.update(_extract_question_map(vision))
                except Exception as ex:
                    log("WARNING", f"[FORM CONTEXT] vision context skipped for item #{idx + 1}: {ex}")
        if text and ("textItem" in item or _has_textbook_marker(text)):
            section.text_notes.append(text)

        q = q_by_item.get(item.get("itemId"))
        if q and q.get("questionId"):
            question_section[q["questionId"]] = current_key

    questions: Dict[str, Dict] = {}
    for q in structure:
        _write_heartbeat("form_context_questions")
        qid = q.get("questionId")
        if not qid:
            continue
        section = sections.get(question_section.get(qid, "section-0"), sections["section-0"])
        pos = q_index_by_id.get(qid, 0)
        nearby = structure[max(0, pos - 2):pos] + structure[pos + 1:pos + 3]
        expected = expected_by_item_id.get(q.get("itemId"), [])
        teacher_expected = expected[:1]
        enriched = _compose_question_context(form_title, section, q, nearby, teacher_expected)
        # Default effective_expected preserves all provided variants; validation may replace it.
        effective_expected = list(expected)
        expected_was_replaced = False
        questions[qid] = {
            "question_id": qid,
            "item_id": q.get("itemId"),
            "section": {
                "title": section.title,
                "description": section.description,
            },
            "enriched_context": enriched,
            "context_hash": sha256_text(enriched),
            "has_image_context": bool(section.image_notes),
            "has_vision_context": bool(section.vision_notes),
            "vision_status": "ok" if section.vision_notes else "not_found",
            "mapped_textbook_question": section.question_map.get(_normalize_question_label(q.get("title")), ""),
            "teacher_answer": teacher_expected,
            "effective_expected": effective_expected,
            "expected_was_replaced_for_grading": expected_was_replaced,
        }
        # Optionally validate the first teacher answer using the expected-answer validator
        try:
            cfg = load_config()
            if bool(cfg.get("validate_expected_answers", False)):
                try:
                    import expected_answer_validator as _eav

                    validation = _eav.validate_expected_answer(enriched, teacher_expected)
                    questions[qid]["expected_validation"] = validation
                    # If configured to use validated expected for grading, apply suggested answers when validator recommends replacement
                    if bool(cfg.get("use_validated_expected_for_grading", False)) and validation and not validation.get("valid", True):
                        conf = float(validation.get("confidence", 0.0) or 0.0)
                        min_conf = float(cfg.get("expected_answer_validator_min_confidence", 0.85))
                        if conf >= min_conf and validation.get("suggested_answers"):
                            questions[qid]["effective_expected"] = list(validation.get("suggested_answers"))
                            questions[qid]["expected_was_replaced_for_grading"] = True
                except Exception:
                    # Non-fatal: skip validation if validator is unavailable
                    pass
        except Exception:
            pass

    out = {
        "enabled": True,
        "form_id": form_id,
        "form_title": form_title,
        "questions": questions,
    }
    try:
        with open(_cache_path(form_id), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=True)
    except Exception as ex:
        log("WARNING", f"[FORM CONTEXT] could not write context cache for {form_id}: {ex}")
    log("INFO", f"[FORM CONTEXT] built context for form_id={form_id} questions={len(questions)}")
    return out


def apply_question_context(structure: List[Dict], context: Dict) -> List[Dict]:
    questions = context.get("questions", {}) if isinstance(context, dict) else {}
    enriched_structure = []
    for q in structure:
        q2 = dict(q)
        qctx = questions.get(q.get("questionId"), {})
        if qctx.get("enriched_context"):
            q2["enriched_context"] = qctx["enriched_context"]
            q2["context_hash"] = qctx.get("context_hash")
            q2["has_image_context"] = bool(qctx.get("has_image_context"))
            q2["has_vision_context"] = bool(qctx.get("has_vision_context"))
            q2["mapped_textbook_question"] = qctx.get("mapped_textbook_question", "")
            q2["teacher_answer"] = qctx.get("teacher_answer", [])[:1]
            q2["effective_expected"] = qctx.get("effective_expected", [])
            q2["expected_was_replaced_for_grading"] = qctx.get("expected_was_replaced_for_grading", False)
        enriched_structure.append(q2)
    return enriched_structure


def get_question_context(question: Dict) -> str:
    return str(question.get("enriched_context") or question.get("title") or "Untitled Question")


def get_effective_expected(question: Dict, fallback_expected: Optional[List[str]] = None) -> List[str]:
    """Return only the first teacher-supplied answer as grading truth.

    Later Google Form answers are accepted variants, never canonical evidence.
    """
    values = [str(x) for x in (fallback_expected or []) if _clean(x)]
    return values[:1]


def should_block_answer_updates(question: Dict) -> bool:
    """Return True when answer updates should be blocked due to a validated replacement."""
    if not isinstance(question, dict):
        return False
    return bool(question.get("expected_was_replaced_for_grading", False))


"""ProvidersPage worker-section grouping tests (offscreen Qt).

Verifies that worker chips land in per-provider sections in fixed order,
stay numerically sorted inside a section after removals, and that empty
sections are hidden.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui_studio.pages import ProvidersPage  # noqa: E402


_app = QApplication.instance() or QApplication([])


def _chip_ids_in_grid(page, grid):
    ids = []
    for index in range(grid.count()):
        widget = grid.itemAt(index).widget()
        for wid, chip in page._worker_chips.items():
            if chip is widget:
                ids.append(wid)
                break
    return ids


def test_group_for_parses_all_worker_families():
    assert ProvidersPage._group_for("openrouter-3") == "openrouter"
    assert ProvidersPage._group_for("llamacpp-1") == "llamacpp"
    assert ProvidersPage._group_for("ollama-1") == "ollama"
    assert ProvidersPage._group_for("ai-openrouter-2") == "app_openrouter"
    assert ProvidersPage._group_for("ai-llamacpp-1") == "app_llamacpp"
    assert ProvidersPage._group_for("ai-4") == "app_generic"
    assert ProvidersPage._group_for("mystery-9") == "other"


def test_mixed_inserts_land_in_ordered_sections():
    page = ProvidersPage()
    page.show()
    mixed = [
        "ai-openrouter-2",
        "openrouter-10",
        "ai-1",
        "llamacpp-3",
        "ai-openrouter-1",
        "ollama-1",
        "weird-thing",
    ]
    for wid in mixed:
        page.add_worker_chip(wid, wid)

    expected_order = [
        ("openrouter", ["openrouter-10"]),
        ("llamacpp", ["llamacpp-3"]),
        ("ollama", ["ollama-1"]),
        ("app_openrouter", ["ai-openrouter-1", "ai-openrouter-2"]),
        ("app_generic", ["ai-1"]),
        ("other", ["weird-thing"]),
    ]
    visible_groups = [
        group for group, _ in ProvidersPage.WORKER_GROUP_ORDER
        if group in page._sections and page._sections[group]["grid"].count()
    ]
    assert visible_groups == [g for g, _ in expected_order]

    for group, members in expected_order:
        section = page._sections[group]
        assert not section["host"].isHidden(), f"{group} should be visible"
        ids = _chip_ids_in_grid(page, section["grid"])
        assert sorted(ids) == sorted(members), f"{group}: {ids} != {members}"
        caption = section["caption"].text()
        assert str(len(members)) in caption, f"caption missing count: {caption}"

    # app_llamacpp was never created and must stay absent.
    assert "app_llamacpp" not in page._sections
    page.hide()


def test_removal_compacts_and_keeps_numeric_sort_within_section():
    page = ProvidersPage()
    page.show()
    for wid in ("openrouter-1", "openrouter-2", "openrouter-10", "ai-1"):
        page.add_worker_chip(wid, wid)

    page.remove_worker_chip("openrouter-2")
    ids = _chip_ids_in_grid(page, page._sections["openrouter"]["grid"])
    assert ids == ["openrouter-1", "openrouter-10"], f"order after removal: {ids}"

    # Draining a section hides it entirely.
    page.remove_worker_chip("openrouter-1")
    page.remove_worker_chip("openrouter-10")
    assert "openrouter" in page._sections
    assert page._sections["openrouter"]["host"].isHidden()
    assert page._sections["openrouter"]["grid"].count() == 0
    page.hide()

"""Every tab strip in the app renders through tau.tui.widgets.tabs.Tabs.

The selectors used to hand-roll their own, marking the active tab by wrapping
its label in brackets. They now share the widget, so selection is by style and
the tabs are separated by the widget's divider.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.modes.interactive.components.session_selector import ResumeSelector
from tau.modes.interactive.components.settings_selector import SettingItem, SettingsSelector
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect

WIDTH = 70


def _strip(component) -> str:
    """The first rendered row — the tab strip."""
    buf = Buffer.empty(Rect(0, 0, WIDTH, 40))
    component.render_cells(Rect(0, 0, WIDTH, 40), buf)
    return "".join(buf.get(x, 0).symbol for x in range(WIDTH)).rstrip()


def _styles(component) -> list:
    buf = Buffer.empty(Rect(0, 0, WIDTH, 40))
    component.render_cells(Rect(0, 0, WIDTH, 40), buf)
    return [buf.get(x, 0) for x in range(WIDTH)]


@pytest.fixture
def resume():
    now = datetime.now()
    sessions = [
        SimpleNamespace(
            id=f"sess{i:04d}",
            path=Path(f"/tmp/s{i}.jsonl"),
            name=None,
            modified=now - timedelta(hours=i),
            cwd=f"/w/p{i}",
            message_count=i,
        )
        for i in range(3)
    ]
    return ResumeSelector(
        current_sessions=sessions, all_sessions_loader=lambda: sessions, max_visible=2
    )


@pytest.fixture
def settings():
    tabs = [
        ("General", [SettingItem(id="a", label="Theme", current_value="dark")]),
        ("Tools", [SettingItem(id="b", label="Timeout", current_value="30s")]),
    ]
    return SettingsSelector(items=[], on_change=lambda *a: None, tabs=tabs)


class TestResumeScopeStrip:
    def test_uses_the_widget_divider_not_brackets(self, resume):
        strip = _strip(resume)

        assert "│" in strip
        assert "[Folder]" not in strip and "[All]" not in strip
        assert "Folder" in strip and "All" in strip

    def test_the_sort_mode_sits_beside_the_tabs(self, resume):
        strip = _strip(resume)

        assert "Sort: Recent" in strip
        assert strip.index("Folder") < strip.index("Sort:")

    def test_toggling_scope_moves_the_emphasis(self, resume):
        before = _styles(resume)
        folder_x = _strip(resume).index("Folder")
        all_x = _strip(resume).index("All")

        resume.toggle_scope()
        after = _styles(resume)

        assert before[folder_x].style != after[folder_x].style
        assert before[all_x].style != after[all_x].style


class TestSettingsTabStrip:
    def test_uses_the_widget_divider_not_brackets(self, settings):
        strip = _strip(settings)

        assert "│" in strip
        assert "[General]" not in strip
        assert "General" in strip and "Tools" in strip

    def test_cycling_moves_the_emphasis(self, settings):
        general_x = _strip(settings).index("General")
        before = _styles(settings)[general_x].style

        settings.next_tab()

        assert _styles(settings)[general_x].style != before

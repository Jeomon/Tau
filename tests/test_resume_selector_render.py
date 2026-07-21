"""Render tests for the /resume session selector.

The component had no coverage while it faked multi-row entries with three
``ListItem``s each and a ``* 3`` index mapping. It now emits one tall
``ListItem`` per session, so these pin the layout that mapping produced.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.modes.interactive.components.session_selector import ResumeSelector
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect

WIDTH = 72


def _sessions(count: int = 6):
    now = datetime.now()
    return [
        SimpleNamespace(
            id=f"sess{i:04d}aaaa",
            path=Path(f"/tmp/s{i}.jsonl"),
            name=f"named session {i}" if i % 2 == 0 else None,
            modified=now - timedelta(hours=i),
            cwd=f"/work/project{i}",
            message_count=i * 3,
        )
        for i in range(count)
    ]


@pytest.fixture
def selector():
    sessions = _sessions()
    return ResumeSelector(
        current_sessions=sessions,
        all_sessions_loader=lambda: sessions,
        current_session_path=Path("/tmp/s1.jsonl"),
        max_visible=4,
    )


def _rows(selector) -> list[str]:
    buf = Buffer.empty(Rect(0, 0, WIDTH, 40))
    written = selector.render_cells(Rect(0, 0, WIDTH, 40), buf)
    return [
        "".join(buf.get(x, y).symbol for x in range(WIDTH)).rstrip() for y in range(written)
    ]


class TestLayout:
    def test_each_session_shows_a_name_row_then_a_meta_row(self, selector):
        rows = _rows(selector)
        name_row = next(i for i, r in enumerate(rows) if "named session 0" in r)

        assert "ago" in rows[name_row + 1] or "just now" in rows[name_row + 1]

    def test_unnamed_sessions_fall_back_to_an_id_prefix(self, selector):
        assert any("sess0003aaaa" in r for r in _rows(selector))

    def test_entries_are_separated_by_a_blank_row(self, selector):
        rows = _rows(selector)
        first = next(i for i, r in enumerate(rows) if "named session 0" in r)

        assert rows[first + 2] == ""  # name, meta, blank
        assert "named session 2" in rows[first + 3]

    def test_no_trailing_blank_after_the_last_visible_entry(self, selector):
        rows = _rows(selector)
        last = max(i for i, r in enumerate(rows) if "named session 4" in r)

        # meta row follows, then the divider — not a blank separator.
        assert rows[last + 1].strip()
        assert "─" in rows[last + 2]

    def test_message_counts_appear_in_the_meta_row(self, selector):
        assert any("⚙ 6" in r for r in _rows(selector))


class TestSelection:
    def test_the_arrow_starts_on_the_first_session(self, selector):
        rows = _rows(selector)
        assert "❯" in next(r for r in rows if "named session 0" in r)

    def test_moving_down_moves_the_arrow_one_session_not_one_row(self, selector):
        selector.move_down()
        rows = _rows(selector)

        # The old flat-row model needed a ×3 mapping here; one step must land on
        # the next *session*, not on its meta row.
        assert "❯" in next(r for r in rows if "named session 2" in r)
        assert "❯" not in next(r for r in rows if "named session 0" in r)

    def test_selection_survives_scrolling_the_window(self, selector):
        for _ in range(5):
            selector.move_down()
        rows = _rows(selector)

        assert any("more above" in r for r in rows)
        assert "❯" in next(r for r in rows if "sess0005aaaa" in r)

    def test_selected_path_tracks_the_highlighted_session(self, selector):
        assert selector.selected_path() == Path("/tmp/s0.jsonl")
        selector.move_down()
        # s1 is the *active* session and is filtered out — you cannot resume
        # the session you are already in.
        assert selector.selected_path() == Path("/tmp/s2.jsonl")

    def test_the_active_session_is_not_offered(self, selector):
        rows = _rows(selector)
        assert not any("named session 1" in r or "sess0001aaaa" in r for r in rows)


class TestEmptyState:
    def test_no_sessions_renders_a_message_not_a_list(self):
        selector = ResumeSelector(current_sessions=[], all_sessions_loader=list)
        rows = _rows(selector)

        assert any("No sessions" in r for r in rows)

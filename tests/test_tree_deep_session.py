"""`/tree` must open on a long conversation.

Both traversals in `open_tree_selector` recursed once per node. A linear
conversation is a chain one node deep per entry, so a session past roughly a
thousand entries produced

    /tree     └ error: maximum recursion depth exceeded

and the command was unusable on exactly the sessions it is most useful for. A
real session reached a chain depth of 2390 against Python's default limit of
1000.

`SessionManager.get_tree` and `_contains_active` were already written
iteratively for this reason; the two that were not are now as well.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from tau.message.types import AssistantMessage, TextContent, UserMessage
from tau.modes.interactive.commands.session import open_tree_selector
from tau.session.manager import SessionManager
from tau.session.types import LeafEntry

#: Comfortably past the default recursion limit, so the test fails on the old
#: code rather than merely being slow.
_DEPTH = 1400


def _linear_session(tmp_path: Path, turns: int) -> SessionManager:
    """A conversation with no branches: the deepest tree per entry count."""
    sm = SessionManager(cwd=tmp_path, session_dir=tmp_path, persist=False)
    for i in range(turns):
        sm.append_message(UserMessage(contents=[TextContent(content=f"q{i}")]))
        sm.append_message(AssistantMessage(contents=[TextContent(content=f"a{i}")]))
    return sm


def _displayable_ids(sm: SessionManager) -> set[str]:
    """Entry ids that produce a row.

    `branch()` records a `LeafEntry` marking where the new branch starts. It
    carries no role to render, so it has never appeared in the tree — which
    makes "one row per entry" true only for an unbranched session.
    """
    return {e.id for e in sm.get_entries() if not isinstance(e, LeafEntry)}


class _Layout:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    def open_branch_tree_selector(self, rows: list, _commit: Any, _cancel: Any) -> None:
        self.rows = rows


class _Ctx:
    def __init__(self, sm: SessionManager) -> None:
        self.runtime = type("R", (), {"session_manager": sm})()
        self.layout = _Layout()
        self.notices: list[str] = []

    def notify(self, message: str = "", *_a: Any, **_k: Any) -> None:
        self.notices.append(message)


def test_tree_opens_on_a_session_deeper_than_the_recursion_limit(tmp_path: Path) -> None:
    sm = _linear_session(tmp_path, _DEPTH)
    assert len(sm.get_entries()) > sys.getrecursionlimit()
    ctx = _Ctx(sm)

    open_tree_selector(ctx)  # type: ignore[arg-type]

    assert len(ctx.layout.rows) == len(sm.get_entries())
    assert ctx.notices == []


def test_the_current_leaf_is_marked(tmp_path: Path) -> None:
    """Depth must not cost the active-path marking the rows are read by."""
    sm = _linear_session(tmp_path, _DEPTH)
    ctx = _Ctx(sm)

    open_tree_selector(ctx)  # type: ignore[arg-type]

    assert sum(1 for row in ctx.layout.rows if row.is_current) == 1
    assert all(row.on_active_path for row in ctx.layout.rows), (
        "a linear session has exactly one path, so every row is on it"
    )


def test_a_shallow_session_is_unchanged(tmp_path: Path) -> None:
    sm = _linear_session(tmp_path, 3)
    ctx = _Ctx(sm)

    open_tree_selector(ctx)  # type: ignore[arg-type]

    assert len(ctx.layout.rows) == 6
    assert [row.role for row in ctx.layout.rows[:2]] == ["user", "assistant"]


def test_branch_structure_still_renders(tmp_path: Path) -> None:
    """The iterative walk must reproduce connectors, not just avoid crashing."""
    sm = _linear_session(tmp_path, 2)
    entries = sm.get_entries()
    sm.branch(entries[1].id)
    sm.append_message(UserMessage(contents=[TextContent(content="other branch")]))
    ctx = _Ctx(sm)

    open_tree_selector(ctx)  # type: ignore[arg-type]

    prefixes = [row.prefix for row in ctx.layout.rows]
    assert any(p.strip() for p in prefixes), "a branched tree must draw connectors"


def _branched_session(tmp_path: Path, turns: int, forks: int) -> SessionManager:
    """Deep *and* branched — the combination the fix has to survive."""
    sm = _linear_session(tmp_path, turns)
    for k in range(forks):
        entries = sm.get_entries()
        sm.branch(entries[len(entries) // 2].id)
        for j in range(3):
            sm.append_message(UserMessage(contents=[TextContent(content=f"b{k}-{j}")]))
            sm.append_message(AssistantMessage(contents=[TextContent(content=f"c{k}-{j}")]))
    return sm


def test_a_deep_session_that_also_branches(tmp_path: Path) -> None:
    """Depth and branching together, since each was only covered alone.

    The chain is what exceeds the recursion limit; the forks are what exercise
    connectors, gutters and the active-branch-first ordering. A stack-based
    walk could pass either in isolation and still mis-order the combination.
    """
    sm = _branched_session(tmp_path, _DEPTH, forks=4)
    assert len(sm.get_entries()) > sys.getrecursionlimit()
    ctx = _Ctx(sm)

    open_tree_selector(ctx)  # type: ignore[arg-type]

    assert {row.value for row in ctx.layout.rows} == _displayable_ids(sm)
    assert ctx.notices == []
    assert any(row.prefix.strip() for row in ctx.layout.rows), "forks must draw connectors"


def test_a_deep_branched_session_marks_one_current_leaf(tmp_path: Path) -> None:
    sm = _branched_session(tmp_path, _DEPTH, forks=3)
    ctx = _Ctx(sm)

    open_tree_selector(ctx)  # type: ignore[arg-type]

    assert sum(1 for row in ctx.layout.rows if row.is_current) == 1
    on_path = sum(1 for row in ctx.layout.rows if row.on_active_path)
    assert 0 < on_path < len(ctx.layout.rows), (
        "a branched session has rows both on and off the active path"
    )


def test_children_of_a_fork_are_both_reachable(tmp_path: Path) -> None:
    """No branch is dropped by the traversal.

    Compared against the displayable entries rather than all of them: each
    `branch()` also records a `LeafEntry`, which has no role to render and so
    never produced a row, before this change or after.
    """
    sm = _branched_session(tmp_path, 20, forks=2)
    ctx = _Ctx(sm)

    open_tree_selector(ctx)  # type: ignore[arg-type]

    assert {row.value for row in ctx.layout.rows} == _displayable_ids(sm)


@pytest.mark.parametrize("turns", [1, 2, 50])
def test_row_count_matches_entry_count(tmp_path: Path, turns: int) -> None:
    sm = _linear_session(tmp_path, turns)
    ctx = _Ctx(sm)

    open_tree_selector(ctx)  # type: ignore[arg-type]

    assert len(ctx.layout.rows) == len(sm.get_entries())

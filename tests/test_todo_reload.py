"""Regression tests: the todo list must survive an extension reload.

Observed failure. A task was created mid-session, the user changed a setting
(which triggers a reload), and the next `todo update` reported the task id as
unknown while `todo list` reported no tasks at all — even though nothing had
been deleted and the `todo:state` snapshots were still on the session branch.

The tell in the session file was the id counter going *backwards*
(``next_id`` 9 -> 8). A delete leaves the counter alone; only a freshly
constructed ``TodoState`` resets it. ``register()`` re-runs on every reload and
builds exactly that, and ``_rebuild`` was wired only to ``session_start`` /
``session_tree`` — neither of which fires on reload.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from tests.ext_loader import load_extension

_PKG = load_extension("todo", builtin=True).__name__
todo_ext = importlib.import_module(_PKG)
todo_tool = importlib.import_module(f"{_PKG}.todo_tool")

TodoState = todo_tool.TodoState
CUSTOM_TYPE = todo_tool.CUSTOM_TYPE


def _entry(state: TodoState) -> Any:
    """A session entry holding a snapshot of ``state``, as the tool writes."""
    from tau.session.types import CustomInfoEntry

    return CustomInfoEntry(id="e1", custom_type=CUSTOM_TYPE, data=state.to_dict())


class _API:
    """Minimal ExtensionAPI double that records what register() subscribes to."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.config: dict[str, Any] = {}
        self.tools: list[Any] = []
        self.commands: list[str] = []
        self._runtime_ref = None

    def on(self, event: str, handler: Any = None) -> Any:
        if handler is None:

            def deco(fn: Any) -> Any:
                self.handlers.setdefault(event, []).append(fn)
                return fn

            return deco
        self.handlers.setdefault(event, []).append(handler)
        return handler

    def register_tool(self, tool: Any) -> None:
        self.tools.append(tool)

    def register_command(self, name: str, *_a: Any, **_k: Any) -> None:
        self.commands.append(name)


class _Ctx:
    """ExtensionContext double exposing only what _rebuild touches."""

    def __init__(self, entries: list[Any]) -> None:
        self.branch_entries = entries
        self.ui = None


def test_register_subscribes_rebuild_to_extension_reloaded() -> None:
    api = _API()
    todo_ext.register(api)
    assert "extension_reloaded" in api.handlers, "without this the list empties on every /reload"


def test_state_survives_a_reload() -> None:
    # A list built up before the reload, including a task inserted with
    # after_id — the exact shape that went missing.
    before = TodoState()
    for subject in ("one", "two", "three"):
        before.create(subject, None)
    inserted = before.create("inserted", None, after_id=1)
    entries = [_entry(before)]

    assert [i.id for i in before.items] == [1, 4, 2, 3]
    assert inserted.id == 4

    # Reload: register() runs again and builds a brand-new empty state.
    api = _API()
    todo_ext.register(api)
    ctx = _Ctx(entries)

    for handler in api.handlers["extension_reloaded"]:
        handler(None, ctx)

    rebuilt = api.tools[0]._state
    assert [i.id for i in rebuilt.items] == [1, 4, 2, 3]
    assert rebuilt.find(4) is not None, "the inserted task was lost across reload"


def test_the_id_counter_does_not_rewind_across_a_reload() -> None:
    # next_id going backwards is what let a later create silently reuse an id.
    before = TodoState()
    for subject in ("a", "b", "c"):
        before.create(subject, None)
    assert before.to_dict()["next_id"] == 4

    api = _API()
    todo_ext.register(api)
    for handler in api.handlers["extension_reloaded"]:
        handler(None, _Ctx([_entry(before)]))

    assert api.tools[0]._state.to_dict()["next_id"] == 4


def test_reload_with_no_prior_entries_is_an_empty_list_not_a_crash() -> None:
    api = _API()
    todo_ext.register(api)
    for handler in api.handlers["extension_reloaded"]:
        handler(None, _Ctx([]))
    assert api.tools[0]._state.items == []


def test_rebuild_takes_the_latest_snapshot() -> None:
    first = TodoState()
    first.create("early", None)
    later = TodoState()
    later.create("early", None)
    later.create("added afterwards", None)

    state = TodoState()
    state.rebuild([_entry(first), _entry(later)])

    assert [i.subject for i in state.items] == ["early", "added afterwards"]


@pytest.mark.parametrize("event", ["session_start", "session_tree", "extension_reloaded"])
def test_every_state_restoring_event_is_wired(event: str) -> None:
    api = _API()
    todo_ext.register(api)
    assert api.handlers.get(event), f"{event} must restore todo state"

"""Tests for asynchronous session-scope loading in ResumeSelector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tau.modes.interactive.components.session_selector import ResumeSelector


@dataclass
class _Session:
    id: str
    path: Path
    modified: datetime
    name: str | None = None
    cwd: Path | None = None
    message_count: int = 0


def _session(identifier: str) -> _Session:
    return _Session(
        id=identifier,
        path=Path(f"/{identifier}.jsonl"),
        modified=datetime.now(UTC),
        name=identifier,
    )


def test_toggle_to_all_scope_starts_async_load_without_showing_folder_sessions() -> None:
    load_requests: list[None] = []
    selector = ResumeSelector(
        current_sessions=[_session("folder")],
        all_sessions_loader=lambda: (_ for _ in ()).throw(
            AssertionError("must not run synchronously")
        ),
        on_load_all=lambda: load_requests.append(None),
    )

    selector.toggle_scope()

    assert load_requests == [None]
    assert selector._scope == "all"
    assert selector._loading_all is True
    assert selector._filtered == []

    # Switching away and back while loading does not start a duplicate scan.
    selector.toggle_scope()
    selector.toggle_scope()
    assert load_requests == [None]

    selector.append_sessions("all", [_session("other-project")], has_more=False, total_count=1)
    assert selector._loading_all is False
    assert selector._all_total_count == 1
    assert selector.selected_path() == Path("/other-project.jsonl")

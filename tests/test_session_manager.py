from __future__ import annotations

import pytest

from tau.message.types import AssistantMessage, TextContent, UserMessage
from tau.session.manager import SessionManager
from tau.session.types import CustomInfoEntry, MessageEntry


def _manager(tmp_path) -> SessionManager:
    return SessionManager(
        cwd=tmp_path,
        session_dir=tmp_path / "sessions",
        persist=False,
    )


def test_create_branched_session_rechains_entries_after_removing_labels(tmp_path) -> None:
    manager = _manager(tmp_path)
    first_id = manager.append_message(UserMessage.from_text("first"))
    manager.append_label_change(first_id, "checkpoint")
    second_id = manager.append_message(UserMessage.from_text("second"))

    manager.create_branched_session(second_id)

    entries = manager.get_entries()
    retained_first, retained_second = entries[:2]
    assert retained_first.id == first_id
    assert retained_first.parent_id is None
    assert retained_second.id == second_id
    assert retained_second.parent_id == first_id
    assert manager.get_branch(second_id) == [retained_first, retained_second]


def test_get_tree_treats_orphaned_entry_as_root(tmp_path) -> None:
    manager = _manager(tmp_path)
    orphan = CustomInfoEntry(custom_type="test", parent_id="missing")
    manager.entries.append(orphan)
    manager.by_id[orphan.id] = orphan

    roots = manager.get_tree()

    assert [node.entry.id for node in roots] == [orphan.id]


def test_get_tree_handles_deep_trees_iteratively(tmp_path) -> None:
    manager = _manager(tmp_path)
    for index in range(2_000):
        manager.append_custom_info("depth", {"index": index})

    roots = manager.get_tree()

    assert len(roots) == 1
    depth = 1
    node = roots[0]
    while node.children:
        assert len(node.children) == 1
        node = node.children[0]
        depth += 1
    assert depth == 2_000


def test_opening_invalid_existing_session_does_not_overwrite_it(tmp_path) -> None:
    session_file = tmp_path / "broken.jsonl"
    original = "not-json\n"
    session_file.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid or empty session"):
        SessionManager(
            cwd=tmp_path,
            session_dir=tmp_path / "sessions",
            session_file=session_file,
            persist=True,
        )

    assert session_file.read_text(encoding="utf-8") == original


def test_explicit_new_session_file_remains_valid_after_first_turn(tmp_path) -> None:
    session_file = tmp_path / "explicit.jsonl"
    manager = SessionManager(
        cwd=tmp_path,
        session_dir=tmp_path / "sessions",
        session_file=session_file,
        persist=True,
    )
    manager.append_message(UserMessage.from_text("first task"))
    manager.append_message(AssistantMessage.from_text("first result"))

    resumed = SessionManager(
        cwd=tmp_path,
        session_dir=tmp_path / "sessions",
        session_file=session_file,
        persist=True,
    )

    messages = [
        "".join(
            content.content
            for content in entry.message.contents
            if isinstance(content, TextContent)
        )
        for entry in resumed.get_branch()
        if isinstance(entry, MessageEntry)
    ]
    assert messages == ["first task", "first result"]


def test_persistence_errors_propagate(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager.persist = True
    manager.session_file = tmp_path

    with pytest.raises(OSError):
        manager._rewrite_file()


def test_get_branch_rejects_parent_cycles(tmp_path) -> None:
    manager = _manager(tmp_path)
    first = CustomInfoEntry(id="first", custom_type="test", parent_id="second")
    second = CustomInfoEntry(id="second", custom_type="test", parent_id="first")
    manager.entries.extend([first, second])
    manager.by_id.update({"first": first, "second": second})
    manager.leaf_id = "second"

    with pytest.raises(ValueError, match="Cycle detected"):
        manager.get_branch()

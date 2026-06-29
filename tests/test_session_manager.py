from __future__ import annotations

from tau.message.types import UserMessage
from tau.session.manager import SessionManager
from tau.session.types import CustomInfoEntry


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

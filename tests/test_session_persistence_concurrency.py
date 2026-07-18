"""Regression coverage for cross-process-style session persistence interleavings."""

from tau.message.types import AssistantMessage, UserMessage
from tau.session.manager import SessionManager
from tau.session.types import CustomInfoEntry


def test_stale_managers_merge_independent_appends(tmp_path) -> None:
    session_file = tmp_path / "shared.jsonl"
    seed = SessionManager(tmp_path, session_dir=tmp_path / "sessions", session_file=session_file)
    seed.append_message(UserMessage.from_text("question"))
    seed.append_message(AssistantMessage.from_text("answer"))

    # Both instances now have the same durable snapshot. The second append is
    # deliberately made from a stale view, as it would be in another process.
    first = SessionManager(tmp_path, session_dir=tmp_path / "sessions", session_file=session_file)
    second = SessionManager(tmp_path, session_dir=tmp_path / "sessions", session_file=session_file)
    first_id = first.append_custom_info("first", {"writer": 1})
    second_id = second.append_custom_info("second", {"writer": 2})

    resumed = SessionManager(tmp_path, session_dir=tmp_path / "sessions", session_file=session_file)
    ids = {entry.id for entry in resumed.get_entries()}
    assert {first_id, second_id} <= ids
    custom_types = [
        entry.custom_type for entry in resumed.get_entries() if isinstance(entry, CustomInfoEntry)
    ]
    assert custom_types == ["first", "second"]


def test_stale_rewrite_does_not_discard_another_manager_append(tmp_path) -> None:
    session_file = tmp_path / "shared.jsonl"
    seed = SessionManager(tmp_path, session_dir=tmp_path / "sessions", session_file=session_file)
    seed.append_message(UserMessage.from_text("question"))
    assistant_id = seed.append_message(AssistantMessage.from_text("answer"))

    stale_rewriter = SessionManager(
        tmp_path, session_dir=tmp_path / "sessions", session_file=session_file
    )
    appender = SessionManager(
        tmp_path, session_dir=tmp_path / "sessions", session_file=session_file
    )
    appended_id = appender.append_custom_info("remote", {})

    assert stale_rewriter.remove_last_message("assistant")
    resumed = SessionManager(tmp_path, session_dir=tmp_path / "sessions", session_file=session_file)
    ids = {entry.id for entry in resumed.get_entries()}
    assert assistant_id not in ids
    assert appended_id in ids

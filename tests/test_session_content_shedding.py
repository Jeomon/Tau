"""pi #6841 — bound session RAM by shedding folded message content.

After compaction, the message bodies before the kept window are represented to
the LLM only by the summary, so their heavy content (tool results, file bodies)
is dropped from the in-memory cache while the full copy stays on disk. These
tests verify the memory is freed AND that every reader still sees correct data.
"""

from __future__ import annotations

from tau.message.types import (
    AssistantMessage,
    CompactionSummaryMessage,
    TextContent,
    UserMessage,
)
from tau.session.manager import SessionManager
from tau.session.types import MessageEntry
from tau.session.utils import read_session_file


def _text(msg) -> str:
    """Concatenate TextContent from any message (works for shed/full)."""
    return "".join(
        c.content for c in getattr(msg, "contents", []) if isinstance(c, TextContent)
    )


def _manager(tmp_path) -> SessionManager:
    return SessionManager(cwd=tmp_path, session_dir=tmp_path / "sessions", persist=True)


def _compacted_session(tmp_path) -> tuple[SessionManager, dict[str, str]]:
    """Build a persisted session with a compaction; return (manager, ids)."""
    m = _manager(tmp_path)
    ids = {}
    ids["u1"] = m.append_message(UserMessage.from_text("USER-ONE-" + "x" * 500))
    ids["a1"] = m.append_message(AssistantMessage.from_text("ASSISTANT-ONE-" + "y" * 500))
    ids["u2"] = m.append_message(UserMessage.from_text("USER-TWO-kept"))
    m.append_compaction(summary="THE-SUMMARY", first_kept_entry_id=ids["u2"], tokens_before=1234)
    ids["a2"] = m.append_message(AssistantMessage.from_text("ASSISTANT-TWO-kept"))
    return m, ids


def test_folded_message_content_is_shed_but_disk_keeps_it(tmp_path):
    m, ids = _compacted_session(tmp_path)

    # Pre-compaction messages are shed from RAM...
    assert ids["u1"] in m._shed_ids
    assert ids["a1"] in m._shed_ids
    assert m.by_id[ids["u1"]].message.contents == []
    assert m.by_id[ids["a1"]].message.contents == []
    # ...but the kept ones are untouched.
    assert ids["u2"] not in m._shed_ids
    assert m.by_id[ids["u2"]].message.contents

    # Disk still holds the FULL content of the shed entries.
    on_disk = {e.id: e for e in read_session_file(m.session_file)}
    u1_disk = on_disk[ids["u1"]]
    assert isinstance(u1_disk, MessageEntry)
    assert "USER-ONE-" in _text(u1_disk.message)


def test_build_session_context_correct_after_shedding(tmp_path):
    m, ids = _compacted_session(tmp_path)
    ctx = m.build_session_context()

    assert any(isinstance(msg, CompactionSummaryMessage) for msg in ctx.messages)
    joined = " ".join(_text(msg) for msg in ctx.messages) + " ".join(
        msg.summary for msg in ctx.messages if isinstance(msg, CompactionSummaryMessage)
    )
    assert "USER-TWO-kept" in joined
    assert "ASSISTANT-TWO-kept" in joined
    assert "USER-ONE-" not in joined  # folded body must not leak back in
    assert "THE-SUMMARY" in joined


def test_get_entries_rehydrates_full_content_from_disk(tmp_path):
    m, ids = _compacted_session(tmp_path)
    by_id = {e.id: e for e in m.get_entries()}
    assert "USER-ONE-" in _text(by_id[ids["u1"]].message)
    assert "ASSISTANT-ONE-" in _text(by_id[ids["a1"]].message)


def test_fork_from_shed_entry_preserves_full_content(tmp_path):
    m, ids = _compacted_session(tmp_path)
    new_file = m.create_branched_session(ids["a2"])
    assert new_file is not None
    forked = {e.id: e for e in read_session_file(new_file)}
    assert "USER-ONE-" in _text(forked[ids["u1"]].message)
    assert "ASSISTANT-ONE-" in _text(forked[ids["a1"]].message)


def test_resume_sheds_immediately(tmp_path):
    m, ids = _compacted_session(tmp_path)
    session_file = m.session_file

    resumed = SessionManager(
        cwd=tmp_path, session_dir=tmp_path / "sessions", session_file=session_file, persist=True
    )
    assert ids["u1"] in resumed._shed_ids
    assert resumed.by_id[ids["u1"]].message.contents == []
    entries = {e.id: e for e in resumed.get_entries()}
    assert "USER-ONE-" in _text(entries[ids["u1"]].message)
    ctx = resumed.build_session_context()
    assert any(isinstance(msg, CompactionSummaryMessage) for msg in ctx.messages)


def test_navigating_into_shed_region_rehydrates_context(tmp_path):
    """If the user branches back to a folded entry, its content must come back."""
    m, ids = _compacted_session(tmp_path)
    assert ids["u1"] in m._shed_ids  # u1's body is shed in RAM

    m.branch(ids["u1"])  # navigate back before the compaction
    ctx = m.build_session_context()
    joined = " ".join(_text(msg) for msg in ctx.messages)
    assert "USER-ONE-" in joined  # rehydrated, not an empty stub


def test_no_compaction_sheds_nothing(tmp_path):
    m = _manager(tmp_path)
    uid = m.append_message(UserMessage.from_text("just a message"))
    m.append_message(AssistantMessage.from_text("reply"))
    assert m._shed_ids == set()
    assert m.by_id[uid].message.contents  # untouched


def test_rewrite_does_not_persist_shed_stubs(tmp_path):
    """After a rewrite (e.g. undo), disk must still hold full content, RAM re-shed."""
    m, ids = _compacted_session(tmp_path)
    m.remove_last_message()  # triggers _rewrite_file
    on_disk = {e.id: e for e in read_session_file(m.session_file)}
    assert "USER-ONE-" in _text(on_disk[ids["u1"]].message)  # disk full
    assert m.by_id[ids["u1"]].message.contents == []  # RAM still shed
    assert ids["u1"] in m._shed_ids

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


# ── Loader/manager shed-criterion drift guard ─────────────────────────────────
#
# read_session_file_shedding() re-implements _shed_folded_message_content()'s
# criterion ("MessageEntries on the current leaf's branch before the most
# recent compaction boundary") on raw dicts, so pydantic never constructs the
# heavy bodies. Nothing but convention keeps the two implementations agreeing;
# these tests turn silent drift into a red test by deriving the shed set both
# ways on the same file and asserting equality.


def _shed_sets_both_ways(session_file, tmp_path, monkeypatch):
    """(loader_ids, manager_ids) for one file, derived independently."""
    import tau.session.manager as manager_mod
    from tau.session.utils import read_session_file_shedding

    _, loader_ids = read_session_file_shedding(session_file)

    # Force the legacy full-read path so _shed_folded_message_content()
    # re-derives the set from scratch instead of trusting the loader.
    monkeypatch.setattr(
        manager_mod,
        "read_session_file_shedding",
        lambda f: (read_session_file(f), set()),
    )
    m = SessionManager(cwd=tmp_path, session_dir=tmp_path / "drift", persist=True)
    m.set_session(session_file)
    return loader_ids, set(m._shed_ids)


def test_loader_shed_set_matches_manager_linear(tmp_path, monkeypatch):
    """Linear (never-navigated) session — the loader's fast path."""
    m, ids = _compacted_session(tmp_path)
    loader_ids, manager_ids = _shed_sets_both_ways(m.session_file, tmp_path, monkeypatch)
    assert loader_ids == manager_ids
    assert loader_ids == {ids["u1"], ids["a1"]}  # and the set is the right one


def test_loader_shed_set_matches_manager_navigated(tmp_path, monkeypatch):
    """Tree-navigated session — the loader's general (branch-walk) path.

    Layout: u1→a1→u2→a2, branch back to u2 (LeafEntry breaks linearity),
    then a3, compaction keeping from a3, then u4. On the current branch,
    u1/a1/u2 are folded; the abandoned a2 is off-branch and must NOT be shed
    by either implementation.
    """
    m = _manager(tmp_path)
    ids = {}
    ids["u1"] = m.append_message(UserMessage.from_text("USER-ONE-" + "x" * 500))
    ids["a1"] = m.append_message(AssistantMessage.from_text("ASSISTANT-ONE-" + "y" * 500))
    ids["u2"] = m.append_message(UserMessage.from_text("USER-TWO-" + "z" * 500))
    ids["a2"] = m.append_message(AssistantMessage.from_text("ABANDONED-" + "w" * 500))
    m.branch(ids["u2"])  # navigate back — a2 is now off-branch
    ids["a3"] = m.append_message(AssistantMessage.from_text("ASSISTANT-THREE-kept"))
    m.append_compaction(summary="S", first_kept_entry_id=ids["a3"], tokens_before=99)
    ids["u4"] = m.append_message(UserMessage.from_text("USER-FOUR-kept"))

    loader_ids, manager_ids = _shed_sets_both_ways(m.session_file, tmp_path, monkeypatch)
    assert loader_ids == manager_ids
    assert ids["a2"] not in loader_ids  # off-branch content stays resident
    assert {ids["u1"], ids["a1"], ids["u2"]} <= loader_ids


def test_loader_shed_set_matches_manager_uncompacted(tmp_path, monkeypatch):
    """No compaction — both implementations must shed nothing."""
    m = _manager(tmp_path)
    m.append_message(UserMessage.from_text("hello"))
    m.append_message(AssistantMessage.from_text("world"))
    loader_ids, manager_ids = _shed_sets_both_ways(m.session_file, tmp_path, monkeypatch)
    assert loader_ids == manager_ids == set()


# ── Selective rehydration on tree navigation ──────────────────────────────────
#
# A /tree jump back into a folded region is "a resume with a different leaf":
# the context builder must recover ONLY the bodies inside the jump target's
# own visible window (its nearest compaction → target), never re-validate the
# whole file, and everything before that window's compaction stays shed.


def _two_compaction_session(tmp_path):
    """root: u1 a1 | C1 keeps from u2 | u2 a2 | C2 keeps from u3 | u3 (leaf)."""
    m = _manager(tmp_path)
    ids = {}
    ids["u1"] = m.append_message(UserMessage.from_text("ANCIENT-" + "x" * 500))
    ids["a1"] = m.append_message(AssistantMessage.from_text("ANCIENT-REPLY-" + "y" * 500))
    ids["u2"] = m.append_message(UserMessage.from_text("MIDDLE-USER-" + "m" * 500))
    m.append_compaction(summary="SUMMARY-ONE", first_kept_entry_id=ids["u2"], tokens_before=10)
    # NB: append_compaction's entry sits after u2 in file order but the kept
    # window starts AT u2, so u2's body is needed when we jump back to a2.
    ids["a2"] = m.append_message(AssistantMessage.from_text("MIDDLE-REPLY-" + "n" * 500))
    ids["u3"] = m.append_message(UserMessage.from_text("RECENT-kept"))
    m.append_compaction(summary="SUMMARY-TWO", first_kept_entry_id=ids["u3"], tokens_before=20)
    m.append_message(AssistantMessage.from_text("RECENT-REPLY-kept"))
    return m, ids


def test_jump_before_compaction_sees_older_compactions_window(tmp_path):
    """Jumping between C1 and C2: context = C1's summary + middle window only."""
    m, ids = _two_compaction_session(tmp_path)
    assert ids["u1"] in m._shed_ids and ids["a2"] in m._shed_ids  # both folded now

    m.branch(ids["a2"])  # jump back before C2
    ctx = m.build_session_context()
    joined = " ".join(_text(msg) for msg in ctx.messages) + " ".join(
        msg.summary for msg in ctx.messages if isinstance(msg, CompactionSummaryMessage)
    )
    assert "SUMMARY-ONE" in joined  # the OLDER compaction governs this view
    assert "MIDDLE-USER-" in joined and "MIDDLE-REPLY-" in joined  # window rehydrated
    assert "ANCIENT-" not in joined  # pre-C1 stays behind the summary
    assert "SUMMARY-TWO" not in joined  # the newer compaction is off this branch


def test_jump_rehydration_is_selective_not_full_file(tmp_path, monkeypatch):
    """The jump must never pay full-file validation to recover a few bodies."""
    m, ids = _two_compaction_session(tmp_path)
    m.branch(ids["a2"])

    def _boom(self):
        raise AssertionError("full-file rehydration (_full_entries) used on jump path")

    monkeypatch.setattr(SessionManager, "_full_entries", _boom)
    ctx = m.build_session_context()  # must succeed without _full_entries
    joined = " ".join(_text(msg) for msg in ctx.messages)
    assert "MIDDLE-REPLY-" in joined

    # Residency moved WITH the jump: the new window is resident in full
    # (so per-turn context builds don't re-read disk), while pre-C1 ancients
    # were never validated or rehydrated.
    assert m.by_id[ids["a2"]].message.contents  # in-window: resident full
    assert m.by_id[ids["u1"]].message.contents == []
    assert ids["u1"] in m._shed_ids


def test_jump_moves_residency_window_both_directions(tmp_path):
    """Navigation swaps residency: the window you leave gets shed, the window
    you enter gets rehydrated — round trip included."""
    m, ids = _two_compaction_session(tmp_path)
    tail_id = m.get_branch()[-1].id  # RECENT-REPLY-kept, resident full at start
    assert m.by_id[tail_id].message.contents

    m.branch(ids["a2"])  # jump back before C2
    # old window (post-C2) no longer visible → shed from RAM
    assert m.by_id[ids["u3"]].message.contents == []
    assert m.by_id[tail_id].message.contents == []
    assert ids["u3"] in m._shed_ids
    # new window (C1-kept → a2) resident in full
    assert m.by_id[ids["u2"]].message.contents
    assert m.by_id[ids["a2"]].message.contents
    # pre-C1 stays shed — it's behind the older summary either way
    assert m.by_id[ids["u1"]].message.contents == []

    m.branch(tail_id)  # jump forward again
    # residency swaps back: old window rehydrated, middle shed again
    assert m.by_id[ids["u3"]].message.contents
    assert m.by_id[tail_id].message.contents
    assert m.by_id[ids["a2"]].message.contents == []
    assert ids["a2"] in m._shed_ids
    # and the context is complete after the round trip
    joined = " ".join(_text(msg) for msg in m.build_session_context().messages)
    assert "RECENT-REPLY-kept" in joined

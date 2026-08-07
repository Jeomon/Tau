"""Finding a past session by what was said in it.

`/resume` lists sessions by name and date, which only helps when you remember
one of those. What you usually remember is the content.
"""

from __future__ import annotations

from pathlib import Path

from tau.message.types import AssistantMessage, UserMessage
from tau.session.manager import SessionManager
from tau.session.search import SNIPPET_RADIUS, search_sessions
from tau.session.utils import build_session_info


def _session(tmp_path: Path, name: str, exchanges: list[tuple[str, str]], summary: str = ""):
    directory = tmp_path / name
    manager = SessionManager(cwd=tmp_path / name, session_dir=directory)
    for question, answer in exchanges:
        manager.append_message(UserMessage.from_text(question))
        manager.append_message(AssistantMessage.from_text(answer))
    if summary:
        manager.append_compaction(summary=summary, first_kept_entry_id="x", tokens_before=1)
    info = build_session_info(manager.session_file)
    assert info is not None
    return info


class TestMatching:
    def test_finds_text_in_a_user_message(self, tmp_path):
        info = _session(tmp_path, "a", [("why does compaction wedge?", "because of the phase")])

        hits = search_sessions("wedge", sessions=[info])

        assert len(hits) == 1
        assert hits[0].role == "user"
        assert "wedge" in hits[0].snippet

    def test_finds_text_in_an_assistant_message(self, tmp_path):
        info = _session(tmp_path, "a", [("q", "the answer involves a semaphore")])

        hits = search_sessions("semaphore", sessions=[info])

        assert [h.role for h in hits] == ["assistant"]

    def test_finds_text_in_a_compaction_summary(self, tmp_path):
        """Often the most memorable thing in a long session is its summary."""
        info = _session(tmp_path, "a", [("q", "a")], summary="Fixed the retry backoff")

        hits = search_sessions("retry backoff", sessions=[info])

        assert len(hits) == 1
        assert "retry backoff" in hits[0].snippet

    def test_matching_is_case_insensitive(self, tmp_path):
        info = _session(tmp_path, "a", [("q", "The Compaction Race")])

        assert search_sessions("compaction race", sessions=[info])

    def test_a_miss_returns_nothing(self, tmp_path):
        info = _session(tmp_path, "a", [("q", "a")])

        assert search_sessions("kangaroo", sessions=[info]) == []

    def test_an_empty_query_matches_nothing(self, tmp_path):
        """Otherwise every entry of every session would come back."""
        info = _session(tmp_path, "a", [("q", "a")])

        assert search_sessions("   ", sessions=[info]) == []

    def test_metadata_matches_do_not_count(self, tmp_path):
        """A query hitting an entry id or a path in the raw line is not a hit;
        only the message text is searched."""
        info = _session(tmp_path, "a", [("hello", "world")])

        assert search_sessions(info.id, sessions=[info]) == []


class TestSnippets:
    def test_the_snippet_carries_context_around_the_match(self, tmp_path):
        filler = "x" * (SNIPPET_RADIUS * 3)
        info = _session(tmp_path, "a", [("q", f"{filler} NEEDLE {filler}")])

        snippet = search_sessions("needle", sessions=[info])[0].snippet

        assert "NEEDLE" in snippet
        assert snippet.startswith("\u2026") and snippet.endswith("\u2026")
        assert len(snippet) < len(filler) * 2

    def test_whitespace_is_collapsed_to_one_line(self, tmp_path):
        info = _session(tmp_path, "a", [("q", "first line\n\n\tsecond   NEEDLE line")])

        assert "\n" not in search_sessions("needle", sessions=[info])[0].snippet


class TestLimits:
    def test_hits_per_session_are_capped(self, tmp_path):
        info = _session(tmp_path, "a", [("needle", "needle")] * 10)

        assert len(search_sessions("needle", sessions=[info], limit_per_session=2)) == 2

    def test_the_overall_limit_is_respected(self, tmp_path):
        infos = [_session(tmp_path, f"s{i}", [("needle", "needle")]) for i in range(5)]

        assert len(search_sessions("needle", sessions=infos, limit=3)) == 3

    def test_results_follow_the_order_of_the_sessions_given(self, tmp_path):
        first = _session(tmp_path, "first", [("needle one", "a")])
        second = _session(tmp_path, "second", [("needle two", "a")])

        hits = search_sessions("needle", sessions=[second, first])

        assert [h.session.path for h in hits] == [second.path, first.path]


class TestSurface:
    def test_the_search_command_reuses_the_resume_picker(self):
        """A result is resumed exactly as `/resume` would, rather than through
        a second, subtly different path."""
        import inspect

        from tau.modes.interactive.commands import session as panel

        source = inspect.getsource(panel.open_search_selector)

        assert "open_resume_selector" in source
        assert "_apply_resume" in source

    def test_the_command_is_registered(self):
        import inspect

        from tau.modes.interactive.app import App

        source = inspect.getsource(App)

        assert 'name="search"' in source


class TestBackends:
    def test_a_non_jsonl_session_is_refused_loudly(self, tmp_path, caplog):
        """A SQLite session's path is the database file. Scanning it as text
        finds nothing, and reporting that as "no match" would be a lie."""
        import logging

        from tau.session.search import _search_jsonl

        database = tmp_path / "sessions.db"
        database.write_bytes(b"SQLite format 3\x00 needle")
        info = _session(tmp_path, "a", [("q", "a")])

        with caplog.at_level(logging.WARNING):
            hits = _search_jsonl(database, "needle", info, 10)

        assert hits == []
        assert "only JSONL sessions are searchable" in caplog.text

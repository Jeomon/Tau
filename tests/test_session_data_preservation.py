"""Regression tests: session files must not silently lose data.

Covers:
- read_session_file() logging (not silently swallowing) unparseable lines
- _rewrite_file() preserving a .bak of the original file when a rewrite would
  otherwise permanently destroy unparseable lines
- the per-project session dir encoding no longer colliding for sibling paths
  (/x/my-app vs /x/my/app) while legacy directories stay findable
- continue_recent() skipping sessions whose header cwd belongs to another
  project (possible in a shared legacy-encoded directory)
"""

from __future__ import annotations

import json
import logging
import re
import time

from tau.session.manager import SessionManager
from tau.session.utils import (
    find_most_recent_session,
    get_default_project_session_dir,
    read_session_file,
)


def _header_line(cwd: str, id: str = "abc123") -> str:
    return json.dumps(
        {
            "type": "session",
            "id": id,
            "cwd": cwd,
            "timestamp": time.time(),
            "parent_session": None,
        }
    )


def _legacy_dir_name(resolved: str) -> str:
    return "--" + re.sub(r"^[/\\]", "", resolved).replace("/", "-") + "--"


class TestUnparseableLines:
    def test_read_session_file_warns_with_line_number(self, tmp_path, caplog):
        f = tmp_path / "s.jsonl"
        f.write_text(_header_line("/tmp") + "\n{not json}\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="tau.session.utils"):
            entries = read_session_file(f)
        assert len(entries) == 1
        assert any("unparseable line 2" in rec.getMessage() for rec in caplog.records)

    def test_rewrite_preserves_original_as_bak(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        f = session_dir / "2026-01-01T00-00-00-000000_abc123.jsonl"
        original = _header_line(str(tmp_path)) + "\n{corrupt line\n"
        f.write_text(original, encoding="utf-8")

        manager = SessionManager.open(f, cwd_override=tmp_path)
        manager._rewrite_file()

        backup = f.with_name(f.name + ".bak")
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == original
        # The rewritten file keeps only the parsed entries...
        assert "{corrupt line" not in f.read_text(encoding="utf-8")
        # ...and a second rewrite does not clobber the preserved original.
        manager._rewrite_file()
        assert backup.read_text(encoding="utf-8") == original

    def test_rewrite_of_clean_file_creates_no_backup(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        f = session_dir / "2026-01-01T00-00-00-000000_clean1.jsonl"
        f.write_text(_header_line(str(tmp_path), id="clean1") + "\n", encoding="utf-8")

        manager = SessionManager.open(f, cwd_override=tmp_path)
        manager._rewrite_file()
        assert not f.with_name(f.name + ".bak").exists()


class TestSessionDirEncoding:
    def test_sibling_paths_do_not_collide(self, tmp_path):
        a = tmp_path / "my-app"
        b = tmp_path / "my" / "app"
        a.mkdir()
        b.mkdir(parents=True)
        base = tmp_path / "sessions"
        d1 = get_default_project_session_dir(a, sessions_dir=base)
        d2 = get_default_project_session_dir(b, sessions_dir=base)
        assert d1 != d2

    def test_legacy_directory_still_found(self, tmp_path):
        cwd = tmp_path / "proj"
        cwd.mkdir()
        base = tmp_path / "sessions"
        base.mkdir()
        legacy = base / _legacy_dir_name(str(cwd.resolve()))
        legacy.mkdir()
        assert get_default_project_session_dir(cwd, sessions_dir=base) == legacy

    def test_new_directory_is_hash_disambiguated(self, tmp_path):
        cwd = tmp_path / "proj"
        cwd.mkdir()
        base = tmp_path / "sessions"
        d = get_default_project_session_dir(cwd, sessions_dir=base)
        assert d.exists()
        assert d.name != _legacy_dir_name(str(cwd.resolve()))
        # Stable across calls.
        assert get_default_project_session_dir(cwd, sessions_dir=base) == d


class TestContinueRecentCwdCrossCheck:
    def test_skips_sessions_from_other_project(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        mine = tmp_path / "mine"
        other = tmp_path / "other"
        mine.mkdir()
        other.mkdir()
        other_file = shared / "other.jsonl"
        other_file.write_text(_header_line(str(other), id="other01") + "\n", encoding="utf-8")

        assert find_most_recent_session(shared, cwd=mine) is None
        assert find_most_recent_session(shared, cwd=other) == other_file
        assert find_most_recent_session(shared) == other_file

        # continue_recent must not resume another project's session.
        manager = SessionManager.continue_recent(mine, session_dir=shared)
        assert manager.session_file != other_file

        resumed = SessionManager.continue_recent(other, session_dir=shared)
        assert resumed.session_file == other_file
        assert resumed.session_id == "other01"

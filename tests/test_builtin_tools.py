"""Tests for tau/builtins/tools/ — read, write, edit, grep, ls, glob."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tau.builtins.tools.edit import EditTool, _render_edit_result
from tau.builtins.tools.glob import GlobTool
from tau.builtins.tools.grep import GrepTool
from tau.builtins.tools.ls import LsTool
from tau.builtins.tools.read import ReadTool
from tau.builtins.tools.terminal import TerminalTool
from tau.builtins.tools.utils import (
    OutputAccumulator,
    _digests,
    forget_digests,
    record_digests,
    split_lines,
    stamp_lines,
    verify_resolved,
)
from tau.builtins.tools.write import WriteTool
from tau.tool.types import ToolInvocation, ToolRenderOptions
from tau.utils.format import human_size


def _inv(name: str, cwd: Path | None = None, **params) -> ToolInvocation:
    return ToolInvocation(id="test-id", name=name, cwd=cwd, params=params)


def run(coro):
    return asyncio.run(coro)


def _anchor(line_number: int, content: str) -> str:
    """Tier-0 anchor, valid only when the content is unique in the file AND the
    file is short enough for 4-character tokens (<= 1024 lines).

    Duplicated content is salted from its neighbours, so this shortcut does not
    apply there — use _anchor_in, which stamps the real file.
    """
    stripped = content.strip()
    line_hash = "    " if not stripped else hashlib.md5(stripped.encode()).hexdigest()[:4]
    return f"{line_number}:{line_hash}"


def _seed(f: Path, as_read: str | None = None) -> None:
    """Record digests for a file as ``read`` would.

    ``edit`` verifies the line an anchor resolved to against what ``read``
    displayed there, and refuses when it has no record — an anchor is only
    meaningful against the read that produced it. Tests that write a file and
    edit it without reading have to stand in for that read.

    ``as_read`` is the content the anchor was taken from, when that differs from
    what is on disk now — a test simulating a file that moved underneath an
    anchor has to record the state the reader actually saw, not the state after
    the shift.
    """
    record_digests(f, split_lines(as_read if as_read is not None else f.read_text()))


def _anchor_in(text: str, line_number: int) -> str:
    """Real per-file anchor for targeting a specific line. Required whenever the
    file has repeated or blank lines, or is long enough to widen the token."""
    hashes = stamp_lines(text.splitlines())
    return f"{line_number}:{hashes[line_number - 1]}"


def _python_command(source: str) -> str:
    args = [sys.executable, "-u", "-c", source]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


# ---------------------------------------------------------------------------
# OutputAccumulator
# ---------------------------------------------------------------------------


def test_output_accumulator_preserves_full_truncated_output() -> None:
    accumulator = OutputAccumulator(
        max_bytes=8,
        max_lines=2,
        temp_file_prefix="tau-test-output-",
    )
    complete = b"one\ntwo\nthree\n"

    accumulator.append(complete)
    snapshot = accumulator.finish()

    assert snapshot.truncated
    assert snapshot.total_bytes == len(complete)
    assert snapshot.full_output_path is not None
    full_output = Path(snapshot.full_output_path)
    try:
        assert full_output.read_bytes() == complete
    finally:
        full_output.unlink(missing_ok=True)


def test_output_accumulator_removes_unneeded_spill_file() -> None:
    accumulator = OutputAccumulator(
        max_bytes=100,
        max_lines=10,
        temp_file_prefix="tau-test-output-",
    )
    accumulator.append(b"complete")
    snapshot = accumulator.finish()

    assert not snapshot.truncated
    assert snapshot.full_output_path is None


# ---------------------------------------------------------------------------
# TerminalTool
# ---------------------------------------------------------------------------


class TestTerminalTool:
    def setup_method(self) -> None:
        self.tool = TerminalTool()

    def test_streams_initial_and_final_updates_with_throttling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tau.builtins.tools.terminal._UPDATE_INTERVAL_SECONDS", 10.0)
        updates = []

        async def on_update(result) -> None:
            updates.append(result)

        command = _python_command(
            "import time\nfor value in range(5):\n print(value, flush=True)\n time.sleep(0.02)\n"
        )
        result = run(
            self.tool.execute(
                _inv("terminal", cmd=command),
                tool_execution_update_callback=on_update,
            )
        )

        assert not result.is_error
        assert updates[0].content == ""
        assert updates[-1].content == result.content
        assert updates[-1].metadata["running"] is False
        assert len(updates) == 2

    def test_truncated_output_is_saved_to_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tau.builtins.tools.terminal._MAX_OUTPUT_BYTES", 16)
        command = _python_command("print('abcdefghijklmnopqrstuvwxyz', flush=True)")

        result = run(self.tool.execute(_inv("terminal", cmd=command)))

        assert result.metadata["truncated"] is True
        full_output_path = result.metadata["full_output_path"]
        assert full_output_path is not None
        full_output = Path(full_output_path)
        try:
            assert full_output.read_text(encoding="utf-8") == "abcdefghijklmnopqrstuvwxyz\n"
            assert str(full_output) in result.content
        finally:
            full_output.unlink(missing_ok=True)

    def test_abort_terminates_running_process_tree(self) -> None:
        abort = asyncio.Event()
        command = _python_command("import time; time.sleep(30)")

        async def execute_and_abort():
            task = asyncio.create_task(
                self.tool.execute(
                    _inv("terminal", cmd=command),
                    signal=abort,
                )
            )
            await asyncio.sleep(0.05)
            abort.set()
            return await asyncio.wait_for(task, timeout=2)

        result = run(execute_and_abort())

        assert result.is_error
        assert result.metadata["cancelled"] is True

    def test_defaults_to_bash_not_posix_sh(self):
        # asyncio.create_subprocess_shell always runs /bin/sh on POSIX, which on
        # many distros is a strict shell that rejects bash-only syntax like
        # [[ ]]. Without an explicit shell_path setting, the tool must still
        # resolve to bash rather than falling through to sh.
        result = run(self.tool.execute(_inv("terminal", cmd="[[ 1 -eq 1 ]] && echo bash_works")))

        assert not result.is_error
        assert "bash_works" in result.content


# ---------------------------------------------------------------------------
# ReadTool
# ---------------------------------------------------------------------------


class TestReadTool:
    def setup_method(self):
        self.tool = ReadTool()

    def test_reads_file_with_line_numbers(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("line one\nline two\nline three\n")
        result = run(self.tool.execute(_inv("read", path=str(f))))
        assert not result.is_error
        assert f"{_anchor(1, 'line one')}|line one" in result.content
        assert f"{_anchor(2, 'line two')}|line two" in result.content

    def test_file_not_found(self, tmp_path):
        result = run(self.tool.execute(_inv("read", path=str(tmp_path / "nope.txt"))))
        assert result.is_error
        assert "not found" in result.content.lower()

    def test_not_a_file(self, tmp_path):
        result = run(self.tool.execute(_inv("read", path=str(tmp_path))))
        assert result.is_error

    def test_offset_and_limit(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"line {i}" for i in range(1, 11)))
        result = run(self.tool.execute(_inv("read", path=str(f), offset=2, limit=3)))
        assert not result.is_error
        assert f"{_anchor(3, 'line 3')}|line 3" in result.content
        assert f"{_anchor(5, 'line 5')}|line 5" in result.content
        assert f"{_anchor(6, 'line 6')}|line 6" not in result.content

    def test_truncation_metadata(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"line {i}" for i in range(1, 101)))
        result = run(self.tool.execute(_inv("read", path=str(f), limit=5)))
        assert result.metadata["truncated"] is True
        assert result.metadata["lines_returned"] == 5
        assert "offset=5" in result.content

    def test_metadata_total_lines(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\n")
        result = run(self.tool.execute(_inv("read", path=str(f))))
        assert result.metadata["total_lines"] == 3

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = run(self.tool.execute(_inv("read", path=str(f))))
        assert not result.is_error
        assert result.metadata["lines_returned"] == 0

    def test_repeated_lines_get_distinct_anchors(self, tmp_path):
        f = tmp_path / "dup.txt"
        f.write_text("foo\nfoo\nfoo\n")
        result = run(self.tool.execute(_inv("read", path=str(f))))
        assert not result.is_error
        anchors = [raw.split("|", 1)[0] for raw in result.content.splitlines() if "|" in raw]
        assert len(set(anchors)) == len(anchors) == 3

    def test_anchor_for_a_line_is_stable_regardless_of_chunk_offset(self, tmp_path):
        """The same absolute line must get the same anchor whether it's read
        as part of the whole file or as part of an offset chunk — read hashes
        the full file before slicing so chunk boundaries can't change it."""
        f = tmp_path / "dup.txt"
        f.write_text("foo\n" * 6)
        full = run(self.tool.execute(_inv("read", path=str(f))))
        chunk = run(self.tool.execute(_inv("read", path=str(f), offset=3, limit=2)))

        def anchor_for_line(content: str, line_number: int) -> str:
            for raw in content.splitlines():
                if "|" not in raw:
                    continue
                anchor, _, _ = raw.partition("|")
                if anchor.startswith(f"{line_number}:"):
                    return anchor
            raise AssertionError(f"line {line_number} not found")

        assert anchor_for_line(full.content, 4) == anchor_for_line(chunk.content, 4)


# ---------------------------------------------------------------------------
# WriteTool
# ---------------------------------------------------------------------------


class TestWriteTool:
    def setup_method(self):
        self.tool = WriteTool()

    def test_writes_new_file(self, tmp_path):
        p = tmp_path / "out.txt"
        result = run(self.tool.execute(_inv("write", path=str(p), content="hello world\n")))
        assert not result.is_error
        assert p.read_text() == "hello world\n"

    def test_overwrites_existing_file(self, tmp_path):
        p = tmp_path / "existing.txt"
        p.write_text("old content")
        run(self.tool.execute(_inv("write", path=str(p), content="new content")))
        assert p.read_text() == "new content"

    def test_creates_parent_directories(self, tmp_path):
        p = tmp_path / "a" / "b" / "c.txt"
        result = run(self.tool.execute(_inv("write", path=str(p), content="deep")))
        assert not result.is_error
        assert p.exists()

    def test_metadata_created_flag_new(self, tmp_path):
        p = tmp_path / "new.txt"
        result = run(self.tool.execute(_inv("write", path=str(p), content="x")))
        assert result.metadata["created"] is True

    def test_metadata_created_flag_overwrite(self, tmp_path):
        p = tmp_path / "old.txt"
        p.write_text("y")
        result = run(self.tool.execute(_inv("write", path=str(p), content="x")))
        assert result.metadata["created"] is False

    def test_metadata_total_lines(self, tmp_path):
        p = tmp_path / "lines.txt"
        result = run(self.tool.execute(_inv("write", path=str(p), content="a\nb\nc")))
        assert result.metadata["total_lines"] == 3


# ---------------------------------------------------------------------------
# EditTool
# ---------------------------------------------------------------------------


class TestEditTool:
    def setup_method(self):
        self.tool = EditTool()

    def test_result_diff_is_always_expanded(self):
        assert self.tool.result_expandable is False

    def test_replaces_single_anchored_line(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def old_name():\n    pass\n")
        _seed(f)
        anchor = _anchor(1, "def old_name():")
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content="def new_name():",
                )
            )
        )
        assert not result.is_error
        assert "new_name" in f.read_text()

    def test_accepts_legacy_content_parameter(self, tmp_path):
        f = tmp_path / "legacy.py"
        f.write_text("old\n")
        _seed(f)
        anchor = _anchor(1, "old")
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    content="new",
                )
            )
        )
        assert not result.is_error
        assert f.read_text() == "new\n"

    def test_file_not_found(self, tmp_path):
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(tmp_path / "missing.py"),
                    start_anchor="1:9dd4",
                    end_anchor="1:9dd4",
                    new_content="y",
                )
            )
        )
        assert result.is_error
        assert "not found" in result.content.lower()

    def test_anchor_not_found(self, tmp_path):
        f = tmp_path / "f.py"
        f.write_text("hello world")
        _seed(f)
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor="1:d16f",
                    end_anchor="1:d16f",
                    new_content="abc",
                )
            )
        )
        assert result.is_error
        assert "not found" in result.content.lower()
        assert "Current file content near hinted line 1:" in result.content
        assert f"{_anchor(1, 'hello world')}|hello world" in result.content
        assert "Re-read the relevant range" in result.content

    def test_line_number_params_get_actionable_hint(self):
        """Observed failure mode: model retries with line_start/line_end instead
        of start_anchor/end_anchor. The bare Pydantic error never explains the
        correct format, so add a concrete example rather than just "Field
        required"."""
        ok, errors = self.tool.validate({"path": "f.py", "line_start": 1, "line_end": 2})
        assert not ok
        assert any("start_anchor" in e for e in errors)
        assert any("hashline anchors" in e and "12:a3f1" in e for e in errors)

    def test_malformed_anchor_gets_actionable_hint(self):
        """Observed failure mode: anchor missing the ':' separator (e.g. '311a')."""
        ok, errors = self.tool.validate(
            {"path": "f.py", "start_anchor": "311a", "end_anchor": "311a", "new_content": "x"}
        )
        assert not ok
        assert any("hashline anchors" in e for e in errors)

    def test_valid_params_have_no_hint_appended(self):
        ok, errors = self.tool.validate(
            {
                "path": "f.py",
                "start_anchor": "1:aaaa",
                "end_anchor": "1:aaaa",
                "new_content": "x",
            }
        )
        assert ok
        assert errors == []

    def test_repeated_lines_get_distinct_anchors_and_edit_precisely(self, tmp_path):
        """Perfect hashing: identical lines no longer share an anchor, so an
        edit lands on exactly the targeted line rather than being resolved by
        line-number proximity — check the middle occurrence specifically,
        since a proximity guess would also happen to get the last one right."""
        f = tmp_path / "dup.py"
        text = "foo\nfoo\nfoo\n"
        f.write_text(text)
        _seed(f)
        anchor = _anchor_in(text, 2)
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content="bar",
                )
            )
        )
        assert not result.is_error
        assert f.read_text() == "foo\nbar\nfoo\n"

    def test_blank_lines_get_distinct_anchors(self, tmp_path):
        f = tmp_path / "blanks.py"
        text = "a\n\n\nb\n"
        f.write_text(text)
        _seed(f)
        anchor = _anchor_in(text, 3)
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content="filled",
                )
            )
        )
        assert not result.is_error
        assert f.read_text() == "a\n\nfilled\nb\n"

    def test_replaces_anchored_range(self, tmp_path):
        f = tmp_path / "rep.py"
        f.write_text("one\ntwo\nthree\nfour\n")
        _seed(f)
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=_anchor(2, "two"),
                    end_anchor=_anchor(3, "three"),
                    new_content="replacement",
                )
            )
        )
        assert not result.is_error
        assert f.read_text() == "one\nreplacement\nfour\n"

    def test_anchor_survives_shifted_lines(self, tmp_path):
        f = tmp_path / "shifted.py"
        f.write_text("inserted\none\ntwo\nthree\n")
        # The anchor came from a read of the file BEFORE "inserted" was
        # prepended, when "two" was line 2 — that is the shift being tested.
        _seed(f, "one\ntwo\nthree\n")
        old_anchor = _anchor(2, "two")
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=old_anchor,
                    end_anchor=old_anchor,
                    new_content="changed",
                )
            )
        )
        assert not result.is_error
        assert f.read_text() == "inserted\none\nchanged\nthree\n"

    def test_diff_metadata(self, tmp_path):
        f = tmp_path / "diff.py"
        f.write_text("hello world\n")
        _seed(f)
        anchor = _anchor(1, "hello world")
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content="goodbye world",
                )
            )
        )
        assert not result.is_error
        assert result.metadata["lines_added"] >= 1
        assert result.metadata["lines_removed"] >= 1

    def test_diff_renderer_includes_old_and_new_hashline_anchors(self, tmp_path):
        f = tmp_path / "diff.py"
        f.write_text("before\nold value\nafter\n")
        _seed(f)
        anchor = _anchor(2, "old value")
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content="new value",
                )
            )
        )

        rendered = "\n".join(
            _render_edit_result(
                result.content,
                ToolRenderOptions(metadata=result.metadata),
            )
        )

        assert f"{_anchor(2, 'old value')}  -  old value" in rendered
        assert f"{_anchor(2, 'new value')}  +  new value" in rendered
        assert f"{_anchor(1, 'before')}     before" in rendered

    def test_diff_renderer_collapses_only_distant_context(self, tmp_path):
        f = tmp_path / "diff.py"
        original_lines = [f"line {number}" for number in range(1, 16)]
        f.write_text("\n".join(original_lines) + "\n")
        _seed(f)
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=_anchor(8, "line 8"),
                    end_anchor=_anchor(8, "line 8"),
                    new_content="changed line",
                )
            )
        )

        collapsed = "\n".join(
            _render_edit_result(
                result.content,
                ToolRenderOptions(metadata=result.metadata),
            )
        )

        assert "line 4" not in collapsed
        assert "line 5" in collapsed
        assert "line 11" in collapsed
        assert "line 12" not in collapsed
        assert collapsed.count("… (+4 lines)") == 2
        assert "changed line" in collapsed
        assert "ctrl+o to expand" in collapsed

        expanded = "\n".join(
            _render_edit_result(
                result.content,
                ToolRenderOptions(expanded=True, metadata=result.metadata),
            )
        )

        assert "line 1" in expanded
        assert "line 15" in expanded
        assert "… (+" not in expanded
        assert "ctrl+o to collapse" in expanded

    def test_not_a_file(self, tmp_path):
        result = run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(tmp_path),
                    start_anchor="1:0cc1",
                    end_anchor="1:0cc1",
                    new_content="b",
                )
            )
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# GrepTool
# ---------------------------------------------------------------------------


class TestGrepTool:
    def setup_method(self):
        self.tool = GrepTool()

    def test_finds_pattern_in_file(self, tmp_path):
        f = tmp_path / "src.py"
        f.write_text("def hello():\n    return 42\n")
        result = run(
            self.tool.execute(_inv("grep", cwd=tmp_path, pattern="def hello", path=str(f)))
        )
        assert not result.is_error
        assert result.metadata["match_count"] == 1
        assert "def hello" in result.content

    def test_no_matches(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("nothing here\n")
        result = run(self.tool.execute(_inv("grep", cwd=tmp_path, pattern="NOTFOUND", path=str(f))))
        assert not result.is_error
        assert result.metadata["match_count"] == 0

    def test_searches_directory_recursively(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.py").write_text("SECRET_VALUE = 1\n")
        (tmp_path / "b.py").write_text("no match\n")
        result = run(
            self.tool.execute(
                _inv("grep", cwd=tmp_path, pattern="SECRET_VALUE", path=str(tmp_path))
            )
        )
        assert result.metadata["match_count"] == 1

    def test_case_insensitive(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("Hello World\n")
        result = run(
            self.tool.execute(
                _inv("grep", cwd=tmp_path, pattern="hello world", path=str(f), case_sensitive=False)
            )
        )
        assert result.metadata["match_count"] == 1

    def test_invalid_regex(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x\n")
        result = run(self.tool.execute(_inv("grep", cwd=tmp_path, pattern="[invalid", path=str(f))))
        assert result.is_error
        assert "regex parse error" in result.content.lower()

    def test_path_not_found(self, tmp_path):
        result = run(
            self.tool.execute(_inv("grep", cwd=tmp_path, pattern="x", path=str(tmp_path / "nope")))
        )
        assert result.is_error

    def test_include_filter(self, tmp_path):
        (tmp_path / "a.py").write_text("match here\n")
        (tmp_path / "b.txt").write_text("match here\n")
        result = run(
            self.tool.execute(
                _inv("grep", cwd=tmp_path, pattern="match here", path=str(tmp_path), include="*.py")
            )
        )
        assert result.metadata["match_count"] == 1

    def test_errors_when_rg_is_absent(self, tmp_path, monkeypatch):
        async def fake_exec(*cmd, **kwargs):
            if cmd[0] == "rg":
                raise FileNotFoundError
            raise AssertionError(f"Unexpected command: {cmd}")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        f = tmp_path / "f.py"
        f.write_text("TARGET_TOKEN = 1\n")
        result = run(
            self.tool.execute(_inv("grep", cwd=tmp_path, pattern="TARGET_TOKEN", path=str(f)))
        )
        assert result.is_error
        assert "ripgrep" in result.content.lower()


# ---------------------------------------------------------------------------
# LsTool
# ---------------------------------------------------------------------------


class TestLsTool:
    def setup_method(self):
        self.tool = LsTool()

    def test_lists_files_and_dirs(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        (tmp_path / "subdir").mkdir()
        result = run(self.tool.execute(_inv("ls", cwd=tmp_path, path=str(tmp_path))))
        assert not result.is_error
        assert result.metadata["file_count"] == 1
        assert result.metadata["dir_count"] == 1

    def test_empty_directory(self, tmp_path):
        result = run(self.tool.execute(_inv("ls", cwd=tmp_path, path=str(tmp_path))))
        assert not result.is_error
        assert result.metadata["file_count"] == 0
        assert result.metadata["dir_count"] == 0

    def test_path_not_found(self, tmp_path):
        result = run(self.tool.execute(_inv("ls", cwd=tmp_path, path=str(tmp_path / "nope"))))
        assert result.is_error

    def test_path_is_file_not_dir(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        result = run(self.tool.execute(_inv("ls", cwd=tmp_path, path=str(f))))
        assert result.is_error

    def test_entries_metadata(self, tmp_path):
        (tmp_path / "alpha.py").write_text("x" * 100)
        result = run(self.tool.execute(_inv("ls", cwd=tmp_path, path=str(tmp_path))))
        entries = result.metadata["entries"]
        assert len(entries) == 1
        assert entries[0]["name"] == "alpha.py"
        assert entries[0]["is_dir"] is False


class TestHumanSize:
    def test_bytes(self):
        assert human_size(0) == "0B"
        assert human_size(500) == "500B"

    def test_kilobytes(self):
        assert human_size(1024) == "1.0KB"
        assert human_size(2048) == "2.0KB"

    def test_megabytes(self):
        assert human_size(1024 * 1024) == "1.0MB"

    def test_gigabytes(self):
        assert human_size(1024**3) == "1.0GB"

    def test_terabytes(self):
        assert human_size(1024**4) == "1.0TB"


# ---------------------------------------------------------------------------
# GlobTool
# ---------------------------------------------------------------------------


class TestGlobTool:
    def setup_method(self):
        self.tool = GlobTool()

    def test_finds_matching_files(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        result = run(
            self.tool.execute(_inv("glob", cwd=tmp_path, pattern="*.py", path=str(tmp_path)))
        )
        assert not result.is_error
        assert result.metadata["match_count"] == 2

    def test_recursive_glob(self, tmp_path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "mod.py").write_text("")
        (tmp_path / "top.py").write_text("")
        result = run(
            self.tool.execute(_inv("glob", cwd=tmp_path, pattern="**/*.py", path=str(tmp_path)))
        )
        assert result.metadata["match_count"] == 2

    def test_no_matches(self, tmp_path):
        result = run(
            self.tool.execute(_inv("glob", cwd=tmp_path, pattern="*.xyz", path=str(tmp_path)))
        )
        assert not result.is_error
        assert result.metadata["match_count"] == 0

    def test_base_path_not_a_dir(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        result = run(self.tool.execute(_inv("glob", cwd=tmp_path, pattern="*", path=str(f))))
        assert result.is_error

    def test_result_content_has_paths(self, tmp_path):
        (tmp_path / "x.py").write_text("")
        result = run(
            self.tool.execute(_inv("glob", cwd=tmp_path, pattern="*.py", path=str(tmp_path)))
        )
        assert "x.py" in result.content

    def test_errors_when_rg_is_absent(self, tmp_path, monkeypatch):
        async def fake_exec(*cmd, **kwargs):
            if cmd[0] == "rg":
                raise FileNotFoundError
            raise AssertionError(f"Unexpected command: {cmd}")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        result = run(
            self.tool.execute(_inv("glob", cwd=tmp_path, pattern="*.py", path=str(tmp_path)))
        )
        assert result.is_error
        assert "ripgrep" in result.content.lower()


class TestReadDoesNotBlockTheEventLoop:
    """read's decode/split/hash tail is CPU-bound and scales with file size.
    It must run on a worker thread like edit and write already do, or a
    multi-MiB file freezes rendering and input for its whole duration.
    """

    def test_event_loop_keeps_ticking_during_a_large_read(self, tmp_path):
        import asyncio

        big = tmp_path / "big.py"
        big.write_text("\n".join(f"row {i % 997}" for i in range(40_000)))

        async def scenario():
            ticks = 0

            async def heartbeat():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.001)
                    ticks += 1

            hb = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.05)
            ticks = 0
            result = await ReadTool().execute(
                _inv("read", cwd=tmp_path, path=str(big), limit=20)
            )
            hb.cancel()
            return ticks, result

        ticks, result = asyncio.run(scenario())
        assert not result.is_error
        assert ticks > 0, "event loop never ran during read — the sync tail is back on the loop"


class TestEditPreservesFileShape:
    """Three pre-existing bugs, all from one root cause: edit split with
    str.splitlines and rejoined with "\\n", and decoded with strict UTF-8 while
    catching only OSError.
    """

    tool = EditTool()

    def _anchor_at(self, tmp_path, f, index):
        out = run(ReadTool().execute(_inv("read", cwd=tmp_path, path=str(f))))
        rows = [
            line.split("|")[0]
            for line in out.content.splitlines()
            if "|" in line and line.split("|")[0].split(":")[0].isdigit()
        ]
        return rows[index]

    def test_crlf_endings_survive_an_edit(self, tmp_path):
        """Normalising CRLF to LF turns a one-line change into a whole-file diff
        on a Windows checkout."""
        f = tmp_path / "a.py"
        f.write_bytes(b"l0\r\nl1\r\nl2\r\n")
        anchor = self._anchor_at(tmp_path, f, 1)
        result = run(
            self.tool.execute(
                _inv("edit", cwd=tmp_path, path=str(f), start_anchor=anchor,
                     end_anchor=anchor, new_content="X")
            )
        )
        assert not result.is_error
        assert f.read_bytes() == b"l0\r\nX\r\nl2\r\n"

    def test_cr_only_endings_survive(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_bytes(b"l0\rl1\rl2\r")
        anchor = self._anchor_at(tmp_path, f, 1)
        run(
            self.tool.execute(
                _inv("edit", cwd=tmp_path, path=str(f), start_anchor=anchor,
                     end_anchor=anchor, new_content="X")
            )
        )
        assert f.read_bytes() == b"l0\rX\rl2\r"

    def test_form_feed_is_not_turned_into_a_newline(self, tmp_path):
        """str.splitlines breaks on \\x0c, so editing an unrelated line used to
        replace the form feed with a newline — silent corruption of files that
        use it as a page separator."""
        f = tmp_path / "a.py"
        f.write_bytes(b"first\nmiddle\x0ctail\nlast\n")
        anchor = self._anchor_at(tmp_path, f, 0)
        run(
            self.tool.execute(
                _inv("edit", cwd=tmp_path, path=str(f), start_anchor=anchor,
                     end_anchor=anchor, new_content="FIRST")
            )
        )
        assert f.read_bytes() == b"FIRST\nmiddle\x0ctail\nlast\n"

    def test_missing_trailing_newline_is_not_invented(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("l0\nl1\nl2")
        anchor = self._anchor_at(tmp_path, f, 1)
        run(
            self.tool.execute(
                _inv("edit", cwd=tmp_path, path=str(f), start_anchor=anchor,
                     end_anchor=anchor, new_content="X")
            )
        )
        assert f.read_text() == "l0\nX\nl2"

    def test_trailing_newline_is_kept_when_the_last_line_is_replaced(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("l0\nl1\n")
        anchor = self._anchor_at(tmp_path, f, 1)
        run(
            self.tool.execute(
                _inv("edit", cwd=tmp_path, path=str(f), start_anchor=anchor,
                     end_anchor=anchor, new_content="X")
            )
        )
        assert f.read_text() == "l0\nX\n"

    def test_non_utf8_file_is_refused_not_crashed(self, tmp_path):
        """read tolerates such a file with errors="replace", so its anchors
        describe replacement characters rather than the bytes on disk. edit used
        to raise UnicodeDecodeError from read_text; it must refuse cleanly and
        leave the file alone."""
        f = tmp_path / "a.py"
        f.write_bytes(b"caf\xe9\nsecond\n")
        original = f.read_bytes()
        result = run(
            self.tool.execute(
                _inv("edit", cwd=tmp_path, path=str(f), start_anchor="1:abcd",
                     end_anchor="1:abcd", new_content="X")
            )
        )
        assert result.is_error
        assert "utf-8" in result.content.lower()
        assert f.read_bytes() == original


class TestEditVerification:
    """``edit`` checks the line an anchor resolved to against what ``read``
    displayed there.

    ``resolve_anchor`` answers "which line carries this token". That is not the
    same question as "is this the line the caller was looking at", and only the
    second catches a token collision: once the whole token matches, every
    content-derived part of it matches too, so no width can separate the two
    lines. The digest is compared against the line instead of the anchor, which
    is why it sees what the token cannot.
    """

    def setup_method(self):
        self.tool = EditTool()
        forget_digests()

    def _edit(self, f, anchor, new_content="changed"):
        return run(
            self.tool.execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content=new_content,
                )
            )
        )

    def test_refuses_a_file_that_was_never_read(self, tmp_path):
        """An anchor can only have come from a read. Without one there is no
        record to check against, and a 4-hex token has no width behind it to
        fall back on, so proceeding would be guessing."""
        f = tmp_path / "unread.py"
        f.write_text("def old():\n    pass\n")
        result = self._edit(f, _anchor(1, "def old():"))
        assert result.is_error
        assert "no record" in result.content
        assert f.read_text() == "def old():\n    pass\n", "refusal must not touch the file"

    def test_refuses_after_a_restart(self, tmp_path):
        """The one path no harness covers: the anchor is still live, but the
        process that displayed it is gone. Dropping the digests is what a
        restart does to this store."""
        f = tmp_path / "restart.py"
        f.write_text("def old():\n    pass\n")
        _seed(f)
        anchor = _anchor(1, "def old():")
        assert not self._edit(f, anchor).is_error

        f.write_text("def old():\n    pass\n")
        forget_digests()  # the restart
        result = self._edit(f, anchor)
        assert result.is_error
        assert "re-read" in result.content

    def test_refuses_when_the_resolved_line_says_something_else(self, tmp_path):
        """The collision case, made deterministic. The token resolves, but the
        line it resolves to is not the line the reader was shown — which is
        exactly what a colliding anchor looks like from inside edit."""
        f = tmp_path / "moved.py"
        f.write_text("alpha\nbeta\n")
        # Recorded as though read had shown something different at line 1.
        _seed(f, "gamma\nbeta\n")
        result = self._edit(f, _anchor(1, "alpha"))
        assert result.is_error
        assert "does not match" in result.content
        assert f.read_text() == "alpha\nbeta\n"

    def test_several_edits_from_one_read(self, tmp_path):
        """The pattern that actually happens: read once, edit repeatedly. Later
        edits carry anchors from the original read, and the line numbers have
        moved underneath them — verification must not reject those."""
        f = tmp_path / "many.py"
        f.write_text("one\ntwo\nthree\nfour\n")
        run(ReadTool().execute(_inv("read", path=str(f))))  # the real read path

        text = "one\ntwo\nthree\nfour\n"
        for target, replacement in ((1, "ONE\nextra"), (3, "THREE"), (4, "FOUR")):
            anchor = _anchor_in(text, target)
            result = self._edit(f, anchor, replacement)
            assert not result.is_error, f"line {target}: {result.content}"

        assert f.read_text() == "ONE\nextra\ntwo\nTHREE\nFOUR\n"

    def test_verify_resolved_reports_absence_distinctly(self, tmp_path):
        """None means "no record", False means "wrong line". The caller refuses
        on both, but for different reasons and with different messages."""
        f = tmp_path / "x.py"
        assert verify_resolved(f, 1, "anything") is None
        record_digests(f, ["alpha", "beta"])
        assert verify_resolved(f, 1, "alpha") is True
        assert verify_resolved(f, 1, "beta") is False
        assert verify_resolved(f, 99, "alpha") is None


class TestReadDisplaysStructureBreakingCharacters:
    """``str.splitlines`` breaks on form feed, vertical tab and the Unicode line
    separators; ``utils._LINE_BREAK`` deliberately does not.

    Emitted raw, those characters break the read format's one-anchor-per-line
    invariant — the line is shown as an anchored empty line followed by a
    phantom line with no anchor — and they are invisible, so a model rewriting
    the line drops them without knowing. The write side of this was fixed
    separately; this is the display side.
    """

    def setup_method(self):
        self.tool = ReadTool()

    def _read(self, f):
        return run(self.tool.execute(_inv("read", path=str(f)))).content

    def test_form_feed_does_not_create_a_phantom_line(self, tmp_path):
        f = tmp_path / "page.c"
        f.write_bytes(b"int main(void)\n{\n}\n\x0c\nstatic void helper(void)\n{\n}\n")
        body = self._read(f).split("\n\n")[0].splitlines()
        assert len(body) == 7, body
        assert all("|" in line for line in body), "every displayed line must carry an anchor"

    def test_the_character_is_visible(self, tmp_path):
        f = tmp_path / "page.c"
        f.write_bytes(b"a\n\x0c\nb\n")
        assert "|\\f" in self._read(f)

    def test_the_escape_is_explained(self, tmp_path):
        f = tmp_path / "page.c"
        f.write_bytes(b"a\n\x0c\nb\n")
        out = self._read(f)
        assert "shown escaped" in out
        # It must not claim the escape can be written back: edit writes exactly
        # what it is given, so rewriting the line replaces one byte with two.
        assert "REPLACES the real character" in out

    def test_ordinary_files_get_no_such_footer(self, tmp_path):
        f = tmp_path / "plain.py"
        f.write_text("import os\n\n\ndef main():\n    pass\n")
        assert "shown escaped" not in self._read(f)

    def test_the_anchor_still_addresses_the_real_line(self, tmp_path):
        """The escape is display-only: anchors and digests are computed over the
        true content, so the line stays editable like any other."""
        import re

        f = tmp_path / "page.c"
        f.write_bytes(b"alpha\n\x0c\nbeta\n")
        out = self._read(f)
        anchor = re.match(r"^(\d+:[0-9a-z]+)\|", out.splitlines()[1]).group(1)
        result = run(
            EditTool().execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content="PAGE BREAK WAS HERE",
                )
            )
        )
        assert not result.is_error, result.content
        assert f.read_bytes() == b"alpha\nPAGE BREAK WAS HERE\nbeta\n"


class TestTwinsAreSeparatedByContext:
    """Two content-identical lines cannot be told apart by anything the anchor
    carries — token, hash and per-line digest all agree by construction.

    What still differs is what sat AROUND the one the reader saw, and ``read``
    retained exactly that. Scored as an unbroken run of agreement counted
    outward from the anchor: the original of a copied block agrees immediately
    above and diverges further up, while a decoy diverges immediately.
    """

    def setup_method(self):
        forget_digests()

    def _run(self, tmp_path, before, after, line, new="EDITED"):
        f = tmp_path / "twin.py"
        f.write_text("\n".join(before) + "\n")
        out = run(ReadTool().execute(_inv("read", path=str(f)))).content
        anchor = re.match(r"^(\d+:[0-9a-z]+)\|", out.splitlines()[line - 1]).group(1)
        f.write_text("\n".join(after) + "\n")
        result = run(
            EditTool().execute(
                _inv(
                    "edit",
                    path=str(f),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content=new,
                )
            )
        )
        if result.is_error:
            return None, f.read_text()
        return f.read_text().splitlines().index(new) + 1, f.read_text()

    SAVE = ["def save(p):", "    if p is None:", "        return None", "    return write(p)"]
    BOTH = [
        "def load(p):", "    if p is None:", "        return None", "    return read(p)", "",
        "def save(p):", "    if p is None:", "        return None", "    return write(p)",
    ]

    def test_the_founding_reproduction_now_resolves(self, tmp_path):
        """Read a four-line save(), anchor its `return None`, then add a load()
        helper ABOVE carrying an identical line. This edited the decoy silently
        before neighbour salting, and was refused outright before context."""
        got, _ = self._run(tmp_path, self.SAVE, self.BOTH, 3)
        assert got == 8, "must edit save()'s line, not the copy in load()"

    def test_it_resolves_with_a_full_context_window_too(self, tmp_path):
        pad = ["import os", "import sys", ""]
        got, _ = self._run(tmp_path, pad + self.SAVE, pad + self.BOTH, 6)
        assert got == 11

    def test_identical_neighbourhoods_are_refused(self, tmp_path):
        """Both candidates sit in the same surroundings, so the evidence does
        not separate them and no amount of context will. Refusing costs a
        re-read; choosing would be a coin flip on a file."""
        before = ["a()", "    x = 1", "        return None", "b()"]
        after = ["a()", "    x = 1", "        return None",
                 "a()", "    x = 1", "        return None", "b()"]
        got, text = self._run(tmp_path, before, after, 3)
        assert got is None
        assert "EDITED" not in text, "a refusal must leave the file untouched"

    def test_an_anchor_with_no_comparable_context_is_refused(self, tmp_path):
        """The anchored line was the first in the file, so the reader saw
        nothing above it. Zero comparisons all agreeing must resolve nothing."""
        got, _ = self._run(
            tmp_path,
            ["        return None", "t()"],
            ["        return None", "m()", "        return None", "t()"],
            1,
        )
        assert got is None


class TestPathSpellingDoesNotMatter:
    """``read`` and ``edit`` must agree on which file they are talking about.

    They do not agree on how to SPELL it: ``glob`` and ``grep`` return fully
    resolved paths while ``resolve_tool_path`` leaves symlinks alone, and on
    macOS ``/tmp`` and ``/var`` are themselves symlinks. So the ordinary sequence
    "grep for a symbol, read the hit, edit the path the user typed" reaches one
    file by two names — and a store keyed on the raw path would refuse the edit
    for want of a record filed under the other one.
    """

    def setup_method(self):
        forget_digests()

    def _read(self, p):
        return run(ReadTool().execute(_inv("read", path=str(p)))).content

    def _edit(self, p, anchor, new="EDITED"):
        return run(
            EditTool().execute(
                _inv("edit", path=str(p), start_anchor=anchor, end_anchor=anchor, new_content=new)
            )
        )

    def _link_pair(self, tmp_path):
        real = tmp_path / "pkg"
        real.mkdir()
        (real / "mod.py").write_text("import os\nvalue = 1\nother = 2\n")
        link = tmp_path / "linked"
        link.symlink_to(real)
        return real / "mod.py", link / "mod.py"

    def test_read_real_then_edit_through_a_symlink(self, tmp_path):
        real, linked = self._link_pair(tmp_path)
        anchor = re.match(r"^(\d+:[0-9a-z]+)\|", self._read(real).splitlines()[1]).group(1)
        result = self._edit(linked, anchor)
        assert not result.is_error, result.content
        assert "EDITED" in real.read_text()

    def test_read_through_a_symlink_then_edit_real(self, tmp_path):
        real, linked = self._link_pair(tmp_path)
        anchor = re.match(r"^(\d+:[0-9a-z]+)\|", self._read(linked).splitlines()[1]).group(1)
        result = self._edit(real, anchor)
        assert not result.is_error, result.content
        assert "EDITED" in real.read_text()

    def test_two_names_are_not_two_records(self, tmp_path):
        """Reading via both spellings must not consume two slots in the store,
        or a caller alternating between them would evict its own evidence."""
        real, linked = self._link_pair(tmp_path)
        self._read(real)
        self._read(linked)
        assert len(_digests) == 1, f"one file, {len(_digests)} records"


class TestSymlinkTargetsAreFollowed:
    """``os.replace`` renames ONTO the path it is given, so an atomic write to a
    symlink replaced the link with a regular file and left the real file
    untouched — the change looked applied and landed nowhere.

    Reachable through ``write`` on any branch, and through ``edit`` once the
    digest store learned that two spellings name one file. Symlinked config, a
    linked package in a monorepo, and anything under ``/tmp`` on macOS (itself a
    symlink) all hit it.
    """

    def setup_method(self):
        forget_digests()

    def _linked(self, tmp_path, body):
        real = tmp_path / "real.txt"
        real.write_text(body)
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        return real, link

    def test_write_through_a_symlink_updates_the_target(self, tmp_path):
        real, link = self._linked(tmp_path, "original\n")
        result = run(WriteTool().execute(_inv("write", path=str(link), content="new\n")))
        assert not result.is_error
        assert link.is_symlink(), "the symlink was replaced by a regular file"
        assert real.read_text() == "new\n", "the write was orphaned"

    def test_edit_through_a_symlink_updates_the_target(self, tmp_path):
        real, link = self._linked(tmp_path, "alpha\nbeta\ngamma\n")
        out = run(ReadTool().execute(_inv("read", path=str(real)))).content
        anchor = re.match(r"^(\d+:[0-9a-z]+)\|", out.splitlines()[1]).group(1)
        result = run(
            EditTool().execute(
                _inv(
                    "edit",
                    path=str(link),
                    start_anchor=anchor,
                    end_anchor=anchor,
                    new_content="BETA",
                )
            )
        )
        assert not result.is_error, result.content
        assert link.is_symlink()
        assert real.read_text() == "alpha\nBETA\ngamma\n"

    def test_an_ordinary_file_is_unaffected(self, tmp_path):
        f = tmp_path / "plain.txt"
        f.write_text("a\n")
        run(WriteTool().execute(_inv("write", path=str(f), content="b\n")))
        assert f.read_text() == "b\n"
        assert not f.is_symlink()

    def test_a_symlink_to_a_missing_file_creates_the_target(self, tmp_path):
        missing = tmp_path / "notyet.txt"
        link = tmp_path / "link.txt"
        link.symlink_to(missing)
        run(WriteTool().execute(_inv("write", path=str(link), content="made\n")))
        assert missing.read_text() == "made\n"
        assert link.is_symlink()

"""Tests for tau/builtins/tools/ — read, write, edit, grep, ls, glob."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from tau.builtins.tools.edit import EditTool, _render_edit_result
from tau.builtins.tools.glob import GlobTool
from tau.builtins.tools.grep import GrepTool
from tau.builtins.tools.ls import LsTool
from tau.builtins.tools.read import ReadTool
from tau.builtins.tools.utils import human_size
from tau.builtins.tools.write import WriteTool
from tau.tool.types import ToolInvocation, ToolRenderOptions


def _inv(name: str, cwd: Path | None = None, **params) -> ToolInvocation:
    return ToolInvocation(id="test-id", name=name, cwd=cwd, params=params)


def run(coro):
    return asyncio.run(coro)


def _anchor(line_number: int, content: str) -> str:
    stripped = content.strip()
    line_hash = "    " if not stripped else hashlib.md5(stripped.encode()).hexdigest()[:4]
    return f"{line_number}:{line_hash}"


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

    def test_replaces_single_anchored_line(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def old_name():\n    pass\n")
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

    def test_duplicate_hash_uses_closest_line_hint(self, tmp_path):
        f = tmp_path / "dup.py"
        f.write_text("foo\nfoo\nfoo\n")
        anchor = _anchor(3, "foo")
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
        assert f.read_text() == "foo\nfoo\nbar\n"

    def test_replaces_anchored_range(self, tmp_path):
        f = tmp_path / "rep.py"
        f.write_text("one\ntwo\nthree\nfour\n")
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

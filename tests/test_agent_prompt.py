"""Tests for tau/agent/prompt/builder.py — prompt construction."""

from __future__ import annotations

import json
import platform
from pathlib import Path

from tau.agent.prompt.builder import (
    _DEFAULT_IDENTITY,
    _GENERAL_GUIDELINES,
    _PRECEDENCE_GUIDELINES,
    PromptBuilder,
    _detect_os,
    _detect_shell,
    _redact_remote_url,
    load_project_context_file,
    load_project_context_files,
)
from tau.agent.prompt.types import PromptOptions
from tau.builtins.tools.read import ReadTool
from tau.builtins.tools.write import WriteTool
from tau.settings.paths import get_docs_dir


def _opts(cwd: Path, **kwargs) -> PromptOptions:
    return PromptOptions(cwd=cwd, project_trusted=True, **kwargs)


# ---------------------------------------------------------------------------
# load_project_context_file
# ---------------------------------------------------------------------------


class TestLoadProjectContextFile:
    def test_returns_none_when_no_file(self, tmp_path):
        assert load_project_context_file(tmp_path) is None

    def test_loads_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Agent instructions\nDo stuff.")
        result = load_project_context_file(tmp_path)
        assert result is not None
        content, path = result
        assert "Agent instructions" in content
        assert path.name == "AGENTS.md"

    def test_loads_claude_md_when_no_agents_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Claude instructions")
        content, path = load_project_context_file(tmp_path)
        assert path.name == "CLAUDE.md"

    def test_agents_md_preferred_over_claude_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("agents content")
        (tmp_path / "CLAUDE.md").write_text("claude content")
        content, path = load_project_context_file(tmp_path)
        assert path.name == "AGENTS.md"

    def test_empty_file_returns_none(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("   ")
        assert load_project_context_file(tmp_path) is None

    def test_case_insensitive_detection(self, tmp_path):
        (tmp_path / "AgEnTs.Md").write_text("content")
        result = load_project_context_file(tmp_path)
        assert result is not None
        assert result[1].name == "AgEnTs.Md"

    def test_agents_file_takes_priority_over_claude_file(self, tmp_path):
        (tmp_path / "claude.md").write_text("claude content")
        (tmp_path / "agents.md").write_text("agents content")
        result = load_project_context_file(tmp_path)
        assert result is not None
        assert result[0] == "agents content"


# ---------------------------------------------------------------------------
# _detect_os
# ---------------------------------------------------------------------------


class TestDetectOs:
    def test_returns_nonempty_string(self):
        result = _detect_os()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_macos_contains_macos(self):
        if platform.system() == "Darwin":
            assert "macOS" in _detect_os()

    def test_linux_contains_linux(self):
        if platform.system() == "Linux":
            assert "Linux" in _detect_os()


# ---------------------------------------------------------------------------
# _detect_shell
# ---------------------------------------------------------------------------


class TestDetectShell:
    def test_returns_nonempty_string(self):
        assert len(_detect_shell()) > 0

    def test_returns_shell_from_env(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        assert _detect_shell() == "zsh"

    def test_returns_basename_only(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/usr/local/bin/bash")
        assert _detect_shell() == "bash"

    def test_falls_back_when_shell_unset(self, monkeypatch):
        monkeypatch.delenv("SHELL", raising=False)
        result = _detect_shell()
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


class TestPromptBuilderIdentity:
    def test_default_identity_used_when_no_custom(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert _DEFAULT_IDENTITY in prompt
        assert prompt.startswith("You are Tau, an agentic coding assistant.")

    def test_default_identity_disowns_oauth_claude_code_block(self, tmp_path):
        """The Claude Code OAuth adapter prepends a "You are Claude Code" system block it
        cannot drop (see inference/api/text/anthropic_claude_code.py). Tau's identity is the
        last system block, so it must explicitly disown that one or the model answers as
        Claude Code — guessing at MCP servers and ~/.claude config instead of reading
        Tau's own docs."""
        prompt = PromptBuilder(_opts(tmp_path)).build()
        assert "provider compatibility artifact" in prompt
        assert "You are Tau" in prompt

    def test_identity_prompt_overrides_identity(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path, identity_prompt="Custom identity."))
        prompt = builder.build()
        assert "Custom identity." in prompt
        assert _DEFAULT_IDENTITY not in prompt

    def test_system_md_overrides_default(self, tmp_path):
        tau_dir = tmp_path / ".tau"
        tau_dir.mkdir()
        (tau_dir / "SYSTEM.md").write_text("My custom identity.")
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert "My custom identity." in prompt
        assert _DEFAULT_IDENTITY not in prompt


class TestPromptBuilderGuidelines:
    def test_guidelines_section_present(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert "# Guidelines" in prompt

    def test_all_general_guidelines_included(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        for guideline in _GENERAL_GUIDELINES:
            assert guideline in prompt

    def test_precedence_guidelines_present(self, tmp_path):
        prompt = PromptBuilder(_opts(tmp_path)).build()
        assert _PRECEDENCE_GUIDELINES in prompt

    def test_guidelines_present_with_identity_prompt(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path, identity_prompt="Custom identity."))
        prompt = builder.build()
        assert "# Guidelines" in prompt
        assert _GENERAL_GUIDELINES[0] in prompt


class TestPromptBuilderFooter:
    def test_footer_contains_cwd(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert str(tmp_path).replace("\\", "/") in prompt

    def test_footer_contains_date(self, tmp_path):
        from datetime import date

        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert date.today().isoformat() in prompt

    def test_footer_contains_os(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert "OS:" in prompt

    def test_footer_contains_shell(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert "Shell:" in prompt


class TestPromptBuilderToolsSection:
    def test_no_tools_no_section(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path, tools=[]))
        prompt = builder.build()
        assert "Available Tools" not in prompt

    def test_tools_section_lists_tools(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path, tools=[ReadTool(), WriteTool()]))
        prompt = builder.build()
        assert "Available Tools" in prompt
        assert "read" in prompt
        assert "write" in prompt

    def test_tool_guidelines_included(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path, tools=[ReadTool()]))
        prompt = builder.build()
        assert "Tool Guidelines" in prompt


class TestLoadProjectContextFiles:
    def test_returns_empty_when_no_files(self, tmp_path):
        assert load_project_context_files(tmp_path) == []

    def test_loads_single_file_from_cwd(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("cwd rules")
        results = load_project_context_files(tmp_path)
        assert len(results) == 1
        assert "cwd rules" in results[0][0]

    def test_loads_multiple_files_root_first(self, tmp_path):
        # Simulate git repo: parent is root, child is cwd
        (tmp_path / ".git").mkdir()
        child = tmp_path / "sub"
        child.mkdir()
        (tmp_path / "AGENTS.md").write_text("root rules")
        (child / "AGENTS.md").write_text("child rules")
        results = load_project_context_files(child)
        assert len(results) == 2
        assert "root rules" in results[0][0]
        assert "child rules" in results[1][0]

    def test_stops_at_git_root(self, tmp_path):
        # Files above git root should not be included
        (tmp_path / "AGENTS.md").write_text("above root rules")
        git_root = tmp_path / "repo"
        git_root.mkdir()
        (git_root / ".git").mkdir()
        (git_root / "AGENTS.md").write_text("repo rules")
        results = load_project_context_files(git_root)
        assert len(results) == 1
        assert "repo rules" in results[0][0]

    def test_deduplicates_same_file(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("rules")
        results = load_project_context_files(tmp_path)
        assert len(results) == 1


class TestPromptBuilderProjectContext:
    def test_context_included_when_trusted_and_file_exists(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Project rules here.")
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert "Project rules here." in prompt
        assert "Project Instructions" in prompt

    def test_context_uses_xml_wrapping(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Project rules here.")
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert "<project_context>" in prompt
        assert "<project_instructions path=" in prompt
        assert "</project_instructions>" in prompt
        assert "</project_context>" in prompt

    def test_context_excluded_when_disabled(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Project rules here.")
        builder = PromptBuilder(_opts(tmp_path, disable_context_files=True))
        prompt = builder.build()
        assert "Project rules here." not in prompt

    def test_context_excluded_when_not_trusted(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Project rules here.")
        opts = PromptOptions(cwd=tmp_path, project_trusted=False)
        builder = PromptBuilder(opts)
        prompt = builder.build()
        assert "Project rules here." not in prompt

    def test_no_context_when_no_file(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert "Project Instructions" not in prompt


class TestPromptBuilderAppend:
    def test_append_prompt_included(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path, append_prompt="Always respond in English."))
        prompt = builder.build()
        assert "Always respond in English." in prompt

    def test_extra_appends_included(self, tmp_path):
        builder = PromptBuilder(_opts(tmp_path, extra_appends=["Extra 1", "Extra 2"]))
        prompt = builder.build()
        assert "Extra 1" in prompt
        assert "Extra 2" in prompt

    def test_appends_are_last(self, tmp_path):
        prompt = PromptBuilder(_opts(tmp_path, append_prompt="LAST INSTRUCTION")).build()
        assert prompt.endswith("LAST INSTRUCTION")

    def test_append_system_md_loaded(self, tmp_path):
        tau_dir = tmp_path / ".tau"
        tau_dir.mkdir()
        (tau_dir / "APPEND_SYSTEM.md").write_text("Appended instructions.")
        builder = PromptBuilder(_opts(tmp_path))
        prompt = builder.build()
        assert "Appended instructions." in prompt


class TestRemoteUrlRedaction:
    def test_redacts_https_credentials(self):
        assert (
            _redact_remote_url("https://user:secret@example.com/org/repo.git")
            == "https://***@example.com/org/repo.git"
        )

    def test_preserves_remote_without_credentials(self):
        remote = "git@github.com:org/repo.git"
        assert _redact_remote_url(remote) == remote

    def test_redacts_query_parameters(self):
        assert (
            _redact_remote_url("https://example.com/org/repo.git?token=secret")
            == "https://example.com/org/repo.git?***"
        )


# ---------------------------------------------------------------------------
# Regression coverage for prompt-content correctness
# ---------------------------------------------------------------------------


class _DescTool(ReadTool):
    """A tool with an arbitrary description, for tools-section rendering tests."""

    def __init__(self, name: str, description: str) -> None:
        super().__init__()
        self.name = name
        self.description = description


class TestToolsSectionDescriptionHandling:
    """`description.splitlines()[0]` raised IndexError on an empty description,
    which failed the entire prompt build — an extension registering a tool
    without a description took down startup."""

    def test_empty_description_does_not_crash(self, tmp_path):
        prompt = PromptBuilder(_opts(tmp_path, tools=[_DescTool("noop", "")])).build()
        assert "- **noop**" in prompt

    def test_leading_blank_line_uses_first_real_line(self, tmp_path):
        prompt = PromptBuilder(_opts(tmp_path, tools=[_DescTool("t", "\n\nActual text")])).build()
        assert "- **t** — Actual text" in prompt

    def test_whitespace_only_description_does_not_crash(self, tmp_path):
        prompt = PromptBuilder(_opts(tmp_path, tools=[_DescTool("ws", "   \n  \n")])).build()
        assert "- **ws**" in prompt


class TestEmptyContextFileFallback:
    """An empty AGENTS.md must not suppress a populated CLAUDE.md. The empty-content
    check used to `return None` from inside the candidate loop, ending the search."""

    def test_empty_agents_falls_back_to_claude(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("   \n")
        (tmp_path / "CLAUDE.md").write_text("claude content")

        result = load_project_context_file(tmp_path)

        assert result is not None
        content, path = result
        assert path.name == "CLAUDE.md"
        assert content == "claude content"

    def test_empty_agents_falls_back_in_multi_dir_loader(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("")
        (tmp_path / "CLAUDE.md").write_text("claude content")

        results = load_project_context_files(tmp_path)

        assert [p.name for _content, p in results] == ["CLAUDE.md"]

    def test_populated_agents_still_wins(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("agents content")
        (tmp_path / "CLAUDE.md").write_text("claude content")

        result = load_project_context_file(tmp_path)

        assert result is not None
        assert result[1].name == "AGENTS.md"

    def test_all_empty_still_returns_none(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("  ")
        (tmp_path / "CLAUDE.md").write_text("")

        assert load_project_context_file(tmp_path) is None


class TestDocsAndSkillsRequireReadTool:
    """Both sections tell the model to open files on disk. Without a read tool
    they advertise capabilities it cannot reach."""

    def test_docs_section_omitted_without_read_tool(self, tmp_path):
        prompt = PromptBuilder(_opts(tmp_path, tools=[WriteTool()])).build()
        assert "# Tau Documentation" not in prompt
        assert "Read .md files completely" not in prompt

    def test_docs_section_present_with_read_tool(self, tmp_path):
        prompt = PromptBuilder(_opts(tmp_path, tools=[ReadTool()])).build()
        assert "# Tau Documentation" in prompt

    def test_docs_section_omitted_when_docs_absent(self, tmp_path, monkeypatch):
        """Wheel installs ship no docs/ — the section must not name unreachable paths."""
        monkeypatch.setattr(
            "tau.agent.prompt.builder.get_docs_dir", lambda: tmp_path / "definitely-absent"
        )
        prompt = PromptBuilder(_opts(tmp_path, tools=[ReadTool()])).build()
        assert "# Tau Documentation" not in prompt
        assert "definitely-absent" not in prompt


class TestDocsSectionCuratedTopics:
    """The doc list was hardcoded and named 17 of 30 real files. It is now driven by
    ``agentTopics`` in docs.json — a deliberately narrow, extend/embed-oriented set with
    topic labels the model can route on, mirroring how pi names 10 of its 29 docs."""

    def _docs(self, tmp_path, monkeypatch, *, index: str | None, names: list[str]):
        docs = tmp_path / "docs"
        docs.mkdir()
        for name in names:
            (docs / name).write_text("x")
        if index is not None:
            (docs / "docs.json").write_text(index)
        monkeypatch.setattr("tau.agent.prompt.builder.get_docs_dir", lambda: docs)
        return docs

    def _index(self, items, topics):
        return json.dumps(
            {
                "navigation": [{"title": "Section", "items": items}],
                "agentTopics": topics,
            }
        )

    def test_lists_curated_topics_with_titles(self, tmp_path, monkeypatch):
        index = self._index([{"title": "Terminal UI", "path": "tui.md"}], ["tui.md"])
        docs = self._docs(tmp_path, monkeypatch, index=index, names=["tui.md"])

        prompt = PromptBuilder(_opts(tmp_path, tools=[ReadTool()])).build()

        # Absolute paths, one topic per line: the run-on comma list buried the entries
        # mid-paragraph and left the model resolving "docs/tui.md" against cwd.
        assert f"- Terminal UI — {docs}/tui.md" in prompt

    def test_omits_docs_not_in_agent_topics(self, tmp_path, monkeypatch):
        """A doc existing on disk and in the nav is still not named unless opted in."""
        index = self._index(
            [
                {"title": "Terminal UI", "path": "tui.md"},
                {"title": "Usage Guide", "path": "usage.md"},
            ],
            ["tui.md"],
        )
        self._docs(tmp_path, monkeypatch, index=index, names=["tui.md", "usage.md"])

        prompt = PromptBuilder(_opts(tmp_path, tools=[ReadTool()])).build()

        assert "tui.md" in prompt
        assert "usage.md" not in prompt

    def test_points_elsewhere_for_uncurated_topics(self, tmp_path, monkeypatch):
        """Curation must not imply the other docs are unreachable."""
        index = self._index([{"title": "Terminal UI", "path": "tui.md"}], ["tui.md"])
        self._docs(tmp_path, monkeypatch, index=index, names=["tui.md"])

        prompt = PromptBuilder(_opts(tmp_path, tools=[ReadTool()])).build()

        assert "For any other Tau topic" in prompt

    def test_skips_topic_whose_file_is_missing(self, tmp_path, monkeypatch):
        index = self._index(
            [{"title": "Ghost", "path": "ghost.md"}, {"title": "Real", "path": "real.md"}],
            ["ghost.md", "real.md"],
        )
        docs = self._docs(tmp_path, monkeypatch, index=index, names=["real.md"])

        prompt = PromptBuilder(_opts(tmp_path, tools=[ReadTool()])).build()

        assert f"- Real — {docs}/real.md" in prompt
        assert "ghost.md" not in prompt

    def test_section_dropped_when_index_malformed(self, tmp_path, monkeypatch):
        self._docs(tmp_path, monkeypatch, index="{not json", names=["alpha.md"])

        prompt = PromptBuilder(_opts(tmp_path, tools=[ReadTool()])).build()

        assert "# Tau Documentation" not in prompt

    def test_real_agent_topics_are_valid(self):
        """Every curated topic must exist on disk and in the navigation."""
        docs = get_docs_dir()
        raw = json.loads((docs / "docs.json").read_text())
        nav = {item["path"] for s in raw["navigation"] for item in s["items"]}
        topics = raw["agentTopics"]

        assert topics, "agentTopics must not be empty"
        assert set(topics) <= nav
        for path in topics:
            assert (docs / path).is_file(), path

    def test_real_docs_json_covers_every_shipped_doc(self):
        """Guards the AGENTS.md rule that a new doc is added to docs.json."""
        docs = get_docs_dir()
        listed = {
            item["path"]
            for section in json.loads((docs / "docs.json").read_text())["navigation"]
            for item in section["items"]
        }
        on_disk = {p.name for p in docs.iterdir() if p.suffix == ".md"}

        assert on_disk - listed == set()
        assert listed - on_disk == set()

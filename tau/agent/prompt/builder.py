from __future__ import annotations

import os
import platform
import shutil
from datetime import date
from pathlib import Path

from tau.agent.prompt.types import PromptOptions
from tau.settings.paths import (
    get_append_system_prompt_path,
    get_docs_dir,
    get_readme_path,
    get_system_prompt_path,
)


def load_project_context_file(cwd: Path) -> tuple[str, Path] | None:
    """Load AGENTS.md or CLAUDE.md from project directory.

    Returns (content, path) tuple if found, None otherwise.
    Looks for AGENTS.md first, then CLAUDE.md (case-insensitive).
    """
    candidates = ["AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD"]
    for filename in candidates:
        path = cwd / filename
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8").strip()
                return (content, path) if content else None
            except OSError:
                pass
    return None


_DEFAULT_IDENTITY = """\
You are a coding agent. You help users understand, write, and modify code and files.

You have strong software engineering skills. You think carefully before making changes,
and follow the existing style and conventions of the project.
If a task is ambiguous, ask a clarifying question before proceeding.\
"""


_GIT_STATUS_MAX_LINES = 30
_GIT_LOG_COUNT = 5


def _detect_os() -> str:
    """Return a human-readable OS name and version."""
    system = platform.system()
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0] or platform.release()}"
    if system == "Linux":
        return f"Linux {platform.release()}"
    if system == "Windows":
        return f"Windows {platform.release()}"
    return f"{system} {platform.release()}".strip()


def _detect_shell() -> str:
    """Detect the user's shell from $SHELL, falling back to common shells on PATH."""
    shell = os.environ.get("SHELL", "")
    if shell:
        return Path(shell).name
    for candidate in ("bash", "zsh", "fish", "sh"):
        if shutil.which(candidate):
            return candidate
    return "unknown"


def _git_status(cwd: Path) -> str:
    """Return a git-state snapshot for the system prompt, or "" if not a repo.

    Best-effort: any GitPython error (not a repo, no commits, git binary missing)
    results in an empty string rather than raising. The ``status`` listing is
    truncated to keep large/dirty working trees from bloating the prompt.
    """
    try:
        from git import Repo
        from git.exc import GitError, InvalidGitRepositoryError, NoSuchPathError
    except ImportError:
        return ""

    repo = None
    try:
        repo = Repo(cwd, search_parent_directories=True)

        try:
            branch = repo.active_branch.name
        except TypeError:
            # Detached HEAD — no active branch.
            branch = f"(detached at {repo.head.commit.hexsha[:8]})"

        remote = next((r.url for r in repo.remotes if r.name == "origin"), None)
        if remote is None and repo.remotes:
            remote = repo.remotes[0].url

        status = repo.git.status("--porcelain")
        if status:
            lines = status.splitlines()
            shown = lines[:_GIT_STATUS_MAX_LINES]
            extra = len(lines) - len(shown)
            status_block = "\n".join(shown)
            if extra > 0:
                status_block += f"\n… and {extra} more changed file(s)"
        else:
            status_block = "(clean)"

        try:
            log = repo.git.log(f"-{_GIT_LOG_COUNT}", "--oneline", "--no-color")
        except GitError:
            log = ""  # repo with no commits yet
    except (InvalidGitRepositoryError, NoSuchPathError):
        return ""
    except GitError:
        return ""
    finally:
        if repo is not None:
            repo.close()

    parts = [
        "\n\ngitStatus: snapshot taken at session start; it is not updated during the "
        "conversation. Re-run git yourself before relying on it.",
        f"Current branch: {branch}",
    ]
    if remote:
        parts.append(f"Remote (origin): {remote}")
    parts.append(f"Status:\n{status_block}")
    if log:
        parts.append(f"Recent commits:\n{log}")
    return "\n".join(parts)


class PromptBuilder:
    """
    Assembles the system prompt from layered sources.

    Layers in order:
      Identity              — SYSTEM.md if present, else built-in identity;
                              --system bypasses entirely
      Tools section         — auto-generated from tool list (descriptions + guidelines)
      Tau docs              — tau documentation and examples
      Project Instructions  — AGENTS.md or CLAUDE.md from project (if present)
      Skills section        — available skills
      APPEND_SYSTEM.md      — verbatim append (user additions)
      Footer                — cwd, date
    """

    def __init__(self, options: PromptOptions) -> None:
        self._opts = options

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build(self) -> str:
        """Build the complete system prompt."""
        identity = self._identity()
        tools = self._tools_section()
        docs = self._docs_section()
        project_context = self._project_context_section()
        skills = self._skills_section()
        append = self._append()
        git = self._git_section()
        footer = self._footer()
        return identity + tools + docs + project_context + skills + append + git + footer

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------

    def _identity(self) -> str:
        if self._opts.custom_prompt:
            return self._opts.custom_prompt

        system_md = self._read_path(get_system_prompt_path(self._opts.cwd))
        if system_md:
            return system_md

        return _DEFAULT_IDENTITY

    def _tools_section(self) -> str:
        tools = self._opts.tools
        if not tools:
            return ""
        lines: list[str] = []
        guidelines: list[str] = []
        for t in sorted(tools, key=lambda t: t.name):
            desc = t.description.splitlines()[0].strip().rstrip(".")
            snippet = getattr(t, "prompt_snippet", None)
            if snippet:
                desc = f"{desc}. {snippet.strip()}"
            lines.append(f"- **{t.name}** — {desc}")
            guideline = getattr(t, "prompt_guidelines", None)
            if guideline:
                guidelines.append(f"- **{t.name}**: {guideline.strip()}")
        section = "\n\n# Available Tools\n\n" + "\n".join(lines)
        if guidelines:
            section += "\n\n## Tool Guidelines\n\n" + "\n".join(guidelines)
        return section

    def _project_context_section(self) -> str:
        """Include project-specific context from AGENTS.md or CLAUDE.md if present.

        Skipped if:
        - disable_context_files is True (--no-context-files flag)
        - project is not trusted (--no-approve flag or trust store)
        """
        if self._opts.disable_context_files:
            return ""
        if self._opts.project_trusted is False:
            return ""
        context = load_project_context_file(self._opts.cwd)
        if not context:
            return ""
        content, path = context
        return (
            f"\n\n# Project Instructions\n\n"
            f"Project-specific guidelines and rules (from {path.name}):\n\n"
            f"{content}\n\n"
            "Follow project-specific instructions before general Tau guidelines."
        )

    def _docs_section(self) -> str:
        readme = get_readme_path()
        docs = get_docs_dir()
        return (
            "\n\nTau documentation (read only when the user asks about Tau itself, its"
            " settings, extensions, themes, skills, tools, sessions, or keybindings):\n"
            f"- README: {readme}\n"
            f"- Docs directory: {docs}\n"
            "- When asked about: settings (docs/settings.md), tools (docs/tools.md),"
            " extensions (docs/extensions.md), themes (docs/themes.md),"
            " skills (docs/skills.md), keybindings (docs/keybindings.md),"
            " sessions (docs/sessions.md), usage (docs/usage.md),"
            " Python API (docs/python-api.md), inference providers (docs/inference-providers.md)\n"
            "- Resolve all doc paths under the Docs directory above,"
            " not the current working directory\n"
            "- Read .md files completely and follow cross-references before answering"
        )

    def _skills_section(self) -> str:
        from tau.skills.registry import skill_registry

        block = skill_registry.format_for_system_prompt(self._opts.skills)
        return f"\n\n{block}" if block else ""

    def _append(self) -> str:
        parts: list[str] = []

        if self._opts.append_prompt:
            parts.append(self._opts.append_prompt)
        else:
            append_md = self._read_path(get_append_system_prompt_path(self._opts.cwd))
            if append_md:
                parts.append(append_md)

        for extra in self._opts.extra_appends:
            stripped = extra.strip()
            if stripped:
                parts.append(stripped)

        return ("\n\n" + "\n\n".join(parts)) if parts else ""

    def _git_section(self) -> str:
        """Include a snapshot of git state when cwd is inside a repo.

        Skipped if the project is not trusted (--no-approve flag or trust store),
        mirroring the project-context gating. Returns "" when not a git repo or
        if anything goes wrong reading it — git info is best-effort context, never
        a hard failure.
        """
        if self._opts.project_trusted is False:
            return ""
        return _git_status(self._opts.cwd)

    def _footer(self) -> str:
        cwd = str(self._opts.cwd).replace("\\", "/")
        today = date.today().isoformat()
        return (
            "\n\n# Environment\n"
            f"Current working directory: {cwd}\n"
            f"OS: {_detect_os()}\n"
            f"Architecture: {platform.machine()}\n"
            f"Shell: {_detect_shell()}\n"
            f"Date: {today}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_path(self, path: Path) -> str | None:
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8").strip()
                return content if content else None
            except OSError:
                return None
        return None


def build_prompt(options: PromptOptions) -> str:
    """Build a system prompt from the given options."""
    return PromptBuilder(options).build()

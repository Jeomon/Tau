from __future__ import annotations

import os
import platform
import shutil
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from tau.agent.prompt.types import PromptOptions
from tau.settings.paths import (
    get_append_system_prompt_path,
    get_docs_dir,
    get_examples_path,
    get_readme_path,
    get_system_prompt_path,
)

_CONTEXT_FILE_NAMES = ("agents.md", "claude.md")


def _context_file_paths(directory: Path) -> list[Path]:
    """Return supported context files in deterministic priority order."""
    try:
        entries = [entry for entry in directory.iterdir() if entry.is_file()]
    except OSError:
        return []

    paths: list[Path] = []
    for preferred_name in _CONTEXT_FILE_NAMES:
        matches = [entry for entry in entries if entry.name.casefold() == preferred_name]
        matches.sort(key=lambda entry: (entry.name != preferred_name.upper(), entry.name))
        paths.extend(matches)
    return paths


def load_project_context_file(cwd: Path) -> tuple[str, Path] | None:
    """Load AGENTS.md or CLAUDE.md from project directory.

    Returns (content, path) tuple if found, None otherwise.
    Looks for AGENTS.md first, then CLAUDE.md, case-insensitively.
    """
    for path in _context_file_paths(cwd):
        try:
            content = path.read_text(encoding="utf-8").strip()
            return (content, path) if content else None
        except OSError:
            pass
    return None


def _find_git_root(cwd: Path) -> Path | None:
    """Walk up from cwd and return the first directory containing .git, or None."""
    current = cwd.resolve()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_project_context_files(
    cwd: Path,
    *,
    on_error: Callable[[Path, OSError], None] | None = None,
) -> list[tuple[str, Path]]:
    """Walk from git root (or cwd if not in a repo) down to cwd, collecting context files.

    Returns (content, path) tuples ordered root-first so cwd instructions appear
    last and take highest precedence when the model reads top-to-bottom.
    """

    def _load(directory: Path) -> tuple[str, Path] | None:
        seen_files: set[tuple[int, int]] = set()
        for path in _context_file_paths(directory):
            try:
                stat = path.stat()
            except OSError as exc:
                if on_error is not None:
                    on_error(path, exc)
                continue
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen_files:
                continue
            seen_files.add(identity)
            try:
                content = path.read_text(encoding="utf-8").strip()
                return (content, path) if content else None
            except OSError as exc:
                if on_error is not None:
                    on_error(path, exc)
        return None

    resolved = cwd.resolve()
    git_root = _find_git_root(resolved)
    stop_at = git_root if git_root else resolved

    # Collect directories from stop_at down to cwd
    dirs: list[Path] = []
    current = resolved
    while True:
        dirs.append(current)
        if current == stop_at:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    seen: set[Path] = set()
    results: list[tuple[str, Path]] = []
    for directory in reversed(dirs):
        entry = _load(directory)
        if entry and entry[1] not in seen:
            seen.add(entry[1])
            results.append(entry)

    return results


_DEFAULT_IDENTITY = """\
You are Tau, an agentic coding assistant operating through the Tau CLI framework. You help
users understand and modify software by reading files, executing commands, editing code,
and writing new files.

You have strong software engineering skills. You think carefully before making changes,
and follow the existing style and conventions of the project.
"""

_GENERAL_GUIDELINES = [
    (
        "When ambiguity materially affects the result or would make an action risky, "
        "ask a precise clarifying question; otherwise state a reasonable assumption and proceed."
    ),
    ("Do only what the task asks; don't add features, refactors, or abstractions beyond scope."),
    "Write comments when the *why* is non-obvious — well-named code explains itself.",
    (
        "Keep responses concise and direct. After making changes, report the outcome, "
        "the relevant files, and validation results without narrating routine steps."
    ),
    (
        "Prioritize accuracy over agreement — investigate before confirming, and "
        "disagree when the evidence calls for it."
    ),
    (
        "Verify before reporting a task done: run the tests, build, or command that "
        "exercises the change. Don't present untested, partial, or stubbed-out work as complete."
    ),
    (
        "When fixing a bug, reproduce it before changing code. If the same fix attempt "
        "fails twice, stop and investigate the root cause instead of retrying it."
    ),
    (
        "Treat git history as shared: never force-push, amend a commit that isn't yours, "
        "or skip hooks (--no-verify) without the user's explicit go-ahead."
    ),
]

_PRECEDENCE_GUIDELINES = """\
## Instruction Precedence

- The identity establishes your baseline role.
- Appended user or extension instructions have the highest priority for behavioral guidance.
- Project instructions override general Tau and tool guidelines when they conflict.
- For nested project instructions, the file closest to the current working directory wins.
- Tool guidelines apply unless a higher-priority instruction explicitly conflicts with them.
"""


_GIT_STATUS_MAX_LINES = 30
_GIT_LOG_COUNT = 5


def _detect_os() -> str:
    """Return a human-readable OS name and version.

    On Windows, ``platform.system()``/``platform.release()`` call
    ``platform.uname()``, which shells out to two separate WMI COM queries
    (~150-200ms combined) to determine the release name. ``sys.getwindowsversion()``
    reads the same information directly from the OS with no WMI round-trip, so we
    use it here and derive the release name (10 vs 11) from the build number using
    the same >=22000 heuristic Windows itself uses.
    """
    if sys.platform == "win32":
        v = sys.getwindowsversion()
        release = "11" if v.build >= 22000 else str(v.major)
        return f"Windows {release}"
    system = platform.system()
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0] or platform.release()}"
    if system == "Linux":
        return f"Linux {platform.release()}"
    return f"{system} {platform.release()}".strip()


def _detect_machine() -> str:
    """Return the machine architecture (e.g. 'x86_64', 'AMD64').

    ``platform.machine()`` is a thin wrapper around ``platform.uname()``, which
    is expensive on Windows (see ``_detect_os``). ``PROCESSOR_ARCHITECTURE`` is
    always set by the OS and gives the same value without the WMI round-trip.
    """
    if sys.platform == "win32":
        arch = os.environ.get("PROCESSOR_ARCHITECTURE")
        if arch:
            return arch
    return platform.machine()


def _detect_shell() -> str:
    """Detect the user's shell from $SHELL, falling back to common shells on PATH.

    On Windows, ``$SHELL`` is a POSIX convention and is essentially never set,
    so this used to fall through to up to 4 ``shutil.which()`` calls, each
    scanning every ``PATH`` entry against every ``PATHEXT`` suffix for shells
    (zsh, fish, sh) that don't exist on Windows. We instead resolve the native
    shell directly from environment variables the OS always sets, with no
    PATH scan.
    """
    shell = os.environ.get("SHELL", "")
    if shell:
        return Path(shell).name
    if sys.platform == "win32":
        if os.environ.get("PSModulePath"):  # noqa: SIM112 — actual Windows env var casing
            return "powershell"
        comspec = os.environ.get("COMSPEC", "")
        return Path(comspec).stem if comspec else "cmd"
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
        if remote is not None:
            remote = _redact_remote_url(remote)

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
    # No explicit repo.close() here: GitPython's Repo.__del__ already calls it,
    # and close() forces two gc.collect() passes on Windows (its own workaround
    # for tempfile handles). Closing explicitly *and* via __del__ paid for that
    # gc.collect() pass twice for no benefit — we don't touch the object database
    # in the common (attached-branch) path, so there's nothing time-sensitive to
    # release synchronously.

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


def _redact_remote_url(url: str) -> str:
    """Remove credentials from a Git remote URL before prompt inclusion."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "(redacted invalid remote URL)"
    netloc = parsed.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", maxsplit=1)[1]
        netloc = f"***@{host}"
    query = "***" if parsed.query else ""
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


class PromptBuilder:
    """
    Assembles the system prompt from layered sources.

    Layers in order:
      Identity              — SYSTEM.md if present, else built-in identity
      Guidelines            — general behavior and explicit precedence
      Tools section         — auto-generated from tool list (descriptions + guidelines)
      Tau docs              — tau documentation and examples
      Project Instructions  — AGENTS.md or CLAUDE.md from project (if present)
      Skills section        — available skills
      Git snapshot          — branch, redacted remote, status, recent commits
      Environment           — cwd, OS, architecture, shell, date
      APPEND_SYSTEM.md      — verbatim append, deliberately last

    RuntimeConfig.system_prompt and the CLI --system option bypass this builder.
    """

    def __init__(self, options: PromptOptions) -> None:
        self._opts = options

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build(self) -> str:
        """Build the complete system prompt."""
        identity = self._identity()
        guidelines = self._guidelines_section()
        tools = self._tools_section()
        docs = self._docs_section()
        project_context = self._project_context_section()
        skills = self._skills_section()
        git = self._git_section()
        footer = self._footer()
        append = self._append()
        return (
            identity + guidelines + tools + docs + project_context + skills + git + footer + append
        )

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------

    def _identity(self) -> str:
        if self._opts.identity_prompt:
            return self._opts.identity_prompt

        system_md = self._read_path(get_system_prompt_path(self._opts.cwd))
        if system_md:
            return system_md

        return _DEFAULT_IDENTITY

    def _guidelines_section(self) -> str:
        bullets = "\n".join(f"- {g}" for g in _GENERAL_GUIDELINES)
        return f"\n\n# Guidelines\n\n{bullets}\n\n{_PRECEDENCE_GUIDELINES}"

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
        section += (
            "\nIn addition to the tools above, you may have access to other custom "
            "tools depending on the project."
        )
        if guidelines:
            section += "\n\n## Tool Guidelines\n\n" + "\n".join(guidelines)
        return section

    def _project_context_section(self) -> str:
        """Include project-specific context from AGENTS.md or CLAUDE.md if present.

        Walks from git root down to cwd, loading one file per directory.
        Each file is wrapped in <project_instructions path="..."> so the model
        knows which directory each set of rules comes from.

        Skipped if:
        - disable_context_files is True (--no-context-files flag)
        - project is not trusted (--no-approve flag or trust store)
        """
        if self._opts.disable_context_files:
            return ""
        if self._opts.project_trusted is False:
            return ""
        files = (
            [(item.content, item.path) for item in self._opts.context_files]
            if self._opts.context_files is not None
            else load_project_context_files(self._opts.cwd)
        )
        if not files:
            return ""
        blocks = "".join(
            f'<project_instructions path="{path}">\n{content}\n</project_instructions>\n\n'
            for content, path in files
        )
        return (
            "\n\n# Project Instructions\n\n"
            "Project-specific guidelines. Files are ordered from the repository root "
            "toward the current directory; later files take precedence:\n\n"
            "<project_context>\n\n" + blocks + "</project_context>"
        )

    def _docs_section(self) -> str:
        readme = get_readme_path()
        docs = get_docs_dir()
        examples = get_examples_path()
        return (
            "\n\nTau documentation (read only when the user asks about Tau itself, its"
            " settings, extensions, themes, skills, tools, sessions, or keybindings):\n"
            f"- README: {readme}\n"
            f"- Docs directory: {docs}\n"
            f"- Examples directory: {examples} (extensions, custom tools, themes, skills)\n"
            "- When asked about: quickstart (docs/quickstart.md),"
            " installation (docs/installation.md),"
            " usage (docs/usage.md), CLI flags (docs/cli-reference.md),"
            " architecture (docs/architecture.md),"
            " settings (docs/settings.md), tools (docs/tools.md),"
            " extensions (docs/extensions.md), themes (docs/themes.md),"
            " skills (docs/skills.md), prompts (docs/prompts.md),"
            " keybindings (docs/keybindings.md),"
            " sessions (docs/sessions.md), messages (docs/messages.md),"
            " Python API (docs/python-api.md),"
            " inference providers (docs/inference-providers.md),"
            " auth (docs/auth.md)\n"
            "- Resolve all doc paths under the Docs directory above,"
            " not the current working directory\n"
            "- Resolve all example paths under the Examples directory above\n"
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
        if self._opts.git_snapshot is not None:
            return self._opts.git_snapshot
        return _git_status(self._opts.cwd)

    def _footer(self) -> str:
        cwd = str(self._opts.cwd).replace("\\", "/")
        today = date.today().isoformat()
        return (
            "\n\n# Environment\n"
            f"Current working directory: {cwd}\n"
            f"OS: {_detect_os()}\n"
            f"Architecture: {_detect_machine()}\n"
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
    pb = PromptBuilder(options=options)
    return pb.build()

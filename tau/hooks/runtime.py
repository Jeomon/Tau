from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class InputEvent:
    """Fired when a new user message is received;
    handlers may transform or handle it via InputEventResult.
    """

    type: Literal["input"] = field(default="input", init=False)
    text: str = ""
    source: Literal[
        "interactive", "rpc", "extension", "cron", "subagent", "goal", "queue", "background"
    ] = "interactive"


@dataclass
class UserTerminalEvent:
    """Fired when a shell command is run on the user's behalf (e.g. via the terminal tool)."""

    type: Literal["user_terminal"] = field(default="user_terminal", init=False)
    command: str = ""
    private: bool = False
    cwd: str = ""


@dataclass
class TerminalExecutionEvent:
    """Fired twice: at command start (streaming=True) and on completion (streaming=False)."""

    type: Literal["terminal_execution"] = field(default="terminal_execution", init=False)
    message: Any = None
    streaming: bool = False


@dataclass
class TerminalOutputEvent:
    """Fired for each output chunk while a ! command is running."""

    type: Literal["terminal_output"] = field(default="terminal_output", init=False)
    message: Any = None


@dataclass
class ResourcesDiscoverEvent:
    """Fired during extension reload so extensions can inject extra resource directories."""

    type: Literal["resources_discover"] = field(default="resources_discover", init=False)
    cwd: str = ""


@dataclass
class ProjectTrustEvent:
    """Fired when a project's trust status needs to be determined."""

    type: Literal["project_trust"] = field(default="project_trust", init=False)
    project_dir: str = ""


@dataclass
class RuntimeStartEvent:
    """Fired once at the very start of runtime construction, right after the hooks
    bus exists but BEFORE extensions, skills, themes, or tools are loaded.

    This is the earliest possible lifecycle signal. Note that extension
    ``@tau.on("runtime_start")`` handlers will NOT fire here — extensions aren't
    loaded yet, so nothing is subscribed. It's intended for core / manually
    registered subscribers (boot timing, metrics, early bootstrap), and as the
    opening bracket of the runtime lifecycle: runtime_start → runtime_ready →
    runtime_stop. Extensions that need an early hook should use ``register()``;
    extensions that need everything loaded should use ``runtime_ready``.
    """

    type: Literal["runtime_start"] = field(default="runtime_start", init=False)


@dataclass
class RuntimeReadyEvent:
    """Fired once when the runtime is fully constructed — engine, agent, tools,
    and extensions are all wired and ``session_start`` has fired — but before any
    mode-specific execution (TUI / print / rpc) begins.

    This is the earliest point at which an extension can rely on the whole runtime
    being in place. It fires well before ``tui_ready`` (which only exists in the
    interactive TUI) and is mode-independent, so it's the right place to kick off
    background work like language-server warm-up.
    """

    type: Literal["runtime_ready"] = field(default="runtime_ready", init=False)


@dataclass
class RuntimeStopEvent:
    """Fired once when the runtime is shutting down, after the mode-specific loop
    (TUI / print / rpc) has exited but before the process terminates.

    Symmetric counterpart to ``runtime_ready``: it brackets the runtime lifecycle
    and is mode-independent, so it's the right place for terminal cleanup that must
    run on quit regardless of mode — e.g. shutting down spawned language servers.
    Unlike ``session_shutdown`` (which only fires on session transitions) and
    ``tui_exit`` (interactive only), this fires exactly once on the way out.
    """

    type: Literal["runtime_stop"] = field(default="runtime_stop", init=False)


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class InputEventResult:
    """Returned by input handlers; 'transform' replaces text,
    'handled' suppresses normal processing.
    """

    action: Literal["continue", "transform", "handled"] = "continue"
    text: str | None = None


@dataclass
class UserTerminalResult:
    """Returned by user_terminal handlers to intercept and replace shell execution."""

    handled: bool = False
    output: str = ""
    exit_code: int = 0


@dataclass
class ResourcesDiscoverResult:
    """Returned by resources_discover handlers to contribute additional resource paths."""

    skill_paths: list[str] = field(default_factory=list)
    prompt_paths: list[str] = field(default_factory=list)
    theme_paths: list[str] = field(default_factory=list)


@dataclass
class ProjectTrustResult:
    """Returned by project_trust handlers to approve, deny, or defer the trust decision."""

    trusted: bool | None = None
    remember: bool = False

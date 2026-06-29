from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from tau.engine.types import FollowupMode, SteeringMode
from tau.inference.types import ThinkingLevel, Transport


class SCOPE(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"


@dataclass
class LockResult:
    result: Any
    next: str | None = None


@dataclass
class SettingsError:
    scope: SCOPE
    error: Exception


@dataclass
class ProviderRetrySettings:
    timeout_ms: int | None = None
    max_retries: int | None = None
    max_retry_delay_ms: int | None = None


@dataclass
class RetrySettings:
    enabled: bool | None = None
    max_retries: int | None = None
    base_delay_ms: int | None = None
    provider: ProviderRetrySettings | None = None


@dataclass
class ModelRef:
    """A single ``{id, provider}`` model reference used per modality."""

    id: str | None = None
    provider: str | None = None


@dataclass
class ModelSettings:
    """Per-modality model selection, each a ``{id, provider}`` reference.

    ``text`` is the chat model; ``voice`` is speech-to-text (input), ``speak``
    is text-to-speech (output). Legacy flat ``model`` / ``provider`` keys are
    migrated into ``text`` on load.
    """

    text: ModelRef | None = None
    voice: ModelRef | None = None  # STT — voice input
    speak: ModelRef | None = None  # TTS — spoken output
    image: ModelRef | None = None
    video: ModelRef | None = None


@dataclass
class ThinkingBudgetsSettings:
    minimal: int | None = None
    low: int | None = None
    medium: int | None = None
    high: int | None = None
    xhigh: int | None = None
    max: int | None = None


@dataclass
class ImageSettings:
    auto_resize: bool | None = (
        None  # resize images to 2000×2000 max before sending to LLM (default: True)
    )
    block_images: bool | None = (
        None  # prevent all images from being sent to the LLM (default: False)
    )


@dataclass
class CompactionSettings:
    enabled: bool | None = None  # enable auto-compaction (default: True)
    reserve_tokens: int | None = None  # tokens reserved for LLM response (default: 16384)
    keep_recent_tokens: int | None = None  # recent tokens to keep verbatim (default: 20000)


@dataclass
class BranchSummarySettings:
    enabled: bool | None = None  # enable branch summarization (default: True)
    skip_prompt: bool | None = None  # always skip the "Summarize branch?" prompt (default: False)
    reserve_tokens: int | None = None  # tokens to reserve when summarizing (default: 16384)


@dataclass
class ExtensionEntry:
    """Per-extension config entry stored in the ``extensions.list`` settings block."""

    path: str
    name: str | None = None
    enabled: bool = True
    source: str | None = None
    author: str | None = None
    settings: dict | None = field(default=None)


@dataclass
class ExtensionsSettings:
    """Global extension toggle plus per-extension configuration list."""

    enabled: bool | None = None  # global on/off for all extensions
    list: list[ExtensionEntry] | None = None


@dataclass
class PackageEntry:
    """A package installed via pip/uv/poetry into the tau-managed venv."""

    source: str  # "pypi:name==1.0", "git+https://...", "/local/path"
    name: str  # package name (normalised)
    version: str | None = None  # installed version, if known
    installed_path: str | None = None  # path to package dir inside the venv
    enabled: bool = True
    extensions: list[str] | None = None
    skills: list[str] | None = None
    prompts: list[str] | None = None
    themes: list[str] | None = None
    index_url: str | None = None
    extra_index_urls: list[str] | None = None


@dataclass
class PackagesSettings:
    """Installed package list stored in settings."""

    list: list[PackageEntry] | None = None


@dataclass
class TerminalSettings:
    shell_path: str | None = None  # custom shell binary (default: system shell)
    shell_command_prefix: str | None = None  # lines prepended inside the shell before each command


@dataclass
class HTTPProxySettings:
    url: str | None = None  # Proxy URL for both HTTP and HTTPS (overrides env vars)
    no_proxy: str | None = None  # Comma-separated hosts to exclude from proxying
    headers: dict[str, str] | None = None  # Custom headers for proxy (e.g., authentication)


@dataclass
class Settings:
    # Per-modality model selection. Legacy flat ``model``/``provider`` string
    # keys are migrated into ``model.text`` on load (see SettingsManager).
    model: ModelSettings | None = None
    thinking_level: ThinkingLevel | None = None
    transport: Transport | None = None
    enabled_models: list[str] | None = None

    # UI
    theme: str | None = None
    show_thinking: bool | None = None
    show_tool_calls: bool | None = None
    show_images: bool | None = None
    picker_max_visible: int | None = None
    tool_result_preview_lines: int | None = None

    # Queue behaviour
    steering_mode: SteeringMode | None = None
    follow_up_mode: FollowupMode | None = None

    # Nested sub-settings
    retry: RetrySettings | None = None
    thinking_budgets: ThinkingBudgetsSettings | None = None
    image: ImageSettings | None = None
    compaction: CompactionSettings | None = None
    branch_summary: BranchSummarySettings | None = None

    # Execution
    terminal: TerminalSettings | None = None

    # Network
    http_idle_timeout_ms: int | None = None  # idle timeout for LLM HTTP streams (default: 60000)
    websocket_connect_timeout_ms: int | None = None  # WebSocket connect/open handshake timeout
    http_proxy: HTTPProxySettings | None = None  # HTTP proxy configuration (overrides env vars)

    # Project trust (global only)
    project_trust: Literal["ask", "always", "never"] | None = (
        None  # default: "ask" — controls loading of project files (.tau/ config, extensions),
        # project context files (AGENTS.md/CLAUDE.md), and project skills
    )

    # Session
    session_dir: str | None = None

    # Startup
    quiet_startup: bool | None = None  # suppress startup notices (default: False)

    # UI behaviour
    double_escape_action: Literal["fork", "tree", "none"] | None = (
        None  # action on double-Escape with empty editor (default: "fork")
    )
    tree_filter_mode: Literal["default", "no-tools", "user-only", "labeled-only", "all"] | None = (
        None  # default /tree filter mode
    )
    autocomplete_max_visible: int | None = None  # max items in autocomplete dropdown (default: 5)
    show_hardware_cursor: bool | None = (
        None  # show terminal cursor while positioning (IME support, default: False)
    )
    editor_padding_x: int | None = None  # horizontal padding for the input editor (default: 0)

    # Extensions
    extensions: ExtensionsSettings | None = None

    # Packages
    packages: PackagesSettings | None = None

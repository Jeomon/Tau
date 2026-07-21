"""
RPC protocol type definitions.

Mirrors the reference implementation (rpc-types.ts).
All values are plain dicts in practice — these are provided as
documentation and for type-checker hints only.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

# ── Commands (stdin) ──────────────────────────────────────────────────────────


class RpcAttachment(TypedDict, total=False):
    """A single media attachment on a prompt/steer/follow-up command.

    ``kind`` selects the content block. Exactly one source must be supplied:
      - ``data``: base64-encoded bytes (any kind)
      - ``path``: server-side file path, read into bytes (any kind)
      - ``url``:  remote URL (images only)
    ``mimeType`` and ``name`` are optional metadata.
    """

    kind: Literal["image", "audio", "video", "file"]
    data: str
    path: str
    url: str
    mimeType: str
    name: str


class PromptCommand(TypedDict, total=False):
    type: Literal["prompt"]
    id: str
    message: str
    attachments: list[RpcAttachment]
    streamingBehavior: Literal["steer", "followUp"]


class SteerCommand(TypedDict, total=False):
    type: Literal["steer"]
    id: str
    message: str
    attachments: list[RpcAttachment]


class FollowUpCommand(TypedDict, total=False):
    type: Literal["follow_up"]
    id: str
    message: str
    attachments: list[RpcAttachment]


class AbortCommand(TypedDict, total=False):
    type: Literal["abort"]
    id: str


class NewSessionCommand(TypedDict, total=False):
    type: Literal["new_session"]
    id: str
    parentSession: str


class GetStateCommand(TypedDict, total=False):
    type: Literal["get_state"]
    id: str


class SetModelCommand(TypedDict, total=False):
    type: Literal["set_model"]
    id: str
    modelId: str
    provider: str


class CycleModelCommand(TypedDict, total=False):
    type: Literal["cycle_model"]
    id: str


class GetAvailableModelsCommand(TypedDict, total=False):
    type: Literal["get_available_models"]
    id: str


class SetThinkingLevelCommand(TypedDict, total=False):
    type: Literal["set_thinking_level"]
    id: str
    level: str


class CycleThinkingLevelCommand(TypedDict, total=False):
    type: Literal["cycle_thinking_level"]
    id: str


class SetSteeringModeCommand(TypedDict, total=False):
    type: Literal["set_steering_mode"]
    id: str
    mode: Literal["all", "one-at-a-time"]


class SetFollowUpModeCommand(TypedDict, total=False):
    type: Literal["set_follow_up_mode"]
    id: str
    mode: Literal["all", "one-at-a-time"]


class CompactCommand(TypedDict, total=False):
    type: Literal["compact"]
    id: str
    customInstructions: str


class SetAutoCompactionCommand(TypedDict, total=False):
    type: Literal["set_auto_compaction"]
    id: str
    enabled: bool


class SetAutoRetryCommand(TypedDict, total=False):
    type: Literal["set_auto_retry"]
    id: str
    enabled: bool


class AbortRetryCommand(TypedDict, total=False):
    type: Literal["abort_retry"]
    id: str


class TerminalCommand(TypedDict, total=False):
    type: Literal["terminal"]
    id: str
    command: str
    excludeFromContext: bool


class AbortTerminalCommand(TypedDict, total=False):
    type: Literal["abort_terminal"]
    id: str


class GetSessionStatsCommand(TypedDict, total=False):
    type: Literal["get_session_stats"]
    id: str


class ExportHtmlCommand(TypedDict, total=False):
    type: Literal["export_html"]
    id: str
    outputPath: str


class SwitchSessionCommand(TypedDict, total=False):
    type: Literal["switch_session"]
    id: str
    sessionPath: str


class ForkCommand(TypedDict, total=False):
    type: Literal["fork"]
    id: str
    entryId: str
    position: Literal["before", "at"]


class CloneCommand(TypedDict, total=False):
    type: Literal["clone"]
    id: str


class GetForkMessagesCommand(TypedDict, total=False):
    type: Literal["get_fork_messages"]
    id: str


class GetLastAssistantTextCommand(TypedDict, total=False):
    type: Literal["get_last_assistant_text"]
    id: str


class SetSessionNameCommand(TypedDict, total=False):
    type: Literal["set_session_name"]
    id: str
    name: str


class GetMessagesCommand(TypedDict, total=False):
    type: Literal["get_messages"]
    id: str


class GetEntriesCommand(TypedDict, total=False):
    """Raw session entries. ``since`` returns only what follows that entry id."""

    type: Literal["get_entries"]
    id: str
    since: str


class GetTreeCommand(TypedDict, total=False):
    """The session's branch tree, entries nested under their parents."""

    type: Literal["get_tree"]
    id: str


class GetCommandsCommand(TypedDict, total=False):
    type: Literal["get_commands"]
    id: str


class ExtensionUIResponseCommand(TypedDict, total=False):
    type: Literal["extension_ui_response"]
    id: str
    value: Any
    confirmed: bool
    cancelled: bool


# ── Responses (stdout) ────────────────────────────────────────────────────────


class RpcResponse(TypedDict, total=False):
    type: Literal["response"]
    command: str
    id: str
    success: bool
    data: Any
    error: str


# ── Extension UI requests (stdout) ────────────────────────────────────────────


class SelectUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["select"]
    title: str
    options: list[str]
    timeout: int


class MultiSelectUIRequest(TypedDict, total=False):
    """Pick zero or more of ``options``.

    The reply's ``value`` is a list of chosen labels (``[]`` is a valid answer,
    meaning "none of these"); ``cancelled`` dismisses. Tau-specific — the
    reference protocol has no multi-select shape.
    """

    type: Literal["extension_ui_request"]
    id: str
    method: Literal["multi_select"]
    title: str
    options: list[str]
    timeout: int


class ConfirmUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["confirm"]
    title: str
    message: str
    timeout: int


class InputUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["input"]
    title: str
    placeholder: str
    timeout: int


class EditorUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["editor"]
    title: str
    prefill: str


class NotifyUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["notify"]
    message: str
    notifyType: Literal["info", "warning", "error"]


class SetStatusUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["setStatus"]
    statusKey: str
    statusText: str | None


class SetWidgetUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["setWidget"]
    widgetKey: str
    widgetLines: list[str] | None
    widgetPlacement: Literal["aboveEditor", "belowEditor"]


class SetTitleUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["setTitle"]
    title: str


class SetEditorTextUIRequest(TypedDict, total=False):
    type: Literal["extension_ui_request"]
    id: str
    method: Literal["set_editor_text"]
    text: str


# ── Session state ─────────────────────────────────────────────────────────────


class RpcSessionState(TypedDict, total=False):
    model: dict[str, Any] | None
    thinkingLevel: str | None
    isStreaming: bool
    isCompacting: bool
    steeringMode: Literal["all", "one-at-a-time"]
    followUpMode: Literal["all", "one-at-a-time"]
    sessionFile: str | None
    sessionId: str | None
    sessionName: str | None
    autoCompactionEnabled: bool
    messageCount: int
    pendingMessageCount: int

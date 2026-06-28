from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field as PydanticField

from tau.inference.types import StopReason
from tau.inference.utils import ErrorKind
from tau.message.utils import audio_to_base64, image_to_base64, video_to_base64
from tau.tool.types import ToolKind

if TYPE_CHECKING:
    from tau.session.types import CustomMessageEntry


@dataclass
class TextContent:
    """Plain text content (system/user/assistant messages)."""

    type: Literal["text"] = "text"
    content: str = ""


@dataclass
class ImageContent:
    """Image content (PIL images, bytes, URLs, or base64 strings)."""

    type: Literal["image"] = "image"
    images: list[str | Any | bytes] = field(default_factory=list)  # str | PIL Image | bytes
    dimension_note: str | None = None

    def __post_init__(self) -> None:
        # Normalize raw bytes / PIL images to base64 strings so the content is
        # always JSON-serializable for session persistence (pydantic cannot
        # encode non-UTF-8 image bytes). Existing base64 and URL strings pass
        # through unchanged, and to_base64() re-detects the MIME type from the
        # base64 magic bytes, so downstream API/render code is unaffected.
        normalized: list[str | Any | bytes] = []  # str | PIL Image | bytes
        for img in self.images:
            if isinstance(img, str):
                normalized.append(img)
            else:
                b64, _ = image_to_base64(img)
                normalized.append(b64)
        self.images = normalized

    def to_base64(self) -> list[tuple[str, str]]:
        """Convert all images to (base64_data, mime_type) pairs.

        Returns:
            List of (base64_string, mime_type) tuples.
        """
        return [image_to_base64(img) for img in self.images]

    @classmethod
    def from_file(cls, path: str | Path) -> ImageContent:
        """Load image from a file path.

        Args:
            path: Path to the image file.

        Returns:
            An ImageContent instance with the loaded image bytes.
        """
        return cls(images=[Path(path).read_bytes()])

    @classmethod
    def from_url(cls, url: str) -> ImageContent:
        """Create ImageContent from a URL.

        Args:
            url: The image URL.

        Returns:
            An ImageContent instance with the URL.
        """
        return cls(images=[url])


@dataclass
class AudioContent:
    """Audio content (bytes, base64 strings, or 'file:' paths)."""

    type: Literal["audio"] = "audio"
    # Each item is raw bytes, a base64 string, or a file path string prefixed with "file:"
    audio: list[bytes | str] = field(default_factory=list)

    def to_base64(self) -> list[tuple[str, str]]:
        """Convert all audio to (base64_data, mime_type) pairs.

        Returns:
            List of (base64_string, mime_type) tuples.
        """
        return [audio_to_base64(item) for item in self.audio]

    @classmethod
    def from_file(cls, path: str | Path) -> AudioContent:
        """Load audio from a file path.

        Args:
            path: Path to the audio file.

        Returns:
            An AudioContent instance with the loaded audio bytes.
        """
        return cls(audio=[Path(path).read_bytes()])

    @classmethod
    def from_base64(cls, data: str, mime_type: str | None = None) -> AudioContent:
        """Create AudioContent from a base64-encoded string.

        Args:
            data: Base64-encoded audio data.
            mime_type: Optional MIME type hint (not currently used).

        Returns:
            An AudioContent instance with the base64 data.
        """
        return cls(audio=[data])


@dataclass
class VideoContent:
    """Video content (bytes, base64 strings, or 'file:' paths)."""

    type: Literal["video"] = "video"
    video: list[bytes | str] = field(default_factory=list)

    def to_base64(self) -> list[tuple[str, str]]:
        return [video_to_base64(item) for item in self.video]

    @classmethod
    def from_file(cls, path: str | Path) -> VideoContent:
        return cls(video=[Path(path).read_bytes()])


@dataclass
class ThinkingContent:
    """Extended thinking content from Claude models with thinking enabled."""

    type: Literal["thinking"] = "thinking"
    content: str = ""
    signature: str = ""


@dataclass
class ToolCallContent:
    """Tool invocation from assistant with args, semantic kind, and call id."""

    type: Literal["tool_call"] = "tool_call"
    id: str = ""
    name: str = ""
    kind: ToolKind | None = None
    args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultContent:
    """Tool execution result paired with ToolCallContent by id."""

    type: Literal["tool_result"] = "tool_result"
    id: str = ""
    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    terminate: bool = False
    terminate_message: str | None = None
    tool_name: str = ""


@dataclass
class LinesContent:
    """Pre-rendered lines for notify(list[str]) — rendered via apply_render_shell."""

    type: Literal["lines"] = "lines"
    lines: list[str] = field(default_factory=list)
    notify_type: str = "info"


Content = (
    TextContent
    | ImageContent
    | AudioContent
    | VideoContent
    | ThinkingContent
    | ToolCallContent
    | ToolResultContent
)

# Per-role content constraints (for type hints and documentation).
SystemContent = TextContent
UserContent = TextContent | ImageContent | AudioContent | VideoContent | ToolResultContent
AssistantContent = TextContent | ThinkingContent | ToolCallContent
ToolContent = ToolResultContent


@dataclass
class UsageCost:
    """Monetized costs in USD for input, output, cache operations, and total."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass
class Usage:
    """Token counts and costs for a single LLM completion."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_1h_tokens: int = 0
    input_tokens_include_cache_read: bool = False
    cost: UsageCost = field(default_factory=UsageCost)


class Role(StrEnum):
    """Message roles in the conversation history."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    CUSTOM = "custom"
    SKILL_INVOCATION = "skill_invocation"
    TEMPLATE_INVOCATION = "template_invocation"
    BASH_EXECUTION = "terminal_execution"
    COMPACTION_SUMMARY = "compaction_summary"
    BRANCH_SUMMARY = "branch_summary"


@dataclass
class BaseMessage:
    """Common fields for all message types (contents, id, timestamp)."""

    contents: list[Content] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


@dataclass
class SystemMessage(BaseMessage):
    """System context and instructions for the LLM."""

    role: Literal[Role.SYSTEM] = field(default=Role.SYSTEM, kw_only=True)

    @classmethod
    def text(cls, content: str) -> SystemMessage:
        """Construct SystemMessage from plain text.

        Args:
            content: The system message text.

        Returns:
            A SystemMessage with the text content.
        """
        return cls(contents=[TextContent(content=content)])


@dataclass
class UserMessage(BaseMessage):
    """User input containing text, images, audio, and/or tool results."""

    role: Literal[Role.USER] = field(default=Role.USER, kw_only=True)

    @classmethod
    def from_text(cls, content: str) -> UserMessage:
        """Construct UserMessage from plain text.

        Args:
            content: The user message text.

        Returns:
            A UserMessage with the text content.
        """
        return cls(contents=[TextContent(content=content)])

    @classmethod
    def with_images(
        cls, content: str, images: list[str | Any | bytes]
    ) -> UserMessage:  # str | PIL Image | bytes
        """Construct UserMessage with text and images.

        Args:
            content: The user message text.
            images: List of PIL Images, image bytes, or image URLs.

        Returns:
            A UserMessage with text and image content.
        """
        return cls.with_media(content, images=images)

    @classmethod
    def with_audio(cls, content: str, audio: list[bytes | str]) -> UserMessage:
        """Construct UserMessage with text and audio.

        Args:
            content: The user message text.
            audio: List of audio bytes, base64 strings, or 'file:' paths.

        Returns:
            A UserMessage with text and audio content.
        """
        return cls.with_media(content, audio=audio)

    @classmethod
    def with_video(cls, content: str, video: list[bytes | str]) -> UserMessage:
        """Construct UserMessage with text and video.

        Args:
            content: The user message text.
            video: List of video bytes, base64 strings, or 'file:' paths.

        Returns:
            A UserMessage with text and video content.
        """
        return cls.with_media(content, video=video)

    @classmethod
    def with_media(
        cls,
        content: str,
        images: Sequence[str | Any | bytes] | None = None,  # str | PIL Image | bytes
        audio: Sequence[bytes | str] | None = None,
        video: Sequence[bytes | str] | None = None,
    ) -> UserMessage:
        """Construct UserMessage with text plus any combination of media.

        Each present modality is appended as its own content block, so a single
        message can carry images, audio, and video together. Providers that don't
        support a given modality simply skip that block.

        Args:
            content: The user message text.
            images: Optional images (PIL Images, bytes, or URLs).
            audio: Optional audio (bytes, base64 strings, or 'file:' paths).
            video: Optional video (bytes, base64 strings, or 'file:' paths).

        Returns:
            A UserMessage with text and every supplied media block.
        """
        contents: list[Content] = [TextContent(content=content)]
        if images:
            contents.append(ImageContent(images=list(images)))
        if audio:
            contents.append(AudioContent(audio=list(audio)))
        if video:
            contents.append(VideoContent(video=list(video)))
        return cls(contents=contents)


@dataclass
class AssistantMessage(BaseMessage):
    """LLM response with text, thinking, tool calls, usage, and stop reason."""

    role: Literal[Role.ASSISTANT] = field(default=Role.ASSISTANT, kw_only=True)
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = StopReason.Stop
    error: str = ""
    error_kind: ErrorKind = (
        ErrorKind.UNKNOWN
    )  # classification of `error`, used to drive recovery (e.g. compaction on overflow)

    @classmethod
    def from_text(cls, content: str) -> AssistantMessage:
        """Create an AssistantMessage with a single TextContent block."""
        return cls(contents=[TextContent(content=content)])

    def text_content(self) -> str:
        """Concatenate all TextContent items.

        Returns:
            The concatenated text from all text content blocks.
        """
        return "".join(c.content for c in self.contents if isinstance(c, TextContent))

    def tool_calls(self) -> list[ToolCallContent]:
        """Extract all ToolCallContent items.

        Returns:
            List of all tool calls in this message.
        """
        return [c for c in self.contents if isinstance(c, ToolCallContent)]

    def thinking(self) -> list[ThinkingContent]:
        """Extract all ThinkingContent items.

        Returns:
            List of all extended thinking blocks in this message.
        """
        return [c for c in self.contents if isinstance(c, ThinkingContent)]


@dataclass
class ToolMessage(BaseMessage):
    """Tool execution results responding to ToolCallContent."""

    role: Literal[Role.TOOL] = field(default=Role.TOOL, kw_only=True)

    @classmethod
    def from_results(cls, results: list[ToolResultContent]) -> ToolMessage:
        """Construct ToolMessage from multiple tool results.

        Args:
            results: List of tool execution results.

        Returns:
            A ToolMessage containing all the tool results.
        """
        return cls(contents=list(results))  # type: ignore[arg-type]

    @classmethod
    def from_result(cls, result: ToolResultContent) -> ToolMessage:
        """Construct ToolMessage from a single tool result.

        Args:
            result: A single tool execution result.

        Returns:
            A ToolMessage containing the tool result.
        """
        return cls(contents=[result])  # type: ignore[arg-type]


LLMMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage


@dataclass
class CustomMessage:
    """Application-defined message with custom type, contents, and details."""

    role: Literal[Role.CUSTOM] = field(default=Role.CUSTOM, kw_only=True)
    custom_type: str = ""
    timestamp: float = field(default_factory=time.time)
    contents: list[TextContent | ImageContent | LinesContent] = field(default_factory=list)
    details: Any | None = None

    @classmethod
    def from_session(cls, entry: CustomMessageEntry) -> CustomMessage:
        """Reconstruct CustomMessage from session storage entry.

        Args:
            entry: A CustomMessageEntry from the session JSONL.

        Returns:
            A CustomMessage reconstructed from the session entry.
        """
        raw = entry.content
        # Normalize content to list of TextContent or ImageContent
        from typing import cast

        contents: list[TextContent | ImageContent | LinesContent]
        if isinstance(raw, list):
            contents = cast(list[TextContent | ImageContent | LinesContent], raw)
        elif isinstance(raw, str):
            contents = cast(
                list[TextContent | ImageContent | LinesContent], [TextContent(content=raw)]
            )
        else:
            contents = []
        return cls(
            custom_type=entry.custom_type,
            contents=contents,
            timestamp=entry.timestamp,
            details=entry.details,
        )


@dataclass
class SkillInvocationMessage:
    """A skill invocation — shown collapsed in the TUI, Ctrl+O to expand."""

    role: Literal[Role.SKILL_INVOCATION] = field(default=Role.SKILL_INVOCATION, kw_only=True)
    name: str = ""
    args: str = ""
    content: str = ""
    expanded: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class TemplateInvocationMessage:
    """A prompt template invocation — shown collapsed in the TUI, Ctrl+O to expand."""

    role: Literal[Role.TEMPLATE_INVOCATION] = field(default=Role.TEMPLATE_INVOCATION, kw_only=True)
    name: str = ""
    args: str = ""
    expanded_content: str = ""
    expanded: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class TerminalExecutionMessage:
    """Result of a user-initiated ! shell command, containing both the command and its output."""

    role: Literal[Role.BASH_EXECUTION] = field(default=Role.BASH_EXECUTION, kw_only=True)
    command: str = ""
    output: str = ""
    exit_code: int | None = None
    cancelled: bool = False
    timestamp: float = field(default_factory=time.time)
    exclude: bool = False

    def to_user_message(self) -> UserMessage:
        """Convert to a UserMessage for LLM context."""
        text = f"Ran `{self.command}`\n"
        if self.output:
            text += f"```\n{self.output}\n```"
        else:
            text += "(no output)"

        if self.cancelled:
            text += "\n\n(command cancelled)"
        elif self.exit_code is not None and self.exit_code != 0:
            text += f"\n\nCommand exited with code {self.exit_code}"

        return UserMessage.from_text(text)


@dataclass
class CompactionSummaryMessage:
    """Injected at the start of context after a compaction — represents summarised history."""

    role: Literal[Role.COMPACTION_SUMMARY] = field(default=Role.COMPACTION_SUMMARY, kw_only=True)
    summary: str = ""
    tokens_before: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class BranchSummaryMessage:
    """Injected into context when returning from a branch — represents the abandoned path."""

    role: Literal[Role.BRANCH_SUMMARY] = field(default=Role.BRANCH_SUMMARY, kw_only=True)
    summary: str = ""
    from_id: str = ""
    timestamp: float = field(default_factory=time.time)


SessionMessage = CustomMessage

# Discriminated on `role` so each persisted message deserializes to its exact
# class (and an unknown role fails loudly rather than silently collapsing to the
# first structurally-compatible member).
AgentMessage = Annotated[
    SystemMessage
    | UserMessage
    | AssistantMessage
    | ToolMessage
    | TerminalExecutionMessage
    | CustomMessage
    | CompactionSummaryMessage
    | BranchSummaryMessage,
    PydanticField(discriminator="role"),
]

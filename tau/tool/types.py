from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from tau.inference.api.text.service import TextLLM as LLM
    from tau.message.types import AudioContent, ImageContent, VideoContent
    from tau.settings.manager import SettingsManager


@dataclass
class ToolError:
    """File-level tool load failure with optional stack trace."""

    path: str
    error: str
    stack: str = ""


@dataclass
class LoadToolResults:
    """Aggregate result of loading tools from one or more directories."""

    tools: list[Tool] = field(default_factory=list)
    errors: list[ToolError] = field(default_factory=list)


class ToolKind(StrEnum):
    """Semantic category used by the engine to apply execution policy to a tool call."""

    Read = "read"
    Edit = "edit"
    Write = "write"
    Execute = "execute"
    Web = "web"


class ToolExecutionMode(StrEnum):
    """Controls how the engine schedules concurrent calls to the same tool."""

    Sequential = "sequential"
    Parallel = "parallel"
    Batch = "batch"


@dataclass
class ToolInvocation:
    """Complete tool call specification with resolved args and execution context."""

    id: str
    name: str
    cwd: Path | None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Tool execution outcome with optional error flag and early termination signal."""

    id: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    terminate: bool = False
    terminate_message: str | None = None
    # Media a tool wants to hand back to the model alongside its text content
    # (e.g. Read returning an image) — see ToolResultContent.image/audio/video
    # for which providers can actually deliver it to the model.
    image: ImageContent | None = None
    audio: AudioContent | None = None
    video: VideoContent | None = None

    @classmethod
    def ok(
        cls,
        id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        image: ImageContent | None = None,
        audio: AudioContent | None = None,
        video: VideoContent | None = None,
    ) -> ToolResult:
        """Construct a successful outcome.

        Args:
            id: The tool call ID this result corresponds to.
            content: The result content (output of the tool).
            metadata: Optional metadata dict (default empty).
            image: Optional image block to attach to the result.
            audio: Optional audio block to attach to the result.
            video: Optional video block to attach to the result.

        Returns:
            A ToolResult with is_error=False.
        """
        return cls(
            id=id,
            content=content,
            is_error=False,
            metadata=metadata or {},
            image=image,
            audio=audio,
            video=video,
        )

    @classmethod
    def with_images(
        cls,
        id: str,
        content: str,
        images: Sequence[str | Any | bytes],  # str | PIL Image | bytes
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Construct a successful outcome carrying one or more images.

        Args:
            id: The tool call ID this result corresponds to.
            content: The result's text content.
            images: PIL Images, image bytes, or image URLs.
            metadata: Optional metadata dict (default empty).

        Returns:
            A ToolResult with is_error=False and the images attached.
        """
        return cls.with_media(id, content, images=images, metadata=metadata)

    @classmethod
    def with_audio(
        cls,
        id: str,
        content: str,
        audio: Sequence[bytes | str],
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Construct a successful outcome carrying an audio clip.

        Args:
            id: The tool call ID this result corresponds to.
            content: The result's text content.
            audio: Audio bytes, base64 strings, or 'file:' paths.
            metadata: Optional metadata dict (default empty).

        Returns:
            A ToolResult with is_error=False and the audio attached.
        """
        return cls.with_media(id, content, audio=audio, metadata=metadata)

    @classmethod
    def with_video(
        cls,
        id: str,
        content: str,
        video: Sequence[bytes | str],
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Construct a successful outcome carrying a video clip.

        Args:
            id: The tool call ID this result corresponds to.
            content: The result's text content.
            video: Video bytes, base64 strings, or 'file:' paths.
            metadata: Optional metadata dict (default empty).

        Returns:
            A ToolResult with is_error=False and the video attached.
        """
        return cls.with_media(id, content, video=video, metadata=metadata)

    @classmethod
    def with_media(
        cls,
        id: str,
        content: str,
        images: Sequence[str | Any | bytes] | None = None,  # str | PIL Image | bytes
        audio: Sequence[bytes | str] | None = None,
        video: Sequence[bytes | str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Construct a successful outcome carrying any combination of media.

        Each present modality is wrapped into its own content block, mirroring
        ``UserMessage.with_media`` — only providers with native tool-result
        media support (Anthropic, Gemini, OpenAI Responses) can deliver it to
        the model; others silently ignore it.

        Args:
            id: The tool call ID this result corresponds to.
            content: The result's text content.
            images: Optional images (PIL Images, bytes, or URLs).
            audio: Optional audio (bytes, base64 strings, or 'file:' paths).
            video: Optional video (bytes, base64 strings, or 'file:' paths).
            metadata: Optional metadata dict (default empty).

        Returns:
            A ToolResult with is_error=False and every supplied media block attached.
        """
        from tau.message.types import AudioContent, ImageContent, VideoContent

        return cls.ok(
            id,
            content,
            metadata=metadata,
            image=ImageContent(images=list(images)) if images else None,
            audio=AudioContent(audios=list(audio)) if audio else None,
            video=VideoContent(videos=list(video)) if video else None,
        )

    @classmethod
    def error(
        cls,
        id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Construct a failed outcome.

        Args:
            id: The tool call ID this result corresponds to.
            content: The error message or description.
            metadata: Optional metadata dict (default empty).

        Returns:
            A ToolResult with is_error=True.
        """
        return cls(id=id, content=content, is_error=True, metadata=metadata or {})


ToolExecutionUpdateCallback = Callable[[ToolResult], Awaitable[None]]

AbortSignal = asyncio.Event


@dataclass
class ToolContext:
    """Runtime services available to tools during execution (LLM, agents, managers, etc)."""

    llm: LLM | None = None
    cwd: Path | None = None
    settings: SettingsManager | None = None


@dataclass
class ToolRenderOptions:
    """Render-time flags passed to render_result callbacks.

    is_error:   True when the tool call returned an error.
    expanded:   True when the user has toggled tool results open (Ctrl+O).
    is_partial: True while the tool is still executing (streaming output).
    metadata:   Arbitrary data the tool stored in ToolResult.metadata.
    theme:      The active UI theme — the stable styling surface for renderers.
                Use its semantic colour roles instead of importing ANSI codes
                from internal modules (which is fragile across versions,
                especially for extensions loaded from ~/.tau): ``theme.muted``
                (dim), ``theme.error`` (red), ``theme.warning`` (yellow),
                ``theme.success`` (green), ``theme.accent``, ``theme.emphasis``.
                Each takes a string and returns it wrapped with reset. May be
                None outside the interactive TUI.
    """

    is_error: bool = False
    expanded: bool = False
    is_partial: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    theme: Any = None


class Tool(ABC):
    """Abstract base for tools: executable, schema-validated components with metadata and policy."""

    # Explicit class-level annotations so Pyright infers the correct attribute
    # types at every call site without relying on __init__ parameter inference.
    render_call: Callable[[dict, bool], list[str]] | None
    render_result: Callable[[str, ToolRenderOptions], list[str]] | None
    render_shell: str
    result_expandable: bool
    result_preview_lines: int | None

    def __init__(
        self,
        name: str,
        description: str,
        schema: type[BaseModel],
        kind: ToolKind,
        execution_mode: ToolExecutionMode = ToolExecutionMode.Sequential,
        *,
        render_call: Callable[[dict, bool], list[str]] | None = None,
        render_result: Callable[[str, ToolRenderOptions], list[str]] | None = None,
        render_shell: str = "self",
        result_expandable: bool = True,
        result_preview_lines: int | None = None,
        prompt_snippet: str | None = None,
        prompt_guidelines: str | None = None,
        prepare_arguments: Callable[[dict], dict] | None = None,
    ) -> None:
        """Initialize tool with name, description, schema, kind, and execution concurrency policy.

        render_shell controls how the result block is framed in the TUI:
          "self"    (default) — renderer output is used as-is, no extra framing.
          "default" — the standard ``└ first_line`` shell is applied to the
                      renderer output and centrally handles preview/collapse hints.

        result_expandable disables central collapsing when False.
        result_preview_lines overrides the global default-shell preview threshold.
        """
        self.name = name
        self.description = description
        self.schema = schema
        self.kind = kind
        self.execution_mode = execution_mode
        self.render_call = render_call
        self.render_result = render_result
        self.render_shell = render_shell
        self.result_expandable = result_expandable
        self.result_preview_lines = result_preview_lines
        self.prompt_snippet = prompt_snippet
        self.prompt_guidelines = prompt_guidelines
        self.prepare_arguments = prepare_arguments

    def validate(self, params: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate params against schema; return (success, error_list).

        Args:
            params: Tool call parameters to validate.

        Returns:
            A tuple of (success: bool, errors: list[str]).
        """
        try:
            self.schema.model_validate(params)
            return True, []
        except Exception as e:
            from pydantic import ValidationError

            # Format Pydantic errors with field path for clarity
            if isinstance(e, ValidationError):
                errors = [
                    f"{' -> '.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                ]
            else:
                errors = [str(e)]
            return False, errors

    def to_json(self) -> dict[str, Any]:
        """Serialize to JSON schema with name, description, and input_schema.

        Returns:
            A dict with 'name', 'description', and 'input_schema' keys suitable for provider APIs.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema.model_json_schema(),
        }

    def _is_cancelled(self, signal: AbortSignal | None) -> bool:
        """Check if abort signal has been set.

        Args:
            signal: Optional asyncio.Event abort signal.

        Returns:
            True if the signal has been set, indicating cancellation requested.
        """
        return signal is not None and signal.is_set()

    @abstractmethod
    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the tool with params; subclasses must override.

        Args:
            invocation: Complete tool call specification with resolved parameters.
            tool_execution_update_callback: Optional callback for streaming updates.
            signal: Optional abort signal to check for user-initiated cancellation.
            context: Optional ToolContext with runtime services available to the tool.

        Returns:
            A ToolResult with the outcome, content, and optional error details.
        """
        ...

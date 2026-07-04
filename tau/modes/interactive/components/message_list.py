from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.input import InputEvent, Key, KeyEvent, get_keybindings
from tau.tui.markdown import render_markdown
from tau.tui.style import Style, apply_style
from tau.tui.theme import MessageTheme
from tau.tui.utils import BOLD, RESET, _is_diff, cursor_block, visible_width, wrap

if TYPE_CHECKING:
    from tau.tool.types import Tool

_TOOL_INDENT = "  "
_RESULT_INDENT = "    "
_DEFAULT_DETAIL_PREVIEW_LINES = 5


def _apply_nested_style(text: str, style: Style) -> str:
    """Apply a semantic style and restore it after nested ANSI resets."""
    if style == Style():
        return text
    prefix = style.sgr()
    return prefix + text.replace(RESET, RESET + prefix) + RESET


def _default_shell_preview(
    lines: list[str],
    *,
    expanded: bool,
    expandable: bool,
    preview_lines: int,
    theme: Any,
) -> list[str]:
    """Apply centralized default-shell collapsing and hints."""
    threshold = max(1, preview_lines)
    if not expandable or len(lines) <= threshold:
        return lines
    if expanded:
        return [*lines, apply_style(theme.dim, "(ctrl+o to collapse)")]
    hidden = len(lines) - threshold
    hint = apply_style(theme.dim, f"… +{hidden} lines (ctrl+o to expand)")
    return [*lines[:threshold], hint]


def apply_render_shell(lines: list[str], theme: Any, style: Style | None = None) -> list[str]:
    """Apply the standard └ framing to a pre-rendered list of lines.

    First line gets '    └ <line>', subsequent lines get '      <line>'.
    Optional style is applied to the first line (e.g. theme.error for red).
    Shared by tool results and notify so any style change propagates everywhere.
    """
    if not lines:
        return []
    first = apply_style(style, lines[0]) if style is not None else lines[0]
    out = [f"{_RESULT_INDENT}{apply_style(theme.dim, '└')} {first}"]
    out.extend(f"{_RESULT_INDENT}  {line}" for line in lines[1:])
    return out


# ── MessageBlock ──────────────────────────────────────────────────────────────


class MessageBlock:
    """
    Cached rendering of a single message (any type).

    Pass a MessageTheme to control all colours.  Call invalidate() whenever
    the underlying message changes (e.g. a streaming token arrives).
    """

    def __init__(
        self,
        message: object,
        streaming: bool = False,
        theme: MessageTheme | None = None,
        user_prefix: str = "❯ ",
        tool_lookup: Callable[[str], Tool | None] | None = None,
        tool_result_preview_lines: int = _DEFAULT_DETAIL_PREVIEW_LINES,
    ) -> None:
        self._message = message
        self._streaming = streaming
        self._finalized = False
        self._expanded = False
        self._theme = theme or MessageTheme()
        self._user_prefix = user_prefix
        self._tool_lookup = tool_lookup
        self._tool_result_preview_lines = max(1, tool_result_preview_lines)
        self._cached: list[str] | None = None
        self._cached_width = 0
        # Keyed by (content_idx, image_idx) — persisted so Kitty image IDs stay stable
        self._image_components: dict[tuple[int, int], Any] = {}
        self._tool_results_cache: list[str] | None = None
        self._tool_results_message: object | None = None
        self._tool_results_width = 0

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def theme(self) -> MessageTheme:
        """Return the active message theme."""
        return self._theme

    def invalidate(self) -> None:
        self._cached = None
        self._tool_results_cache = None
        self._tool_results_message = None

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self.invalidate()

    def is_expanded(self) -> bool:
        """Return whether this block is in expanded view."""
        return self._expanded

    @property
    def is_streaming(self) -> bool:
        """Return whether this block's content may still be actively changing."""
        return self._streaming

    def set_streaming(self, value: bool) -> None:
        if self._streaming != value:
            self._streaming = value
            self.invalidate()

    def finalize(self) -> None:
        """Mark this block as guaranteed to never be mutated again.

        ``is_streaming`` alone isn't sufficient proof of that — the driver
        that owns a block's lifecycle (agent_hooks.py) can report
        streaming=False for a moment before the block is truly done (e.g. the
        placeholder created at message_start, or a lull between token-batch
        flushes). Call this only at the exact point that driver is dropping
        its own reference to the block for good, so MessageList can freeze it
        immediately regardless of position instead of waiting for some later
        message to prove it's finished.
        """
        self._finalized = True

    @property
    def is_settled(self) -> bool:
        """Return whether this block is explicitly confirmed to be finished."""
        return self._finalized

    def set_theme(self, theme: MessageTheme) -> None:
        self._theme = theme
        self.invalidate()

    def set_user_prefix(self, prefix: str) -> None:
        if self._user_prefix != prefix:
            self._user_prefix = prefix
            self.invalidate()

    def set_tool_lookup(self, fn: Callable[[str], Tool | None] | None) -> None:
        self._tool_lookup = fn
        self.invalidate()

    def _render_image(self, key: tuple[int, int], b64: str, mime: str, width: int) -> list[str]:
        if not self._theme.show_images:
            from tau.tui.components.image import Image

            return [Image(b64, mime)._fallback_text()]
        if key not in self._image_components:
            from tau.tui.components.image import Image

            self._image_components[key] = Image(b64, mime)
        return self._image_components[key].render(width)

    @property
    def message(self) -> object:
        return self._message

    # -------------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        if self._cached is not None and self._cached_width == width:
            return self._cached
        self._cached = self._build(width)
        self._cached_width = width
        return self._cached

    def render_with_tool_results(self, tool_message: object, width: int) -> list[str]:
        from tau.message.types import AssistantMessage, ToolMessage, ToolResultContent

        if not isinstance(self._message, AssistantMessage) or not isinstance(
            tool_message, ToolMessage
        ):
            return self.render(width)
        if (
            self._tool_results_cache is not None
            and self._tool_results_message is tool_message
            and self._tool_results_width == width
        ):
            return self._tool_results_cache

        results = {
            item.id: item for item in tool_message.contents if isinstance(item, ToolResultContent)
        }
        lines = self._render_assistant(self._message, width, results)

        matched_ids = {item.id for item in self._message.tool_calls() if item.id in results}
        for item in tool_message.contents:
            if isinstance(item, ToolResultContent) and item.id not in matched_ids:
                lines.extend(self._render_tool_result(item, width, item.tool_name))

        lines.append("")
        self._tool_results_cache = lines
        self._tool_results_message = tool_message
        self._tool_results_width = width
        return lines

    def _build(self, width: int) -> list[str]:
        from tau.message.types import (
            AssistantMessage,
            CustomMessage,
            TerminalExecutionMessage,
            ToolMessage,
            UserMessage,
        )

        msg = self._message
        lines: list[str] = []

        if isinstance(msg, UserMessage):
            lines.extend(self._render_user(msg, width))
        elif isinstance(msg, AssistantMessage):
            lines.extend(self._render_assistant(msg, width))
        elif isinstance(msg, ToolMessage):
            lines.extend(self._render_tool_message(msg, width))
        elif isinstance(msg, TerminalExecutionMessage):
            lines.extend(self._render_terminal(msg, width))
        elif isinstance(msg, CustomMessage):
            lines.extend(self._render_custom(msg, width))
        else:
            from tau.message.types import SkillInvocationMessage, TemplateInvocationMessage

            if isinstance(msg, TemplateInvocationMessage):
                lines.extend(self._render_template_invocation(msg, width))
            elif isinstance(msg, SkillInvocationMessage):
                lines.extend(self._render_skill_invocation(msg, width))
            else:
                lines.append(apply_style(self._theme.dim, str(msg)))

        from tau.message.types import (
            SkillInvocationMessage,
            TemplateInvocationMessage,
            TextContent,
            UserMessage,
        )

        is_command = isinstance(msg, UserMessage) and any(
            isinstance(c, TextContent) and c.content.lstrip().startswith("/") for c in msg.contents
        )
        if (
            not isinstance(msg, (CustomMessage, TemplateInvocationMessage, SkillInvocationMessage))
            and not is_command
        ):
            lines.append("")  # blank separator after each message
        return lines

    # -------------------------------------------------------------------------
    # Per-type renderers
    # -------------------------------------------------------------------------

    def _render_user(self, msg: Any, width: int) -> list[str]:
        from tau.message.types import ImageContent, TextContent, UserMessage

        if not isinstance(msg, UserMessage):
            return []
        t = self._theme
        prefix = self._user_prefix
        inner_width = max(1, width - visible_width(prefix))
        lines: list[str] = []
        for c_idx, item in enumerate(msg.contents):
            if isinstance(item, TextContent) and item.content:
                for line in wrap(item.content.rstrip(), inner_width):
                    lead = apply_style(t.you_label, prefix) if not lines else "  "
                    lines.append(lead + line)
            elif isinstance(item, ImageContent):
                for i_idx, (b64, mime) in enumerate(item.to_base64()):
                    lines.extend(self._render_image((c_idx, i_idx), b64, mime, inner_width))
        return lines

    def _render_assistant(
        self,
        msg: Any,
        width: int,
        tool_results: dict[str, Any] | None = None,
    ) -> list[str]:
        from tau.inference.types import StopReason
        from tau.message.types import (
            AssistantMessage,
            TextContent,
            ThinkingContent,
            ToolCallContent,
        )

        if not isinstance(msg, AssistantMessage):
            return []

        t = self._theme
        inner_width = max(1, width - 2)
        lines: list[str] = []

        has_content = any(
            (isinstance(c, TextContent) and c.content)
            or isinstance(c, (ThinkingContent, ToolCallContent))
            for c in msg.contents
        )

        if not has_content and msg.stop_reason == StopReason.Error and msg.error:
            lines.append(apply_style(t.error_label, "error"))
            for line in wrap(msg.error, inner_width):
                lines.append("  " + line)
            return lines

        # No "assistant" label — the content speaks for itself.
        from tau.message.types import ImageContent as _ImageContent

        for idx, item in enumerate(msg.contents):
            if isinstance(item, ThinkingContent):
                if not t.show_thinking:
                    continue
                if idx > 0 and isinstance(msg.contents[idx - 1], ThinkingContent):
                    continue

                thinking_parts: list[str] = []
                thinking_idx = idx
                while thinking_idx < len(msg.contents):
                    thinking = msg.contents[thinking_idx]
                    if not isinstance(thinking, ThinkingContent):
                        break
                    if thinking.content:
                        thinking_parts.append(thinking.content)
                    thinking_idx += 1
                thinking_text = "".join(thinking_parts).rstrip()
                thinking_lines = (
                    render_markdown(
                        thinking_text,
                        inner_width,
                        t.markdown,
                        preserve_soft_breaks=True,
                    )
                    if thinking_text
                    else []
                )
                # Thinking is a compact diagnostic stream. Markdown paragraph
                # separators otherwise introduce empty rows in the middle of
                # reasoning, making short thoughts consume excessive height.
                thinking_lines = [line for line in thinking_lines if line.strip()]
                if thinking_lines:
                    thinking_lines = [
                        _apply_nested_style(line, t.thinking) for line in thinking_lines
                    ]
                if not thinking_lines:
                    thinking_lines.append(apply_style(t.thinking, t.thinking_label))
                thinking_lines = _default_shell_preview(
                    thinking_lines,
                    expanded=self._expanded,
                    expandable=True,
                    preview_lines=self._tool_result_preview_lines,
                    theme=t,
                )
                lines.extend("  " + line for line in thinking_lines)
                lines.append("")

            elif isinstance(item, TextContent) and item.content:
                for line in render_markdown(item.content.rstrip(), inner_width, t.markdown):
                    lines.append("  " + line)

            elif isinstance(item, _ImageContent):
                for i_idx, (b64, mime) in enumerate(item.to_base64()):
                    lines.extend(self._render_image((idx, i_idx), b64, mime, inner_width))

            elif isinstance(item, ToolCallContent) and t.show_tool_calls:
                # Separate a tool call from preceding assistant text/image with a
                # blank line so the call block doesn't render flush against the
                # prose. Thinking blocks already append their own trailing blank,
                # and consecutive tool calls are spaced below, so only text/image
                # predecessors need a gap here.
                prev_item = msg.contents[idx - 1] if idx > 0 else None
                needs_gap = (
                    isinstance(prev_item, TextContent) and bool(prev_item.content)
                ) or isinstance(prev_item, _ImageContent)
                if needs_gap and lines:
                    lines.append("")
                tool = self._tool_lookup(item.name) if self._tool_lookup else None
                if tool is not None and tool.render_call is not None:
                    custom = tool.render_call(item.args, self._streaming)
                    if custom:
                        lines.extend(custom)
                else:
                    from tau.tool.render import call_line, display_name

                    if item.args:
                        first_val = next(iter(item.args.values()), "")
                        lines.extend(call_line(item.name, str(first_val) if first_val else ""))
                    else:
                        lines.append(f"{_TOOL_INDENT}{BOLD}{display_name(item.name)}{RESET}")
                if tool_results is not None and item.id in tool_results:
                    lines.extend(self._render_tool_result(tool_results[item.id], width, item.name))
                # Separate consecutive tool-call blocks with a blank line so
                # they don't render flush against each other (mirrors how
                # ThinkingContent spaces itself above).
                next_item = msg.contents[idx + 1] if idx + 1 < len(msg.contents) else None
                if isinstance(next_item, ToolCallContent):
                    lines.append("")

        if self._streaming:
            cursor = cursor_block()
            if lines:
                # If appending the cursor would push the last line past the
                # renderer's clamp width (inner_width + 2), the clamp wraps the
                # line at the first word boundary ("  "), stripping the indent
                # from the remaining content (e.g. a table's bottom border).
                # Put the cursor on a new line instead to avoid the misalignment.
                if visible_width(lines[-1]) + visible_width(cursor) > inner_width + 2:
                    lines.append("  " + cursor)
                else:
                    lines[-1] = lines[-1] + cursor
            else:
                lines.append("  " + cursor)
        elif msg.stop_reason == StopReason.Abort:
            lines.append("  " + apply_style(t.dim, "┌ User Interrupted"))

        return lines

    def _render_terminal(self, msg: Any, width: int) -> list[str]:
        from tau.message.types import TerminalExecutionMessage
        from tau.tui.utils import BRIGHT_RED

        if not isinstance(msg, TerminalExecutionMessage):
            return []
        t = self._theme
        label = apply_style(t.dim, "$ " + msg.command)
        if msg.cancelled:
            label += "  " + BRIGHT_RED + "(cancelled)" + RESET
        elif msg.exit_code is not None and msg.exit_code != 0:
            label += "  " + BRIGHT_RED + f"(exit {msg.exit_code})" + RESET
        lines = [label]
        if msg.output:
            for line in msg.output.rstrip().split("\n"):
                lines.append("  " + apply_style(t.dim, line))
        if self._streaming:
            lines.append("  " + cursor_block())
        return lines

    def _render_custom(self, msg: Any, width: int) -> list[str]:
        from tau.message.types import CustomMessage, LinesContent, TextContent

        if not isinstance(msg, CustomMessage):
            return []
        from tau.tui.markdown import message_renderer_registry

        custom = message_renderer_registry.render(msg, self._theme, width)
        if custom is not None:
            return custom
        t = self._theme
        for item in msg.contents:
            if isinstance(item, LinesContent):
                style = t.tool_result_err if item.notify_type == "error" else None
                return apply_render_shell(item.lines, t, style)
            if isinstance(item, TextContent) and item.content:
                lines = wrap(
                    item.content.rstrip(), max(1, width - visible_width(_RESULT_INDENT) - 4)
                )
                return apply_render_shell([line for line in lines], t)
        return []

    def _render_template_invocation(self, msg: Any, width: int) -> list[str]:
        from tau.message.types import TemplateInvocationMessage

        if not isinstance(msg, TemplateInvocationMessage):
            return []
        t = self._theme
        if msg.expanded:
            lines = [""]
            header = f"  {BOLD}/{msg.name}{RESET}"
            if msg.args:
                header += apply_style(t.dim, f"  {msg.args}")
            lines.append(header)
            lines.append("")
            for line in msg.expanded_content.splitlines():
                styled = apply_style(t.dim, line) if line.strip() == "" else line
                lines.append(f"  {styled}")
            lines.append("")
        else:
            name_args = f"/{msg.name}" + (f"  {msg.args}" if msg.args else "")
            hint = apply_style(t.dim, "  (ctrl+o to expand)")
            lines = [f"  {name_args}", f"  {hint}", ""]
        return lines

    def _render_skill_invocation(self, msg: Any, width: int) -> list[str]:
        from tau.message.types import SkillInvocationMessage
        from tau.tui.utils import BOLD, RESET

        if not isinstance(msg, SkillInvocationMessage):
            return []
        t = self._theme
        if msg.expanded:
            lines = [""]
            header = f"  {BOLD}/{msg.name}{RESET}"
            if msg.args:
                header += apply_style(t.dim, f"  {msg.args}")
            lines.append(header)
            lines.append("")
            for line in msg.content.splitlines():
                lines.append(f"  {line}")
            lines.append("")
        else:
            name_args = f"/{msg.name}" + (f"  {msg.args}" if msg.args else "")
            hint = apply_style(t.dim, "  (ctrl+o to expand)")
            lines = [f"  {name_args}", f"  {hint}", ""]
        return lines

    def _render_tool_message(self, msg: Any, width: int) -> list[str]:
        from tau.message.types import ToolMessage, ToolResultContent

        if not isinstance(msg, ToolMessage):
            return []
        lines: list[str] = []

        for item in msg.contents:
            if isinstance(item, ToolResultContent):
                lines.extend(self._render_tool_result(item, width, item.tool_name))

        return lines

    def _render_tool_result(self, item: Any, width: int, tool_name: str = "") -> list[str]:
        from tau.message.types import ToolResultContent

        if not isinstance(item, ToolResultContent):
            return []

        display_content = item.metadata.get("_display_content", item.content)
        tool = self._tool_lookup(tool_name) if (self._tool_lookup and tool_name) else None
        if tool is not None and tool.render_result is not None:
            from tau.tool.types import ToolRenderOptions

            opts = ToolRenderOptions(
                is_error=item.is_error,
                expanded=self._expanded,
                is_partial=self._streaming,
                metadata=item.metadata,
                theme=self._theme,
            )
            custom = tool.render_result(str(display_content), opts)
            if custom:
                # A custom renderer must return one terminal line per element.
                # Defensively flatten any embedded newlines so the differential
                # renderer's per-line height accounting stays correct — a single
                # element spanning multiple rows otherwise corrupts the diff.
                if any("\n" in str(c) for c in custom):
                    custom = [seg for c in custom for seg in str(c).split("\n")]
                if not item.is_error and item.metadata.get("_render_format") == "markdown":
                    custom = render_markdown(
                        "\n".join(str(line) for line in custom),
                        max(1, width - len(_RESULT_INDENT) - 2),
                        self._theme.markdown,
                    )
                if tool.render_shell == "default":
                    t = self._theme
                    style = t.tool_result_err if item.is_error else t.tool_result_ok
                    threshold = (
                        tool.result_preview_lines
                        if tool.result_preview_lines is not None
                        else self._tool_result_preview_lines
                    )
                    framed = _default_shell_preview(
                        list(custom),
                        expanded=self._expanded,
                        expandable=tool.result_expandable,
                        preview_lines=threshold,
                        theme=t,
                    )
                    if not framed:
                        return []
                    framed[0] = apply_style(style, framed[0])
                    lines = apply_render_shell(framed, t)
                else:
                    lines = list(custom)
                lines.extend(
                    _render_extra_blocks(
                        item.metadata,
                        self._expanded,
                        self._tool_result_preview_lines,
                        self._theme,
                    )
                )
                return lines

        t = self._theme
        style = t.tool_result_err if item.is_error else t.tool_result_ok
        content = str(display_content).strip() if display_content else ""
        all_lines = content.split("\n") if content else []
        if not all_lines:
            rendered = [apply_style(style, "(no output)")]
        elif not item.is_error and _is_diff(content):
            from tau.tui.utils import render_diff

            diff_lines = render_diff(
                content,
                added=lambda s: apply_style(t.diff_added, s),
                removed=lambda s: apply_style(t.diff_removed, s),
                context=lambda s: apply_style(t.diff_context, s),
                hunk=lambda s: apply_style(t.diff_hunk, s),
                inverse=t.diff_inverse,
            )
            rendered = diff_lines or [apply_style(style, "(empty diff)")]
        elif not item.is_error and item.metadata.get("_render_format") == "markdown":
            rendered = render_markdown(
                content,
                max(1, width - len(_RESULT_INDENT) - 2),
                t.markdown,
            )
            if not rendered:
                rendered = [apply_style(style, "(no output)")]
        else:
            rendered = [apply_style(style, all_lines[0])]
            rendered += [apply_style(t.dim, line) for line in all_lines[1:]]
        rendered = _default_shell_preview(
            rendered,
            expanded=self._expanded,
            expandable=tool.result_expandable if tool is not None else True,
            preview_lines=(
                tool.result_preview_lines
                if tool is not None and tool.result_preview_lines is not None
                else self._tool_result_preview_lines
            ),
            theme=t,
        )
        lines = apply_render_shell(rendered, t)
        lines.extend(
            _render_extra_blocks(
                item.metadata,
                self._expanded,
                self._tool_result_preview_lines,
                self._theme,
            )
        )
        return lines


def _render_extra_blocks(
    metadata: dict,
    expanded: bool,
    preview_lines: int,
    theme: Any,
) -> list[str]:
    """Render generic extension blocks appended below any tool result."""
    blocks = (metadata or {}).get("_extra_blocks")
    if not blocks:
        return []
    lines: list[str] = []
    for block in blocks:
        block_lines: list[str] = block.get("lines") or []
        if not block_lines:
            continue
        rendered = _default_shell_preview(
            block_lines,
            expanded=expanded,
            expandable=True,
            preview_lines=block.get("preview_lines", preview_lines),
            theme=theme,
        )
        lines.append(f"{_RESULT_INDENT}{apply_style(theme.dim, '└')} {rendered[0]}")
        lines.extend(f"{_RESULT_INDENT}  {line}" for line in rendered[1:])
    return lines


# ── MessageList ───────────────────────────────────────────────────────────────


class MessageList(Component):
    """
    Scrollable list of MessageBlock objects rendered inside a fixed-height
    viewport.  Pass a MessageTheme to MessageList to apply it to all new blocks.
    """

    def __init__(
        self,
        height: int = 20,
        theme: MessageTheme | None = None,
        user_prefix: str = "❯ ",
        tool_result_preview_lines: int = _DEFAULT_DETAIL_PREVIEW_LINES,
    ) -> None:
        self._blocks: list[MessageBlock] = []
        self._height = height
        self._scroll = 0
        self._auto_scroll = True
        self._focused = False
        self._theme = theme or MessageTheme()
        self._user_prefix = user_prefix
        self._tool_lookup: Callable[[str], Tool | None] | None = None
        self._tool_result_preview_lines = max(1, tool_result_preview_lines)
        # Cell-level cache of render units old enough to be considered
        # finalized (see render_split_cells) — never rebuilt once written, so
        # a frame only pays for the still-changing tail, not the whole
        # transcript. Reset whenever something could have altered already-
        # frozen content (see _bump_invalidation) or the width changes.
        self._frozen_buf: Buffer | None = None
        self._frozen_block_count = 0
        self._frozen_width = -1
        self._invalidation_seq = 0
        self._frozen_seq = -1

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def theme(self) -> MessageTheme:
        """Return the active message-list theme."""
        return self._theme

    def set_height(self, height: int) -> None:
        self._height = max(1, height)

    def _bump_invalidation(self) -> None:
        """Force the frozen-cell cache to rebuild from scratch on the next render.

        Called by anything that can retroactively change already-frozen
        content (theme/prefix/tool-lookup swaps, expand/collapse-all) so a
        stale cache is never handed to the renderer.
        """
        self._invalidation_seq += 1

    def set_theme(self, theme: MessageTheme) -> None:
        self._theme = theme
        for block in self._blocks:
            block.set_theme(theme)
        self._bump_invalidation()

    def set_show_images(self, enabled: bool) -> None:
        """Update image visibility across existing and future message blocks."""
        self._theme.show_images = enabled
        self.set_theme(self._theme)

    def set_user_prefix(self, prefix: str) -> None:
        self._user_prefix = prefix
        for block in self._blocks:
            block.set_user_prefix(prefix)
        self._bump_invalidation()

    def set_tool_lookup(self, fn: Callable[[str], Tool | None] | None) -> None:
        self._tool_lookup = fn
        for block in self._blocks:
            block.set_tool_lookup(fn)
        self._bump_invalidation()

    def toggle_details_expanded(self) -> None:
        """Toggle thinking and tool-result details for assistant/tool blocks.

        Touches every matching block regardless of frozen state. "Frozen"
        only means "something else was added after it in self._blocks" — it
        is not a reliable proxy for "scrolled off-screen" (a message can be
        frozen the instant a short follow-up reply is appended, while still
        fully visible), so restricting this to the live tail broke the
        feature for the common case. Correctness comes first here: bump the
        frozen-cache generation so render_split_cells does a full rebuild on
        the next call — a real cost for a very long session, but this is a
        deliberate, user-triggered action, not a per-frame one.
        """
        from tau.message.types import AssistantMessage, ToolMessage

        targets = [
            b for b in self._blocks if isinstance(b.message, (AssistantMessage, ToolMessage))
        ]
        if not targets:
            return
        new_state = not targets[-1].is_expanded()
        for b in targets:
            b._expanded = new_state
            b.invalidate()
        self._bump_invalidation()

    def toggle_invocations_expanded(self) -> None:
        """Ctrl+O — toggle expand/collapse for all template and skill invocation blocks.

        See toggle_details_expanded: touches every matching block regardless
        of frozen state, for the same reason.
        """
        from tau.message.types import SkillInvocationMessage, TemplateInvocationMessage

        targets = [
            b
            for b in self._blocks
            if isinstance(b.message, (TemplateInvocationMessage, SkillInvocationMessage))
        ]
        if not targets:
            return
        last_msg = targets[-1].message
        if isinstance(last_msg, (TemplateInvocationMessage, SkillInvocationMessage)):
            new_state = not last_msg.expanded
            for b in targets:
                if isinstance(b.message, (TemplateInvocationMessage, SkillInvocationMessage)):
                    b.message.expanded = new_state
                    b.invalidate()
            self._bump_invalidation()

    def add_block(self, block: MessageBlock) -> None:
        self._blocks.append(block)
        if self._auto_scroll:
            self._scroll = 0

    def _guard_frozen_bounds(self) -> None:
        """Defensively drop the frozen cache if a pop ever reached into it.

        A unit only freezes once none of its blocks are streaming, and
        remove_last()/remove_pending_user_turn() only ever pop the most
        recent one or two blocks — normally still streaming or just added, so
        this should rarely trigger. If it ever does (e.g. a render happened
        to land between adding a block and an immediate undo), resetting here
        forces one full rebuild rather than leaving the cache pointing past
        the end of self._blocks.
        """
        if self._frozen_block_count > len(self._blocks):
            self._frozen_buf = None
            self._frozen_block_count = 0

    def remove_last(self) -> bool:
        """Remove the last block (used to undo a user message on pre-stream abort)."""
        if self._blocks:
            self._blocks.pop()
            self._guard_frozen_bounds()
            return True
        return False

    def remove_pending_user_turn(self) -> bool:
        """Pop trailing blocks up to and including the most recent user message.

        Used to undo a pre-stream abort. ``message_start`` may have already added
        an empty assistant placeholder block (the model began a message but no
        token arrived yet), so removing only the last block would drop that
        placeholder and leave the user message visible. This removes both.
        Returns True if a user message was removed.
        """
        from tau.message.types import UserMessage

        while self._blocks:
            block = self._blocks.pop()
            if isinstance(block.message, UserMessage):
                self._guard_frozen_bounds()
                return True
        self._guard_frozen_bounds()
        return False

    def clear(self) -> None:
        self._blocks.clear()
        self._scroll = 0
        self._auto_scroll = True
        self._frozen_buf = None
        self._frozen_block_count = 0

    def add_message(self, message: object, streaming: bool = False) -> MessageBlock:
        block = MessageBlock(
            message,
            streaming=streaming,
            theme=self._theme,
            user_prefix=self._user_prefix,
            tool_lookup=self._tool_lookup,
            tool_result_preview_lines=self._tool_result_preview_lines,
        )
        self.add_block(block)
        return block

    def set_focused(self, focused: bool) -> None:
        self._focused = focused

    def scroll_up(self, n: int = 1) -> None:
        self._scroll += n
        self._auto_scroll = False

    def scroll_down(self, n: int = 1) -> None:
        self._scroll = max(0, self._scroll - n)
        if self._scroll == 0:
            self._auto_scroll = True

    def scroll_to_bottom(self) -> None:
        self._scroll = 0
        self._auto_scroll = True

    def scroll_to_top(self) -> None:
        self._auto_scroll = False
        self._scroll = 999_999

    @property
    def at_bottom(self) -> bool:
        return self._scroll == 0

    # -------------------------------------------------------------------------
    # Component
    # -------------------------------------------------------------------------

    def all_lines(self, width: int) -> list[str]:
        return self._render_blocks(width)

    def render(self, width: int) -> list[str]:
        # In scrollback mode the terminal's own buffer handles scrolling.
        # Return all rendered lines without any clipping or top-padding so that
        # (a) no blank lines appear above content when the list is short, and
        # (b) old messages naturally flow into the terminal's scrollback buffer
        #     as new content pushes them off the visible viewport.
        return self._render_blocks(width)

    def _iter_units(self, width: int):
        """Yield (start_block_index, end_block_index, lines) for each renderable unit.

        A unit is either one block, or an assistant+tool pair merged for a
        joint tool-result render — shared by ``_render_blocks`` and
        ``render_split_cells`` so both agree on exactly the same grouping.
        """
        from tau.message.types import AssistantMessage, ToolCallContent, ToolMessage

        index = 0
        while index < len(self._blocks):
            block = self._blocks[index]
            next_message = (
                self._blocks[index + 1].message if index + 1 < len(self._blocks) else None
            )
            message = block.message
            followed_by_tool_result = (
                isinstance(message, AssistantMessage)
                and any(isinstance(item, ToolCallContent) for item in message.contents)
                and isinstance(next_message, ToolMessage)
            )
            if followed_by_tool_result:
                yield index, index + 2, block.render_with_tool_results(next_message, width)
                index += 2
                continue

            yield index, index + 1, block.render(width)
            index += 1

    def _render_blocks(self, width: int) -> list[str]:
        lines: list[str] = []
        for _start_idx, _end_idx, unit_lines in self._iter_units(width):
            lines.extend(unit_lines)
        return lines

    def render_split_cells(self, width: int) -> tuple[Buffer | None, list[str]]:
        """Return (frozen_buf, live_lines) for the incremental render fast path.

        ``frozen_buf`` holds Cell rows for render units old enough to be
        considered finalized — cached across calls and only ever grown, never
        rebuilt, so a caller can splice its rows straight into the frame
        buffer instead of re-parsing ANSI text that hasn't changed.

        A unit freezes once none of its blocks are streaming AND either (a) it
        is explicitly marked settled (MessageBlock.finalize(), called by the
        driver the instant it drops its own reference to a block for good —
        e.g. a !shell command's output completing, or a plain tool result
        with no further tracking) or (b) it is no longer the last unit.

        Neither ``streaming`` alone nor plain position is sufficient proof of
        finality on its own. The interactive app creates an assistant's
        placeholder block at message_start with streaming=False (real
        content, and streaming=True, only arrive once the first token
        lands), and can momentarily report streaming=False between
        token-batch flushes before the message is actually done — freezing
        is one-way with no re-check, so freezing a unit that looks "done" for
        a moment but isn't yet permanently hides every update that arrives
        afterward. For a block never explicitly finalized, "not last" is the
        fallback proof of finality (once something else has been added
        after it, the app has moved on and will never mutate it in place
        again). A unit frozen here that later turns out to still need
        changing (e.g. undo pops it, or a toggle mutates it) is still safe:
        popping past the frozen boundary is caught by _guard_frozen_bounds,
        and any mutation bumps _invalidation_seq — both force a one-time full
        rebuild rather than corrupting state.
        """
        from tau.tui.ansi_bridge import parse_ansi_into
        from tau.tui.geometry import Rect

        if width != self._frozen_width or self._frozen_seq != self._invalidation_seq:
            self._frozen_buf = None
            self._frozen_block_count = 0
            self._frozen_width = width
            self._frozen_seq = self._invalidation_seq

        units = list(self._iter_units(width))
        live_lines: list[str] = []
        for i, (start_idx, end_idx, unit_lines) in enumerate(units):
            if end_idx <= self._frozen_block_count:
                continue
            blocks = self._blocks[start_idx:end_idx]
            streaming = any(b.is_streaming for b in blocks)
            is_last_unit = i == len(units) - 1
            settled = all(b.is_settled for b in blocks)
            if streaming or (is_last_unit and not settled):
                live_lines.extend(unit_lines)
                continue
            if self._frozen_buf is None:
                self._frozen_buf = Buffer.empty(Rect(0, 0, max(1, width), 0))
            base = self._frozen_buf.area.height
            self._frozen_buf.grow_to(base + len(unit_lines))
            for j, line in enumerate(unit_lines):
                parse_ansi_into(self._frozen_buf, 0, base + j, line, width)
            self._frozen_block_count = end_idx
        return self._frozen_buf, live_lines

    def handle_input(self, event: InputEvent) -> bool:
        if not self._focused or not isinstance(event, KeyEvent):
            return False
        keybindings = get_keybindings()
        if keybindings.matches(event, "tui.scroll.up") or event.matches("b"):
            self.scroll_up(self._height)
        elif keybindings.matches(event, "tui.scroll.down") or event.matches(Key.SPACE):
            self.scroll_down(self._height)
        elif event.matches(Key.UP, "k"):
            self.scroll_up(1)
        elif event.matches(Key.DOWN, "j"):
            self.scroll_down(1)
        elif keybindings.matches(event, "tui.scroll.bottom") or event.matches(Key.shift("g")):
            self.scroll_to_bottom()
        elif keybindings.matches(event, "tui.scroll.top") or event.matches("g"):
            self.scroll_to_top()
        else:
            return False
        return True

    def invalidate(self) -> None:
        for block in self._blocks:
            block.invalidate()


# ── Arg formatter ─────────────────────────────────────────────────────────────


def _format_args(args: dict, max_width: int) -> str:
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 40:
            v_str = v_str[:37] + "…"
        parts.append(f"{k}={v_str}")
    result = "  ".join(parts)
    if len(result) > max_width:
        result = result[: max_width - 1] + "…"
    return result

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tau.tui.component import Component
from tau.tui.input import InputEvent, Key, KeyEvent
from tau.tui.markdown import render_markdown
from tau.tui.theme import MessageTheme
from tau.tui.utils import BOLD, RESET, _is_diff, cursor_block, visible_width, wrap

if TYPE_CHECKING:
    from tau.tool.types import Tool

_TOOL_INDENT = "  "
_RESULT_INDENT = "    "
_DEFAULT_TOOL_RESULT_PREVIEW_LINES = 5


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
        return [*lines, theme.dim("(ctrl+o to collapse)")]
    return [*lines[:threshold], theme.dim("···  (ctrl+o to expand)")]


def apply_render_shell(lines: list[str], theme: Any, color_fn: Any = None) -> list[str]:
    """Apply the standard └ framing to a pre-rendered list of lines.

    First line gets '    └ <line>', subsequent lines get '      <line>'.
    Optional color_fn is applied to the first line (e.g. theme.error for red).
    Shared by tool results and notify so any style change propagates everywhere.
    """
    if not lines:
        return []
    first = color_fn(lines[0]) if color_fn else lines[0]
    out = [f"{_RESULT_INDENT}{theme.dim('└')} {first}"]
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
        tool_result_preview_lines: int = _DEFAULT_TOOL_RESULT_PREVIEW_LINES,
    ) -> None:
        self._message = message
        self._streaming = streaming
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

    def set_streaming(self, value: bool) -> None:
        if self._streaming != value:
            self._streaming = value
            self.invalidate()

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
                lines.append(self._theme.dim(str(msg)))

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
                    lead = t.you_label(prefix) if not lines else "  "
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
            lines.append(t.error_label("error"))
            for line in wrap(msg.error, inner_width):
                lines.append("  " + line)
            return lines

        # No "assistant" label — the content speaks for itself.
        from tau.message.types import ImageContent as _ImageContent

        for idx, item in enumerate(msg.contents):
            if isinstance(item, ThinkingContent):
                if t.show_thinking:
                    if item.content:
                        for line in wrap(item.content.rstrip(), inner_width):
                            lines.append("  " + t.thinking(line))
                    else:
                        lines.append("  " + t.thinking(t.thinking_label))
                    next_item = msg.contents[idx + 1] if idx + 1 < len(msg.contents) else None
                    if not isinstance(next_item, ThinkingContent):
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
            lines.append("  " + t.dim("┌ User Interrupted"))

        return lines

    def _render_terminal(self, msg: Any, width: int) -> list[str]:
        from tau.message.types import TerminalExecutionMessage
        from tau.tui.utils import BRIGHT_RED

        if not isinstance(msg, TerminalExecutionMessage):
            return []
        t = self._theme
        label = t.dim("$ " + msg.command)
        if msg.cancelled:
            label += "  " + BRIGHT_RED + "(cancelled)" + RESET
        elif msg.exit_code is not None and msg.exit_code != 0:
            label += "  " + BRIGHT_RED + f"(exit {msg.exit_code})" + RESET
        lines = [label]
        if msg.output:
            for line in msg.output.rstrip().split("\n"):
                lines.append("  " + t.dim(line))
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
                color_fn = t.tool_result_err if item.notify_type == "error" else None
                return apply_render_shell(item.lines, t, color_fn)
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
                header += t.dim(f"  {msg.args}")
            lines.append(header)
            lines.append("")
            for line in msg.expanded_content.splitlines():
                lines.append(f"  {t.dim(line) if line.strip() == '' else line}")
            lines.append("")
        else:
            name_args = f"/{msg.name}" + (f"  {msg.args}" if msg.args else "")
            hint = t.dim("  (ctrl+o to expand)")
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
                header += t.dim(f"  {msg.args}")
            lines.append(header)
            lines.append("")
            for line in msg.content.splitlines():
                lines.append(f"  {line}")
            lines.append("")
        else:
            name_args = f"/{msg.name}" + (f"  {msg.args}" if msg.args else "")
            hint = t.dim("  (ctrl+o to expand)")
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

        tool = self._tool_lookup(tool_name) if (self._tool_lookup and tool_name) else None
        if tool is not None and tool.render_result is not None:
            from tau.tool.types import ToolRenderOptions

            opts = ToolRenderOptions(
                is_error=item.is_error,
                expanded=self._expanded if tool.render_shell != "default" else False,
                is_partial=self._streaming,
                metadata=item.metadata,
                theme=self._theme,
            )
            custom = tool.render_result(item.content, opts)
            if custom:
                # A custom renderer must return one terminal line per element.
                # Defensively flatten any embedded newlines so the differential
                # renderer's per-line height accounting stays correct — a single
                # element spanning multiple rows otherwise corrupts the diff.
                if any("\n" in str(c) for c in custom):
                    custom = [seg for c in custom for seg in str(c).split("\n")]
                if tool.render_shell == "default":
                    t = self._theme
                    color_fn = t.tool_result_err if item.is_error else t.tool_result_ok
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
                    framed[0] = color_fn(framed[0])
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
        color_fn = t.tool_result_err if item.is_error else t.tool_result_ok
        content = str(item.content).strip() if item.content else ""
        all_lines = content.split("\n") if content else []
        if not all_lines:
            rendered = [color_fn("(no output)")]
        elif not item.is_error and _is_diff(content):
            from tau.tui.utils import render_diff

            diff_lines = render_diff(
                content,
                added=t.diff_added,
                removed=t.diff_removed,
                context=t.diff_context,
                hunk=t.diff_hunk,
                inverse=t.diff_inverse,
            )
            rendered = diff_lines or [color_fn("(empty diff)")]
        else:
            rendered = [color_fn(all_lines[0])] + [t.dim(line) for line in all_lines[1:]]
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
            preview_lines=preview_lines,
            theme=theme,
        )
        lines.append(f"{_RESULT_INDENT}{theme.dim('└')} {rendered[0]}")
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
        tool_result_preview_lines: int = _DEFAULT_TOOL_RESULT_PREVIEW_LINES,
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

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def set_height(self, height: int) -> None:
        self._height = max(1, height)

    def set_theme(self, theme: MessageTheme) -> None:
        self._theme = theme
        for block in self._blocks:
            block.set_theme(theme)

    def set_user_prefix(self, prefix: str) -> None:
        self._user_prefix = prefix
        for block in self._blocks:
            block.set_user_prefix(prefix)

    def set_tool_lookup(self, fn: Callable[[str], Tool | None] | None) -> None:
        self._tool_lookup = fn
        for block in self._blocks:
            block.set_tool_lookup(fn)

    def toggle_tool_results_expanded(self) -> None:
        """Ctrl+E — toggle expanded/collapsed view for all tool result blocks."""
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

    def toggle_invocations_expanded(self) -> None:
        """Ctrl+O — toggle expand/collapse for all template and skill invocation blocks."""
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

    def add_block(self, block: MessageBlock) -> None:
        self._blocks.append(block)
        if self._auto_scroll:
            self._scroll = 0

    def remove_last(self) -> bool:
        """Remove the last block (used to undo a user message on pre-stream abort)."""
        if self._blocks:
            self._blocks.pop()
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
                return True
        return False

    def clear(self) -> None:
        self._blocks.clear()
        self._scroll = 0
        self._auto_scroll = True

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

    def _render_blocks(self, width: int) -> list[str]:
        from tau.message.types import AssistantMessage, ToolCallContent, ToolMessage

        lines: list[str] = []
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
                lines.extend(block.render_with_tool_results(next_message, width))
                index += 2
                continue

            lines.extend(block.render(width))
            index += 1
        return lines

    def handle_input(self, event: InputEvent) -> bool:
        if not self._focused or not isinstance(event, KeyEvent):
            return False
        if event.matches(Key.PAGE_UP, "b"):
            self.scroll_up(self._height)
        elif event.matches(Key.PAGE_DOWN, Key.SPACE):
            self.scroll_down(self._height)
        elif event.matches(Key.UP, "k"):
            self.scroll_up(1)
        elif event.matches(Key.DOWN, "j"):
            self.scroll_down(1)
        elif event.matches(Key.END, Key.shift("g")):
            self.scroll_to_bottom()
        elif event.matches(Key.HOME, "g"):
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

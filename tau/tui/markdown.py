from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from mistletoe.base_renderer import BaseRenderer
from mistletoe.block_token import Document

from tau.tui.utils import RESET, visible_width, wrap

if TYPE_CHECKING:
    from tau.tui.theme import MarkdownTheme


# ── Syntax highlighting (pygments) ──────────────────────────────────────────────

from pygments import highlight as _pyg_highlight
from pygments.formatters import Terminal256Formatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound


@lru_cache(maxsize=8)
def _formatter(style: str) -> Terminal256Formatter:
    try:
        return Terminal256Formatter(style=style)
    except Exception:
        return Terminal256Formatter(style="default")


@lru_cache(maxsize=128)
def _lexer(lang: str):
    try:
        return get_lexer_by_name(lang, stripnl=False)
    except ClassNotFound:
        return None


def _highlight_code(code: str, lang: str, style: str) -> list[str] | None:
    """Return syntax-highlighted ANSI lines for a code block, or None to fall back.

    Falls back (returns None) when the fence has no language, the language is
    unknown, or highlighting raises for any reason — so plain rendering is
    always a safe default.
    """
    if not lang or not style:
        return None
    lexer = _lexer(lang.lower())
    if lexer is None:
        return None
    try:
        out = _pyg_highlight(code, lexer, _formatter(style))
    except Exception:
        return None
    return out.rstrip("\n").split("\n")


class _MdContext(BaseRenderer):
    """
    A no-op renderer subclass.

    mistletoe only tokenizes inline (span) content while a renderer is active,
    so we instantiate one purely to establish that context, then walk the AST
    ourselves to produce width-aware ANSI lines.  CommonMark + strikethrough are
    enabled by mistletoe's default token set.
    """

    def render_inner(self, token: Any) -> str:  # pragma: no cover - unused
        return ""


# ── Public API ────────────────────────────────────────────────────────────────


def render_markdown(
    text: str,
    width: int,
    theme: MarkdownTheme,
    *,
    preserve_soft_breaks: bool = False,
) -> list[str]:
    """Render a markdown string to a list of ANSI-coloured terminal lines."""
    with _MdContext():
        doc = Document(text.splitlines(keepends=True))
        lines = _Renderer(width, theme, preserve_soft_breaks).render_blocks(doc.children or [])
    while lines and lines[-1] == "":
        lines.pop()
    return lines


# ── Renderer ──────────────────────────────────────────────────────────────────


class _Renderer:
    def __init__(
        self,
        width: int,
        theme: MarkdownTheme,
        preserve_soft_breaks: bool = False,
    ) -> None:
        self.width = width
        self.theme = theme
        self.preserve_soft_breaks = preserve_soft_breaks

    # ── Block rendering ───────────────────────────────────────────────────────

    def _render_blocks_at(self, nodes: Iterable[Any], width: int) -> list[str]:
        """Render nested blocks at a reduced width (for indented/prefixed content).

        Inner content that will be prefixed (a quote's ``▎ `` border, a list
        item's bullet/indent) must wrap to the *remaining* width, not the full
        width — otherwise each full-width inner line spills its last few columns
        onto a tiny extra line once the prefix is added.
        """
        saved = self.width
        self.width = max(1, width)
        try:
            return self.render_blocks(nodes)
        finally:
            self.width = saved

    def render_blocks(self, nodes: Iterable[Any]) -> list[str]:
        lines: list[str] = []
        for node in nodes:
            name = type(node).__name__

            if name in ("Heading", "SetextHeading"):
                text = self._render_inline(node.children or [])
                for wl in wrap(text, self.width) or [text]:
                    lines.append(self.theme.heading(wl))
                lines.append("")

            elif name == "Paragraph":
                text = self._render_inline(node.children or [])
                for wl in wrap(text, self.width) or [text]:
                    lines.append(wl)
                lines.append("")

            elif name in ("CodeFence", "BlockCode"):
                lang = (getattr(node, "language", "") or "").strip()
                if lang:
                    lines.append(self.theme.code_block_border(lang))
                code = self._code_content(node).rstrip("\n")
                style = getattr(self.theme, "code_syntax_style", "")
                highlighted = _highlight_code(code, lang, style)
                if highlighted is not None:
                    # Already coloured by pygments; reset each wrapped segment so a
                    # trailing colour can't bleed onto the next line (SGR persists
                    # across newlines in terminals).
                    for cl in highlighted:
                        for wl in wrap(cl, self.width - 2) or [""]:
                            lines.append("  " + wl + RESET)
                else:
                    for cl in code.split("\n"):
                        for wl in wrap(cl, self.width - 2) or [""]:
                            lines.append("  " + self.theme.code_block(wl))
                lines.append("")

            elif name == "ThematicBreak":
                lines.append(self.theme.hr("─" * self.width))
                lines.append("")

            elif name == "List":
                lines.extend(self._render_list(node, depth=0))
                lines.append("")

            elif name == "Quote":
                border = self.theme.quote_border("▎ ")
                inner_w = max(1, self.width - visible_width(border))
                # Render inner content at the reduced width so it wraps to fit
                # beside the border instead of spilling a 2-char remainder.
                inner = self._render_blocks_at(node.children or [], inner_w)
                while inner and inner[-1] == "":
                    inner.pop()
                for il in inner:
                    for wl in wrap(il, inner_w) or [il]:
                        lines.append(border + self.theme.quote(wl))
                lines.append("")

            elif name == "Table":
                lines.extend(self._render_table(node))
                lines.append("")

            elif name in ("HTMLBlock", "HtmlBlock"):
                lines.append(getattr(node, "content", "").rstrip())

        return lines

    @staticmethod
    def _code_content(node: Any) -> str:
        content = getattr(node, "content", None)
        if content is not None:
            return content
        children = getattr(node, "children", None) or []
        return "".join(getattr(c, "content", "") for c in children)

    # ── List rendering ────────────────────────────────────────────────────────

    def _render_list(self, node: Any, depth: int) -> list[str]:
        lines: list[str] = []
        indent = "  " * depth
        ordered = getattr(node, "start", None) is not None
        num = node.start if ordered else 1

        for item in node.children or []:
            bullet = f"{num}." if ordered else "•"
            marker = self.theme.list_bullet(bullet)
            prefix = indent + marker + " "
            cont_pref = indent + " " * (len(bullet) + 1)
            inner_w = max(1, self.width - visible_width(prefix))

            item_lines = self._render_list_item(item, depth, inner_w)
            for j, il in enumerate(item_lines):
                lines.append((prefix if j == 0 else cont_pref) + il)
            if ordered:
                num += 1

        return lines

    def _render_list_item(self, item: Any, depth: int, inner_w: int) -> list[str]:
        lines: list[str] = []
        for child in item.children or []:
            name = type(child).__name__
            if name == "Paragraph":
                text = self._render_inline(child.children or [])
                for wl in wrap(text, inner_w) or [text]:
                    lines.append(wl)
            elif name == "List":
                lines.extend(self._render_list(child, depth + 1))
            else:
                # Code blocks, quotes, etc. nested inside a list item — render at
                # the item's inner width so they wrap to fit beside the bullet
                # indent instead of spilling a few columns onto extra lines.
                sub = self._render_blocks_at([child], inner_w)
                while sub and sub[-1] == "":
                    sub.pop()
                lines.extend(sub)
        return lines

    # ── Table rendering ───────────────────────────────────────────────────────

    def _render_table(self, node: Any) -> list[str]:
        header = getattr(node, "header", None)
        raw_rows: list[Any] = []  # mistletoe TableRow nodes (each has .children)
        if header is not None:
            raw_rows.append(header)
        raw_rows.extend(node.children or [])

        # Render all cell text up-front so we can measure column widths.
        rendered: list[list[str]] = [
            [self._render_inline(c.children or []) for c in (row.children or [])]
            for row in raw_rows
        ]
        if not rendered:
            return []

        # Detect and drop an empty header row (no cell has visible text).
        has_header = header is not None
        if has_header and not any(c.strip() for c in rendered[0]):
            rendered = rendered[1:]
            has_header = False
        if not rendered:
            return []

        # Canonical column count comes from the header row when present.
        # Using max() would inflate ncols when a data cell contains a literal
        # "|" that the parser split into an extra column.
        ncols = len(rendered[0]) if has_header and rendered else max(len(r) for r in rendered)

        # Normalise every row to exactly ncols cells.
        for r in rendered:
            if len(r) > ncols:
                # Extra cells came from a literal "|" inside cell content.
                # Re-join them back into the last expected cell.
                r[ncols - 1 :] = ["|".join(r[ncols - 1 :])]
            while len(r) < ncols:
                r.append("")

        # Max visible width per column; leave room for outer borders + inner gaps:
        # "│  " + cells joined by "  │  " + "  │" → ncols*5+1 overhead
        col_widths = [max(visible_width(r[c]) for r in rendered) for c in range(ncols)]
        overhead = ncols * 5 + 1
        available = max(ncols, self.width - overhead)
        total = sum(col_widths)
        if total > available:
            # Level-down algorithm (inspired by Textualize/rich _collapse_widths):
            # Repeatedly reduce the widest column(s) toward the next-widest level
            # until the total fits.  Narrow columns are never touched because they
            # never reach max_w, so they keep their full natural width for free.
            widths = list(col_widths)
            excess = total - available
            while excess > 0:
                max_w = max(widths)
                second_w = max((w for w in widths if w < max_w), default=0)
                at_max = [i for i, w in enumerate(widths) if w == max_w]
                n = len(at_max)
                headroom = max_w - second_w  # reduction before hitting next level
                total_reduce = min(excess, n * headroom)
                per = total_reduce // n
                extra = total_reduce - per * n
                for rank, i in enumerate(at_max):
                    widths[i] -= per + (1 if rank < extra else 0)
                excess -= total_reduce
            col_widths = [max(1, w) for w in widths]

        def _border(left: str, mid: str, right: str, fill: str = "─") -> str:
            segs = (fill * (w + 4) for w in col_widths)
            return self.theme.hr(left + mid.join(segs) + right)

        top = _border("┌", "┬", "┐")
        mid = _border("├", "┼", "┤")
        bottom = _border("└", "┴", "┘")

        def _row(cells: list[str]) -> list[str]:
            wrapped = [wrap(cell, col_widths[ci]) or [cell] for ci, cell in enumerate(cells)]
            height = max(len(w) for w in wrapped)
            blank = (
                self.theme.hr("│")
                + self.theme.hr("│").join(" " * (col_widths[ci] + 4) for ci in range(ncols))
                + self.theme.hr("│")
            )
            out = [blank]
            for li in range(height):
                padded = []
                for ci, lines in enumerate(wrapped):
                    cw = col_widths[ci]
                    cell = lines[li] if li < len(lines) else ""
                    padded.append("  " + cell + " " * max(2, cw - visible_width(cell) + 2))
                sep = self.theme.hr("│")
                out.append(sep + sep.join(padded) + sep)
            out.append(blank)
            return out

        lines: list[str] = [top]
        for ri, cells in enumerate(rendered):
            lines.extend(_row(cells))
            if ri == 0 and has_header:
                lines.append(mid)
        lines.append(bottom)
        return lines

    # ── Inline rendering ──────────────────────────────────────────────────────

    def _render_inline(self, nodes: Iterable[Any]) -> str:
        parts: list[str] = []
        for node in nodes:
            name = type(node).__name__

            if name == "RawText":
                parts.append(node.content)
            elif name == "LineBreak":
                soft = getattr(node, "soft", True)
                parts.append("\n" if not soft or self.preserve_soft_breaks else " ")
            elif name == "InlineCode":
                parts.append(self.theme.code_inline(self._raw(node)))
            elif name == "Strong":
                parts.append(self.theme.bold(self._render_inline(node.children or [])))
            elif name == "Emphasis":
                parts.append(self.theme.italic(self._render_inline(node.children or [])))
            elif name == "Strikethrough":
                parts.append(self.theme.strikethrough(self._render_inline(node.children or [])))
            elif name == "Link":
                inner = self._render_inline(node.children or []) or getattr(node, "target", "")
                target = self._safe_link_target(getattr(node, "target", ""))
                label = self.theme.link_text(inner)
                parts.append(f"\x1b]8;;{target}\x1b\\{label}\x1b]8;;\x1b\\" if target else label)
            elif name == "AutoLink":
                target = self._safe_link_target(getattr(node, "target", ""))
                inner = self._render_inline(node.children or []) or target
                label = self.theme.link_url(inner)
                parts.append(f"\x1b]8;;{target}\x1b\\{label}\x1b]8;;\x1b\\" if target else label)
            elif name == "Image":
                alt = self._render_inline(node.children or [])
                url = getattr(node, "src", "") or getattr(node, "target", "")
                label = f"[image: {alt}]" if alt else "[image]"
                styled_label = self.theme.italic(label)
                target = self._image_link_target(url)
                parts.append(
                    f"\x1b]8;;{target}\x1b\\{styled_label}\x1b]8;;\x1b\\"
                    if target
                    else styled_label
                )
            elif name in ("HTMLSpan", "HtmlSpan"):
                parts.append(getattr(node, "content", ""))
            elif name == "EscapeSequence":
                parts.append(self._raw(node))
            else:
                children = getattr(node, "children", None)
                if children:
                    parts.append(self._render_inline(children))
                else:
                    parts.append(getattr(node, "content", ""))
        return "".join(parts)

    @staticmethod
    def _raw(node: Any) -> str:
        """Concatenate the raw text of a token's children (or its own content)."""
        children = getattr(node, "children", None)
        if children:
            return "".join(getattr(c, "content", "") for c in children)
        return getattr(node, "content", "")

    @staticmethod
    def _safe_link_target(target: str) -> str:
        """Remove control characters that could terminate an OSC 8 hyperlink."""
        return "".join(
            char for char in target if ord(char) >= 0x20 and not 0x7F <= ord(char) <= 0x9F
        )

    @classmethod
    def _image_link_target(cls, target: str) -> str:
        """Return a clickable URI for a remote URL or local image path."""
        safe_target = cls._safe_link_target(target)
        if not safe_target or urlsplit(safe_target).scheme:
            return safe_target
        return Path(safe_target).expanduser().resolve().as_uri()


# ---------------------------------------------------------------------------
# Message renderer registry
# ---------------------------------------------------------------------------

from collections.abc import Callable  # noqa: E402
from typing import TYPE_CHECKING as _TYPE_CHECKING  # noqa: E402

if _TYPE_CHECKING:
    from tau.message.types import CustomMessage
    from tau.tui.theme import MessageTheme

RendererFn = Callable[["CustomMessage", "MessageTheme", int], list[str]]


class MessageRendererRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, RendererFn] = {}

    def register(self, custom_type: str, fn: RendererFn) -> None:
        self._registry[custom_type] = fn

    def replace(self, renderers: dict[str, RendererFn]) -> None:
        """Replace extension-provided renderers atomically."""
        self._registry = dict(renderers)

    def render(
        self,
        message: CustomMessage,
        theme: MessageTheme,
        width: int,
    ) -> list[str] | None:
        fn = self._registry.get(message.custom_type)
        if fn is None:
            return None
        return fn(message, theme, width)


message_renderer_registry = MessageRendererRegistry()

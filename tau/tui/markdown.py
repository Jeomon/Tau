from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from tau.tui.style import apply_style
from tau.tui.utils import RESET, visible_width, wrap

if TYPE_CHECKING:
    from tau.tui.theme import MarkdownTheme


# ── LaTeX math ────────────────────────────────────────────────────────────────
#
# Math is extracted from the raw text *before* mistletoe tokenizes it, for two
# reasons that both trace back to mistletoe being a real CommonMark parser:
#
#  1. \(\)/\[\] delimiters use a literal backslash, which collides with
#     CommonMark's own backslash-escaping -- "(", ")", "[", "]" are escapable
#     punctuation, so by the time any post-tokenization code sees this text,
#     mistletoe has already silently stripped \( down to a bare "(" (letters
#     like \lambda survive, since they're not escapable). A regex applied
#     after tokenization can never see the delimiter.
#  2. A literal "|" inside math (e.g. absolute-value bars, $|\sin\theta|$)
#     is otherwise indistinguishable from a table-row column separator to
#     mistletoe's tokenizer, which has no notion of math syntax and will
#     shred the row into extra cells.
#
# Extracting everything up front sidesteps both: each matched span is
# converted immediately and swapped for an inert placeholder that mistletoe
# can only ever see as ordinary text, then spliced back in once rendering
# reaches that placeholder's RawText node (see _render_inline).
_DISPLAY_MATH_RE = re.compile(r"\$\$(?!\s)(.+?)(?<!\s)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<![\\$])\$(?![\s$])(.+?)(?<![\s\\])\$(?![\d$])", re.DOTALL)
_DISPLAY_MATH_BRACKET_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_INLINE_MATH_PAREN_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
# Ordered display-before-inline within each delimiter family, so the inline
# pattern can't partially match into a display block's own delimiters.
_MATH_RES = (
    (_DISPLAY_MATH_RE, True),
    (_DISPLAY_MATH_BRACKET_RE, True),
    (_INLINE_MATH_RE, False),
    (_INLINE_MATH_PAREN_RE, False),
)

_MATH_PLACEHOLDER = ""  # private-use codepoint, never appears in real text
_MATH_PLACEHOLDER_RE = re.compile(
    re.escape(_MATH_PLACEHOLDER) + r"(\d+)" + re.escape(_MATH_PLACEHOLDER)
)

# Fenced/inline code is left untouched by extraction below: a LaTeX example
# shown inside a ```tex block or `$...$` code span is text to display
# verbatim, not math to render, and mistletoe's own code-span/fence
# recognition isn't available yet at this pre-tokenization stage to lean on.
# Approximates CommonMark fence/span matching (same-length open/close
# backtick or tilde run) rather than implementing it in full.
_CODE_REGION_RE = re.compile(
    r"(?P<fence>`{3,}|~{3,})[^\n]*\n.*?\n?(?P=fence)|`+[^`\n]+?`+",
    re.DOTALL,
)

_SCRIPT_RE = re.compile(r"([_^])\{([^{}]+)\}|([_^])([A-Za-z0-9])")
_TASK_CHECKBOX_RE = re.compile(r"^\[([ xX])\]\s+")
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_BARE_URL_TRAILING_PUNCT = ".,;:!?'\")]}*_~"
_SUPERSCRIPTS = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
_SUBSCRIPTS = str.maketrans(
    "0123456789+-=()aehijklmnoprstuvx",
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ",
)


@lru_cache(maxsize=1)
def _latex_node_cls():
    # Deferred: pylatexenc is only needed once a message actually contains
    # LaTeX math, not at import time (which is on the app-startup path).
    from pylatexenc.latex2text import LatexNodes2Text  # type: ignore[import-untyped]

    return LatexNodes2Text


def _convert_script(marker: str, value: str) -> str:
    """Convert a LaTeX script body to Unicode where suitable glyphs exist."""
    plain = _latex_node_cls()(math_mode="text").latex_to_text(f"${value}$").strip()
    table = _SUPERSCRIPTS if marker == "^" else _SUBSCRIPTS
    converted = plain.translate(table)
    supported = all(ord(char) in table for char in plain)
    if supported:
        return converted
    # Unicode has no general superscript alphabet and no superscript infinity.
    # Keep explicit notation and separate it from a following expression.
    return f"{marker}{plain}{' ' if marker == '^' else ''}"


def _unicode_scripts(expression: str) -> str:
    """Replace braced and single-character LaTeX scripts with readable Unicode."""

    def replace(match: re.Match[str]) -> str:
        marker = match.group(1) or match.group(3)
        value = match.group(2) or match.group(4)
        return _convert_script(marker, value)

    # Keep a converted Unicode script from becoming part of the preceding
    # control-word name (for example ``\sumₙ``). An empty group terminates the
    # LaTeX macro without changing its rendered output.
    expression = re.sub(r"(\\[A-Za-z]+)(?=[_^])", r"\1{}", expression)
    return _SCRIPT_RE.sub(replace, expression)


def _normalize_math_spacing(text: str) -> str:
    """Add terminal-friendly spacing around binary relation operators."""
    text = re.sub(r"\s*(≤|≥|≈|≠|=)\s*", r" \1 ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=512)
def _latex_math_to_text(expression: str) -> str:
    """Convert one LaTeX math expression to terminal-readable Unicode text."""
    try:
        expression = _unicode_scripts(expression)
        converted = _latex_node_cls()(math_mode="text").latex_to_text(f"${expression}$")
    except Exception:
        return expression
    converted = " ".join(line.strip() for line in converted.splitlines() if line.strip())
    return _normalize_math_spacing(converted) or expression


def _extract_math(text: str) -> tuple[str, list[str]]:
    """Pull every recognised math span out of the raw text before mistletoe
    tokenizes it (see the module comment above for why). Returns the
    placeholder-substituted text plus the list of already-converted Unicode
    replacements, indexed by the placeholder. Fenced/inline code regions are
    passed through untouched (see _CODE_REGION_RE).
    """
    replacements: list[str] = []

    def _repl(is_display: bool):
        def repl(match: re.Match[str]) -> str:
            converted = _latex_math_to_text(match.group(1))
            replacements.append(f"\n{converted}\n" if is_display else converted)
            return f"{_MATH_PLACEHOLDER}{len(replacements) - 1}{_MATH_PLACEHOLDER}"

        return repl

    def _extract_in_segment(segment: str) -> str:
        for pattern, is_display in _MATH_RES:
            segment = pattern.sub(_repl(is_display), segment)
        return segment

    parts: list[str] = []
    pos = 0
    for m in _CODE_REGION_RE.finditer(text):
        parts.append(_extract_in_segment(text[pos : m.start()]))
        parts.append(m.group(0))
        pos = m.end()
    parts.append(_extract_in_segment(text[pos:]))
    text = "".join(parts)
    return text, replacements


# ── Syntax highlighting (pygments) ──────────────────────────────────────────────


@lru_cache(maxsize=1)
def _pygments():
    # Deferred: pygments is only needed once a code block is actually
    # highlighted, not at import time (which is on the app-startup path).
    from pygments import highlight as pyg_highlight
    from pygments.formatters import Terminal256Formatter
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound

    return pyg_highlight, Terminal256Formatter, get_lexer_by_name, ClassNotFound


@lru_cache(maxsize=8)
def _formatter(style: str):
    _, terminal256_formatter, _, _ = _pygments()
    try:
        return terminal256_formatter(style=style)
    except Exception:
        return terminal256_formatter(style="default")


@lru_cache(maxsize=128)
def _lexer(lang: str):
    _, _, get_lexer_by_name, class_not_found = _pygments()
    try:
        return get_lexer_by_name(lang, stripnl=False)
    except class_not_found:
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
    pyg_highlight, _, _, _ = _pygments()
    try:
        out = pyg_highlight(code, lexer, _formatter(style))
    except Exception:
        return None
    return out.rstrip("\n").split("\n")


@lru_cache(maxsize=1)
def _mistletoe():
    # Deferred: mistletoe is only needed once a message is actually rendered
    # as markdown, not at import time (which is on the app-startup path) —
    # mistletoe.core_tokens alone classifies every Unicode code point on import.
    from mistletoe.base_renderer import BaseRenderer
    from mistletoe.block_token import Document, HtmlBlock
    from mistletoe.span_token import HtmlSpan

    class _MdContext(BaseRenderer):
        """
        A no-op renderer subclass.

        mistletoe only tokenizes inline (span) content while a renderer is
        active, so we instantiate one purely to establish that context, then
        walk the AST ourselves to produce width-aware ANSI lines.  CommonMark +
        strikethrough are enabled by mistletoe's default token set; HtmlSpan
        and HtmlBlock are registered as extras so inline HTML tags like `<br>`
        and standalone HTML blocks are tokenized separately instead of being
        swallowed into surrounding RawText/Paragraph nodes.
        """

        def render_inner(self, token: Any) -> str:  # pragma: no cover - unused
            return ""

        def render_html_span(self, token: Any) -> str:  # pragma: no cover - unused
            return ""

        def render_html_block(self, token: Any) -> str:  # pragma: no cover - unused
            return ""

    return Document, HtmlBlock, HtmlSpan, _MdContext


# ── Public API ────────────────────────────────────────────────────────────────


def render_markdown(
    text: str,
    width: int,
    theme: MarkdownTheme,
    *,
    preserve_soft_breaks: bool = False,
) -> list[str]:
    """Render a markdown string to a list of ANSI-coloured terminal lines."""
    return _render_markdown(text, width, theme, preserve_soft_breaks=preserve_soft_breaks)


def _render_markdown(
    text: str,
    width: int,
    theme: MarkdownTheme,
    *,
    preserve_soft_breaks: bool = False,
    trim_trailing_blank_lines: bool = True,
) -> list[str]:
    document, html_block, html_span, md_context = _mistletoe()
    text, math_replacements = _extract_math(text)
    with md_context(html_span, html_block):
        doc = document(text.splitlines(keepends=True))
        renderer = _Renderer(width, theme, preserve_soft_breaks, math_replacements)
        lines = renderer.render_blocks(doc.children or [])
    if trim_trailing_blank_lines:
        while lines and lines[-1] == "":
            lines.pop()
    return lines


@dataclass(frozen=True)
class StreamingMarkdownRender:
    """Rendered split for append-only streamed markdown.

    ``frozen_lines`` are completed top-level markdown blocks cached across
    frames. ``live_lines`` are the current open block rendered for this frame.
    ``frozen_generation`` changes whenever the frozen prefix is rebuilt or
    extended, so callers can invalidate downstream cell caches precisely.
    """

    frozen_lines: list[str]
    live_lines: list[str]
    frozen_generation: int

    @property
    def lines(self) -> list[str]:
        return [*self.frozen_lines, *self.live_lines]


class StreamingMarkdownRenderer:
    """Incremental renderer for append-only streamed markdown.

    A normal ``render_markdown(growing_text, ...)`` call reparses the entire
    CommonMark document every frame.  For long streamed replies that becomes
    O(total_response_size) on the UI event loop and can delay keystroke echo.

    This cache freezes complete top-level block groups once a blank-line
    boundary has moved behind the live tail, then reparses only the still-open
    suffix on subsequent frames.  The final non-streaming render should still
    call ``render_markdown`` once for exact whole-document semantics.
    """

    def __init__(self) -> None:
        self._width = -1
        self._theme_id = 0
        self._preserve_soft_breaks = False
        self._text = ""
        self._frozen_until = 0
        self._frozen_lines: list[str] = []
        self._frozen_generation = 0
        # Incremental freeze-cutoff scan state (see _advance_freeze_scan).
        # ``_scan_pos`` only ever advances past *complete* lines (ones ending
        # in \n/\r), so an in-progress trailing line is safely re-scanned
        # next call instead of being counted as a boundary too early.
        self._scan_pos = 0
        self._scan_in_fence = False
        self._scan_fence_marker = ""
        self._scan_last_boundary = 0
        # Cache for render_prefixed(): the frozen half only needs re-prefixing
        # when frozen_generation actually moves (i.e. a new top-level block
        # just froze), not on every streamed token.
        self._prefixed_generation = -1
        self._prefixed_prefix = ""
        self._prefixed_frozen: list[str] = []

    def reset(self) -> None:
        self._text = ""
        self._frozen_until = 0
        self._frozen_lines = []
        self._frozen_generation += 1
        self._scan_pos = 0
        self._scan_in_fence = False
        self._scan_fence_marker = ""
        self._scan_last_boundary = 0

    def _advance_freeze_scan(self, text: str) -> int:
        """Return an append-only cutoff for completed top-level markdown blocks.

        During streaming, only the current open block needs to remain live.
        Once a blank-line boundary is seen outside a fenced code block, the
        block before it is structurally complete for the common
        assistant-output cases we render (paragraphs, headings, lists, tables,
        quotes, fenced code); freezing up to the latest such boundary keeps
        active work bounded to the current block rather than the whole reply.

        A naive version would re-scan the *entire* accumulated text on every
        call — O(total response length) per streamed token, thus O(n²) over one
        long reply. Since ``text`` only ever grows by appending (enforced by
        the caller's ``text.startswith(self._text)`` reset check), this instead
        resumes scanning from the last position a *complete* line ended,
        carrying the fenced-code-block state across calls, so each call costs
        only in the newly arrived text, not the whole reply.
        """
        pos = self._scan_pos
        in_fence = self._scan_in_fence
        fence_marker = self._scan_fence_marker
        last_boundary = self._scan_last_boundary

        chunk = text[pos:]
        lines = chunk.splitlines(keepends=True)
        # A trailing fragment with no line terminator is still being
        # written to (more characters may land right after it before the
        # next newline) — leave it unconsumed so it's re-scanned, cheaply,
        # next call instead of being treated as a finished line now.
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines.pop()

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
            pos += len(line)
            if not in_fence and stripped == "":
                last_boundary = pos

        self._scan_pos = pos
        self._scan_in_fence = in_fence
        self._scan_fence_marker = fence_marker
        self._scan_last_boundary = last_boundary
        return last_boundary

    def render_split(
        self,
        text: str,
        width: int,
        theme: MarkdownTheme,
        *,
        preserve_soft_breaks: bool = False,
    ) -> StreamingMarkdownRender:
        theme_id = id(theme)
        if (
            width != self._width
            or theme_id != self._theme_id
            or preserve_soft_breaks != self._preserve_soft_breaks
            or not text.startswith(self._text)
        ):
            self._width = width
            self._theme_id = theme_id
            self._preserve_soft_breaks = preserve_soft_breaks
            self.reset()

        self._text = text
        freeze_until = self._advance_freeze_scan(text)
        if freeze_until > self._frozen_until:
            newly_stable = text[self._frozen_until:freeze_until]
            rendered = _render_markdown(
                newly_stable,
                width,
                theme,
                preserve_soft_breaks=preserve_soft_breaks,
            )
            if rendered:
                if self._frozen_lines and self._frozen_lines[-1] != "":
                    self._frozen_lines.append("")
                self._frozen_lines.extend(rendered)
                # Preserve the block separator represented by the stable blank
                # boundary; render_markdown trims it for full-document display.
                if self._frozen_lines[-1] != "":
                    self._frozen_lines.append("")
                self._frozen_generation += 1
            self._frozen_until = freeze_until

        tail = text[self._frozen_until :]
        tail_lines = _render_markdown(
            tail,
            width,
            theme,
            preserve_soft_breaks=preserve_soft_breaks,
        )
        frozen = self._frozen_lines
        # ``frozen`` only ever grows by appending (see above) and callers only
        # ever iterate it, never mutate it — so it's safe (and much cheaper
        # for a long streamed reply, avoiding an O(frozen-so-far) copy on
        # every single token flush) to hand back the live list directly
        # instead of copying it every call. The only case that needs an
        # actual copy is trimming the trailing block-separator blank line
        # when there's no live tail to keep it meaningful for.
        frozen_lines = frozen[:-1] if not tail_lines and frozen[-1:] == [""] else frozen
        return StreamingMarkdownRender(frozen_lines, tail_lines, self._frozen_generation)

    def render(
        self,
        text: str,
        width: int,
        theme: MarkdownTheme,
        *,
        preserve_soft_breaks: bool = False,
    ) -> list[str]:
        return self.render_split(
            text,
            width,
            theme,
            preserve_soft_breaks=preserve_soft_breaks,
        ).lines

    def render_prefixed(
        self,
        text: str,
        width: int,
        theme: MarkdownTheme,
        *,
        preserve_soft_breaks: bool = False,
        prefix: str,
    ) -> list[str]:
        """Like ``render``, but with every line prefixed (e.g. an indent).

        Re-prefixing every already-frozen line on every single streamed
        token flush is O(response-size-so-far) per flush — this instead
        re-prefixes the frozen half only when ``frozen_generation`` actually
        moves (a new top-level block just froze, which happens once per
        paragraph/heading/etc., not once per token), and always re-prefixes
        just the small live tail.

        The cache is keyed on ``frozen_generation`` and prefixes the full,
        *untrimmed* ``self._frozen_lines`` — NOT ``split.frozen_lines``, which
        render_split trims the trailing block-separator blank line off of when
        there's no live tail *without bumping the generation*. Caching that
        trimmed form let a flush that happened to land exactly on a block
        boundary (empty tail) poison the cache, so the blank line stayed
        dropped and paragraphs visually collapsed together once the next token
        arrived. Re-deriving the trim per call from the untrimmed list here
        keeps it in lockstep with render_split.
        """
        split = self.render_split(text, width, theme, preserve_soft_breaks=preserve_soft_breaks)
        if (
            self._prefixed_generation != split.frozen_generation
            or self._prefixed_prefix != prefix
        ):
            self._prefixed_frozen = [prefix + line for line in self._frozen_lines]
            self._prefixed_generation = split.frozen_generation
            self._prefixed_prefix = prefix
        frozen = self._prefixed_frozen
        if not split.live_lines:
            # Mirror render_split's trailing block-separator trim, driven by the
            # untrimmed source list so the cached prefixed copy stays valid.
            if self._frozen_lines[-1:] == [""]:
                return frozen[:-1]
            return frozen
        return [*frozen, *(prefix + line for line in split.live_lines)]


# ── Renderer ──────────────────────────────────────────────────────────────────


class _Renderer:
    def __init__(
        self,
        width: int,
        theme: MarkdownTheme,
        preserve_soft_breaks: bool = False,
        math_replacements: list[str] | None = None,
    ) -> None:
        self.width = width
        self.theme = theme
        self.preserve_soft_breaks = preserve_soft_breaks
        # Already-converted math spans, indexed by the placeholder tokens
        # _extract_math() left in RawText nodes' content — see the module
        # comment near the top of this file for why.
        self.math_replacements = math_replacements or []

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
                    lines.append(apply_style(self.theme.heading, wl))
                lines.append("")

            elif name == "Paragraph":
                text = self._render_inline(node.children or [])
                for wl in wrap(text, self.width) or [text]:
                    lines.append(wl)
                lines.append("")

            elif name in ("CodeFence", "BlockCode"):
                lang = (getattr(node, "language", "") or "").strip()
                if lang:
                    lines.append(apply_style(self.theme.code_block_border, lang))
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
                            lines.append("  " + apply_style(self.theme.code_block, wl))
                lines.append("")

            elif name == "ThematicBreak":
                lines.append(apply_style(self.theme.hr, "─" * self.width))
                lines.append("")

            elif name == "List":
                lines.extend(self._render_list(node, depth=0))
                lines.append("")

            elif name == "Quote":
                border = apply_style(self.theme.quote_border, "▎ ")
                inner_w = max(1, self.width - visible_width(border))
                # Render inner content at the reduced width so it wraps to fit
                # beside the border instead of spilling a 2-char remainder.
                inner = self._render_blocks_at(node.children or [], inner_w)
                while inner and inner[-1] == "":
                    inner.pop()
                for il in inner:
                    for wl in wrap(il, inner_w) or [il]:
                        lines.append(border + apply_style(self.theme.quote, wl))
                lines.append("")

            elif name == "Table":
                lines.extend(self._render_table(node))
                lines.append("")

            elif name in ("HTMLBlock", "HtmlBlock"):
                content = getattr(node, "content", "").rstrip()
                for cl in content.split("\n"):
                    for wl in wrap(cl, self.width) or [""]:
                        lines.append(wl)
                lines.append("")

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
            marker = apply_style(self.theme.list_bullet, bullet)
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
        for idx, child in enumerate(item.children or []):
            name = type(child).__name__
            if name == "Paragraph":
                children = list(child.children or [])
                checkbox = None
                if idx == 0 and children and type(children[0]).__name__ == "RawText":
                    match = _TASK_CHECKBOX_RE.match(children[0].content)
                    if match:
                        checkbox = "☑" if match.group(1).lower() == "x" else "☐"
                        children[0].content = children[0].content[match.end() :]
                text = self._render_inline(children)
                if checkbox is not None:
                    text = apply_style(self.theme.list_bullet, checkbox) + " " + text
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

        # Column alignment from the delimiter row: None=left, 0=center, 1=right.
        column_align: list[int | None] = list(getattr(node, "column_align", None) or [])

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
        while len(column_align) < ncols:
            column_align.append(None)

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
            return apply_style(self.theme.hr, left + mid.join(segs) + right)

        top = _border("┌", "┬", "┐")
        mid = _border("├", "┼", "┤")
        bottom = _border("└", "┴", "┘")

        def _pad_cell(cell: str, cw: int, align: int | None) -> str:
            pad = max(0, cw - visible_width(cell))
            if align == 1:  # right
                return " " * pad + cell
            if align == 0:  # center
                left = pad // 2
                return " " * left + cell + " " * (pad - left)
            return cell + " " * pad  # left (default)

        def _row(cells: list[str]) -> list[str]:
            wrapped = [wrap(cell, col_widths[ci]) or [cell] for ci, cell in enumerate(cells)]
            height = max(len(w) for w in wrapped)
            sep_glyph = apply_style(self.theme.hr, "│")
            blank = (
                sep_glyph
                + sep_glyph.join(" " * (col_widths[ci] + 4) for ci in range(ncols))
                + sep_glyph
            )
            out = [blank]
            for li in range(height):
                padded = []
                for ci, lines in enumerate(wrapped):
                    cw = col_widths[ci]
                    cell = lines[li] if li < len(lines) else ""
                    padded.append("  " + _pad_cell(cell, cw, column_align[ci]) + "  ")
                sep = apply_style(self.theme.hr, "│")
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
                content = node.content
                if self.math_replacements:
                    content = _MATH_PLACEHOLDER_RE.sub(
                        lambda m: self.math_replacements[int(m.group(1))], content
                    )
                content = self._autolink_bare_urls(content)
                parts.append(apply_style(self.theme.body, content))
            elif name == "LineBreak":
                soft = getattr(node, "soft", True)
                parts.append("\n" if not soft or self.preserve_soft_breaks else " ")
            elif name == "InlineCode":
                parts.append(apply_style(self.theme.code_inline, self._raw(node)))
            elif name == "Strong":
                bold_text = self._render_inline(node.children or [])
                parts.append(apply_style(self.theme.bold, bold_text))
            elif name == "Emphasis":
                italic_text = self._render_inline(node.children or [])
                parts.append(apply_style(self.theme.italic, italic_text))
            elif name == "Strikethrough":
                inner_text = self._render_inline(node.children or [])
                parts.append(apply_style(self.theme.strikethrough, inner_text))
            elif name == "Link":
                inner = self._render_inline(node.children or []) or getattr(node, "target", "")
                target = self._safe_link_target(getattr(node, "target", ""))
                label = apply_style(self.theme.link_text, inner)
                parts.append(f"\x1b]8;;{target}\x1b\\{label}\x1b]8;;\x1b\\" if target else label)
            elif name == "AutoLink":
                target = self._safe_link_target(getattr(node, "target", ""))
                inner = self._render_inline(node.children or []) or target
                label = apply_style(self.theme.link_url, inner)
                parts.append(f"\x1b]8;;{target}\x1b\\{label}\x1b]8;;\x1b\\" if target else label)
            elif name == "Image":
                alt = self._render_inline(node.children or [])
                url = getattr(node, "src", "") or getattr(node, "target", "")
                label = f"[image: {alt}]" if alt else "[image]"
                styled_label = apply_style(self.theme.italic, label)
                target = self._image_link_target(url)
                parts.append(
                    f"\x1b]8;;{target}\x1b\\{styled_label}\x1b]8;;\x1b\\"
                    if target
                    else styled_label
                )
            elif name in ("HTMLSpan", "HtmlSpan"):
                content = getattr(node, "content", "")
                if re.fullmatch(r"<br\s*/?>", content, re.IGNORECASE):
                    parts.append("\n")
                else:
                    parts.append(content)
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

    def _autolink_bare_urls(self, text: str) -> str:
        """Turn bare ``http(s)://`` URLs into clickable OSC 8 hyperlinks."""

        def replace(match: re.Match[str]) -> str:
            url = match.group(0)
            trailing = ""
            while url and url[-1] in _BARE_URL_TRAILING_PUNCT:
                # Only strip a trailing ")" if it's an unmatched closer (e.g. the
                # surrounding "(see https://.../Foo_(bar))." wrapper) — keep one
                # that balances an opening "(" inside the URL itself, as in a
                # Wikipedia link ending in "_(disambiguation)".
                if url[-1] == ")" and url.count(")") <= url.count("("):
                    break
                trailing = url[-1] + trailing
                url = url[:-1]
            if not url:
                return match.group(0)
            target = self._safe_link_target(url)
            label = apply_style(self.theme.link_url, url)
            return f"\x1b]8;;{target}\x1b\\{label}\x1b]8;;\x1b\\{trailing}"

        return _BARE_URL_RE.sub(replace, text)

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

RendererFn = Callable[[Any, Any, int], list[str]]


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
        message: Any,
        theme: Any,
        width: int,
    ) -> list[str] | None:
        fn = self._registry.get(message.custom_type)
        if fn is None:
            return None
        return fn(message, theme, width)


message_renderer_registry = MessageRendererRegistry()

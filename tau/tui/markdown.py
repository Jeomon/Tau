from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from tau.tui.latex import extract_math, restore_math
from tau.tui.style import OSC8_CLOSE, apply_style
from tau.tui.utils import RESET, visible_width, wrap

if TYPE_CHECKING:
    from tau.tui.theme import MarkdownTheme


# LaTeX math lives in tau.tui.latex: it is extracted and converted before
# mistletoe ever sees the text (that module's docstring explains why it has to
# happen there rather than after tokenization), and spliced back in when
# rendering reaches the placeholder it left behind.
_TASK_CHECKBOX_RE = re.compile(r"^\[([ xX])\]\s+")
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_BARE_URL_TRAILING_PUNCT = ".,;:!?'\")]}*_~"


def _hyperlink(target: str, label: str) -> str:
    """Wrap ``label`` in an OSC 8 hyperlink, or return it bare when there is no target.

    An empty target would emit the close sequence as the open one, which
    renders as plain text anyway — so it is skipped outright.
    """
    return f"\x1b]8;;{target}\x1b\\{label}{OSC8_CLOSE}" if target else label


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
    cache: bool = True,
) -> list[str]:
    """Render a markdown string to a list of ANSI-coloured terminal lines.

    ``cache=False`` for text that is still changing — a streaming reply. The
    parse cache exists so a *width* change does not re-parse settled history,
    but streaming text differs on every token, so it never hits while still
    paying to hash an ever-growing key, build a tree, retain it, and evict
    something real to make room. Measured at ~10 ms per token on a long reply,
    which is a visible spinner stutter.
    """
    return _render_markdown(
        text, width, theme, preserve_soft_breaks=preserve_soft_breaks, cache=cache
    )


def _parse_markdown_uncached(text: str, preserve_soft_breaks: bool):
    """Parse ``text`` into a mistletoe token tree, cached across widths.

    Parsing does not depend on width — only layout does — but a width change
    invalidates ``MessageBlock``'s rendered-line cache, so every resize used to
    re-parse the entire transcript. Measured at ~69% of the markdown work on a
    resize, which is itself ~69% of the frame.

    Caching the tree instead costs ~5 KiB per typical reply (~17 KiB for a long
    one), so a few MiB for a large session — next to nothing beside the 140 MiB
    the cell grid used to peak at, and it is bounded by ``maxsize`` anyway.

    Safe because the renderer only reads the tree: verified byte-identical
    output rendering one parsed doc at four widths, in both orders, against a
    fresh parse each time, across paragraphs, nested lists, tables,
    blockquotes, headings and links.
    """
    document, html_block, html_span, md_context = _mistletoe()
    stripped, math_replacements = extract_math(text)
    with md_context(html_span, html_block):
        doc = document(stripped.splitlines(keepends=True))
    return doc, math_replacements


_parse_markdown = lru_cache(maxsize=512)(_parse_markdown_uncached)


def _render_markdown(
    text: str,
    width: int,
    theme: MarkdownTheme,
    *,
    preserve_soft_breaks: bool = False,
    trim_trailing_blank_lines: bool = True,
    cache: bool = True,
    allow_diagrams: bool = True,
) -> list[str]:
    parse = _parse_markdown if cache else _parse_markdown_uncached
    doc, math_replacements = parse(text, preserve_soft_breaks)
    _document, html_block, html_span, md_context = _mistletoe()
    with md_context(html_span, html_block):
        renderer = _Renderer(
            width,
            theme,
            preserve_soft_breaks,
            math_replacements,
            allow_diagrams=allow_diagrams,
        )
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


# Inline delimiters whose meaning is only decided by their closing half. While
# streaming, the text between an opening delimiter and the end of the live tail
# is ambiguous — "**very" is literal until the closing "**" lands — so the
# parser correctly renders the raw syntax, and the reader watches "[docs](http…"
# sit on screen until the ")" arrives and it snaps to a link. Holding the open
# run back until it resolves trades that flicker for the phrase appearing in one
# go, already styled. See _open_inline_cutoff.
_EMPHASIS_RUNS = ("***", "___", "**", "__", "~~", "*", "_")


def _open_inline_cutoff(line: str, start: int = 0) -> int:
    """Index in ``line`` where an unresolved inline construct begins.

    Scans ``line[start:]`` but returns an index into ``line``, so the caller can
    pass the whole tail with the offset of its last line instead of slicing a
    copy out of it on every streamed frame.

    Returns ``len(line)`` when everything from ``start`` on is closed. Only the
    final line of the live tail is ever scanned, so a construct left open by a
    model that never closes it stalls at most one line: as soon as the newline
    arrives that line is no longer the tail's last line and renders in full.
    """
    n = len(line)
    i = start
    # Earliest still-open construct. Emphasis is tracked separately from
    # brackets because "[a *b](c)" closes the bracket while the emphasis is
    # still open, and the earlier of the two is what has to be held.
    open_bracket = -1
    open_emphasis: dict[str, int] = {}

    while i < n:
        ch = line[i]

        if ch == "\\":  # escaped delimiter — consumes the next character
            i += 2
            continue

        if ch == "`":
            # Code spans bind tighter than everything else: a run of N backticks
            # closes only on another run of exactly N.
            run = 1
            while i + run < n and line[i + run] == "`":
                run += 1
            marker = "`" * run
            close = line.find(marker, i + run)
            while close != -1 and close + run < n and line[close + run] == "`":
                # A longer run is not a match — keep looking past it.
                nxt = close + run
                while nxt < n and line[nxt] == "`":
                    nxt += 1
                close = line.find(marker, nxt)
            if close == -1:
                return i
            i = close + run
            continue

        if ch == "[" or (ch == "!" and line[i : i + 2] == "!["):
            if open_bracket == -1:
                open_bracket = i
            i += 2 if ch == "!" else 1
            continue

        if ch == "]" and open_bracket != -1:
            # "[1]" in prose is literal text, not a half-written link, so a
            # bracket not followed by "(" resolves here instead of holding the
            # rest of the line. A "]" that is *last* stays open: the "(" may
            # simply not have streamed in yet, and that costs one frame.
            if i + 1 < n and line[i + 1] != "(":
                open_bracket = -1
            i += 1
            continue

        if ch == ")" and open_bracket != -1:
            open_bracket = -1
            i += 1
            continue

        for marker in _EMPHASIS_RUNS:
            if line.startswith(marker, i):
                # Same delimiter seen twice on the line closes it.
                if marker in open_emphasis:
                    del open_emphasis[marker]
                else:
                    open_emphasis[marker] = i
                i += len(marker)
                break
        else:
            i += 1

    candidates = [pos for pos in (open_bracket, *open_emphasis.values()) if pos != -1]
    # A lone trailing "~" may be the first half of a "~~" still streaming in.
    # Anywhere else a single tilde is literal in CommonMark and renders without
    # flicker ("~100ms" must keep streaming), so only the last one is held.
    if n > start and line[-1] == "~" and not line.endswith("~~"):
        candidates.append(n - 1)
    return min(candidates) if candidates else n


def _hold_open_inline(tail: str, in_fence: bool) -> str:
    """Trim ``tail`` at the first unresolved inline construct on its last line.

    ``in_fence`` suppresses the trim: inside a fenced code block every delimiter
    is literal, so there is nothing ambiguous to wait for.

    Runs on every streamed frame, so the common case — nothing open — returns
    ``tail`` itself with no copying. Only an actual hold slices, and only once.
    """
    if in_fence or not tail:
        return tail
    cutoff = _open_inline_cutoff(tail, tail.rfind("\n") + 1)
    return tail if cutoff >= len(tail) else tail[:cutoff]


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
            newly_stable = text[self._frozen_until : freeze_until]
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

        # _advance_freeze_scan already tracked whether the tail sits inside a
        # fenced code block, so the hold-back gets that for free.
        tail = _hold_open_inline(text[self._frozen_until :], self._scan_in_fence)
        tail_lines = _render_markdown(
            tail,
            width,
            theme,
            preserve_soft_breaks=preserve_soft_breaks,
            # The tail is the open block: different on every token, so caching
            # its parse never hits, while still hashing an ever-growing key,
            # retaining a tree per token and evicting settled history to make
            # room. The newly_stable render above *is* cached -- that text is
            # final, which is exactly what the cache is for.
            cache=False,
            # The tail may hold a fence that has not closed yet, and a
            # half-written diagram relayouts on every token. Frozen blocks
            # never contain an open fence (_advance_freeze_scan refuses to
            # freeze inside one), and the final non-streaming render goes
            # through render_markdown, so both of those still draw diagrams.
            allow_diagrams=False,
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
        if self._prefixed_generation != split.frozen_generation or self._prefixed_prefix != prefix:
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
        *,
        allow_diagrams: bool = True,
    ) -> None:
        self.width = width
        self.theme = theme
        self.preserve_soft_breaks = preserve_soft_breaks
        # False while a fence is still streaming in: a half-written diagram
        # relayouts on every token, so boxes grow, shrink and vanish frame to
        # frame. Source text until the fence closes, diagram after.
        self.allow_diagrams = allow_diagrams
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
                diagram = self._render_mermaid(node, lang)
                if diagram is not None:
                    lines.extend(diagram)
                    lines.append("")
                    continue
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

    def _render_mermaid(self, node: Any, lang: str) -> list[str] | None:
        """Box art for a ```mermaid fence, or None to fall back to source text.

        Falls back whenever the diagram cannot be laid out — an unsupported or
        malformed diagram, or one wider than the terminal — so the reader still
        gets the source rather than nothing. The two-space indent matches the
        fenced-code rendering it replaces.
        """
        if not self.allow_diagrams:
            return None
        from tau.tui.mermaid import is_mermaid_language, render_diagram

        if not is_mermaid_language(lang):
            return None
        art = render_diagram(self._code_content(node).rstrip("\n"), self.width - 2)
        if art is None:
            return None
        return ["  " + apply_style(self.theme.code_block, line) for line in art]

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
                content = restore_math(node.content, self.math_replacements)
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
                raw_target = getattr(node, "target", "")
                inner = self._render_inline(node.children or []) or raw_target
                target = self._safe_link_target(raw_target)
                parts.append(_hyperlink(target, apply_style(self.theme.link_text, inner)))
            elif name == "AutoLink":
                target = self._safe_link_target(getattr(node, "target", ""))
                inner = self._render_inline(node.children or []) or target
                parts.append(_hyperlink(target, apply_style(self.theme.link_url, inner)))
            elif name == "Image":
                alt = self._render_inline(node.children or [])
                url = getattr(node, "src", "") or getattr(node, "target", "")
                label = f"[image: {alt}]" if alt else "[image]"
                styled_label = apply_style(self.theme.italic, label)
                parts.append(_hyperlink(self._image_link_target(url), styled_label))
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
            return _hyperlink(target, label) + trailing

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

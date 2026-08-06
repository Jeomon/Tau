"""LaTeX math converted to terminal-readable Unicode.

Math is extracted from the raw text *before* mistletoe tokenizes it, for two
reasons that both trace back to mistletoe being a real CommonMark parser:

 1. \\(\\)/\\[\\] delimiters use a literal backslash, which collides with
    CommonMark's own backslash-escaping -- "(", ")", "[", "]" are escapable
    punctuation, so by the time any post-tokenization code sees this text,
    mistletoe has already silently stripped \\( down to a bare "(" (letters
    like \\lambda survive, since they're not escapable). A regex applied
    after tokenization can never see the delimiter.
 2. A literal "|" inside math (e.g. absolute-value bars, $|\\sin\\theta|$)
    is otherwise indistinguishable from a table-row column separator to
    mistletoe's tokenizer, which has no notion of math syntax and will
    shred the row into extra cells.

Extracting everything up front sidesteps both: :func:`extract_math` converts
each matched span immediately and swaps it for an inert placeholder that
mistletoe can only ever see as ordinary text, and :func:`restore_math` splices
the conversions back in once rendering reaches that placeholder's text node.
The placeholder's shape stays private to this module — callers hold the
returned replacement list and hand it straight back.
"""

from __future__ import annotations

import re
from functools import lru_cache

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

_MATH_PLACEHOLDER = "\ue000"  # private-use codepoint, never appears in real text
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
def math_to_text(expression: str) -> str:
    """Convert one LaTeX math expression to terminal-readable Unicode text."""
    try:
        expression = _unicode_scripts(expression)
        converted = _latex_node_cls()(math_mode="text").latex_to_text(f"${expression}$")
    except Exception:
        return expression
    converted = " ".join(line.strip() for line in converted.splitlines() if line.strip())
    return _normalize_math_spacing(converted) or expression


def extract_math(text: str) -> tuple[str, list[str]]:
    """Pull every recognised math span out of the raw text before mistletoe
    tokenizes it (see the module docstring for why). Returns the
    placeholder-substituted text plus the list of already-converted Unicode
    replacements, indexed by the placeholder. Fenced/inline code regions are
    passed through untouched (see _CODE_REGION_RE).
    """
    replacements: list[str] = []

    def _repl(is_display: bool):
        def repl(match: re.Match[str]) -> str:
            converted = math_to_text(match.group(1))
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
    return "".join(parts), replacements


def restore_math(text: str, replacements: list[str]) -> str:
    """Splice converted math back in wherever :func:`extract_math` left a marker."""
    if not replacements:
        return text
    return _MATH_PLACEHOLDER_RE.sub(lambda m: replacements[int(m.group(1))], text)

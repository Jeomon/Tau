from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .blocks import Block
from .content import clip
from .skill_collapse import collapse_skill_text
from .tool_args import extract_path

_TRUNCATE_USER = 256
_TRUNCATE_ASSISTANT = 200

# Strip common self-reflective assistant prefixes that carry no semantic info.
_SELF_TALK_PREFIX_RE = re.compile(r"^\s*(?:hmm|wait|actually|oh|okay|ok|well|so)[,.!\s-]+", re.I)

_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "over",
    "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "neither", "each", "every", "all", "any", "few", "more", "most",
    "other", "some", "such", "no",
    "that", "this", "these", "those", "it", "its",
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "they", "them", "their", "who", "which", "what",
    "if", "then", "than", "when", "where", "how", "just", "also",
}

_WORDLIKE_RE = re.compile(r"[^\W_]", re.UNICODE)
_TOKEN_RE = re.compile(r"\S+")
_STRIP_PUNCT_RE = re.compile(r"^\W+|\W+$", re.UNICODE)


def _truncate_tokens(text: str, limit: int) -> str:
    """Truncate to ~``limit`` significant words (stop words don't count)."""
    flat = re.sub(r"\s+", " ", text).strip()
    count = 0
    last_end = 0
    for m in _TOKEN_RE.finditer(flat):
        token = m.group()
        if _WORDLIKE_RE.search(token):
            bare = _STRIP_PUNCT_RE.sub("", token).lower()
            if bare and bare not in _STOP_WORDS:
                count += 1
                if count > limit:
                    return flat[:last_end].rstrip() + "...(truncated)"
        last_end = m.end()
    return flat


_BASH_CAP = 120
_PIPE_TAIL_RE = re.compile(
    r"\s*\|\s*(?:head|tail|sort|wc|column|tr|cut|awk|uniq|python3|node|bun)(?:\s[^|]*)?$"
)


def _compress_bash(raw: str) -> str:
    """Semantic compression: strip cd prefix, pipe-tail formatting, cap length."""
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    cmd = lines[0] if lines else raw
    cmd = re.sub(r"^cd\s+\S+\s*&&\s*", "", cmd)
    for _ in range(3):
        stripped = _PIPE_TAIL_RE.sub("", cmd)
        if stripped == cmd:
            break
        cmd = stripped
    if len(cmd) > _BASH_CAP:
        return cmd[: _BASH_CAP - 3] + "..."
    return cmd


_TOOL_SUMMARY_FIELDS: dict[str, str] = {
    "Read": "file_path", "Edit": "file_path", "Write": "file_path",
    "read": "file_path", "edit": "file_path", "write": "file_path",
    "Glob": "pattern", "Grep": "pattern", "glob": "pattern", "grep": "pattern",
    "ls": "path",
}

_SHELL_TOOLS = {"bash", "Bash", "terminal"}


def _tool_one_liner(name: str, args: dict[str, Any]) -> str:
    field_name = _TOOL_SUMMARY_FIELDS.get(name)
    if field_name and isinstance(args.get(field_name), str):
        return f'* {name} "{args[field_name]}"'
    path = extract_path(args)
    if path:
        return f'* {name} "{path}"'
    if name in _SHELL_TOOLS:
        raw = args.get("command") or args.get("description") or ""
        return f'* {name} "{_compress_bash(str(raw))}"'
    if isinstance(args.get("query"), str):
        return f'* {name} "{clip(args["query"], 60)}"'
    return f"* {name}"


@dataclass
class BriefLine:
    header: str
    lines: list[str] = field(default_factory=list)


def _ref(source_index: int | None) -> str:
    return f" (#{source_index})" if source_index is not None else ""


def build_brief_sections(blocks: list[Block]) -> list[BriefLine]:
    sections: list[BriefLine] = []
    last_header = ""

    def push(header: str, line: str) -> None:
        nonlocal last_header
        if header == last_header and sections:
            sections[-1].lines.append(line)
            return
        sections.append(BriefLine(header=header, lines=[line]))
        last_header = header

    for b in blocks:
        if b.kind == "user":
            if not b.text.strip():
                continue
            text = _truncate_tokens(collapse_skill_text(b.text), _TRUNCATE_USER)
            if text:
                push("[user]", text + _ref(b.source_index))
            last_header = "[user]"
        elif b.kind == "bash":
            cmd = _compress_bash(b.command)
            if cmd:
                push("[user]", f"$ {cmd}{_ref(b.source_index)}")
            last_header = "[user]"
        elif b.kind == "assistant":
            raw = b.text
            for _ in range(2):
                stripped = _SELF_TALK_PREFIX_RE.sub("", raw)
                if stripped == raw:
                    break
                raw = stripped
            text = _truncate_tokens(raw, _TRUNCATE_ASSISTANT)
            if text:
                push("[assistant]", text + _ref(b.source_index))
        elif b.kind == "tool_call":
            if not b.name or not b.name.strip():
                continue
            push("[assistant]", _tool_one_liner(b.name, b.args) + _ref(b.source_index))
        elif b.kind == "tool_result":
            # Tool result bodies are intentionally omitted from compact briefs.
            continue

    _collapse_repeated_tool_lines(sections)
    _cap_tool_calls_per_turn(sections)
    return sections


_TRAILING_REF_RE = re.compile(r"\(#(\d+)\)$")
_COLLAPSED_RE = re.compile(r"^(.*) \((#[\d, #]+)\) x(\d+)$")


def _collapse_repeated_tool_lines(sections: list[BriefLine]) -> None:
    """Collapse consecutive identical tool lines (same text, different #ref)."""
    for sec in sections:
        if sec.header != "[assistant]":
            continue
        out: list[str] = []
        for line in sec.lines:
            if not line.startswith("* "):
                out.append(line)
                continue
            ref_m = _TRAILING_REF_RE.search(line)
            ref = ref_m.group(1) if ref_m else ""
            base = line[: -(len(ref) + 3)].rstrip() if ref else line
            last = out[-1] if out else ""
            m = _COLLAPSED_RE.match(last)
            if m and m.group(1) == base:
                out[-1] = f"{base} ({m.group(2)}, #{ref}) x{int(m.group(3)) + 1}"
            elif re.search(r"\(#\d+\)$", last) and re.sub(r"\s*\(#\d+\)$", "", last) == base:
                prev_ref_m = _TRAILING_REF_RE.search(last)
                prev_ref = prev_ref_m.group(1) if prev_ref_m else ""
                out[-1] = f"{base} (#{prev_ref}, #{ref}) x2"
            else:
                out.append(line)
        sec.lines = out


def _cap_tool_calls_per_turn(sections: list[BriefLine], per_turn: int = 8) -> None:
    """Cap tool calls per [assistant] turn — keep the tail (latest actions)."""
    for sec in sections:
        if sec.header != "[assistant]":
            continue
        tool_idxs = [i for i, ln in enumerate(sec.lines) if ln.startswith("* ")]
        if len(tool_idxs) <= per_turn:
            continue
        drop_count = len(tool_idxs) - per_turn
        drop_set = set(tool_idxs[:drop_count])
        first_kept = tool_idxs[drop_count]
        nxt: list[str] = []
        inserted = False
        for i, line in enumerate(sec.lines):
            if i in drop_set:
                continue
            if not inserted and i == first_kept:
                nxt.append(f"* ({drop_count} earlier tool-call entries omitted)")
                inserted = True
            nxt.append(line)
        sec.lines = nxt


def stringify_brief(sections: list[BriefLine]) -> str:
    """Stringify BriefLine sections; suppress blank lines between tool runs."""
    out: list[str] = []
    for i, sec in enumerate(sections):
        if i > 0:
            prev = sections[i - 1]
            prev_is_tools = prev.header == "[assistant]" and all(
                ln.startswith("* ") for ln in prev.lines
            )
            cur_is_tools = sec.header == "[assistant]" and all(
                ln.startswith("* ") for ln in sec.lines
            )
            if not (prev_is_tools and cur_is_tools):
                out.append("")
        out.append(sec.header)
        out.extend(sec.lines)
    return "\n".join(out)

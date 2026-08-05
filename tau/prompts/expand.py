from __future__ import annotations

import re
import shlex


def _parse_args(args_str: str) -> list[str]:
    """Parse a shell-style argument string into a list of arguments."""
    try:
        return shlex.split(args_str)
    except ValueError:
        return args_str.split()


def expand(content: str, args_str: str) -> str:
    """
    Substitute argument placeholders in template content.

    Patterns supported:
        $1, $2, ...         Positional argument (1-based)
        $@ or $ARGUMENTS    All arguments joined with spaces
        ${1:-default}       Positional with fallback default
        ${@:N}              Args from index N (1-based) joined
        ${@:N:L}            Args from N, length L
    """
    args = _parse_args(args_str.strip()) if args_str.strip() else []
    all_args = " ".join(args)

    def _brace(match: re.Match) -> str:
        inner = match.group(1)

        # ${@:N} or ${@:N:L}
        m = re.match(r"@:(\d+)(?::(\d+))?$", inner)
        if m:
            n = int(m.group(1)) - 1
            length = int(m.group(2)) if m.group(2) else None
            sliced = args[n : n + length] if length is not None else args[n:]
            return " ".join(sliced)

        # ${N:-default}
        m = re.match(r"(\d+):-(.*)$", inner)
        if m:
            idx = int(m.group(1)) - 1
            return args[idx] if idx < len(args) else m.group(2)

        # ${N}
        m = re.match(r"(\d+)$", inner)
        if m:
            idx = int(m.group(1)) - 1
            return args[idx] if idx < len(args) else ""

        return match.group(0)

    def _positional(match: re.Match) -> str:
        idx = int(match.group(1)) - 1
        return args[idx] if idx < len(args) else ""

    result = re.sub(r"\$\{([^}]+)\}", _brace, content)
    result = re.sub(r"\$(?:@|ARGUMENTS)\b", all_args, result)
    result = re.sub(r"\$([1-9])\b", _positional, result)

    return result

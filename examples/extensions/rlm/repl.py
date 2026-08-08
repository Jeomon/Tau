"""The REPL environment an RLM root model drives.

The idea (Zhang, Kraska and Khattab, arXiv:2512.24601) is to stop feeding a
huge body of text to the model and instead hand it a *variable* holding that
text, plus a Python prompt. The model greps, slices and chunks its way through
it, and calls a model on the pieces it cares about. Its own context then grows
with what it chose to look at rather than with the size of the input.

Two things live here: a restricted namespace, and the ``llm_query`` function
that makes the recursion possible. Everything the model can reach is listed
explicitly, because the alternative — handing it the real builtins — makes the
output of a mis-generated line unpredictable.

This is a legibility boundary, not a security sandbox. Model-written Python
runs in this process. That is not a new capability in Tau, whose ``terminal``
tool already runs arbitrary commands, but it must not be mistaken for
containment.
"""

from __future__ import annotations

import builtins
import contextlib
import functools
import io
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Characters of a cell's output fed back to the root model. The whole point is
#: to keep the root context small, so a cell that prints the entire context is
#: truncated rather than defeating the exercise.
MAX_CELL_OUTPUT = 4000

#: Names the model may use. Anything absent raises NameError, which the model
#: sees and can correct on the next turn.
_ALLOWED_BUILTINS = frozenset(
    [
        "abs",
        "all",
        "any",
        "bool",
        "chr",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "hasattr",
        "hash",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "oct",
        "ord",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "zip",
        "Exception",
        "ValueError",
        "KeyError",
        "IndexError",
        "TypeError",
        "AttributeError",
        "StopIteration",
    ]
)

#: Modules preloaded into the namespace. Chosen for examining text: regex and
#: counting cover most of what a decomposition step needs.
_PRELOADED_MODULES = ("re", "json", "math", "statistics", "collections", "itertools", "textwrap")


class FinalAnswer(Exception):
    """Raised inside the REPL when the model calls ``FINAL`` or ``FINAL_VAR``.

    An exception rather than a return value because the model calls these from
    arbitrary depth inside a cell, and the cell should stop there.
    """

    def __init__(self, answer: str) -> None:
        super().__init__(answer)
        self.answer = answer


@dataclass
class CellResult:
    """What one executed cell produced."""

    stdout: str
    stderr: str
    elapsed: float
    truncated: bool = False

    def for_model(self) -> str:
        """The cell's output as the root model should see it."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr}")
        if not parts:
            return "(no output)"
        body = "\n".join(parts)
        if self.truncated:
            body += f"\n… output truncated at {MAX_CELL_OUTPUT} characters"
        return body


@dataclass
class ReplEnvironment:
    """A namespace holding the context, plus the recursion hook.

    ``sub_query`` is injected rather than imported so the environment stays
    synchronous and testable: the tool supplies a callable that reaches the
    model, and a test supplies one that does not.
    """

    context: str
    sub_query: Callable[[str], str]
    sub_call_budget: int = 8
    namespace: dict[str, Any] = field(default_factory=dict)
    sub_calls: int = 0

    def __post_init__(self) -> None:
        safe_builtins = {
            name: getattr(builtins, name) for name in _ALLOWED_BUILTINS if hasattr(builtins, name)
        }
        self.namespace = {
            "__builtins__": safe_builtins,
            "context": self.context,
            "context_length": len(self.context),
            "llm_query": self._llm_query,
            "FINAL": self._final,
            "FINAL_VAR": self._final_var,
        }
        for module_name in _PRELOADED_MODULES:
            with contextlib.suppress(ImportError):
                self.namespace[module_name] = __import__(module_name)

    def _llm_query(self, prompt: str) -> str:
        """Ask a model about one slice of the context.

        Depth is capped at one: this calls a model, never another RLM. The
        paper limits its own experiments the same way, and without the cap a
        runaway decomposition could fan out without bound.
        """
        if self.sub_calls >= self.sub_call_budget:
            return (
                f"[sub-call budget of {self.sub_call_budget} exhausted — "
                "answer from what you already gathered]"
            )
        if not isinstance(prompt, str) or not prompt.strip():
            return "[llm_query needs a non-empty string prompt]"
        self.sub_calls += 1
        return self.sub_query(prompt)

    def _final(self, answer: object) -> None:
        raise FinalAnswer(str(answer))

    def _final_var(self, name: str) -> None:
        """Answer with the contents of a variable.

        An answer assembled programmatically can be far longer than one the
        model would retype, and retyping it is where a long answer gets
        silently truncated.
        """
        if name not in self.namespace:
            raise NameError(f"FINAL_VAR: no variable named {name!r}")
        raise FinalAnswer(str(self.namespace[name]))

    def run(self, code: str) -> CellResult:
        """Execute one cell, capturing what it printed.

        Output is captured by binding ``print`` to this cell's buffer, not by
        redirecting ``sys.stdout``: the cell runs in a worker thread while the
        event loop keeps running, and ``redirect_stdout`` swaps a
        process-global. Anything the loop wrote meanwhile — a progress update,
        a repaint — landed in the cell's captured output instead of on the
        screen, and was lost from both.

        Errors are captured rather than raised: a traceback is information the
        model can act on, and killing the run on the first bad line would waste
        everything gathered so far.
        """
        stdout, stderr = io.StringIO(), io.StringIO()
        started = time.monotonic()
        self.namespace["print"] = functools.partial(print, file=stdout)
        try:
            exec(code, self.namespace, self.namespace)  # noqa: S102 - the point of a REPL
        except FinalAnswer:
            raise
        except Exception as error:
            stderr.write(f"{type(error).__name__}: {error}")
        elapsed = time.monotonic() - started

        out, err = stdout.getvalue(), stderr.getvalue()
        truncated = False
        if len(out) > MAX_CELL_OUTPUT:
            out = out[:MAX_CELL_OUTPUT]
            truncated = True
        return CellResult(stdout=out, stderr=err, elapsed=elapsed, truncated=truncated)

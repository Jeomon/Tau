"""Shared JSON-lines wire layer for Tau's stdout protocols.

Both headless modes stream the same agent events as JSON lines: ``--mode rpc``
(bidirectional, commands on stdin) and ``-p --mode json`` (one-shot, events
only). They differ in what a client may *send*, not in what Tau emits, so
everything about the outgoing side lives here rather than being implemented
twice:

* :class:`ProtocolOutput` — owns the real stdout, so a stray ``print`` cannot
  corrupt the stream, and provides backpressure.
* :func:`serialize_event` / :func:`json_default` — turn an event into a dict
  that will always encode.
* :class:`StreamDeltas` — the appended-text view of ``message_update``.
* :data:`FORWARDED_EVENTS` — the one list of events a client needs to mirror
  the session.

The two modes previously carried their own copies of all four. They drifted:
the JSON mode learned to emit deltas and the RPC mode did not, while the RPC
mode learned to survive un-encodable fields, guard stdout and apply
backpressure and the JSON mode did not. Keep this module the only place any of
it is written.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import enum
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Retry budget for the unbuffered fallback write path; see _write_raw.
_RAW_WRITE_RETRY_DELAY = 0.01
_RAW_WRITE_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Protocol stdout
# ---------------------------------------------------------------------------


class ProtocolOutput:
    """Owns the real stdout so the JSON-lines stream cannot be corrupted.

    Two jobs:

    * **Guard** — ``install()`` dups fd 1 aside for protocol writes and points
      fd 1 at stderr, so a stray ``print`` from a tool, an extension, or a
      subprocess lands on stderr instead of in the middle of a JSON line.
    * **Backpressure** — once :meth:`start_async` has run, writes go through an
      ``asyncio`` pipe writer. :meth:`write_line` stays synchronous and never
      blocks the event loop; async callers ``await drain()`` to wait for a slow
      client to catch up instead of stalling the agent inside a blocking write.

    When neither is installed (unit tests, unsupported platforms) writes fall
    back to the current ``sys.stdout``.
    """

    def __init__(self) -> None:
        self._raw: Any = None  # binary file object on the dup'd stdout fd
        self._restore_fd: int | None = None  # separate dup, kept for restore()
        self._saved_stdout: Any = None
        self._writer: asyncio.StreamWriter | None = None
        self._installed = False

    # ── Guard ────────────────────────────────────────────────────────────────

    def install(self) -> None:
        """Redirect fd 1 → fd 2 and keep the original stdout for protocol writes."""
        if self._installed:
            return
        try:
            dup_fd = os.dup(1)
            restore_fd = os.dup(1)
        except OSError:
            _log.warning("cannot duplicate stdout; protocol stream is unguarded")
            return
        try:
            raw = os.fdopen(dup_fd, "wb", buffering=0)
            os.dup2(2, 1)
        except OSError:
            _log.warning("cannot redirect stdout; protocol stream is unguarded")
            for fd in (dup_fd, restore_fd):
                with contextlib.suppress(OSError):
                    os.close(fd)
            return
        self._raw = raw
        self._restore_fd = restore_fd
        # Python-level writes hold their own buffer on the old fd 1; point them
        # at stderr too so nothing is flushed into the protocol stream later.
        self._saved_stdout = sys.stdout
        sys.stdout = sys.stderr
        self._installed = True

    def restore(self) -> None:
        """Undo :meth:`install` (best effort — called on the way out)."""
        if not self._installed:
            return
        self._installed = False
        if self._saved_stdout is not None:
            sys.stdout = self._saved_stdout
            self._saved_stdout = None
        writer, self._writer = self._writer, None
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
        raw, self._raw = self._raw, None
        if raw is not None and writer is None:
            # With a writer attached the transport owns (and closed) this fd.
            with contextlib.suppress(Exception):
                raw.close()
        restore_fd, self._restore_fd = self._restore_fd, None
        if restore_fd is not None:
            with contextlib.suppress(OSError):
                os.dup2(restore_fd, 1)
            with contextlib.suppress(OSError):
                os.close(restore_fd)

    # ── Backpressure ─────────────────────────────────────────────────────────

    async def start_async(self) -> None:
        """Attach an asyncio writer to the protocol fd (enables :meth:`drain`)."""
        if self._raw is None or self._writer is not None:
            return
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin, self._raw
            )
            self._writer = asyncio.StreamWriter(transport, protocol, None, loop)
        except (NotImplementedError, OSError, ValueError):
            # Windows Proactor loop and odd stdout targets (a regular file) do
            # not support pipe transports — keep the blocking path.
            _log.debug("async stdout writer unavailable", exc_info=True)
            self._writer = None

    async def drain(self) -> None:
        """Wait until the client has consumed what we buffered."""
        writer = self._writer
        if writer is None:
            return
        with contextlib.suppress(Exception):
            await writer.drain()

    # ── Writing ──────────────────────────────────────────────────────────────

    def write_line(self, line: str) -> None:
        if self._writer is not None:
            self._writer.write(line.encode("utf-8"))
        elif self._raw is not None:
            self._write_raw(line.encode("utf-8"))
        else:
            sys.stdout.write(line)
            sys.stdout.flush()

    def _write_raw(self, payload: bytes) -> None:
        """Write to the unbuffered protocol fd, retrying a full pipe.

        This path is only taken before :meth:`start_async` has run, or where a
        pipe transport is unavailable — so there is no flow control underneath
        it. A reader that has not drained yet makes the fd report "try again"
        (``EAGAIN``/``EWOULDBLOCK``, or ``ENOBUFS`` on BSD/macOS, all of which
        surface as ``BlockingIOError``), and without a retry the line is simply
        lost mid-stream. Bounded, because blocking here stalls the event loop:
        past the budget the line is dropped with a warning rather than hanging
        the agent on a client that has stopped reading.
        """
        deadline = time.monotonic() + _RAW_WRITE_TIMEOUT
        while True:
            try:
                self._raw.write(payload)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _log.warning(
                        "protocol stdout blocked for %.1fs; dropping a line",
                        _RAW_WRITE_TIMEOUT,
                    )
                    return
                time.sleep(_RAW_WRITE_RETRY_DELAY)
            except InterruptedError:
                continue  # EINTR: a signal arrived mid-write, just resume


OUTPUT = ProtocolOutput()


def install_output_guard() -> None:
    """Claim stdout for the protocol as early as possible.

    The CLI calls this the moment it knows the run speaks a stdout protocol —
    before the runtime (and its extensions) is built, since anything they print
    would otherwise corrupt the stream. Idempotent.
    """
    OUTPUT.install()


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def json_default(value: object) -> Any:
    """Last-resort encoder so an exotic field can never kill the stream."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        with contextlib.suppress(Exception):
            return dataclasses.asdict(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes | bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def encode_line(obj: dict) -> str:
    """Encode one protocol record, newline included."""
    return json.dumps(obj, default=json_default) + "\n"


def write(obj: dict) -> None:
    """Write a JSON line to the protocol stdout immediately."""
    OUTPUT.write_line(encode_line(obj))


def shallow_asdict(event: object) -> dict:
    """``dataclasses.asdict`` without the deep copy (used when that one fails)."""
    return {f.name: getattr(event, f.name, None) for f in dataclasses.fields(event)}  # type: ignore[arg-type]


def serialize_event(event: object) -> dict:
    """Turn an event object into the dict that goes on the wire.

    Field names stay Python ``snake_case`` — see docs/rpc.md. Non-dataclass
    events keep their payload (``vars``) instead of collapsing to a bare type,
    and a dataclass whose fields resist deep-copying degrades to a shallow dict
    rather than raising and dropping the event entirely.
    """
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        try:
            return dataclasses.asdict(event)
        except Exception:
            _log.debug("asdict failed for %s; using shallow dict", type(event).__name__)
            return shallow_asdict(event)
    payload = getattr(event, "__dict__", None)
    event_type = getattr(event, "type", None)
    if isinstance(payload, dict) and payload:
        out = {k: v for k, v in payload.items() if not k.startswith("_")}
        out["type"] = event_type if isinstance(event_type, str) else type(event).__name__
        return out
    if isinstance(event_type, str):
        return {"type": event_type}
    return {"type": type(event).__name__}


# ---------------------------------------------------------------------------
# Streaming deltas
# ---------------------------------------------------------------------------


class StreamDeltas:
    """Tracks streamed text so ``message_update`` can carry what was appended.

    ``message_update`` fires once per streamed token and carries the *whole*
    accumulated message, so a client that only wants the new characters still
    pays for the full message every tick — stdout grows with the square of the
    reply length (measured: 38 MB for a 39 KB answer). ``delta`` and
    ``thinking_delta`` are added alongside for clients that would rather
    append; ``omit_message`` drops the redundant copy entirely.

    RPC keeps the full message by default (existing clients redraw from it) and
    lets a client opt out with ``set_update_mode``. The one-shot JSON mode has
    no handshake to opt in with, so it omits from the start.
    """

    def __init__(self, *, omit_message: bool = False) -> None:
        self._text = ""
        self._thinking = ""
        self.omit_message = omit_message

    def reset(self) -> None:
        """Start a fresh message; deltas are relative to it, not the last one."""
        self._text = ""
        self._thinking = ""

    @staticmethod
    def _appended(previous: str, current: str) -> str:
        """The suffix added to ``previous``, or all of ``current`` if rewritten.

        A TextEndEvent replaces a streaming block's content outright rather than
        appending to it, so the prefix does not always hold.
        """
        return current[len(previous) :] if current.startswith(previous) else current

    def annotate(self, payload: dict, message: object) -> dict:
        """Add ``delta``/``thinking_delta`` to a serialized message_update."""
        from tau.message.types import TextContent, ThinkingContent

        contents = getattr(message, "contents", []) or []
        text = "".join(c.content for c in contents if isinstance(c, TextContent))
        thinking = "".join(c.content for c in contents if isinstance(c, ThinkingContent))
        if delta := self._appended(self._text, text):
            payload["delta"] = delta
        if delta := self._appended(self._thinking, thinking):
            payload["thinking_delta"] = delta
        self._text, self._thinking = text, thinking
        if self.omit_message:
            payload.pop("message", None)
        return payload

    def apply(self, payload: dict, event: object) -> dict:
        """Shape one already-serialized event, tracking stream state.

        Handles the ``message_start`` reset and the ``message_update``
        annotation together so a caller only has to route events through here.
        """
        event_type = payload.get("type")
        if event_type == "message_start":
            self.reset()
        elif event_type == "message_update":
            return self.annotate(payload, getattr(event, "message", None))
        return payload


# ---------------------------------------------------------------------------
# Event coverage
# ---------------------------------------------------------------------------


# Events forwarded to a protocol client. Every engine event needed to mirror
# the session must be here — `message_rollback` in particular, or a client that
# replays the transcript silently drifts after an interrupted tool turn.
FORWARDED_EVENTS = (
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "message_rollback",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "tool_execution_failure",
    "agent_error",
    "llm_retry",
    "compaction_start",
    "compaction_end",
    "compaction_cancelled",
    "compaction_failure",
    "queue_update",
    "settled",
    # Without these the `terminal` command is a black box: success: true and
    # no way to see what the command printed.
    "terminal_execution",
    "terminal_output",
)

# What the one-shot JSON stream sends unless asked for everything. It is the
# historical set plus `message_rollback`, which is not a verbosity choice: an
# interrupted tool turn persists an assistant tool-call message and its result
# before the abort lands, and both are then withdrawn. A consumer that mirrors
# the transcript and never hears about the withdrawal silently diverges from
# the session file — so it stays in the default even though everything else
# added alongside it is opt-in.
COMPACT_EVENTS = (
    "agent_start",
    "agent_end",
    "message_start",
    "message_update",
    "message_end",
    "message_rollback",
    "tool_execution_start",
    "tool_execution_end",
    "agent_error",
    "settled",
)

EVENT_SETS = {"compact": COMPACT_EVENTS, "full": FORWARDED_EVENTS}

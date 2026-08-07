"""Print mode (single-shot): send prompts, emit the result, exit.

Covers both non-interactive shapes, which differ only in what reaches stdout:

* ``text`` — ``tau -p "prompt"``: the final assistant message, nothing else.
* ``json`` — ``tau --mode json "prompt"``: every forwarded event as a JSON
  line, ending at ``settled``.

Everything around that — sending the prompts in order, waiting for the run to
settle, signal handling — is the same for both, so it is written once here. The
wire format for the ``json`` shape lives in :mod:`tau.modes.wire`, shared with
RPC mode.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import click

from tau.modes import wire
from tau.modes.signals import (
    EXIT_SIGHUP,
    EXIT_SIGTERM,
    Interrupted,
    exit_on_signal,
    raise_if_interrupted,
)

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_log = logging.getLogger(__name__)

__all__ = ["EXIT_SIGHUP", "EXIT_SIGTERM", "Interrupted", "run_print_mode"]


def _abort(runtime: Runtime) -> None:
    """Stop the turn so tools stop and the session is written out."""
    agent = getattr(runtime, "agent", None)
    abort = getattr(agent, "abort", None) if agent is not None else None
    if callable(abort):
        abort()


async def _send(runtime: Runtime, messages: list[str], settled: asyncio.Event) -> None:
    """Run each prompt in order, waiting for the previous one to settle."""
    for message in messages:
        settled.clear()
        try:
            await runtime.invoke(message)
        except click.ClickException:
            raise
        except Exception as exc:
            # A mid-stream provider error reaches the caller on the assistant
            # message (text mode) or as an agent_error event (json mode). A
            # failure *before* the stream starts — bad model or provider
            # config — has no message to carry it, so without this it would
            # propagate as a raw traceback instead of a one-line CLI error.
            raise click.ClickException(str(exc)) from exc
        await settled.wait()


async def run_print_mode(
    runtime: Runtime,
    messages: list[str],
    *,
    output: str = "text",
    json_events: str = "compact",
) -> None:
    """Send ``messages`` in order and report the result.

    ``output`` is ``"text"`` (final assistant message only) or ``"json"``
    (the event stream). ``json_events`` selects the forwarded set for the
    latter — see :data:`tau.modes.wire.EVENT_SETS`.
    """
    if not messages:
        raise click.ClickException(
            'A message is required in non-interactive mode. Usage: tau -p "your prompt"'
        )

    if output == "json":
        await _run_json(runtime, messages, json_events)
    else:
        await _run_text(runtime, messages)


async def _run_text(runtime: Runtime, messages: list[str]) -> None:
    """Print the final assistant message; fail loudly if it carried an error."""
    from tau.message.types import AssistantMessage

    result: AssistantMessage | None = None
    settled = asyncio.Event()

    async def on_message_end(event: object) -> None:
        nonlocal result
        msg = getattr(event, "message", None)
        if isinstance(msg, AssistantMessage):
            result = msg

    async def on_settled(_event: object) -> None:
        settled.set()

    hooks = runtime.hooks
    unsubs = [
        hooks.register("message_end", on_message_end),
        hooks.register("settled", on_settled),
    ]

    try:
        with exit_on_signal(lambda: _abort(runtime)) as interrupted:
            await _send(runtime, messages, settled)
    finally:
        for unsub in unsubs:
            unsub()

    raise_if_interrupted(interrupted)

    if result is None:
        raise click.ClickException("No response received.")
    if result.error:
        raise click.ClickException(result.error)

    click.echo(result.text_content(), nl=False)


async def _run_json(runtime: Runtime, messages: list[str], json_events: str) -> None:
    """Stream every forwarded event as a JSON line, ending at ``settled``."""
    from tau.hooks.types import SettledEvent

    settled = asyncio.Event()

    # The one-shot stream has no handshake to opt in with, so it always omits
    # the cumulative `message` copy: `message_start` gives the initial message,
    # the deltas build it, `message_end` is authoritative. RPC keeps the full
    # copy by default because its clients redraw from it.
    deltas = wire.StreamDeltas(omit_message=True)

    async def on_event(event: object) -> None:
        wire.write(deltas.apply(wire.serialize_event(event), event))
        # Let a slow consumer apply backpressure here rather than inside a
        # blocking write that would stall the whole event loop.
        await wire.OUTPUT.drain()
        if isinstance(event, SettledEvent):
            settled.set()

    hooks = runtime.hooks
    await wire.OUTPUT.start_async()
    forwarded = wire.EVENT_SETS.get(json_events, wire.COMPACT_EVENTS)
    unsubs = [hooks.register(name, on_event) for name in forwarded]

    try:
        with exit_on_signal(lambda: _abort(runtime)) as interrupted:
            await _send(runtime, messages, settled)
    finally:
        for unsub in unsubs:
            unsub()

    raise_if_interrupted(interrupted)

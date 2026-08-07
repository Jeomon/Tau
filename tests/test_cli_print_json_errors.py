"""Covers pre-stream failure handling in `tau --print` / `tau --mode json`.

Regression context: a mid-stream provider error is captured cleanly (print
mode raises click.ClickException(result.error); json mode streams it as a
message_end/agent_end payload). But a failure before the stream starts (bad
model/provider config, a client construction error, etc.) never reaches
those paths — runtime.invoke() itself raises, and without a wrapper that
propagates past main() as a raw Python traceback instead of Click's clean
one-line "Error: ..." message.
"""

from __future__ import annotations

import asyncio

import click
import pytest

from tau.hooks.service import Hooks
from tau.modes.print.mode import _run_json, _run_text


class _FailingRuntime:
    """A runtime whose invoke() blows up before emitting any events."""

    def __init__(self, exc: BaseException) -> None:
        self.hooks = Hooks()
        self._exc = exc

    async def invoke(self, _message: str) -> None:
        raise self._exc


def test_print_mode_wraps_pre_stream_failure_in_click_exception() -> None:
    runtime = _FailingRuntime(ValueError("Anthropic on Vertex AI requires a project ID."))
    with pytest.raises(click.ClickException) as excinfo:
        asyncio.run(_run_text(runtime, ["prompt"]))
    assert "requires a project ID" in str(excinfo.value)


def test_json_mode_wraps_pre_stream_failure_in_click_exception() -> None:
    runtime = _FailingRuntime(ValueError("Anthropic on Vertex AI requires a project ID."))
    with pytest.raises(click.ClickException) as excinfo:
        asyncio.run(_run_json(runtime, ["prompt"], "compact"))
    assert "requires a project ID" in str(excinfo.value)


def test_print_mode_does_not_rewrap_an_existing_click_exception() -> None:
    original = click.ClickException("already clean")
    runtime = _FailingRuntime(original)
    with pytest.raises(click.ClickException) as excinfo:
        asyncio.run(_run_text(runtime, ["prompt"]))
    assert excinfo.value is original


def test_json_mode_does_not_rewrap_an_existing_click_exception() -> None:
    original = click.ClickException("already clean")
    runtime = _FailingRuntime(original)
    with pytest.raises(click.ClickException) as excinfo:
        asyncio.run(_run_json(runtime, ["prompt"], "compact"))
    assert excinfo.value is original

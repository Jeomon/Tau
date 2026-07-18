"""Regression tests: Agent.abort() must survive the per-continuation signal swap.

invoke() installs a fresh asyncio.Event before each retry/continuation, so an
abort issued while no engine run is in flight (compaction, save-point hooks)
used to be silently dropped and queued follow-ups still executed. The
persistent _abort_requested flag plus _replace_signal() carry it across.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tau.agent.service import Agent


def _bare_agent() -> Agent:
    agent = Agent.__new__(Agent)
    agent._abort_requested = False
    agent._signal = asyncio.Event()
    agent._engine = SimpleNamespace(  # type: ignore[assignment]
        llm=SimpleNamespace(api=SimpleNamespace(options=SimpleNamespace(signal=None)))
    )
    return agent


def test_abort_is_carried_into_the_replacement_signal() -> None:
    agent = _bare_agent()
    agent.abort()
    assert agent._signal.is_set()

    agent._replace_signal()
    # The pending abort survives the swap instead of being silently dropped...
    assert agent._abort_requested is True
    assert agent._signal.is_set()
    # ...and the LLM options track the new signal.
    assert agent._engine.llm.api.options.signal is agent._signal


def test_replace_signal_starts_clear_without_a_pending_abort() -> None:
    agent = _bare_agent()
    agent._replace_signal()
    assert not agent._signal.is_set()


def test_shutdown_also_persists_the_abort() -> None:
    agent = _bare_agent()
    agent.shutdown()
    agent._replace_signal()
    assert agent._signal.is_set()


def test_check_compaction_short_circuits_on_pending_abort() -> None:
    agent = _bare_agent()
    agent._abort_requested = True
    assert asyncio.run(agent._check_compaction()) is False

"""Token usage is priced, and every model call that costs money records it.

Providers report tokens, never money — the per-million rates live on the model.
`Model.calculate_cost()` existed from the start but had no production caller, so
`usage.cost` stayed at its zero default on every message and anything totalling
spend silently reported nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tau.engine.service import Engine
from tau.engine.types import EngineContext
from tau.inference.model.types import Cost, Model
from tau.inference.types import EndEvent, StopReason, TextEndEvent
from tau.message.types import AssistantMessage, TextContent, Usage, UserMessage


class _PricedModel:
    """Only the slice of Model the engine touches for pricing."""

    name = "priced-model"

    def __init__(self) -> None:
        self.cost = Cost(input=3.0, output=15.0, cache_read=0.30, cache_write=3.75)

    def calculate_cost(self, usage: Usage):
        return Model.calculate_cost(self, usage)  # type: ignore[arg-type]


class _Options:
    headers: dict = {}


class _Api:
    options = _Options()


class _LLM:
    def __init__(self, events: list, model=None) -> None:
        self._events = events
        self.model = model if model is not None else _PricedModel()
        self.api = _Api()
        self.provider_id = "fake"

    def stream(self, ctx):
        return self._gen()

    async def _gen(self):
        for ev in self._events:
            yield ev


def _turn(**usage) -> list:
    return [
        TextEndEvent(text=TextContent(content="done")),
        EndEvent(reason=StopReason.Stop, **usage),
    ]


def _run(llm) -> AssistantMessage:
    engine = Engine(cwd=Path("."), llm=llm, tools=[], system_prompt="")  # type: ignore[arg-type]
    asyncio.run(engine.run(EngineContext(system_prompt="", messages=[UserMessage.from_text("hi")])))
    return next(m for m in engine.state.messages if isinstance(m, AssistantMessage))


class TestPricingReachesTheMessage:
    def test_a_completed_turn_carries_a_non_zero_cost(self):
        msg = _run(_LLM(_turn(input_tokens=1_000_000, output_tokens=100_000)))

        assert msg.usage.input_tokens == 1_000_000
        assert msg.usage.cost.input == 3.0  # $3/M
        assert msg.usage.cost.output == 1.5  # $15/M on 100k
        assert msg.usage.cost.total == 4.5

    def test_cache_tokens_are_priced_at_their_own_rates(self):
        msg = _run(_LLM(_turn(input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000)))

        assert msg.usage.cost.cache_read == 0.30
        assert msg.usage.cost.total == 0.30

    def test_a_zero_token_turn_costs_nothing(self):
        assert _run(_LLM(_turn())).usage.cost.total == 0.0

    def test_a_model_without_pricing_does_not_break_the_turn(self):
        """Custom providers and test doubles may expose no calculate_cost."""

        class _Bare:
            name = "bare"

        msg = _run(_LLM(_turn(input_tokens=1_000), model=_Bare()))

        assert msg.usage.input_tokens == 1_000
        assert msg.usage.cost.total == 0.0

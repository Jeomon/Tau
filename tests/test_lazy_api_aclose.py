"""Regression tests for LazyAPI.aclose() and BaseLLMAPI's default aclose().

Runtime.set_model() closes the outgoing provider adapter on every model
switch (see tau/runtime/service.py) via this exact contract: LazyAPI must
not force-construct (import the SDK, build a network client) an adapter
that was never actually used just to immediately close it — that would
defeat the whole point of the lazy-resolution the proxy exists for.
"""

from __future__ import annotations

import asyncio

from tau.inference.api.registry import LazyAPI
from tau.inference.api.text.base import BaseLLMAPI


class _FakeReal:
    instances: list[_FakeReal] = []

    def __init__(self, options: object) -> None:
        self.options = options
        self.aclose_calls = 0
        _FakeReal.instances.append(self)

    async def aclose(self) -> None:
        self.aclose_calls += 1


def _reset() -> None:
    _FakeReal.instances = []


def test_aclose_does_not_construct_an_unresolved_adapter() -> None:
    _reset()
    lazy = LazyAPI(registry=None, api_ref=_FakeReal, options=object())

    asyncio.run(lazy.aclose())

    assert _FakeReal.instances == []


def test_aclose_closes_an_already_resolved_adapter() -> None:
    _reset()
    lazy = LazyAPI(registry=None, api_ref=_FakeReal, options=object())
    lazy._resolve()  # simulate a real request having happened

    asyncio.run(lazy.aclose())

    assert len(_FakeReal.instances) == 1
    assert _FakeReal.instances[0].aclose_calls == 1


def test_aclose_only_closes_once_even_if_called_twice() -> None:
    _reset()
    lazy = LazyAPI(registry=None, api_ref=_FakeReal, options=object())
    lazy._resolve()

    asyncio.run(lazy.aclose())
    asyncio.run(lazy.aclose())

    assert _FakeReal.instances[0].aclose_calls == 2  # delegates each time; close is idempotent


class _ConcreteAPI(BaseLLMAPI):
    async def stream(self, context, model):  # pragma: no cover - not exercised
        raise NotImplementedError
        yield  # type: ignore[unreachable]


def test_base_llm_api_aclose_default_is_a_safe_noop() -> None:
    from datetime import timedelta

    from tau.inference.types import LLMOptions, Transport

    options = LLMOptions(
        api_key="x",
        base_url=None,
        headers={},
        max_retries=0,
        timeout=timedelta(seconds=1),
        transport=Transport.HTTP,
    )
    api = _ConcreteAPI(options)

    asyncio.run(api.aclose())  # must not raise

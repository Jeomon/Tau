from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from tau.inference.model.types import Model
from tau.inference.types import LLMContext, LLMEvent, LLMOptions, Transport


class BaseLLMAPI(ABC):
    """Abstract base for all provider-specific streaming LLM API implementations."""

    SUPPORTED_TRANSPORTS: tuple[Transport, ...] = (Transport.HTTP,)

    def __init__(self, options: LLMOptions) -> None:
        """Validate transport compatibility and store options."""
        if options.transport not in self.SUPPORTED_TRANSPORTS:
            raise ValueError(
                f"{self.__class__.__name__} does not support transport"
                f" '{options.transport.value}'."
                f" Supported: {[t.value for t in self.SUPPORTED_TRANSPORTS]}"
            )
        self.options = options

    def _cancelled(self) -> bool:
        """Return True if the caller has set the abort signal."""
        return self.options.signal is not None and self.options.signal.is_set()

    @abstractmethod
    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:
        """Yield LLMEvent objects as the provider streams its response."""
        # The unreachable `yield` marks this as an async-generator signature so
        # callers (and type-checkers) treat stream() as returning an
        # AsyncGenerator — which supports aclose() — rather than a coroutine.
        raise NotImplementedError
        yield  # type: ignore[unreachable]  # pragma: no cover

    async def invoke(self, context: LLMContext, model: Model) -> list[LLMEvent]:
        """Collect all stream events into a list and return them."""
        events: list[LLMEvent] = []
        async for event in self.stream(context, model=model):
            events.append(event)
        return events

    async def aclose(self) -> None:
        """Release any persistent network client this adapter holds.

        Default no-op: most adapters either open a per-call client (already
        scoped with ``async with``) or hold nothing closable. Adapters that
        construct a persistent SDK client in ``__init__`` (Anthropic, OpenAI,
        Ollama, Mistral, the Vertex variants, ...) override this — callers
        (e.g. ``Runtime.set_model()`` swapping the active model) must close
        the outgoing adapter or its connection pool leaks for the rest of
        the process.
        """
        return None

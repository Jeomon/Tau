from __future__ import annotations

import importlib
from typing import TypeVar

T = TypeVar("T")

_Entry = type[T] | str


class BaseAPIRegistry[T]:
    """Shared dict-backed registry for API implementation classes.

    Entries may be either a class or a lazy ``"module:ClassName"`` import path,
    so a provider SDK is only imported when that provider is actually resolved.
    """

    def __init__(self) -> None:
        self._apis: dict[str, _Entry] = {}

    def register(self, name: str, api: _Entry) -> None:
        """Register an API class or a lazy 'module:ClassName' import path."""
        self._apis[name] = api

    def unregister(self, name: str) -> None:
        self._apis.pop(name, None)

    def list(self) -> list[type[T]]:
        return [self._resolve(name) for name in list(self._apis)]

    def get(self, name: str) -> type[T] | None:
        if name not in self._apis:
            return None
        return self._resolve(name)

    def reset(self) -> None:
        self._apis.clear()

    def _resolve(self, name: str) -> type[T]:
        entry = self._apis[name]
        if isinstance(entry, str):
            mod_path, _, cls_name = entry.partition(":")
            cls = getattr(importlib.import_module(mod_path), cls_name)
            self._apis[name] = cls
            return cls
        return entry


class LazyAPI:
    """Proxy around a provider API adapter that defers importing the provider
    SDK and constructing the network client until the first request.

    ``.options`` is served directly (cheap) so configuration set right after
    construction — timeout, thinking level, abort signal, api_key — never
    triggers an SDK import. Any other attribute access (``stream``, ``invoke``,
    ``generate``, ``synthesize``, ``transcribe``, ...) resolves the real
    adapter, imports its SDK, builds the client, and delegates thereafter.
    """

    def __init__(self, registry: object, api_ref: object, options: object) -> None:
        object.__setattr__(self, "options", options)
        object.__setattr__(self, "_registry", registry)
        object.__setattr__(self, "_api_ref", api_ref)
        object.__setattr__(self, "_real", None)

    def _resolve(self):
        real = object.__getattribute__(self, "_real")
        if real is None:
            ref = object.__getattribute__(self, "_api_ref")
            if isinstance(ref, str):
                api_class = object.__getattribute__(self, "_registry").get(ref)
                if api_class is None:
                    raise ValueError(f"API '{ref}' not found in registry.")
            else:
                api_class = ref
            real = api_class(object.__getattribute__(self, "options"))
            object.__setattr__(self, "_real", real)
        return real

    async def resolve_async(self) -> None:
        """Import the provider SDK and build its client off the event loop.

        The first resolution imports the provider's SDK module (e.g.
        ``anthropic``), which can take seconds on a cold cache. Running that
        synchronously on the asyncio event loop stalls everything else driven
        by it, including the render loop's spinner animation. Call this before
        the real adapter is needed so the import happens on a worker thread.
        """
        if object.__getattribute__(self, "_real") is None:
            import asyncio

            await asyncio.to_thread(self._resolve)

    def __getattr__(self, name: str):
        # Only reached for attributes not set on the proxy itself (e.g. not
        # ``options``), which means the real adapter is genuinely needed.
        return getattr(self._resolve(), name)

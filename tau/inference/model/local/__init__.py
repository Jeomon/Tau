"""Discovery of models installed in locally-running inference backends.

Each backend gets its own module here (`ollama.py`, `lmstudio.py`, `vllm.py`,
`llamacpp.py`)
exposing a `discover_local_*_models()` / `register_local_*_models()` pair —
both best-effort, so a missing/unreachable local daemon yields zero models
rather than an exception. `register_all()` runs every backend's discovery
concurrently and registers whatever is found into the shared text model
registry (`TextLLM._builtin_models()`).

From there, results need no separate wiring to reach the TUI: the `/model`
picker (`tau.modes.interactive.commands.model._list_for`) calls
`TextLLM.list_available()` fresh every time it opens rather than caching a
startup snapshot, so newly-registered local models simply appear next time
the picker is opened.

Intended to run once, in the background, at process startup — see
`Runtime._start_local_model_discovery`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

_log = logging.getLogger(__name__)


def _backends() -> tuple[Callable[[], Coroutine[Any, Any, int]], ...]:
    # Imported lazily so importing this package doesn't eagerly pull in every
    # backend module (mirrors the lazy-adapter-loading convention elsewhere
    # in tau.inference).
    from tau.inference.model.local.llamacpp import register_local_llamacpp_models
    from tau.inference.model.local.lmstudio import register_local_lmstudio_models
    from tau.inference.model.local.ollama import register_local_ollama_models
    from tau.inference.model.local.vllm import register_local_vllm_models

    return (
        register_local_ollama_models,
        register_local_lmstudio_models,
        register_local_vllm_models,
        register_local_llamacpp_models,
    )


async def register_all() -> int:
    """Run every local backend's discovery concurrently; return total models registered.

    Each backend is independently best-effort — one unreachable/missing local
    daemon contributes 0 and never blocks or fails the others.

    Each backend runs in its own worker thread (its own fresh event loop via
    ``asyncio.run``), not just as a task on the caller's loop. Constructing an
    ``httpx.AsyncClient`` has real synchronous CPU cost beyond the (already
    shared/cached, see ``get_shared_ssl_context``) SSL context — connection
    pool and transport setup — measured at ~90-100ms per backend, ~0.3-0.4s
    total across the four. Run as plain tasks on the caller's loop (as this
    used to be), that cost lands squarely on the same thread driving the
    just-launched TUI's render loop, at the exact moment ``Runtime.create``
    is finishing up. Threading it mirrors the same fix already applied to
    git-status (``tau/agent/prompt/builder.py``) and the LSP extension's
    eager warm-up.
    """
    backends = _backends()
    results = await asyncio.gather(
        *(asyncio.to_thread(asyncio.run, backend()) for backend in backends),
        return_exceptions=True,
    )

    total = 0
    for backend, result in zip(backends, results, strict=True):
        if isinstance(result, BaseException):
            _log.debug(
                "Local model discovery failed for %s", backend.__module__, exc_info=result
            )
            continue
        total += result
    return total

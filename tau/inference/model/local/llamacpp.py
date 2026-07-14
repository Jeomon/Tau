"""Discovery of models served by a local llama.cpp server (`llama-server`).

Like vLLM, llama.cpp's server has no separate catalog API — a running
instance (`llama-server -m model.gguf`) exposes exactly the model it was
launched with via the standard OpenAI-compatible `GET /v1/models` endpoint
(the same endpoint chat itself goes through, via the `openai_completions`
adapter). Unlike vLLM's plain response, llama.cpp nests useful metadata under
`meta` per entry — `n_ctx_train` gives the model's trained/max context length
directly, so no second endpoint call is needed.

Mirrors `tau.inference.model.local.vllm`: intended to run once, in the
background, at process startup (see
`Runtime._start_local_model_discovery`). Best-effort throughout — any failure
(llama.cpp not installed/running, network error, malformed response) results
in an empty list rather than an exception, since a local llama.cpp install is
always optional.
"""

from __future__ import annotations

import logging
from typing import Any

from tau.inference.model.types import Cost, Model, Modality

_log = logging.getLogger(__name__)

_MODELS_TIMEOUT = 3.0
_DEFAULT_MAX_OUTPUT_TOKENS = 4096


def _build_model(item: dict[str, Any]) -> Model:
    model_id = item["id"]
    meta = item.get("meta") or {}
    context_window = meta.get("n_ctx_train") or 0
    return Model(
        id=model_id,
        name=model_id,
        provider="llamacpp",
        cost=Cost(),
        context_window=context_window,
        max_output_tokens=context_window or _DEFAULT_MAX_OUTPUT_TOKENS,
        input=[Modality.Text],
        output=[Modality.Text],
    )


async def discover_local_llamacpp_models(base_url: str = "http://localhost:8080") -> list[Model]:
    """Return Model descriptors for whatever model a local llama.cpp server is serving.

    `base_url` is the llama.cpp host root (no `/v1` suffix) — e.g.
    `http://localhost:8080`, llama-server's documented default port.
    """
    import httpx

    from tau.utils.ssl_context import get_shared_ssl_context

    try:
        async with httpx.AsyncClient(verify=get_shared_ssl_context()) as client:
            resp = await client.get(f"{base_url}/v1/models", timeout=_MODELS_TIMEOUT)
            resp.raise_for_status()
            items = resp.json().get("data") or []
    except Exception:
        return []

    return [_build_model(item) for item in items if item.get("id")]


async def register_local_llamacpp_models(base_url: str | None = None) -> int:
    """Discover models served by a local llama.cpp instance and register them
    into the global text model registry.

    Returns the number of models registered (0 on any failure, including no
    local llama.cpp server being reachable). Safe to call as a fire-and-forget task.
    """
    from tau.inference.api.text.service import TextLLM

    resolved_base_url = base_url
    if resolved_base_url is None:
        provider = TextLLM._builtin_providers().get("llamacpp")
        configured = getattr(getattr(provider, "options", None), "base_url", None)
        resolved_base_url = configured if isinstance(configured, str) else "http://localhost:8080"
        # The registered provider's base_url points at the OpenAI-compat /v1
        # path used for chat; discovery hits the same path directly, so just
        # strip it back to the host root for a consistent base_url shape
        # across all local/*.py modules.
        resolved_base_url = resolved_base_url.removesuffix("/v1").removesuffix("/v1/")

    try:
        models = await discover_local_llamacpp_models(resolved_base_url)
    except Exception:
        _log.debug("Local llama.cpp model discovery failed", exc_info=True)
        return 0

    registry = TextLLM._builtin_models()
    for model in models:
        registry.register(model)
    return len(models)

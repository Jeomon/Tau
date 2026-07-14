"""Discovery of models served by a local vLLM instance.

Unlike Ollama/LM Studio, vLLM has no separate catalog API — a running vLLM
server (`vllm serve <model>`) exposes exactly the model(s) it was launched
with via the standard OpenAI-compatible `GET /v1/models` endpoint (the same
endpoint chat itself goes through, via the `openai_completions` adapter).
vLLM adds a non-standard `max_model_len` field to each entry, which this
module uses for context window; LoRA adapters loaded alongside the base model
show up as additional entries and are treated as ordinary chat models.

Mirrors `tau.inference.model.local.ollama`/`.lmstudio`: intended to run once,
in the background, at process startup (see
`Runtime._start_local_model_discovery`). Best-effort throughout — any failure
(vLLM not installed/running, network error, malformed response) results in an
empty list rather than an exception, since a local vLLM install is always
optional.
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
    context_window = item.get("max_model_len") or 0
    return Model(
        id=model_id,
        name=model_id,
        provider="vllm",
        cost=Cost(),
        context_window=context_window,
        max_output_tokens=context_window or _DEFAULT_MAX_OUTPUT_TOKENS,
        input=[Modality.Text],
        output=[Modality.Text],
    )


async def discover_local_vllm_models(base_url: str = "http://localhost:8000") -> list[Model]:
    """Return Model descriptors for whatever model(s) a local vLLM server is serving.

    `base_url` is the vLLM host root (no `/v1` suffix) — e.g.
    `http://localhost:8000`, vLLM's documented default port.
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


async def register_local_vllm_models(base_url: str | None = None) -> int:
    """Discover models served by a local vLLM instance and register them into
    the global text model registry.

    Returns the number of models registered (0 on any failure, including no
    local vLLM server being reachable). Safe to call as a fire-and-forget task.
    """
    from tau.inference.api.text.service import TextLLM

    resolved_base_url = base_url
    if resolved_base_url is None:
        provider = TextLLM._builtin_providers().get("vllm")
        configured = getattr(getattr(provider, "options", None), "base_url", None)
        resolved_base_url = configured if isinstance(configured, str) else "http://localhost:8000"
        # The registered provider's base_url points at the OpenAI-compat /v1
        # path used for chat; discovery hits the same path directly, so just
        # strip it back to the host root for a consistent base_url shape
        # across all local/*.py modules.
        resolved_base_url = resolved_base_url.removesuffix("/v1").removesuffix("/v1/")

    try:
        models = await discover_local_vllm_models(resolved_base_url)
    except Exception:
        _log.debug("Local vLLM model discovery failed", exc_info=True)
        return 0

    registry = TextLLM._builtin_models()
    for model in models:
        registry.register(model)
    return len(models)

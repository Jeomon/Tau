"""Discovery of models installed in a local LM Studio instance.

LM Studio's native REST API (`/api/v0/models`, served at the host root — not
under the `/v1` OpenAI-compat prefix used for chat) lists every model the app
knows about, including embedding models that aren't usable for chat, plus
context length and load state. Chat itself goes through the standard
OpenAI-compatible `openai_completions` adapter against LM Studio's `/v1`
endpoint; this module only handles catalog discovery.

Mirrors `tau.inference.model.local.ollama`: intended to run once, in the
background, at process startup (see `Runtime._start_lmstudio_discovery`).
Best-effort throughout — any failure (LM Studio not installed/running,
network error, malformed response) results in an empty list rather than an
exception, since a local LM Studio install is always optional.
"""

from __future__ import annotations

import logging
from typing import Any

from tau.inference.model.types import Cost, Model, Modality

_log = logging.getLogger(__name__)

_MODELS_TIMEOUT = 3.0
_DEFAULT_MAX_OUTPUT_TOKENS = 4096
# "embeddings" (and any other non-chat type LM Studio reports) is excluded —
# it isn't usable through the chat completions endpoint.
_CHAT_MODEL_TYPES = {"llm", "vlm"}


def _build_model(item: dict[str, Any]) -> Model:
    model_id = item["id"]
    context_window = item.get("max_context_length") or item.get("loaded_context_length") or 0
    is_vision = item.get("type") == "vlm" or bool(item.get("vision"))
    input_modalities = [Modality.Text, Modality.Image] if is_vision else [Modality.Text]
    return Model(
        id=model_id,
        name=model_id,
        provider="lmstudio",
        cost=Cost(),
        context_window=context_window,
        max_output_tokens=context_window or _DEFAULT_MAX_OUTPUT_TOKENS,
        input=input_modalities,
        output=[Modality.Text],
    )


async def discover_local_lmstudio_models(base_url: str = "http://localhost:1234") -> list[Model]:
    """Return Model descriptors for chat-capable models known to a local LM Studio instance.

    `base_url` is the LM Studio host root (no `/v1` suffix) — e.g.
    `http://localhost:1234`, matching where `/api/v0/models` is actually served.
    """
    import httpx

    from tau.utils.ssl_context import get_shared_ssl_context

    try:
        async with httpx.AsyncClient(verify=get_shared_ssl_context()) as client:
            resp = await client.get(f"{base_url}/api/v0/models", timeout=_MODELS_TIMEOUT)
            resp.raise_for_status()
            items = resp.json().get("data") or []
    except Exception:
        return []

    return [
        _build_model(item)
        for item in items
        if item.get("id") and item.get("type") in _CHAT_MODEL_TYPES
    ]


async def register_local_lmstudio_models(base_url: str | None = None) -> int:
    """Discover local LM Studio models and register them into the global text model registry.

    Returns the number of models registered (0 on any failure, including no
    local LM Studio server being reachable). Safe to call as a fire-and-forget task.
    """
    from tau.inference.api.text.service import TextLLM

    resolved_base_url = base_url
    if resolved_base_url is None:
        provider = TextLLM._builtin_providers().get("lmstudio")
        configured = getattr(getattr(provider, "options", None), "base_url", None)
        resolved_base_url = configured if isinstance(configured, str) else "http://localhost:1234"
        # The registered provider's base_url points at the OpenAI-compat /v1
        # path used for chat; the discovery API lives at the host root.
        resolved_base_url = resolved_base_url.removesuffix("/v1").removesuffix("/v1/")

    try:
        models = await discover_local_lmstudio_models(resolved_base_url)
    except Exception:
        _log.debug("Local LM Studio model discovery failed", exc_info=True)
        return 0

    registry = TextLLM._builtin_models()
    for model in models:
        registry.register(model)
    return len(models)

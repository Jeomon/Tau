"""Discovery of models installed on a local Ollama daemon.

`/api/tags` on a local Ollama server returns both genuinely local models and
cloud-linked tags (pulled via `ollama pull <model>` against Ollama Cloud) —
the latter are already covered by the static catalog in
`tau.builtins.models.text`, so this module filters them out via the
`remote_host` field the raw HTTP API exposes (the `ollama` SDK's typed
response silently drops that field, hence using httpx directly here).

Intended to run once, in the background, at process startup — see
`Runtime._start_ollama_discovery`. Best-effort throughout: any failure
(daemon not installed/running, network error, malformed response) results in
an empty list rather than an exception, since a local Ollama install is
always optional.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tau.inference.model.types import Cost, Modality, Model

_log = logging.getLogger(__name__)

_TAGS_TIMEOUT = 3.0
_SHOW_TIMEOUT = 3.0
_DEFAULT_MAX_OUTPUT_TOKENS = 4096


async def _fetch_tags(client: Any, base_url: str) -> list[dict[str, Any]]:
    try:
        resp = await client.get(f"{base_url}/api/tags", timeout=_TAGS_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("models") or []
    except Exception:
        return []


async def _fetch_show(client: Any, base_url: str, name: str) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"{base_url}/api/show", json={"model": name}, timeout=_SHOW_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _context_length(model_info: dict[str, Any]) -> int:
    """Model-info keys are namespaced by architecture (e.g. "qwen3vl.context_length");
    the prefix varies per model family, so scan for the suffix instead of a fixed key."""
    for key, value in model_info.items():
        if key.endswith(".context_length") and isinstance(value, int):
            return value
    return 0


def _build_model(name: str, show: dict[str, Any]) -> Model:
    capabilities = show.get("capabilities") or []
    model_info = show.get("model_info") or {}
    context_window = _context_length(model_info)
    input_modalities = [Modality.Text]
    if "vision" in capabilities:
        input_modalities.append(Modality.Image)
    return Model(
        id=name,
        name=name,
        provider="ollama",
        cost=Cost(),
        thinking="thinking" in capabilities,
        context_window=context_window,
        max_output_tokens=context_window or _DEFAULT_MAX_OUTPUT_TOKENS,
        input=input_modalities,
        output=[Modality.Text],
    )


async def discover_local_ollama_models(base_url: str = "http://localhost:11434") -> list[Model]:
    """Return Model descriptors for models installed on the local Ollama daemon.

    Excludes cloud-linked tags (`remote_host` set) since those duplicate the
    static Ollama Cloud catalog already registered under the same provider.
    """
    import httpx

    from tau.utils.ssl_context import get_shared_ssl_context

    async with httpx.AsyncClient(verify=get_shared_ssl_context()) as client:
        tags = await _fetch_tags(client, base_url)
        local_tags = [t for t in tags if not t.get("remote_host") and t.get("name")]
        if not local_tags:
            return []

        shows = await asyncio.gather(
            *(_fetch_show(client, base_url, t["name"]) for t in local_tags),
            return_exceptions=True,
        )

    models: list[Model] = []
    for tag, show in zip(local_tags, shows, strict=True):
        models.append(_build_model(tag["name"], show if isinstance(show, dict) else {}))
    return models


async def register_local_ollama_models(base_url: str | None = None) -> int:
    """Discover local Ollama models and register them into the global text model registry.

    Returns the number of models registered (0 on any failure, including no
    local daemon being reachable). Safe to call as a fire-and-forget task.
    """
    from tau.inference.api.text.service import TextLLM

    resolved_base_url = base_url
    if resolved_base_url is None:
        provider = TextLLM._builtin_providers().get("ollama")
        configured = getattr(getattr(provider, "options", None), "base_url", None)
        resolved_base_url = configured if isinstance(configured, str) else "http://localhost:11434"

    try:
        models = await discover_local_ollama_models(resolved_base_url)
    except Exception:
        _log.debug("Local Ollama model discovery failed", exc_info=True)
        return 0

    registry = TextLLM._builtin_models()
    for model in models:
        registry.register(model)
    return len(models)

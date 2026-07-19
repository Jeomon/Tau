"""Tests for local inference-backend model discovery (tau/inference/model/local/).

Covers Ollama tag filtering/context extraction, LM Studio model-type
filtering, vLLM's and llama.cpp's single-endpoint discovery, registry
population, and the unified parallel `register_all()` entry point used by
`Runtime._start_local_model_discovery`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from tau.inference.api.text.service import TextLLM
from tau.inference.model.local import register_all
from tau.inference.model.local.llamacpp import (
    _build_model as _llamacpp_build_model,
)
from tau.inference.model.local.llamacpp import (
    discover_local_llamacpp_models,
    register_local_llamacpp_models,
)
from tau.inference.model.local.lmstudio import (
    _build_model as _lmstudio_build_model,
)
from tau.inference.model.local.lmstudio import (
    discover_local_lmstudio_models,
    register_local_lmstudio_models,
)
from tau.inference.model.local.ollama import (
    _build_model as _ollama_build_model,
)
from tau.inference.model.local.ollama import (
    _context_length,
    discover_local_ollama_models,
)
from tau.inference.model.local.vllm import (
    _build_model as _vllm_build_model,
)
from tau.inference.model.local.vllm import (
    discover_local_vllm_models,
    register_local_vllm_models,
)
from tau.inference.model.registry import ModelRegistry
from tau.inference.model.types import Modality

_OLLAMA_URL = "http://localhost:11434"
_LMSTUDIO_URL = "http://localhost:1234"
_VLLM_URL = "http://localhost:8000"
_LLAMACPP_URL = "http://localhost:8080"

_OLLAMA_TAGS = {
    "models": [
        {"name": "qwen3-vl:4b", "model": "qwen3-vl:4b"},  # genuinely local
        {
            "name": "gpt-oss:120b-cloud",
            "model": "gpt-oss:120b-cloud",
            "remote_model": "gpt-oss:120b",
            "remote_host": "https://ollama.com:443",
        },
    ]
}
_OLLAMA_SHOWS: dict[str, dict[str, Any]] = {
    "qwen3-vl:4b": {
        "capabilities": ["completion", "vision", "tools", "thinking"],
        "model_info": {"qwen3vl.context_length": 262144},
    },
}

_LMSTUDIO_MODELS = {
    "data": [
        {"id": "qwen2.5-7b-instruct", "type": "llm", "max_context_length": 32768},
        {"id": "some-vlm", "type": "vlm", "max_context_length": 8192},
        {"id": "text-embedding-nomic", "type": "embeddings", "max_context_length": 2048},
    ]
}

_VLLM_MODELS = {
    "object": "list",
    "data": [
        {
            "id": "meta-llama/Llama-3.1-8B-Instruct",
            "object": "model",
            "owned_by": "vllm",
            "max_model_len": 131072,
        }
    ],
}

_LLAMACPP_MODELS = {
    "object": "list",
    "data": [
        {
            "id": "nvidia/nemotron-3-nano-4b",
            "object": "model",
            "owned_by": "llamacpp",
            "meta": {"n_ctx_train": 1048576, "n_params": 4000000000},
        }
    ],
}


def _ollama_transport(
    tags: dict[str, Any] | None = _OLLAMA_TAGS,
    shows: dict[str, dict[str, Any]] | None = None,
) -> httpx.MockTransport:
    show_map = shows if shows is not None else _OLLAMA_SHOWS

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags)
        if request.url.path == "/api/show":
            import json

            name = json.loads(request.content)["model"]
            return httpx.Response(200, json=show_map.get(name, {}))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _lmstudio_transport(models: dict[str, Any] | None = _LMSTUDIO_MODELS) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=models)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _vllm_transport(models: dict[str, Any] | None = _VLLM_MODELS) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=models)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _llamacpp_transport(models: dict[str, Any] | None = _LLAMACPP_MODELS) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=models)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **kw: real_async_client(*a, transport=transport, **kw)
    )


# ── Ollama ────────────────────────────────────────────────────────────────


def test_ollama_context_length_scans_namespaced_key() -> None:
    assert _context_length({"qwen3vl.context_length": 262144}) == 262144
    assert _context_length({"gptoss.context_length": 131072, "other": "x"}) == 131072
    assert _context_length({}) == 0


def test_ollama_build_model_sets_vision_and_thinking() -> None:
    model = _ollama_build_model(
        "qwen3-vl:4b",
        {"capabilities": ["vision", "thinking"], "model_info": {"qwen3vl.context_length": 262144}},
    )
    assert model.provider == "ollama"
    assert model.thinking is True
    assert model.input == [Modality.Text, Modality.Image]


def test_ollama_discover_excludes_cloud_linked_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch, _ollama_transport())

    models = asyncio.run(discover_local_ollama_models(_OLLAMA_URL))

    assert [m.id for m in models] == ["qwen3-vl:4b"]
    assert models[0].context_window == 262144


def test_ollama_discover_is_silent_when_daemon_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    assert asyncio.run(discover_local_ollama_models(_OLLAMA_URL)) == []


# ── LM Studio ─────────────────────────────────────────────────────────────


def test_lmstudio_build_model_marks_vlm_as_vision() -> None:
    model = _lmstudio_build_model({"id": "some-vlm", "type": "vlm", "max_context_length": 8192})
    assert model.provider == "lmstudio"
    assert model.input == [Modality.Text, Modality.Image]
    assert model.context_window == 8192


def test_lmstudio_discover_excludes_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch, _lmstudio_transport())

    models = asyncio.run(discover_local_lmstudio_models(_LMSTUDIO_URL))

    assert {m.id for m in models} == {"qwen2.5-7b-instruct", "some-vlm"}


def test_lmstudio_discover_is_silent_when_server_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    assert asyncio.run(discover_local_lmstudio_models(_LMSTUDIO_URL)) == []


def test_register_local_lmstudio_models_strips_v1_suffix_from_provider_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registered provider's base_url is .../v1 (for chat); discovery must
    hit the host root, not .../v1/api/v0/models."""
    monkeypatch.setattr(TextLLM, "_models", ModelRegistry())
    seen_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(str(request.url))
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=_LMSTUDIO_MODELS)
        return httpx.Response(404)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    count = asyncio.run(register_local_lmstudio_models())

    assert count == 2
    assert all(url.startswith("http://localhost:1234/api/v0/models") for url in seen_paths)


# ── vLLM ──────────────────────────────────────────────────────────────────


def test_vllm_build_model_uses_max_model_len_for_context_window() -> None:
    model = _vllm_build_model(
        {"id": "meta-llama/Llama-3.1-8B-Instruct", "max_model_len": 131072}
    )
    assert model.provider == "vllm"
    assert model.context_window == 131072
    assert model.input == [Modality.Text]


def test_vllm_build_model_defaults_when_max_model_len_missing() -> None:
    model = _vllm_build_model({"id": "some-model"})
    assert model.context_window == 0
    assert model.max_output_tokens == 4096


def test_vllm_discover_returns_served_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch, _vllm_transport())

    models = asyncio.run(discover_local_vllm_models(_VLLM_URL))

    assert [m.id for m in models] == ["meta-llama/Llama-3.1-8B-Instruct"]
    assert models[0].context_window == 131072


def test_vllm_discover_is_silent_when_server_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    assert asyncio.run(discover_local_vllm_models(_VLLM_URL)) == []


def test_register_local_vllm_models_strips_v1_suffix_from_provider_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TextLLM, "_models", ModelRegistry())
    seen_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(str(request.url))
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=_VLLM_MODELS)
        return httpx.Response(404)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    count = asyncio.run(register_local_vllm_models())

    assert count == 1
    assert all(url.startswith("http://localhost:8000/v1/models") for url in seen_paths)


# ── llama.cpp ─────────────────────────────────────────────────────────────


def test_llamacpp_build_model_uses_meta_n_ctx_train_for_context_window() -> None:
    model = _llamacpp_build_model(
        {"id": "nvidia/nemotron-3-nano-4b", "meta": {"n_ctx_train": 1048576}}
    )
    assert model.provider == "llamacpp"
    assert model.context_window == 1048576
    assert model.input == [Modality.Text]


def test_llamacpp_build_model_defaults_when_meta_missing() -> None:
    model = _llamacpp_build_model({"id": "some-model"})
    assert model.context_window == 0
    assert model.max_output_tokens == 4096


def test_llamacpp_discover_returns_served_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch, _llamacpp_transport())

    models = asyncio.run(discover_local_llamacpp_models(_LLAMACPP_URL))

    assert [m.id for m in models] == ["nvidia/nemotron-3-nano-4b"]
    assert models[0].context_window == 1048576


def test_llamacpp_discover_is_silent_when_server_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    assert asyncio.run(discover_local_llamacpp_models(_LLAMACPP_URL)) == []


def test_register_local_llamacpp_models_strips_v1_suffix_from_provider_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TextLLM, "_models", ModelRegistry())
    seen_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(str(request.url))
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=_LLAMACPP_MODELS)
        return httpx.Response(404)

    _patch_httpx(monkeypatch, httpx.MockTransport(handler))

    count = asyncio.run(register_local_llamacpp_models())

    assert count == 1
    assert all(url.startswith("http://localhost:8080/v1/models") for url in seen_paths)


# ── Unified parallel entry point ─────────────────────────────────────────


def test_register_all_runs_backends_in_parallel_and_populates_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TextLLM, "_models", ModelRegistry())

    async def ollama_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=_OLLAMA_TAGS)
        if request.url.path == "/api/show":
            import json

            name = json.loads(request.content)["model"]
            return httpx.Response(200, json=_OLLAMA_SHOWS.get(name, {}))
        return httpx.Response(404)

    async def lmstudio_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=_LMSTUDIO_MODELS)
        return httpx.Response(404)

    async def vllm_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=_VLLM_MODELS)
        return httpx.Response(404)

    async def llamacpp_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=_LLAMACPP_MODELS)
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient

    async def router(request: httpx.Request) -> httpx.Response:
        if request.url.host == "localhost" and request.url.port == 11434:
            return await ollama_handler(request)
        if request.url.host == "localhost" and request.url.port == 1234:
            return await lmstudio_handler(request)
        if request.url.host == "localhost" and request.url.port == 8000:
            return await vllm_handler(request)
        if request.url.host == "localhost" and request.url.port == 8080:
            return await llamacpp_handler(request)
        return httpx.Response(404)

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        # All backends construct a plain httpx.AsyncClient() with no
        # base_url and pass the full URL per-request, so route by the
        # request's own target host/port at call time instead.
        return real_async_client(*args, transport=httpx.MockTransport(router), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    total = asyncio.run(register_all())

    # 1 ollama + 2 lmstudio (chat-capable) + 1 vllm + 1 llamacpp
    assert total == 5
    registry = TextLLM._builtin_models()
    assert registry.get("qwen3-vl:4b", provider="ollama") is not None
    assert registry.get("qwen2.5-7b-instruct", provider="lmstudio") is not None
    assert registry.get("some-vlm", provider="lmstudio") is not None
    assert registry.get("text-embedding-nomic", provider="lmstudio") is None
    assert registry.get("meta-llama/Llama-3.1-8B-Instruct", provider="vllm") is not None
    assert registry.get("nvidia/nemotron-3-nano-4b", provider="llamacpp") is not None


def test_register_all_tolerates_one_backend_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama unreachable shouldn't stop LM Studio's models from being registered."""
    monkeypatch.setattr(TextLLM, "_models", ModelRegistry())

    async def router(request: httpx.Request) -> httpx.Response:
        if request.url.port == 11434:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=_LMSTUDIO_MODELS)
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(*args, transport=httpx.MockTransport(router), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    total = asyncio.run(register_all())

    assert total == 2
    registry = TextLLM._builtin_models()
    assert registry.get("qwen2.5-7b-instruct", provider="lmstudio") is not None

"""Dynamic model catalog backed by models.dev.

``Catalog`` wraps the community catalog at https://models.dev/api.json: it
fetches the payload, caches it on disk, and exposes the entries as tau
``Model`` descriptors — chat models plus image/video generation, TTS, and STT
for providers whose tau adapters are wired for that modality. Standalone data
access only: how (and whether) callers integrate these models into tau's
registries is up to them.

The cache lives at ``~/.tau/models-catalog.json`` (trimmed to the providers
tau maps — the full payload is ~3.5 MB, ~88% of it unmappable providers).
``is_stale`` implements the refresh throttle (``_MIN_REFRESH_INTERVAL_S``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tau.inference.model.types import Cost, Modality, Model
from tau.inference.types import ThinkingLevel

CATALOG_URL = "https://models.dev/api.json"

_FETCH_TIMEOUT_S = 15.0
_MIN_REFRESH_INTERVAL_S = 4 * 60 * 60  # match pi: at most one refresh per 4 hours

# tau provider id → models.dev provider id. Only providers whose model ids are
# passed through to the API verbatim are listed; providers with curated or
# transformed id schemes (bedrock ARNs, vertex publishers, codex/copilot OAuth
# catalogs, local runtimes) are deliberately absent.
PROVIDER_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "mistral": "mistral",
    "xai": "xai",
    "groq": "groq",
    "deepseek": "deepseek",
    "fireworks": "fireworks-ai",
    "cerebras": "cerebras",
    "openrouter": "openrouter",
    "perplexity": "perplexity",
    "nvidia": "nvidia",
    "huggingface": "huggingface",
    "minimax": "minimax",
    "zai": "zai",
    "kimi": "moonshotai",
    "subconscious": "subconscious",
}

# Per-modality provider maps for the non-text registries: only providers with
# a wired tau adapter for that modality (see tau/builtins/providers/
# {image,audio,video}.py). Catalog models leave ``Model.api`` unset — the
# service resolves ``model.api or provider.api``, so they inherit the
# provider's default adapter. fal (video) and elevenlabs (audio) have no
# models.dev entry; sarvam/zai/fireworks/together currently list no media
# models there but are mapped so future entries appear without a code change.
IMAGE_PROVIDER_MAP: dict[str, str] = {
    "openai": "openai",
    "google": "google",
    "openrouter": "openrouter",
    "together": "togetherai",
    "fireworks": "fireworks-ai",
    "zai": "zai",
}
VIDEO_PROVIDER_MAP: dict[str, str] = {
    "openrouter": "openrouter",
    "zai": "zai",
}
AUDIO_PROVIDER_MAP: dict[str, str] = {
    "openai": "openai",
    "openrouter": "openrouter",
    "groq": "groq",
    "google": "google",
    "sarvam": "sarvam",
    "zai": "zai",
}


def _all_dev_ids() -> set[str]:
    return (
        set(PROVIDER_MAP.values())
        | set(IMAGE_PROVIDER_MAP.values())
        | set(VIDEO_PROVIDER_MAP.values())
        | set(AUDIO_PROVIDER_MAP.values())
    )

# Input modalities each tau adapter actually wires beyond text+image — mirrors
# the constraints documented in tau/builtins/models/text.py. The catalog's
# declared modalities are intersected with this set so a dynamic model can't
# advertise content types its adapter would reject.
_BASE_INPUT = {Modality.Text, Modality.Image}
_EXTRA_INPUT: dict[str, set[Modality]] = {
    "anthropic": {Modality.File},
    "google": {Modality.File, Modality.Audio, Modality.Video},
    "openrouter": {Modality.Audio},
}

_MODALITY_BY_NAME = {
    "text": Modality.Text,
    "image": Modality.Image,
    "pdf": Modality.File,
    "audio": Modality.Audio,
    "video": Modality.Video,
}


class Catalog:
    """Fetches, caches, and queries the models.dev catalog."""

    def __init__(self, path: Path | None = None, url: str = CATALOG_URL) -> None:
        if path is None:
            from tau.settings.paths import CONFIG_DIR_PATH

            path = CONFIG_DIR_PATH / "models-catalog.json"
        self.path = path
        self.url = url
        self._data: dict[str, Any] | None = None
        self._fetched_at: float | None = None

    # ── Cache ──────────────────────────────────────────────────────────────────

    @property
    def data(self) -> dict[str, Any] | None:
        """The raw (trimmed) models.dev payload, or None until load()/fetch()."""
        return self._data

    @property
    def fetched_at(self) -> float | None:
        """Unix timestamp of the last successful fetch, or None."""
        return self._fetched_at

    def load(self) -> bool:
        """Load the on-disk cache into memory. Returns True when usable."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
            return False
        fetched_at = raw.get("fetched_at")
        self._data = raw["data"]
        self._fetched_at = float(fetched_at) if isinstance(fetched_at, (int, float)) else None
        return True

    def save(self) -> None:
        """Persist the in-memory payload to the cache file (atomic)."""
        if self._data is None:
            return
        from tau.utils.fs import atomic_write_text

        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path, json.dumps({"fetched_at": self._fetched_at, "data": self._data})
        )

    def is_stale(self, now: float | None = None) -> bool:
        """True when there is no usable payload or it's older than the interval."""
        if self._data is None or self._fetched_at is None:
            return True
        current = time.time() if now is None else now
        return (current - self._fetched_at) >= _MIN_REFRESH_INTERVAL_S

    # ── Fetch ──────────────────────────────────────────────────────────────────

    def fetch(self) -> None:
        """Blocking GET of the catalog; trims, saves, and keeps it in memory.

        Run off the event loop. Network/parse errors propagate to the caller.
        """
        import httpx

        from tau.utils.ssl_context import get_shared_ssl_context

        with httpx.Client(timeout=_FETCH_TIMEOUT_S, verify=get_shared_ssl_context()) as client:
            resp = client.get(self.url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError(f"unexpected catalog payload: {type(payload).__name__}")
        # Keep only providers the overlay can use — the full payload is ~3.5 MB
        # and the cache is re-parsed at every startup.
        mapped = _all_dev_ids()
        self._data = {k: v for k, v in payload.items() if k in mapped}
        self._fetched_at = time.time()
        self.save()

    # ── Queries ────────────────────────────────────────────────────────────────

    def _models_for(
        self,
        provider_map: dict[str, str],
        mapper,
        provider: str | None,
    ) -> list[Model]:
        """Shared iteration: map entries via ``mapper`` for mapped providers.

        Skips deprecated entries (in the mappers), date-suffixed aliases whose
        base id exists, malformed entries, and providers outside the map.
        """
        if self._data is None:
            return []
        if provider is None:
            providers = provider_map
        elif provider in provider_map:
            providers = {provider: provider_map[provider]}
        else:
            return []
        out: list[Model] = []
        for tau_provider, dev_provider in providers.items():
            entry = self._data.get(dev_provider)
            models = entry.get("models") if isinstance(entry, dict) else None
            if not isinstance(models, dict):
                continue
            for model_id, raw in models.items():
                if not isinstance(raw, dict) or _is_dated_duplicate(model_id, models):
                    continue
                model = mapper(tau_provider, raw)
                if model is not None:
                    out.append(model)
        return out

    def text_models(self, provider: str | None = None) -> list[Model]:
        """Return chat models as tau ``Model``s, optionally for one tau provider."""
        return self._models_for(PROVIDER_MAP, _to_text_model, provider)

    def image_models(self, provider: str | None = None) -> list[Model]:
        """Return image-generation models for providers with a wired image adapter."""
        return self._models_for(IMAGE_PROVIDER_MAP, _to_image_model, provider)

    def video_models(self, provider: str | None = None) -> list[Model]:
        """Return video-generation models for providers with a wired video adapter."""
        return self._models_for(VIDEO_PROVIDER_MAP, _to_video_model, provider)

    def audio_models(self, provider: str | None = None) -> list[Model]:
        """Return TTS and STT models for providers with a wired audio adapter.

        TTS entries map to ``input=[Text], output=[Audio]`` (``is_tts``) and
        STT to ``input=[Audio], output=[Text]`` (``is_stt``). Audio-input CHAT
        models (e.g. Gemini) are excluded — they belong to the text registry.
        Catalog TTS models carry no voice list (models.dev has none), so the
        voice picker simply doesn't open for them.
        """
        return self._models_for(AUDIO_PROVIDER_MAP, _to_audio_model, provider)

    def _get_model(
        self,
        provider_map: dict[str, str],
        mapper,
        model_id: str,
        provider: str,
    ) -> Model | None:
        """Shared single-model lookup: map one entry via ``mapper``, or None."""
        if self._data is None or provider not in provider_map:
            return None
        entry = self._data.get(provider_map[provider])
        models = entry.get("models") if isinstance(entry, dict) else None
        raw = models.get(model_id) if isinstance(models, dict) else None
        if not isinstance(raw, dict):
            return None
        return mapper(provider, raw)

    def get_text_model(self, model_id: str, provider: str) -> Model | None:
        """Return one chat model by tau provider and id, or None."""
        return self._get_model(PROVIDER_MAP, _to_text_model, model_id, provider)

    def get_image_model(self, model_id: str, provider: str) -> Model | None:
        """Return one image-generation model by tau provider and id, or None."""
        return self._get_model(IMAGE_PROVIDER_MAP, _to_image_model, model_id, provider)

    def get_video_model(self, model_id: str, provider: str) -> Model | None:
        """Return one video-generation model by tau provider and id, or None."""
        return self._get_model(VIDEO_PROVIDER_MAP, _to_video_model, model_id, provider)

    def get_audio_model(self, model_id: str, provider: str) -> Model | None:
        """Return one TTS/STT model by tau provider and id, or None."""
        return self._get_model(AUDIO_PROVIDER_MAP, _to_audio_model, model_id, provider)


# ── Mapping ────────────────────────────────────────────────────────────────────


def _to_text_model(tau_provider: str, entry: dict[str, Any]) -> Model | None:
    """Map one models.dev entry onto a tau text ``Model``, or None if unusable."""
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    if entry.get("status") == "deprecated":
        return None

    modalities = entry.get("modalities") or {}
    # Chat models only. Image/video generators declare text *input* (the
    # prompt) and models.dev marks many as also emitting text (output:
    # ["text", "image"]) — but every model in the text registry has text-only
    # output and the text adapters can't handle media parts in responses.
    if set(modalities.get("output") or ["text"]) != {"text"}:
        return None

    allowed = _BASE_INPUT | _EXTRA_INPUT.get(tau_provider, set())
    inputs = [
        _MODALITY_BY_NAME[name]
        for name in modalities.get("input", ["text"])
        if _MODALITY_BY_NAME.get(name) in allowed
    ]
    if Modality.Text not in inputs:
        return None

    cost_raw = entry.get("cost") or {}
    limit = entry.get("limit") or {}

    def _price(key: str) -> float:
        v = cost_raw.get(key)
        return float(v) if isinstance(v, (int, float)) else 0.0

    def _limit(key: str, default: int) -> int:
        v = limit.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else default

    context_window = _limit("context", 0)
    max_input = _limit("input", 0)

    return Model(
        id=model_id,
        name=entry.get("name") or model_id,
        provider=tau_provider,
        cost=Cost(
            input=_price("input"),
            output=_price("output"),
            cache_read=_price("cache_read"),
            cache_write=_price("cache_write"),
        ),
        thinking=bool(entry.get("reasoning")),
        thinking_levels=_thinking_levels(entry),
        context_window=context_window,
        # limit.input is the prompt ceiling (compaction keys off it); only
        # meaningful when it's a real bound below the total window.
        max_input_tokens=max_input if 0 < max_input < context_window else None,
        max_output_tokens=_limit("output", 16384),
        input=inputs,
        output=[Modality.Text],
    )


def _thinking_levels(entry: dict[str, Any]) -> list[ThinkingLevel]:
    """Map ``reasoning_options`` effort values onto tau's enum when clean.

    Only an ``effort`` option whose values ALL parse as ThinkingLevel is
    trusted (with Off prepended, matching builtin convention so the picker
    offers disabling thinking). ``toggle``/``budget_tokens`` options and any
    unrecognised value yield an empty list — per Model's contract that means
    unconfirmed, and pickers fall back to the full enum.
    """
    if not entry.get("reasoning"):
        return []
    for option in entry.get("reasoning_options") or []:
        if not isinstance(option, dict) or option.get("type") != "effort":
            continue
        values = option.get("values")
        if not isinstance(values, list) or not values:
            return []
        levels: list[ThinkingLevel] = []
        for value in values:
            try:
                levels.append(ThinkingLevel(value))
            except ValueError:
                return []  # unknown level name — don't guess
        if ThinkingLevel.Off not in levels:
            levels.insert(0, ThinkingLevel.Off)
        return levels
    return []


def _media_common(entry: dict[str, Any]) -> tuple[str, dict, dict] | None:
    """Shared validity checks for media mappers: returns (id, modalities, cost)."""
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    if entry.get("status") == "deprecated":
        return None
    # Router meta-models (e.g. OpenRouter's "openrouter/auto") advertise every
    # modality but aren't generation models the media adapters can drive.
    if entry.get("family") == "auto" or model_id.endswith("/auto"):
        return None
    return model_id, entry.get("modalities") or {}, entry.get("cost") or {}


def _media_cost(cost_raw: dict[str, Any]) -> Cost:
    def _price(key: str) -> float:
        v = cost_raw.get(key)
        return float(v) if isinstance(v, (int, float)) else 0.0

    return Cost(
        input=_price("input"),
        output=_price("output"),
        cache_read=_price("cache_read"),
        cache_write=_price("cache_write"),
    )


def _to_image_model(tau_provider: str, entry: dict[str, Any]) -> Model | None:
    """Map an image-generation entry (image in output, text prompt in input)."""
    common = _media_common(entry)
    if common is None:
        return None
    model_id, modalities, cost_raw = common
    inputs_raw = modalities.get("input") or []
    if "image" not in (modalities.get("output") or []) or "text" not in inputs_raw:
        return None
    inputs = [Modality.Text] + ([Modality.Image] if "image" in inputs_raw else [])
    return Model(
        id=model_id,
        name=entry.get("name") or model_id,
        provider=tau_provider,
        cost=_media_cost(cost_raw),
        input=inputs,
        output=[Modality.Image],
    )


def _to_video_model(tau_provider: str, entry: dict[str, Any]) -> Model | None:
    """Map a video-generation entry (video in output, text prompt in input)."""
    common = _media_common(entry)
    if common is None:
        return None
    model_id, modalities, cost_raw = common
    inputs_raw = modalities.get("input") or []
    if "video" not in (modalities.get("output") or []) or "text" not in inputs_raw:
        return None
    inputs = [Modality.Text] + ([Modality.Image] if "image" in inputs_raw else [])
    return Model(
        id=model_id,
        name=entry.get("name") or model_id,
        provider=tau_provider,
        cost=_media_cost(cost_raw),
        input=inputs,
        output=[Modality.Video],
    )


def _to_audio_model(tau_provider: str, entry: dict[str, Any]) -> Model | None:
    """Map a TTS or pure-STT entry; audio-input chat models are excluded.

    TTS: text in input, output is audio-only → ``is_tts``.
    STT: input is audio-only (no text — that would be an audio-capable chat
    model like Gemini, which belongs to the text registry), output text-only
    → ``is_stt``.
    """
    common = _media_common(entry)
    if common is None:
        return None
    model_id, modalities, cost_raw = common
    inputs_raw = set(modalities.get("input") or [])
    outputs_raw = set(modalities.get("output") or [])
    if outputs_raw == {"audio"} and "text" in inputs_raw:
        io = ([Modality.Text], [Modality.Audio])  # TTS
    elif outputs_raw == {"text"} and "audio" in inputs_raw and "text" not in inputs_raw:
        io = ([Modality.Audio], [Modality.Text])  # STT
    else:
        return None
    return Model(
        id=model_id,
        name=entry.get("name") or model_id,
        provider=tau_provider,
        cost=_media_cost(cost_raw),
        input=io[0],
        output=io[1],
    )


def _is_dated_duplicate(model_id: str, provider_models: dict[str, Any]) -> bool:
    """True for date-suffixed aliases (``foo-20250805``) whose base id also exists."""
    base, sep, suffix = model_id.rpartition("-")
    return bool(sep) and len(suffix) == 8 and suffix.isdigit() and base in provider_models


# ── Default instance ───────────────────────────────────────────────────────────

_default: Catalog | None = None


def default_catalog() -> Catalog:
    """Process-wide Catalog instance backed by the standard cache path."""
    global _default
    if _default is None:
        _default = Catalog()
    return _default

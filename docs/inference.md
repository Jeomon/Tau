# Inference

`tau.inference` is Tau's inference subsystem: model and provider registries,
credential resolution, and normalized streaming adapters for text, image,
audio, and video. It is usable entirely on its own — see
[Standalone Usage](#standalone-usage) — or as the model layer under the full
Tau runtime.

Provider setup and credentials are documented in
[Inference Providers](inference-providers.md) and [Authentication](auth.md).

## Table of Contents

- [Responsibilities](#responsibilities)
- [Package Layout](#package-layout)
- [Public Clients](#public-clients)
- [TextLLM](#textllm)
- [Options](#options)
- [Events](#events)
- [Retries and Empty Responses](#retries-and-empty-responses)
- [Thinking and Reasoning](#thinking-and-reasoning)
- [Model Descriptors](#model-descriptors)
- [Dynamic Model Catalog](#dynamic-model-catalog)
- [Other Modalities](#other-modalities)
- [Standalone Usage](#standalone-usage)
- [Extension Boundary](#extension-boundary)

## Responsibilities

The subsystem owns:

- text, image, audio, and video inference adapters
- normalized request, response, and streaming event types
- model and provider descriptors and registries
- lazy API adapter selection (provider SDKs import on first request)
- API-key and OAuth credential integration through `AuthManager`
- option merging, retries, and provider error classification

It does not own conversation orchestration, session persistence, tool
execution, or terminal rendering. Those live in `tau.engine`, `tau.session`,
and `tau.tui`.

## Package Layout

```text
tau/inference/
├── __init__.py            # Client proxies (LLM, ImageLLM, AudioLLM, VideoLLM) + shared types
├── types.py               # Contexts, options, events, stop reasons, ThinkingLevel/Budgets
├── utils.py               # Error classification (ErrorKind) and retry-delay helpers
├── api/
│   ├── availability.py    # available_models(): models whose provider has usable auth
│   ├── registry.py        # LazyAPI — defers SDK import until first request
│   ├── text/              # Streaming text adapters + TextLLM service
│   ├── image/             # Image generation adapters + ImageLLM service
│   ├── audio/             # TTS/STT adapters + AudioLLM service
│   └── video/             # Video generation adapters + VideoLLM service
├── model/
│   ├── types.py           # Model, Cost, Modality
│   ├── registry.py        # ModelRegistry (multi-provider variants per model id)
│   ├── catalog.py         # models.dev dynamic catalog
│   └── local/             # Ollama / LM Studio / vLLM / llama.cpp discovery
└── provider/
    ├── types.py           # APIProvider, OAuthProvider, Image/Audio/VideoProvider
    ├── registry.py        # Per-modality provider registries + unified ProviderRegistry
    └── oauth/             # OAuth flows (Codex, Claude Code, Copilot, Antigravity, Grok)
```

Built-in model and provider definitions live under `tau.builtins.models` and
`tau.builtins.providers`. Registries load those definitions and may also
receive entries from extensions or programmatic configuration.

## Public Clients

| Client | Concrete class | Operation |
|--------|----------------|-----------|
| `LLM` | `tau.inference.api.text.service.TextLLM` | Stream text, thinking, tool-call, usage, and error events |
| `ImageLLM` | `tau.inference.api.image.service.ImageLLM` | Generate images from an `ImageContext` |
| `AudioLLM` | `tau.inference.api.audio.service.AudioLLM` | Synthesize speech or transcribe audio |
| `VideoLLM` | `tau.inference.api.video.service.VideoLLM` | Generate video from a `VideoContext` |

The four names exported from `tau.inference` are thin proxies: instantiating
one delegates to the concrete service class. This keeps
`from tau.inference import LLM` free of circular imports at parse time.

Importing these names does not import any provider SDK. Adapter references are
kept as `"registry-key"` or `"module:ClassName"` strings and resolved on the
first `stream()`/`invoke()` call, on a worker thread so a cold SDK import does
not block the event loop.

Each concrete service exposes `list_available()`, returning models whose
provider has usable authentication — a provider qualifies when its `auth_type`
is `AuthType.None_` (local servers), when a matching credential is stored, or
when `<PROVIDER>_API_KEY` is set in the environment.

## TextLLM

```python
TextLLM(
    model_id: str,
    provider: str | None = None,
    options: LLMOptions | None = None,
    *,
    models: ModelRegistry | None = None,
    providers: TextProviderRegistry | None = None,
    apis: LLMAPIRegistry | None = None,
    auth_manager: AuthManager | None = None,
)
```

The keyword-only registries default to lazily-built process-wide builtins.
Pass your own for full dependency control (tests, embedding hosts).

| Member | Type | Description |
|--------|------|-------------|
| `stream(context)` | `AsyncGenerator[LLMEvent, None]` | Stream events for one turn |
| `invoke(context, thinking_level=None)` | `list[LLMEvent]` | Collect a full turn into a list; `thinking_level` overrides for this call only and is restored afterwards |
| `list_available()` | `classmethod -> list[Model]` | Text models with usable auth |
| `model` | `Model` | The resolved model descriptor |
| `provider_id` | `str` | The resolved provider id |
| `api` | `LazyAPI` | The (still unresolved) adapter; `api.options` is the merged `LLMOptions` |
| `requested_model_id` | `str` | The id as passed in |
| `requested_provider_id` | `str \| None` | The provider as passed in |
| `fallback_reason` | `str \| None` | Set when resolution had to fall back (see below) |

There is no separate `LLM.complete()`; `invoke()` is the non-streaming form and
still returns events, not a message.

### Resolution

1. Collect model variants for `model_id`. With `provider` pinned, only that
   provider's variant; otherwise every registered variant, in registration
   order.
2. If a pinned provider has no variant of this exact id but does have other
   registered models, synthesize one by copying that provider's first model's
   defaults (api, base_url, cost, capabilities) under the requested id. This is
   how custom or brand-new model ids work against a known provider.
   `fallback_reason` records it. A trailing `:<level>` on such an id (e.g.
   `my-model:high`) sets the default thinking level, unless the caller pinned
   `thinking_level` explicitly.
3. Skip OAuth providers with no stored OAuth credential and try the next
   variant. This is what lets `claude-sonnet-4-6` work with an API key even
   when an OAuth variant is registered first. Skipped providers are recorded in
   `fallback_reason`.
4. Merge options: provider defaults → model `base_url` override → caller
   `options`. `max_tokens` falls back to `model.max_output_tokens`.
5. Resolve `$ENV_VAR` / `!command` references in headers (see
   [Authentication](auth.md#credential-references)).
6. Wrap the adapter in `LazyAPI`.

Credentials are fetched per request inside `stream()`/`invoke()`, not at
construction, so a token refreshed between turns is picked up.

## Options

`LLMOptions` is a dataclass that tracks which fields the caller actually set.
Only explicitly-set fields override a provider's base options — passing a bare
`LLMOptions()` never clobbers provider defaults with dataclass defaults.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key` | `str \| None` | `None` | Overwritten per request by `AuthManager` when a credential resolves |
| `base_url` | `str \| None` | `None` | Endpoint override; `None` uses the provider default |
| `headers` | `dict[str, str] \| None` | `None` | Extra request headers; normalized to `{}` and shared live with the HTTP client |
| `max_retries` | `int` | `3` | Retry budget for transient errors and empty responses |
| `retry_base_delay_ms` | `int` | `1000` | Base for exponential backoff (`base * 2**attempt`) |
| `timeout` | `timedelta` | `60s` | Per-request timeout |
| `temperature` | `float` | `1.0` | Sampling temperature |
| `max_tokens` | `int \| None` | `None` | Falls back to `model.max_output_tokens` |
| `transport` | `Transport` | `Transport.HTTP` | Adapters declare `SUPPORTED_TRANSPORTS`; a mismatch raises `ValueError` |
| `thinking_level` | `ThinkingLevel \| None` | `None` | See [Thinking and Reasoning](#thinking-and-reasoning) |
| `thinking_budgets` | `ThinkingBudgets \| None` | `None` | Token budgets for budget-style providers |
| `signal` | `asyncio.Event \| None` | `None` | Abort signal; adapters poll it |
| `extra_params` | `dict[str, Any] \| None` | `None` | Provider-specific params spread into the request body |
| `on_payload` | `Callable[[dict], dict \| None]` | `None` | Inspect or rewrite the outgoing body |
| `on_response` | `Callable[[Any], None]` | `None` | Observe the raw response object |
| `distrust_thought_signatures` | `bool` | `False` | Internal Gemini-family flag; set after a model switch so a signature minted elsewhere is not replayed |

`explicitly_set(name)` reports whether a field was set by the caller, at
construction or by later assignment.

## Events

Every text adapter emits the same `LLMEvent` union, so consumers never need
provider-specific streaming code.

| Event | `LLMEventType` | Payload |
|-------|----------------|---------|
| `StartEvent` | `start` | — (emitted locally, before any HTTP round-trip) |
| `RetryEvent` | `retry` | `attempt`, `max_retries`, `error` |
| `TextStartEvent` | `text_start` | `text: TextContent` |
| `TextDeltaEvent` | `text_delta` | `text: TextContent` (the chunk) |
| `TextEndEvent` | `text_end` | `text: TextContent` (the whole block) |
| `ThinkingStartEvent` | `thinking_start` | `thinking: ThinkingContent \| None` |
| `ThinkingDeltaEvent` | `thinking_delta` | `thinking: ThinkingContent \| None` |
| `ThinkingEndEvent` | `thinking_end` | `thinking: ThinkingContent \| None` (carries `signature` when the provider supports replay) |
| `ToolCallStartEvent` | `tool_call_start` | `tool_call: ToolCallContent \| None` |
| `ToolCallDeltaEvent` | `tool_call_delta` | `tool_call: ToolCallContent \| None` |
| `ToolCallEndEvent` | `tool_call_end` | `tool_call: ToolCallContent \| None` (arguments parsed) |
| `EndEvent` | `end` | `reason`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `input_tokens_include_cache_read` |
| `ErrorEvent` | `error` | `reason: StopReason`, `error: str`, `kind: ErrorKind` |

`StopReason` values: `stop`, `length`, `tool_calls`, `content_filter`, `abort`,
`error`.

> `RetryEvent` is not re-exported from `tau.inference`. Import it from
> `tau.inference.types`.

## Retries and Empty Responses

`stream()` and `invoke()` share one retry policy, driven by
`max_retries` and `retry_base_delay_ms`.

1. Errors are classified by `tau.inference.utils.classify_error` into a
   `ClassifiedError(kind, message, status_code, retryable)`:

   | `ErrorKind` | Typical status | Retryable |
   |-------------|----------------|-----------|
   | `auth` | 401/403 | No |
   | `auth_permanent` | 401/403, unrecoverable | No |
   | `billing` | 402, credits exhausted | No |
   | `rate_limit` | 429 | Yes |
   | `overloaded` | 503/529 | Yes |
   | `server_error` | 500/502 | Yes |
   | `timeout` | connection/read timeout | Yes |
   | `context_overflow` | context too large | Compact, then retry |
   | `model_not_found` | 404 | No |
   | `content_blocked` | safety filter | No |
   | `format_error` | 400 | No |
   | `unknown` | unclassified | Yes |

   A quota-exhausted 429 carrying a multi-day reset window is classified
   non-retryable despite the status code.
2. A retryable error retries with exponential backoff, honoring a provider's
   `Retry-After` when present (`get_retry_after_delay`). Each retry emits a
   `RetryEvent`.
3. In `stream()`, once any non-`Start`/`Retry` event has been yielded, errors
   are no longer retried — already-yielded events cannot be recalled — and are
   forwarded as an `ErrorEvent` instead.
4. **OAuth recovery:** an `AUTH` error before any data arrived, on a provider
   with an OAuth credential, triggers one forced token refresh and a free retry
   that does not consume an attempt. If the refresh token itself is dead, the
   stored credential is dropped and an `ErrorEvent` tells the user to run
   `/login`.
5. **Empty responses** are treated as failures. A turn that produced no
   `TextDeltaEvent`, `TextEndEvent`, or `ToolCallEndEvent` is retried like a
   transient error. If the final allowed attempt is still empty, an
   `ErrorEvent` is emitted rather than returning silently — otherwise the
   engine would commit an empty assistant message with `StopReason.Stop` as if
   the model had genuinely said nothing.

`invoke()` applies the same rules, judging content on non-whitespace text or
any tool call, but retries silently (it has no stream to emit `RetryEvent` on).

## Thinking and Reasoning

`ThinkingLevel` is Tau's provider-neutral effort scale:

```text
off · minimal · low · medium · high · xhigh · max · ultra
```

Set it with `LLMOptions(thinking_level=...)`, per call with
`invoke(context, thinking_level=...)`, or from the CLI with `--effort`
(non-persistent, clamped to the model's supported levels).

`Model.thinking_levels` lists the levels a model genuinely accepts. An empty
list means unconfirmed — pickers fall back to the full enum.
`Model.clamp_thinking_level()` snaps an unsupported level to the nearest
supported one instead of sending a value the backend will reject.
`Model.default_thinking_level` is `Medium` when supported, else the middle of
the supported range.

### Request Shapes

| Backend | Mechanism |
|---------|-----------|
| Anthropic Messages (`anthropic_messages`, `anthropic_vertex`, `anthropic_claude_code`) | `thinking: {type: "adaptive"}` + `output_config.effort` when `Model.thinking_adaptive`; otherwise `thinking: {type: "enabled", budget_tokens: N}` from `ThinkingBudgets` |
| OpenAI Responses (`openai_responses`) | `reasoning: {effort}` |
| OpenAI Codex Responses (`openai_codex_responses`) | `reasoning: {effort, summary: "auto"}` plus `reasoning.context: "all_turns"` |
| Gemini (`gemini_generate`, `google_vertex`) | `thinking_config.thinking_level` when `Model.thinking_uses_level` (Gemini 3.x), else `thinking_config.thinking_budget` |
| Google Antigravity (`google_antigravity`) | Always a raw token budget |
| Mistral (`mistral_chat`) | `reasoning_effort`, binary — only `"none"` / `"high"` |
| Ollama (`ollama_chat`) | Boolean `think` |
| OpenAI-compatible completions (`openai_completions`) | Dispatched by `Model.thinking_format` — see below |

`ThinkingBudgets` defaults, in tokens: `minimal` 1024, `low` 2048, `medium`
4096, `high` 8192, `xhigh` 16384, `max` 32768. Override globally under the
`thinking_budgets` key in settings, or per client via
`LLMOptions(thinking_budgets=...)`.

### Thinking Dialects

OpenAI-compatible providers disagree on how reasoning is requested.
`Model.thinking_format` (`tau/inference/api/text/dialect.py`) names the dialect;
`None` means the plain `reasoning_effort` shape.

| `thinking_format` | Request shape |
|-------------------|---------------|
| *(unset)* | `reasoning_effort: "low"\|"medium"\|"high"` |
| `zai` | `thinking: {type}` + `reasoning_effort` |
| `qwen` | `enable_thinking: bool` |
| `qwen-chat-template` | `chat_template_kwargs: {enable_thinking, preserve_thinking}` |
| `chat-template` | `chat_template_kwargs: {enable_thinking}` |
| `deepseek` | `thinking: {type}` + `reasoning_effort` |
| `openrouter` | `reasoning: {effort}`; an explicit `off` sends `reasoning: {enabled: false}`, while an unset level leaves reasoning on |
| `ant-ling` | `reasoning: {effort}` |
| `together` | `reasoning: {enabled}` + `reasoning_effort` |
| `string-thinking` | `thinking: "<effort>"` or `"none"` |
| `moonshot` | `reasoning_effort: "max"` only at `ThinkingLevel.Max` |
| `tinker` | Full `reasoning_effort` set including `minimal`/`xhigh`/`none` |

Generic levels collapse to three values: `minimal`/`low` → `low`, `medium` →
`medium`, `high`/`xhigh`/`max` → `high`. Tinker maps 1:1 instead.

On the response side, dialect does not matter: reasoning text is pulled from
whichever of `reasoning_content`, `reasoning`, `reasoning_text`, or `thinking`
the delta carries.

### Reasoning Replay

Providers that reason statelessly need the previous turn's reasoning items sent
back, or they lose the chain of thought across tool calls.

`openai_responses` implements this. When a thinking level is active it sets
`include: ["reasoning.encrypted_content"]`, and on each completed reasoning
item it stores the full raw item — encrypted content included — as JSON in
`ThinkingContent.signature`, alongside the human-readable summary text.

On the next request, `_messages_to_input` walks assistant content **in original
order** rather than grouping text first, because in the Responses API a
`reasoning` item must immediately precede the `function_call` or message it
justified. Signed thinking blocks are re-emitted as top-level input items;
unsigned blocks — from older sessions, or left over from a provider switch —
are dropped rather than sent as malformed reasoning items. The same signature
round-trips through xAI's Grok CLI proxy, which speaks the Responses API.

`openai_codex_responses` uses the same signature-carrying scheme.

For models whose adapter reports no thinking support, `ThinkingContent` is
merged into the text content (thinking first, then text) in memory only — the
session file is unaffected.

## Model Descriptors

`Model` (`tau/inference/model/types.py`) is plain data. The fields that change
behavior:

| Field | Purpose |
|-------|---------|
| `id`, `name`, `provider` | Identity; `provider` is the registry key |
| `cost` | `Cost(input, output, cache_read, cache_write)` per million tokens |
| `thinking`, `thinking_levels`, `thinking_format` | Reasoning capability and dialect |
| `thinking_adaptive` | Anthropic adaptive-thinking mechanism |
| `thinking_suppresses_sampling` | Anthropic models that reject non-default temperature/top_p/top_k |
| `thinking_uses_level` | Gemini 3.x coarse `thinking_level` instead of a raw budget |
| `antigravity_is_claude` | Claude models proxied through Google's Cloud Code Assist gateway |
| `context_window`, `max_input_tokens`, `max_output_tokens` | Limits; `input_limit` resolves to `max_input_tokens or context_window` and is what compaction must key off |
| `input`, `output` | `Modality` lists (`text`, `image`, `audio`, `video`, `file`) |
| `api`, `base_url` | Per-model adapter/endpoint override; unset inherits the provider's |
| `voices`, `tts_format` | Audio-model metadata |

`ModelRegistry` stores a list of variants per model id, so the same id can be
served by several providers. `get(model_id, provider=None)` returns the first
variant, or the pinned provider's.

## Dynamic Model Catalog

`tau.inference.model.catalog` wraps the community catalog at
<https://models.dev/api.json>, so new models appear without waiting for a Tau
release.

It is **standalone data access**. `Catalog` fetches, caches, and maps entries
to `Model` descriptors; it does not register anything. Whether and how to merge
its output into a `ModelRegistry` is the caller's decision — which makes the
natural pattern an additive overlay: register built-ins first, then add catalog
models that the built-ins do not already define.

| Member | Description |
|--------|-------------|
| `Catalog(path=None, url=CATALOG_URL)` | Defaults to `~/.tau/models-catalog.json` |
| `load()` | Read the on-disk cache into memory; `True` when usable |
| `fetch()` | Blocking HTTPS GET, trim, cache, save. Run off the event loop |
| `save()` | Atomically persist the in-memory payload |
| `is_stale(now=None)` | `True` with no payload or one older than 4 hours |
| `data` / `fetched_at` | Raw trimmed payload; unix timestamp of last fetch |
| `text_models(provider=None)` | Chat models as `Model`s |
| `image_models` / `video_models` / `audio_models` | Same, per modality |
| `get_text_model(model_id, provider)` | Single lookup; `None` when absent |
| `get_image_model` / `get_video_model` / `get_audio_model` | Same, per modality |
| `default_catalog()` | Process-wide instance on the standard cache path |

### Behavior

- **Provider mapping is explicit.** Only providers whose model ids pass through
  to the API verbatim are mapped (`PROVIDER_MAP` and the per-modality maps).
  Providers with curated or transformed id schemes — Bedrock ARNs, Vertex
  publisher paths, the Codex/Copilot OAuth catalogs, local runtimes — are
  deliberately absent.
- **The cache is trimmed.** Only mapped providers are kept; the full payload is
  roughly 3.5 MB, about 88% of it unmappable.
- **Refresh is throttled** to at most once per four hours via `is_stale()`.
- **Catalog models leave `Model.api` unset**, so they inherit the provider's
  default adapter — the service resolves `model.api or provider.api`.
- **Declared modalities are intersected** with what the adapter actually
  supports, so a catalog model cannot advertise content types the adapter would
  reject. Beyond text and image, only `anthropic` (file), `google` (file, audio,
  video), and `openrouter` (audio) add inputs.
- **Entries are filtered:** deprecated models, models whose output is not
  text-only (for the text registry), router meta-models such as
  `openrouter/auto`, and date-suffixed aliases (`foo-20250805`) whose base id
  also exists.
- **Thinking levels are only trusted when clean.** An `effort` reasoning option
  whose values all parse as `ThinkingLevel` yields that list with `Off`
  prepended. `toggle`/`budget_tokens` options, or any unrecognized value, yield
  an empty list — meaning unconfirmed, so pickers fall back to the full enum.
- `limit.input` becomes `max_input_tokens` only when it is a real bound below
  the total context window.

### Additive Overlay

```python
from tau.inference.model.catalog import default_catalog
from tau.inference.model.registry import ModelRegistry

registry = ModelRegistry.from_text_builtins()
known = {(m.provider, m.id) for m in registry.list()}

catalog = default_catalog()
if not catalog.load() or catalog.is_stale():
    catalog.fetch()          # blocking — run via asyncio.to_thread in async code

added = 0
for model in catalog.text_models():
    if (model.provider, model.id) not in known:
        registry.register(model)
        added += 1

print(f"{len(known)} built-in models, {added} added from models.dev")
```

Built-in descriptors win on conflict: they carry hand-verified thinking levels,
dialect tags, and per-backend quirks the catalog does not express. Pass the
result to `TextLLM(..., models=registry)` to use it.

## Other Modalities

Image, audio, and video clients return normalized result dataclasses rather
than event streams.

| Client | Call | Returns |
|--------|------|---------|
| `ImageLLM` | `generate(ImageContext)` | `GeneratedImage(model_id, provider, output, stop_reason, usage, error, timestamp)` |
| `AudioLLM` | `synthesize(TTSContext)` | `SynthesizedAudio` |
| `AudioLLM` | `transcribe(STTContext)` | `TranscribedAudio(text, language, duration, words, segments, stop_reason, ...)` |
| `VideoLLM` | `generate(VideoContext)` | `GeneratedVideo(url, video, format, duration, stop_reason, ...)` |

`GeneratedImage.output` is normalized message content; depending on the adapter
the image content holds bytes or a URL. `GeneratedVideo` sets `video` or `url`,
not necessarily both.

For speech-to-text timestamps, pass `TimestampGranularity.Word` or
`TimestampGranularity.Segment` in `STTContext.timestamp_granularities`; see
[Inference Providers](inference-providers.md#speech-to-text-timestamps) for
per-provider support.

## Standalone Usage

`tau.inference` runs without the Tau runtime: no session, no engine, no TUI, no
tool loop. You supply the message list; it resolves the model, provider, and
credentials and gives you back normalized events. It does **not** persist
anything, execute tool calls, build system prompts, or compact context — for
those, see [Engine](engine.md) for the tool loop and
[Python API](python-api.md) for the full runtime.

Credentials come from the same places as the CLI, so an exported
`ANTHROPIC_API_KEY` (or a `~/.tau/auth.json` entry) is all the setup needed.
See [Authentication](auth.md).

```python
"""Standalone tau.inference: streaming, non-streaming, and model listing."""

import asyncio

from tau.inference import (
    LLM,
    EndEvent,
    ErrorEvent,
    LLMContext,
    LLMOptions,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ThinkingLevel,
    ToolCallEndEvent,
)
from tau.inference.api.text.service import TextLLM
from tau.inference.types import RetryEvent
from tau.message.types import UserMessage


async def stream_example() -> None:
    """Stream a response token by token, printing thinking dimly."""
    llm = LLM(
        "claude-sonnet-4-6",
        provider="anthropic",
        options=LLMOptions(
            temperature=0.2,
            max_tokens=1024,
            thinking_level=ThinkingLevel.Medium,
        ),
    )
    print(f"→ {llm.provider_id}/{llm.model.id}")
    if llm.fallback_reason:
        print(f"  note: {llm.fallback_reason}")

    context = LLMContext(
        system_prompt="Answer concisely.",
        messages=[UserMessage.from_text("Explain event streaming in two sentences.")],
    )

    async for event in llm.stream(context):
        match event:
            case ThinkingDeltaEvent() if event.thinking is not None:
                print(f"\033[2m{event.thinking.content}\033[0m", end="", flush=True)
            case TextDeltaEvent():
                print(event.text.content, end="", flush=True)
            case ToolCallEndEvent() if event.tool_call is not None:
                print(f"\n[tool] {event.tool_call.name}({event.tool_call.args})")
            case RetryEvent():
                print(f"\n[retry {event.attempt}/{event.max_retries}] {event.error}")
            case ErrorEvent():
                print(f"\n[error:{event.kind}] {event.error}")
            case EndEvent():
                print(
                    f"\n[{event.reason}] in={event.input_tokens} out={event.output_tokens}"
                    f" cache_read={event.cache_read_tokens}"
                )


async def invoke_example() -> None:
    """Collect a whole turn at once instead of streaming it."""
    llm = LLM("claude-sonnet-4-6", provider="anthropic")
    context = LLMContext(messages=[UserMessage.from_text("Name three primes.")])

    events = await llm.invoke(context, thinking_level=ThinkingLevel.Off)
    text = "".join(e.text.content for e in events if isinstance(e, TextDeltaEvent))
    print(text or "(no text)")


def list_models() -> None:
    """Show text models whose provider has usable credentials right now."""
    for model in TextLLM.list_available()[:10]:
        thinking = "thinking" if model.thinking else "-"
        print(f"{model.provider}/{model.id:<40} {model.input_limit:>9,} ctx  {thinking}")


async def main() -> None:
    list_models()
    await stream_example()
    await invoke_example()


if __name__ == "__main__":
    asyncio.run(main())
```

### Full Dependency Control

Supply your own registries to avoid the process-wide builtins entirely —
useful in tests and in hosts that must not read `~/.tau/auth.json`:

```python
import asyncio

from tau.auth.manager import AuthManager
from tau.inference.api.text.registry import LLMAPIRegistry
from tau.inference.api.text.service import TextLLM
from tau.inference.model.registry import ModelRegistry
from tau.inference.provider.registry import ProviderRegistry, TextProviderRegistry
from tau.inference.types import LLMContext
from tau.message.types import UserMessage


async def main() -> None:
    providers = TextProviderRegistry.from_builtins()
    auth = AuthManager.in_memory(
        ProviderRegistry(text=providers),
        {"anthropic": {"type": "api_key", "key": "sk-ant-..."}},
    )

    llm = TextLLM(
        "claude-sonnet-4-6",
        provider="anthropic",
        models=ModelRegistry.from_text_builtins(),
        providers=providers,
        apis=LLMAPIRegistry.from_builtins(),
        auth_manager=auth,
    )

    context = LLMContext(messages=[UserMessage.from_text("Hello.")])
    events = await llm.invoke(context)
    print(len(events), "events")


asyncio.run(main())
```

### Other Modalities, Standalone

```python
import asyncio
from pathlib import Path

from tau.inference import (
    AudioFormat,
    AudioLLM,
    ImageContext,
    ImageLLM,
    STTContext,
    TTSContext,
    VideoContext,
    VideoLLM,
)
from tau.message.types import TextContent


async def main() -> None:
    image = await ImageLLM("dall-e-3").generate(
        ImageContext(
            contents=[TextContent(content="A technical cutaway of a lunar rover")],
            size="1024x1024",
            quality="standard",
        )
    )
    print(image.stop_reason, len(image.output), "content items")

    speech = await AudioLLM("tts-1").synthesize(
        TTSContext(input="Tau inference is usable as a library.", voice="alloy")
    )
    Path("speech.mp3").write_bytes(speech.audio)

    transcript = await AudioLLM("whisper-1").transcribe(
        STTContext(audio=Path("speech.mp3").read_bytes(), format=AudioFormat.MP3)
    )
    print(transcript.text)

    video = await VideoLLM("fal-ai/veo3-fast").generate(
        VideoContext(
            prompt="A slow orbital shot around a satellite",
            duration=5,
            aspect_ratio="16:9",
        )
    )
    if video.video is not None:
        Path(f"video.{video.format.value}").write_bytes(video.video)
    else:
        print(video.url)


asyncio.run(main())
```

## Extension Boundary

Extensions register models, providers, and API adapters through the extension
API. Applications needing complete dependency control pass custom model,
provider, API, and auth registries to the service constructors, as shown above.

## Next Steps

- [Inference Providers](inference-providers.md) — every provider, its ids, auth, and endpoints
- [Authentication](auth.md) — credential storage and resolution order
- [HTTP Proxy](http-proxy.md) — routing inference traffic through a proxy
- [Engine](engine.md) — the tool-calling loop on top of this layer
- [Python API](python-api.md) — embedding the full Tau runtime

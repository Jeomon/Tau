# Inference Providers

Every provider Tau ships with, its id, how it authenticates, and its default
endpoint. Provider ids are the keys used in `~/.tau/auth.json`, in
`tau auth set`, and in the `provider/model` shorthand.

Credential mechanics are covered in [Authentication](auth.md); the request path
is covered in [Inference](inference.md).

## Table of Contents

- [Text Providers](#text-providers)
- [API-Key Providers](#api-key-providers)
- [OAuth Providers](#oauth-providers)
- [Local Providers](#local-providers)
- [Google Cloud Vertex AI](#google-cloud-vertex-ai)
- [Media Providers](#media-providers)
- [Selecting a Provider](#selecting-a-provider)
- [Base URL Overrides](#base-url-overrides)
- [Custom and Unlisted Models](#custom-and-unlisted-models)
- [Speech-to-Text Timestamps](#speech-to-text-timestamps)
- [Troubleshooting](#troubleshooting)

## Text Providers

The model picker (`/model`) is the authoritative model list. Built-in model
metadata changes more often than this page, so model ids and pricing are not
enumerated here.

| Provider | Id | Auth | Adapter | Default base URL |
|----------|----|------|---------|------------------|
| OpenAI | `openai` | `OPENAI_API_KEY` | `openai_responses` | SDK default |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | `anthropic_messages` | SDK default |
| Google | `google` | `GOOGLE_API_KEY` | `gemini_generate` | SDK default |
| Mistral | `mistral` | `MISTRAL_API_KEY` | `mistral_chat` | SDK default |
| NVIDIA | `nvidia` | `NVIDIA_API_KEY` | `openai_completions` | `https://integrate.api.nvidia.com/v1` |
| Groq | `groq` | `GROQ_API_KEY` | `openai_completions` | `https://api.groq.com/openai/v1` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | `openai_completions` | `https://openrouter.ai/api/v1` |
| Perplexity | `perplexity` | `PERPLEXITY_API_KEY` | `openai_responses` | `https://api.perplexity.ai/v1` |
| xAI | `xai` | `XAI_API_KEY` | `openai_responses` | `https://api.x.ai/v1` |
| AWS Bedrock | `bedrock` | `BEDROCK_API_KEY` | `openai_responses` | `https://bedrock-mantle.us-east-1.api.aws/v1` |
| Kimi / Moonshot | `kimi` | `KIMI_API_KEY` | `openai_completions` | `https://api.moonshot.ai/v1` |
| MiniMax | `minimax` | `MINIMAX_API_KEY` | `anthropic_messages` | `https://api.minimax.io/anthropic` |
| Cerebras | `cerebras` | `CEREBRAS_API_KEY` | `openai_completions` | `https://api.cerebras.ai/v1` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | `openai_completions` | `https://api.deepseek.com` |
| Z.ai | `zai` | `ZAI_API_KEY` | `openai_completions` | `https://api.z.ai/api/paas/v4` |
| Kilo Code | `kilocode` | `KILOCODE_API_KEY` | `openai_completions` | `https://api.kilo.ai/api/gateway` |
| Fireworks AI | `fireworks` | `FIREWORKS_API_KEY` | `openai_completions` | `https://api.fireworks.ai/inference/v1` |
| Hugging Face | `huggingface` | `HUGGINGFACE_API_KEY` | `openai_completions` | `https://router.huggingface.co/v1` |
| Subconscious | `subconscious` | `SUBCONSCIOUS_API_KEY` | `openai_completions` | `https://api.subconscious.dev/v1` |
| Tinker | `tinker` | `TINKER_API_KEY` | `openai_completions` | `https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1` |
| Ollama | `ollama` | None | `ollama_chat` | `http://localhost:11434` |
| LM Studio | `lmstudio` | None | `openai_completions` | `http://localhost:1234/v1` |
| vLLM | `vllm` | None | `openai_completions` | `http://localhost:8000/v1` |
| llama.cpp | `llamacpp` | None | `openai_completions` | `http://localhost:8080/v1` |
| Google Vertex AI | `google-vertex` | Ambient GCP | `google_vertex` | Derived from project/location |
| Anthropic on Vertex AI | `anthropic-vertex` | Ambient GCP | `anthropic_vertex` | Derived from project/region |
| OpenAI-compatible on Vertex AI | `openai-vertex` | Ambient GCP | `openai_vertex` | Derived from project/location |
| ChatGPT Plus/Pro (Codex) | `openai-codex` | OAuth | `openai_codex_responses` | Provider-managed |
| Anthropic (Claude Pro/Max) | `anthropic-claude-code` | OAuth | `anthropic_claude_code` | Provider-managed |
| GitHub Copilot | `github-copilot` | OAuth | `github_copilot_chat` | Provider-managed |
| Google Antigravity | `google-antigravity` | OAuth | `google_antigravity` | Provider-managed |
| xAI Grok CLI | `xai-grok` | OAuth | `xai_responses` | Provider-managed |

"SDK default" means Tau sets no `base_url` and the vendor SDK uses its own.
A model may override its provider's adapter and base URL through
`Model.api` / `Model.base_url`.

Extensions can register additional providers, models, and adapters.

## API-Key Providers

All API-key providers follow the same three steps: create a key, expose it, and
verify.

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # 1. env var, or
tau auth set anthropic sk-ant-...       #    stored credential, or /login in a session

tau --model anthropic/claude-sonnet-4-6 -p "Say hello"   # 2. verify
```

Substitute the id and env var from the table above for any other provider.

### Anthropic

Create a key at [Anthropic Console](https://console.anthropic.com) → API keys.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
tau --model anthropic/claude-sonnet-4-6 -p "Say hello"
```

Anthropic models use structured thinking blocks. Newer models
(`Model.thinking_adaptive`) use `thinking: {type: "adaptive"}` with an effort
level; older ones use a `budget_tokens` budget. See
[Inference](inference.md#thinking-and-reasoning).

### OpenAI

Create a key at [OpenAI Platform](https://platform.openai.com) → API keys.

```bash
export OPENAI_API_KEY=sk-...
tau --model openai/gpt-4o -p "Say hello"
```

Tau uses the **Responses API**, not Chat Completions. When reasoning is active
it requests `reasoning.encrypted_content` so reasoning items can be replayed
across tool calls, see
[Reasoning Replay](inference.md#reasoning-replay).

### Google

Tau's `google` provider is the Gemini Developer API (Google AI Studio), through
the Google Gen AI SDK. It is separate from `google-vertex`, which uses Google
Cloud.

1. Create a key at [Google AI Studio](https://aistudio.google.com), no billing
   account needed.
2. Export it:

```bash
export GOOGLE_API_KEY=...
tau --model google/gemini-2.5-flash -p "Say hello"
```

Gemini 3.x models use the coarse `thinking_config.thinking_level` enum;
earlier ones use a raw `thinking_budget` token count.

### Mistral

Create a key at [Mistral Console](https://console.mistral.ai).

```bash
export MISTRAL_API_KEY=...
tau --model mistral/mistral-large -p "Say hello"
```

Mistral's `reasoning_effort` is binary: Tau sends `"none"` for
`ThinkingLevel.Off` and `"high"` for every other level.

### Fireworks AI

Fast inference for open-source models (Llama, DeepSeek, Qwen, Mixtral) over an
OpenAI-compatible API.

```bash
export FIREWORKS_API_KEY=fw_...
tau --model fireworks/accounts/fireworks/models/llama-v3p3-70b-instruct -p "Say hello"
```

Tau sends a fresh `x-session-affinity` header (a UUID) per request and enables
the `cache_compat` extra parameter.

### Hugging Face

The Inference Providers router gives OpenAI-compatible access to open-source
models hosted across partner backends. A `read` token from
[huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) is
enough.

```bash
export HUGGINGFACE_API_KEY=hf_...
tau --model huggingface/deepseek-ai/DeepSeek-V3-0324 -p "Say hello"
```

Model ids are Hub repo ids. Built-in entries are pinned to a specific backend
with `<repo>:<provider>` (e.g. `openai/gpt-oss-120b:groq`) rather than the
router's default `:fastest` routing, because unpinned routing can silently
switch to a backend with different tool-calling behavior. Any
`<repo>[:<backend>]` combination the router supports still works via
`--model huggingface/<repo>:<backend>` even when it is not in the built-in list.

### Subconscious

Hosted inference over OpenAI-compatible Chat Completions.

```bash
export SUBCONSCIOUS_API_KEY=...
tau --model subconscious/subconscious/tim-qwen3.6-27b -p "Say hello"
```

Subconscious publishes no numeric context-window or max-output limits, so Tau
leaves those fields unset rather than guessing.

### Tinker

Tinker accepts the full OpenAI `reasoning_effort` string set (`none`,
`minimal`, `low`, `medium`, `high`, `xhigh`) so Tau maps thinking levels 1:1
instead of collapsing them to three tiers. `ThinkingLevel.Off` sends an explicit
`"none"`, because omitting the parameter would leave reasoning on by default.

```bash
export TINKER_API_KEY=...
```

### OpenRouter

```bash
export OPENROUTER_API_KEY=sk-or-...
tau --model openrouter/anthropic/claude-sonnet-4.5 -p "Say hello"
```

OpenRouter normalizes reasoning through a nested `reasoning` object. An explicit
`--effort off` sends `reasoning: {enabled: false}`; leaving the level unset
leaves reasoning enabled, because some reasoning-capable models reject a request
that disables it.

## OAuth Providers

Subscription providers authenticate with `/login` in a session or
`tau auth login <id>` from the shell.

| Provider | Id | Requires | Flow |
|----------|----|----------|------|
| ChatGPT Plus/Pro (Codex) | `openai-codex` | ChatGPT Plus or Pro | Local callback server |
| Anthropic (Claude Pro/Max) | `anthropic-claude-code` | Claude Pro or Max | Local callback server |
| GitHub Copilot | `github-copilot` | Copilot subscription | Device code |
| Google Antigravity | `google-antigravity` | Google account | Local callback server |
| xAI Grok CLI | `xai-grok` | SuperGrok subscription | Local callback server |

```bash
tau auth login anthropic-claude-code
tau auth status
tau auth logout anthropic-claude-code
```

Tokens are stored in `~/.tau/auth.json` and refreshed automatically, including
a forced refresh and free retry if a token is rejected mid-request. See
[Authentication](auth.md#oauth-providers).

Note that `google-antigravity` proxies both Gemini and Anthropic Claude models
through Google's Cloud Code Assist gateway. Claude models routed that way are
flagged with `Model.antigravity_is_claude`, which switches on the stricter
request rules that backend requires.

## Local Providers

Local runtimes need no credential (`AuthType.None_`) and are always considered
available.

| Runtime | Id | Default endpoint | Discovery |
|---------|----|------------------|-----------|
| Ollama | `ollama` | `http://localhost:11434` | Queries the local model list |
| LM Studio | `lmstudio` | `http://localhost:1234/v1` | Queries the local model list |
| vLLM | `vllm` | `http://localhost:8000/v1` | Reports the running server's model |
| llama.cpp | `llamacpp` | `http://localhost:8080/v1` | Reports the running server's model |

vLLM and llama.cpp expose no separate catalog endpoint, so Tau reports whatever
model the running server has loaded rather than a list.

### Ollama

```bash
ollama pull mistral
ollama serve
tau --model ollama/mistral -p "Say hello"
```

Ollama Cloud models are covered by the static built-in catalog under the same
provider id; locally installed models are discovered at startup and merged in.

### OpenAI-compatible servers

`lmstudio`, `vllm`, and `llamacpp` all speak `openai_completions`. Point them
somewhere else with `--base-url`:

```bash
tau --provider vllm --model my-finetune --base-url http://gpu-box:8000/v1
```

## Google Cloud Vertex AI

Three providers cover Vertex, one per model family. All use ambient Google
Cloud credentials rather than a `<PROVIDER>_API_KEY`.

| Provider | Id | Adapter |
|----------|----|---------|
| Google models on Vertex | `google-vertex` | `google_vertex` |
| Anthropic models on Vertex | `anthropic-vertex` | `anthropic_vertex` |
| OpenAI-compatible on Vertex | `openai-vertex` | `openai_vertex` |

| Environment variable | Used by | Purpose |
|----------------------|---------|---------|
| `GOOGLE_CLOUD_PROJECT` | all three | Project id |
| `GCLOUD_PROJECT` | all three | Fallback project id |
| `GOOGLE_CLOUD_LOCATION` | all three | Region |
| `GOOGLE_CLOUD_API_KEY` | `google-vertex` | Express-mode API key, used when `LLMOptions.api_key` is unset |
| `GOOGLE_APPLICATION_CREDENTIALS` | `google-vertex` | Service-account key file |

Region defaults differ by adapter: `anthropic-vertex` falls back to `global`,
`openai-vertex` to `us-central1`, and `google-vertex` has no fallback. Set
`GOOGLE_CLOUD_LOCATION` explicitly.

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=my-project
export GOOGLE_CLOUD_LOCATION=us-central1

tau --provider anthropic-vertex --model claude-sonnet-4-6 -p "Say hello"
```

Project and region can also be supplied per request through
`LLMOptions.extra_params` (`project`, and `region` or `location`), which takes
precedence over the environment.

## Media Providers

Image, audio, and video providers are registered separately from text
providers. They share the same credential store and the same
`<PROVIDER>_API_KEY` rule.

### Image

| Provider | Id | Adapter | Base URL |
|----------|----|---------|----------|
| OpenAI | `openai` | `openai-image` | `https://api.openai.com/v1` |
| Google | `google` | `gemini-image` | `https://generativelanguage.googleapis.com` |
| OpenRouter | `openrouter` | `openrouter-image` | `https://openrouter.ai/api/v1` |
| Together AI | `together` | `openai-image` | `https://api.together.xyz/v1` |
| Fireworks AI | `fireworks` | `openai-image` | `https://api.fireworks.ai/inference/v1` |
| Z.ai | `zai` | `openai-image` | `https://api.z.ai/api/paas/v4` |

### Audio

| Provider | Id | Adapter | Base URL |
|----------|----|---------|----------|
| OpenAI | `openai` | `openai-audio` | SDK default |
| Google | `google` | `gemini-audio` | SDK default |
| Groq | `groq` | `openai-audio` | `https://api.groq.com/openai/v1` |
| OpenRouter | `openrouter` | `openai-audio` | `https://openrouter.ai/api/v1` |
| ElevenLabs | `elevenlabs` | `elevenlabs-audio` | `https://api.elevenlabs.io` |
| Sarvam | `sarvam` | `sarvam-audio` | `https://api.sarvam.ai` |
| Z.ai | `zai` | `openai-audio` | `https://api.z.ai/api/paas/v4` |

### Video

| Provider | Id | Adapter | Base URL |
|----------|----|---------|----------|
| fal.ai | `fal` | `fal-video` | SDK default |
| OpenRouter | `openrouter` | `openrouter-video` | `https://openrouter.ai/api/v1` |
| Z.ai | `zai` | `zai-video` | `https://api.z.ai/api/paas/v4` |

Because provider ids are shared across modalities, one `OPENAI_API_KEY` covers
OpenAI text, image, and audio; one `ZAI_API_KEY` covers Z.ai text, image, audio,
and video.

## Selecting a Provider

### Command line

```bash
tau --provider anthropic                     # provider only; model from settings
tau --model openai/gpt-4o                    # provider/model shorthand
tau --model ollama/mistral                   # local
tau --provider groq --model llama-3.3-70b-versatile
```

`--provider` wins over the prefix in `--model` when both are given. When
neither is set, Tau uses the model saved in settings, then its built-in default.

### Interactive

Use `/model` to switch models mid-session. The picker lists only models whose
provider has usable authentication.

### Settings

```json
{
  "model": {
    "text": {
      "provider": "anthropic",
      "id": "claude-sonnet-4-6"
    }
  }
}
```

Per-modality keys (`text`, `image`, `audio`, `video`) live under `model`. See
[Settings](settings.md).

## Base URL Overrides

`--base-url` temporarily points the resolved provider at a different endpoint:

```bash
tau --provider anthropic --base-url https://my-gateway.corp.example/v1
tau --provider openai --base-url http://localhost:8080/v1 -p "Say hello"
```

| Property | Behavior |
|----------|----------|
| Persistence | **None.** It is not written to settings and applies only to this run |
| Precedence | Above provider defaults, model-level `base_url`, and `LLMOptions.base_url` |
| Scope | Applied to the LLM built at startup |

> **Limitation:** switching models in-session with `/model` constructs a fresh
> client and does **not** re-apply `--base-url`. The override is lost after a
> model switch. Restart with the flag to reapply it.

Programmatically the equivalent is `LLMOptions(base_url=...)` passed to the
client, which participates in normal option merging instead of overriding it.

## Custom and Unlisted Models

Pinning a provider that has no built-in entry for a model id is not an error.
Tau synthesizes a descriptor from that provider's first registered model
(copying adapter, base URL, cost, and capabilities) under the requested id:

```bash
tau --provider openrouter --model some-vendor/brand-new-model
tau --provider vllm --model /models/my-finetune --base-url http://gpu-box:8000/v1
```

Capability metadata is inherited, but `thinking_levels` is deliberately cleared
so an unrelated model's verified levels do not leak onto a custom id. A trailing
`:<level>` sets a default thinking level for such an id:

```bash
tau --provider openrouter --model some-vendor/model:high
```

An explicit `--effort` always wins over the suffix, in which case the suffix
stays part of the model id as-is.

For a broader set of real model ids without waiting for a Tau release, see the
[Dynamic Model Catalog](inference.md#dynamic-model-catalog).

## Speech-to-Text Timestamps

Pass `TimestampGranularity.Word` or `TimestampGranularity.Segment` in
`STTContext.timestamp_granularities`.

| Model | Word | Segment |
|-------|------|---------|
| OpenAI `whisper-1` | Yes | Yes |
| Groq Whisper models | Yes | Yes |
| OpenAI GPT-4o transcription models | No | No |
| ElevenLabs Scribe | Yes | No |
| Sarvam Saaras | Yes | No |

GPT-4o transcription models return plain JSON and expose no detailed timestamps
through this API. Sarvam receives `with_timestamps=true` when word timestamps
are requested.

## Troubleshooting

### Provider not picking up credentials

1. Check what Tau sees: `tau auth status`.
2. Confirm the env var matches the provider id upper-cased with `_API_KEY`
   appended: `huggingface` → `HUGGINGFACE_API_KEY`.
3. Remember that a **stored credential blocks the env var**. If `auth.json` has
   a stale entry, `tau auth unset <provider>` before relying on the environment.
4. If the key is a `$ENV_VAR` or `!command` reference, verify it resolves.
   References that produce an empty value are treated as unset.

### Model not found

A model id must be registered for the provider, or the provider must have at
least one other registered model for the custom-id fallback to apply. Check
`/model`, or `TextLLM.list_available()` from Python.

### Empty responses

Tau retries a turn that produced no text and no tool call, then reports an
error rather than committing a blank message. Repeated empty responses usually
mean a thinking dialect mismatch on an OpenAI-compatible endpoint. Try
`--effort off`.

### Connection timeouts

1. Check reachability of the provider's base URL from the table above.
2. If you are behind a proxy, see [HTTP Proxy](http-proxy.md).
3. Raise the per-request timeout with the `http_idle_timeout_ms` setting.

### Rate limits

Tau classifies rate-limit errors as retryable and honors `Retry-After` with
exponential backoff. Tune the budget with the `retry` settings, or switch
providers for the same model with `--provider`.

## Next Steps

- [Authentication](auth.md): credential storage and resolution
- [Inference](inference.md): the request path, events, and thinking levels
- [HTTP Proxy](http-proxy.md): proxy configuration
- [Settings](settings.md): defaults for provider, model, retries, and timeouts

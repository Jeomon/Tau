# Inference Providers

This page documents all supported LLM inference providers and their setup.

## Supported Providers

Tau supports the following inference providers:

Built-in API-key providers are `openai`, `anthropic`, `google`, `nvidia`,
`groq`, `openrouter`, `perplexity`, `xai`, `bedrock`, `kimi`, `minimax`,
`cerebras`, `deepseek`, `zai`, `kilocode`, `fireworks`, `huggingface`,
`subconscious`, and `mistral`.

Tau also includes local `ollama`, Google/Anthropic/OpenAI-compatible Vertex AI
providers, and OAuth providers for OpenAI Codex, Claude Code, GitHub Copilot,
Google Antigravity, and xAI Grok. Extensions can register additional providers.

The model picker is the authoritative model catalogue. Built-in model metadata
changes more frequently than this guide, so model IDs and pricing are not
enumerated here.

## Anthropic

Anthropic provides Claude models with best-in-class reasoning and code generation.

### Setup

1. Create an account at [Anthropic Console](https://console.anthropic.com)
2. Generate an API key in the API keys section
3. Set the environment variable:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### Verify

```bash
tau --model anthropic/claude-3-5-sonnet -p "Say hello"
```

## OpenAI

OpenAI provides GPT models optimized for a wide range of tasks.

### Setup

1. Create an account at [OpenAI Platform](https://platform.openai.com)
2. Generate an API key in the API keys section
3. Set the environment variable:

```bash
export OPENAI_API_KEY=sk-...
```

### Verify

```bash
tau --model openai/gpt-4o -p "Say hello"
```

## Google Gemini

Google AI Studio provides API-key access to Gemini models through the Google
Gen AI SDK. Tau's `google` provider uses the Gemini Developer API; it is
separate from the `google-vertex` provider, which uses Google Cloud.

### Setup

1. Visit [Google AI Studio](https://aistudio.google.com)
2. Create an API key (no account creation needed)
3. Set the environment variable:

```bash
export GOOGLE_API_KEY=...
```

### Verify

```bash
tau --model google/gemini-2.5-flash -p "Say hello"
```

Current Google AI Studio model IDs include:

- `gemini-3.5-flash` (stable)
- `gemini-3.1-flash-lite` (stable)
- `gemini-3.1-pro-preview` (preview)
- `gemini-2.5-pro`, `gemini-2.5-flash`, and `gemini-2.5-flash-lite`

## Mistral AI

Mistral offers efficient open-source-based models.

### Setup

1. Create an account at [Mistral Console](https://console.mistral.ai)
2. Generate an API key
3. Set the environment variable:

```bash
export MISTRAL_API_KEY=...
```

### Verify

```bash
tau --model mistral/mistral-large -p "Say hello"
```

## Fireworks AI

Fireworks AI provides fast, cost-efficient inference for open-source models including Llama, DeepSeek, Qwen, and Mixtral — with OpenAI-compatible APIs.

### Setup

1. Create an account at [Fireworks AI](https://fireworks.ai)
2. Generate an API key in your account settings
3. Set the environment variable:

```bash
export FIREWORKS_API_KEY=fw_...
```

### Verify

```bash
tau --model fireworks/accounts/fireworks/models/llama-v3p3-70b-instruct -p "Say hello"
```

### Notes

- Uses the OpenAI-compatible completions API under the hood
- Session affinity is maintained automatically via a per-request `x-session-affinity` header

## Hugging Face

Hugging Face's Inference Providers router gives OpenAI-compatible access to
open-source models (DeepSeek, Llama, Qwen, Mixtral, and more) hosted across
its partner inference providers.

### Setup

1. Create an account at [Hugging Face](https://huggingface.co)
2. Generate an access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (a `read` token is sufficient)
3. Set the environment variable:

```bash
export HUGGINGFACE_API_KEY=hf_...
```

### Verify

```bash
tau --model huggingface/deepseek-ai/DeepSeek-V3-0324 -p "Say hello"
```

### Notes

- Uses the OpenAI-compatible completions API under the hood, routed through `https://router.huggingface.co/v1`
- Model IDs are Hugging Face Hub repo IDs (e.g. `meta-llama/Llama-3.3-70B-Instruct`)
- Built-in model entries are pinned to a specific backend with `<repo>:<provider>` (e.g. `openai/gpt-oss-120b:groq`) rather than the router's default `:fastest` routing, since unpinned routing can silently switch to a backend with different tool-calling behavior. You can still use any model/backend combination the router supports via `tau --model huggingface/<repo>[:<provider>]` even if it isn't in the built-in list.

## Subconscious

Subconscious provides hosted inference through OpenAI-compatible Chat Completions
and Anthropic-compatible Messages APIs. Tau uses Chat Completions because dashboard
keys with the documented `sky_` prefix authenticate through Bearer authorization on
that endpoint.

### Setup

1. Create an account at [Subconscious](https://www.subconscious.dev/)
2. Generate an API key in the dashboard
3. Set the environment variable:

```bash
export SUBCONSCIOUS_API_KEY=...
```

### Verify

```bash
tau --model subconscious/subconscious/tim-qwen3.6-27b -p "Say hello"
```

### Notes

- The built-in model ID is `subconscious/tim-qwen3.6-27b`; the advertised `subconscious/glm-5.2` is omitted because the live API currently rejects it as an unknown model
- `TIM-Qwen3.6 27B` is published as multimodal with optional thinking; Tau enables its documented image modality and thinking selector
- Subconscious does not publish numeric context-window or maximum-output limits, so Tau leaves those fields unspecified rather than guessing
- Model availability and pricing come from the [official pricing page](https://www.subconscious.dev/pricing)

## Ollama (Local)

Run open-source models locally without API keys or internet.

### Setup

1. Install [Ollama](https://ollama.ai)
2. Pull a model:

```bash
ollama pull mistral
```

3. Start the Ollama server:

```bash
ollama serve
```

Tau connects to `http://localhost:11434` by default.

### Verify

```bash
tau --model ollama/mistral -p "Say hello"
```

## Switching Providers

## Speech-to-Text Timestamps

Pass `TimestampGranularity.Word` or `TimestampGranularity.Segment` in
`STTContext.timestamp_granularities` when using the Python API.

- OpenAI `whisper-1` and Groq Whisper models support word and segment timestamps.
- OpenAI GPT-4o transcription models return plain JSON and do not expose detailed
  timestamps through this API.
- ElevenLabs Scribe and Sarvam Saaras support word timestamps. Sarvam receives
  `with_timestamps=true` when word timestamps are requested.

### Command Line

Use a specific provider for a single session:

```bash
tau --provider anthropic
tau --model openai/gpt-4o
tau --model ollama/mistral
```

### Interactive

Use the `/model` slash command during a session to switch models.

### Default

Set your default provider and model in `~/.tau/settings.json`:

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

## Troubleshooting

### Provider Not Found

If a provider isn't picking up credentials, check:

1. **API key is set**: `env | grep API_KEY`
2. **Provider is supported**: Verify it's in the list above
3. **Credentials are valid**: Test with a curl request to the provider's API

### Connection Timeout

If tau cannot reach a provider:

1. Check your internet connection
2. Verify the provider's API is not down
3. Check for network firewalls or proxies blocking the connection

### Rate Limits

If you hit rate limits from a provider:

1. Wait before retrying
2. Consider upgrading your account tier
3. Use a different provider with higher limits

## Next Steps

- [Quickstart](quickstart.md) - Set up your first provider
- [Settings](settings.md) - Configure default provider behavior
- [Installation](installation.md) - Detailed authentication setup

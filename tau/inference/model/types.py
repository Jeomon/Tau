from dataclasses import dataclass, field
from enum import StrEnum

from tau.inference.types import ThinkingLevel
from tau.message.types import Usage, UsageCost


class Modality(StrEnum):
    """Content modality supported by a model's input or output."""

    Text = "text"
    Image = "image"
    Audio = "audio"
    Video = "video"
    File = "file"


@dataclass
class Cost:
    """Per-million-token pricing for a model (USD)."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass
class Model:
    """Full descriptor for a single LLM/image/audio/video model variant."""

    id: str
    name: str
    provider: str
    cost: Cost = field(default_factory=Cost)
    thinking: bool = False
    # The exact reasoning-effort values this model's API genuinely accepts (verified
    # against the provider's own docs/model page, not the full ThinkingLevel range) —
    # e.g. GPT-5 Pro only accepts High, o-series models max out at High with no XHigh/
    # Max. Empty means unconfirmed/unconstrained: pickers should fall back to the full
    # enum. Ordered weakest-to-strongest; use `default_thinking_level` for the model's
    # starting level rather than assuming index 0.
    thinking_levels: list[ThinkingLevel] = field(default_factory=list)
    thinking_format: str | None = None
    # True for Anthropic models that use the newer thinking:{type:"adaptive"} +
    # output_config.effort mechanism instead of the older
    # thinking:{type:"enabled", budget_tokens:N} shape. Only meaningful for
    # models hitting an Anthropic-Messages-compatible backend (anthropic_messages.py,
    # anthropic_vertex.py, anthropic_claude_code.py) — ignored elsewhere.
    thinking_adaptive: bool = False
    # True for the subset of adaptive-thinking Anthropic models that reject
    # non-default temperature/top_p/top_k with a 400 error on every request,
    # regardless of whether thinking is active (Fable 5, Opus 4.8, Opus 4.7,
    # Sonnet 5 — NOT Sonnet 4.6 or Opus 4.6, which are adaptive but still
    # accept temperature). Only meaningful for the same 3 Anthropic-Messages-
    # compatible backends as thinking_adaptive.
    thinking_suppresses_sampling: bool = False
    # True for Gemini 3.x models that use the coarse thinking_config.thinking_level
    # enum (MINIMAL/LOW/MEDIUM/HIGH — xhigh and max both collapse to HIGH, there is
    # no distinct tier above it) instead of the older thinking_config.thinking_budget
    # raw token count. Only meaningful for models hitting gemini_generate.py or
    # google_vertex.py — ignored elsewhere (e.g. google-antigravity always uses a
    # raw budget regardless of generation, so this stays False there).
    thinking_uses_level: bool = False
    # True for models on provider="google-antigravity" that are actually
    # Anthropic Claude models proxied through Google's Cloud Code Assist
    # gateway rather than native Gemini models. Google's gateway forces a
    # uniform Gemini-style request envelope on every model regardless of the
    # underlying vendor, so this tells google_antigravity.py where the Claude
    # backend's stricter rules apply: explicit ids on functionCall/
    # functionResponse parts, strict JSON Schema (every object needs an
    # explicit "type"), snake_case thinkingConfig, and the interleaved-
    # thinking beta header. Only meaningful for that one backend.
    antigravity_is_claude: bool = False
    context_window: int = 0
    max_input_tokens: int | None = None
    max_output_tokens: int = 16384
    input: list[Modality] = field(default_factory=list)
    output: list[Modality] = field(default_factory=list)
    voices: list[str] = field(default_factory=list)
    tts_format: str | None = None
    api: str | None = None
    base_url: str | None = None

    @property
    def input_limit(self) -> int:
        """Maximum input/prompt tokens the backend will accept.

        For most models this equals ``context_window``. It differs when a model's
        total window reserves space for output/reasoning (e.g. GPT-5: 400K total =
        272K input + 128K output) or when a proxy enforces a smaller prompt cap
        (e.g. GitHub Copilot caps Claude at 128K). Compaction and overflow detection
        must key off this value — not the total window — so the proactive threshold
        sits below the backend's hard limit instead of above it.
        """
        return self.max_input_tokens or self.context_window

    @property
    def default_thinking_level(self) -> ThinkingLevel | None:
        """Starting reasoning effort for this model: Medium if supported, else the
        middle of its supported range, else None if thinking_levels is unconfirmed/empty.
        """
        if not self.thinking_levels:
            return None
        if ThinkingLevel.Medium in self.thinking_levels:
            return ThinkingLevel.Medium
        return self.thinking_levels[len(self.thinking_levels) // 2]

    @property
    def is_stt(self) -> bool:
        """True for a speech-to-text model (audio in → text out). UI label: Voice."""
        return Modality.Audio in self.input and Modality.Text in self.output

    @property
    def is_tts(self) -> bool:
        """True for a text-to-speech model (text in → audio out). UI label: Speak."""
        return Modality.Text in self.input and Modality.Audio in self.output

    def get_name(self) -> str:
        """Return the human-readable model name."""
        return self.name

    def get_model_id(self) -> str:
        """Return the provider-facing model identifier string."""
        return self.id

    def get_cost(self) -> Cost:
        """Return the per-million-token cost schedule for this model."""
        return self.cost

    def calculate_cost(self, usage: Usage) -> UsageCost:
        """Populate usage.cost from token counts and return it."""
        # Rates are stored per-million; divide before multiplying by actual token count
        usage.cost.input = (self.cost.input / 1_000_000) * usage.input_tokens
        usage.cost.output = (self.cost.output / 1_000_000) * usage.output_tokens
        usage.cost.cache_read = (self.cost.cache_read / 1_000_000) * usage.cache_read_tokens
        _1h_tokens = getattr(usage, "cache_write_1h_tokens", 0) or 0
        _5m_tokens = usage.cache_write_tokens - _1h_tokens
        usage.cost.cache_write = (self.cost.cache_write / 1_000_000) * _5m_tokens + (
            self.cost.input * 2 / 1_000_000
        ) * _1h_tokens
        usage.cost.total = (
            usage.cost.input + usage.cost.output + usage.cost.cache_read + usage.cost.cache_write
        )
        return usage.cost

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
    thinking_level: ThinkingLevel | None = None
    thinking_format: str | None = None
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

"""Configuration for the voice input extension."""

from __future__ import annotations

from dataclasses import dataclass

# How long with no space event before we treat the key as released.
# Must exceed the OS key-repeat initial delay (typically 300–700 ms on macOS/Linux).
# On Kitty terminals (Tau default) the real release event fires immediately and
# cancels the watcher, so this only matters for non-Kitty terminals.
RELEASE_GAP = 1.0


@dataclass
class VoiceConfig:
    enabled: bool = True
    voice_model: str = "whisper-1"
    voice_provider: str = "openai"
    hold_ms: int = 500
    sample_rate: int = 16000

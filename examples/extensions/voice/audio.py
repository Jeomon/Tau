"""Stateless audio helpers: encode captured frames and run speech-to-text.

These are kept free of controller state so they can be reasoned about (and
tested) in isolation. The microphone stream itself lives in the controller,
which owns its lifecycle.
"""

from __future__ import annotations

import io
import wave
from typing import Any


def encode_wav(frames: list[Any], sample_rate: int) -> bytes:
    """Concatenate int16 mono capture frames into a WAV byte string."""
    import numpy as np

    audio_data = np.concatenate(frames, axis=0)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data.tobytes())
    return buf.getvalue()


async def transcribe_wav(wav_bytes: bytes, model_id: str, provider: str) -> str:
    """Transcribe WAV audio via the configured voice model. Returns raw text."""
    from tau.inference.api.audio.service import AudioLLM
    from tau.inference.types import AudioFormat, STTContext

    llm = AudioLLM(model_id, provider)
    result = await llm.transcribe(STTContext(audio=wav_bytes, format=AudioFormat.WAV))
    return result.text

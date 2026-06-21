"""
Top-level access to inference clients and shared types.

Usage:
    from tau.inference import LLM, ImageLLM, AudioLLM, VideoLLM
    from tau.inference import LLMContext, LLMOptions, StopReason
    from tau.inference import ImageContext, ImageOptions, GeneratedImage
    from tau.inference import (
        AudioOptions, TTSContext, STTContext, SynthesizedAudio, TranscribedAudio
    )
    from tau.inference import VideoContext, VideoOptions, GeneratedVideo
"""

from tau.inference.types import (
    AudioFormat,
    # Audio
    AudioOptions,
    AudioStopReason,
    EndEvent,
    ErrorEvent,
    GeneratedImage,
    GeneratedVideo,
    # Image
    ImageContext,
    ImageOptions,
    ImageStopReason,
    # LLM
    LLMContext,
    LLMEvent,
    LLMEventType,
    LLMOptions,
    SegmentTimestamp,
    # LLM events
    StartEvent,
    StopReason,
    StructuredResponseFormat,
    StructuredResponseInput,
    STTContext,
    SynthesizedAudio,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingBudgets,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingLevel,
    ThinkingStartEvent,
    TimestampGranularity,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    TranscribedAudio,
    Transport,
    TTSContext,
    # Video
    VideoContext,
    VideoFormat,
    VideoOptions,
    VideoStopReason,
    WordTimestamp,
    normalize_structured_response_format,
)


def _get_llm_class():
    from tau.inference.api.text.service import LLM

    return LLM


def _get_image_llm_class():
    from tau.inference.api.image.service import ImageLLM

    return ImageLLM


def _get_audio_llm_class():
    from tau.inference.api.audio.service import AudioLLM

    return AudioLLM


def _get_video_llm_class():
    from tau.inference.api.video.service import VideoLLM

    return VideoLLM


class LLM:
    """
    Thin proxy so `from tau.inference import LLM` works without triggering
    circular imports at parse time. Instantiation delegates to the real class.
    """

    def __new__(cls, *args, **kwargs):
        real = _get_llm_class()
        return real(*args, **kwargs)

    @classmethod
    def __class_getitem__(cls, item):
        return _get_llm_class().__class_getitem__(item)


class ImageLLM:
    """
    Thin proxy so `from tau.inference import ImageLLM` works without
    triggering circular imports at parse time.
    """

    def __new__(cls, *args, **kwargs):
        real = _get_image_llm_class()
        return real(*args, **kwargs)


class AudioLLM:
    """
    Thin proxy so `from tau.inference import AudioLLM` works without
    triggering circular imports at parse time.
    """

    def __new__(cls, *args, **kwargs):
        real = _get_audio_llm_class()
        return real(*args, **kwargs)


class VideoLLM:
    """
    Thin proxy so `from tau.inference import VideoLLM` works without
    triggering circular imports at parse time.
    """

    def __new__(cls, *args, **kwargs):
        real = _get_video_llm_class()
        return real(*args, **kwargs)


__all__ = [
    # Clients
    "LLM",
    "ImageLLM",
    "AudioLLM",
    "VideoLLM",
    # LLM context / options
    "LLMContext",
    "LLMEvent",
    "LLMEventType",
    "LLMOptions",
    "StructuredResponseFormat",
    "StructuredResponseInput",
    "normalize_structured_response_format",
    "StopReason",
    "ThinkingLevel",
    "ThinkingBudgets",
    "Transport",
    # LLM events
    "StartEvent",
    "EndEvent",
    "ErrorEvent",
    "TextStartEvent",
    "TextDeltaEvent",
    "TextEndEvent",
    "ThinkingStartEvent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ToolCallStartEvent",
    "ToolCallDeltaEvent",
    "ToolCallEndEvent",
    # Image
    "ImageContext",
    "ImageOptions",
    "GeneratedImage",
    "ImageStopReason",
    # Audio
    "AudioOptions",
    "AudioFormat",
    "AudioStopReason",
    "TimestampGranularity",
    "TTSContext",
    "STTContext",
    "SynthesizedAudio",
    "TranscribedAudio",
    "WordTimestamp",
    "SegmentTimestamp",
    # Video
    "VideoContext",
    "VideoOptions",
    "GeneratedVideo",
    "VideoFormat",
    "VideoStopReason",
]

from tau.inference.provider.types import AudioProvider

providers = [
    AudioProvider(id="openai", name="OpenAI", api="openai-audio"),
    AudioProvider(
        id="openrouter",
        name="OpenRouter",
        api="openai-audio",
        base_url="https://openrouter.ai/api/v1",
    ),
    AudioProvider(
        id="groq", name="Groq", api="openai-audio", base_url="https://api.groq.com/openai/v1"
    ),
    AudioProvider(id="google", name="Google", api="gemini-audio"),
    AudioProvider(id="sarvam", name="Sarvam", api="sarvam-audio", base_url="https://api.sarvam.ai"),
    AudioProvider(
        id="elevenlabs",
        name="ElevenLabs",
        api="elevenlabs-audio",
        base_url="https://api.elevenlabs.io",
    ),
    AudioProvider(
        id="zai",
        name="Z.ai",
        api="openai-audio",
        base_url="https://api.z.ai/api/paas/v4",
    ),
]

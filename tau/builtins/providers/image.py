from tau.inference.provider.types import ImageProvider

providers = [
    ImageProvider(
        id="openrouter",
        name="OpenRouter",
        api="openrouter-image",
        base_url="https://openrouter.ai/api/v1",
    ),
    ImageProvider(
        id="openai", name="OpenAI", api="openai-image", base_url="https://api.openai.com/v1"
    ),
    ImageProvider(
        id="together",
        name="Together AI",
        api="openai-image",
        base_url="https://api.together.xyz/v1",
    ),
    ImageProvider(
        id="fireworks",
        name="Fireworks AI",
        api="openai-image",
        base_url="https://api.fireworks.ai/inference/v1",
    ),
    ImageProvider(
        id="google",
        name="Google",
        api="gemini-image",
        base_url="https://generativelanguage.googleapis.com",
    ),
    ImageProvider(
        id="zai",
        name="Z.ai",
        api="openai-image",
        base_url="https://api.z.ai/api/paas/v4",
    ),
]

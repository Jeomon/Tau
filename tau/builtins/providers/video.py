from tau.inference.provider.types import VideoProvider

providers = [
    VideoProvider(id="fal", name="fal.ai", api="fal-video"),
    VideoProvider(
        id="openrouter",
        name="OpenRouter",
        api="openrouter-video",
        base_url="https://openrouter.ai/api/v1",
    ),
    VideoProvider(
        id="zai",
        name="Z.ai",
        api="zai-video",
        base_url="https://api.z.ai/api/paas/v4",
    ),
]

# Lazy registry entries: each provider API is referenced by a "module:ClassName"
# path so its SDK is only imported when that provider is actually used, not at
# registry construction.
VIDEO_APIS: list[tuple[str, str]] = [
    ("fal-video", "tau.inference.api.video.fal_video:FalVideoAPI"),
    ("openrouter-video", "tau.inference.api.video.openrouter_video:OpenRouterVideoAPI"),
    ("zai-video", "tau.inference.api.video.zai_video:ZaiVideoAPI"),
]

from __future__ import annotations

import asyncio

from tau.inference.model.types import Model
from tau.modes.interactive.commands.context import CommandContext

# (modality, tab label) in display order. "voice" = STT, "speak" = TTS.
_MODALITIES: list[tuple[str, str]] = [
    ("text", "Text"),
    ("voice", "Voice"),
    ("speak", "Speak"),
    ("image", "Image"),
    ("video", "Video"),
]
_MODALITY_ALIASES: dict[str, str] = {"stt": "voice", "tts": "speak", "audio": "voice"}
_MODALITY_DESCRIPTIONS: dict[str, str] = {
    "text": "Chat model",
    "voice": "Speech-to-text (dictation)",
    "speak": "Text-to-speech (read aloud)",
    "image": "Image generation",
    "video": "Video generation",
}


def _list_for(modality: str) -> list[Model]:
    """Return the available models for a modality (auth-filtered)."""
    from tau.inference.api.audio.service import AudioLLM
    from tau.inference.api.image.service import ImageLLM
    from tau.inference.api.text.service import TextLLM
    from tau.inference.api.video.service import VideoLLM

    if modality == "text":
        return TextLLM.list_available()
    if modality in ("voice", "speak"):
        audio = AudioLLM.list_available()
        return [m for m in audio if (m.is_stt if modality == "voice" else m.is_tts)]
    if modality == "image":
        return ImageLLM.list_available()
    if modality == "video":
        return VideoLLM.list_available()
    return []


def _current_key(ctx: CommandContext, modality: str) -> str:
    """Return ``provider/id`` of the current selection for a modality, or ""."""
    if modality == "text":
        # Prefer the live agent model (most accurate); fall back to settings.
        llm = (
            getattr(getattr(ctx.runtime.agent, "_engine", None), "llm", None)
            if ctx.runtime.agent
            else None
        )
        model = getattr(llm, "model", None) if llm is not None else None
        if model is not None:
            return f"{model.provider}/{model.id}"
    sm = ctx.runtime.settings_manager
    ref = sm.get_model_ref(modality) if sm is not None else None
    return f"{ref.provider}/{ref.id}" if ref is not None and ref.id else ""


def modality_completions(prefix: str) -> list:
    """Autocomplete for the ``/model <modality>`` argument."""
    from tau.tui.autocomplete import AutocompleteItem

    p = prefix.lower()
    return [
        AutocompleteItem(label=mod, description=_MODALITY_DESCRIPTIONS[mod])
        for mod, _label in _MODALITIES
        if mod.startswith(p)
    ]


def open_model_selector(ctx: CommandContext, modality: str | None = None) -> None:
    """Open the tabbed model selector, optionally focused on ``modality``."""
    sections: list[tuple[str, str, list, str]] = []
    models_by_key: dict[tuple[str, str, str], Model] = {}
    for mod, label in _MODALITIES:
        try:
            models = _list_for(mod)
        except Exception:
            models = []
        if models:
            sections.append((mod, label, models, _current_key(ctx, mod)))
            models_by_key.update({(mod, model.provider, model.id): model for model in models})

    if not sections:
        ctx.notify("No models available. Use /login to add providers.")
        return

    initial = _MODALITY_ALIASES.get(modality, modality) if modality else None

    def commit(value: tuple[str, str, str]) -> None:
        model_id, provider, mod = value
        model = models_by_key.get((mod, provider, model_id))
        voices = list(getattr(model, "voices", []))
        if mod == "speak" and voices:
            current_ref = (
                ctx.runtime.settings_manager.get_model_ref("speak")
                if ctx.runtime.settings_manager is not None
                else None
            )
            current_voice = (
                current_ref.voice
                if current_ref is not None
                and current_ref.id == model_id
                and current_ref.provider == provider
                else None
            )

            def apply_voice(voice: str) -> None:
                asyncio.ensure_future(
                    _apply_model(ctx, mod, model_id, provider, voice=voice)
                )

            ctx.layout.open_voice_selector(
                getattr(model, "name", None) or model_id,
                voices,
                current_voice,
                apply_voice,
                lambda: ctx.notify("Voice selection cancelled."),
            )
            return
        asyncio.ensure_future(_apply_model(ctx, mod, model_id, provider))

    ctx.layout.open_model_selector(
        sections,
        commit,
        lambda: ctx.notify("Model selection cancelled."),
        initial=initial,
    )


async def _apply_model(
    ctx: CommandContext,
    modality: str,
    model_id: str,
    provider: str,
    *,
    voice: str | None = None,
) -> None:
    try:
        if modality == "text":
            # Live-swap the chat model (this also persists to settings).
            await ctx.runtime.set_model(model_id, provider)
            ctx.notify(f"Model set to {provider}/{model_id}")
            if ctx.on_palette_refresh is not None:
                ctx.on_palette_refresh()
            return
        sm = ctx.runtime.settings_manager
        if sm is not None:
            sm.set_model_ref(modality, provider, model_id, voice=voice)
        label = dict(_MODALITIES).get(modality, modality)
        voice_label = f" with voice {voice}" if voice else ""
        ctx.notify(f"{label} model set to {provider}/{model_id}{voice_label}")
    except Exception as exc:
        ctx.notify(f"Failed to set model: {exc}")


def open_effort_selector(ctx: CommandContext) -> None:
    from tau.inference.types import ThinkingLevel

    agent = ctx.runtime.agent
    model = None
    if agent is not None:
        model = getattr(agent._engine.llm, "model", None)

    if model is None or not getattr(model, "thinking", False):
        ctx.notify("Model doesn't support thinking")
        return

    current_level = None
    if agent is not None:
        current_level = agent._engine.llm.api.options.thinking_level
    current = current_level if current_level is not None else ThinkingLevel.Off

    levels = list(ThinkingLevel)

    def commit(level: ThinkingLevel) -> None:
        asyncio.ensure_future(_apply_effort(ctx, level.value))

    ctx.layout.open_effort_selector(
        levels, current, commit, lambda: ctx.notify("Effort selection cancelled.")
    )


def get_palette_overrides(agent: object) -> dict[str, str]:
    """Return dynamic palette description overrides for /model and /effort."""
    overrides: dict[str, str] = {}

    llm = getattr(getattr(agent, "_engine", None), "llm", None) if agent is not None else None
    model = getattr(llm, "model", None) if llm is not None else None

    if model is not None:
        provider = getattr(model, "provider", "") or ""
        model_id = getattr(model, "id", "") or ""
        overrides["model"] = f"{provider}/{model_id}" if provider else model_id
    else:
        overrides["model"] = "Switch the active model"

    effort_val = None
    if llm is not None:
        opts = getattr(getattr(llm, "api", None), "options", None)
        if opts is not None:
            level = getattr(opts, "thinking_level", None)
            if level is not None:
                effort_val = getattr(level, "value", str(level))
    overrides["effort"] = effort_val if effort_val is not None else "Set thinking effort level"

    return overrides


async def _apply_effort(ctx: CommandContext, level_val: str) -> None:
    from tau.hooks.tui import ThinkingLevelSelectEvent
    from tau.inference.types import ThinkingLevel

    agent = ctx.runtime.agent
    if agent is None:
        return
    previous_level = agent._engine.llm.api.options.thinking_level
    level = ThinkingLevel(level_val)
    agent._engine.llm.api.options.thinking_level = None if level == ThinkingLevel.Off else level

    sm = ctx.runtime.session_manager
    if sm is not None:
        sm.append_thinking_level_change(level)

    settings = ctx.runtime.settings_manager
    if settings is not None:
        settings.set_thinking_level(level)

    await ctx.runtime.hooks.emit(
        ThinkingLevelSelectEvent(level=level, previous_level=previous_level)
    )

    ctx.notify(f"Effort set to {level_val}")
    if ctx.on_palette_refresh is not None:
        ctx.on_palette_refresh()

"""Tests for tau/settings/manager.py — in-memory settings manager."""

from __future__ import annotations

import asyncio

from tau.engine.types import FollowupMode, SteeringMode
from tau.inference.types import ThinkingLevel, Transport
from tau.settings.manager import SettingsManager


def _manager(data: dict | None = None) -> SettingsManager:
    return SettingsManager.in_memory(data or {})


def _set_and_persist(mgr: SettingsManager, *calls: tuple) -> None:
    """Run set_model_ref calls inside a loop and await the async write queue.

    ``set_*`` enqueues an asyncio task, so it must run with a running loop.
    """

    async def _run() -> None:
        for modality, provider, model_id in calls:
            mgr.set_model_ref(modality, provider, model_id)
        if mgr._write_queue is not None:
            await mgr._write_queue

    asyncio.run(_run())


def _ref(mgr: SettingsManager, modality: str):
    """Return a non-None ModelRef for the modality (asserts presence)."""
    ref = mgr.get_model_ref(modality)
    assert ref is not None, f"expected a model ref for {modality!r}"
    return ref


class TestInMemoryFactory:
    def test_creates_without_error(self):
        assert _manager() is not None

    def test_is_project_trusted_default(self):
        assert _manager().is_project_trusted() is True

    def test_drain_errors_empty(self):
        assert _manager().drain_errors() == []

    def test_drain_errors_cleared(self):
        mgr = _manager()
        mgr.drain_errors()
        assert mgr.drain_errors() == []


class TestSeedData:
    def test_seeded_model(self):
        mgr = _manager({"model": {"text": {"id": "claude-sonnet"}}})
        assert _ref(mgr, "text").id == "claude-sonnet"

    def test_seeded_theme(self):
        mgr = _manager({"theme": "monokai"})
        assert mgr.get_theme() == "monokai"

    def test_seeded_provider(self):
        mgr = _manager({"model": {"text": {"provider": "anthropic"}}})
        assert _ref(mgr, "text").provider == "anthropic"

    def test_legacy_flat_model_provider_coerced_to_text(self):
        # Old config files stored model/provider as flat strings.
        mgr = _manager({"model": "gpt-oss:120b-cloud", "provider": "ollama"})
        ref = _ref(mgr, "text")
        assert ref.id == "gpt-oss:120b-cloud"
        assert ref.provider == "ollama"

    def test_legacy_model_does_not_break_other_slots(self):
        # Setting another modality on top of a legacy config must not crash.
        mgr = _manager({"model": "claude", "provider": "anthropic"})
        _set_and_persist(mgr, ("voice", "openai", "whisper-1"))
        assert _ref(mgr, "voice").id == "whisper-1"
        assert _ref(mgr, "text").id == "claude"

    def test_seeded_thinking_level(self):
        mgr = _manager({"thinking_level": "high"})
        assert mgr.get_thinking_level() == ThinkingLevel.High

    def test_seeded_transport(self):
        mgr = _manager({"transport": "http"})
        assert mgr.get_transport() == Transport.HTTP

    def test_seeded_steering_mode(self):
        mgr = _manager({"steering_mode": "all"})
        assert mgr.get_steering_mode() == SteeringMode.All

    def test_seeded_follow_up_mode(self):
        mgr = _manager({"follow_up_mode": "all"})
        assert mgr.get_follow_up_mode() == FollowupMode.All

    def test_seeded_quiet_startup(self):
        mgr = _manager({"quiet_startup": True})
        assert mgr.get_quiet_startup() is True

    def test_telemetry_defaults_enabled(self):
        assert _manager().get_telemetry() is True

    def test_seeded_telemetry_disabled(self):
        mgr = _manager({"telemetry": False})
        assert mgr.get_telemetry() is False

    def test_seeded_show_thinking(self):
        mgr = _manager({"show_thinking": False})
        assert mgr.get_show_thinking() is False

    def test_seeded_show_tool_calls(self):
        mgr = _manager({"show_tool_calls": False})
        assert mgr.get_show_tool_calls() is False

    def test_seeded_enabled_models(self):
        mgr = _manager({"enabled_models": ["gpt-4o", "claude-*"]})
        assert mgr.get_enabled_models() == ["gpt-4o", "claude-*"]


class TestApplyOverrides:
    """apply_overrides() mutates the merged settings without triggering async I/O."""

    def test_override_theme(self):
        mgr = _manager({"theme": "light"})
        mgr.apply_overrides({"theme": "dark"})
        assert mgr.get_theme() == "dark"

    def test_override_model(self):
        mgr = _manager()
        mgr.apply_overrides({"model": {"text": {"id": "gpt-4o"}}})
        assert _ref(mgr, "text").id == "gpt-4o"

    def test_override_provider(self):
        mgr = _manager()
        mgr.apply_overrides({"model": {"text": {"provider": "openai"}}})
        assert _ref(mgr, "text").provider == "openai"

    def test_override_thinking_level(self):
        mgr = _manager()
        mgr.apply_overrides({"thinking_level": "max"})
        assert mgr.get_thinking_level() == ThinkingLevel.Max

    def test_multiple_overrides(self):
        mgr = _manager()
        mgr.apply_overrides({"theme": "dark", "model": {"text": {"id": "claude-sonnet"}}})
        assert mgr.get_theme() == "dark"
        assert _ref(mgr, "text").id == "claude-sonnet"

    def test_override_does_not_affect_global_settings(self):
        mgr = _manager({"theme": "light"})
        mgr.apply_overrides({"theme": "dark"})
        # Global settings object itself should be unchanged
        assert mgr.get_global_settings().theme == "light"


class TestGlobalAndProjectSettings:
    def test_get_global_settings_returns_settings(self):
        mgr = _manager({"theme": "dark"})
        assert mgr.get_global_settings() is not None

    def test_get_project_settings_returns_settings(self):
        mgr = _manager()
        assert mgr.get_project_settings() is not None

    def test_global_settings_deep_copy(self):
        mgr = _manager({"theme": "dark"})
        g1 = mgr.get_global_settings()
        g2 = mgr.get_global_settings()
        assert g1 is not g2


class TestDefaultValues:
    def test_retry_max_retries_has_default(self):
        mgr = _manager()
        assert isinstance(mgr.get_retry_max_retries(), int)

    def test_compaction_reserve_tokens_has_default(self):
        mgr = _manager()
        assert mgr.get_compaction_reserve_tokens() > 0

    def test_compaction_keep_recent_tokens_has_default(self):
        mgr = _manager()
        assert mgr.get_compaction_keep_recent_tokens() > 0

    def test_branch_summary_reserve_tokens_has_default(self):
        mgr = _manager()
        assert mgr.get_branch_summary_reserve_tokens() > 0

    def test_thinking_budget_high_has_default(self):
        mgr = _manager()
        assert mgr.get_thinking_budget("high") > 0

    def test_http_idle_timeout_ms_has_default(self):
        mgr = _manager()
        assert mgr.get_http_idle_timeout_ms() > 0


class TestModelRefSlots:
    def test_text_slot_reads(self):
        mgr = _manager({"model": {"text": {"id": "claude-opus-4-8", "provider": "anthropic"}}})
        ref = mgr.get_model_ref("text")
        assert ref is not None
        assert ref.id == "claude-opus-4-8"
        assert ref.provider == "anthropic"

    def test_text_slot_none_when_unset(self):
        assert _manager().get_model_ref("text") is None

    def test_seeded_voice_slot(self):
        mgr = _manager({"model": {"voice": {"id": "whisper-1", "provider": "openai"}}})
        ref = mgr.get_model_ref("voice")
        assert ref is not None
        assert ref.id == "whisper-1"
        assert ref.provider == "openai"

    def test_stt_alias_maps_to_voice(self):
        mgr = _manager({"model": {"voice": {"id": "whisper-1", "provider": "openai"}}})
        assert mgr.get_model_ref("stt") == mgr.get_model_ref("voice")

    def test_tts_alias_maps_to_speak(self):
        mgr = _manager({"model": {"speak": {"id": "tts-1", "provider": "openai", "voice": "nova"}}})
        assert mgr.get_model_ref("tts") == mgr.get_model_ref("speak")
        assert _ref(mgr, "speak").voice == "nova"

    def test_unset_slot_returns_none(self):
        assert _manager().get_model_ref("video") is None

    def test_unknown_modality_raises(self):
        import pytest

        with pytest.raises(ValueError):
            _manager().get_model_ref("hologram")

    def test_set_text_ref(self):
        mgr = _manager()
        _set_and_persist(mgr, ("text", "anthropic", "claude-opus-4-8"))
        ref = _ref(mgr, "text")
        assert ref.id == "claude-opus-4-8"
        assert ref.provider == "anthropic"

    def test_set_voice_and_speak_roundtrip_via_storage(self):
        mgr = _manager()
        _set_and_persist(
            mgr,
            ("stt", "openai", "whisper-1"),
            ("tts", "openai", "tts-1"),
            ("image", "openai", "gpt-image-1"),
        )
        # Reload from the same storage to confirm it persisted in the nested shape.
        reloaded = SettingsManager.from_storage(mgr.storage)
        assert _ref(reloaded, "voice").id == "whisper-1"
        assert _ref(reloaded, "speak").id == "tts-1"
        assert _ref(reloaded, "image").provider == "openai"
        assert reloaded.get_model_ref("video") is None

    def test_set_speak_voice_roundtrip_via_storage(self):
        mgr = _manager()

        async def _run() -> None:
            mgr.set_model_ref("speak", "openai", "tts-1", voice="coral")
            if mgr._write_queue is not None:
                await mgr._write_queue

        asyncio.run(_run())
        reloaded = SettingsManager.from_storage(mgr.storage)
        assert _ref(reloaded, "speak").voice == "coral"

    def test_set_one_slot_preserves_others(self):
        mgr = _manager({"model": {"voice": {"id": "whisper-1", "provider": "openai"}}})
        _set_and_persist(mgr, ("speak", "openai", "tts-1"))
        reloaded = SettingsManager.from_storage(mgr.storage)
        assert _ref(reloaded, "voice").id == "whisper-1"  # untouched
        assert _ref(reloaded, "speak").id == "tts-1"


def test_engine_execution_settings_load_from_json() -> None:
    mgr = _manager(
        {
            "tool_timeout_seconds": 45.0,
            "max_parallel_tool_calls": 3,
            "event_handler_timeout_seconds": 4.0,
        }
    )

    assert mgr.settings.tool_timeout_seconds == 45.0
    assert mgr.settings.max_parallel_tool_calls == 3
    assert mgr.settings.event_handler_timeout_seconds == 4.0

"""Tests for the /login provider union (tau/tui/commands/auth.py)."""

from __future__ import annotations

from tau.modes.interactive.commands.auth import _all_providers


def _by_id() -> dict[str, tuple]:
    return {p[0]: p for p in _all_providers()}


class TestAllProviders:
    def test_includes_non_text_providers(self):
        ids = _by_id()
        # audio/image/video-only providers must now be loginable
        for pid in ("elevenlabs", "sarvam", "together", "fal"):
            assert pid in ids, f"{pid} missing from /login provider union"

    def test_text_providers_keep_display_names(self):
        ids = _by_id()
        # shared providers merge under the text entry (richer display name)
        assert ids["openai"][1] == "OpenAI"
        assert ids["google"][1] == "Google"

    def test_non_text_providers_have_pretty_names(self):
        ids = _by_id()
        assert ids["elevenlabs"][1] == "ElevenLabs"
        assert ids["sarvam"][1] == "Sarvam"
        assert ids["together"][1] == "Together AI"
        assert ids["fal"][1] == "fal.ai"

    def test_deduped_by_id(self):
        provs = _all_providers()
        seen = [p[0] for p in provs]
        assert len(seen) == len(set(seen))  # openai appears once despite multiple modalities

    def test_non_text_providers_are_api_key(self):
        ids = _by_id()
        for pid in ("elevenlabs", "sarvam", "together", "fal"):
            _id, _name, is_oauth, needs_key = ids[pid]
            assert is_oauth is False
            assert needs_key is True

"""Tests for the tabbed ModelSelectorModal (step 3)."""

from __future__ import annotations

from tau.inference.model.types import Cost, Modality, Model
from tau.modes.interactive.components.model_selector import ModelSelectorModal


def _m(
    id: str,
    provider: str,
    *,
    input_modalities: list[Modality] | None = None,
    output_modalities: list[Modality] | None = None,
    thinking: bool = False,
) -> Model:
    return Model(
        id=id,
        name=id,
        provider=provider,
        cost=Cost(),
        context_window=128_000,
        thinking=thinking,
        input=input_modalities or [Modality.Text, Modality.Image],
        output=output_modalities or [Modality.Text],
    )


def _sv(modal: ModelSelectorModal) -> tuple[str, str, str]:
    """selected_value asserted non-None (for type-narrowing in assertions)."""
    val = modal.selected_value()
    assert val is not None
    return val


def _sec(modal: ModelSelectorModal):
    """Active section asserted non-None (for type-narrowing in assertions)."""
    sec = modal._section
    assert sec is not None
    return sec


def _sections():
    return [
        ("text", "Text", [_m("claude", "anthropic"), _m("gpt-4o", "openai")], "anthropic/claude"),
        (
            "voice",
            "Voice",
            [
                _m(
                    "whisper-1",
                    "openai",
                    input_modalities=[Modality.Audio],
                    output_modalities=[Modality.Text],
                ),
                _m(
                    "saarika",
                    "sarvam",
                    input_modalities=[Modality.Audio],
                    output_modalities=[Modality.Text],
                ),
            ],
            "",
        ),
        (
            "speak",
            "Speak",
            [
                _m(
                    "tts-1",
                    "openai",
                    input_modalities=[Modality.Text],
                    output_modalities=[Modality.Audio],
                )
            ],
            "",
        ),
        ("image", "Image", [], ""),  # empty → dropped
    ]


class TestSections:
    def test_empty_sections_dropped(self):
        modal = ModelSelectorModal(_sections())
        # text, voice, speak survive; image (empty) is dropped
        assert [s.modality for s in modal._sections] == ["text", "voice", "speak"]

    def test_default_active_is_first(self):
        modal = ModelSelectorModal(_sections())
        assert _sv(modal)[2] == "text"

    def test_initial_selects_tab(self):
        modal = ModelSelectorModal(_sections(), initial="voice")
        assert _sv(modal)[2] == "voice"

    def test_initial_unknown_falls_back_to_first(self):
        modal = ModelSelectorModal(_sections(), initial="hologram")
        assert _sv(modal)[2] == "text"


class TestNavigation:
    def test_next_prev_section_wraps(self):
        modal = ModelSelectorModal(_sections())  # text, voice, speak
        modal.next_section()
        assert _sv(modal)[2] == "voice"
        modal.prev_section()
        assert _sv(modal)[2] == "text"
        modal.prev_section()  # wrap backwards
        assert _sv(modal)[2] == "speak"

    def test_move_down_changes_selection(self):
        modal = ModelSelectorModal(_sections(), initial="voice")
        first = modal.selected_value()
        modal.move_down()
        assert modal.selected_value() != first
        assert _sv(modal)[2] == "voice"


class TestSelectedValue:
    def test_returns_id_provider_modality(self):
        modal = ModelSelectorModal(_sections(), initial="speak")
        assert modal.selected_value() == ("tts-1", "openai", "speak")

    def test_current_model_preselected_in_text(self):
        modal = ModelSelectorModal(_sections())
        # text section's current_key is anthropic/claude → preselected
        assert modal.selected_value() == ("claude", "anthropic", "text")


class TestSearchIsolation:
    def test_search_filters_active_section_only(self):
        modal = ModelSelectorModal(_sections(), initial="voice")
        for ch in "whisper":
            modal.append_search(ch)
        assert modal.selected_value() == ("whisper-1", "openai", "voice")
        # switching tabs leaves the other tab unfiltered
        modal.prev_section()  # → text
        assert _sv(modal)[2] == "text"
        assert _sv(modal)[0] == "claude"

    def test_backspace_search(self):
        modal = ModelSelectorModal(_sections(), initial="voice")
        for ch in "zzz":
            modal.append_search(ch)
        assert modal.selected_value() is None  # nothing matches
        for _ in range(3):
            modal.backspace_search()
        assert modal.selected_value() is not None  # restored


class TestScope:
    def test_multi_provider_tab_can_scope(self):
        # voice tab spans openai + sarvam
        modal = ModelSelectorModal(_sections(), initial="voice")
        assert _sec(modal).can_scope is True

    def test_single_provider_tab_cannot_scope(self):
        # speak tab has only openai
        modal = ModelSelectorModal(_sections(), initial="speak")
        assert _sec(modal).can_scope is False
        modal.toggle_scope()  # no-op
        assert _sec(modal).scope == "all"

    def test_toggle_scopes_to_highlighted_provider(self):
        modal = ModelSelectorModal(_sections(), initial="voice")
        # highlighted is whisper-1 (openai); scoping filters to openai only
        assert _sv(modal)[0] == "whisper-1"
        modal.toggle_scope()
        assert _sec(modal).scope == "scoped"
        assert _sec(modal).scope_provider == "openai"
        assert {m.provider for m in _sec(modal).filtered} == {"openai"}
        modal.toggle_scope()  # back to all
        assert _sec(modal).scope == "all"

    def test_scope_is_per_tab(self):
        modal = ModelSelectorModal(_sections(), initial="voice")
        modal.toggle_scope()  # voice → scoped
        modal.prev_section()  # → text
        assert _sec(modal).modality == "text"
        # text starts scoped to its current model's provider (anthropic)
        assert _sec(modal).scope == "scoped"


class TestRender:
    def test_tab_strip_lists_modalities(self):
        modal = ModelSelectorModal(_sections())
        out = "\n".join(modal.render(80))
        assert "Text" in out and "Voice" in out and "Speak" in out
        assert "tab: modality" in out

    def test_text_model_shows_context_and_modalities(self):
        modal = ModelSelectorModal(_sections())
        lines = modal.render(100)
        out = "\n".join(lines)
        detail_line = next(line for line in lines if "Context: 128K" in line)
        assert "Modalities: text+image → text" in detail_line
        assert "(thinking)" not in out

    def test_reasoning_text_model_shows_capability(self):
        sections = [
            (
                "text",
                "Text",
                [_m("reasoning-model", "provider", thinking=True)],
                "",
            )
        ]
        out = "\n".join(ModelSelectorModal(sections).render(100))
        assert "reasoning-model (thinking)" in out
        assert "Reasoning:" not in out

    def test_non_text_model_shows_modalities_without_context(self):
        modal = ModelSelectorModal(_sections(), initial="voice")
        out = "\n".join(modal.render(100))
        assert "Context:" not in out
        assert "Modalities: audio → text" in out

    def test_speak_model_shows_text_to_audio(self):
        modal = ModelSelectorModal(_sections(), initial="speak")
        out = "\n".join(modal.render(100))
        assert "Modalities: text → audio" in out

    def test_image_and_video_models_show_output_modality(self):
        sections = [
            (
                "image",
                "Image",
                [
                    _m(
                        "image-model",
                        "provider",
                        input_modalities=[Modality.Text, Modality.Image],
                        output_modalities=[Modality.Image],
                    )
                ],
                "",
            ),
            (
                "video",
                "Video",
                [
                    _m(
                        "video-model",
                        "provider",
                        input_modalities=[Modality.Text, Modality.Image],
                        output_modalities=[Modality.Video],
                    )
                ],
                "",
            ),
        ]
        image_out = "\n".join(ModelSelectorModal(sections, initial="image").render(100))
        video_out = "\n".join(ModelSelectorModal(sections, initial="video").render(100))
        assert "Modalities: text+image → image" in image_out
        assert "Modalities: text+image → video" in video_out

    def test_render_shows_active_section_models(self):
        modal = ModelSelectorModal(_sections(), initial="voice")
        out = "\n".join(modal.render(80))
        assert "whisper-1" in out

    def test_render_empty_when_no_sections(self):
        modal = ModelSelectorModal([("image", "Image", [], "")])
        out = "\n".join(modal.render(80))
        assert "No models available" in out
        assert modal.selected_value() is None

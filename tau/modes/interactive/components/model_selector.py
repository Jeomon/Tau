from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from tau.inference.model.types import Modality
from tau.tui.ansi_bridge import row_to_ansi
from tau.tui.buffer import Buffer
from tau.tui.geometry import Rect
from tau.tui.style import Style, apply_style
from tau.tui.text import Line, Span
from tau.tui.widgets.list import List, ListItem, ListState
from tau.tui.widgets.tabs import Tabs

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme

VISIBLE_ROWS = 10


def _format_token_count(tokens: int) -> str:
    """Format a token count compactly for the model picker."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.0f}K"
    return str(tokens)


def _format_modalities(modalities: list[Modality]) -> str:
    """Format model modalities as a compact plus-separated label."""
    return "+".join(modality.value for modality in modalities) or "unknown"


class _Section:
    """One modality tab: owns its own search, scope toggle, and selection state.

    Scope:
      "scoped" = only models from one provider (the current model's provider, or
                 the highlighted model's provider when toggled on)
      "all"    = every available model for this modality

    The toggle is offered on any tab with more than one provider, so it works for
    voice/speak/image/video too — not just text.
    """

    def __init__(self, modality: str, label: str, models: list, current_key: str) -> None:
        self.modality = modality
        self.label = label
        self.all_models: list = list(models)
        self.current_key = current_key
        self.providers: list[str] = []
        for m in self.all_models:
            p = m.provider or ""
            if p not in self.providers:
                self.providers.append(p)

        # Start scoped to the current model's provider when there is one.
        current_provider = current_key.split("/")[0] if "/" in current_key else ""
        if current_provider and current_provider in self.providers:
            self.scope: str = "scoped"
            self.scope_provider: str = current_provider
        else:
            self.scope = "all"
            self.scope_provider = ""

        self.search: str = ""
        self.selected: int = 0
        self.filtered: list = []
        self._apply_filter(jump_to_current=True)

    @property
    def can_scope(self) -> bool:
        """Scoping is only meaningful when the tab spans multiple providers."""
        return len(self.providers) > 1

    @property
    def active(self) -> list:
        if self.scope == "scoped" and self.scope_provider:
            return [m for m in self.all_models if (m.provider or "") == self.scope_provider]
        return self.all_models

    def move_up(self) -> None:
        if self.filtered:
            self.selected = (self.selected - 1) % len(self.filtered)

    def move_down(self) -> None:
        if self.filtered:
            self.selected = (self.selected + 1) % len(self.filtered)

    def toggle_scope(self) -> None:
        if not self.can_scope:
            return
        if self.scope == "all":
            # Scope to the provider of the model currently under the cursor.
            provider = self.filtered[self.selected].provider if self.filtered else ""
            if provider:
                self.scope = "scoped"
                self.scope_provider = provider
        else:
            self.scope = "all"
        self.search = ""
        self._apply_filter(jump_to_current=True)

    def append_search(self, ch: str) -> None:
        self.search += ch
        self._apply_filter()

    def backspace_search(self) -> None:
        self.search = self.search[:-1]
        self._apply_filter()

    def selected_value(self) -> tuple[str, str] | None:
        if not self.filtered:
            return None
        m = self.filtered[self.selected]
        return (m.id, m.provider)

    def _apply_filter(self, jump_to_current: bool = False) -> None:
        q = self.search.lower()
        if not q:
            self.filtered = list(self.active)
        else:
            self.filtered = [
                m
                for m in self.active
                if q in (m.id or "").lower()
                or q in (m.name or "").lower()
                or q in f"{m.provider}/{m.id}".lower()
            ]
        if not self.filtered:
            self.selected = 0
            return
        if jump_to_current:
            self.selected = 0
            for i, m in enumerate(self.filtered):
                if f"{m.provider}/{m.id}" == self.current_key:
                    self.selected = i
                    break
        else:
            self.selected = min(self.selected, len(self.filtered) - 1)


class ModelSelector:
    """Tabbed model selector — one tab per modality.

    Owns the modality tabs (Text / Voice / Speak / Image / Video), and per-tab
    search, scope toggle, navigation, and rendering. Designed to be wrapped in
    InlineSelector(kind="model").

    Keys (handled by the layout): ↑/↓ navigate the list, ←/→ toggle provider
    scope, Tab switches modality, Enter selects, Esc cancels.

    Visual:
      Text │ Voice │ Speak │ Image
      Scope: all | scoped  ←/→: toggle
      Search: <query>█
      → whisper-1 [openai] ✓
        gpt-4o-transcribe [openai]
      (1/6)
      Model Name: GPT-5 (thinking)
      Context: 128K · Modalities: text+image → text
    """

    def __init__(
        self,
        sections: list[tuple[str, str, list, str]],
        initial: str | None = None,
        theme: LayoutTheme | None = None,
    ):
        """``sections`` is a list of ``(modality, label, models, current_key)``.

        Empty sections (no models) are dropped. ``initial`` selects the starting
        tab by modality key; defaults to the first non-empty section.
        """
        self._sections: list[_Section] = [
            _Section(modality, label, models, current_key)
            for (modality, label, models, current_key) in sections
            if models
        ]
        self._active: int = 0

        if theme is None:
            from tau.tui.theme import LayoutTheme as _LT

            theme = _LT()
        self._muted = theme.muted
        self._emphasis = theme.emphasis
        self._success = theme.success
        self._accent = theme.accent
        self._border = theme.border
        if initial is not None:
            for i, s in enumerate(self._sections):
                if s.modality == initial:
                    self._active = i
                    break

    @property
    def _section(self) -> _Section | None:
        return self._sections[self._active] if self._sections else None

    # ── Navigation ────────────────────────────────────────────────────────────

    def move_up(self) -> None:
        if self._section:
            self._section.move_up()

    def move_down(self) -> None:
        if self._section:
            self._section.move_down()

    def next_section(self) -> None:
        if self._sections:
            self._active = (self._active + 1) % len(self._sections)

    def prev_section(self) -> None:
        if self._sections:
            self._active = (self._active - 1) % len(self._sections)

    def toggle_scope(self) -> None:
        if self._section:
            self._section.toggle_scope()

    def append_search(self, ch: str) -> None:
        if self._section:
            self._section.append_search(ch)

    def backspace_search(self) -> None:
        if self._section:
            self._section.backspace_search()

    # ── Value ─────────────────────────────────────────────────────────────────

    def selected_value(self) -> tuple[str, str, str] | None:
        """Return ``(model_id, provider, modality)`` for the active selection."""
        sec = self._section
        if sec is None:
            return None
        val = sec.selected_value()
        return (val[0], val[1], sec.modality) if val is not None else None

    # ── Render ────────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:
        muted = partial(apply_style, self._muted)
        emphasis = partial(apply_style, self._emphasis)
        border = partial(apply_style, self._border)
        divider = border("─" * width)
        lines: list[str] = []

        sec = self._section
        if sec is None:
            lines.append("  " + muted("No models available. Use /login to add providers."))
            return lines

        # ── Tab bar (modality sections) ────────────────────────────────────────
        titles = [
            f"[{s.label}]" if i == self._active else s.label
            for i, s in enumerate(self._sections)
        ]
        tabs_buf = Buffer.empty(Rect(0, 0, width, 1))
        Tabs(
            titles=titles,
            selected=self._active,
            style=self._muted,
            highlight_style=self._emphasis,
            divider="  ",
        ).render(Rect(2, 0, max(0, width - 2), 1), tabs_buf)
        lines.append(row_to_ansi(tabs_buf, 0))
        lines.append(divider)

        # ── Scope row (only when tab spans multiple providers) ─────────────────
        if sec.can_scope:
            if sec.scope == "scoped" and sec.scope_provider:
                scope_label = f"scoped ({sec.scope_provider})"
            elif sec.scope == "scoped":
                scope_label = "scoped"
            else:
                scope_label = "all"
            lines.append(f"  {muted('←')}  {emphasis(scope_label)}  {muted('→')}")

        # ── Search box ─────────────────────────────────────────────────────────
        if sec.search:
            lines.append(f"  {muted('⊘')} {sec.search}█")
        else:
            lines.append("  " + muted("⊘ Search models…"))
        lines.append(divider)

        # ── Model list ─────────────────────────────────────────────────────────
        if not sec.filtered:
            lines.append("  " + muted("No models match"))
        else:
            count = len(sec.filtered)
            visible = min(VISIBLE_ROWS, count)
            start = max(0, min(sec.selected - visible // 2, count - visible))

            if start > 0:
                lines.append("  " + muted(f"↑ {start} more above"))

            max_id = min(36, max(len(m.id) for m in sec.filtered[start : start + visible]))
            list_items: list[ListItem] = []
            for i in range(start, start + visible):
                m = sec.filtered[i]
                is_sel = i == sec.selected
                is_current = f"{m.provider}/{m.id}" == sec.current_key
                badge = f"[{m.provider}]" if m.provider else ""
                model_id = m.id.ljust(max_id)

                if is_sel:
                    spans = [
                        Span("  ", Style()),
                        Span(">", self._accent),
                        Span(" ", Style()),
                        Span(model_id, self._emphasis),
                        Span("  ", Style()),
                        Span(badge, self._accent),
                    ]
                else:
                    spans = [
                        Span("    ", Style()),
                        Span(model_id, self._muted),
                        Span("  ", Style()),
                        Span(badge, self._muted),
                    ]
                if is_current:
                    spans.append(Span(" ", Style()))
                    spans.append(Span("✓", self._success))
                list_items.append(ListItem(Line(spans)))

            state = ListState()
            state.select(sec.selected - start)
            state.offset = 0
            list_buf = Buffer.empty(Rect(0, 0, width, visible))
            List(items=list_items, highlight_symbol="", highlight_style=Style()).render(
                Rect(0, 0, width, visible), list_buf, state
            )
            lines.extend(row_to_ansi(list_buf, y) for y in range(visible))

            remaining = count - (start + visible)
            if remaining > 0:
                lines.append("  " + muted(f"↓ {remaining} more below"))

        lines.append(divider)

        # ── Selected model name ────────────────────────────────────────────────
        if sec.filtered:
            sel_m = sec.filtered[sec.selected]
            name = getattr(sel_m, "name", None) or sel_m.id
            if sec.modality == "text" and sel_m.thinking:
                name += " (thinking)"
            lines.append("  " + emphasis(name))
            inputs = _format_modalities(sel_m.input)
            outputs = _format_modalities(sel_m.output)
            if sec.modality == "text":
                context = _format_token_count(sel_m.context_window)
                lines.append(f"  Context: {context} · Modalities: {inputs} → {outputs}")
            else:
                lines.append(f"  Modalities: {inputs} → {outputs}")
        else:
            lines.append("  " + muted("—"))
        lines.append(divider)

        # ── Status bar ─────────────────────────────────────────────────────────
        scope_hint = "  ·  ←/→: scope" if sec.can_scope else ""
        tab_hint = "  ·  tab: modality" if len(self._sections) > 1 else ""
        lines.append("  " + muted(f"Enter: select{scope_hint}{tab_hint}  ·  Esc: cancel"))

        return lines


# Backward-compatible public name retained for extensions built before the
# selector-controller refactor.
ModelSelectorModal = ModelSelector

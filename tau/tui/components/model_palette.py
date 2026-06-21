from __future__ import annotations

from tau.tui.ansi import BOLD, BRIGHT_BLACK, BRIGHT_WHITE, GREEN, RESET
from tau.tui.component import Component
from tau.tui.input import InputEvent

VISIBLE_ROWS = 10


class ModelSelectorModal:
    """Model selector modal.

    Owns search input, scope toggle, navigation, and rendering.
    Designed to be wrapped in InlineSelector(kind="model").

    Scope:
      "scoped" = only models from the same provider as the current model
      "all"    = every available model

    Visual:
      Scope: all | scoped  tab: toggle
      Search: <query>█
      → claude-sonnet-4-20250514 [anthropic] ✓
        claude-opus-4-20250514 [anthropic]
        gpt-4o [openai]
      (3/20)
      Model Name: Claude Sonnet 4
    """

    def __init__(self, models: list, current_key: str) -> None:
        self._all_models: list = list(models)
        self._current_key = current_key

        current_provider = current_key.split("/")[0] if "/" in current_key else ""
        self._scoped_models = (
            [m for m in models if (m.provider or "") == current_provider]
            if current_provider
            else []
        )

        self._scope: str = "scoped" if self._scoped_models else "all"
        self._active: list = self._scoped_models if self._scope == "scoped" else self._all_models

        self._filtered: list = list(self._active)
        self._selected: int = 0
        self._search: str = ""

        # Jump to current model
        for i, m in enumerate(self._filtered):
            if f"{m.provider}/{m.id}" == current_key:
                self._selected = i
                break

    # ── Navigation ────────────────────────────────────────────────────────────

    def move_up(self) -> None:
        if self._filtered:
            self._selected = (self._selected - 1) % len(self._filtered)

    def move_down(self) -> None:
        if self._filtered:
            self._selected = (self._selected + 1) % len(self._filtered)

    def toggle_scope(self) -> None:
        if self._scope == "all":
            if self._scoped_models:
                self._scope = "scoped"
                self._active = self._scoped_models
        else:
            self._scope = "all"
            self._active = self._all_models
        self._search = ""
        self._apply_filter()
        # jump to current model in new scope
        self._selected = 0
        for i, m in enumerate(self._filtered):
            if f"{m.provider}/{m.id}" == self._current_key:
                self._selected = i
                break

    def append_search(self, ch: str) -> None:
        self._search += ch
        self._apply_filter()

    def backspace_search(self) -> None:
        self._search = self._search[:-1]
        self._apply_filter()

    # ── Value ─────────────────────────────────────────────────────────────────

    def selected_value(self) -> tuple[str, str] | None:
        if not self._filtered:
            return None
        m = self._filtered[self._selected]
        return (m.id, m.provider)

    # ── Render ────────────────────────────────────────────────────────────────

    def render(self, width: int) -> list[str]:  # noqa: ARG002
        lines: list[str] = []

        # Scope header
        if self._scoped_models:
            all_t = (
                (BRIGHT_WHITE + BOLD + "all" + RESET)
                if self._scope == "all"
                else (BRIGHT_BLACK + "all" + RESET)
            )
            sc_t = (
                (BRIGHT_WHITE + BOLD + "scoped" + RESET)
                if self._scope == "scoped"
                else (BRIGHT_BLACK + "scoped" + RESET)
            )
            lines.append(
                f"  {BRIGHT_BLACK}Scope:{RESET} {all_t}"
                f"{BRIGHT_BLACK} | {RESET}{sc_t}  {BRIGHT_BLACK}tab: toggle{RESET}"
            )
        else:
            lines.append(f"  {BRIGHT_BLACK}↑/↓: navigate  enter: select  esc: cancel{RESET}")

        # Search line
        cursor = "█"
        if self._search:
            lines.append(f"  {BRIGHT_BLACK}Search:{RESET} {self._search}{cursor}")
        else:
            lines.append(f"  {BRIGHT_BLACK}Search: {cursor}{RESET}")

        if not self._filtered:
            lines.append(f"  {BRIGHT_BLACK}No models match{RESET}")
            return lines

        count = len(self._filtered)
        visible = min(VISIBLE_ROWS, count)
        start = max(0, min(self._selected - visible // 2, count - visible))

        for i in range(start, start + visible):
            m = self._filtered[i]
            is_sel = i == self._selected
            is_current = f"{m.provider}/{m.id}" == self._current_key
            check = f" {GREEN}✓{RESET}" if is_current else ""
            badge = f"{BRIGHT_BLACK}[{m.provider}]{RESET}"

            if is_sel:
                lines.append(f"  {BRIGHT_WHITE}{BOLD}→ {m.id}{RESET} {badge}{check}")
            else:
                lines.append(f"    {m.id} {badge}{check}")

        if count > visible:
            lines.append(f"  {BRIGHT_BLACK}({self._selected + 1}/{count}){RESET}")

        sel_m = self._filtered[self._selected]
        name = getattr(sel_m, "name", None) or sel_m.id
        lines.append(f"  {BRIGHT_BLACK}Model Name: {name}{RESET}")

        return lines

    # ── Internal ──────────────────────────────────────────────────────────────

    def _apply_filter(self) -> None:
        q = self._search.lower()
        if not q:
            self._filtered = list(self._active)
        else:
            self._filtered = [
                m
                for m in self._active
                if q in (m.id or "").lower()
                or q in (m.name or "").lower()
                or q in f"{m.provider}/{m.id}".lower()
            ]
        if self._filtered:
            self._selected = min(self._selected, len(self._filtered) - 1)
        else:
            self._selected = 0


# ---------------------------------------------------------------------------
# Legacy inline palette (kept for compat; no longer activated by /model )
# ---------------------------------------------------------------------------


class ModelPalette(Component):
    """Deprecated inline model palette. Kept so existing wiring doesn't break."""

    def __init__(self) -> None:
        self._active = False

    @property
    def active(self) -> bool:
        return False

    @property
    def selected(self):
        return None

    def set_models(self, models: list, current_key: str = "") -> None:  # noqa: ARG002
        pass

    def clear(self) -> None:
        self._active = False

    def set_query(self, name: str, provider: str = "") -> None:  # noqa: ARG002
        pass

    def toggle_scope(self) -> None:
        pass

    def move_up(self) -> None:
        pass

    def move_down(self) -> None:
        pass

    def render(self, width: int) -> list[str]:  # noqa: ARG002
        return []

    def handle_input(self, event: InputEvent) -> bool:  # noqa: ARG002
        return False

    def invalidate(self) -> None:
        pass

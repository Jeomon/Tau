"""Editor (prompt input) contracts.

These Protocols document the interface the :class:`~tau.tui.components.primitives.layout.Layout`
expects from the prompt editor, so an extension can replace it via
``ctx.ui.set_editor_component`` against a defined surface instead of mimicking
:class:`~tau.tui.components.primitives.text_input.TextInput`'s private fields.

Two tiers, by design:

- :class:`EditorComponent` — the *core* an editor must provide to function
  (render, handle input, buffer access, submit wiring). Implement this and you
  get a working prompt wired to submit / follow-up / dequeue.
- :class:`EditorExtras` — *optional* capabilities the Layout feature-detects at
  runtime: command arg-hints, the dynamic ``!``/``❯`` prefix, history-aware
  pickers, caret theming, and placeholder overrides. Omit them and only those
  enrichments are skipped for your editor — the core still works.

Both are ``@runtime_checkable`` so the host can ``isinstance``-gate optional
behaviour. The stock :class:`TextInput` satisfies both structurally (no
inheritance required).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tau.tui.input import InputEvent

if TYPE_CHECKING:
    from tau.tui.buffer import Buffer
    from tau.tui.geometry import Rect


@runtime_checkable
class EditorComponent(Protocol):
    """Minimum interface for a Layout prompt editor."""

    # ── Rendering + input (the Component contract) ─────────────────────────────
    def render_cells(self, area: Rect, buf: Buffer) -> int: ...
    def handle_input(self, event: InputEvent) -> bool: ...

    # ── Buffer access ──────────────────────────────────────────────────────────
    @property
    def text(self) -> str: ...
    @property
    def cursor(self) -> int: ...
    def set_text(self, text: str) -> None: ...
    def clear(self) -> None: ...
    def insert_at_cursor(self, text: str) -> None: ...
    def submit(self) -> None: ...

    # ── Submit wiring (Layout assigns these after construction) ───────────────
    on_submit: Callable[[str], None] | None
    on_followup: Callable[[str], None] | None
    on_dequeue: Callable[[], None] | None


@runtime_checkable
class EditorExtras(EditorComponent, Protocol):
    """A core editor that *also* provides the optional capabilities below.

    Inherits :class:`EditorComponent` so an ``isinstance(x, EditorExtras)`` check
    requires the full editor surface plus these extras — the Layout skips the
    enrichment features (arg-hints, dynamic prefix, history pickers, caret
    theming, placeholder override) for editors that don't satisfy it.
    """

    prefix: str
    placeholder: str
    arg_hint: str
    visual_strip: int
    history_idx: int
    cursor_cell: Callable[[str], str]

    def set_placeholder_override(self, text: str | None) -> None: ...
    def backspace(self) -> None: ...

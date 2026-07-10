# Terminal UI

`tau.tui` is a standalone terminal UI framework. It provides terminal I/O,
differential rendering, input parsing, focus, overlays, reusable components,
layout primitives, themes, and Markdown rendering without depending on Tau's
agent, runtime, sessions, extensions, or interactive application.

Application-specific layouts and agent integration live in
`tau.modes.interactive`.

## Public API

Import supported primitives from `tau.tui`:

```python
from tau.tui import (
    Column,
    Component,
    KeyEvent,
    Text,
    TextInput,
    TUI,
)
```

| Area | Main exports |
|------|--------------|
| Application | `TUI`, `Renderer`, `Terminal` |
| Components | `Component`, `Text`, `TextInput`, `Spinner`, `Image`, `SelectList`, `Box` |
| Layout | `Container`, `Column`, `Row`, `Rows`, `Columns`, `Constrained` |
| Input | `InputParser`, `Key`, `KeyEvent`, `PasteEvent`, `MouseEvent` |
| Overlays | `OverlayOptions`, `OverlayHandle` |
| Styling | Theme dataclasses, color helpers, `render_markdown` |

### Markdown math

`render_markdown` converts inline (`$...$`) and display (`$$...$$`) LaTeX math
to terminal-readable Unicode text through `pylatexenc`. Display math is placed
on separate lines. Code spans and fenced code blocks retain their original
LaTeX source. This is a readable plain-text representation, not fully typeset
mathematical layout.

## Minimal Application

```python
import asyncio

from tau.tui import Column, KeyEvent, Text, TextInput, TUI


async def main() -> None:
    tui = TUI(title="My terminal application")
    output = Text("Type a message and press Enter.")
    editor = TextInput(prefix="> ", tui=tui)

    def submit(value: str) -> None:
        output.set_text(f"You entered: {value}")
        editor.clear()
        tui.request_render()

    editor.on_submit = submit
    tui.set_root(Column([output, editor]))
    tui.set_focus(editor)

    def handle_input(event: object) -> None:
        if isinstance(event, KeyEvent) and event.matches("ctrl+c"):
            tui.stop()

    tui.on_input(handle_input)
    await tui.run()


asyncio.run(main())
```

`TUI.run()` enters terminal raw mode and runs until `stop()` is called. It uses
the main terminal buffer, preserving native scrollback.

Call `dispose()` when embedding a TUI without Tau's interactive application.
It releases component background tasks, render caches, input handlers, and
terminal resize subscriptions. Tau's interactive application does this
automatically during shutdown.

## Components

Subclass `Component` to create a reusable view:

```python
from tau.tui import Buffer, Component, KeyEvent, Rect, Span, TextLine


class Counter(Component):
    def __init__(self) -> None:
        self.value = 0

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        buf.grow_to(area.y + 1)
        buf.set_line(area.x, area.y, TextLine([Span(f"Count: {self.value}")]), area.width)
        return 1

    def handle_input(self, event: object) -> bool:
        if isinstance(event, KeyEvent) and event.matches("up"):
            self.value += 1
            return True
        return False
```

`render_cells()` writes styled cells into the provided `Buffer` and returns the
number of rows written. Components must grow dynamic buffers before writing and
must respect the provided `Rect`.

## Layout

- `Column` stacks fixed children vertically.
- `Container` supports dynamic child insertion and removal.
- `Row` arranges single-line children by left, center, or right alignment.
- `Columns` creates multi-line fixed and flexible columns.
- `Rows` distributes a fixed terminal height across children.
- `Constrained` places a child at an absolute or percentage width.
- `Box` wraps a child `Component` with horizontal/vertical padding and an
  optional background `Style`.

## Focus and Input

`TUI.set_focus(component)` routes input to one component. Components needing
focus state should implement `Focusable`. Global handlers registered through
`on_input()` receive events not consumed by the focused component.

Input is normalized into typed events:

- `KeyEvent`
- `PasteEvent`
- `MouseEvent`
- `FocusEvent`
- `BgColorEvent`

Use `KeyEvent.matches()` instead of comparing raw escape sequences.
Mouse reporting is not enabled by the TUI — terminals only offer clicks and
wheel-scroll as a single reporting mode, so requesting it would take over the
terminal's native wheel-scroll and click-drag copy for the whole session.
`MouseEvent` remains available for embedders that want to opt into that
trade-off themselves via `Terminal.enable_mouse_tracking()`.

## Overlays

```python
from tau.tui import OverlayOptions, Text

handle = tui.show_overlay(
    Text("Settings"),
    OverlayOptions(width=40, anchor="center"),
)

handle.set_hidden(True)
handle.show()
handle.close()
```

Overlays support absolute or percentage sizing, nine anchor positions,
responsive visibility, margins, focus capture, and restoration of the previous
focus target.

## Terminal Injection

`TUI` accepts an optional terminal object:

```python
tui = TUI(terminal=terminal, title="Test application")
```

This supports alternative terminal implementations and deterministic tests.
The object must provide the operations used by `Terminal` and `Renderer`.

## Dependency Boundary

Files under `tau.tui` may depend only on the Python standard library,
third-party rendering/input dependencies, and other `tau.tui` modules.
Runtime-aware behavior belongs in `tau.modes.interactive`.

This boundary is enforced by `tests/test_tui_public_api.py`.

The renderer retains only the current transcript frame. Styled content that
exceeds its available width wraps directly into Buffer rows without losing
content, and reflows when the terminal is resized. Finalized message rows are
cached as cells so streaming updates do not repeatedly parse ANSI styling
across the complete session.

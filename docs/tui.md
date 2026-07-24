# Terminal UI

`tau.tui` is a standalone terminal UI framework: terminal I/O, differential rendering, input parsing, focus, overlays, components, layout primitives, themes, and Markdown rendering. It depends on nothing else in Tau, not the engine, runtime, sessions, or extensions.

Use `tau.tui` when you want to build a terminal interface. Use [`tau.modes.interactive`](architecture.md) when you want Tau's agent chat UI; that package supplies the application-specific layouts and agent wiring on top of this one.

## Table of Contents

- [Public API](#public-api)
- [Standalone Usage](#standalone-usage)
- [The Component Contract](#the-component-contract)
- [Layout Components](#layout-components)
- [Built-In Components](#built-in-components)
- [Styled Text](#styled-text)
- [Widgets](#widgets)
- [Running a Full Application](#running-a-full-application)
- [Focus and Input](#focus-and-input)
- [Overlays](#overlays)
- [Testing](#testing)
- [Dependency Boundary](#dependency-boundary)

## Public API

Every name below is importable directly from `tau.tui`. Imports are lazy (the submodule is only loaded when a name is first accessed), so `from tau.tui import *` is cheap.

| Area | Exports |
|------|---------|
| Application | `TUI`, `Renderer`, `Terminal`, `TerminalCapabilities`, `CellDimensions`, `detect_capabilities`, `get_capabilities`, `get_cell_dimensions` |
| Component base | `Component`, `Focusable`, `Container`, `StaticComponent`, `Text` |
| Layout components | `Column`, `Row`, `Rows`, `Columns`, `Constrained`, `VerticalStack` |
| Components | `TextInput`, `EditorComponent`, `EditorExtras`, `Spinner`, `Image`, `ImageDimensions`, `ImageOptions`, `SelectList`, `SelectItem`, `InlineSelector`, `Box`, `DynamicBorder` |
| Geometry | `Rect`, `Position` |
| Buffer | `Buffer`, `Cell` |
| Style | `Style`, `Stylize`, `Color`, `RESET_COLOR`, `parse_color`, `Modifier` |
| Text | `Span`, `Masked`, `TextLine`, `StyledText` |
| Widgets | `Widget`, `StatefulWidget`, `render_widget` |
| Backends | `Backend`, `TestBackend`, `AnsiBackend` |
| Frames | `Frame`, `BufferedTerminal`, `Fullscreen`, `Fixed`, `Inline` |
| Constraint layout | `Layout`, `Constraint`, `Direction`, `Flex`, `Alignment` |
| Palettes | `tailwind`, `material` |
| Input | `InputEvent`, `InputParser`, `Key`, `KeyEvent`, `PasteEvent`, `MouseEvent`, `BgColorEvent`, `FocusEvent`, `KeyMap`, `KeybindingsManager`, `get_keybindings`, `configure_keybindings` |
| Theme | `LayoutTheme`, `SpinnerTheme`, `MarkdownTheme`, `MessageTheme`, `InputTheme`, `SelectListTheme`, `ColorFn`, `color`, `rgb`, `rgb_bold`, `rgb_italic` |
| Markdown | `render_markdown` |
| Testing | `assert_buffer_eq` |

Two names are aliases to avoid collisions: `TextLine` is `tau.tui.text.Line`, and `StyledText` is `tau.tui.text.Text` (distinct from the `Text` *component*).

## Standalone Usage

You do not need a TUI application, an event loop, or even a terminal to render a component. A component writes into a `Buffer`, and `row_to_ansi()` flattens a buffer row into an ANSI string you can print.

This script defines a custom component and renders it. Copy, paste, and run it:

```python
"""Render a custom tau.tui component with no application, event loop, or TTY."""

from tau.tui import Buffer, Component, Rect, Span, Style, TextLine
from tau.tui.ansi_bridge import row_to_ansi


class Gauge(Component):
    """A labelled bar: [#####-----] 50%"""

    def __init__(self, label: str, fraction: float) -> None:
        self.label = label
        self.fraction = max(0.0, min(1.0, fraction))

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        # Buffers start at height 0 — grow before writing, or the write no-ops.
        buf.grow_to(area.y + 2)

        buf.set_line(
            area.x,
            area.y,
            TextLine([Span.styled(self.label, Style().bold().with_fg("bright_cyan"))]),
            area.width,
        )

        bar_width = max(1, area.width - 8)
        filled = round(bar_width * self.fraction)
        buf.set_line(
            area.x,
            area.y + 1,
            TextLine([
                Span.raw("["),
                Span.styled("#" * filled, Style().with_fg("bright_green")),
                Span.styled("-" * (bar_width - filled), Style().with_fg("bright_black")),
                Span.raw(f"] {self.fraction:>4.0%}"),
            ]),
            area.width,
        )
        return 2  # Rows written


def render(component: Component, width: int) -> list[str]:
    """Render a component into a scratch buffer and return ANSI rows."""
    area = Rect(0, 0, width, 0)
    buf = Buffer.empty(area)
    rows = component.render_cells(area, buf)
    return [row_to_ansi(buf, y) for y in range(rows)]


def main() -> None:
    for name, value in [("Download", 0.72), ("Upload", 0.15)]:
        for line in render(Gauge(name, value), 40):
            print(line)
        print()


main()
```

Output (styling elided):

```text
Download
[#######################---------]  72%

Upload
[#####---------------------------]  15%
```

Standalone rendering gives you layout, styling, and composition. It does **not** give you input handling, a redraw loop, resize handling, or overlays. Those need a `TUI`, covered in [Running a Full Application](#running-a-full-application). Built-in components work standalone too, with one exception: `Spinner` requires a `TUI` because it drives its own animation through `request_render()`.

## The Component Contract

`Component` is an ABC with exactly one abstract method. A subclass that does not override `render_cells` fails at construction.

```python
class Component(ABC):
    @abstractmethod
    def render_cells(self, area: Rect, buf: Buffer) -> int:
        """Render into buf starting at row area.y; return the number of rows written."""

    def handle_input(self, event: InputEvent) -> bool:
        """Return True if the event was consumed, stopping propagation."""
        return False

    def invalidate(self) -> None:
        """Clear cached render state. Called by the renderer after a resize."""

    def dispose(self) -> None:
        """Release background tasks or subscriptions owned by this component."""
```

| Method | Required | Purpose |
|--------|----------|---------|
| `render_cells(area, buf)` | Yes | Write cells, return rows written |
| `handle_input(event)` | No | Consume an input event |
| `invalidate()` | No | Drop render caches on resize or theme change |
| `dispose()` | No | Tear down tasks and subscriptions |

There is no `measure()`, `on_mount()`, or `on_unmount()`. The lifecycle is: construct → `render_cells` repeatedly → `invalidate()` on resize → `dispose()` at teardown. Input dispatch is independent of rendering.

> **Critical:** a `Buffer` starts at height 0 and grows on demand. Call `buf.grow_to(area.y + n)` before writing row `area.y + n - 1`. `Buffer.set()` and `set_string()` **silently no-op on an out-of-bounds row** rather than growing it for you. A missing `grow_to` shows up as blank output, not an error.

Respect the supplied `Rect`: start at `area.x` / `area.y` and never exceed `area.width`.

### Focusable

Components that render a cursor should mix in `Focusable`, a plain class with a single attribute:

```python
class Focusable:
    focused: bool = False
```

`TUI.set_focus(component)` sets `focused = True` and routes `handle_input()` exclusively to that component.

```python
from tau.tui import Buffer, Component, Focusable, Rect


class MyInput(Component, Focusable):
    def __init__(self) -> None:
        self._text = ""

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        cursor = "█" if self.focused else ""
        buf.grow_to(area.y + 1)
        buf.set_string(area.x, area.y, f"> {self._text}{cursor}")
        return 1
```

## Layout Components

| Class | Constructor | Purpose |
|-------|-------------|---------|
| `Container` | `Container()` | Dynamic children: `add_child`, `remove_child`, `clear` |
| `Column` | `Column(children)` | Stack children vertically. `VerticalStack` is an alias |
| `Row` | `Row(slots=None)` | Single-line row; slots align `"left"`, `"center"`, `"right"` |
| `Columns` | `Columns(slots=None, gap=1)` | Multi-line columns; a `None` width is flexible |
| `Rows` | `Rows(slots=None, height=None, gap=0)` | Distribute a fixed height across children |
| `Constrained` | `Constrained(child, width, align="left")` | Fix a child to absolute columns or `"30%"` |
| `Box` | `Box(child, padding_x=0, padding_y=0, bg_style=None)` | Pad a child and apply a background style |
| `StaticComponent` | `StaticComponent(lines)` | Render a fixed list of ANSI strings |
| `Text` | `Text(text="", style=None)` | Word-wrapped text; `set_text()`, `.text` |

```python
from tau.tui import Box, Column, Columns, Constrained, Text
from tau.tui.style import Style

sidebar = Text("Files")
main = Text("Contents")

layout = Column([
    Box(Text("Header"), padding_x=1, bg_style=Style().with_bg("#1e1e2e")),
    Columns([(Constrained(sidebar, "25%"), None), (main, None)], gap=2),
])
```

`Container` dispatches input to children in order; `Column` dispatches in reverse order.

### Constraint Layout

`tau.tui.layout.Layout` is a separate, grid-style constraint solver that splits a `Rect`, unrelated to the component tree.

```python
from tau.tui import Constraint, Layout, Rect

areas = Layout.vertical([
    Constraint.length(3),      # Exactly 3 rows
    Constraint.fill(1),        # Take the remaining space
    Constraint.length(1),
]).split(Rect(0, 0, 80, 24))
```

Constraints are built with `Constraint.length(n)`, `.percentage(pct)`, `.ratio(num, den)`, `.min(n)`, `.max(n)`, and `.fill(weight)`.

## Built-In Components

| Component | Constructor | Purpose |
|-----------|-------------|---------|
| `TextInput` | `TextInput(prefix="> ", placeholder="", …, tui=None, cursor_blink=True)` | Multi-line input: cursor, undo/redo, history, readline keys, soft wrap |
| `SelectList` | `SelectList(items=None, max_visible=5, theme=None)` | Fuzzy-filterable scrolling picker |
| `SelectItem` | `SelectItem(label, description="", value=None)` | One row in a `SelectList` |
| `Spinner` | `Spinner(tui, label="", theme=None)` | Themed animated spinner. Requires a `TUI` |
| `Image` | `Image(...)` | Inline images via the Kitty and iTerm2 graphics protocols |
| `DynamicBorder` | `DynamicBorder(style=None)` | Animated border |

```python
from tau.tui import SelectItem, SelectList

picker = SelectList(
    [SelectItem("dark", "Terminal-adaptive"), SelectItem("tokyo-night", "Hex")],
    max_visible=5,
)
picker.move_down()
```

`TextInput`'s `tui` argument is optional; without it the cursor stays solid and no blink task starts.

## Styled Text

`Style` is a frozen dataclass and a *patch*: `None` fields inherit from whatever it is applied over. It is immutable, so every builder method returns a new instance.

```python
from tau.tui import Span, Style, TextLine

style = Style().with_fg("#a78bfa").with_bg("bright_black").bold().italic()

line = TextLine([
    Span.raw("plain "),
    Span.styled("emphasised", style),
])
```

| Method | Effect |
|--------|--------|
| `with_fg(color)` / `with_bg(color)` | Foreground / background color |
| `with_underline_color(color)` | Underline color |
| `with_link(url)` | OSC 8 hyperlink |
| `bold()`, `dim()`, `italic()`, `underline()`, `blink()`, `reversed()`, `strikethrough()` | Text attributes |
| `patch(other)` | Layer another style on top |
| `sgr()` | Render to an ANSI SGR sequence |

Colors accept a hex string (`"#a78bfa"`), a named ANSI color (`"bright_cyan"`), an `(r, g, b)` tuple, a palette index, or `RESET_COLOR` to force the terminal default. `parse_color()` converts a string spec. The `tailwind` and `material` palettes provide ready-made triples: `tailwind.SLATE.c500`.

There are three ways to emit styled output, in decreasing order of preference:

1. **Structured**: `buf.set_line(x, y, TextLine([...]), width)` or `buf.set_span(...)`. Style stays data until the cell resolves it.
2. **Direct**: `buf.set_string(x, y, "text", Style().bold())`.
3. **ANSI bridge**: build a string with `apply_style()` and push it through `tau.tui.ansi_bridge.parse_ansi_into(buf, x, y, line, width)`. This is how `Text` and `StaticComponent` work internally, and it is the escape hatch for content that is already ANSI-encoded.

## Widgets

Widgets are a second, lower-level drawing layer: they render into a **pre-sized** `Rect` and return nothing. Compose them by writing into non-overlapping rectangles.

```python
@runtime_checkable
class Widget(Protocol):
    def render(self, area: Rect, buf: Buffer) -> None: ...


@runtime_checkable
class StatefulWidget(Protocol):
    def render(self, area: Rect, buf: Buffer, state: Any) -> None: ...
```

| Contrast | `Component` | `Widget` |
|----------|-------------|----------|
| Signature | `render_cells(area, buf) -> int` | `render(area, buf) -> None` |
| Height | Grows the buffer itself | Fixed by the caller's `Rect` |
| Composition | Tree with input dispatch | Manual rectangle placement |

Available in `tau.tui.widgets`:

| Module | Exports |
|--------|---------|
| `block` | `Block`, `Borders`, `Padding`, `Title`, `TitlePosition` |
| `paragraph` | `Paragraph`, `Wrap` |
| `list` | `List`, `ListItem`, `ListState`, `ListDirection` |
| `table` | `Table`, `Row`, `TableState` |
| `tabs` | `Tabs` |
| `gauge` | `Gauge`, `LineGauge` |
| `scrollbar` | `Scrollbar`, `ScrollbarState`, `ScrollbarOrientation` |
| `sparkline` | `Sparkline`, `RenderDirection` |
| `barchart` | `BarChart`, `Bar`, `BarGroup` |
| `chart` | `Chart`, `Dataset`, `Axis`, `GraphType`, `LegendPosition` |
| `canvas` | `Canvas`, `CanvasLine`, `Points`, `Rectangle`, `Marker`, `Map`, `MapResolution` |
| `calendar` | `Monthly`, `DateStyler`, `CalendarEventStore` |
| `clear` | `Clear` |

Bridge a widget into the component tree with `WidgetComponent`, or render one to ANSI lines directly:

```python
from tau.tui.components.widget_bridge import WidgetComponent, render_widget_lines
from tau.tui.widgets.gauge import Gauge

component = WidgetComponent(Gauge(...), height=1)      # Into a Component tree
lines = render_widget_lines(Gauge(...), width=40, height=1)   # Straight to ANSI
```

## Running a Full Application

`TUI` is itself a `Container`, so the application *is* the root of the component tree. `run()` is async and holds the terminal in raw mode until `stop()` is called.

```python
import asyncio

from tau.tui import TUI, Column, KeyEvent, Text, TextInput


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

| Method | Purpose |
|--------|---------|
| `await run()` | Enter raw mode and run the render/event loop |
| `stop()` | Request a clean exit |
| `dispose()` | Release components, overlays, timers, and terminal callbacks |
| `request_render()` | Schedule a debounced render after a state change |
| `set_root(component)` | Set the root of the tree |
| `set_focus(component)` | Route input to one component |
| `set_title(title)` | Set the terminal title |
| `on_input(handler, *, prepend=False)` | Register a global handler; returns an unsubscribe callable |
| `on_input_intercept(handler)` | Register a handler that runs before everything, including key releases |
| `show_overlay(component, options)` | Show an overlay; returns an `OverlayHandle` |
| `await query_background_color()` | Query the terminal background over OSC 11 |

`TUI` uses the main terminal buffer, so native scrollback is preserved. Call `dispose()` when embedding a TUI in a longer-lived process; Tau's interactive application does this during shutdown.

`TUI(terminal=...)` accepts an alternative terminal object, which is how tests drive the renderer without a TTY. The object needs `width`, `height`, `write`, `write_flush`, `begin_sync`, `end_sync`, and `on_resize`.

### Markdown

`render_markdown()` renders Markdown with syntax-highlighted code blocks. It also converts inline (`$…$`) and display (`$$…$$`) LaTeX math to readable Unicode via `pylatexenc`; display math goes on its own lines, and code spans and fenced blocks keep their original LaTeX source. This is a plain-text approximation, not typeset layout.

## Focus and Input

Input is parsed into typed events by `InputParser`:

| Event | Fires on |
|-------|----------|
| `KeyEvent` | A key press or release |
| `PasteEvent` | Bracketed paste |
| `MouseEvent` | Mouse click or wheel, when tracking is enabled |
| `BgColorEvent` | An OSC 11 background-color reply |
| `FocusEvent` | Terminal focus gained or lost |

`InputEvent` is the union of all five.

Match keys with `KeyEvent.matches()` rather than comparing raw escape sequences. It is modifier-order- and alias-independent, so `"ctrl+shift+x"`, `"shift+ctrl+x"`, and `"control+shift+x"` are equivalent:

```python
from tau.tui import Key, KeyEvent

if event.matches("ctrl+c"):
    ...
if event.matches(Key.ESCAPE, Key.ctrl("g")):
    ...
```

`Key` is a class of string constants (`Key.ESCAPE`, `Key.ENTER`, `Key.PAGE_UP`, `Key.F1` …) with modifier builders (`Key.ctrl`, `Key.alt`, `Key.shift`, `Key.meta`, `Key.ctrl_shift`, `Key.ctrl_alt`, `Key.alt_shift`, `Key.ctrl_shift_alt`). It is not an Enum.

Dispatch order for each event: intercept handlers → key releases dropped → focused overlay → focus target → global `on_input` handlers.

Named-action bindings go through `get_keybindings()` / `configure_keybindings()`. See [Keybindings](keybindings.md).

Mouse reporting is **not** enabled by default. Terminals expose clicks and wheel-scroll as one reporting mode, so enabling it would take over native wheel-scroll and click-drag copy for the whole session. Embedders who accept that trade-off can call `Terminal.enable_mouse_tracking()`.

## Overlays

```python
from tau.tui import OverlayOptions, Text

handle = tui.show_overlay(
    Text("Settings"),
    OverlayOptions(width="50%", min_width=40, anchor="center", margin=2),
)

handle.set_hidden(True)
handle.show()
handle.focus()
handle.unfocus()
handle.close()
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `width` | number or percent string | `"60%"` | Overlay width |
| `height` | number or percent string | `None` | Overlay height; content-sized when unset |
| `min_width` / `max_width` | number or percent string | `None` | Width bounds |
| `min_height` / `max_height` | number or percent string | `None` / `"80%"` | Height bounds |
| `anchor` | string | `"center"` | One of nine anchors: `"center"`, `"top-left"`, `"right-center"`, … |
| `offset_x` / `offset_y` | int | `0` | Offset from the anchor |
| `row` / `col` | number or percent string | `None` | Absolute or percentage position, instead of an anchor |
| `margin` | int or dict | `1` | All sides, or `{"top", "right", "bottom", "left"}` |
| `visible` | `(width, height) -> bool` | `None` | Hide responsively on small terminals |
| `non_capturing` | bool | `False` | Do not take input ownership |

A focused overlay owns input until it is closed, hidden, or unfocused. `handle.unfocus(target)` hands input to a specific component while the overlay stays visible.

## Testing

Render a component to strings and assert on the result, no terminal required:

```python
from tau.tui import Buffer, Rect, Text
from tau.tui.ansi_bridge import row_to_ansi


def render_lines(component, width):
    area = Rect(0, 0, width, 0)
    buf = Buffer.empty(area)
    rows = component.render_cells(area, buf)
    return [row_to_ansi(buf, y).rstrip() for y in range(rows)]


def test_text_wraps():
    assert render_lines(Text("alpha beta"), 6) == ["alpha", "beta"]
```

`assert_buffer_eq(actual, expected)` compares two buffers and raises with a rendered text view of both plus the exact differing cells. `TestBackend(width, height)` is an in-memory `Backend` exposing `.buffer`, `.cursor`, `.cursor_hidden`, and `.flush_count`.

## Dependency Boundary

Modules under `tau.tui` may import only the standard library, third-party rendering and input dependencies, and other `tau.tui` modules. Runtime-aware behavior belongs in `tau.modes.interactive`.

This is enforced mechanically: `tests/test_tui_public_api.py` walks the AST of every file under `tau/tui/` and fails on any import of a `tau.*` module outside `tau.tui`.

The renderer keeps only the current transcript frame. Content wider than the available width wraps into buffer rows without loss and reflows on resize. Finalized message rows are cached as cells, so streaming updates do not re-parse ANSI styling across the whole session.

## Next Steps

- [Keybindings](keybindings.md): the named-action keymap and input handling
- [Themes](themes.md): theme dataclasses and color tokens
- [Architecture](architecture.md): how `tau.tui` fits under `tau.modes.interactive`
</content>

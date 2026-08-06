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
| Text measurement | `tau.tui.ansi_text` (`wrap_ansi`, `splice_ansi`, `tokenize`) |
| Style | `Style`, `Stylize`, `Color`, `RESET_COLOR`, `parse_color`, `Modifier` |
| Text | `Span`, `Masked`, `TextLine`, `StyledText` |
| Widgets | `tau.tui.widgets` (`Block`, `List`, `Tabs`, …) — see [Widgets](#widgets) |
| Frames | `Frame`, `BufferedTerminal`, `Fullscreen`, `Fixed`, `Inline` |
| Constraint layout | `Layout`, `Constraint`, `Direction`, `Flex`, `Alignment` |
| Palettes | `tailwind`, `material` |
| Input | `InputEvent`, `InputParser`, `Key`, `KeyEvent`, `PasteEvent`, `MouseEvent`, `BgColorEvent`, `FocusEvent`, `KeyMap`, `KeybindingsManager`, `get_keybindings`, `configure_keybindings` |
| Theme | `LayoutTheme`, `SpinnerTheme`, `MarkdownTheme`, `MessageTheme`, `InputTheme`, `SelectListTheme`, `ColorFn`, `color`, `rgb`, `rgb_bold`, `rgb_italic` |
| Markdown | `render_markdown` |


Two names are aliases to avoid collisions: `TextLine` is `tau.tui.text.Line`, and `StyledText` is `tau.tui.text.Text` (distinct from the `Text` *component*).

## Standalone Usage

You do not need a TUI application, an event loop, or even a terminal to render a component. A component's `render(width)` returns styled ANSI lines you can print directly.

This script defines a custom component and renders it. Copy, paste, and run it:

```python
"""Render a custom tau.tui component with no application, event loop, or TTY."""

from tau.tui import Component, Span, Style, TextLine, line_to_ansi


class Gauge(Component):
    """A labelled bar: [#####-----] 50%"""

    def __init__(self, label: str, fraction: float) -> None:
        self.label = label
        self.fraction = max(0.0, min(1.0, fraction))

    def render(self, width: int) -> list[str]:
        bar_width = max(1, width - 8)
        filled = round(bar_width * self.fraction)
        return [
            line_to_ansi(
                TextLine([Span.styled(self.label, Style().bold().with_fg("bright_cyan"))]),
                width,
            ),
            line_to_ansi(
                TextLine([
                    Span.raw("["),
                    Span.styled("#" * filled, Style().with_fg("bright_green")),
                    Span.styled("-" * (bar_width - filled), Style().with_fg("bright_black")),
                    Span.raw(f"] {self.fraction:>4.0%}"),
                ]),
                width,
            ),
        ]


def render(component: Component, width: int) -> list[str]:
    """Render a component and return its ANSI rows."""
    return component.render(width)


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

`Component` has **one** render contract:

```python
class Component(ABC):
    def render(self, width: int) -> list[str]:
        """Return this component's styled ANSI lines."""
```

The renderer consumes lines directly, with no per-character cell grid in between. Width-aware work — wrapping, clipping, compositing, measuring wide glyphs — lives in `tau.tui.ansi_text`, which operates on grapheme clusters rather than characters.

A subclass implementing `render` incorrectly (or not at all) raises `NotImplementedError` naming the class on first render, rather than failing at construction.

If you write a component that does *not* subclass `Component` (duck-typing works, since `add_child` does not enforce its type hint), implement `render(width)` and it will still be rendered. A child providing neither raises a named `TypeError` instead of being swallowed into a frozen screen.

> **Changed:** `render_cells(area, buf)`, `Buffer`, `Cell`, the `Widget`/`StatefulWidget` protocols, `render_widget`, the `Backend` classes and `assert_buffer_eq` have all been removed along with the cell grid. A component still on `render_cells` will not render — implement `render(width)` instead.

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
| `render(width)` | Yes | Return styled ANSI lines |
| `handle_input(event)` | No | Consume an input event |
| `invalidate()` | No | Drop render caches on resize or theme change |
| `dispose()` | No | Tear down tasks and subscriptions |

There is no `measure()`, `on_mount()`, or `on_unmount()`. The lifecycle is: construct → `render` repeatedly → `invalidate()` on resize → `dispose()` at teardown. Input dispatch is independent of rendering.

Respect the supplied `width`: never return a line wider than it. `tau.tui.ansi_text.wrap_ansi(line, width)` splits on grapheme-cluster boundaries, so it will not cut a CJK character or ZWJ emoji in half the way character-based slicing does.

### Focusable

Components that render a cursor should mix in `Focusable`, a plain class with a single attribute:

```python
class Focusable:
    focused: bool = False
```

`TUI.set_focus(component)` sets `focused = True` and routes `handle_input()` exclusively to that component.

```python
from tau.tui import Component, Focusable


class MyInput(Component, Focusable):
    def __init__(self) -> None:
        self._text = ""

    def render(self, width: int) -> list[str]:
        cursor = "█" if self.focused else ""
        return [f"> {self._text}{cursor}"]
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

There are two ways to emit styled output, in decreasing order of preference:

1. **Structured**: build a `TextLine([...])` of styled `Span`s and flatten it with `line_to_ansi(line, width)`. Style stays data until the last moment, and alignment and clipping are resolved for you.
2. **Direct**: `apply_style(Style().bold(), "text")` returns an ANSI string. This is the escape hatch for content that is already ANSI-encoded; measure it with `tau.tui.utils.visible_width`, not `len`.

## Widgets

Widgets are a second, lower-level drawing layer: they render a **fixed-size** block of lines rather than growing to fit their content. Compose them by placing their lines yourself.

| Contrast | `Component` | Widget |
|----------|-------------|--------|
| Signature | `render(width) -> list[str]` | `render_lines(width, height) -> list[str]` |
| Height | Grows to fit its content | Fixed by the caller |
| Composition | Tree with input dispatch | Manual line placement |

Available in `tau.tui.widgets`:

| Module | Exports |
|--------|---------|
| `block` | `Block`, `Borders`, `Padding`, `Title`, `TitlePosition` |
| `list` | `List`, `ListItem`, `ListState`, `ListDirection` |
| `tabs` | `Tabs` |

`Block` and `List` expose `render_lines(width, height)`; `List` also takes a `ListState`. `Tabs` is a single row, so it exposes `render_line(width)`.

> **Removed:** `paragraph`, `table`, `gauge`, `scrollbar`, `sparkline`, `barchart`,
> `chart`, `canvas`, `calendar` and `clear`. Nothing in tau used them, and they
> were carrying the only remaining reason for several cell-level helpers to
> exist. The three above are the ones the app actually renders through.

Use one from a component by returning its lines, and place it with `splice_ansi` if it sits beside other content:

```python
from tau.tui.ansi_text import splice_ansi
from tau.tui.widgets.block import Block, Borders

class Panel(Component):
    def render(self, width: int) -> list[str]:
        frame = Block(borders=Borders.ALL).render_lines(width, 3)
        inner = Block(borders=Borders.ALL).inner(Rect(0, 0, width, 3))
        frame[inner.y] = splice_ansi(frame[inner.y], "hello", inner.x, inner.width, width)
        return frame
```

> **Removed:** the `Widget`/`StatefulWidget` protocols, `render_widget`, and
> `tau.tui.components.widget_bridge` (`WidgetComponent`, `render_widget_lines`).
> They existed to adapt between the cell grid and the component tree; with a
> single line-based contract there is nothing left to adapt.

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

While a reply is streaming, `StreamingMarkdownRenderer` is used instead: it freezes completed top-level blocks and reparses only the open tail, so cost stays proportional to the current block rather than the whole reply. It also holds back an inline construct whose closing delimiter has not arrived yet — `**bold`, `` `code ``, `[text](url` — because those are literal text until they close, and rendering them verbatim would show the raw syntax on screen until it snapped into place. The held run is bounded to the last line: once a newline arrives the line renders in full, so a delimiter the model never closes stalls nothing. A finished message is re-rendered once through `render_markdown()` for exact whole-document semantics, which is what resolves constructs the incremental path cannot see, such as a reference link whose `[ref]:` definition arrives in a later block.

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
from tau.tui import Text
from tau.tui.utils import strip_ansi


def test_text_wraps():
    assert [strip_ansi(line) for line in Text("alpha beta").render(6)] == ["alpha", "beta"]
```

`render(width)` returns the lines directly, so there is nothing to set up and nothing to flatten. Use `tau.tui.utils.strip_ansi` to assert on text alone, and `visible_width` to assert on layout — `len()` counts escape bytes and miscounts wide glyphs.

## Dependency Boundary

Modules under `tau.tui` may import only the standard library, third-party rendering and input dependencies, and other `tau.tui` modules. Runtime-aware behavior belongs in `tau.modes.interactive`.

This is enforced mechanically: `tests/test_tui_public_api.py` walks the AST of every file under `tau/tui/` and fails on any import of a `tau.*` module outside `tau.tui`.

The renderer keeps only the current transcript frame. Content wider than the available width wraps into buffer rows without loss and reflows on resize. Finalized message rows are cached as cells, so streaming updates do not re-parse ANSI styling across the whole session.

## Next Steps

- [Keybindings](keybindings.md): the named-action keymap and input handling
- [Themes](themes.md): theme dataclasses and color tokens
- [Architecture](architecture.md): how `tau.tui` fits under `tau.modes.interactive`
</content>

# Keybindings

## Default Keybindings

### Editor

| Shortcut | Action |
|----------|--------|
| Enter | Submit message |
| Shift+Enter | Insert newline |
| Ctrl+V | Paste |
| Ctrl+U | Clear input (kill to start) |
| Ctrl+K | Kill to end of line |
| Ctrl+W | Delete previous word |
| Ctrl+A / Home | Move to line start |
| Ctrl+E / End | Move to line end (Ctrl+E falls through to the app when input is empty) |
| Delete / Ctrl+D | Delete character at cursor |

### Message Queue

| Shortcut | Action |
|----------|--------|
| Alt+Enter | Queue as follow-up message (delivered when agent is fully idle) |
| Alt+Up | Restore queued messages into editor |

### App

| Shortcut | Action |
|----------|--------|
| Escape | Abort current turn; restore queued messages |
| Ctrl+C | Abort turn; double-press to quit |
| Ctrl+D | Quit (on empty input) |
| Ctrl+O | Toggle expand/collapse for thinking and tool-result blocks |
| Ctrl+E | Toggle template and skill blocks when the editor is empty |

### Pickers (model, theme, command palette)

| Shortcut | Action |
|----------|--------|
| Up / Ctrl+P | Move selection up |
| Down / Ctrl+N | Move selection down |
| Page Up | Move up a page |
| Page Down | Move down a page |
| Home | Jump to top |
| End | Jump to bottom |
| Enter / Tab | Confirm selection |
| Escape | Dismiss picker |

---

## Customising Keybindings

Pass a `KeyMap` to `App.create()` at startup:

```python
from tau.tui import KeyMap

overrides: KeyMap = {
    "app.details.toggle": ["ctrl+t"],
    "app.invocations.toggle": ["ctrl+r"],
    "tui.select.up": ["up", "ctrl+k"],
    "tui.select.down": ["down", "ctrl+j"],
}

app = await App.create(runtime, keybindings=overrides)
```

A `KeyMap` is `dict[str, list[str]]` — action name → list of key combos that trigger it.

### Available actions

| Action | Default keys | Description |
|--------|-------------|-------------|
| `tui.input.submit` | `enter` | Submit the current message |
| `tui.input.newline` | `shift+enter` | Insert a newline in the editor |
| `tui.input.clear` | `ctrl+u` | Kill from cursor to start |
| `tui.input.word_back` | `ctrl+w` | Delete previous word |
| `app.message.followup` | `alt+enter` | Queue as follow-up message |
| `app.message.dequeue` | `alt+up` | Restore queued messages into editor |
| `app.details.toggle` | `ctrl+o` | Toggle thinking and tool-result previews |
| `app.invocations.toggle` | `ctrl+e` | Toggle template and skill blocks |
| `tui.app.quit` | `ctrl+c`, `ctrl+d` | Quit tau |
| `tui.app.abort` | `escape`, `ctrl+c` | Abort the current turn |
| `tui.select.up` | `up`, `ctrl+p` | Move selection up |
| `tui.select.down` | `down`, `ctrl+n` | Move selection down |
| `tui.select.page_up` | `page_up` | Move up a page |
| `tui.select.page_down` | `page_down` | Move down a page |
| `tui.select.top` | `home` | Jump to top |
| `tui.select.bottom` | `end` | Jump to bottom |
| `tui.select.confirm` | `enter`, `tab` | Confirm selection |
| `tui.select.dismiss` | `escape` | Dismiss picker |
| `tui.scroll.up` | `page_up` | Scroll messages up one page |
| `tui.scroll.down` | `page_down` | Scroll messages down one page |
| `tui.scroll.top` | `home` | Scroll to the first message |
| `tui.scroll.bottom` | `end` | Scroll to the latest message |

The `tui.select.*` actions apply to components built on Tau's reusable
`SelectList` and to the corresponding navigation operations in specialized
pickers. A specialized picker may not support every operation; for example, a
compact autocomplete list only supports up and down.

Extension shortcuts are literal key combinations rather than `KeyMap` actions.
Tau checks them against the effective map and prevents extensions from
replacing reserved editor and application bindings. See
[Keyboard shortcuts](extensions.md#keyboard-shortcuts).

### Key notation

| Notation | Meaning |
|----------|---------|
| `a` | Letter key |
| `ctrl+a` | Ctrl+A |
| `shift+a` | Shift+A |
| `alt+a` | Alt/Option+A |
| `enter` | Enter/Return |
| `tab` | Tab |
| `escape` | Escape |
| `backspace` | Backspace |
| `delete` | Delete |
| `up` / `down` / `left` / `right` | Arrow keys |
| `page_up` / `page_down` | Page keys |
| `home` / `end` | Home/End keys |
| `space` | Space bar |

---

## Terminal Compatibility

Some key combinations may be intercepted by the terminal itself:

- **Ctrl+S** — often used for flow control
- **Ctrl+Q** — may quit the terminal
- **Ctrl+Z** — may suspend the process

If a binding doesn't work, choose a different combination.

---

## Reloading

`App.create(keybindings=...)` applies overrides at startup. Because these
overrides are supplied by the embedding application rather than loaded from a
settings file, `/reload` does not change them. Restart the application with a
new `KeyMap` to apply different overrides.

---

## Next Steps

- [Settings](settings.md) — Other configuration
- [Usage Guide](usage.md) — Keyboard shortcuts reference

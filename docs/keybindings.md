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

Tau currently supports programmatic overrides for a limited set of
application and reusable picker actions. Pass a `KeyMap` to `App.create()` at
startup:

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

### Effective customizable actions

| Action | Default keys | Description |
|--------|-------------|-------------|
| `app.details.toggle` | `ctrl+o` | Toggle thinking and tool-result previews |
| `app.invocations.toggle` | `ctrl+e` | Toggle template and skill blocks |
| `tui.select.up` | `up`, `ctrl+p` | Move selection up |
| `tui.select.down` | `down`, `ctrl+n` | Move selection down |
| `tui.select.page_up` | `page_up` | Move up a page |
| `tui.select.page_down` | `page_down` | Move down a page |
| `tui.select.top` | `home` | Jump to top |
| `tui.select.bottom` | `end` | Jump to bottom |
| `tui.select.confirm` | `enter`, `tab` | Confirm selection |
| `tui.select.dismiss` | `escape` | Dismiss picker |

The `tui.select.*` actions apply to components built on Tau's reusable
`SelectList`. Some specialized pickers currently handle their keys directly
and do not honor these overrides.

### Fixed bindings

The editor, message queue, quit, abort, and scroll bindings listed under
[Default Keybindings](#default-keybindings) are currently fixed in their
components. Entries for these actions exist in Tau's internal keymap for
conflict classification, but overriding them does not change their runtime
behavior:

- `tui.input.*`
- `app.message.*`
- `tui.app.quit`
- `tui.app.abort`
- `tui.scroll.*`

Extension shortcuts are literal key combinations rather than `KeyMap` actions.
Tau checks them against the configured map used for conflict classification
and prevents extensions from replacing reserved editor and application
bindings. See
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

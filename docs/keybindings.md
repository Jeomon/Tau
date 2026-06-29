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
| Ctrl+O | Toggle expand/collapse for tool result blocks |
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
    "tui.app.quit": ["ctrl+q"],
    "tui.input.submit": ["enter"],
    "app.message.followup": ["alt+enter"],
    "app.tool_results.toggle": ["ctrl+o"],
    "app.invocations.toggle": ["ctrl+e"],
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
| `app.tool_results.toggle` | `ctrl+o` | Toggle tool-result previews |
| `app.invocations.toggle` | `ctrl+e` | Toggle template and skill blocks |
| `tui.app.quit` | `ctrl+c`, `ctrl+d` | Quit tau |
| `tui.app.abort` | `ctrl+c` | Abort the current turn |
| `tui.select.up` | `up`, `ctrl+p` | Move selection up |
| `tui.select.down` | `down`, `ctrl+n` | Move selection down |
| `tui.select.page_up` | `page_up` | Move up a page |
| `tui.select.page_down` | `page_down` | Move down a page |
| `tui.select.top` | `home` | Jump to top |
| `tui.select.bottom` | `end` | Jump to bottom |
| `tui.select.confirm` | `enter`, `tab` | Confirm selection |
| `tui.select.dismiss` | `escape` | Dismiss picker |

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

Keybinding overrides are applied at startup. To pick up changes, restart tau or run `/reload`.

---

## Next Steps

- [Settings](settings.md) — Other configuration
- [Usage Guide](usage.md) — Keyboard shortcuts reference

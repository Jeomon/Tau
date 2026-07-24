# Keybindings

Tau's keyboard shortcuts come from two places: a small set of **named actions** that an embedding application can rebind, and a larger set of **hardcoded literal keys** in individual components. This page enumerates both, grouped by the context they apply in.

> **There is no keybindings config file.** Tau reads no `keybindings.json`, and `settings.json` has no keybindings key. The only override path is programmatic, see [Customizing Keybindings](#customizing-keybindings).

## Table of Contents

- [Key Notation](#key-notation)
- [Global](#global)
- [Editor](#editor)
- [Command Palette](#command-palette)
- [File Picker](#file-picker)
- [Autocomplete](#autocomplete)
- [Selectors](#selectors)
- [Session Picker](#session-picker)
- [Tree View](#tree-view)
- [Settings Panel](#settings-panel)
- [Overlays and Dialogs](#overlays-and-dialogs)
- [Trust Screen](#trust-screen)
- [Named Actions](#named-actions)
- [Customizing Keybindings](#customizing-keybindings)
- [Extension Shortcuts](#extension-shortcuts)

## Key Notation

Key combos are `modifier+key` strings. Modifier order and aliases do not matter: `shift+ctrl+x`, `ctrl+shift+x`, and `control+shift+x` are the same binding.

| Notation | Meaning |
|----------|---------|
| `a` | A letter key |
| `ctrl+a` | Ctrl held |
| `alt+a` | Alt / Option held |
| `shift+a` | Shift held |
| `enter` | Enter / Return |
| `escape` | Escape |
| `tab`, `space`, `backspace`, `delete`, `insert` | Named keys |
| `up`, `down`, `left`, `right` | Arrow keys |
| `pageup`, `pagedown` | Page keys |
| `home`, `end` | Home / End |
| `f1` … `f12` | Function keys |

Accepted modifier aliases: `ctrl`/`control`, `alt`/`opt`/`option`, `shift`, `super`/`cmd`/`command`/`win`/`meta`. Accepted key aliases: `esc`→`escape`, `return`→`enter`, `del`→`delete`, `spacebar`/`" "`→`space`, `pgup`/`page_up`→`pageup`, `pgdn`/`page_down`→`pagedown`.

On macOS, `alt` is displayed as `option` in on-screen hints. The binding string is unchanged.

## Global

Active whenever no modal has captured input.

| Key | Action |
|-----|--------|
| `escape` | Abort the running turn |
| `escape` `escape` | Double-press while idle, runs the configured `double_escape_action` |
| `ctrl+c` | Abort the running turn |
| `ctrl+c` `ctrl+c` | Double-press while idle (within 0.5 s): quit. A single press clears the editor |
| `ctrl+d` | Quit |
| `ctrl+o` | Toggle expanded thinking and tool-result details |
| `ctrl+e` | Toggle template and skill invocation blocks (when the editor is empty) |
| `ctrl+g` | Compose the prompt in an external editor |

### External editor

`ctrl+g` writes the current prompt to a temporary `.tau.md` file, hands the terminal
to an editor, and reads the text back when it exits. The command is resolved in this
order:

1. `external_editor` in `settings.json`: e.g. `"code --wait"`, or a quoted path
   containing spaces
2. `$VISUAL`
3. `$EDITOR`
4. `notepad` on Windows, otherwise `nano`

**A non-zero exit leaves the prompt untouched**, so `:cq` in vim cancels the edit
while `:wq` applies it. One trailing newline is stripped (editors add it on save);
any beyond that are yours. The temporary file is always removed.


Double-Escape while idle dispatches on the `double_escape_action` setting:

| Value | Behavior |
|-------|----------|
| `"clear"` | Clear TUI messages *(default)* |
| `"tree"` | Open the tree selector |
| `"fork"` | Clone the current branch |
| `"none"` | Do nothing |

See [Settings](settings.md#ui--display).

## Editor

The prompt input. `ctrl+e` and `ctrl+d` fall through to the global handlers when the editor is empty.

### Submission

| Key | Action |
|-----|--------|
| `enter` | Submit the message. A trailing `\` becomes a newline instead |
| `shift+enter` | Insert a newline |
| `alt+enter` | Queue as a follow-up, delivered once the agent is fully idle |
| `ctrl+up` | Restore queued messages into the editor |

### Movement

| Key | Action |
|-----|--------|
| `left` / `right` | Move one character |
| `alt+left` / `ctrl+left` | Move one word left |
| `alt+right` / `ctrl+right` | Move one word right |
| `home` / `ctrl+a` | Move to line start |
| `end` / `ctrl+e` | Move to line end (`ctrl+e` only while the editor has text) |
| `up` | Move up a visual row; on the first row, recall the previous history entry |
| `down` | Move down a visual row; on the last row, recall the next history entry |

### Editing

| Key | Action |
|-----|--------|
| `backspace` | Delete the character before the cursor |
| `delete` / `ctrl+d` | Delete the character at the cursor (`ctrl+d` only while the editor has text) |
| `ctrl+w` | Delete the previous word |
| `ctrl+u` | Kill from the cursor to the start |
| `ctrl+k` | Kill from the cursor to the end |
| `ctrl+z` | Undo |
| `ctrl+y` | Redo |
| `ctrl+v` | Paste from the clipboard |

Bracketed paste is handled natively: carriage returns are stripped and tabs become four spaces. A left-button mouse click inside the editor moves the cursor to the clicked position.

## Command Palette

Opened by typing `/` at the start of the editor.

| Key | Action |
|-----|--------|
| `up` / `ctrl+p` | Move selection up |
| `down` / `ctrl+n` | Move selection down |
| `enter` | Execute the selected command |
| `tab` / `right` | Insert the selected command without executing it |
| `escape` | Dismiss and clear the editor |

## File Picker

Opened by typing `@` in the editor.

| Key | Action |
|-----|--------|
| `up` / `ctrl+p` | Move selection up |
| `down` / `ctrl+n` | Move selection down |
| `enter` / `tab` | Accept the file, or descend into the directory |
| `escape` | Close the picker |

## Autocomplete

The inline completion popup for extension providers and command arguments. Only unmodified keys are handled; anything with `ctrl`, `alt`, `shift`, or `meta` falls through to the editor.

| Key | Action |
|-----|--------|
| `up` | Move selection up |
| `down` | Move selection down |
| `tab` / `enter` | Accept the completion |
| `escape` | Dismiss |

## Selectors

Shared by the theme (`/theme`), voice, and thinking-effort selectors.

| Key | Action |
|-----|--------|
| `up` / `down` | Move selection (live preview in the theme selector) |
| `enter` / `tab` | Confirm |
| `escape` | Cancel and restore the previous value |

The model selector adds scope switching and search:

| Key | Action |
|-----|--------|
| `up` / `down` | Move selection |
| `left` / `right` | Toggle scope |
| `tab` | Jump to the next section |
| `enter` | Confirm |
| `escape` | Cancel |
| `backspace` | Delete a search character |
| *(type)* | Fuzzy-search |

The extension selector also accepts `j` / `k` for down / up. The config selector uses `space` to toggle an entry, and supports type-to-search with `backspace`.

## Session Picker

Opened with `/resume`.

| Key | Action |
|-----|--------|
| `up` / `down` | Move selection |
| `tab` | Toggle scope between the current folder and all sessions |
| `ctrl+r` | Cycle the sort mode |
| `ctrl+d` | Start deleting the selected session |
| `enter` | Open the session, or confirm a pending delete |
| `escape` | Cancel a pending delete, or close the picker |
| `backspace` | Delete a search character |
| *(type)* | Fuzzy-search |

## Tree View

Opened with `/tree` or via double-Escape when `double_escape_action` is `"tree"`.

| Key | Action |
|-----|--------|
| `up` / `down` | Move selection |
| `pageup` / `pagedown` | Page up / down |
| `ctrl+left` / `alt+left` | Page up |
| `ctrl+right` / `alt+right` | Page down |
| `left` | Fold the current branch segment, or jump to the previous segment |
| `right` | Unfold the segment, or jump to the next segment |
| `enter` / `tab` | Confirm |
| `escape` | Cancel |
| `shift+L` | Edit the label on the selected node |
| `shift+T` | Toggle label timestamps |
| `backspace` | Delete a search character |
| *(type)* | Fuzzy-search |

### Tree Filters

| Key | Filter |
|-----|--------|
| `ctrl+d` | Reset to the default view |
| `ctrl+t` | Toggle "no tools" |
| `ctrl+u` | Toggle "user only" |
| `ctrl+l` | Toggle "labeled only" |
| `ctrl+a` | Toggle "all" |
| `ctrl+f` | Cycle through filters |

The starting filter is set by `tree_filter_mode` in [Settings](settings.md#ui--display).

### Label Editing

While editing a node label:

| Key | Action |
|-----|--------|
| `enter` | Commit the label |
| `escape` / `ctrl+c` | Cancel |
| `backspace` | Delete a character |

## Settings Panel

Opened with `/settings`.

| Key | Action |
|-----|--------|
| `up` / `down` | Move between rows |
| `tab` | Cycle tabs |
| `enter` | Cycle a value, open a sub-panel, or enter text-edit mode |
| `space` | Same as `enter`, unless editing text, then it inserts a space |
| `escape` | Close a sub-panel, or close the panel |
| `backspace` | Delete a search character, or an edit character while editing |
| *(type)* | Fuzzy-search rows |

## Overlays and Dialogs

Info overlays are modal: `escape` closes them and every other key is swallowed.

Prompt overlays (single-line input):

| Key | Action |
|-----|--------|
| `enter` | Confirm |
| `escape` | Cancel |
| `backspace` | Delete a character |

Editor overlays (multi-line input):

| Key | Action |
|-----|--------|
| `enter` | Split the line |
| `backspace` | Delete a character |
| `up` / `down` / `left` / `right` / `home` | Move the cursor |
| `escape` | Cancel |

> The editor overlay advertises `Ctrl+S to save` in its footer, but that binding does not currently fire: the handler compares against a key string the input parser never produces. Use `escape` to dismiss.

## Trust Screen

Shown on first use of an untrusted project directory.

| Key | Action |
|-----|--------|
| `up` / `down` | Move selection |
| `enter` | Confirm the highlighted choice |
| `escape` | Cancel |

See [Project Trust](settings.md#project-trust).

## Named Actions

These are the only bindings routed through the keymap, and therefore the only ones an embedder can rebind. Everything else listed above is a hardcoded literal.

| Action | Default keys | Description |
|--------|--------------|-------------|
| `tui.select.up` | `up`, `ctrl+p` | Move selection up |
| `tui.select.down` | `down`, `ctrl+n` | Move selection down |
| `tui.select.page_up` | `page_up` | Page up in a list |
| `tui.select.page_down` | `page_down` | Page down in a list |
| `tui.select.top` | `home` | Jump to the first item |
| `tui.select.bottom` | `end` | Jump to the last item |
| `tui.select.confirm` | `enter`, `tab` | Confirm the selection |
| `tui.select.dismiss` | `escape` | Dismiss the list |
| `tui.input.submit` | `enter` | Submit the message |
| `tui.input.newline` | `shift+enter` | Insert a newline |
| `tui.input.clear` | `ctrl+u` | Kill from the cursor to the start |
| `tui.input.word_back` | `ctrl+w` | Delete the previous word |
| `app.message.followup` | `alt+enter` | Queue as a follow-up message |
| `app.message.dequeue` | `ctrl+up` | Restore queued messages into the editor |
| `app.details.toggle` | `ctrl+o` | Toggle thinking and tool-result details |
| `app.invocations.toggle` | `ctrl+e` | Toggle template and skill blocks |
| `app.editor.external` | `ctrl+g` | Compose the prompt in an external editor |
| `tui.app.quit` | `ctrl+c`, `ctrl+d` | Quit |
| `tui.app.abort` | `escape`, `ctrl+c` | Abort the current turn |
| `tui.scroll.up` | `page_up` | Scroll the message list up a page |
| `tui.scroll.down` | `page_down` | Scroll down a page |
| `tui.scroll.top` | `home` | Scroll to the first message |
| `tui.scroll.bottom` | `end` | Scroll to the latest message |

`tui.app.abort` is checked before `tui.app.quit`, so `ctrl+c` always aborts first; quitting with `ctrl+c` happens only through the double-press timer. `ctrl+d` reaches `tui.app.quit` directly.

The `tui.select.*` actions apply to every component built on `SelectList` and to the equivalent operations in specialized pickers. Not every picker supports every operation: a compact autocomplete list only handles up and down.

## Customizing Keybindings

Overrides are supplied by the embedding application at startup, as a `KeyMap`, a `dict[str, list[str]]` mapping action name to key combos.

```python
import asyncio

from tau.tui import KeyMap
from tau.modes.interactive.app import App


async def main() -> None:
    overrides: KeyMap = {
        "tui.select.up": ["up", "ctrl+k"],
        "tui.select.down": ["down", "ctrl+j"],
        "app.details.toggle": ["ctrl+t"],
        "app.invocations.toggle": ["ctrl+r"],
    }

    app = await App.create(runtime, keybindings=overrides)
    await app.run()


asyncio.run(main())
```

An override replaces all keys for that action. Actions you omit keep their defaults.

Inside `tau.tui` alone, the same registry is reachable directly:

```python
from tau.tui import configure_keybindings, get_keybindings

configure_keybindings({"tui.select.confirm": ["enter"]})

km = get_keybindings()
km.add_binding("tui.select.up", "ctrl+k")   # Append without removing defaults
km.bind("tui.select.dismiss", ["escape", "ctrl+["])  # Replace outright
print(km.keys_for("tui.select.up"))          # ['up', 'ctrl+p', 'ctrl+k']
print(km.effective_map())                    # Full action → keys mapping
```

`configure_keybindings()` replaces the global singleton, so call it once at startup before any component is constructed.

Because overrides come from the embedder rather than a settings file, `/reload` does not pick up changes. Restart with a new `KeyMap`.

## Extension Shortcuts

Extensions register **literal key combos**, not named actions:

```python
def register(tau):
    @tau.register_shortcut("ctrl+g", "Open greeter")
    async def on_ctrl_g(ctx):
        await ctx.ui.notify("Hello")
```

Extension shortcuts are installed at the front of the handler chain, so they take priority over the editor and global handlers. Tau checks each against the effective keymap and rejects any combo bound to a reserved action: every named action except `app.details.toggle` and `app.invocations.toggle`. See [Extensions](extensions.md#keyboard-shortcuts).

For raw, unfiltered terminal input, an extension can use `ctx.ui.on_terminal_input(handler)`, which runs before the editor and overlays.

## Terminal Compatibility

Some combinations are intercepted by the terminal before Tau sees them:

| Key | Typical conflict |
|-----|------------------|
| `ctrl+s` | XON/XOFF flow control |
| `ctrl+q` | Flow control resume, or quit |
| `ctrl+z` | Job-control suspend in some shells |

If a binding does not respond, pick a different combination.

## Next Steps

- [Settings](settings.md): `double_escape_action`, `tree_filter_mode`, and other behavior toggles
- [Terminal UI](tui.md): the input event model behind these bindings
- [Extensions](extensions.md): registering custom shortcuts
</content>

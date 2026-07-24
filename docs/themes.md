> Tau can create themes. Ask it to build one for your terminal.

# Themes

Themes are YAML files that define the colors of Tau's terminal UI. Every token is optional. An omitted token falls back to the built-in default, so a theme can be three lines or forty.

## Table of Contents

- [Locations and Precedence](#locations-and-precedence)
- [Built-In Themes](#built-in-themes)
- [Selecting a Theme](#selecting-a-theme)
- [Creating a Custom Theme](#creating-a-custom-theme)
- [Theme Format](#theme-format)
- [Color Values](#color-values)
- [Color Tokens](#color-tokens)
- [Non-Color Keys](#non-color-keys)
- [Worked Example](#worked-example)
- [Python Theme API](#python-theme-api)

## Locations and Precedence

Tau loads every `.yaml`, `.yml`, and `.json` file in each themes directory, non-recursively. Later sources override earlier ones by theme name:

| Source | Location | Precedence |
|--------|----------|------------|
| Built-in | `tau/builtins/themes/` | Lowest |
| Global | `~/.tau/themes/` | Overrides built-in |
| Project | `.tau/themes/` (in the working directory) | Overrides global |
| Runtime | Registered by an extension via `tau.register_theme()` | Survives reloads |

Names are matched case-insensitively. The **`name` field inside the file** determines the theme name; the filename is ignored. A file with no `name` is skipped with an error.

## Built-In Themes

| Theme | Colors |
|-------|--------|
| `dark` | Named ANSI: adapts to the terminal palette. The default |
| `light` | Hex |
| `ayu-dark` | Hex |
| `catppuccin` | Hex |
| `dracula` | Hex |
| `everforest` | Hex |
| `gruvbox` | Hex |
| `horizon` | Hex |
| `kanagawa` | Hex |
| `material-ocean` | Hex |
| `monokai` | Hex |
| `night-owl` | Hex |
| `nord` | Hex |
| `one-dark` | Hex |
| `rose-pine` | Hex |
| `solarized-dark` | Hex |
| `tokyo-night` | Hex |

## Selecting a Theme

```bash
tau --theme tokyo-night      # This run only
tau -t tokyo-night           # Short form
```

```json
{ "theme": "tokyo-night" }
```

```text
/theme                       # Interactive picker with ↑↓ live preview
/settings                    # Theme row opens the same picker
```

In both pickers, ↑/↓ previews the theme live, Enter applies and persists it, Escape restores the previous theme.

The special value `auto` is not a theme file: it queries the terminal background over OSC 11 and resolves to `light` or `dark` by perceived luminance (ITU-R BT.601). If the terminal does not answer, it falls back to `dark`.

## Creating a Custom Theme

1. Create the directory and file:

```bash
mkdir -p ~/.tau/themes
$EDITOR ~/.tau/themes/my-theme.yaml
```

2. Write the theme. Only `name` is required:

```yaml
name: my-theme

colors:
  heading:         { color: "#a78bfa", bold: true }
  you_label:       { color: "#a78bfa", bold: true }
  assistant_label: { color: "#50fa7b", bold: true }
  accent:          "#a78bfa"
  divider:         "#374151"
```

3. Apply it:

```bash
tau --theme my-theme
```

Copying a built-in from `tau/builtins/themes/` and editing the colors you care about is the fastest route to a complete theme.

## Theme Format

```yaml
name: my-theme            # Required, unique, case-insensitive

vars:                     # Optional reusable palette
  brand: "#a78bfa"
  subtle: "#6b7280"

colors:                   # Optional; every token falls back to a default
  accent: brand
  muted: subtle
  heading: { color: brand, bold: true }

input:                    # Optional
  prefix: "❯ "
  placeholder: "Ask anything…"

spinner:                  # Optional
  frames: ["◐", "◓", "◑", "◒"]
  interval_ms: 100

code_syntax_style: monokai   # Optional Pygments style
show_thinking: true          # Optional
show_tool_calls: true        # Optional
show_images: true            # Optional
```

Top-level keys the loader reads: `name`, `vars`, `colors`, `input`, `spinner`, `show_thinking`, `show_tool_calls`, `show_images`, `code_syntax_style`. Anything else produces an `unknown key` warning and is ignored.

### vars

`vars` defines named colors once and lets `colors` reference them by name. A var may point at another var; the chain is followed until it resolves. Cyclic or dangling references settle on their last value rather than erroring.

```yaml
vars:
  base: "#7aa2f7"
  primary: base        # resolves to "#7aa2f7"

colors:
  accent: primary
  heading: { color: primary, bold: true }
```

> **Formats:** `.yaml` and `.yml` are preferred. `.json` is accepted for backwards compatibility and uses the same key names.

## Color Values

| Form | Example | Renders as |
|------|---------|------------|
| Hex | `"#a78bfa"` | Exact 24-bit RGB, identical on every terminal |
| Named ANSI | `bright_cyan` | The terminal's own palette color; adapts to the user's terminal theme |
| Var reference | `brand` | Whatever the `vars` entry resolves to |
| Attributed | `{ color: "#a78bfa", bold: true, italic: true, dim: true }` | Any of the above, plus text attributes |

Valid names: `black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`, `default`, and their `bright_` variants (`bright_black`, `bright_red`, …).

Hex must be the full six-digit `#rrggbb`. A malformed value (`#fff`, a typo, a number) falls back to that token's built-in default rather than erroring, and is reported as a load warning.

**Which to use?** Named ANSI for a theme that should blend into whatever palette the user already runs. That is why built-in `dark` uses names. Hex for a designed, branded look that must be identical everywhere.

Some tokens have an attribute applied automatically by the loader, so you only need to supply the color:

| Auto-applied | Tokens |
|--------------|--------|
| Bold | `heading`, `bold`, `you_label`, `assistant_label`, `error_label`, `selected_label`, `selected_dir`, `emphasis`, `error` |
| Italic | `quote`, `italic`, `thinking` |

You can still pass explicit `bold` / `italic` / `dim` on any token; they combine.

## Color Tokens

All tokens live under `colors:` and are optional.

### Chrome and Semantic Roles

| Token | Purpose |
|-------|---------|
| `divider` | Separator lines between turns |
| `divider_command` | Divider in command mode |
| `divider_execute` | Divider in shell-execute mode |
| `accent` | Highlighted values and paths |
| `border` | Modal and box borders |
| `muted` | Dim chrome and secondary text |
| `emphasis` | Highlighted or active item |
| `success` | Positive / current state |
| `warning` | Caution and highlight |
| `error` | Error state |

The six semantic roles (`muted`, `emphasis`, `success`, `warning`, `error`, `accent`) are mirrored onto the message theme, so setting one recolors it everywhere: chrome, selectors, modals, and tool renderers alike.

### Messages

| Token | Purpose |
|-------|---------|
| `you_label` | The "You" label on user messages |
| `assistant_label` | The assistant label |
| `tool_arrow` | Arrow glyph preceding a tool call |
| `tool_result_ok` | Successful tool result text |
| `tool_result_err` | Failed tool result text |
| `thinking` | Extended-thinking block text |
| `error_label` | Error label |
| `dim` | Dimmed message text |
| `stream_cursor` | Cursor shown while a response streams |

### Diffs

| Token | Purpose |
|-------|---------|
| `diff_added` | Added lines |
| `diff_removed` | Removed lines |
| `diff_context` | Unchanged context lines |
| `diff_hunk` | Hunk headers |

### Markdown

| Token | Purpose |
|-------|---------|
| `heading` | Headings |
| `code_inline` | Inline `` `code` `` |
| `code_block` | Fenced code block content |
| `code_block_border` | Fence lines |
| `quote` | Blockquote text |
| `quote_border` | Blockquote border |
| `hr` | Horizontal rules |
| `list_bullet` | List bullets |
| `bold` | `**bold**` text |
| `italic` | `_italic_` text |
| `strikethrough` | `~~strikethrough~~` text |
| `link_text` | Link label |
| `link_url` | Link URL |

> `body` (default markdown body text) is read by the loader but is missing from its validation list, so setting it works while also emitting a spurious `unknown color 'body'` warning.

### Select Lists and Pickers

| Token | Purpose |
|-------|---------|
| `selected_label` | Label of the highlighted row |
| `selected_desc` | Description of the highlighted row |
| `selected_dir` | Emphasised entry, e.g. a directory in the file picker |
| `selected_bg` | Full-row background behind the highlighted row |
| `normal_label` | Label of a non-highlighted row |
| `normal_desc` | Description of a non-highlighted row |
| `indicator` | Scroll and position indicators |
| `empty` | "No results" placeholder text |

### Spinner

| Token | Purpose |
|-------|---------|
| `spinner_frame` | The animated glyph |
| `spinner_label` | The status label beside it |

## Non-Color Keys

### input

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `prefix` | string | `"❯ "` | Prompt prefix |
| `placeholder` | string | `""` | Placeholder text in an empty editor |

### spinner

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `frames` | string[] | `["▖", "▘", "▝", "▗"]` | Animation frames, cycled each tick |
| `interval_ms` | integer | `120` | Milliseconds between frames |
| `label_thinking` | string | `"Thinking…"` | Label while the model reasons |
| `label_tool_calling` | string | `"Tool Calling…"` | Label during tool execution |
| `label_compacting` | string | `"Compacting…"` | Label during context compaction |

`SpinnerTheme` also has `label_working` and `label_streaming`, but the YAML loader does not read them. Set those through the [Python API](#python-theme-api).

### Message visibility

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `show_thinking` | boolean | `true` | Show thinking blocks |
| `show_tool_calls` | boolean | `true` | Show tool call and result blocks |
| `show_images` | boolean | `true` | Render inline images |

These mirror the identically named [settings](settings.md#ui--display); a theme sets the baseline, settings override per user.

### code_syntax_style

Fenced code blocks are highlighted with [Pygments](https://pygments.org/styles/).

```yaml
code_syntax_style: monokai   # Any Pygments style name; "" disables highlighting
```

Pick a dark style (`monokai`, `dracula`, `nord`, `gruvbox-dark`) for dark themes and a light one (`friendly`, `default`, `xcode`, `solarized-light`) for light backgrounds. `""` falls back to the flat `code_block` color.

## Worked Example

The bundled `tokyo-night` theme, in full (`tau/builtins/themes/tokyo-night.yaml`):

```yaml
name: tokyo-night

colors:
  # UI chrome
  divider:          "#292e42"
  spinner_frame:    "#7aa2f7"
  spinner_label:    "#565f89"

  # Markdown
  heading:          { color: "#bb9af7", bold: true }
  code_inline:      "#e0af68"
  code_block:       "#9ece6a"
  code_block_border: "#3b4261"
  quote:            { color: "#565f89", italic: true }
  quote_border:     "#3b4261"
  hr:               "#3b4261"
  list_bullet:      "#7aa2f7"
  link_text:        "#7dcfff"
  link_url:         "#565f89"

  # Messages
  you_label:        { color: "#7aa2f7", bold: true }
  assistant_label:  { color: "#9ece6a", bold: true }
  tool_arrow:       "#ff9e64"
  tool_result_ok:   "#565f89"
  tool_result_err:  "#f7768e"
  thinking:         { color: "#565f89", italic: true }
  error_label:      { color: "#f7768e", bold: true }
  dim:              "#565f89"
  stream_cursor:    "#c0caf5"

  # Select list
  selected_label:   { color: "#bb9af7", bold: true }
  selected_desc:    "#7dcfff"
  normal_label:     "#a9b1d6"
  normal_desc:      "#565f89"
  indicator:        "#565f89"
  empty:            "#565f89"

input:
  prefix:      "❯ "
  placeholder: ""

spinner:
  frames:      ["◐", "◓", "◑", "◒"]
  interval_ms: 100
```

Note what it leaves out: no `vars`, no diff colors, no semantic roles, no `code_syntax_style`. All of those inherit the built-in defaults.

## Python Theme API

Themes are dataclasses under `tau.tui.theme`. Build one directly when you need behavior the YAML loader does not expose: `terminal_bg`, `selector_arrow`, `label_working`, `label_streaming`, or `stat_color`.

```python
from tau.tui.theme import LayoutTheme, SpinnerTheme
from tau.tui.style import Style

theme = LayoutTheme(
    divider=Style().with_fg("bright_magenta"),
    accent=Style().with_fg("#a78bfa"),
    selector_arrow="▸",
    terminal_bg="#1e1e2e",
    spinner=SpinnerTheme(
        frames=["◐", "◓", "◑", "◒"],
        interval_ms=100,
        label_working="Working…",
        label_streaming="Streaming…",
    ),
)
```

Register it from an extension so it appears in `/theme`:

```python
def register(tau):
    from tau.tui.theme import LayoutTheme, SpinnerTheme

    tau.register_theme(
        "my-theme",
        lambda: LayoutTheme(spinner=SpinnerTheme(label_thinking="Pondering…")),
    )
```

`register_theme()` accepts a `LayoutTheme` instance or a zero-argument factory; the factory form is preferred so the theme is only built when selected. Runtime-registered themes survive `/reload`, unlike file-backed ones which are re-read.

### Theme Classes

| Class | Controls |
|-------|----------|
| `LayoutTheme` | Top level: dividers, semantic roles, `border`, `selector_arrow`, `terminal_bg`, and all sub-themes |
| `MessageTheme` | Message labels, tool results, thinking, diff styles, visibility toggles |
| `MarkdownTheme` | Headings, code, quotes, links, emphasis, `code_syntax_style` |
| `SpinnerTheme` | Frames, interval, colors, and every status label |
| `InputTheme` | Prompt `prefix` and `placeholder` |
| `SelectListTheme` | Picker rows, indicator, and `selected_bg` |

### LayoutTheme fields not available in YAML

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `selector_arrow` | `str` | `"❯"` | Selection-cursor glyph in every picker list |
| `terminal_bg` | `str \| None` | `None` | Terminal background applied over OSC 11 at startup, e.g. `"#1e1e2e"` or `"rgb(30,30,46)"`. `None` leaves the terminal unchanged |

### SpinnerTheme

| Field | Type | Default | In YAML |
|-------|------|---------|---------|
| `frames` | `list[str]` | `["▖", "▘", "▝", "▗"]` | Yes |
| `interval_ms` | `int` | `120` | Yes |
| `frame_color` | `Style` | bright cyan | As `colors.spinner_frame` |
| `label_color` | `Style` | inherit | As `colors.spinner_label` |
| `stat_color` | `Style` | bright black | No |
| `label_working` | `str` | `"Working…"` | No |
| `label_thinking` | `str` | `"Thinking…"` | Yes |
| `label_streaming` | `str` | `"Streaming…"` | No |
| `label_tool_calling` | `str` | `"Tool Calling…"` | Yes |
| `label_compacting` | `str` | `"Compacting…"` | Yes |

Color-bearing fields are `Style` objects, not functions. Build them with the fluent API: `Style().with_fg("#a78bfa").bold()`.

## Next Steps

- [Settings](settings.md): set the default theme
- [Terminal UI](tui.md): the component framework themes style
- [Extensions](extensions.md): registering themes from an extension
</content>

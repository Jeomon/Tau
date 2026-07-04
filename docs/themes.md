# Themes

This page explains how to customise tau's terminal appearance.

## Built-In Themes

| Theme | Description |
|-------|-------------|
| `dark` | Default terminal-adaptive dark theme |
| `light` | Light theme using explicit RGB colours |

## Base Themes

Both built-ins are complete themes and useful starting points for custom
variants.

## Set a Theme

### Command line

```bash
tau --theme dark
```

### Settings

Set your default theme in `~/.tau/settings.json` or `.tau/settings.json`:

```json
{
  "theme": "dark"
}
```

### Interactive

```text
/theme
```

Opens an interactive picker. Use ↑↓ to preview, Enter to apply.
The theme submenu in `/settings` uses the same ↑/↓ live preview. Press Enter to
apply or Escape to restore the previous theme.

---

## Creating a Custom Theme

Save a YAML file to either:

- **Global**: `~/.tau/themes/my_theme.yaml`
- **Project**: `.tau/themes/my_theme.yaml`

Then use it with `--theme my_theme` or `"theme": "my_theme"` in settings.

The `name` field in the file determines the theme name used in settings and `--theme` — the filename is ignored.

> **Formats**: `.yaml` and `.yml` are recommended. `.json` is also accepted for backwards compatibility.

### Color values

Every color token accepts three forms, and you can mix them freely in one theme:

| Form | Example | Renders as |
|------|---------|------------|
| **Hex** (24-bit truecolor) | `"#a78bfa"` | exact RGB, identical on every terminal |
| **Named ANSI** | `bright_cyan` | the terminal's own palette color (adapts to the user's terminal theme) |
| **With attributes** | `{ color: "#a78bfa", bold: true, italic: true, dim: true }` | either hex or named, plus styling |

Valid names: `black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`, `default`, and their `bright_` variants (e.g. `bright_black`).

**Which to use?**
- **Named ANSI** for a theme that should *blend into the user's terminal* (the built-in `dark` theme uses these, so its colors match whatever palette the user has set). Best for a default/adaptive theme.
- **Hex** for a theme with an *exact, branded look* that should appear the same on every machine (the built-in `light` theme uses hex). Best for a distinctive, designed theme.

An unrecognized value (a typo, or malformed hex like `#fff`) silently falls back to that token's built-in default rather than erroring. Hex must be full `#rrggbb`.

### Syntax highlighting

Fenced code blocks are syntax-highlighted with [Pygments](https://pygments.org/styles/). Choose the style with a top-level `code_syntax_style` key:

```yaml
code_syntax_style: monokai   # any Pygments style name; "" disables highlighting
```

Pick a dark style (`monokai`, `dracula`, `nord`, `gruvbox-dark`, …) for dark themes and a light style (`friendly`, `default`, `xcode`, `solarized-light`, …) for light backgrounds. Setting it to `""` falls back to the flat `code_block` color.

### Theme YAML format

```yaml
name: my_theme

# Toggle message visibility
show_thinking:   true   # show/hide the model's thinking blocks
show_tool_calls: true   # show/hide tool call output

# Syntax highlighting for fenced code blocks (Pygments style; "" to disable)
code_syntax_style: monokai

colors:
  # UI chrome — hex for exact colors, or names like `bright_black` to use the terminal palette
  divider:           "#374151"
  spinner_frame:     "#0ea5e9"
  spinner_label:     "#6b7280"

  # Markdown
  heading:           { color: "#a78bfa", bold: true }
  code_inline:       "#f1fa8c"
  code_block:        "#50fa7b"
  code_block_border: "#374151"
  quote:             { color: "#6b7280", italic: true }
  quote_border:      "#374151"
  hr:                "#374151"
  list_bullet:       "#a78bfa"
  bold:              "#ffffff"        # **bold** text
  italic:            "#aaaaaa"        # _italic_ text
  strikethrough:     "#888888"        # ~~strikethrough~~ text
  link_text:         "#50fa7b"
  link_url:          "#6b7280"

  # Messages
  you_label:         { color: "#a78bfa", bold: true }
  assistant_label:   { color: "#50fa7b", bold: true }
  tool_arrow:        "#ffb86c"
  tool_result_ok:    "#6b7280"
  tool_result_err:   "#ef4444"
  thinking:          { color: "#6b7280", italic: true }
  error_label:       { color: "#ef4444", bold: true }
  dim:               "#6b7280"
  stream_cursor:     "#0ea5e9"

  # Select list
  selected_label:    { color: "#a78bfa", bold: true }
  selected_desc:     "#50fa7b"
  selected_bg:       "#1a1a2e"        # full-row background on selected item (optional)
  normal_label:      "#6b7280"
  normal_desc:       "#6b7280"
  indicator:         "#6b7280"
  empty:             "#6b7280"

input:
  prefix:      "❯ "
  placeholder: "Ask Anything…"

spinner:
  frames:          ["▖", "▘", "▝", "▗"]
  interval_ms:     110
  label_thinking:  "Thinking…"      # shown while the model generates
  label_tool:      "Tool Calling…"  # shown during tool execution
  label_compacting: "Compacting…"   # shown during context compaction
```

All fields are optional — omitted fields fall back to the default theme's values.

### Color values

Each color accepts:
- Hex string: `"#0ea5e9"`
- Object with styling: `{ color: "#a78bfa", bold: true }` or `{ color: "#6b7280", italic: true }`

The `bold`, `italic`, `dim` modifiers can be combined: `{ color: "#a78bfa", bold: true, italic: true }`.

### Starting from a built-in

The built-in themes are a good starting point. Copy one from
`tau/builtins/themes/` and modify the colors you want to change; omitted fields
use the loader's defaults.

---

## Python Theme API

For programmatic customisation (e.g. from an extension), tau exposes dataclass-based theme objects.

### `LayoutTheme`

The top-level theme that wires together all sub-themes:

```python
from tau.tui.theme import LayoutTheme, SpinnerTheme

theme = LayoutTheme(
    spinner=SpinnerTheme(
        frames=["◐", "◓", "◑", "◒"],
        interval_ms=100,
    ),
)
```

Pass to `App.create(runtime, theme=theme)` or register via an extension:

```python
def register(tau):
    from tau.tui.theme import LayoutTheme, SpinnerTheme
    tau.register_theme("my-theme", LayoutTheme(
        spinner=SpinnerTheme(label_thinking="Working…"),
    ))
```

### `SpinnerTheme`

Controls the animated spinner shown while the agent is working:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `frames` | `list[str]` | `["▖","▘","▝","▗"]` | Animation frames cycled each tick |
| `interval_ms` | `int` | `120` | Milliseconds between frame advances |
| `frame_color` | `ColorFn` | bright cyan | Color applied to the spinner frame character |
| `label_color` | `ColorFn` | passthrough | Color applied to the status label text |
| `label_thinking` | `str` | `"Thinking…"` | Label shown while the model is generating |
| `label_tool` | `str` | `"Tool Calling…"` | Label shown while a tool call is in progress |
| `label_compacting` | `str` | `"Compacting…"` | Label shown during context compaction |

### Other sub-themes

| Class | Controls |
|-------|---------|
| `MessageTheme` | Chat message colours, `show_thinking`, `show_tool_calls`, markdown styles |
| `MarkdownTheme` | Headings, code blocks, links, `bold`, `italic`, `strikethrough` |
| `InputTheme` | Input prompt `prefix` and `placeholder` text |
| `SelectListTheme` | Command palette appearance, `selected_bg` for full-row highlight |

---

## Next Steps

- [Settings](settings.md) — Set default theme
- [Extensions](extensions.md) — Register themes from extensions
- [Usage Guide](usage.md) — Interactive mode

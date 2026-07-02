# Extension Settings

All extensions should use `ExtensionSettings` for type-safe configuration access. It handles both simple flat structures and complex nested configurations seamlessly.

## Quick Start

Define your configuration schema as a dataclass and use `ExtensionSettings`:

```python
from dataclasses import dataclass, field
from tau.extensions import ExtensionSettings

@dataclass
class MyExtConfig:
    api_key: str = ""
    timeout_ms: int = 5000

def register(tau):
    config = ExtensionSettings(MyExtConfig, tau.config)
    api_key = config.get("api_key")
    timeout = config.get("timeout_ms", 5000)
```

The values are stored in `~/.tau/settings.json` (global) or `.tau/settings.json` (project-local):

```json
{
  "extensions": {
    "list": [
      {
        "path": "~/.tau/extensions/my_ext.py",
        "settings": {
          "api_key": "sk-my-key",
          "timeout_ms": 10000
        }
      }
    ]
  }
}
```

### Where settings live

Settings for an extension are stored in the `settings` object of its entry inside `extensions.list`. Tau matches entries by path — the path in the list must match the path or directory the extension was loaded from.

**Project-local** — `.tau/settings.json` (only applies in this directory):

```json
{
  "extensions": {
    "list": [
      {
        "path": ".tau/extensions/lsp",
        "settings": {
          "lsp": true,
          "eager": [],
          "servers": {
            "pyright": { "enabled": true },
            "ruff": { "enabled": true },
            "typescript-language-server": { "enabled": false }
          }
        }
      },
      {
        "path": ".tau/extensions/voice",
        "settings": {
          "enabled": true,
          "stt_model": "whisper-1",
          "stt_provider": "openai",
          "hold_seconds": 2,
          "sample_rate": 16000
        }
      }
    ]
  }
}
```

**Global** — `~/.tau/settings.json` (applies to every project):

```json
{
  "extensions": {
    "list": [
      {
        "path": "~/.tau/extensions/web-search",
        "settings": {
          "enabled": true,
          "engine": "ddgs",
          "results": 10
        }
      },
      {
        "path": "~/.tau/extensions/my_ext.py",
        "settings": {
          "api_key": "sk-...",
          "timeout_ms": 5000
        }
      }
    ]
  }
}
```

Both files can coexist — project settings are merged on top of global settings at startup. Extensions loaded from `.tau/extensions/` read from the project file; extensions loaded from `~/.tau/extensions/` read from the global file.

## Nested Structures

`ExtensionSettings` handles nested configuration just as naturally:

```python
from dataclasses import dataclass, field
from tau.extensions import ExtensionSettings

@dataclass
class RetryConfig:
    enabled: bool = True
    max_attempts: int = 3

@dataclass
class MyExtConfig:
    api_key: str = ""
    retry: RetryConfig = field(default_factory=RetryConfig)

def register(tau):
    config = ExtensionSettings(MyExtConfig, tau.config)
    
    # Access nested settings with dot notation
    retry_enabled = config.get_nested("retry.enabled", True)
    max_attempts = config.get_nested("retry.max_attempts", 3)
```

In settings.json:

```json
{
  "extensions": {
    "list": [
      {
        "path": "~/.tau/extensions/my_ext.py",
        "settings": {
          "api_key": "sk-my-key",
          "retry": {
            "enabled": true,
            "max_attempts": 5
          }
        }
      }
    ]
  }
}
```

## API Reference

### `get(key, default=None) → Any`

Get a top-level setting value. Returns `default` if the key is missing.

```python
config = ExtensionSettings(MyConfig, tau.config)
api_key = config.get("api_key")
timeout = config.get("timeout_ms", 5000)
```

### `get_nested(path, default=None) → Any`

Get a nested setting using dot notation. Returns `default` if the path is missing.

```python
# Access nested fields like "section.subsection.key"
retry_enabled = config.get_nested("retry.enabled", True)
db_host = config.get_nested("database.host", "localhost")
db_user = config.get_nested("database.credentials.username", "admin")
```

### `to_dict() → dict`

Convert the typed instance back to a dictionary for storage or passing to other systems.

```python
config = ExtensionSettings(MyConfig, tau.config)
as_dict = config.to_dict()
json.dump(as_dict, file)
```

## Features

### Type Safety & Validation

Your schema defines the expected structure. `ExtensionSettings` validates it at load time:

```python
from tau.extensions import ExtensionSettings, ExtensionSettingsError

@dataclass
class Config:
    port: int = 8080

try:
    config = ExtensionSettings(Config, tau.config)
except ExtensionSettingsError as e:
    # Handle validation errors
    print(f"Config error: {e}")
```

### Sensible Defaults

When settings are missing from JSON, dataclass defaults are used:

```python
@dataclass
class Config:
    timeout_ms: int = 5000      # Used if not in settings.json
    retries: int = 3
    verbose: bool = False

config = ExtensionSettings(Config, {})
assert config.get("timeout_ms") == 5000  # Gets default
assert config.get("retries") == 3
```

### Deep Nesting

Define arbitrarily deep nested structures:

```python
@dataclass
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    
    @dataclass
    class Credentials:
        username: str = ""
        password: str = ""
    
    credentials: Credentials = field(default_factory=Credentials)

@dataclass
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    debug: bool = False

config = ExtensionSettings(AppConfig, tau.config)
username = config.get_nested("database.credentials.username")
```

## Real-World Example

```python
from dataclasses import dataclass, field
from tau.extensions import ExtensionSettings, ExtensionSettingsError

@dataclass
class LoggingConfig:
    level: str = "info"
    verbose: bool = False

@dataclass
class RetryConfig:
    enabled: bool = True
    max_attempts: int = 3
    backoff_ms: int = 1000

@dataclass
class MyServiceConfig:
    api_key: str = ""
    endpoint: str = "https://api.example.com"
    timeout_ms: int = 30000
    retry: RetryConfig = field(default_factory=RetryConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

def register(tau):
    # Load and validate configuration
    try:
        config = ExtensionSettings(MyServiceConfig, tau.config)
    except ExtensionSettingsError as e:
        async def show_error(event, ctx):
            ctx.ui.notify(f"Config error: {e}", "error")
        tau.on("session_start")(show_error)
        return
    
    # Use in event handlers
    @tau.on("session_start")
    async def on_start(event, ctx):
        if config.get_nested("logging.verbose"):
            ctx.ui.notify(
                f"Connecting to {config.get('endpoint')}...",
                "info"
            )
    
    # Register tools with config
    @tau.register_tool()
    async def make_request(params, ctx):
        timeout = config.get("timeout_ms") / 1000
        max_retries = config.get_nested("retry.max_attempts", 3)
        # Make API call with configured timeout and retries...
```

## Exposing Settings in the /settings Panel

Extensions can register settings items that appear as a named sub-panel at the bottom of the interactive `/settings` panel. Users can then edit extension settings from the TUI without touching JSON.

There are two ways to do this: declare the schema in `manifest.json` (recommended for directory extensions) or register it imperatively with `tau.register_settings()` (useful for single-file extensions).

---

### Option A — manifest.json schema (recommended)

Add a `"settings"` block under the `"tau"` key in `manifest.json`. Tau reads it at load time, builds the `/settings` sub-panel automatically, reads current values from the extension's config, and persists changes back to `settings.json` (reloading just that extension so the change applies live — no restart needed).

```json
{
  "tau": {
    "extensions": ["__init__.py"],
    "dependencies": ["requests"],
    "settings": {
      "title": "Web search",
      "fields": [
        {
          "key": "enabled",
          "label": "Enabled",
          "type": "bool",
          "default": true,
          "description": "Enable or disable the web search extension."
        },
        {
          "key": "engine",
          "label": "Search engine",
          "type": "enum",
          "values": ["ddgs", "exa", "tavily"],
          "default": "ddgs",
          "description": "Which search backend to use."
        },
        {
          "key": "results",
          "label": "Max results",
          "type": "int",
          "default": 10,
          "min": 1,
          "max": 50,
          "description": "Number of results returned per query."
        },
        {
          "key": "api_key",
          "label": "API key",
          "type": "secret",
          "default": "",
          "description": "Provider API key (stored in settings.json)."
        },
        {
          "key": "exa",
          "label": "Exa settings",
          "type": "group",
          "fields": [
            {
              "key": "base_url",
              "label": "Base URL",
              "type": "string",
              "default": "https://api.exa.ai"
            },
            {
              "key": "highlights",
              "label": "Highlights",
              "type": "bool",
              "default": true
            }
          ]
        }
      ]
    }
  }
}
```

The corresponding `.tau/settings.json` entry that stores the current values:

```json
{
  "extensions": {
    "list": [
      {
        "path": ".tau/extensions/web-search",
        "settings": {
          "enabled": true,
          "engine": "exa",
          "results": 20,
          "api_key": "sk-exa-...",
          "exa": {
            "base_url": "https://api.exa.ai",
            "highlights": false
          }
        }
      }
    ]
  }
}
```

#### Field types

| Type | UI control | Notes |
|------|-----------|-------|
| `bool` | Toggle `off` / `on` | Stored as a JSON boolean in settings |
| `int` | Text input (numeric) | Optional `min` and `max` clamps |
| `string` | Text input | Optional `pattern` (regex the value must match) |
| `secret` | Text input | Same as `string`; signals sensitive content |
| `text` | Text input | Same as `string`; intended for longer values |
| `enum` or `select` | Cycle through values on Enter | Requires a non-empty `"values"` list |
| `group` | Opens a nested sub-panel | Contains a `"fields"` list; keys are prefixed |

#### Field properties

| Property | Required | Description |
|----------|----------|-------------|
| `key` | Yes | Setting key. Supports dot-notation: `"exa.api_key"` is equivalent to nesting under a `"exa"` group |
| `label` | Yes | Display text shown in the `/settings` panel |
| `type` | No (defaults to `string`) | One of the types listed above |
| `default` | No | Fallback value when the key is absent from settings |
| `description` | No | Dimmed help text shown below the label |
| `values` | `enum`/`select` only | List of allowed values to cycle through |
| `min` / `max` | `int` only | Clamp the entered value to this range |
| `pattern` | `string`/`secret`/`text` | Regex the value must fully match; invalid input is silently ignored |
| `fields` | `group` only | Nested list of field definitions |

#### Summary badge

The first top-level `bool` field is automatically used as an on/off summary badge on the extension's parent row in the main `/settings` list. This lets users see at a glance whether an extension is enabled without opening its sub-panel.

#### Dot-notation keys

`"key": "exa.api_key"` at the top level is exactly equivalent to a field with `"key": "api_key"` nested inside a group with `"key": "exa"`. Both read and write the same path in settings:

```json
{ "exa": { "api_key": "sk-..." } }
```

Use the explicit `group` type when you want the sub-panel to have a header label. Use dot-notation directly when you just want nested storage without the extra level of UI.

---

### Option B — `tau.register_settings()` (imperative)

#### Basic example

```python
from tau.modes.interactive.components.settings_selector import SettingItem

def register(tau):
    def on_change(key, value):
        tau.settings.set_extension_config_key(__file__, key, value)

    tau.register_settings([
        SettingItem(id="verbose", label="Verbose", current_value="false", values=["false", "true"]),
        SettingItem(id="timeout_ms", label="Timeout (ms)", current_value="5000", text_input=True),
        SettingItem(id="mode", label="Mode", current_value="auto",
                    submenu_items=["auto", "manual", "off"], submenu_title="Mode"),
    ], title="My Extension", on_change=on_change)
```

#### `tau.register_settings(items, title, on_change)`

Registers a sub-panel in `/settings` containing the provided items.

| Parameter | Type | Description |
|-----------|------|-------------|
| `items` | `list[SettingItem]` | The settings items to show in the sub-panel |
| `title` | `str` | The sub-panel title shown in the list |
| `on_change` | `callable(key, value)` | Called when the user changes a value |

#### `SettingItem` fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Key passed to `on_change` when the value changes |
| `label` | `str` | Display label shown in the panel |
| `current_value` | `str` | The current value (always a string) |
| `values` | `list[str] \| None` | Cycle through these values on Enter (enum-style toggle) |
| `text_input` | `bool` | When `True`, Enter opens inline text editing for integer/string values |
| `submenu_items` | `list[str] \| None` | Open a picker sub-panel containing these choices |
| `submenu_title` | `str \| None` | Title for the picker sub-panel |
| `submenu_settings` | `list[SettingItem] \| None` | Open a full nested sub-panel with these items (arbitrary depth) |
| `submenu_on_change` | `callable \| None` | Overrides the parent `on_change` for a nested sub-panel |

#### Nested sub-panels

A `SettingItem` with `submenu_settings` opens another level of the settings panel. Nesting can be arbitrarily deep. Use `submenu_on_change` to override the change callback for that level:

```python
tau.register_settings([
    SettingItem(
        id="retry",
        label="Retry settings",
        current_value="",
        submenu_title="Retry",
        submenu_settings=[
            SettingItem(id="retry.enabled", label="Enabled", current_value="true", values=["false", "true"]),
            SettingItem(id="retry.max_attempts", label="Max attempts", current_value="3", text_input=True),
        ],
        submenu_on_change=lambda key, value: tau.settings.set_extension_config_key(__file__, key, value),
    ),
], title="My Extension", on_change=on_change)
```

#### `tau.settings.set_extension_config_key(ext_path, key, value)`

Persists a value back to `extensions.list[].settings` in `settings.json`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `ext_path` | `str` | Path to the extension file, typically `__file__` |
| `key` | `str` | Setting key. Supports dot-notation for nested values, e.g. `"retry.enabled"` |
| `value` | `str` | New value (as a string; tau coerces to the appropriate type) |

Dot-notation example:

```python
# Writes {"retry": {"enabled": "true"}} into settings.list[].settings
tau.settings.set_extension_config_key(__file__, "retry.enabled", "true")
```

---

## Best Practices

1. **Always use dataclasses** — `ExtensionSettings` only works with dataclass schemas
2. **Provide defaults** — Every field should have a sensible default so config is optional
3. **Use field() for nested dataclasses** — `field(default_factory=SomeClass)` to avoid mutable defaults
4. **Document your schema** — Include example settings.json snippets in your extension docs
5. **Validate early** — Catch config errors during `register()`, not during event handlers
6. **Use dot notation** — `config.get_nested("section.key")` is clearer than manual traversal

## Error Handling

```python
from tau.extensions import ExtensionSettings, ExtensionSettingsError

try:
    config = ExtensionSettings(MyConfig, tau.config)
except ExtensionSettingsError as e:
    # Schema is not a dataclass - programming error
    print(f"Invalid schema: {e}")
```

If your settings.json is missing values, they'll be filled with defaults from your schema — no exception thrown.

---

## See Also

- [Extensions](extensions.md) — Extension system overview
- [Settings](settings.md) — Main settings reference

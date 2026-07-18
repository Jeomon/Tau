from __future__ import annotations

import contextlib
import json

from tau.modes.interactive.commands.context import CommandContext


def _headers_to_str(headers: dict | None) -> str:
    if not headers:
        return ""
    return json.dumps(headers)


def open_theme_selector(ctx: CommandContext) -> None:
    """Open the theme selector modal with live preview."""
    from tau.themes.registry import AUTO_THEME, DEFAULT_THEME, mode_for_background, theme_registry

    # "auto" tracks the terminal background (light/dark); offer it first.
    names = [AUTO_THEME, *theme_registry.list()]
    sm = ctx.runtime.settings_manager
    original = (sm.get_theme() if sm is not None else None) or DEFAULT_THEME

    def _resolve(name: str) -> str:
        """Map "auto" to the concrete builtin for the current terminal background."""
        if name == AUTO_THEME:
            return mode_for_background(ctx.tui.background_color)
        return name

    def preview(name: str) -> None:
        """Preview the selected theme."""
        with contextlib.suppress(ValueError):
            ctx.layout.set_theme(theme_registry.get(_resolve(name)))

    def commit(name: str) -> None:
        """Apply the selected theme."""
        try:
            theme = theme_registry.get(_resolve(name))
        except ValueError:
            return
        ctx.layout.set_theme(theme)
        if sm is not None:
            sm.set_theme(name)  # persist "auto" verbatim so it re-detects next launch
        ctx.notify(f"Theme set to {name}")

    def cancel() -> None:
        """Revert to the original theme."""
        with contextlib.suppress(ValueError):
            ctx.layout.set_theme(theme_registry.get(original))
        ctx.notify(f"Kept theme {original}")

    ctx.layout.open_theme_selector(names, original, preview, commit, cancel)


def open_settings_panel(ctx: CommandContext) -> None:
    """Open the interactive settings modal."""
    from tau.engine.types import FollowupMode, SteeringMode
    from tau.inference.types import ThinkingLevel, Transport
    from tau.modes.interactive.components.settings_selector import SettingItem, SettingsSelector
    from tau.themes.registry import AUTO_THEME, DEFAULT_THEME, theme_registry
    from tau.tui.theme import LayoutTheme

    sm = ctx.runtime.settings_manager
    if sm is None:
        ctx.notify("Settings unavailable.")
        return

    preview_theme_before: list[LayoutTheme] = []

    def preview_theme(name: str) -> None:
        from tau.themes.registry import mode_for_background

        if not preview_theme_before:
            preview_theme_before.append(ctx.layout.theme)
        resolved = mode_for_background(ctx.tui.background_color) if name == AUTO_THEME else name
        with contextlib.suppress(ValueError):
            ctx.layout.set_theme(theme_registry.get(resolved))

    def cancel_theme_preview() -> None:
        if preview_theme_before:
            ctx.layout.set_theme(preview_theme_before.pop())

    # ── Settings tab — display & quick toggles ────────────────────────────────
    settings_items: list[SettingItem] = [
        SettingItem(
            id="quiet_startup",
            label="Quiet startup",
            description="Suppress verbose output at startup",
            current_value="true" if sm.get_quiet_startup() else "false",
            values=["false", "true"],
        ),
        SettingItem(
            id="telemetry",
            label="Telemetry",
            description="Send one anonymous version-only ping after install or update",
            current_value="true" if sm.get_telemetry() else "false",
            values=["true", "false"],
        ),
        SettingItem(
            id="show_thinking",
            label="Show thinking",
            description="Display extended reasoning blocks in responses",
            current_value="true" if sm.get_show_thinking() else "false",
            values=["true", "false"],
        ),
        SettingItem(
            id="show_tool_calls",
            label="Show tool calls",
            description="Display tool call and result blocks",
            current_value="true" if sm.get_show_tool_calls() else "false",
            values=["true", "false"],
        ),
        SettingItem(
            id="show_images",
            label="Show images",
            description=(
                "Render inline images using terminal graphics (Kitty/iTerm2);"
                " disable to show text placeholders"
            ),
            current_value="true" if sm.get_show_images() else "false",
            values=["true", "false"],
        ),
        SettingItem(
            id="image_auto_resize",
            label="Auto-resize images",
            description="Resize large images to 2000×2000 before sending",
            current_value="true" if sm.get_image_auto_resize() else "false",
            values=["true", "false"],
        ),
        SettingItem(
            id="image_block",
            label="Block images",
            description="Prevent images from being sent to providers",
            current_value="true" if sm.get_image_block_images() else "false",
            values=["true", "false"],
        ),
        SettingItem(
            id="show_hardware_cursor",
            label="Hardware cursor",
            description="Show terminal cursor while positioning (useful for IME input)",
            current_value="true" if sm.get_show_hardware_cursor() else "false",
            values=["false", "true"],
        ),
        SettingItem(
            id="cursor_blink",
            label="Cursor blink",
            description="Blink the input cursor when idle and focused",
            current_value="true" if sm.get_cursor_blink() else "false",
            values=["true", "false"],
        ),
        SettingItem(
            id="theme",
            label="Theme",
            description="Color theme for the interface",
            current_value=sm.get_theme() or DEFAULT_THEME,
            submenu_items=[AUTO_THEME, *theme_registry.list()],
            submenu_title="Theme",
            submenu_on_preview=preview_theme,
            submenu_on_cancel=cancel_theme_preview,
        ),
        SettingItem(
            id="project_trust",
            label="Project trust",
            description=(
                "Whether to load project config, extensions,"
                " and context files from .tau/ directories"
            ),
            current_value=sm.get_project_trust(),
            values=["ask", "always", "never"],
        ),
        SettingItem(
            id="double_escape_action",
            label="Double-Escape action",
            description="Action when Escape is pressed twice while idle",
            current_value=sm.get_double_escape_action(),
            values=["clear", "fork", "tree", "none"],
        ),
        SettingItem(
            id="tree_filter_mode",
            label="Tree filter mode",
            description="Default message filter in the /tree view",
            current_value=sm.get_tree_filter_mode(),
            submenu_items=["default", "no-tools", "user-only", "labeled-only", "all"],
            submenu_title="Tree Filter Mode",
        ),
    ]

    # ── Model tab — inference settings ─────────────────────────────────────────
    model_items: list[SettingItem] = [
        SettingItem(
            id="thinking_level",
            label="Thinking level",
            description="Default reasoning depth for thinking-capable models",
            current_value=getattr(sm.get_thinking_level(), "value", None)
            or ThinkingLevel.Off.value,
            submenu_items=[lv.value for lv in ThinkingLevel],
            submenu_title="Thinking Level",
        ),
        SettingItem(
            id="transport",
            label="Transport",
            description="Wire protocol used to reach provider endpoints",
            current_value=sm.get_transport().value,
            values=[t.value for t in Transport],
        ),
        SettingItem(
            id="steering_mode",
            label="Steering mode",
            description="How queued steering messages are delivered while the agent streams",
            current_value=sm.get_steering_mode().value,
            values=[m.value for m in SteeringMode],
        ),
        SettingItem(
            id="follow_up_mode",
            label="Follow-up mode",
            description="How queued follow-up messages are delivered after the agent stops",
            current_value=sm.get_follow_up_mode().value,
            values=[m.value for m in FollowupMode],
        ),
    ]

    # ── Advanced tab — networking, retry, compaction, editor ──────────────────
    advanced_items: list[SettingItem] = [
        # Advanced tab — networking, retry, compaction, editor.
        SettingItem(
            id="proxy",
            label="Proxy",
            description="HTTP proxy URL, exclusions, and custom headers",
            current_value=sm.get_proxy_url() or "(none)",
            submenu_title="Proxy Settings",
            submenu_settings=[
                SettingItem(
                    id="proxy_url",
                    label="URL",
                    description=(
                        "Proxy URL for HTTP and HTTPS requests (overrides HTTP_PROXY env var)"
                    ),
                    current_value=sm.get_proxy_url() or "",
                    text_input=True,
                ),
                SettingItem(
                    id="proxy_no_proxy",
                    label="No-proxy hosts",
                    description=(
                        "Comma-separated hostnames to exclude from proxying"
                        " (overrides NO_PROXY env var)"
                    ),
                    current_value=sm.get_no_proxy() or "",
                    text_input=True,
                ),
                SettingItem(
                    id="proxy_headers",
                    label="Headers (JSON)",
                    description=(
                        "Custom proxy headers as JSON object,"
                        ' e.g. {"Authorization": "Bearer token"}'
                    ),
                    current_value=_headers_to_str(sm.get_proxy_headers()),
                    text_input=True,
                ),
            ],
        ),
        SettingItem(
            id="http_idle_timeout_ms",
            label="HTTP idle timeout (ms)",
            description="Idle timeout for LLM HTTP streams in milliseconds (default: 60000)",
            current_value=str(sm.get_http_idle_timeout_ms()),
            text_input=True,
        ),
        SettingItem(
            id="retry",
            label="Retry",
            description="Automatic retry behaviour for failed API requests",
            current_value="on" if sm.is_retry_enabled() else "off",
            submenu_title="Retry Settings",
            submenu_settings=[
                SettingItem(
                    id="retry_enabled",
                    label="Enabled",
                    description="Automatically retry failed API requests",
                    current_value="true" if sm.is_retry_enabled() else "false",
                    values=["false", "true"],
                ),
                SettingItem(
                    id="retry_max_retries",
                    label="Max attempts",
                    description="Maximum number of automatic retry attempts (default: 3)",
                    current_value=str(sm.get_retry_max_retries()),
                    text_input=True,
                ),
                SettingItem(
                    id="retry_base_delay_ms",
                    label="Base delay (ms)",
                    description="Base delay between retries in milliseconds (default: 1000)",
                    current_value=str(sm.get_retry_base_delay_ms()),
                    text_input=True,
                ),
            ],
        ),
        SettingItem(
            id="compaction",
            label="Compaction",
            description="Automatic context compaction when approaching the token limit",
            current_value="on" if sm.is_compaction_enabled() else "off",
            submenu_title="Compaction Settings",
            submenu_settings=[
                SettingItem(
                    id="compaction_enabled",
                    label="Enabled",
                    description="Automatically compact context when approaching the token limit",
                    current_value="true" if sm.is_compaction_enabled() else "false",
                    values=["true", "false"],
                ),
                SettingItem(
                    id="compaction_reserve_tokens",
                    label="Reserve tokens",
                    description=(
                        "Tokens reserved for LLM response during compaction (default: 16384)"
                    ),
                    current_value=str(sm.get_compaction_reserve_tokens()),
                    text_input=True,
                ),
                SettingItem(
                    id="compaction_keep_recent_tokens",
                    label="Keep recent tokens",
                    description="Recent tokens to keep verbatim during compaction (default: 20000)",
                    current_value=str(sm.get_compaction_keep_recent_tokens()),
                    text_input=True,
                ),
            ],
        ),
        SettingItem(
            id="branch_summary",
            label="Branch summary",
            description="Settings for branch summarization behaviour",
            current_value="on" if sm.is_branch_summary_enabled() else "off",
            submenu_title="Branch Summary Settings",
            submenu_settings=[
                SettingItem(
                    id="branch_summary_enabled",
                    label="Enabled",
                    description="Enable branch summarization when switching branches",
                    current_value="true" if sm.is_branch_summary_enabled() else "false",
                    values=["true", "false"],
                ),
                SettingItem(
                    id="branch_summary_skip_prompt",
                    label="Skip prompt",
                    description=(
                        "Always skip the 'Summarize branch?' confirmation"
                        " (only applies when enabled)"
                    ),
                    current_value="true" if sm.get_branch_summary_skip_prompt() else "false",
                    values=["false", "true"],
                ),
                SettingItem(
                    id="branch_summary_reserve_tokens",
                    label="Reserve tokens",
                    description="Tokens to reserve when summarizing a branch (default: 16384)",
                    current_value=str(sm.get_branch_summary_reserve_tokens()),
                    text_input=True,
                ),
            ],
        ),
        SettingItem(
            id="terminal",
            label="Terminal",
            description="Shell and execution settings for the terminal tool",
            current_value="→",
            submenu_title="Terminal Settings",
            submenu_settings=[
                SettingItem(
                    id="terminal_shell_path",
                    label="Shell path",
                    description="Shell binary to use (default: system shell)",
                    current_value=sm.get_shell_path() or "",
                    text_input=True,
                ),
                SettingItem(
                    id="terminal_shell_command_prefix",
                    label="Shell command prefix",
                    description="Lines prepended inside the shell before each command",
                    current_value=sm.get_shell_command_prefix() or "",
                    text_input=True,
                ),
            ],
        ),
        SettingItem(
            id="picker_max_visible",
            label="Picker max visible",
            description="Maximum number of items visible in list pickers (default: 8)",
            current_value=str(sm.get_picker_max_visible()),
            text_input=True,
        ),
        SettingItem(
            id="autocomplete_max_visible",
            label="Autocomplete max visible",
            description="Maximum number of autocomplete suggestions shown (default: 5)",
            current_value=str(sm.get_autocomplete_max_visible()),
            text_input=True,
        ),
        SettingItem(
            id="editor_padding_x",
            label="Editor padding X",
            description="Horizontal padding for the input editor in characters (default: 0)",
            current_value=str(sm.get_editor_padding_x()),
            text_input=True,
        ),
    ]

    def _ext_on_change(reg, row_id):
        """Wrap an extension's on_change so toggling its summary field refreshes
        the parent row's on/off value live (otherwise it would only update on
        the next /settings open)."""
        base = reg.on_change
        summary_key = reg.summary_key

        def _wrapped(key: str, value: str) -> None:
            base(key, value)
            if summary_key and key == summary_key:
                _update_parent(row_id, "on" if str(value).lower() in ("on", "true") else "off")

        return _wrapped

    # Build manifest-provided extension sub-panels in their own tab.
    extension_items: list[SettingItem] = []
    ext_runtime = ctx.runtime.extension_runtime
    if ext_runtime is not None:
        from pathlib import Path as _Path

        from tau.modes.interactive.components.settings_selector import refresh_current_values

        ext_settings_map = {e.path: (e.settings or {}) for e in sm.get_all_extension_entries()}

        for ext in ext_runtime.get_extensions():
            for reg in ext.settings_registrations:
                p = _Path(ext.path)
                ext_dir = str(p.parent)
                current_config = ext_settings_map.get(ext_dir, ext.config)
                refresh_current_values(reg.items, current_config)
                if reg.summary_key:
                    raw = current_config.get(reg.summary_key)
                    if raw is not None:
                        reg.summary = (
                            "on" if (raw is True or str(raw).lower() in ("true", "on")) else "off"
                        )

                row_id = f"_ext_{id(reg)}"
                extension_items.append(
                    SettingItem(
                        id=row_id,
                        label=reg.title,
                        description=f"Settings for extension: {ext.path}",
                        current_value=reg.summary or "→",
                        submenu_title=reg.title,
                        submenu_settings=reg.items,
                        submenu_on_change=_ext_on_change(reg, row_id),
                    )
                )

    # Flat reference list for _update_parent — built after extensions are appended
    items = settings_items + model_items + advanced_items + extension_items

    sm.begin_batch()

    def _update_parent(parent_id: str, new_value: str) -> None:
        item = next((i for i in items if i.id == parent_id), None)
        if item is not None:
            item.current_value = new_value

    boolean_setters = {
        "quiet_startup": sm.set_quiet_startup,
        "telemetry": sm.set_telemetry,
        "show_thinking": sm.set_show_thinking,
        "show_tool_calls": sm.set_show_tool_calls,
        "image_auto_resize": sm.set_image_auto_resize,
        "image_block": sm.set_image_block_images,
        "show_hardware_cursor": sm.set_show_hardware_cursor,
        "branch_summary_skip_prompt": sm.set_branch_summary_skip_prompt,
    }
    string_setters = {
        "project_trust": sm.set_project_trust,
        "double_escape_action": sm.set_double_escape_action,
        "tree_filter_mode": sm.set_tree_filter_mode,
    }
    optional_string_setters = {
        "proxy_url": sm.set_proxy_url,
        "proxy_no_proxy": sm.set_no_proxy,
        "terminal_shell_path": sm.set_shell_path,
        "terminal_shell_command_prefix": sm.set_shell_command_prefix,
    }
    integer_setters = {
        "http_idle_timeout_ms": sm.set_http_idle_timeout_ms,
        "picker_max_visible": sm.set_picker_max_visible,
        "autocomplete_max_visible": sm.set_autocomplete_max_visible,
        "editor_padding_x": sm.set_editor_padding_x,
        "retry_max_retries": sm.set_retry_max_retries,
        "retry_base_delay_ms": sm.set_retry_base_delay_ms,
        "compaction_reserve_tokens": sm.set_compaction_reserve_tokens,
        "compaction_keep_recent_tokens": sm.set_compaction_keep_recent_tokens,
        "branch_summary_reserve_tokens": sm.set_branch_summary_reserve_tokens,
    }
    parent_boolean_setters = {
        "retry_enabled": (sm.set_retry_enabled, "retry"),
        "compaction_enabled": (sm.set_compaction_enabled, "compaction"),
        "branch_summary_enabled": (
            sm.set_branch_summary_enabled,
            "branch_summary",
        ),
    }

    def on_change(item_id: str, value: str) -> None:
        enabled = value == "true"
        if setter := boolean_setters.get(item_id):
            setter(enabled)
        elif setter := string_setters.get(item_id):
            setter(value)
        elif setter := optional_string_setters.get(item_id):
            setter(value or None)
        elif parent_setter := parent_boolean_setters.get(item_id):
            setter, parent_id = parent_setter
            setter(enabled)
            _update_parent(parent_id, "on" if enabled else "off")
        elif setter := integer_setters.get(item_id):
            try:
                setter(int(value))
            except ValueError:
                ctx.notify(f"Invalid number: {value!r}")
        elif item_id == "show_images":
            v = value == "true"
            sm.set_show_images(v)
            ctx.layout.messages.set_show_images(v)
            ctx.tui.request_render()
        elif item_id == "cursor_blink":
            v = value == "true"
            sm.set_cursor_blink(v)
            # Optional TextInput-only capability — custom editors (set via
            # ctx.ui.set_editor_component) may not implement it.
            if setter := getattr(ctx.layout.input, "set_cursor_blink", None):
                setter(v)
            ctx.tui.request_render()
        elif item_id == "steering_mode":
            sm.set_steering_mode(SteeringMode(value))
        elif item_id == "follow_up_mode":
            sm.set_follow_up_mode(FollowupMode(value))
        elif item_id == "transport":
            sm.set_transport(Transport(value))
        elif item_id == "thinking_level":
            sm.set_thinking_level(ThinkingLevel(value))
        elif item_id == "theme":
            try:
                from tau.themes.registry import AUTO_THEME, mode_for_background
                from tau.themes.registry import theme_registry as _tr

                resolved = (
                    mode_for_background(ctx.tui.background_color) if value == AUTO_THEME else value
                )
                ctx.layout.set_theme(_tr.get(resolved))
                preview_theme_before.clear()
                sm.set_theme(value)  # persist "auto" verbatim
            except ValueError:
                pass
        elif item_id == "proxy_headers":
            if not value.strip():
                sm.set_proxy_headers(None)
            else:
                try:
                    parsed = json.loads(value)
                    if not isinstance(parsed, dict):
                        ctx.notify("Proxy headers must be a JSON object")
                        return
                    if any(
                        not isinstance(k, str) or not isinstance(v, str) for k, v in parsed.items()
                    ):
                        ctx.notify("Proxy header keys and values must be strings")
                        return
                    sm.set_proxy_headers(parsed)
                except json.JSONDecodeError as e:
                    ctx.notify(f"Invalid JSON: {e.msg}")
                    return

    def on_close() -> None:
        sm.save_batch()  # commits the batch and re-merges the live settings view
        ctx.notify("Settings saved.")
        # Rebuild the palette now that settings are committed — feature-gated
        # commands (e.g. /compact when compaction is off) appear/disappear here.
        if ctx.on_palette_refresh is not None:
            ctx.on_palette_refresh()

    modal = SettingsSelector(
        [],
        on_change=on_change,
        theme=ctx.layout.theme,
        tabs=[
            ("Settings", settings_items),
            ("Model", model_items),
            ("Extensions", extension_items),
            ("Advanced", advanced_items),
        ],
    )
    ctx.layout.open_settings_selector(modal, on_cancel=on_close)

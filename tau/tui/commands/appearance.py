from __future__ import annotations

import json

from tau.tui.commands.context import CommandContext


def _headers_to_str(headers: dict | None) -> str:
    if not headers:
        return ""
    return json.dumps(headers)


def open_theme_selector(ctx: CommandContext) -> None:
    """Open the theme selector modal with live preview."""
    from tau.themes.registry import theme_registry, DEFAULT_THEME

    names = theme_registry.list()
    sm = ctx.runtime.settings_manager
    original = (sm.get_theme() if sm is not None else None) or DEFAULT_THEME

    def preview(name: str) -> None:
        """Preview the selected theme."""
        try:
            ctx.layout.set_theme(theme_registry.get(name))
        except ValueError:
            pass

    def commit(name: str) -> None:
        """Apply the selected theme."""
        try:
            theme = theme_registry.get(name)
        except ValueError:
            return
        ctx.layout.set_theme(theme)
        if sm is not None:
            sm.set_theme(name)
        ctx.notify(f"Theme set to {name}")

    def cancel() -> None:
        """Revert to the original theme."""
        try:
            ctx.layout.set_theme(theme_registry.get(original))
        except ValueError:
            pass
        ctx.notify(f"Kept theme {original}")

    ctx.layout.open_theme_selector(names, original, preview, commit, cancel)



def open_settings_panel(ctx: CommandContext) -> None:
    """Open the interactive settings modal."""
    from tau.tui.components.settings_modal import SettingItem, SettingsModal
    from tau.inference.types import Transport, ThinkingLevel
    from tau.engine.types import SteeringMode, FollowupMode
    from tau.themes.registry import theme_registry, DEFAULT_THEME

    sm = ctx.runtime.settings_manager
    if sm is None:
        ctx.notify("Settings unavailable.")
        return

    items: list[SettingItem] = [
        SettingItem(
            id="quiet_startup",
            label="Quiet startup",
            description="Suppress verbose output at startup",
            current_value="true" if sm.get_quiet_startup() else "false",
            values=["false", "true"],
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
            description="Render inline images using terminal graphics (Kitty/iTerm2); disable to show text placeholders",
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
        SettingItem(
            id="transport",
            label="Transport",
            description="Wire protocol used to reach provider endpoints",
            current_value=sm.get_transport().value,
            values=[t.value for t in Transport],
        ),
        SettingItem(
            id="thinking_level",
            label="Thinking level",
            description="Default reasoning depth for thinking-capable models",
            current_value=getattr(sm.get_thinking_level(), 'value', None) or ThinkingLevel.Off.value,
            submenu_items=[lv.value for lv in ThinkingLevel],
            submenu_title="Thinking Level",
        ),
        SettingItem(
            id="theme",
            label="Theme",
            description="Color theme for the interface",
            current_value=sm.get_theme() or DEFAULT_THEME,
            submenu_items=theme_registry.list(),
            submenu_title="Theme",
        ),
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
                    description="Proxy URL for HTTP and HTTPS requests (overrides HTTP_PROXY env var)",
                    current_value=sm.get_proxy_url() or "",
                    text_input=True,
                ),
                SettingItem(
                    id="proxy_no_proxy",
                    label="No-proxy hosts",
                    description="Comma-separated hostnames to exclude from proxying (overrides NO_PROXY env var)",
                    current_value=sm.get_no_proxy() or "",
                    text_input=True,
                ),
                SettingItem(
                    id="proxy_headers",
                    label="Headers (JSON)",
                    description='Custom proxy headers as JSON object, e.g. {"Authorization": "Bearer token"}',
                    current_value=_headers_to_str(sm.get_proxy_headers()),
                    text_input=True,
                ),
            ],
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
                    description="Tokens reserved for LLM response during compaction (default: 16384)",
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
                    description="Always skip the 'Summarize branch?' confirmation (only applies when enabled)",
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
            id="project_trust",
            label="Project trust",
            description="Whether to load project config, extensions, and context files from .tau/ directories",
            current_value=sm.get_project_trust(),
            values=["ask", "always", "never"],
        ),
        SettingItem(
            id="double_escape_action",
            label="Double-Escape action",
            description="Action when Escape is pressed twice on an empty editor",
            current_value=sm.get_double_escape_action(),
            values=["fork", "tree", "none"],
        ),
        SettingItem(
            id="tree_filter_mode",
            label="Tree filter mode",
            description="Default message filter in the /tree view",
            current_value=sm.get_tree_filter_mode(),
            submenu_items=["default", "no-tools", "user-only", "labeled-only", "all"],
            submenu_title="Tree Filter Mode",
        ),
        SettingItem(
            id="show_hardware_cursor",
            label="Hardware cursor",
            description="Show terminal cursor while positioning (useful for IME input)",
            current_value="true" if sm.get_show_hardware_cursor() else "false",
            values=["false", "true"],
        ),
        SettingItem(
            id="http_idle_timeout_ms",
            label="HTTP idle timeout (ms)",
            description="Idle timeout for LLM HTTP streams in milliseconds (default: 60000)",
            current_value=str(sm.get_http_idle_timeout_ms()),
            text_input=True,
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

    # Append sub-panels from loaded extensions that called register_settings()
    ext_runtime = ctx.runtime.extension_runtime
    if ext_runtime is not None:
        for ext in ext_runtime._extensions:
            for reg in ext.settings_registrations:
                row_id = f"_ext_{id(reg)}"
                items.append(SettingItem(
                    id=row_id,
                    label=reg.title,
                    description=f"Settings for extension: {ext.path}",
                    current_value=reg.summary or "→",
                    submenu_title=reg.title,
                    submenu_settings=reg.items,
                    submenu_on_change=_ext_on_change(reg, row_id),
                ))

    sm.begin_batch()

    def _update_parent(parent_id: str, new_value: str) -> None:
        item = next((i for i in items if i.id == parent_id), None)
        if item is not None:
            item.current_value = new_value

    def on_change(item_id: str, value: str) -> None:
        if item_id == "quiet_startup":
            sm.set_quiet_startup(value == "true")
        elif item_id == "show_thinking":
            sm.set_show_thinking(value == "true")
        elif item_id == "show_tool_calls":
            sm.set_show_tool_calls(value == "true")
        elif item_id == "show_images":
            v = value == "true"
            sm.set_show_images(v)
            t = ctx.layout.messages._theme
            t.show_images = v
            ctx.layout.messages.set_theme(t)
            ctx.tui.request_render()
        elif item_id == "image_auto_resize":
            sm.set_image_auto_resize(value == "true")
        elif item_id == "image_block":
            sm.set_image_block_images(value == "true")
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
                from tau.themes.registry import theme_registry as _tr
                ctx.layout.set_theme(_tr.get(value))
                sm.set_theme(value)
            except ValueError:
                pass
        elif item_id == "proxy_url":
            sm.set_proxy_url(value or None)
        elif item_id == "proxy_no_proxy":
            sm.set_no_proxy(value or None)
        elif item_id == "proxy_headers":
            if not value.strip():
                sm.set_proxy_headers(None)
            else:
                try:
                    parsed = json.loads(value)
                    if not isinstance(parsed, dict):
                        ctx.notify("Proxy headers must be a JSON object")
                        return
                    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in parsed.items()):
                        ctx.notify("Proxy header keys and values must be strings")
                        return
                    sm.set_proxy_headers(parsed)
                except json.JSONDecodeError as e:
                    ctx.notify(f"Invalid JSON: {e.msg}")
                    return
        elif item_id == "project_trust":
            sm.set_project_trust(value)
        elif item_id == "double_escape_action":
            sm.set_double_escape_action(value)
        elif item_id == "tree_filter_mode":
            sm.set_tree_filter_mode(value)
        elif item_id == "show_hardware_cursor":
            sm.set_show_hardware_cursor(value == "true")
        elif item_id == "retry_enabled":
            sm.set_retry_enabled(value == "true")
            _update_parent("retry", "on" if value == "true" else "off")
        elif item_id == "compaction_enabled":
            sm.set_compaction_enabled(value == "true")
            _update_parent("compaction", "on" if value == "true" else "off")
        elif item_id == "branch_summary_enabled":
            sm.set_branch_summary_enabled(value == "true")
            _update_parent("branch_summary", "on" if value == "true" else "off")
        elif item_id == "branch_summary_skip_prompt":
            sm.set_branch_summary_skip_prompt(value == "true")
        elif item_id in (
            "http_idle_timeout_ms", "picker_max_visible", "autocomplete_max_visible",
            "editor_padding_x", "retry_max_retries", "retry_base_delay_ms",
            "compaction_reserve_tokens", "compaction_keep_recent_tokens",
            "branch_summary_reserve_tokens",
        ):
            try:
                n = int(value)
            except ValueError:
                ctx.notify(f"Invalid number: {value!r}")
                return
            if item_id == "http_idle_timeout_ms":
                sm.set_http_idle_timeout_ms(n)
            elif item_id == "picker_max_visible":
                sm.set_picker_max_visible(n)
            elif item_id == "autocomplete_max_visible":
                sm.set_autocomplete_max_visible(n)
            elif item_id == "editor_padding_x":
                sm.set_editor_padding_x(n)
            elif item_id == "retry_max_retries":
                sm.set_retry_max_retries(n)
            elif item_id == "retry_base_delay_ms":
                sm.set_retry_base_delay_ms(n)
            elif item_id == "compaction_reserve_tokens":
                sm.set_compaction_reserve_tokens(n)
            elif item_id == "compaction_keep_recent_tokens":
                sm.set_compaction_keep_recent_tokens(n)
            elif item_id == "branch_summary_reserve_tokens":
                sm.set_branch_summary_reserve_tokens(n)
        elif item_id == "terminal_shell_path":
            sm.set_shell_path(value or None)
        elif item_id == "terminal_shell_command_prefix":
            sm.set_shell_command_prefix(value or None)

    def on_close() -> None:
        sm.save_batch()  # commits the batch and re-merges the live settings view
        ctx.notify("Settings saved.")
        # Rebuild the palette now that settings are committed — feature-gated
        # commands (e.g. /compact when compaction is off) appear/disappear here.
        if ctx.on_palette_refresh is not None:
            ctx.on_palette_refresh()

    modal = SettingsModal(items, on_change=on_change)
    ctx.layout.open_settings_selector(modal, on_cancel=on_close)

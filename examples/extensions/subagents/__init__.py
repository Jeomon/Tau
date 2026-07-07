"""
Subagents extension for Tau — spawn parallel sub-agents from within your session.

Features:
  - Three LLM-callable tools: Agent, get_subagent_result, steer_subagent
  - Foreground (blocking) and background (non-blocking, queued) execution
  - Built-in agent types: scout, researcher, planner, worker, reviewer, oracle
  - Custom types via .tau/subagents/agents/*.md (project) or ~/.tau/subagents/agents/*.md (global)
  - Context forking (inherit_context) and session resume
  - Mid-run steering via steer_subagent
  - Streaming transcript saved to .tau/subagents/output/<agent_id>/session.jsonl
  - Scoped persistent memory, skill preloading, and tool denylists
  - Session-scoped scheduling and Git worktree isolation
  - Live status widget, conversation viewer, lifecycle events, and extension RPC

Install: add .tau/extensions/subagents to extensions.list in .tau/settings.json
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

_log = logging.getLogger(__name__)


def register(tau) -> None:
    from .manager import SubagentManager
    from .tool import AgentTool, GetSubagentResultTool, SubagentTool

    cwd = Path(tau.cwd)
    output_dir = cwd / ".tau" / "subagents" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    max_concurrent: int = tau.config.get("maxConcurrent", 4)
    grace_turns: int = tau.config.get("graceTurns", 5)
    disable_builtins: bool = tau.config.get("disableDefaultAgents", False)
    scope_models: bool = tau.config.get("scopeModels", False)
    scheduling_enabled: bool = tau.config.get("schedulingEnabled", True)
    default_max_turns: int | None = tau.config.get("defaultMaxTurns")
    join_mode: str = tau.config.get("joinMode", "smart")
    description_mode: str = tau.config.get("toolDescriptionMode", "full")

    manager = SubagentManager(
        cwd=cwd,
        output_dir=output_dir,
        max_concurrent=max_concurrent,
        grace_turns=grace_turns,
        disable_builtins=disable_builtins,
        scope_models=scope_models,
        default_max_turns=default_max_turns,
    )

    tau.provide("subagents", manager)
    agent_tool = AgentTool(
        manager,
        scheduling_enabled=scheduling_enabled,
        description_mode=description_mode,
    )
    tau.register_tool(agent_tool)
    tau.register_tool(GetSubagentResultTool(manager))
    tau.register_tool(SubagentTool(manager))

    # ── Bind LLM once the runtime is ready ────────────────────────────────────

    def _bind_scheduler(ctx) -> None:
        if not scheduling_enabled or ctx._session_manager is None:
            return
        from .scheduler import SubagentScheduler

        if manager._scheduler is not None:
            manager._scheduler.shutdown()
        scheduler = SubagentScheduler(
            manager,
            cwd,
            ctx._session_manager.session_id or "default",
        )
        manager.set_scheduler(scheduler)
        scheduler.start()

    @tau.on("runtime_ready")
    async def _on_ready(event, ctx):
        if ctx._llm is not None:
            manager.bind_llm(ctx._llm)
        manager.bind_parent_session(ctx._session_manager)
        manager.refresh_agent_types()
        _bind_scheduler(ctx)
        if ctx.ui is not None:
            from .ui import AgentWidget

            ui = ctx.ui
            ui.set_widget("subagents", AgentWidget(manager), placement="above_editor")
            manager.set_update_callback(ui.request_render)

            pending_notifications: list[tuple[str, dict]] = []
            flush_task: asyncio.Task[None] | None = None

            async def flush_notifications() -> None:
                await asyncio.sleep(0.35)
                items = list(pending_notifications)
                pending_notifications.clear()
                if not items:
                    return
                lines = [f"{len(items)} subagents finished:"]
                for kind, payload in items:
                    icon = "✓" if kind == "completed" else "✗"
                    lines.append(
                        f"  {icon} [{payload['id']}] {payload['agent_type']} · "
                        f"{payload['description']}"
                    )
                ui.notify(lines, type="tool")

            async def lifecycle_notification(kind: str, payload: dict) -> None:
                nonlocal flush_task
                if kind not in {"completed", "failed"} or not payload.get("run_in_background"):
                    return
                if join_mode == "async":
                    icon = "✓" if kind == "completed" else "✗"
                    ui.notify(
                        [
                            f"{icon} {payload['description']} ({payload['agent_type']})",
                            f"  id: {payload['id']} · {payload['status']}",
                        ],
                        type="tool",
                    )
                    return
                pending_notifications.append((kind, payload))
                if flush_task is None or flush_task.done():
                    flush_task = asyncio.create_task(flush_notifications())

            manager.subscribe(lifecycle_notification)
        _log.debug("Subagents ready — %d agent types", len(manager.get_agent_types()))

    @tau.on("session_start")
    async def _on_session_start(event, ctx):
        manager.bind_parent_session(ctx._session_manager)
        _bind_scheduler(ctx)

    # ── /agents command ────────────────────────────────────────────────────────

    def _show_command_output(ctx, output: str) -> None:
        if ctx.ui is not None:
            ctx.ui.notify(output.splitlines(), type="tool")
        else:
            print(output)

    async def _cmd_agents(ctx, args: list[str]) -> None:
        sub = args[0].lower() if args else "list"

        if sub in ("list", ""):
            types = manager.get_agent_types()
            records = manager.list_records()
            running = [r for r in records if r.status in ("queued", "running")]

            lines = ["── Subagents ────────────────────────────"]
            if running:
                lines.append(f"Running ({len(running)}):")
                for r in running:
                    lines.append(f"  [{r.id}] {r.agent_type}  {r.description}  ({r.status})")
                lines.append("")

            lines.append("Agent types:")
            source_icon = {"builtin": "◉", "project": "●", "global": "◦"}
            for t in types.values():
                icon = source_icon.get(t.source, "·")
                enabled = "" if t.enabled else "  [disabled]"
                lines.append(f"  {icon} {t.display_name} ({t.name}){enabled}")
                lines.append(f"      {t.description}")

            _show_command_output(ctx, "\n".join(lines))

        elif sub == "stop" and len(args) >= 2:
            agent_id = args[1]
            ok = manager.stop(agent_id)
            msg = f"Agent {agent_id} stopped." if ok else f"No running agent with id {agent_id!r}."
            _show_command_output(ctx, msg)

        elif sub == "result" and len(args) >= 2:
            agent_id = args[1]
            result = manager.get_result(agent_id)
            if "status" not in result:
                _show_command_output(ctx, result.get("error", f"Unknown agent {agent_id!r}."))
                return
            lines = [f"[{agent_id}] status={result['status']}"]
            if result.get("result"):
                lines.append(result["result"])
            if result.get("error"):
                lines.append(f"error: {result['error']}")
            _show_command_output(ctx, "\n".join(lines))

        elif sub == "view" and len(args) >= 2:
            record = manager.get_record(args[1])
            if record is None:
                _show_command_output(ctx, f"No agent with id {args[1]!r}.")
                return
            if ctx.ui is None:
                _show_command_output(ctx, "The conversation viewer requires interactive TUI mode.")
                return
            from tau.tui.service import CustomOptions, OverlayOptions

            from .ui import ConversationViewer

            await ctx.ui.custom(
                lambda _tui, _theme, _kb, done: ConversationViewer(record, done),
                CustomOptions(
                    overlay_options=OverlayOptions(
                        width="85%",
                        max_height="85%",
                        anchor="center",
                    )
                ),
            )

        elif sub == "schedules":
            jobs = manager.list_schedules()
            lines = ["Scheduled agents:"]
            if not jobs:
                lines.append("  (none)")
            for job in jobs:
                when = (
                    datetime.fromtimestamp(job.next_run).astimezone().isoformat(timespec="seconds")
                )
                lines.append(f"  [{job.id}] {job.expression} → {when}")
                lines.append(f"      {job.request.get('description', '')}")
            _show_command_output(ctx, "\n".join(lines))

        elif sub == "cancel" and len(args) >= 2:
            ok = manager.cancel_schedule(args[1])
            _show_command_output(
                ctx,
                f"Cancelled schedule {args[1]}." if ok else f"No schedule with id {args[1]!r}.",
            )

        elif sub == "create":
            if ctx.ui is None:
                _show_command_output(ctx, "The create command requires interactive TUI mode.")
                return
            if not ctx.is_idle():
                _show_command_output(
                    ctx,
                    "Wait for the active agent turn before creating an agent.",
                )
                return

            from tau.modes.interactive.components.settings_selector import (
                SettingItem,
                SettingsSelector,
            )

            initial_name = args[1].lower() if len(args) >= 2 else "new-agent"
            values = {
                "scope": "project",
                "name": initial_name,
                "display_name": initial_name.replace("-", " ").title(),
                "description": "Describe when this agent should be used.",
                "tools": "all",
                "disallowed_tools": "none",
                "skills": "none",
                "model": "default",
                "max_turns": "unlimited",
                "memory": "project",
                "run_in_background": "off",
                "inherit_context": "off",
                "isolated": "off",
                "isolation": "none",
                "enabled": "on",
            }
            items = [
                SettingItem(
                    id="scope",
                    label="Scope",
                    current_value=values["scope"],
                    description=(
                        "project saves under this repository; global is available everywhere"
                    ),
                    values=["project", "global"],
                ),
                SettingItem(
                    id="name",
                    label="Name",
                    current_value=values["name"],
                    description="Lowercase identifier used in Agent tool calls",
                    text_input=True,
                ),
                SettingItem(
                    id="display_name",
                    label="Display name",
                    current_value=values["display_name"],
                    description="Human-readable name shown by /agents list",
                    text_input=True,
                ),
                SettingItem(
                    id="description",
                    label="Description",
                    current_value=values["description"],
                    description="When the parent agent should delegate to this type",
                    text_input=True,
                ),
                SettingItem(
                    id="tools",
                    label="Tools",
                    current_value=values["tools"],
                    description="all, none, or comma-separated tool names",
                    text_input=True,
                ),
                SettingItem(
                    id="model",
                    label="Model",
                    current_value=values["model"],
                    description="Model override, or default to inherit the active model",
                    text_input=True,
                ),
                SettingItem(
                    id="disallowed_tools",
                    label="Denied tools",
                    current_value=values["disallowed_tools"],
                    description="Comma-separated tools removed from the effective tool set",
                    text_input=True,
                ),
                SettingItem(
                    id="skills",
                    label="Skills",
                    current_value=values["skills"],
                    description="all, none, or comma-separated skill names to preload",
                    text_input=True,
                ),
                SettingItem(
                    id="max_turns",
                    label="Maximum turns",
                    current_value=values["max_turns"],
                    description="Positive integer, or unlimited",
                    text_input=True,
                ),
                SettingItem(
                    id="memory",
                    label="Memory",
                    current_value=values["memory"],
                    description="Persistent memory scope, or none to disable",
                    values=["project", "local", "user", "none"],
                ),
                SettingItem(
                    id="run_in_background",
                    label="Run in background",
                    current_value=values["run_in_background"],
                    description="Launch this agent asynchronously by default",
                    values=["off", "on"],
                ),
                SettingItem(
                    id="inherit_context",
                    label="Inherit context",
                    current_value=values["inherit_context"],
                    description="Copy the parent conversation into this agent",
                    values=["off", "on"],
                ),
                SettingItem(
                    id="isolated",
                    label="Isolated",
                    current_value=values["isolated"],
                    description="Run without project context inheritance",
                    values=["off", "on"],
                ),
                SettingItem(
                    id="isolation",
                    label="Git isolation",
                    current_value=values["isolation"],
                    description="Run in a temporary worktree and preserve changes on a branch",
                    values=["none", "worktree"],
                ),
                SettingItem(
                    id="enabled",
                    label="Enabled",
                    current_value=values["enabled"],
                    description="Expose this type to the Agent tool",
                    values=["on", "off"],
                ),
            ]

            details_done = asyncio.get_running_loop().create_future()

            def _change(item_id: str, value: str) -> None:
                values[item_id] = value

            def _close_details() -> None:
                if not details_done.done():
                    details_done.set_result(None)

            selector = SettingsSelector(
                items,
                on_change=_change,
                title="Create agent · Esc continues to system prompt",
                theme=ctx._layout._theme,
            )
            ctx._layout.open_settings_selector(selector, on_cancel=_close_details)
            await details_done

            name = values["name"].strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
                _show_command_output(
                    ctx,
                    "Invalid agent name. Use lowercase letters, numbers, and hyphens.",
                )
                return

            max_turns_raw = values["max_turns"].strip().lower()
            if max_turns_raw in {"", "none", "null", "unlimited"}:
                max_turns = "null"
            else:
                try:
                    parsed_max_turns = int(max_turns_raw)
                except ValueError:
                    parsed_max_turns = 0
                if parsed_max_turns < 1:
                    _show_command_output(
                        ctx,
                        "Maximum turns must be a positive integer or unlimited.",
                    )
                    return
                max_turns = str(parsed_max_turns)

            agents_dir = (
                cwd / ".tau" / "subagents" / "agents"
                if values["scope"] == "project"
                else Path.home() / ".tau" / "subagents" / "agents"
            )
            agent_file = agents_dir / f"{name}.md"
            if agent_file.exists() and not await ctx.ui.confirm(
                "Replace existing agent?", str(agent_file)
            ):
                return

            system_prompt = await ctx.ui.editor(
                f"System prompt: {values['display_name']}  (Ctrl+S to save)",
                prefill="Describe this agent's role, constraints, and expected output.",
            )
            if system_prompt is None:
                return
            if not system_prompt.strip():
                _show_command_output(ctx, "System prompt cannot be empty.")
                return

            model = values["model"].strip()
            model_value = "null" if model.lower() in {"", "default", "none", "null"} else model
            denied = values["disallowed_tools"].strip()
            denied_value = "null" if denied.lower() in {"", "none", "null"} else denied
            skills = values["skills"].strip()
            skills_value = "null" if skills.lower() in {"", "none", "null"} else skills
            content = (
                "---\n"
                f"display_name: {values['display_name'].strip()}\n"
                f"description: {values['description'].strip()}\n"
                f"tools: {values['tools'].strip()}\n"
                f"disallowed_tools: {denied_value}\n"
                f"skills: {skills_value}\n"
                f"model: {model_value}\n"
                f"max_turns: {max_turns}\n"
                f"memory: {values['memory']}\n"
                f"run_in_background: {values['run_in_background'] == 'on'}\n"
                f"inherit_context: {values['inherit_context'] == 'on'}\n"
                f"isolated: {values['isolated'] == 'on'}\n"
                f"isolation: {values['isolation']}\n"
                f"enabled: {values['enabled'] == 'on'}\n"
                "---\n"
                f"{system_prompt.strip()}\n"
            )

            agents_dir.mkdir(parents=True, exist_ok=True)
            agent_file.write_text(content, encoding="utf-8")
            manager.refresh_agent_types()
            agent_tool.description = agent_tool._build_description()
            created = manager.get_agent_types().get(name)
            if created is None:
                _show_command_output(
                    ctx,
                    f"Could not load agent definition: {agent_file}",
                )
                return
            shadow_warning = ""
            if created.source != values["scope"]:
                shadow_warning = (
                    f"\n  Warning: the active {created.source} definition with this name "
                    "takes precedence."
                )
            _show_command_output(
                ctx,
                f"Created {created.display_name} ({created.name}) [{values['scope']}]\n"
                f"  {created.description}\n"
                f"  {agent_file}"
                f"{shadow_warning}",
            )

        else:
            _show_command_output(
                ctx,
                "Usage:\n"
                "  /agents          — list agent types and running agents\n"
                "  /agents create [name] — create a project agent type\n"
                "  /agents view <id>   — open an agent conversation\n"
                "  /agents schedules   — list scheduled agents\n"
                "  /agents cancel <id> — cancel a scheduled agent\n"
                "  /agents stop <id>   — stop a running agent\n"
                "  /agents result <id> — show completed agent output",
            )

    def _agents_argument_completions(text: str) -> list:
        from tau.tui.autocomplete import AutocompleteItem

        actions = {
            "list": "List agent types and running agents",
            "create": "Create a project agent type",
            "view": "Open an agent conversation",
            "schedules": "List scheduled agents",
            "cancel": "Cancel a scheduled agent",
            "stop": "Stop a running agent",
            "result": "Show an agent's result",
        }
        parts = text.split()
        if not parts:
            return [
                AutocompleteItem(label=action, description=description)
                for action, description in actions.items()
            ]
        if len(parts) == 1 and not text.endswith(" "):
            prefix = parts[0].lower()
            return [
                AutocompleteItem(label=action, description=description)
                for action, description in actions.items()
                if action.startswith(prefix)
            ]

        action = parts[0].lower()
        if action not in {"stop", "result", "view", "cancel"}:
            return []
        if len(parts) == 1:
            prefix = ""
        elif len(parts) == 2 and not text.endswith(" "):
            prefix = parts[1]
        else:
            return []

        if action == "cancel":
            return [
                AutocompleteItem(
                    label=f"cancel {job.id}",
                    description=f"{job.expression}: {job.request.get('description', '')}",
                    insert_text=f"cancel {job.id}",
                )
                for job in manager.list_schedules()
                if job.id.startswith(prefix)
            ]

        records = manager.list_records()
        if action == "stop":
            records = [record for record in records if record.status in ("queued", "running")]
        return [
            AutocompleteItem(
                label=f"{action} {record.id}",
                description=f"{record.agent_type}: {record.description} ({record.status})",
                insert_text=f"{action} {record.id}",
            )
            for record in records
            if record.id.startswith(prefix)
        ]

    tau.register_command(
        "agents",
        "Manage subagents — create or list types, stop agents, or inspect results",
        _cmd_agents,
        get_argument_completions=_agents_argument_completions,
        argument_hint="[list|create|stop|result] [name|agent_id]",
        requires_idle=False,
    )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @tau.on("runtime_stop")
    async def _on_stop(event, ctx):
        if manager._scheduler is not None:
            manager._scheduler.shutdown()
        manager.shutdown()

    @tau.on("extension_unload")
    async def _on_unload(event, ctx):
        manager.shutdown()

    @tau.on("extension_reloaded")
    async def _on_reloaded(event, ctx):
        if ctx._llm is not None:
            manager.bind_llm(ctx._llm)
        manager.refresh_agent_types()

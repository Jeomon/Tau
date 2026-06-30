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

Install: add .tau/extensions/subagents to extensions.list in .tau/settings.json
"""

from __future__ import annotations

import logging
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

    manager = SubagentManager(
        cwd=cwd,
        output_dir=output_dir,
        max_concurrent=max_concurrent,
        grace_turns=grace_turns,
        disable_builtins=disable_builtins,
    )

    tau.provide("subagents", manager)
    tau.register_tool(AgentTool(manager))
    tau.register_tool(GetSubagentResultTool(manager))
    tau.register_tool(SubagentTool(manager))

    # ── Bind LLM once the runtime is ready ────────────────────────────────────

    @tau.on("runtime_ready")
    async def _on_ready(event, ctx):
        if ctx._llm is not None:
            manager.bind_llm(ctx._llm)
        manager.refresh_agent_types()
        _log.debug("Subagents ready — %d agent types", len(manager.get_agent_types()))

    # ── /agents command ────────────────────────────────────────────────────────

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

            await ctx.print("\n".join(lines))

        elif sub == "stop" and len(args) >= 2:
            agent_id = args[1]
            ok = manager.stop(agent_id)
            msg = f"Agent {agent_id} stopped." if ok else f"No running agent with id {agent_id!r}."
            await ctx.print(msg)

        elif sub == "result" and len(args) >= 2:
            agent_id = args[1]
            result = manager.get_result(agent_id)
            lines = [f"[{agent_id}] status={result['status']}"]
            if result.get("result"):
                lines.append(result["result"])
            if result.get("error"):
                lines.append(f"error: {result['error']}")
            await ctx.print("\n".join(lines))

        else:
            await ctx.print(
                "Usage:\n"
                "  /agents          — list agent types and running agents\n"
                "  /agents stop <id>   — stop a running agent\n"
                "  /agents result <id> — show completed agent output"
            )

    def _agents_argument_completions(text: str) -> list:
        from tau.tui.autocomplete import AutocompleteItem

        actions = {
            "list": "List agent types and running agents",
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
        if action not in {"stop", "result"}:
            return []
        if len(parts) == 1:
            prefix = ""
        elif len(parts) == 2 and not text.endswith(" "):
            prefix = parts[1]
        else:
            return []

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
        "Manage subagents — list types, view running agents, stop or inspect results",
        _cmd_agents,
        get_argument_completions=_agents_argument_completions,
        argument_hint="[list|stop|result] [agent_id]",
        requires_idle=False,
    )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @tau.on("runtime_stop")
    async def _on_stop(event, ctx):
        manager.shutdown()

    @tau.on("extension_unload")
    async def _on_unload(event, ctx):
        manager.shutdown()

    @tau.on("extension_reloaded")
    async def _on_reloaded(event, ctx):
        if ctx._llm is not None:
            manager.bind_llm(ctx._llm)
        manager.refresh_agent_types()

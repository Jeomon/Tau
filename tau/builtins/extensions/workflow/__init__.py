"""workflow extension — run declarative multi-agent workflows from .tau/workflows/.

Usage:
    /workflow                 open the workflow manager (TUI only): run, enable/
                              disable, rename, delete, or create a new workflow
    /workflow <name>          run a workflow directly by name

Workflows are plain YAML files under .tau/workflows/<name>.yaml. Each one
declares an ordered list of phases; each phase runs one or more subagent
tasks (the same agents the `subagent` tool uses — scout, worker, reviewer,
...), sequentially or in parallel, optionally fanning out over a prior
result via `for_each`. Use "+ New workflow" in the manager to generate a
starter file with the exact shape.

This is a static, declarative runner, not a scripting engine: there is no
code execution, no sandbox, and no LLM tool call involved in running a
workflow. Every task runs in-process (see embedded.py) with its own
isolated Engine/LLM/tools — no OS subprocess, no shared session or registry
state with the parent — so a failed task aborts the run rather than
silently continuing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).parent))

import store  # type: ignore[import-not-found]  # noqa: E402
from agents import discover_agents  # type: ignore[import-not-found]  # noqa: E402
from model import WorkflowDef  # type: ignore[import-not-found]  # noqa: E402
from runner import TaskResult, run_workflow  # type: ignore[import-not-found]  # noqa: E402

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext
    from tau.modes.interactive.ui_context import UIContext


def _emit(ctx: ExtensionContext, text: str | list[str], level: str = "info") -> None:
    if ctx.ui is not None:
        ctx.ui.notify(text, level)
    else:
        print(text if isinstance(text, str) else "\n".join(text))


def _get_argument_completions(prefix: str):
    from tau.tui.autocomplete import AutocompleteItem

    if " " in prefix.strip():
        return []
    valid, _ = store.discover(Path.cwd())
    return [
        AutocompleteItem(label=wf.slug, description=wf.meta.description or "workflow")
        for wf in sorted(valid, key=lambda w: w.slug)
        if wf.slug.startswith(prefix.strip())
    ]


def _workflow_label(wf: WorkflowDef) -> str:
    glyph = "■" if wf.enabled else "☐"
    n = len(wf.phases)
    desc = wf.meta.description
    preview = desc if len(desc) <= 56 else f"{desc[:53]}..."
    return f"{glyph} {wf.slug}  ({n} phase{'s' if n != 1 else ''})  —  {preview}"


def _slug_from_label(label: str) -> str:
    # "■ my-slug  (2 phases)  —  preview" -> "my-slug"
    return label.split(maxsplit=2)[1]


async def _execute(ctx: ExtensionContext, wf: WorkflowDef) -> None:
    if not wf.enabled:
        _emit(ctx, f"Workflow '{wf.slug}' is disabled. Enable it first via /workflow.", "warning")
        return

    agents = discover_agents(ctx.cwd)

    def on_phase(title: str) -> None:
        _emit(ctx, f"▶ {title}")

    def on_task_end(r: TaskResult) -> None:
        glyph = "✓" if r.ok else "✗"
        _emit(ctx, f"  {glyph} {r.label} ({r.agent})")

    def on_tool_start(_phase_title: str, label: str, preview: str) -> None:
        _emit(ctx, f"    → {label}: {preview}")

    _emit(ctx, f"Running workflow '{wf.slug}'...")
    result = await run_workflow(
        wf,
        cwd=ctx.cwd,
        model_id=ctx.model_id or None,
        provider=ctx.provider_id or None,
        agents=agents,
        on_phase=on_phase,
        on_task_end=on_task_end,
        on_tool_start=on_tool_start,
    )

    total_cost = sum(r.cost for r in result.results)
    total_turns = sum(r.turns for r in result.results)

    if not result.ok:
        _emit(ctx, f"✗ Workflow '{wf.slug}' failed: {result.error}", "error")
        return

    summary = (
        f"✓ Workflow '{wf.slug}' completed — {len(result.results)} task(s), {total_turns} turn(s)"
    )
    if total_cost:
        summary += f", ${total_cost:.4f}"
    summary += f", {result.duration_s:.1f}s"

    lines = [summary]
    last = result.results[-1] if result.results else None
    if last and last.output:
        preview = last.output if len(last.output) <= 1500 else f"{last.output[:1500]}..."
        lines += ["", preview]
    _emit(ctx, lines)


async def _manage_broken(ui: UIContext, b: store.BrokenWorkflow) -> None:
    choice = await ui.select(f"{b.path.name} (broken)", ["View error", "Delete", "Back"])
    if choice == "View error":
        ui.notify(b.error, "error")
    elif choice == "Delete":
        ok = await ui.confirm(f"Delete {b.path.name}?", b.error)
        if ok:
            store.delete(b.path)
            ui.notify(f"Deleted {b.path.name}.")


async def _manage_workflow(ctx: ExtensionContext, ui: UIContext, wf: WorkflowDef) -> None:
    while True:
        current = store.find(ctx.cwd, wf.slug)
        if current is None:
            return
        wf = current
        assert wf.path is not None  # always set for workflows loaded from disk
        actions = ["Run", "Disable" if wf.enabled else "Enable", "Rename", "Delete", "Back"]
        choice = await ui.select(f"{wf.slug} — {wf.meta.description or 'no description'}", actions)
        if choice is None or choice == "Back":
            return

        if choice == "Run":
            await _execute(ctx, wf)
            continue

        if choice in ("Enable", "Disable"):
            store.set_enabled(wf.path, choice == "Enable")
            ui.notify(f"{choice}d workflow '{wf.slug}'.")
            continue

        if choice == "Rename":
            text = await ui.prompt("New name")
            if text is not None and text.strip():
                try:
                    new_path = store.rename(wf.path, text.strip())
                    ui.notify(f"Renamed to '{new_path.stem}'.")
                    wf = store.find(ctx.cwd, new_path.stem) or wf
                except FileExistsError as e:
                    ui.notify(str(e), "warning")
            continue

        if choice == "Delete":
            ok = await ui.confirm(f"Delete workflow '{wf.slug}'?", wf.meta.description)
            if ok:
                store.delete(wf.path)
                ui.notify(f"Deleted workflow '{wf.slug}'.")
                return
            continue


async def _show_picker(ctx: ExtensionContext, ui: UIContext) -> None:
    while True:
        valid, broken = store.discover(ctx.cwd)
        sorted_valid = sorted(valid, key=lambda w: w.slug)
        valid_options = [_workflow_label(wf) for wf in sorted_valid]
        broken_options = [f"⚠ {b.path.name}  —  parse error" for b in broken]
        options = [*valid_options, *broken_options, "+ New workflow"]

        choice = await ui.select("Workflows", options)
        if choice is None:
            return

        if choice == "+ New workflow":
            text = await ui.prompt("New workflow name")
            if text is not None and text.strip():
                path = store.create(ctx.cwd, text.strip())
                edited = await ui.editor(f"Edit workflow: {path.name}", prefill=path.read_text())
                if edited is not None and edited.strip():
                    path.write_text(edited)
                ui.notify(f"Created workflow '{path.stem}' at {path}.")
            continue

        if choice.startswith("⚠ "):
            match = next((b for b in broken if f"⚠ {b.path.name}  —  parse error" == choice), None)
            if match is not None:
                await _manage_broken(ui, match)
            continue

        slug = _slug_from_label(choice)
        wf = next((w for w in sorted_valid if w.slug == slug), None)
        if wf is not None:
            await _manage_workflow(ctx, ui, wf)


async def cmd_workflow(ctx: ExtensionContext, args: list[str]) -> None:
    if not args:
        ui = ctx.ui
        if ui is None:
            valid, broken = store.discover(ctx.cwd)
            if not valid and not broken:
                _emit(ctx, "No workflows yet. Add one under .tau/workflows/.")
                return
            lines = ["Workflows:", ""]
            for wf in sorted(valid, key=lambda w: w.slug):
                state = "on" if wf.enabled else "off"
                n = len(wf.phases)
                lines.append(f"{wf.slug}  {state}  {n} phase(s)  —  {wf.meta.description}")
            for b in broken:
                lines.append(f"{b.path.name}  broken  —  {b.error}")
            _emit(ctx, "\n".join(lines))
            return
        await _show_picker(ctx, ui)
        return

    name = " ".join(args).strip()
    wf = store.find(ctx.cwd, name)
    if wf is None:
        valid, _ = store.discover(ctx.cwd)
        available = ", ".join(sorted(w.slug for w in valid)) or "none"
        _emit(ctx, f"No workflow named '{name}'. Available: {available}", "warning")
        return
    await _execute(ctx, wf)


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return

    # The bundled create-workflows skill (skills/create-workflows/) is
    # registered automatically from manifest.json's "skills" field — see
    # ExtensionLoader._register_declared_skills.

    tau.register_command(
        "workflow",
        "Run declarative multi-agent workflows: /workflow opens the manager, "
        "/workflow <name> runs one directly",
        cmd_workflow,
        argument_hint="<name>",
        get_argument_completions=_get_argument_completions,
    )

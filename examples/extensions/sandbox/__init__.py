"""sandbox — run the agent's terminal commands inside a microsandbox microVM.

Registers a tool named ``terminal`` (same schema/description as the built-in
one — see the parent docstring in sandbox_tool.py) so it transparently
replaces host execution with a microVM: `pip install microsandbox`, hardware
isolation via a real guest kernel, project directory bind-mounted at
/workspace. Falls back to the real built-in tool when the runtime is
unavailable (unsupported platform, missing dependency, or boot failure).

Settings (see manifest.json / /settings → Sandbox):
  enabled              Route terminal execution through the sandbox (default true).
  image                OCI image to boot, e.g. "python", "alpine", "node".
  cpus                 Virtual CPUs.
  memory_mib           Memory in MiB.
  persistent           Reuse one microVM for the whole session instead of booting a
                       fresh one per command.
  network              "public" (outbound network allowed) or "none" (isolated).
  idle_timeout_seconds Runtime-enforced auto-stop after this much inactivity, so a
                       crashed/abandoned session doesn't leave a VM running forever.
  prewarm              Start booting the microVM in the background at session start
                       instead of on the first command.

Commands:
  /sandbox         Show current sandbox status and settings.
  /sandbox reset    Stop the running microVM; the next command boots a fresh one.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).parent))

from manager import SandboxConfig, SandboxManager  # type: ignore[import-not-found]
from sandbox_tool import SandboxTerminalTool  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext


def register(tau: ExtensionAPI) -> None:
    config = tau.config or {}
    if not config.get("enabled", True):
        return

    sandbox_config = SandboxConfig(
        image=config.get("image", "python"),
        cpus=int(config.get("cpus", 1)),
        memory_mib=int(config.get("memory_mib", 1024)),
        persistent=bool(config.get("persistent", True)),
        network=config.get("network", "public"),
        idle_timeout_seconds=int(config.get("idle_timeout_seconds", 1800)),
    )
    manager = SandboxManager(tau.cwd, sandbox_config)
    prewarm = bool(config.get("prewarm", True))

    tau.register_tool(SandboxTerminalTool(manager, tau._runtime_ref))

    if prewarm:

        async def _prewarm() -> None:
            # Best-effort: any failure (unsupported platform, missing dep, boot
            # error) surfaces normally on the first real terminal call instead.
            with contextlib.suppress(Exception):
                await manager.get()

        @tau.on("session_start")
        async def _on_session_start(_event: Any, _ctx: ExtensionContext) -> None:
            asyncio.ensure_future(_prewarm())

    @tau.on("session_shutdown")
    async def _on_shutdown(_event: Any, _ctx: ExtensionContext) -> None:
        await manager.stop()

    async def cmd_sandbox(ctx: ExtensionContext, args: list[str]) -> None:
        ui = ctx.ui
        if ui is None:
            return

        if args and args[0] == "reset":
            await manager.reset()
            ui.notify(f'Sandbox "{manager.name}" stopped; next command boots a fresh microVM.')
            return

        c = manager.config
        lines = [
            f"name: {manager.name}",
            f"status: {'running' if manager.is_running else 'not started'}",
            f"image: {c.image}",
            f"cpus: {c.cpus}",
            f"memory: {c.memory_mib} MiB",
            f"persistent: {c.persistent}",
            f"network: {c.network}",
            "mount: <project root> -> /workspace",
        ]
        ui.notify("\n".join(lines))

    tau.register_command(
        "sandbox",
        "Show sandbox status, or 'reset' to stop and rebuild the microVM",
        cmd_sandbox,
        argument_hint="[reset]",
        requires_idle=False,
    )

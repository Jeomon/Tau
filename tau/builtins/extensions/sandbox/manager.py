"""Lifecycle manager for the microsandbox microVM backing the terminal tool.

One microVM is created lazily on the first command and (when ``persistent``
is enabled) reused for the rest of the session — this keeps installed
packages and shell state around between calls the same way a real terminal
would. The project's working directory is bind-mounted into the guest at
``/workspace`` so relative-path commands behave the same as they do on the
host; absolute host paths outside the project root are not reachable from
inside the microVM.

The sandbox name mixes in this process's PID (``tau-<cwd-hash>-<pid>``) so a
subagent — a separate ``tau`` process, possibly sharing the parent's cwd —
never collides with (and ``replace=True``-clobbers) the parent's still-running
microVM. Orphans from a crashed process are reclaimed by the runtime itself:
sandboxes are created with ``idle_timeout``/``ephemeral`` so an abandoned VM
stops and is removed on its own instead of running forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKDIR = "/workspace"


class SandboxUnavailableError(Exception):
    """Raised when the microsandbox runtime cannot be used on this host."""


def _unsupported_platform_reason() -> str | None:
    system = platform.system()
    if system == "Darwin":
        return None if platform.machine() == "arm64" else "requires Apple Silicon"
    if system == "Linux":
        return None  # microsandbox checks KVM availability itself at boot time.
    if system == "Windows":
        return None  # preview support via WHP; let the runtime decide.
    return f"unsupported platform: {system}"


@dataclass
class SandboxConfig:
    image: str = "python"
    cpus: int = 1
    memory_mib: int = 1024
    persistent: bool = True
    network: str = "public"  # "public" | "none"
    idle_timeout_seconds: int = 1800


class SandboxManager:
    """Owns a single named microVM sandbox for one Tau process + cwd."""

    def __init__(self, cwd: Path, config: SandboxConfig) -> None:
        self._cwd = cwd
        self._config = config
        self._sandbox: Any = None
        self._lock = asyncio.Lock()
        digest = hashlib.sha256(str(cwd).encode()).hexdigest()[:12]
        self._cwd_label = digest
        # PID-qualified so a subagent process sharing this cwd never collides
        # with (and replace=True-clobbers) the parent session's live VM.
        self._name = f"tau-{digest}-{os.getpid()}"

    async def get(self) -> Any:
        """Return a live sandbox, booting one on first use."""
        async with self._lock:
            if self._sandbox is not None:
                return self._sandbox
            self._sandbox = await self._boot()
            return self._sandbox

    async def _boot(self) -> Any:
        reason = _unsupported_platform_reason()
        if reason is not None:
            raise SandboxUnavailableError(reason)

        try:
            import microsandbox
        except ImportError as e:
            raise SandboxUnavailableError(f"microsandbox package not installed: {e}") from e

        # Everything that touches the microsandbox API lives inside the try: the
        # promise of this extension is that an unusable runtime degrades to the
        # host terminal, and an API mismatch is exactly that. Building the
        # network/volume config outside it let an AttributeError escape as a
        # hard tool failure, so `terminal` broke instead of falling back.
        try:
            if not microsandbox.is_installed():
                # install() is sync (`def install() -> None`), so awaiting it
                # raises TypeError — and it downloads a runtime, so it must not
                # block the event loop either.
                await asyncio.to_thread(microsandbox.install)

            network = (
                microsandbox.Network.none()
                if self._config.network == "none"
                # Public egress only — no private ranges, no host. There is no
                # `public_only()`; profiles compose the same policy.
                else microsandbox.Network.from_profiles(microsandbox.NetworkProfile.PUBLIC)
            )
            volumes = {WORKDIR: microsandbox.Volume.bind(str(self._cwd))}

            return await microsandbox.Sandbox.create(
                self._name,
                image=self._config.image,
                cpus=self._config.cpus,
                memory=self._config.memory_mib,
                network=network,
                volumes=volumes,
                labels={"tau-cwd": self._cwd_label, "tau-pid": str(os.getpid())},
                idle_timeout=float(self._config.idle_timeout_seconds),
                ephemeral=True,
                replace=True,
            )
        except SandboxUnavailableError:
            raise
        except Exception as e:  # typed microsandbox errors, and API drift
            raise SandboxUnavailableError(f"failed to boot sandbox: {e}") from e

    async def reset(self) -> None:
        """Stop and drop the current sandbox; the next call boots a fresh one."""
        async with self._lock:
            sandbox = self._sandbox
            self._sandbox = None
        if sandbox is not None:
            with contextlib.suppress(Exception):
                await sandbox.stop()

    async def stop(self) -> None:
        await self.reset()

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> SandboxConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._sandbox is not None

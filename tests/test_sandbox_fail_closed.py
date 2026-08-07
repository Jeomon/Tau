"""An unbootable sandbox can refuse the command instead of running it on the host.

Falling back to the host is the default, and it is why this extension can ship
enabled: the microsandbox runtime is genuinely unavailable on some platforms,
and a terminal tool that only ever errors there would be worse than no sandbox.

It is also isolation disappearing without the caller being asked. Every reason
to route a command through a microVM is a reason not to run it on the host, and
a boot failure does not change that — so `fail_closed` exists for unattended or
untrusted work, where a refusal is the better outcome.

Three ways the sandbox becomes unavailable, all landing in the same place
(`manager._boot`): an unsupported platform, microsandbox not installed, and a
boot failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.ext_loader import load_extension

_PKG = load_extension("sandbox", builtin=True).__name__

import importlib  # noqa: E402

manager_mod = importlib.import_module(f"{_PKG}.manager")
tool_mod = importlib.import_module(f"{_PKG}.tool")

SandboxConfig = manager_mod.SandboxConfig
SandboxUnavailableError = manager_mod.SandboxUnavailableError


class _Manager:
    """A manager whose microVM never boots."""

    def __init__(self, config: SandboxConfig, reason: str) -> None:
        self.config = config
        self._reason = reason

    async def get(self) -> Any:
        raise SandboxUnavailableError(self._reason)

    async def reset(self) -> None: ...


class _Fallback:
    """Stands in for the real host terminal tool."""

    def __init__(self) -> None:
        self.ran = False

    async def execute(self, invocation, callback=None, signal=None, context=None):
        self.ran = True
        from tau.tool.types import ToolResult

        return ToolResult.ok(invocation.id, "ran on the host")


def _tool(*, fail_closed: bool, reason: str = "microsandbox package not installed"):
    from tau.tool.types import ToolInvocation

    config = SandboxConfig(fail_closed=fail_closed)
    tool = tool_mod.SandboxTerminalTool.__new__(tool_mod.SandboxTerminalTool)
    tool._manager = _Manager(config, reason)
    tool._fallback = _Fallback()
    tool._warned_unavailable = False
    tool._notify = lambda message: None
    invocation = ToolInvocation(id="c1", name="terminal", cwd=Path("."), params={"cmd": "ls"})
    return tool, invocation


@pytest.mark.anyio
async def test_the_default_still_falls_back_to_the_host() -> None:
    """Unchanged behaviour: this is why the extension can ship enabled."""
    tool, invocation = _tool(fail_closed=False)

    result = await tool.execute(invocation)

    assert tool._fallback.ran is True
    assert result.is_error is False


@pytest.mark.anyio
async def test_fail_closed_refuses_instead_of_running_on_the_host() -> None:
    tool, invocation = _tool(fail_closed=True)

    result = await tool.execute(invocation)

    assert tool._fallback.ran is False, "the command ran on the host despite fail_closed"
    assert result.is_error is True


@pytest.mark.anyio
async def test_the_refusal_names_the_cause() -> None:
    """ "Sandbox unavailable" alone is unactionable; the fix is usually in the cause."""
    tool, invocation = _tool(fail_closed=True, reason="unsupported platform: win32")

    result = await tool.execute(invocation)

    assert "unsupported platform: win32" in result.content


@pytest.mark.anyio
async def test_the_refusal_says_how_to_proceed() -> None:
    tool, invocation = _tool(fail_closed=True)

    result = await tool.execute(invocation)

    assert "fail_closed" in result.content
    assert "Sandbox" in result.content


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reason",
    [
        "unsupported platform: win32",
        "microsandbox package not installed: No module named 'microsandbox'",
        "failed to boot sandbox: connection refused",
    ],
)
async def test_every_unavailability_cause_is_refused(reason: str) -> None:
    """All three of manager._boot's raise sites reach the same decision."""
    tool, invocation = _tool(fail_closed=True, reason=reason)

    result = await tool.execute(invocation)

    assert tool._fallback.ran is False
    assert result.is_error is True


def test_the_default_is_off() -> None:
    """On by default would leave unsupported platforms with a dead terminal."""
    assert SandboxConfig().fail_closed is False


def test_the_setting_is_exposed_in_the_ui() -> None:
    """A knob only reachable by hand-editing settings.json is not a knob."""
    import json

    manifest_path = Path(str(tool_mod.__file__)).parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    keys = {f["key"] for f in manifest["tau"]["settings"]["fields"]}

    assert "fail_closed" in keys


def test_register_reads_the_setting() -> None:
    """The dataclass default is irrelevant if register() never passes it through."""
    source = (Path(str(tool_mod.__file__)).parent / "__init__.py").read_text()

    assert 'config.get("fail_closed"' in source

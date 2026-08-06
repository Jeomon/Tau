"""Reload-lifecycle audit for the bundled extensions.

`/reload` re-runs every `register()`, which means any state or resource created
there is rebuilt from scratch. Two failure modes follow, and neither is visible
at the call site:

* **State loss** — state restored only from `runtime_ready` / `session_start`
  is never restored again, because neither event fires on reload. The todo
  extension emptied its list this way while its snapshots sat on the branch.
* **Resource leak** — a subprocess, watcher, or VM created in `register()` and
  released only on `runtime_stop` / `session_shutdown` is orphaned, because the
  old object is dropped while still running. The sandbox extension stranded a
  microVM per reload this way.

`extension_unload` and `extension_reloaded` are the two events that bracket a
reload, so these tests assert the bundled extensions subscribe to whichever one
their lifetime requires.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BUILTIN_DIR = Path(__file__).parent.parent / "tau" / "builtins" / "extensions"

_ON_RE = re.compile(r'(?:@tau\.on|tau\.on)\(\s*"([a-z_]+)"')


def _events(extension: str) -> set[str]:
    """Every event name the extension subscribes to, across all its modules."""
    directory = BUILTIN_DIR / extension
    found: set[str] = set()
    for path in directory.rglob("*.py"):
        found.update(_ON_RE.findall(path.read_text(encoding="utf-8")))
    return found


# Extensions that rebuild mutable state in register() and therefore have to
# restore it after a reload.
STATEFUL = ["todo"]

# Extensions that create an external resource in register() (process, VM,
# server) and therefore have to release the old one when replaced.
RESOURCE_OWNING = ["sandbox", "web"]


@pytest.mark.parametrize("extension", STATEFUL)
def test_stateful_extensions_restore_state_after_reload(extension: str) -> None:
    events = _events(extension)
    assert "extension_reloaded" in events, (
        f"{extension} rebuilds state in register() but never restores it on reload; "
        "session_start does not fire on that path, so the state stays empty"
    )


@pytest.mark.parametrize("extension", RESOURCE_OWNING)
def test_resource_owning_extensions_release_on_unload(extension: str) -> None:
    events = _events(extension)
    assert "extension_unload" in events, (
        f"{extension} owns an external resource but never releases it on reload; "
        "the replaced instance keeps running with nothing holding a reference"
    )


def test_sandbox_stops_the_microvm_on_every_teardown_path() -> None:
    # A microVM outliving its manager holds real cpu/memory, so all three
    # teardown paths must reap it: session transition, process exit, reload.
    events = _events("sandbox")
    assert {"session_shutdown", "runtime_stop", "extension_unload"} <= events


def test_todo_restores_on_every_branch_changing_path() -> None:
    events = _events("todo")
    assert {"session_start", "session_tree", "extension_reloaded"} <= events

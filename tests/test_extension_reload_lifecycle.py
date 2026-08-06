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

ROOT = Path(__file__).parent.parent
BUILTIN_DIR = ROOT / "tau" / "builtins" / "extensions"
EXAMPLE_DIR = ROOT / "examples" / "extensions"

_ON_RE = re.compile(r'(?:@tau\.on|tau\.on)\(\s*"([a-z_]+)"')


def _events(extension: str, base: Path = BUILTIN_DIR) -> set[str]:
    """Every event name the extension subscribes to, across all its modules."""
    directory = base / extension
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

# Same two rules, applied to the shipped examples. `autoresearch` owns an
# above-editor widget: Session.hide() is guarded by self._shown, so only the
# outgoing Session can take its own widget down. These are checked through
# examples/ rather than the project-local copies, because `.gitignore` excludes
# `.tau/`, so what lives there varies per checkout.
EXAMPLE_STATEFUL = ["autoresearch", "todo"]
EXAMPLE_RESOURCE_OWNING = ["autoresearch", "sandbox"]


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


@pytest.mark.parametrize("extension", EXAMPLE_STATEFUL)
def test_example_stateful_extensions_restore_after_reload(extension: str) -> None:
    assert "extension_reloaded" in _events(extension, EXAMPLE_DIR), (
        f"examples/{extension} rebuilds state in register() but never restores it"
    )


@pytest.mark.parametrize("extension", EXAMPLE_RESOURCE_OWNING)
def test_example_resource_owning_extensions_release_on_unload(extension: str) -> None:
    assert "extension_unload" in _events(extension, EXAMPLE_DIR), (
        f"examples/{extension} never releases what it owns when replaced"
    )


def test_autoresearch_brackets_its_widget_with_both_reload_events() -> None:
    # Only the outgoing Session can remove its own widget (hide() is guarded by
    # self._shown), and only the incoming one can redraw — so both halves are
    # required or the dashboard is stranded on screen.
    events = _events("autoresearch", EXAMPLE_DIR)
    assert {"extension_unload", "extension_reloaded"} <= events

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


def _shorten_path(path: Path) -> str:
    """Abbreviate a path under the user's home directory, e.g. '~/code/tau'."""
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _list_worktrees(cwd: Path) -> list[tuple[Path, str]]:
    """Return (path, branch) for every worktree of the repo containing `cwd`.

    Empty (not an error) when `cwd` isn't inside a git repo, `git` isn't on
    PATH, or the command otherwise fails — callers just show a single-entry
    "no other worktrees" menu in that case.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    worktrees: list[tuple[Path, str]] = []
    path: Path | None = None
    branch = ""
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if path is not None:
                worktrees.append((path, branch))
            path = Path(line[len("worktree ") :])
            branch = ""
        elif line.startswith("branch "):
            branch = line[len("branch ") :].removeprefix("refs/heads/")
        elif line == "detached":
            branch = "(detached)"
    if path is not None:
        worktrees.append((path, branch))
    return worktrees


class WorktreeMenu:
    """Clickable project-path chip that opens a git-worktree switcher menu."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._button: Any | None = None

    def render(self) -> None:
        """Render the chip and its (initially empty) dropdown menu."""
        cwd = self._runtime.session_manager.cwd
        with ui.button(_shorten_path(cwd)).props("flat no-caps align=left").classes(
            "w-full truncate justify-start px-2 py-1 tau-project-path"
        ) as button:
            self._button = button
            with ui.menu() as menu:
                self._menu = menu
            menu.on("show", self._refresh_menu)

    def _refresh_menu(self) -> None:
        if self._button is None:
            return
        cwd = self._runtime.session_manager.cwd
        worktrees = _list_worktrees(cwd)

        self._menu.clear()
        with self._menu:
            if not worktrees:
                ui.menu_item("No git worktrees found", auto_close=True).props("disable")
                return
            for path, branch in worktrees:
                is_current = path.resolve() == cwd.resolve()
                label = f"{_shorten_path(path)}  ({branch})" if branch else _shorten_path(path)
                if is_current:
                    label += "  ✓"
                item = ui.menu_item(label, on_click=lambda p=path: self._switch(p))
                if is_current:
                    item.props("disable")

    async def _switch(self, path: Path) -> None:
        ok = await self._runtime.switch_worktree(path)
        if ok and self._button is not None:
            self._button.props(f'label="{_shorten_path(path)}"')
            ui.notify(f"Switched to worktree {_shorten_path(path)}", type="positive")
        elif not ok:
            ui.notify(f"Could not switch to {path}", type="negative")

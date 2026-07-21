from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import click

from tau.tui.style import RESET, Style
from tau.tui.widgets.symbols import FILL_HORIZONTAL

if TYPE_CHECKING:
    from tau.packages.manager import PackageManager
    from tau.settings.manager import SettingsManager


@click.command("update")
@click.argument("name", required=False, default=None)
@click.option("--all", "update_all", is_flag=True, help="Update Tau and all extension packages.")
@click.option(
    "--extensions",
    "update_extensions",
    is_flag=True,
    help="Update all extension packages only (not Tau).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Reinstall Tau even if the current version is already latest.",
)
@click.option(
    "--local", is_flag=True, default=False, help="Update in project scope instead of global."
)
def update(
    name: str | None,
    update_all: bool,
    update_extensions: bool,
    force: bool,
    local: bool,
) -> None:
    """Update tau itself, all extensions, or a single extension package by NAME."""
    if update_all and name is not None:
        raise click.ClickException("NAME cannot be combined with --all.")
    if update_extensions and name is not None:
        raise click.ClickException("NAME cannot be combined with --extensions.")
    if update_all and update_extensions:
        raise click.ClickException("--all and --extensions cannot be combined.")
    if force and (name is not None or update_extensions):
        raise click.ClickException("--force only applies to updating Tau itself.")

    # No target (and not --extensions) -> update Tau itself.
    if name is None and not update_all and not update_extensions:
        _update_tau(force=force)
        return

    from tau.packages.manager import PackageManager
    from tau.settings.paths import get_packages_venv
    from tau.trust.manager import create_project_settings_manager

    cwd = Path.cwd()
    # Project package entries carry an attacker-controllable `index_url` that is
    # passed straight to `pip install`, so they must not be read from an
    # untrusted project. Untrusted -> project settings are empty -> project
    # packages are simply absent from the update set.
    settings = create_project_settings_manager(cwd)

    # Bulk update: --all (Tau + every package) or --extensions (packages only).
    if update_all or update_extensions:
        if update_all:
            _update_tau(force=force)
        packages = settings.get_all_packages()
        for pkg in packages:
            scope_local = any(p.name == pkg.name for p in settings.get_packages(local=True))
            manager = PackageManager(get_packages_venv(cwd if scope_local else None))
            _update_package(manager, settings, pkg.name, scope_local)
        if packages:
            asyncio.run(settings.flush())
        elif update_extensions:
            click.echo("No extension packages to update.")
        return

    # Single named package.
    venv_dir = get_packages_venv(cwd) if local else get_packages_venv()
    pkg_manager = PackageManager(venv_dir)
    targets = [p for p in settings.get_packages(local=local) if p.name == name]
    if not targets:
        raise click.ClickException(f"Package '{name}' not found.")

    for pkg in targets:
        _update_package(pkg_manager, settings, pkg.name, local)

    asyncio.run(settings.flush())


def _update_package(
    manager: PackageManager, settings: SettingsManager, name: str, local: bool
) -> None:
    """Update one package and report its result."""
    click.echo(f"Updating {name}…")
    try:
        entries = settings.get_packages(local=local)
        entry = next((package for package in entries if package.name == name), None)
        new_version = manager.update(
            name,
            index_url=entry.index_url if entry else None,
            extra_index_urls=entry.extra_index_urls if entry else None,
        )
        settings.update_package_version(name, new_version, local=local)
        arrow = f" → {new_version}" if new_version else ""
        click.echo(click.style(f"✓ Updated {name}{arrow}", fg="green"))
    except Exception as exc:
        click.echo(click.style(f"✗ {name}: {exc}", fg="red"))


def _update_tau(force: bool = False) -> None:
    """Upgrade tau itself using whichever installer manages this install.

    ``force`` reinstalls even when already on the latest version, using each
    installer's reinstall switch, for a clean re-pin of a corrupt install.
    """
    import os
    import shutil
    import subprocess

    from tau.settings.paths import get_app_name, get_package_name

    app = get_package_name()

    # Pick the upgrade tool that matches how this copy was installed, inferred
    # from the venv it runs in, so we upgrade the right managed environment.
    # Only trust prefix-based detection here: falling back to "uv"/"pipx just
    # because they're on PATH (regardless of whether they installed this copy)
    # tells the wrong tool to upgrade a package it doesn't manage, which fails.
    prefix = sys.prefix.replace(os.sep, "/")
    if "/pipx/" in prefix and shutil.which("pipx"):
        cmd = ["pipx", "upgrade", *(["--force"] if force else []), app]
    elif "/uv/tools/" in prefix and shutil.which("uv"):
        cmd = ["uv", "tool", "upgrade", *(["--reinstall"] if force else []), app]
    else:
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            *(["--force-reinstall"] if force else []),
            app,
        ]

    with _progress_bar(f"Updating {get_app_name()}…"):
        result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        click.echo(click.style(f"✓ {get_app_name()} updated successfully", fg="green"))
    else:
        raise click.ClickException(result.stderr.strip() or "Update failed.")


# Width of the sweep track and the highlighted window within it, in cells.
_BAR_WIDTH = 24
_SWEEP_WIDTH = 8
_FRAME_INTERVAL = 0.08


def _sweep_frame(frame: int) -> str:
    """Render one frame of an indeterminate progress bar: a filled window that
    bounces back and forth across the track, built from the same fill glyph
    and Style the tui's own ``LineGauge`` widget uses (tau/tui/widgets/gauge.py)."""
    track_len = max(1, _BAR_WIDTH - _SWEEP_WIDTH)
    period = track_len * 2
    t = frame % period
    pos = t if t <= track_len else period - t
    fill = FILL_HORIZONTAL[-1]
    cells = [fill if pos <= i < pos + _SWEEP_WIDTH else " " for i in range(_BAR_WIDTH)]
    style = Style().with_fg("bright_cyan")
    return f"[{style.sgr()}{''.join(cells)}{RESET}]"


@contextmanager
def _progress_bar(message: str) -> Iterator[Callable[[], None]]:
    """Show an indeterminate sweep bar on stderr while a blocking call runs.

    ``subprocess.run(capture_output=True)`` gives no feedback until it
    returns, which can be tens of seconds for an installer download — animate
    on a background thread so the terminal doesn't look hung, then erase it
    in favor of the caller's own result line.
    """
    stop = threading.Event()

    def _animate() -> None:
        frame = 0
        while not stop.is_set():
            click.echo(f"\r{_sweep_frame(frame)} {message}", nl=False, err=True)
            frame += 1
            stop.wait(_FRAME_INTERVAL)
        click.echo("\r" + " " * (_BAR_WIDTH + len(message) + 3) + "\r", nl=False, err=True)

    thread = threading.Thread(target=_animate, daemon=True)
    thread.start()
    try:
        yield stop.set
    finally:
        stop.set()
        thread.join()

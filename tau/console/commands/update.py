from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from tau.packages.manager import PackageManager
    from tau.settings.manager import SettingsManager


@click.command("update")
@click.argument("name", required=False, default=None)
@click.option("--all", "update_all", is_flag=True, help="Update Tau and all extension packages.")
@click.option(
    "--local", is_flag=True, default=False, help="Update in project scope instead of global."
)
def update(name: str | None, update_all: bool, local: bool) -> None:
    """Update tau itself, or update an extension package by NAME."""
    if update_all and name is not None:
        raise click.ClickException("NAME cannot be combined with --all.")
    if name is None and not update_all:
        _update_tau()
        return

    from tau.packages.manager import PackageManager
    from tau.settings.manager import SettingsManager
    from tau.settings.paths import get_packages_venv

    cwd = Path.cwd()
    venv_dir = get_packages_venv(cwd) if local else get_packages_venv()
    pkg_manager = PackageManager(venv_dir)
    settings = SettingsManager.create(cwd)

    packages = settings.get_packages(local=local)
    if update_all:
        packages = settings.get_all_packages()
        _update_tau()
        if not packages:
            return
        for pkg in packages:
            scope_local = any(p.name == pkg.name for p in settings.get_packages(local=True))
            manager = PackageManager(get_packages_venv(cwd if scope_local else None))
            _update_package(manager, settings, pkg.name, scope_local)
        asyncio.run(settings.flush())
        return

    targets = [p for p in packages if p.name == name]

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


def _update_tau() -> None:
    """Upgrade tau itself using whichever installer manages this install."""
    import os
    import shutil
    import subprocess

    from tau.settings.paths import get_app_name, get_package_name

    app = get_package_name()
    click.echo(f"Updating {get_app_name()}…")

    # Pick the upgrade tool that matches how this copy was installed, inferred
    # from the venv it runs in, so we upgrade the right managed environment.
    prefix = sys.prefix.replace(os.sep, "/")
    if "/pipx/" in prefix and shutil.which("pipx"):
        cmd = ["pipx", "upgrade", app]
    elif "/uv/tools/" in prefix and shutil.which("uv") or shutil.which("uv"):
        cmd = ["uv", "tool", "upgrade", app]
    elif shutil.which("pipx"):
        cmd = ["pipx", "upgrade", app]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", app]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        click.echo(click.style(f"✓ {get_app_name()} updated successfully", fg="green"))
    else:
        raise click.ClickException(result.stderr.strip() or "Update failed.")

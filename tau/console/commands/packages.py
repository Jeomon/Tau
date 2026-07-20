from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import click


def _project_settings_manager(cwd: Path, *, local: bool):
    """Return a trust-gated settings manager, refusing project-scope writes when untrusted.

    Project settings are withheld from an untrusted project, so a project-scoped
    mutation would be computed against an empty settings object and persist a
    ``packages.list`` that drops the entries already on disk. Refusing is both
    safer and clearer than silently rewriting the user's file.
    """
    from tau.trust.manager import create_project_settings_manager

    settings = create_project_settings_manager(cwd)
    if local and not settings.is_project_trusted():
        raise click.ClickException(
            f"Project '{cwd}' is not trusted, so project-scoped packages cannot be "
            "modified. Start tau in this directory and approve the trust prompt, "
            "or re-run without --local to use global scope."
        )
    return settings


@click.command("install")
@click.argument("source")
@click.option(
    "--local",
    is_flag=True,
    default=False,
    help="Install to project scope (.tau/venv/) instead of global (~/.tau/venv/).",
)
@click.option("--index-url", default=None, help="Base URL of a private Python package index.")
@click.option("--extra-index-url", multiple=True, help="Additional Python package index URL.")
def install(
    source: str, local: bool, index_url: str | None, extra_index_url: tuple[str, ...]
) -> None:
    """Install a package as a tau extension source.

    SOURCE formats:
      pypi:name           install latest from PyPI
      pypi:name==1.2.3    install pinned version
      git+https://...     install from a git URL
      ./path  or  /path   install from a local directory, wheel, or source archive
      https://...whl      install a wheel or source archive URL
    """
    from tau.packages.manager import PackageManager
    from tau.settings.paths import get_packages_venv

    cwd = Path.cwd()
    venv_dir = get_packages_venv(cwd) if local else get_packages_venv()
    pkg_manager = PackageManager(venv_dir)

    # Resolve trust before installing: refusing afterwards would leave the package
    # in the venv with no settings entry recording it.
    settings = _project_settings_manager(cwd, local=local)

    click.echo(f"Installing {source}…")
    try:
        entry = pkg_manager.install(
            source,
            index_url=index_url,
            extra_index_urls=list(extra_index_url) or None,
        )
    except Exception as e:
        raise click.ClickException(str(e)) from e

    try:
        settings.add_package(entry, local=local)
        asyncio.run(settings.flush())
    except Exception as e:
        # Do not report success for an installation that cannot be persisted.
        # Best-effort rollback keeps the managed venv and settings aligned.
        with contextlib.suppress(Exception):
            pkg_manager.remove(entry.name)
        raise click.ClickException(f"Installed package could not be saved: {e}") from e

    v = f"@{entry.version}" if entry.version else ""
    scope = "project" if local else "global"
    click.echo(click.style(f"✓ Installed {entry.name}{v} ({scope})", fg="green"))


@click.command("remove")
@click.argument("name")
@click.option(
    "--local", is_flag=True, default=False, help="Remove from project scope instead of global."
)
def remove(name: str, local: bool) -> None:
    """Remove an installed package by NAME."""
    from tau.packages.manager import PackageManager
    from tau.settings.paths import get_packages_venv

    cwd = Path.cwd()
    venv_dir = get_packages_venv(cwd) if local else get_packages_venv()
    pkg_manager = PackageManager(venv_dir)

    settings = _project_settings_manager(cwd, local=local)

    click.echo(f"Removing {name}…")
    try:
        pkg_manager.remove(name)
    except Exception as e:
        raise click.ClickException(str(e)) from e

    try:
        settings.remove_package(name, local=local)
        asyncio.run(settings.flush())
    except Exception as e:
        raise click.ClickException(
            "Package was removed but settings could not be updated; "
            f"run 'tau install' to reconcile: {e}"
        ) from e

    click.echo(click.style(f"✓ Removed {name}", fg="green"))


@click.command("list")
@click.option("--local", is_flag=True, default=False, help="Show project-scoped packages only.")
@click.option(
    "--all", "show_all", is_flag=True, default=False, help="Show both global and project packages."
)
def list_packages(local: bool, show_all: bool) -> None:
    """List installed packages."""
    from tau.trust.manager import create_project_settings_manager

    cwd = Path.cwd()
    settings = create_project_settings_manager(cwd)

    # Listing is read-only, so report the withheld scope instead of refusing —
    # an empty project list would otherwise look like "nothing installed".
    if (show_all or local) and not settings.is_project_trusted():
        click.echo(
            click.style(
                f"Note: '{cwd}' is not trusted, so project-scoped packages are not shown.",
                fg="yellow",
            )
        )

    if show_all:
        packages = settings.get_all_packages()
        header = "Installed packages (global + project)"
    elif local:
        packages = settings.get_packages(local=True)
        header = "Installed packages (project)"
    else:
        packages = settings.get_packages(local=False)
        header = "Installed packages (global)"

    if not packages:
        click.echo("No packages installed.")
        return

    from tau.packages.utils import redact_source

    click.echo(f"{header}:\n")
    for pkg in packages:
        v = f"  {pkg.version}" if pkg.version else ""
        status = click.style("  [disabled]", fg="bright_black") if not pkg.enabled else ""
        source = click.style(f"  ({redact_source(pkg.source)})", fg="bright_black")
        click.echo(f"  {pkg.name}{v}{status}{source}")

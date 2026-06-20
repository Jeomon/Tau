"""
Theme registry.

Priority (highest wins):
    project  (.tau/themes/*.yaml  in cwd)
    global   (~/.tau/themes/*.yaml)
    builtin  (tau/builtins/themes/)

Supported file formats: .yaml, .yml, .json
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from tau.themes.types import ThemeLoadError
from tau.themes.loader import load_themes_from_dir

if TYPE_CHECKING:
    from tau.tui.theme import LayoutTheme


#: Name of the theme used when none is configured. Must always resolve to a
#: builtin so the TUI can start even with no global/project themes installed.
DEFAULT_THEME = "dark"


class ThemeRegistry:
    def __init__(self) -> None:
        """Initialize an empty theme registry."""
        self._registry: dict[str, Callable[[], "LayoutTheme"]] = {}
        self._source: dict[str, str] = {}
        self._builtins_loaded = False

    def _add(self, name: str, factory: "Callable[[], LayoutTheme]", source: str) -> None:
        """Register a theme factory."""
        key = name.lower()
        self._registry[key] = factory
        self._source[key] = source

    def _ensure_builtins(self) -> None:
        """Load builtin themes if not already loaded."""
        if self._builtins_loaded:
            return
        from tau.settings.paths import get_builtins_dir

        _dir = get_builtins_dir() / "themes"
        result = load_themes_from_dir(_dir)
        for name, theme in result.themes.items():
            self._add(name, lambda t=theme: t, "builtin")
        self._builtins_loaded = True

    def load_external(self, cwd: Path | None = None) -> list[ThemeLoadError]:
        """Load themes from global and optional project-specific directories."""
        from tau.settings.paths import get_themes_dir

        self._ensure_builtins()
        errors: list[ThemeLoadError] = []

        global_result = load_themes_from_dir(get_themes_dir())
        errors.extend(global_result.errors)
        for name, theme in global_result.themes.items():
            self._add(name, lambda t=theme: t, "global")

        if cwd is not None:
            project_result = load_themes_from_dir(get_themes_dir(cwd))
            errors.extend(project_result.errors)
            for name, theme in project_result.themes.items():
                self._add(name, lambda t=theme: t, "project")

        return errors

    def get(self, name: str) -> "LayoutTheme":
        """Retrieve and instantiate a theme by name (case-insensitive)."""
        self._ensure_builtins()
        loader = self._registry.get(name.lower())
        if loader is None:
            raise ValueError(
                f"Unknown theme {name!r}. Available: {', '.join(self._registry)}"
            )
        return loader()

    def get_default(self) -> "LayoutTheme":
        """Return a theme that is guaranteed to load.

        Tries the configured default, then any builtin, then falls back to a
        bare ``LayoutTheme()`` so the UI can always start — even when no theme
        files are present at all.
        """
        self._ensure_builtins()
        for name in (DEFAULT_THEME, "light"):
            loader = self._registry.get(name)
            if loader is not None:
                return loader()
        if self._registry:
            return next(iter(self._registry.values()))()
        from tau.tui.theme import LayoutTheme

        return LayoutTheme()

    def list(self) -> list[str]:
        """Return all available theme names."""
        self._ensure_builtins()
        return list(self._registry.keys())

    def source(self, name: str) -> str:
        """Return the source of a theme: 'builtin', 'global', 'project', or 'runtime'."""
        return self._source.get(name.lower(), "unknown")

    def register(
        self,
        name: str,
        theme_or_factory: "LayoutTheme | Callable[[], LayoutTheme]",
    ) -> None:
        """Register a custom theme (instance or factory function)."""
        if callable(theme_or_factory):
            self._add(name, theme_or_factory, "runtime")
        else:
            t = theme_or_factory
            self._add(name, lambda: t, "runtime")

    def unregister(self, name: str) -> None:
        """Remove a theme by name. Raises ValueError if not found."""
        key = name.lower()
        if key not in self._registry:
            raise ValueError(f"Theme {name!r} is not registered.")
        del self._registry[key]
        del self._source[key]


theme_registry = ThemeRegistry()

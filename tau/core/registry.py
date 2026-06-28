from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")
E = TypeVar("E")


class Registry[T, E](ABC):
    """
    Base for lazy-loaded 3-tier content registries (prompts, skills).

    Load priority (highest wins): project → global → builtin.
    Subclasses supply the loader, dir resolver, and result accessors.
    """

    def __init__(self) -> None:
        self._registry: dict[str, T] = {}
        self._builtins_loaded = False

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def _load_from_dir(self, path: Path) -> Any:
        """Load items from *path*; returns a result object with errors and items."""

    @abstractmethod
    def _get_dir(self, cwd: Path | None = None) -> Path:
        """Return the global (or *cwd*-scoped) config directory for this type."""

    @abstractmethod
    def _builtins_subdir(self) -> str:
        """Subdirectory name under the builtins root (e.g. 'prompts', 'skills')."""

    @abstractmethod
    def _extract_items(self, result: Any) -> dict[str, T]:
        """Pull the name→item mapping out of a load result."""

    @abstractmethod
    def _extract_errors(self, result: Any) -> list[E]:
        """Pull the error list out of a load result."""

    def _item_key(self, item: T) -> str:
        return getattr(item, "name", "").lower()

    # ── Shared implementation ─────────────────────────────────────────────────

    def _ensure_builtins(self) -> None:
        if self._builtins_loaded:
            return
        from tau.settings.paths import get_builtins_dir

        _dir = get_builtins_dir() / self._builtins_subdir()
        for item in self._extract_items(self._load_from_dir(_dir)).values():
            self._registry[self._item_key(item)] = item
        self._builtins_loaded = True

    def reload(
        self,
        cwd: Path | None = None,
        extra_paths: list[str] | None = None,
    ) -> list[E]:
        """Clear and reload builtins + external from scratch."""
        self._registry.clear()
        self._builtins_loaded = False
        errors = self.load_external(cwd)
        for p in extra_paths or []:
            r = self._load_from_dir(Path(p))
            errors.extend(self._extract_errors(r))
            self._registry.update(self._extract_items(r))
        return errors

    def load_external(self, cwd: Path | None = None) -> list[E]:
        """Load from global and optional project-specific directories."""
        self._ensure_builtins()
        errors: list[E] = []
        
        g = self._load_from_dir(self._get_dir())
        errors.extend(self._extract_errors(g))
        self._registry.update(self._extract_items(g))

        if cwd is not None:
            p = self._load_from_dir(self._get_dir(cwd))
            errors.extend(self._extract_errors(p))
            self._registry.update(self._extract_items(p))
        return errors

    def get(self, name: str) -> T | None:
        self._ensure_builtins()
        return self._registry.get(name.lower())

    def list(self) -> list[T]:
        self._ensure_builtins()
        return list(self._registry.values())

    def register(self, item: T) -> None:
        self._registry[self._item_key(item)] = item

    def unregister(self, name: str) -> None:
        key = name.lower()
        if key not in self._registry:
            raise ValueError(f"{name!r} is not registered.")
        del self._registry[key]

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

from tau.settings.paths import get_settings_path
from tau.settings.types import SCOPE, LockResult
from tau.utils.fs import atomic_write_text


class SettingsStorage(ABC):
    """Abstract storage backend for settings."""

    @abstractmethod
    def with_lock(self, scope: SCOPE, fn: Callable[[str | None], LockResult]) -> LockResult:
        """Execute fn with locked access to the storage."""
        pass


class FileSettingsStorage(SettingsStorage):
    """File-based storage backend with locking."""

    def __init__(self, cwd: Path, config_dir: Path | None = None):
        self.global_settings_path = (
            config_dir / "settings.json" if config_dir else get_settings_path()
        )
        self.project_settings_path = get_settings_path(cwd)
        self._ensure_parent_dir(self.global_settings_path)

    def _ensure_parent_dir(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _ensure_file_exists(self, path: Path) -> None:
        if not path.exists():
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o600)

    def with_lock(self, scope: SCOPE, fn: Callable[[str | None], LockResult]) -> LockResult:
        path = self.global_settings_path if scope == SCOPE.GLOBAL else self.project_settings_path
        lock_path = path.with_suffix(".lock")

        # Project scopes may not have a .tau directory yet; create it before
        # FileLock attempts to create its sibling lock file.
        self._ensure_parent_dir(path)
        with FileLock(lock_path):
            self._ensure_file_exists(path)
            current = path.read_text(encoding="utf-8") if path.exists() else "{}"
            result = fn(current)
            if result.next is not None:
                atomic_write_text(path, result.next)
                path.chmod(0o600)
            return result


class InMemorySettingsStorage(SettingsStorage):
    """In-memory storage backend for testing."""

    def __init__(self):
        self.global_data: str = "{}"
        self.project_data: str = "{}"

    def with_lock(self, scope: SCOPE, fn: Callable[[str | None], LockResult]) -> LockResult:
        current = self.global_data if scope == SCOPE.GLOBAL else self.project_data
        result = fn(current)
        if result.next is not None:
            if scope == SCOPE.GLOBAL:
                self.global_data = result.next
            else:
                self.project_data = result.next
        return result

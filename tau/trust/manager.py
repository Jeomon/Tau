from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from filelock import FileLock

from tau.settings.paths import get_config_dir
from tau.trust.types import TrustOption
from tau.trust.utils import find_nearest, get_trust_options, has_project_trust_inputs, normalize
from tau.utils.fs import atomic_write_text

_log = logging.getLogger(__name__)


class TrustStore:
    """Persists per-directory trust decisions in ``~/.tau/trust.json``.

    Trust walks up the directory tree — trusting a parent directory implicitly
    trusts all child directories beneath it.
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        base = config_dir or get_config_dir()
        self._path = base / "trust.json"

    # ── Read ──────────────────────────────────────────────────────────────────

    def _read(self) -> dict[str, bool | None]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            # Preserve the corrupt file before any later _write() replaces it,
            # so the raw decisions stay recoverable instead of being lost.
            backup = self._path.with_name(f"{self._path.name}.corrupt-{int(time.time())}")
            try:
                if not backup.exists():
                    shutil.copy2(self._path, backup)
                _log.warning(
                    "trust store corrupted at %s, resetting (original preserved at %s)",
                    self._path,
                    backup,
                )
            except OSError:
                _log.warning("trust store corrupted at %s, resetting", self._path)
            return {}

    def get(self, cwd: str | Path) -> bool | None:
        """Return the stored trust decision, or ``None`` if no decision exists."""
        data = self._read()
        if entry := find_nearest(data, normalize(cwd)):
            _, trusted = entry
            return trusted
        return None

    def get_stored_path(self, cwd: str | Path) -> str | None:
        """Return the directory path that holds the nearest trust decision, or ``None``."""
        data = self._read()
        entry = find_nearest(data, normalize(cwd))
        return entry[0] if entry is not None else None

    # ── Write ─────────────────────────────────────────────────────────────────

    def _lock(self) -> FileLock:
        """Return the lock serialising read-modify-write mutations of the store."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._path) + ".lock")

    def _write(self, data: dict[str, bool | None]) -> None:
        clean = {k: v for k, v in data.items() if v is not None}
        atomic_write_text(self._path, json.dumps(clean, indent=2, sort_keys=True))

    def set(self, cwd: str | Path, decision: bool | None) -> None:
        """Store a trust decision for *cwd*. Pass ``None`` to remove the entry."""
        with self._lock():
            data = self._read()
            key = normalize(cwd)
            if decision is None:
                data.pop(key, None)
            else:
                data[key] = decision
            self._write(data)

    def apply_option(self, option: TrustOption) -> None:
        """Persist a :class:`TrustOption`. ``save_path=None`` means session-only —
        nothing is written.
        """
        if option.save_path is None:
            return
        with self._lock():
            data = self._read()
            data[normalize(option.save_path)] = option.trusted
            if option.clear_child_path is not None:
                data.pop(normalize(option.clear_child_path), None)
            self._write(data)


# ── Module-level singleton ────────────────────────────────────────────────────

trust_store = TrustStore()


# ── Non-interactive resolution ────────────────────────────────────────────────


def resolve_project_trust(
    cwd: Path,
    *,
    override: bool | None = None,
    settings_manager: object | None = None,
) -> bool:
    """Resolve the trust decision for *cwd* without prompting.

    Mirrors the non-interactive branch of ``RuntimeContext.create``: a directory
    with no trust inputs needs no decision, an explicit override wins next, and
    otherwise the ``project_trust`` policy decides — with ``"ask"`` falling back
    to the stored decision, and to untrusted when none exists.

    *settings_manager* supplies the policy; pass one already loaded with
    ``project_trusted=False`` to avoid re-reading the settings files.
    """
    if not has_project_trust_inputs(cwd):
        return True
    if override is not None:
        return override

    sm = settings_manager
    if sm is None:
        from tau.settings.manager import SettingsManager

        sm = SettingsManager.create(cwd=cwd, project_trusted=False)

    policy = sm.get_project_trust()  # type: ignore[attr-defined]
    match policy:
        case "always":
            return True
        case "never":
            return False
        case _:
            stored = trust_store.get(cwd)
            return stored if stored is not None else False


def create_project_settings_manager(
    cwd: Path,
    config_dir: Path | None = None,
    *,
    override: bool | None = None,
):
    """Build a SettingsManager whose project settings are gated on trust.

    This is the entry point for non-interactive callers (the ``tau`` subcommands)
    that need project settings but have no way to prompt. The manager is built
    untrusted, used to read the trust policy, then upgraded in place when the
    project turns out to be trusted — which reloads project settings without a
    second pass over the files.
    """
    from tau.settings.manager import SettingsManager

    sm = SettingsManager.create(cwd=cwd, config_dir=config_dir, project_trusted=False)
    if resolve_project_trust(cwd, override=override, settings_manager=sm):
        sm.set_project_trusted(True)
    return sm


__all__ = [
    "TrustStore",
    "TrustOption",
    "trust_store",
    "has_project_trust_inputs",
    "get_trust_options",
    "resolve_project_trust",
    "create_project_settings_manager",
]

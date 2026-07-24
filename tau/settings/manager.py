from __future__ import annotations

import contextlib
import copy
import dataclasses as dc
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tau.engine.types import FollowupMode, SteeringMode
from tau.inference.types import ThinkingLevel, Transport
from tau.settings.storage import (
    FileSettingsStorage,
    InMemorySettingsStorage,
    LockResult,
    SettingsStorage,
)
from tau.settings.types import (
    SCOPE,
    BranchSummarySettings,
    CompactionSettings,
    ExtensionEntry,
    ExtensionsSettings,
    HTTPProxySettings,
    ImageSettings,
    ModelRef,
    ModelSettings,
    PackageEntry,
    PackagesSettings,
    ProviderRetrySettings,
    RetrySettings,
    Settings,
    SettingsError,
    TerminalSettings,
    ThinkingBudgetsSettings,
)
from tau.settings.utils import coerce_enum, set_nested

_log = logging.getLogger(__name__)

# "retry" is intentionally absent: it needs dedicated parsing (its nested
# ``provider`` object must itself become a ProviderRetrySettings) — see the
# ``key == "retry"`` branch in ``_settings_from_dict``.
_NESTED_FIELD_TYPES: dict[str, type] = {
    "thinking_budgets": ThinkingBudgetsSettings,
    "image": ImageSettings,
    "compaction": CompactionSettings,
    "branch_summary": BranchSummarySettings,
    "http_proxy": HTTPProxySettings,
    "terminal": TerminalSettings,
}

# Enum-typed top-level fields — JSON stores these as plain strings, so they must
# be coerced back into enum instances on load (the type hints promise enums and
# callers rely on `.value` / enum comparisons).
_ENUM_FIELD_TYPES: dict[str, type] = {
    "thinking_level": ThinkingLevel,
    "transport": Transport,
    "steering_mode": SteeringMode,
    "follow_up_mode": FollowupMode,
}

# Per-modality model slots and user-facing aliases. "text" is a facade over the
# flat model/provider keys; the rest live in the nested ``models`` object.
_MODALITY_SLOTS: frozenset[str] = frozenset({"text", "voice", "speak", "image", "video"})
_MODALITY_ALIASES: dict[str, str] = {"stt": "voice", "tts": "speak", "audio": "voice"}


class SettingsManager:
    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: Settings,
        initial_project: Settings,
        global_load_error: Exception | None = None,
        project_load_error: Exception | None = None,
        initial_errors: list[SettingsError] | None = None,
        project_trusted: bool = False,
        global_recovered_issues: list[str] | None = None,
        project_recovered_issues: list[str] | None = None,
    ):
        """Initialise with pre-loaded global and project settings and any load errors."""
        self.storage = storage
        self.global_settings = initial_global
        self._project_trusted: bool = project_trusted
        # Don't merge project settings if the project is untrusted
        self.project_settings = initial_project if project_trusted else Settings()
        self.settings = self._deep_merge_settings(initial_global, self.project_settings)
        self.modified_fields: set[str] = set()
        self.modified_nested_fields: dict[str, set[str]] = {}
        self.modified_project_fields: set[str] = set()
        self.modified_project_nested_fields: dict[str, set[str]] = {}
        self.global_settings_load_error: Exception | None = global_load_error
        self.project_settings_load_error: Exception | None = project_load_error
        # Fields that were malformed and reset to default rather than aborting
        # the whole scope's load — see SettingsManager._settings_from_dict.
        self.global_settings_recovered_issues: list[str] = global_recovered_issues or []
        self.project_settings_recovered_issues: list[str] = project_recovered_issues or []
        self.errors: list[SettingsError] = initial_errors.copy() if initial_errors else []
        self._write_queue = None
        self._batch_mode: bool = False

    @staticmethod
    def create(
        cwd: Path,
        config_dir: Path | None = None,
        project_trusted: bool = False,
    ) -> SettingsManager:
        """Create a SettingsManager backed by files in cwd
        (and optional config_dir for global settings).

        ``project_trusted`` defaults to False so that forgetting to pass it fails
        closed: an untrusted manager exposes no project settings, rather than
        silently merging `.tau/settings.json` from a directory the user never
        approved. Callers that need project settings must resolve trust first —
        see :func:`tau.trust.manager.create_project_settings_manager`.
        """
        storage = FileSettingsStorage(cwd, config_dir)
        return SettingsManager.from_storage(storage, project_trusted=project_trusted)

    @staticmethod
    def from_storage(storage: SettingsStorage, project_trusted: bool = False) -> SettingsManager:
        """Create a SettingsManager from an arbitrary storage backend."""
        global_settings, global_error, global_issues = SettingsManager._try_load_from_storage(
            storage, SCOPE.GLOBAL
        )
        project_settings, project_error, project_issues = SettingsManager._try_load_from_storage(
            storage, SCOPE.PROJECT
        )
        initial_errors = []
        if global_error:
            initial_errors.append(SettingsError(scope=SCOPE.GLOBAL, error=global_error))
        if project_error:
            initial_errors.append(SettingsError(scope=SCOPE.PROJECT, error=project_error))
        return SettingsManager(
            storage,
            global_settings,
            project_settings,
            global_error,
            project_error,
            initial_errors,
            project_trusted=project_trusted,
            global_recovered_issues=global_issues,
            project_recovered_issues=project_issues,
        )

    @staticmethod
    def in_memory(settings: dict | None = None) -> SettingsManager:
        """Create an in-memory SettingsManager with optional seed data
        (no file I/O, useful for testing).
        """
        storage = InMemorySettingsStorage()
        settings_dict = settings or {}
        storage.with_lock(
            SCOPE.GLOBAL,
            lambda _: LockResult(result=None, next=json.dumps(settings_dict, indent=2)),
        )
        return SettingsManager.from_storage(storage)

    @staticmethod
    def _parse_extension_entry(raw: Any) -> ExtensionEntry | None:
        if not isinstance(raw, dict) or "path" not in raw:
            return None
        valid = {f.name for f in dc.fields(ExtensionEntry)}
        return ExtensionEntry(**{k: v for k, v in raw.items() if k in valid})

    @staticmethod
    def _parse_package_entry(raw: Any) -> PackageEntry | None:
        if not isinstance(raw, dict) or "source" not in raw or "name" not in raw:
            return None
        valid = {f.name for f in dc.fields(PackageEntry)}
        return PackageEntry(**{k: v for k, v in raw.items() if k in valid})

    @staticmethod
    def _settings_from_dict(data: dict, issues: list[str] | None = None) -> Settings:
        """Construct a Settings instance from a raw dict,
        rebuilding nested dataclasses from plain dicts.

        A field whose stored value has the wrong shape (e.g. a plain string
        where an object was expected) is skipped, falling back to that
        field's default, rather than leaking the malformed raw value into the
        dataclass — dataclasses don't validate field types at construction,
        so an unguarded assignment would succeed silently and then break the
        first thing that calls a method on the field (e.g. ``.packages.list``
        on a field that's actually still a string). When ``issues`` is given,
        one human-readable description is appended per field skipped this
        way, so callers (``tau doctor``) can report exactly what was dropped
        instead of it failing silently or nuking the whole scope.
        """

        def _issue(key: str, expected: str, value: Any) -> None:
            if issues is not None:
                issues.append(
                    f"{key}: expected {expected}, got {type(value).__name__} — reset to default"
                )

        valid_settings = {f.name for f in dc.fields(Settings)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in valid_settings:
                continue
            if key in _NESTED_FIELD_TYPES:
                if value is None:
                    continue  # unset — the field's own default already covers this
                if not isinstance(value, dict):
                    _issue(key, "object", value)
                    continue
                nested_cls = _NESTED_FIELD_TYPES[key]
                valid_nested = {f.name for f in dc.fields(nested_cls)}
                kwargs[key] = nested_cls(**{k: v for k, v in value.items() if k in valid_nested})
            elif key == "retry":
                if value is None:
                    continue
                if not isinstance(value, dict):
                    _issue(key, "object", value)
                    continue
                provider = value.get("provider")
                if provider is not None and not isinstance(provider, dict):
                    _issue("retry.provider", "object", provider)
                    provider = None
                elif isinstance(provider, dict):
                    valid_provider = {f.name for f in dc.fields(ProviderRetrySettings)}
                    provider = ProviderRetrySettings(
                        **{k: v for k, v in provider.items() if k in valid_provider}
                    )
                valid_retry = {f.name for f in dc.fields(RetrySettings)}
                retry_kwargs = {
                    k: v for k, v in value.items() if k in valid_retry and k != "provider"
                }
                kwargs[key] = RetrySettings(**retry_kwargs, provider=provider)
            elif key == "model":
                if isinstance(value, dict):
                    valid_modalities = {f.name for f in dc.fields(ModelSettings)}
                    valid_ref = {f.name for f in dc.fields(ModelRef)}
                    ms_kwargs: dict[str, Any] = {}
                    for modality, ref in value.items():
                        if modality not in valid_modalities:
                            continue
                        if ref is None:
                            # no model configured for this modality — expected, not an error
                            continue
                        if not isinstance(ref, dict):
                            _issue(f"model.{modality}", "object", ref)
                            continue
                        ms_kwargs[modality] = ModelRef(
                            **{k: v for k, v in ref.items() if k in valid_ref}
                        )
                    kwargs[key] = ModelSettings(**ms_kwargs)
                elif isinstance(value, str):
                    # Legacy flat "model": "<id>" (+ sibling "provider"): fold into
                    # model.text so old config files load instead of crashing. The
                    # nested object is written back on the next save.
                    kwargs[key] = ModelSettings(
                        text=ModelRef(id=value, provider=data.get("provider"))
                    )
                elif value is not None:
                    _issue(key, "object or string", value)
            elif key == "extensions":
                if value is None:
                    continue
                if not isinstance(value, dict):
                    _issue(key, "object", value)
                    continue
                entries = None
                raw_list = value.get("list")
                if isinstance(raw_list, list):
                    entries = [
                        e
                        for e in (
                            SettingsManager._parse_extension_entry(item) for item in raw_list
                        )
                        if e is not None
                    ]
                elif raw_list is not None:
                    _issue("extensions.list", "array", raw_list)
                kwargs[key] = ExtensionsSettings(
                    enabled=value.get("enabled"),
                    list=entries,
                )
            elif key == "packages":
                if value is None:
                    continue
                if not isinstance(value, dict):
                    _issue(key, "object", value)
                    continue
                pkg_entries = None
                raw_list = value.get("list")
                if isinstance(raw_list, list):
                    pkg_entries = [
                        e
                        for e in (
                            SettingsManager._parse_package_entry(item) for item in raw_list
                        )
                        if e is not None
                    ]
                elif raw_list is not None:
                    _issue("packages.list", "array", raw_list)
                kwargs[key] = PackagesSettings(list=pkg_entries)
            elif key in _ENUM_FIELD_TYPES:
                coerced = coerce_enum(_ENUM_FIELD_TYPES[key], value)
                if coerced is None and value is not None:
                    _issue(key, "a valid enum value", value)
                kwargs[key] = coerced
            else:
                kwargs[key] = value
        return Settings(**kwargs)

    @staticmethod
    def _load_from_storage(storage: SettingsStorage, scope: SCOPE, issues: list[str]) -> Settings:
        """Read and parse settings for the given scope from storage.

        ``issues`` collects human-readable descriptions of any individual
        malformed field that was reset to its default rather than aborting
        the whole load — see ``_settings_from_dict``.
        """

        def load_fn(current):
            if not current:
                return LockResult(result=Settings(), next=None)
            parsed = json.loads(current)
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"settings file root must be an object, got {type(parsed).__name__}"
                )
            return LockResult(result=SettingsManager._settings_from_dict(parsed, issues), next=None)

        return storage.with_lock(scope, load_fn).result

    @staticmethod
    def _try_load_from_storage(
        storage: SettingsStorage, scope: SCOPE
    ) -> tuple[Settings, Exception | None, list[str]]:
        """Load settings for the given scope,
        returning an empty Settings and the error on failure.

        The third element lists individual fields that were malformed and
        reset to default rather than failing the whole load (empty on total
        failure, since nothing was recovered in that case).
        """
        issues: list[str] = []
        try:
            return (SettingsManager._load_from_storage(storage, scope, issues), None, issues)
        except Exception as e:
            return (Settings(), e, [])

    def _deep_merge_settings(
        self, global_settings: Settings, project_settings: Settings
    ) -> Settings:
        """Merge global and project settings; project wins,
        nested dataclasses merge field by field.
        """
        merged = copy.deepcopy(global_settings)
        for key, value in vars(project_settings).items():
            if value is None:
                continue
            existing = getattr(merged, key)
            if dc.is_dataclass(value) and existing is not None and dc.is_dataclass(existing):
                merged_nested = copy.deepcopy(existing)
                for f in dc.fields(value):
                    nested_val = getattr(value, f.name)
                    if nested_val is not None:
                        setattr(merged_nested, f.name, nested_val)
                setattr(merged, key, merged_nested)
            else:
                setattr(merged, key, value)
        return merged

    def _mark_modified(self, field: str, nested_field: str | None = None):
        """Record a global settings field (and optional nested key) as modified."""
        self.modified_fields.add(field)
        if nested_field:
            self.modified_nested_fields.setdefault(field, set()).add(nested_field)

    def _mark_project_modified(self, field: str, nested_field: str | None = None):
        """Record a project settings field (and optional nested key) as modified."""
        self.modified_project_fields.add(field)
        if nested_field:
            self.modified_project_nested_fields.setdefault(field, set()).add(nested_field)

    def _clone_modified_nested_fields(self, source: dict[str, set[str]]) -> dict[str, set[str]]:
        """Snapshot the nested-field modification tracker
        so async writes see state at enqueue time.
        """
        return {key: set(value) for key, value in source.items()}

    def _record_error(self, scope: SCOPE, error: Exception):
        """Append a scoped error to the error queue for later retrieval via drain_errors()."""
        self.errors.append(SettingsError(scope=scope, error=error))

    def _clear_modified_scope(
        self,
        scope: SCOPE,
        written_fields: set[str],
        written_nested_fields: dict[str, set[str]],
    ):
        """Reset modification tracking for the fields captured in a completed
        write's snapshot.

        Only the snapshot's fields are cleared — never the whole live set,
        which may have accumulated new marks since the write was enqueued
        (e.g. batch-mode changes made while a pre-batch write was in flight).
        """
        match scope:
            case SCOPE.GLOBAL:
                fields, nested = self.modified_fields, self.modified_nested_fields
            case SCOPE.PROJECT:
                fields, nested = self.modified_project_fields, self.modified_project_nested_fields
        fields -= written_fields
        for field_name, keys in written_nested_fields.items():
            remaining = nested.get(field_name)
            if remaining is None:
                continue
            remaining -= keys
            if not remaining:
                del nested[field_name]

    def _enqueue_write(
        self,
        scope: SCOPE,
        task: Callable[..., None],
        written_fields: set[str],
        written_nested_fields: dict[str, set[str]],
    ):
        """Chain an async write task so concurrent saves are serialised and never interleave."""
        import asyncio

        prev = self._write_queue

        async def chained() -> None:
            if prev is not None:
                with contextlib.suppress(Exception):
                    await prev
            try:
                task()
                # In batch mode marks must survive until save_batch() writes
                # them, even for fields this pre-batch write also touched.
                if not self._batch_mode:
                    self._clear_modified_scope(scope, written_fields, written_nested_fields)
            except Exception as e:
                _log.error("failed to persist %s settings: %s", scope, e, exc_info=True)
                self._record_error(scope, e)

        self._write_queue = asyncio.create_task(chained())

    def _persist_scoped_settings(
        self,
        scope: SCOPE,
        snapshot_settings: Settings,
        modified_fields: set[str],
        modified_nested_fields: dict[str, set[str]],
    ):
        """Write only the modified fields back to storage,
        merging at the key level to preserve concurrent changes.
        """

        def persist_fn(current):
            if current:
                try:
                    current_dict = json.loads(current)
                    if not isinstance(current_dict, dict):
                        current_dict = {}
                except (json.JSONDecodeError, ValueError):
                    # The on-disk file is corrupt — don't let that permanently
                    # block every future save; write from this in-memory
                    # snapshot instead of merging against garbage.
                    current_dict = {}
            else:
                current_dict = {}
            snapshot_dict = asdict(snapshot_settings)
            merged = dict(current_dict)
            for field_name in modified_fields:
                value = snapshot_dict.get(field_name)
                if field_name in modified_nested_fields and isinstance(value, dict):
                    base = current_dict.get(field_name) or {}
                    if isinstance(base, dict):
                        merged_nested = {**base}
                        for nested_key in modified_nested_fields[field_name]:
                            merged_nested[nested_key] = value.get(nested_key)
                        merged[field_name] = merged_nested
                    else:
                        merged[field_name] = value
                else:
                    merged[field_name] = value
            return LockResult(result=None, next=json.dumps(merged, indent=2, default=str))

        self.storage.with_lock(scope, persist_fn)

    def is_batching(self) -> bool:
        """Return True if batch mode is active (writes are deferred)."""
        return self._batch_mode

    def begin_batch(self) -> None:
        """Suppress disk writes until save_batch() is called.
        In-memory state still updates immediately.
        """
        self._batch_mode = True

    def save_batch(self) -> None:
        """End batch mode and write all accumulated changes to disk."""
        self._batch_mode = False
        self._save()

    def _save(self):
        """Update the merged view and enqueue an async write of modified global settings."""
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)
        if self.global_settings_load_error or self._batch_mode:
            return
        snapshot_global = copy.deepcopy(self.global_settings)
        modified_fields = set(self.modified_fields)
        modified_nested_fields = self._clone_modified_nested_fields(self.modified_nested_fields)

        def write_task():
            self._persist_scoped_settings(
                SCOPE.GLOBAL, snapshot_global, modified_fields, modified_nested_fields
            )

        self._enqueue_write(SCOPE.GLOBAL, write_task, modified_fields, modified_nested_fields)

    def _save_project_settings(self, settings: Settings):
        """Update the merged view and enqueue an async write of modified project settings."""
        self.project_settings = copy.deepcopy(settings)
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)
        if self.project_settings_load_error:
            return
        snapshot_project = copy.deepcopy(self.project_settings)
        modified_fields = set(self.modified_project_fields)
        modified_nested_fields = self._clone_modified_nested_fields(
            self.modified_project_nested_fields
        )

        def write_task():
            self._persist_scoped_settings(
                SCOPE.PROJECT, snapshot_project, modified_fields, modified_nested_fields
            )

        self._enqueue_write(SCOPE.PROJECT, write_task, modified_fields, modified_nested_fields)

    async def flush(self) -> None:
        """Wait for any pending async writes to complete."""
        if self._write_queue is not None:
            await self._write_queue

    def drain_errors(self) -> list[SettingsError]:
        """Return and clear all accumulated load and write errors."""
        drained = self.errors.copy()
        self.errors.clear()
        return drained

    def heal_settings_scope(self, scope: SCOPE) -> Path | None:
        """Back up a scope's on-disk settings file and rewrite it with only
        the fields that were actually recovered from it.

        Two cases, both driven by what's already in memory (no re-parsing):
        a total parse failure (invalid JSON, or a non-object root) heals to
        that scope's plain defaults, same as a missing file; a partial
        failure (the file parsed but one or more fields were malformed —
        see ``_settings_from_dict``'s ``issues``) heals to the already-loaded
        ``Settings``, which kept every field that *did* parse and only
        defaulted the broken one(s). Either way the original file is backed
        up alongside itself first (e.g. ``settings.json.corrupt-1730000000``)
        so nothing is destroyed — used by ``tau doctor --fix``.

        Returns the backup file's path, or None if this scope had nothing to
        heal (no load error and no recovered-field issues).
        """
        if scope == SCOPE.GLOBAL:
            has_error = self.global_settings_load_error is not None
            has_issues = bool(self.global_settings_recovered_issues)
            recovered = self.global_settings
        else:
            has_error = self.project_settings_load_error is not None
            has_issues = bool(self.project_settings_recovered_issues)
            recovered = self.project_settings

        if not has_error and not has_issues:
            return None

        backup_path: Path | None = None

        def heal_fn(current: str | None) -> LockResult:
            nonlocal backup_path
            if current and isinstance(self.storage, FileSettingsStorage):
                path = (
                    self.storage.global_settings_path
                    if scope == SCOPE.GLOBAL
                    else self.storage.project_settings_path
                )
                backup_path = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
                backup_path.write_text(current, encoding="utf-8")
            healed = json.dumps(asdict(recovered), indent=2, default=str)
            return LockResult(result=None, next=healed)

        self.storage.with_lock(scope, heal_fn)

        if scope == SCOPE.GLOBAL:
            self.global_settings_load_error = None
            self.global_settings_recovered_issues = []
        else:
            self.project_settings_load_error = None
            self.project_settings_recovered_issues = []
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)

        return backup_path

    async def reload(self) -> None:
        """Flush pending writes, reload both scopes from storage, and recompute the merged view."""
        await self.flush()
        global_load = SettingsManager._try_load_from_storage(self.storage, SCOPE.GLOBAL)
        if not global_load[1]:
            self.global_settings = global_load[0]
            self.global_settings_load_error = None
        else:
            self.global_settings_load_error = global_load[1]
            self._record_error(SCOPE.GLOBAL, global_load[1])
        self.global_settings_recovered_issues = global_load[2]

        self.modified_fields.clear()
        self.modified_nested_fields.clear()
        self.modified_project_fields.clear()
        self.modified_project_nested_fields.clear()

        # Only honour on-disk project settings when the project is trusted —
        # mirrors __init__ and set_project_trusted, both of which keep
        # project_settings empty while untrusted. Without this gate a reload
        # would start merging an untrusted project's .tau/settings.json (its
        # extensions, packages, etc.) that startup had correctly suppressed.
        if self._project_trusted:
            project_load = SettingsManager._try_load_from_storage(self.storage, SCOPE.PROJECT)
            if not project_load[1]:
                self.project_settings = project_load[0]
                self.project_settings_load_error = None
            else:
                self.project_settings_load_error = project_load[1]
                self._record_error(SCOPE.PROJECT, project_load[1])
            self.project_settings_recovered_issues = project_load[2]
        else:
            self.project_settings = Settings()
            self.project_settings_load_error = None
            self.project_settings_recovered_issues = []

        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)

    def apply_overrides(self, overrides: dict[str, Any]):
        """Apply runtime overrides on top of the current merged settings without persisting."""
        override_settings = SettingsManager._settings_from_dict(overrides)
        self.settings = self._deep_merge_settings(self.settings, override_settings)

    def get_global_settings(self) -> Settings:
        """Return a deep copy of the raw global settings (before project merge)."""
        return copy.deepcopy(self.global_settings)

    def get_project_settings(self) -> Settings:
        """Return a deep copy of the raw project settings (before global merge)."""
        return copy.deepcopy(self.project_settings)

    def get_theme(self) -> str | None:
        """Return the persisted UI theme name, or None if unset."""
        return self.settings.theme

    def set_theme(self, theme: str):
        """Set the UI theme name and persist to global settings."""
        self.global_settings.theme = theme
        self._mark_modified("theme")
        self._save()

    def get_external_editor_command(self) -> str:
        """Return the command the external-editor shortcut should run.

        Resolution order: the ``external_editor`` setting, then ``$VISUAL``,
        then ``$EDITOR``, then a platform default. Always returns something —
        a machine without any of these still has ``notepad``/``nano``, and
        failing to *launch* is reported when the spawn fails rather than
        pre-emptively here.
        """
        import os
        import sys

        configured = self.settings.external_editor
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
        for var in ("VISUAL", "EDITOR"):
            value = os.environ.get(var)
            if value and value.strip():
                return value.strip()
        return "notepad" if sys.platform == "win32" else "nano"

    def get_http_idle_timeout_ms(self) -> int:
        """Return the HTTP idle timeout in milliseconds (default: 60000)."""
        v = self.settings.http_idle_timeout_ms
        return v if v is not None else 60_000

    def set_http_idle_timeout_ms(self, value: int):
        """Set the HTTP idle timeout in milliseconds and persist to global settings."""
        self.global_settings.http_idle_timeout_ms = max(0, value)
        self._mark_modified("http_idle_timeout_ms")
        self._save()

    def get_quiet_startup(self) -> bool:
        """Return whether to suppress startup messages (default: False)."""
        v = self.settings.quiet_startup
        return v if v is not None else False

    def set_quiet_startup(self, value: bool):
        """Set whether to suppress startup messages and persist to global settings."""
        self.global_settings.quiet_startup = value
        self._mark_modified("quiet_startup")
        self._save()

    def get_telemetry(self) -> bool:
        """Return whether anonymous install/update telemetry is enabled.

        This is intentionally global-only so project settings cannot re-enable
        telemetry after a user has disabled it.
        """
        value = self.global_settings.telemetry
        return value if value is not None else True

    def set_telemetry(self, value: bool) -> None:
        """Enable or disable version-only telemetry in global settings."""
        self.global_settings.telemetry = value
        self._mark_modified("telemetry")
        self._save()

    def get_picker_max_visible(self) -> int:
        """Return the maximum number of visible picker items (default: 8)."""
        v = self.settings.picker_max_visible
        return v if v is not None else 8

    def set_picker_max_visible(self, value: int):
        """Set the maximum number of visible picker items and persist to global settings."""
        self.global_settings.picker_max_visible = max(1, value)
        self._mark_modified("picker_max_visible")
        self._save()

    def get_tool_result_preview_lines(self) -> int:
        """Return default-shell tool result preview lines (default: 5)."""
        value = self.settings.tool_result_preview_lines
        return max(1, value) if value is not None else 5

    def set_tool_result_preview_lines(self, value: int) -> None:
        """Set default-shell tool result preview lines."""
        self.global_settings.tool_result_preview_lines = max(1, value)
        self._mark_modified("tool_result_preview_lines")
        self._save()

    def get_show_thinking(self) -> bool:
        """Return whether to display extended thinking in responses (default: True)."""
        v = self.settings.show_thinking
        return v if v is not None else True

    def set_show_thinking(self, value: bool):
        """Set whether to display extended thinking and persist to global settings."""
        self.global_settings.show_thinking = value
        self._mark_modified("show_thinking")
        self._save()

    def get_show_tool_calls(self) -> bool:
        """Return whether to display tool calls in responses (default: True)."""
        v = self.settings.show_tool_calls
        return v if v is not None else True

    def set_show_tool_calls(self, value: bool):
        """Set whether to display tool calls and persist to global settings."""
        self.global_settings.show_tool_calls = value
        self._mark_modified("show_tool_calls")
        self._save()

    def get_show_images(self) -> bool:
        """Return whether to render inline images (default: True)."""
        v = self.settings.show_images
        return v if v is not None else True

    def set_show_images(self, value: bool) -> None:
        """Set whether to render inline images and persist to global settings."""
        self.global_settings.show_images = value
        self._mark_modified("show_images")
        self._save()

    def get_model_ref(self, modality: str) -> ModelRef | None:
        """Return the ``{id, provider}`` selected for a modality, or None if unset.

        ``modality`` is one of ``text|voice|speak|image|video`` (aliases
        ``stt``→voice, ``tts``→speak, ``audio``→voice). ``text`` is the chat model.
        """
        modality = _MODALITY_ALIASES.get(modality, modality)
        if modality not in _MODALITY_SLOTS:
            raise ValueError(f"unknown model modality: {modality!r}")
        model = self.settings.model
        return getattr(model, modality, None) if model is not None else None

    def set_model_ref(
        self,
        modality: str,
        provider: str,
        model_id: str,
        *,
        voice: str | None = None,
    ) -> None:
        """Persist a model selection and optional TTS voice to global settings."""
        modality = _MODALITY_ALIASES.get(modality, modality)
        if modality not in _MODALITY_SLOTS:
            raise ValueError(f"unknown model modality: {modality!r}")
        if self.global_settings.model is None:
            self.global_settings.model = ModelSettings()
        setattr(
            self.global_settings.model,
            modality,
            ModelRef(id=model_id, provider=provider, voice=voice),
        )
        self._mark_modified("model", modality)
        self._save()

    def get_thinking_level(self) -> ThinkingLevel | None:
        """Return the default thinking level, or None if unset."""
        return self.settings.thinking_level

    def set_thinking_level(self, level: ThinkingLevel):
        """Set the default thinking level and persist to global settings."""
        self.global_settings.thinking_level = level
        self._mark_modified("thinking_level")
        self._save()

    def get_transport(self) -> Transport:
        """Return the configured transport, defaulting to Transport.Auto."""
        return self.settings.transport or Transport.Auto

    def set_transport(self, transport: Transport):
        """Set the transport and persist to global settings."""
        self.global_settings.transport = transport
        self._mark_modified("transport")
        self._save()

    def get_steering_mode(self) -> SteeringMode:
        """Return the steering mode, defaulting to SteeringMode.OneAtATime."""
        return self.settings.steering_mode or SteeringMode.OneAtATime

    def set_steering_mode(self, mode: SteeringMode):
        """Set the steering mode and persist to global settings."""
        self.global_settings.steering_mode = mode
        self._mark_modified("steering_mode")
        self._save()

    def get_follow_up_mode(self) -> FollowupMode:
        """Return the follow-up mode, defaulting to FollowupMode.OneAtATime."""
        return self.settings.follow_up_mode or FollowupMode.OneAtATime

    def set_follow_up_mode(self, mode: FollowupMode):
        """Set the follow-up mode and persist to global settings."""
        self.global_settings.follow_up_mode = mode
        self._mark_modified("follow_up_mode")
        self._save()

    def get_enabled_models(self) -> list[str] | None:
        """Return the model filter patterns, or None if all models are enabled."""
        return self.settings.enabled_models

    def set_enabled_models(self, patterns: list[str] | None):
        """Set the model filter patterns and persist to global settings."""
        self.global_settings.enabled_models = patterns
        self._mark_modified("enabled_models")
        self._save()

    def get_session_dir(self) -> Path | None:
        """Return the resolved session storage directory, expanding ~ if present."""
        session_dir = self.settings.session_dir
        if session_dir is None:
            return None
        if session_dir == "~":
            return Path.home()
        if session_dir.startswith("~/"):
            return Path.home() / session_dir[2:]
        return Path(session_dir).resolve()

    def set_session_dir(self, path: str | None):
        """Set the session storage directory and persist to global settings."""
        self.global_settings.session_dir = path
        self._mark_modified("session_dir")
        self._save()

    # ── Image ─────────────────────────────────────────────────────────────────

    def get_image_auto_resize(self) -> bool:
        """Return whether images are auto-resized to 2000x2000
        before being sent to the LLM (default: True).
        """
        i = self.settings.image
        return i.auto_resize if i and i.auto_resize is not None else True

    def set_image_auto_resize(self, enabled: bool):
        """Set whether to auto-resize images and persist to global settings."""
        if not self.global_settings.image:
            self.global_settings.image = ImageSettings()
        self.global_settings.image.auto_resize = enabled
        self._mark_modified("image", "auto_resize")
        self._save()

    def get_image_block_images(self) -> bool:
        """Return whether sending images to the LLM is blocked entirely (default: False)."""
        i = self.settings.image
        return i.block_images if i and i.block_images is not None else False

    def set_image_block_images(self, enabled: bool):
        """Set whether to block sending images to the LLM and persist to global settings."""
        if not self.global_settings.image:
            self.global_settings.image = ImageSettings()
        self.global_settings.image.block_images = enabled
        self._mark_modified("image", "block_images")
        self._save()

    # ── Terminal / execution ──────────────────────────────────────────────────────

    def get_shell_path(self) -> str | None:
        return self.settings.terminal.shell_path if self.settings.terminal else None

    def set_shell_path(self, path: str | None):
        if self.global_settings.terminal is None:
            self.global_settings.terminal = TerminalSettings()
        self.global_settings.terminal.shell_path = path
        self._mark_modified("terminal")
        self._save()

    def get_shell_command_prefix(self) -> str | None:
        return self.settings.terminal.shell_command_prefix if self.settings.terminal else None

    def set_shell_command_prefix(self, prefix: str | None):
        if self.global_settings.terminal is None:
            self.global_settings.terminal = TerminalSettings()
        self.global_settings.terminal.shell_command_prefix = prefix
        self._mark_modified("terminal")
        self._save()

    # ── Branch summary ────────────────────────────────────────────────────────

    # ── Compaction ────────────────────────────────────────────────────────────

    def is_compaction_enabled(self) -> bool:
        """Return whether auto-compaction is enabled (default: True)."""
        cs = self.settings.compaction
        v = cs.enabled if cs is not None else None
        return v if v is not None else True

    def get_compaction_reserve_tokens(self) -> int:
        """Return the token reserve for LLM response (default: 16384)."""
        cs = self.settings.compaction
        v = cs.reserve_tokens if cs is not None else None
        return v if v is not None else 16_384

    def get_compaction_keep_recent_tokens(self) -> int:
        """Return the token count for recent messages to keep (default: 20000)."""
        cs = self.settings.compaction
        v = cs.keep_recent_tokens if cs is not None else None
        return v if v is not None else 20_000

    def set_compaction_enabled(self, value: bool) -> None:
        if self.global_settings.compaction is None:
            self.global_settings.compaction = CompactionSettings()
        self.global_settings.compaction.enabled = value
        self._mark_modified("compaction", "enabled")
        self._save()

    def set_compaction_reserve_tokens(self, value: int) -> None:
        if self.global_settings.compaction is None:
            self.global_settings.compaction = CompactionSettings()
        self.global_settings.compaction.reserve_tokens = max(1, value)
        self._mark_modified("compaction", "reserve_tokens")
        self._save()

    def set_compaction_keep_recent_tokens(self, value: int) -> None:
        if self.global_settings.compaction is None:
            self.global_settings.compaction = CompactionSettings()
        self.global_settings.compaction.keep_recent_tokens = max(1, value)
        self._mark_modified("compaction", "keep_recent_tokens")
        self._save()

    def is_branch_summary_enabled(self) -> bool:
        """Return whether branch summarization is enabled (default: True)."""
        bs = self.settings.branch_summary
        v = bs.enabled if bs is not None else None
        return v if v is not None else True

    def set_branch_summary_enabled(self, value: bool) -> None:
        """Set whether branch summarization is enabled and persist to global settings."""
        if self.global_settings.branch_summary is None:
            self.global_settings.branch_summary = BranchSummarySettings()
        self.global_settings.branch_summary.enabled = value
        self._mark_modified("branch_summary", "enabled")
        self._save()

    def get_branch_summary_skip_prompt(self) -> bool:
        """Return whether to skip the 'Summarize branch?' prompt (default: False)."""
        bs = self.settings.branch_summary
        v = bs.skip_prompt if bs is not None else None
        return v if v is not None else False

    def get_branch_summary_reserve_tokens(self) -> int:
        """Return the token reserve for branch summarization (default: 16384)."""
        bs = self.settings.branch_summary
        v = bs.reserve_tokens if bs is not None else None
        return v if v is not None else 16_384

    def set_branch_summary_reserve_tokens(self, value: int) -> None:
        """Set the token reserve for branch summarization and persist to global settings."""
        if self.global_settings.branch_summary is None:
            self.global_settings.branch_summary = BranchSummarySettings()
        self.global_settings.branch_summary.reserve_tokens = max(1, value)
        self._mark_modified("branch_summary", "reserve_tokens")
        self._save()

    def set_branch_summary_skip_prompt(self, value: bool) -> None:
        """Set whether to skip the 'Summarize branch?' prompt and persist to global settings."""
        if self.global_settings.branch_summary is None:
            self.global_settings.branch_summary = BranchSummarySettings()
        self.global_settings.branch_summary.skip_prompt = value
        self._mark_modified("branch_summary", "skip_prompt")
        self._save()

    # ── Retry ─────────────────────────────────────────────────────────────────

    def is_retry_enabled(self) -> bool:
        """Return whether automatic retry is enabled (default: False)."""
        rs = self.settings.retry
        v = rs.enabled if rs is not None else None
        return v if v is not None else False

    def get_retry_max_retries(self) -> int:
        """Return the maximum retry attempts (default: 3)."""
        rs = self.settings.retry
        v = rs.max_retries if rs is not None else None
        return v if v is not None else 3

    def get_retry_base_delay_ms(self) -> int:
        """Return the base retry delay in milliseconds (default: 1000)."""
        rs = self.settings.retry
        v = rs.base_delay_ms if rs is not None else None
        return v if v is not None else 1000

    def set_retry_enabled(self, enabled: bool) -> None:
        """Set whether automatic retry is enabled and persist to global settings."""
        if self.global_settings.retry is None:
            self.global_settings.retry = RetrySettings()
        self.global_settings.retry.enabled = enabled
        self._mark_modified("retry", "enabled")
        self._save()

    def set_retry_max_retries(self, value: int) -> None:
        """Set the maximum retry attempts and persist to global settings."""
        if self.global_settings.retry is None:
            self.global_settings.retry = RetrySettings()
        self.global_settings.retry.max_retries = max(0, value)
        self._mark_modified("retry", "max_retries")
        self._save()

    def set_retry_base_delay_ms(self, value: int) -> None:
        """Set the base retry delay in milliseconds and persist to global settings."""
        if self.global_settings.retry is None:
            self.global_settings.retry = RetrySettings()
        self.global_settings.retry.base_delay_ms = max(1, value)
        self._mark_modified("retry", "base_delay_ms")
        self._save()

    # ── Thinking budgets ──────────────────────────────────────────────────────

    def get_thinking_budget(self, level: str) -> int:
        """Return the token budget for a thinking level (minimal, low, medium, high, xhigh, max)."""
        tb = self.settings.thinking_budgets
        if tb is None:
            tb = ThinkingBudgetsSettings()
        level_lower = level.lower()
        v = getattr(tb, level_lower, None)
        if v is not None:
            return v
        # Fall back to defaults matching ThinkingBudgets in inference/types.py
        defaults = {
            "minimal": 1024,
            "low": 2048,
            "medium": 4096,
            "high": 8192,
            "xhigh": 16384,
            "max": 32768,
        }
        return defaults.get(level_lower, 4096)

    def get_all_thinking_budgets(self) -> dict[str, int]:
        """Return all thinking budgets as a dict (includes defaults for unset levels)."""
        result = {}
        for level in ["minimal", "low", "medium", "high", "xhigh", "max"]:
            result[level] = self.get_thinking_budget(level)
        return result

    def set_thinking_budget(self, level: str, value: int) -> None:
        """Set the token budget for a thinking level and persist to global settings."""
        if self.global_settings.thinking_budgets is None:
            self.global_settings.thinking_budgets = ThinkingBudgetsSettings()
        level_lower = level.lower()
        if level_lower not in ("minimal", "low", "medium", "high", "xhigh", "max"):
            raise ValueError(f"Invalid thinking level: {level}")
        setattr(self.global_settings.thinking_budgets, level_lower, max(1, value))
        self._mark_modified("thinking_budgets", level_lower)
        self._save()

    def set_all_thinking_budgets(self, budgets: dict[str, int]) -> None:
        """Set all thinking budgets from a dict and persist to global settings."""
        if self.global_settings.thinking_budgets is None:
            self.global_settings.thinking_budgets = ThinkingBudgetsSettings()
        for level, value in budgets.items():
            level_lower = level.lower()
            if level_lower in ("minimal", "low", "medium", "high", "xhigh", "max"):
                setattr(self.global_settings.thinking_budgets, level_lower, max(1, value))
        self._mark_modified("thinking_budgets")
        self._save()

    # ── Extensions ────────────────────────────────────────────────────────────

    def is_extensions_enabled(self) -> bool:
        """Return whether extensions are globally enabled (default: True)."""
        ext = self.settings.extensions
        return ext.enabled if ext is not None and ext.enabled is not None else True

    def set_extensions_enabled(self, enabled: bool) -> None:
        """Toggle all extensions on/off and persist to global settings."""
        if self.global_settings.extensions is None:
            self.global_settings.extensions = ExtensionsSettings()
        self.global_settings.extensions.enabled = enabled
        self._mark_modified("extensions", "enabled")
        self._save()

    def get_extension_list(self) -> list[ExtensionEntry]:
        """Return extension entries from the merged settings view (project overrides global)."""
        ext = self.settings.extensions
        return ext.list if ext is not None and ext.list is not None else []

    def get_all_extension_entries(self) -> list[ExtensionEntry]:
        """Return extension entries from BOTH global and project scopes for runtime
        loading and the settings panel.

        The merged view (:meth:`get_extension_list`) lets a project's
        ``extensions.list`` replace the global one wholesale — nested-dataclass
        merge overwrites the whole ``list`` field. That drops the persisted config
        of globally-installed extensions that are still discovered and loaded from
        ``~/.tau/extensions``, so their /settings panel falls back to manifest
        defaults. Loading needs every discovered extension's config regardless of
        scope, so combine both lists keyed by path (project wins on a path
        collision). Mirrors :meth:`get_all_packages`.
        """
        by_path: dict[str, ExtensionEntry] = {}
        for source in (self.global_settings, self.project_settings):
            ext = source.extensions
            if ext is not None and ext.list:
                for entry in ext.list:
                    by_path[entry.path] = entry
        return list(by_path.values())

    def get_extension_paths(self) -> list[str]:
        """Return extension paths from the merged entry list (convenience flat view)."""
        return [entry.path for entry in self.get_extension_list()]

    @staticmethod
    def _resolve_extension_entry_path(entry_path: str, cwd: Path) -> Path:
        p = Path(entry_path).expanduser()
        return p if p.is_absolute() else (cwd / p).resolve()

    def prune_dangling_extensions(self, cwd: Path) -> list[tuple[str, ExtensionEntry]]:
        """Remove enabled extension entries (both scopes) whose path no longer exists.

        Called during extension load/reload housekeeping (startup and every
        ``/extensions`` reload) and by ``tau doctor --fix``, so a moved or
        deleted extension's settings.json record doesn't linger and get
        mislabeled once its old path stops matching anything real. Disabled
        entries are left untouched — a deliberate disable shouldn't be undone
        by silently deleting its record.

        Returns the ``(scope, entry)`` pairs that were removed.
        """
        removed: list[tuple[str, ExtensionEntry]] = []
        scopes = (
            ("global", self.global_settings, self.set_extension_list),
            ("project", self.project_settings, self.set_project_extension_list),
        )
        for scope_name, settings_obj, set_entries in scopes:
            ext = settings_obj.extensions
            entries = list(ext.list) if ext and ext.list else []
            kept = []
            changed = False
            for entry in entries:
                if not entry.enabled or self._resolve_extension_entry_path(
                    entry.path, cwd
                ).exists():
                    kept.append(entry)
                    continue
                changed = True
                removed.append((scope_name, entry))
            if changed:
                set_entries(kept)
        return removed

    def set_extension_paths(self, paths: list[str]) -> None:
        """Set extension paths as plain entries, preserving the list shape."""
        self.set_extension_list([ExtensionEntry(path=p) for p in paths])

    def set_extension_config_key(self, ext_path: str, key: str, value: Any) -> None:
        """Set a key (dot-notation supported) in the config dict of the matching extension entry.

        ``key`` may be a dot-separated path such as ``"retry.enabled"`` to set
        nested values; intermediate dicts are created automatically.
        """
        if self.global_settings.extensions is None:
            self.global_settings.extensions = ExtensionsSettings()
        if self.global_settings.extensions.list is None:
            self.global_settings.extensions.list = []
        for entry in self.global_settings.extensions.list:
            if entry.path == ext_path:
                if entry.settings is None:
                    entry.settings = {}
                set_nested(entry.settings, key, value)
                self._mark_modified("extensions", "list")
                self._save()
                return
        # No matching entry found — create one
        config: dict = {}
        set_nested(config, key, value)
        new_entry = ExtensionEntry(path=ext_path, settings=config)
        self.global_settings.extensions.list.append(new_entry)
        self._mark_modified("extensions", "list")
        self._save()

    def set_extension_list(self, entries: list[ExtensionEntry]) -> None:
        """Set the global extension list and persist."""
        if self.global_settings.extensions is None:
            self.global_settings.extensions = ExtensionsSettings()
        self.global_settings.extensions.list = entries
        self._mark_modified("extensions", "list")
        self._save()

    def set_project_extension_list(self, entries: list[ExtensionEntry]) -> None:
        """Set the project-scoped extension list and persist."""
        if self.project_settings.extensions is None:
            self.project_settings.extensions = ExtensionsSettings()
        self.project_settings.extensions.list = entries
        self._mark_project_modified("extensions", "list")
        self._save_project_settings(self.project_settings)

    # ── Packages ──────────────────────────────────────────────────────────────

    def get_all_packages(self) -> list[PackageEntry]:
        """Return all packages from both global and project settings (for runtime loading)."""
        global_pkgs = self.global_settings.packages
        project_pkgs = self.project_settings.packages
        result: list[PackageEntry] = []
        if global_pkgs and global_pkgs.list:
            result.extend(global_pkgs.list)
        if project_pkgs and project_pkgs.list:
            result.extend(project_pkgs.list)
        return result

    def get_packages(self, local: bool = False) -> list[PackageEntry]:
        """Return packages from the given scope (global by default, project if local=True)."""
        source = self.project_settings if local else self.global_settings
        pkgs = source.packages
        return list(pkgs.list) if pkgs and pkgs.list else []

    def add_package(self, entry: PackageEntry, local: bool = False) -> None:
        """Add or replace a package entry in the given scope and persist."""
        if local:
            if self.project_settings.packages is None:
                self.project_settings.packages = PackagesSettings()
            pkgs = list(self.project_settings.packages.list or [])
            pkgs = [p for p in pkgs if p.name != entry.name]
            pkgs.append(entry)
            self.project_settings.packages.list = pkgs
            self._mark_project_modified("packages", "list")
            self._save_project_settings(self.project_settings)
        else:
            if self.global_settings.packages is None:
                self.global_settings.packages = PackagesSettings()
            pkgs = list(self.global_settings.packages.list or [])
            pkgs = [p for p in pkgs if p.name != entry.name]
            pkgs.append(entry)
            self.global_settings.packages.list = pkgs
            self._mark_modified("packages", "list")
            self._save()

    def remove_package(self, name: str, local: bool = False) -> None:
        """Remove a package entry by name from the given scope and persist."""
        if local:
            if self.project_settings.packages is None:
                return
            pkgs = [p for p in (self.project_settings.packages.list or []) if p.name != name]
            self.project_settings.packages.list = pkgs
            self._mark_project_modified("packages", "list")
            self._save_project_settings(self.project_settings)
        else:
            if self.global_settings.packages is None:
                return
            pkgs = [p for p in (self.global_settings.packages.list or []) if p.name != name]
            self.global_settings.packages.list = pkgs
            self._mark_modified("packages", "list")
            self._save()

    def update_package_version(self, name: str, version: str | None, local: bool = False) -> None:
        """Update the stored version (and installed_path) for an existing package."""
        pkgs = self.project_settings.packages if local else self.global_settings.packages
        if not pkgs or not pkgs.list:
            return
        for entry in pkgs.list:
            if entry.name == name:
                entry.version = version
                break
        if local:
            self._mark_project_modified("packages", "list")
            self._save_project_settings(self.project_settings)
        else:
            self._mark_modified("packages", "list")
            self._save()

    # ── UI behaviour ──────────────────────────────────────────────────────────

    def get_double_escape_action(self) -> str:
        """Return the double-Escape action (default: ``"clear"``)."""
        v = self.settings.double_escape_action
        return v if v is not None else "clear"

    def set_double_escape_action(self, value: str) -> None:
        """Set the double-escape key action and persist to global settings."""
        if value not in ("clear", "fork", "tree", "none"):
            raise ValueError(
                f"double_escape_action must be 'clear', 'fork', 'tree', or 'none', got {value!r}"
            )
        self.global_settings.double_escape_action = value  # type: ignore[assignment]
        self._mark_modified("double_escape_action")
        self._save()

    def get_tree_filter_mode(self) -> str:
        """Return the message tree filter mode (default: 'default')."""
        v = self.settings.tree_filter_mode
        return v if v is not None else "default"

    def set_tree_filter_mode(self, value: str) -> None:
        """Set the message tree filter mode and persist to global settings."""
        valid = ("default", "no-tools", "user-only", "labeled-only", "all")
        if value not in valid:
            raise ValueError(f"tree_filter_mode must be one of {valid}, got {value!r}")
        self.global_settings.tree_filter_mode = value  # type: ignore[assignment]
        self._mark_modified("tree_filter_mode")
        self._save()

    def get_autocomplete_max_visible(self) -> int:
        """Return the maximum number of visible autocomplete suggestions (default: 5)."""
        v = self.settings.autocomplete_max_visible
        return v if v is not None else 5

    def set_autocomplete_max_visible(self, value: int) -> None:
        """Set the maximum number of visible autocomplete suggestions
        and persist to global settings.
        """
        self.global_settings.autocomplete_max_visible = max(1, value)
        self._mark_modified("autocomplete_max_visible")
        self._save()

    def get_show_hardware_cursor(self) -> bool:
        """Return whether to show hardware cursor in the UI (default: False)."""
        v = self.settings.show_hardware_cursor
        return v if v is not None else False

    def set_show_hardware_cursor(self, value: bool) -> None:
        """Set whether to show hardware cursor and persist to global settings."""
        self.global_settings.show_hardware_cursor = value
        self._mark_modified("show_hardware_cursor")
        self._save()

    def get_cursor_blink(self) -> bool:
        """Return whether the input cursor blinks when idle and focused (default: True)."""
        v = self.settings.cursor_blink
        return v if v is not None else True

    def set_cursor_blink(self, value: bool) -> None:
        """Set whether the input cursor blinks and persist to global settings."""
        self.global_settings.cursor_blink = value
        self._mark_modified("cursor_blink")
        self._save()

    def get_editor_padding_x(self) -> int:
        """Return the horizontal editor padding in characters (default: 0)."""
        v = self.settings.editor_padding_x
        return v if v is not None else 0

    def set_editor_padding_x(self, value: int) -> None:
        """Set the horizontal editor padding and persist to global settings."""
        self.global_settings.editor_padding_x = max(0, value)
        self._mark_modified("editor_padding_x")
        self._save()

    def get_websocket_connect_timeout_ms(self) -> int | None:
        """Return the websocket connection timeout in milliseconds, or None if unset."""
        return self.settings.websocket_connect_timeout_ms

    def set_websocket_connect_timeout_ms(self, value: int | None) -> None:
        """Set the websocket connection timeout and persist to global settings."""
        self.global_settings.websocket_connect_timeout_ms = value
        self._mark_modified("websocket_connect_timeout_ms")
        self._save()

    # ── HTTP Proxy ────────────────────────────────────────────────────────────

    def get_proxy_url(self) -> str | None:
        """Return the HTTP/HTTPS proxy URL from settings (overrides env vars).

        The stored value may be a literal URL, ``$ENV_VAR``, or ``!command``; it
        is resolved once and cached (see ``tau.utils.secrets``).
        """
        from tau.utils.secrets import resolve_secret

        proxy = self.settings.http_proxy
        if not (proxy and proxy.url):
            return None
        return resolve_secret(proxy.url) or None

    def get_no_proxy(self) -> str | None:
        """Return the NO_PROXY exclusion list from settings (overrides env var)."""
        proxy = self.settings.http_proxy
        return proxy.no_proxy if proxy and proxy.no_proxy else None

    def set_proxy_url(self, url: str | None) -> None:
        """Set the HTTP/HTTPS proxy URL and persist to global settings."""
        if self.global_settings.http_proxy is None:
            self.global_settings.http_proxy = HTTPProxySettings()
        self.global_settings.http_proxy.url = url
        self._mark_modified("http_proxy", "url")
        self._save()

    def set_no_proxy(self, hosts: str | None) -> None:
        """Set the NO_PROXY exclusion list and persist to global settings."""
        if self.global_settings.http_proxy is None:
            self.global_settings.http_proxy = HTTPProxySettings()
        self.global_settings.http_proxy.no_proxy = hosts
        self._mark_modified("http_proxy", "no_proxy")
        self._save()

    def get_proxy_headers(self) -> dict[str, str] | None:
        """Return custom proxy headers (e.g., for authentication).

        Header values may be a literal, ``$ENV_VAR``, or ``!command``; each is
        resolved once and cached (see ``tau.utils.secrets``).
        """
        from tau.utils.secrets import resolve_secrets

        proxy = self.settings.http_proxy
        if not (proxy and proxy.headers):
            return None
        return resolve_secrets(proxy.headers)

    def set_proxy_headers(self, headers: dict[str, str] | None) -> None:
        """Set custom proxy headers and persist to global settings."""
        if self.global_settings.http_proxy is None:
            self.global_settings.http_proxy = HTTPProxySettings()
        self.global_settings.http_proxy.headers = headers
        self._mark_modified("http_proxy", "headers")
        self._save()

    # ── Project trust ─────────────────────────────────────────────────────────

    def is_project_trusted(self) -> bool:
        """Return True if the current project directory is trusted."""
        return self._project_trusted

    def set_project_trusted(self, trusted: bool) -> None:
        """Mark the project as trusted/untrusted and reload project settings if trust is granted."""
        if self._project_trusted == trusted:
            return
        self._project_trusted = trusted
        if trusted:
            # Re-load project settings now that trust is granted
            project_settings, project_error, project_issues = (
                SettingsManager._try_load_from_storage(self.storage, SCOPE.PROJECT)
            )
            self.project_settings = project_settings
            self.project_settings_recovered_issues = project_issues
            if project_error:
                err = SettingsError(scope=SCOPE.PROJECT, error=project_error)
                if not any(e.scope == SCOPE.PROJECT for e in self.errors):
                    self.errors.append(err)
                self.project_settings_load_error = project_error
            else:
                self.project_settings_load_error = None
        else:
            self.project_settings = Settings()
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)

    def get_project_trust(self) -> str:
        """Return the global project trust policy: 'ask' | 'always' | 'never'."""
        v = self.global_settings.project_trust
        return v if v is not None else "ask"

    def set_project_trust(self, value: str) -> None:
        valid = ("ask", "always", "never")
        if value not in valid:
            raise ValueError(f"project_trust must be one of {valid}, got {value!r}")
        self.global_settings.project_trust = value  # type: ignore[assignment]
        self._mark_modified("project_trust")
        self._save()

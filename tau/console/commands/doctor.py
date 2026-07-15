from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import click

Status = Literal["pass", "warn", "fail"]

_SYMBOL: dict[Status, str] = {"pass": "✓", "warn": "⚠", "fail": "✗"}
_COLOR: dict[Status, str] = {"pass": "green", "warn": "yellow", "fail": "red"}

# Per-modality slot -> which ProviderRegistry sub-registry resolves its provider id.
_MODALITIES: tuple[str, ...] = ("text", "voice", "speak", "image", "video")


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    fixed: bool = False


@dataclass
class Section:
    title: str
    results: list[CheckResult] = field(default_factory=list)


@click.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="Output machine-readable JSON.")
@click.option(
    "--fix",
    "fix",
    is_flag=True,
    help="Apply safe, reversible repairs (refresh expired tokens, remove dangling "
    "extension entries, quarantine corrupt session files). Never touches settings "
    "files directly or reinstalls packages.",
)
def doctor(as_json: bool, fix: bool) -> None:
    """Diagnose Tau's configuration, credentials, and model setup."""
    import asyncio

    sections = asyncio.run(_run_checks(fix=fix))

    if as_json:
        _print_json(sections)
    else:
        _print_report(sections, fix=fix)

    if any(r.status == "fail" for s in sections for r in s.results):
        raise SystemExit(1)


async def _run_checks(fix: bool = False) -> list[Section]:
    from tau.auth.manager import AuthManager
    from tau.inference.provider.registry import ProviderRegistry
    from tau.settings.manager import SettingsManager

    cwd = Path.cwd()
    provider_registry = ProviderRegistry.from_builtins()
    settings_manager = SettingsManager.create(cwd)
    auth_manager = AuthManager.create(provider_registry)

    settings_section = _check_settings(settings_manager)
    models_section, referenced_providers = _check_models(settings_manager, provider_registry)
    auth_section = await _check_auth(provider_registry, auth_manager, referenced_providers, fix=fix)
    extensions_section = _check_extensions(settings_manager, cwd, fix=fix)
    sessions_section = _check_sessions(fix=fix)
    logs_section = _check_logs()
    environment_section = _check_environment(cwd)
    packages_section = _check_packages(settings_manager, cwd)

    # Extension-entry fixes enqueue an async settings write (see SettingsManager._save);
    # flush it so the repair is durable before the process exits.
    await settings_manager.flush()

    return [
        settings_section,
        auth_section,
        models_section,
        extensions_section,
        sessions_section,
        logs_section,
        environment_section,
        packages_section,
    ]


# ---------------------------------------------------------------------------
# 1. Settings
# ---------------------------------------------------------------------------


def _check_settings(sm: object) -> Section:
    results: list[CheckResult] = []

    global_error = getattr(sm, "global_settings_load_error", None)
    results.append(
        CheckResult("Global settings (~/.tau/settings.json)", "fail", str(global_error))
        if global_error is not None
        else CheckResult("Global settings (~/.tau/settings.json)", "pass")
    )

    project_error = getattr(sm, "project_settings_load_error", None)
    results.append(
        CheckResult("Project settings (.tau/settings.json)", "fail", str(project_error))
        if project_error is not None
        else CheckResult("Project settings (.tau/settings.json)", "pass")
    )

    return Section("Settings", results)


# ---------------------------------------------------------------------------
# 2. Auth / credentials
# ---------------------------------------------------------------------------


async def _check_auth(
    provider_registry, auth_manager, referenced_providers: set[str], fix: bool = False
) -> Section:
    from tau.auth.types import OAuthCredential
    from tau.inference.provider.types import OAuthProvider

    results: list[CheckResult] = []

    load_errors = auth_manager.drain_errors()
    if load_errors:
        results.append(
            CheckResult("Credentials store (~/.tau/auth.json)", "fail", str(load_errors[0]))
        )
    else:
        results.append(CheckResult("Credentials store (~/.tau/auth.json)", "pass"))

    # Only report on providers the user has actually touched — a stored
    # credential, or a provider referenced by a configured model — rather
    # than every builtin provider (most of which nobody uses).
    provider_ids = set(auth_manager.list()) | referenced_providers
    for provider_id in sorted(provider_ids):
        provider = provider_registry.text.get(provider_id)
        if provider is None:
            results.append(
                CheckResult(f"{provider_id}", "warn", "referenced but not a known provider")
            )
            continue

        ptype = "oauth" if isinstance(provider, OAuthProvider) else "api_key"
        label = f"{provider_id} ({ptype})"
        status = auth_manager.get_auth_status(provider_id)

        if not status.configured:
            results.append(CheckResult(label, "warn", "not configured"))
            continue

        if isinstance(provider, OAuthProvider) and status.source == "stored":
            credential = auth_manager.get(provider_id)
            if isinstance(credential, OAuthCredential):
                try:
                    valid = await provider.validate(credential)
                except Exception as exc:  # noqa: BLE001 — surface as a check result, not a crash
                    results.append(CheckResult(label, "warn", f"could not validate: {exc}"))
                    continue
                if valid:
                    results.append(CheckResult(label, "pass", "token valid"))
                    continue

                if fix:
                    try:
                        refreshed = await auth_manager.force_refresh(
                            provider_id, stale_access=credential.access
                        )
                    except Exception as exc:  # noqa: BLE001 — report, don't crash the run
                        refreshed = None
                        results.append(CheckResult(label, "warn", f"refresh attempt failed: {exc}"))
                        continue
                    if refreshed is not None:
                        results.append(
                            CheckResult(label, "pass", "fixed: refreshed expired token", fixed=True)
                        )
                    else:
                        results.append(
                            CheckResult(
                                label,
                                "warn",
                                f"refresh failed (refresh token likely dead) — "
                                f"run `tau auth login {provider_id}`",
                            )
                        )
                    continue

                results.append(
                    CheckResult(
                        label,
                        "warn",
                        f"stored token invalid — run `tau auth login {provider_id}` "
                        "(or `tau doctor --fix` to attempt a refresh)",
                    )
                )
                continue

        results.append(CheckResult(label, "pass", f"configured (source: {status.source})"))

    return Section("Auth", results)


# ---------------------------------------------------------------------------
# 3. Model resolution
# ---------------------------------------------------------------------------


def _sub_registry(provider_registry, modality: str):
    if modality in ("voice", "speak"):
        return provider_registry.audio
    return getattr(provider_registry, modality, provider_registry.text)


def _check_models(sm, provider_registry, model_registry=None) -> tuple[Section, set[str]]:
    if model_registry is None:
        from tau.inference.model.registry import ModelRegistry

        model_registry = ModelRegistry.from_all_builtins()
    results: list[CheckResult] = []
    referenced_providers: set[str] = set()

    for modality in _MODALITIES:
        ref = sm.get_model_ref(modality)
        if ref is None or not ref.id:
            continue

        label = f"{modality} model ({ref.provider or '?'}/{ref.id})"
        model = model_registry.get(ref.id, ref.provider)

        if model is None:
            other_variants = {m.provider for m in model_registry.list() if m.id == ref.id}
            if other_variants:
                results.append(
                    CheckResult(
                        label,
                        "fail",
                        f"model '{ref.id}' exists but not for provider '{ref.provider}' "
                        f"(available: {', '.join(sorted(other_variants))})",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        label,
                        "warn",
                        f"model '{ref.id}' not in the builtin catalog"
                        " (may be a custom/package model)",
                    )
                )
            continue

        provider_id = ref.provider or model.provider
        referenced_providers.add(provider_id)
        sub_registry = _sub_registry(provider_registry, modality)
        if sub_registry.get(provider_id) is None:
            results.append(CheckResult(label, "fail", f"provider '{provider_id}' not registered"))
            continue

        results.append(CheckResult(label, "pass"))

    if not results:
        results.append(
            CheckResult("Model selection", "warn", "no models configured — defaults will be used")
        )

    return Section("Models", results), referenced_providers


# ---------------------------------------------------------------------------
# 4. Extensions
# ---------------------------------------------------------------------------

# Static checks only — this deliberately does not run ExtensionLoader.load(),
# which executes extension code and can install dependencies. A diagnostic
# command shouldn't have side effects like that; it only checks that what's
# configured/discoverable is well-formed.


def _check_manifest(path: Path) -> CheckResult | None:
    """Validate one manifest.json's shape; return None if there's nothing to report."""
    from tau.settings.paths import get_app_name

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckResult(f"manifest: {path}", "fail", f"invalid JSON: {exc}")
    if not isinstance(data, dict) or get_app_name().lower() not in data:
        return CheckResult(
            f"manifest: {path}", "warn", f"missing top-level '{get_app_name().lower()}' key"
        )
    return None


def _resolve_entry_path(entry, cwd: Path) -> Path:
    path = Path(entry.path).expanduser()
    return path if path.is_absolute() else (cwd / path).resolve()


def _check_dangling_entries(sm, cwd: Path, fix: bool) -> list[CheckResult]:
    """Check configured extension entries in both scopes; remove dangling ones if fix=True.

    The removal itself (shared with automatic startup/reload housekeeping) lives in
    SettingsManager.prune_dangling_extensions — this just reports what it did, or
    what it *would* do when running without --fix.
    """
    results: list[CheckResult] = []

    if fix:
        for scope_name, entry in sm.prune_dangling_extensions(cwd):
            label = entry.name or entry.path
            path = _resolve_entry_path(entry, cwd)
            results.append(
                CheckResult(
                    label,
                    "pass",
                    f"fixed: removed dangling {scope_name} extension entry "
                    f"(path not found: {path})",
                    fixed=True,
                )
            )
        return results

    for get_settings in (sm.get_global_settings, sm.get_project_settings):
        ext = get_settings().extensions
        entries = list(ext.list) if ext and ext.list else []
        for entry in entries:
            if not entry.enabled or _resolve_entry_path(entry, cwd).exists():
                continue
            results.append(
                CheckResult(
                    entry.name or entry.path, "fail", f"path not found: {_resolve_entry_path(entry, cwd)}"
                )
            )
    return results


def _check_manifest_declarations(manifest: Path, subdir: Path, cwd: Path, source: str) -> list[CheckResult]:
    """Check one extension's manifest-declared entry files, skill dirs, and
    dependency install state. Static only — mirrors the resolution rules
    ExtensionLoader itself uses (see tau/extensions/loader.py) without
    importing the extension or running an installer.
    """
    from tau.extensions.loader import dependency_digest, resolve_extension_venv_dir
    from tau.settings.paths import get_app_name

    results: list[CheckResult] = []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return results  # invalid JSON already reported by _check_manifest
    if not isinstance(data, dict):
        return results
    app_data = data.get(get_app_name().lower(), {})
    label = f"{subdir.name} ({source})"

    for rel in app_data.get("extensions", []) or []:
        if not (subdir / rel).resolve().is_file():
            results.append(CheckResult(label, "fail", f"declared extension entry not found: {rel}"))

    for rel in app_data.get("skills", []) or []:
        if not (subdir / rel).resolve().is_dir():
            results.append(CheckResult(label, "warn", f"declared skill path not found: {rel}"))

    deps = app_data.get("dependencies", []) or []
    if deps:
        venv_dir = resolve_extension_venv_dir(cwd, source)
        cache_file = venv_dir / ".tau_ext_deps.json"
        cache: dict = {}
        if cache_file.is_file():
            try:
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cache = {}
        cache_entry = cache.get(str(subdir.resolve()))
        cached_digest = cache_entry.get("digest") if isinstance(cache_entry, dict) else cache_entry
        if cached_digest != dependency_digest(deps):
            results.append(
                CheckResult(
                    f"{label} dependencies",
                    "warn",
                    "not yet installed for the current dependency list — will install on next load",
                )
            )
        elif isinstance(cache_entry, dict) and not cache_entry.get("ok", True):
            results.append(
                CheckResult(
                    f"{label} dependencies",
                    "fail",
                    f"previously failed to install: {cache_entry.get('error', 'unknown error')}",
                )
            )

    return results


def _check_extensions(sm, cwd: Path, fix: bool = False) -> Section:
    from tau.settings.paths import get_extensions_dir

    results: list[CheckResult] = []

    if not sm.is_extensions_enabled():
        results.append(CheckResult("Extensions", "pass", "disabled globally"))
        return Section("Extensions", results)

    results.extend(_check_dangling_entries(sm, cwd, fix))

    for source, extensions_dir in (("project", get_extensions_dir(cwd)), ("global", get_extensions_dir())):
        if not extensions_dir.is_dir():
            continue
        for subdir in sorted(extensions_dir.iterdir(), key=lambda e: e.name):
            if subdir.name.startswith("_") or not subdir.is_dir():
                continue
            manifest = subdir / "manifest.json"
            if not manifest.is_file():
                if not (subdir / "__init__.py").is_file():
                    results.append(
                        CheckResult(
                            f"{subdir.name} ({source})",
                            "warn",
                            "no manifest.json or __init__.py — not discoverable as an extension",
                        )
                    )
                continue
            if result := _check_manifest(manifest):
                results.append(result)
                continue  # invalid/unexpected shape — skip declaration checks below
            results.extend(_check_manifest_declarations(manifest, subdir, cwd, source))

    if not results:
        results.append(CheckResult("Extensions", "pass", "no issues found"))

    return Section("Extensions", results)


# ---------------------------------------------------------------------------
# 5. Session storage
# ---------------------------------------------------------------------------

_MAX_LISTED_BAD_SESSIONS = 5


_QUARANTINE_DIRNAME = ".corrupt"


def _quarantine_session_file(f: Path, sessions_dir: Path) -> Path:
    """Move a corrupt session file into a .corrupt/ subdir, never overwriting an existing one."""
    quarantine_dir = sessions_dir / _QUARANTINE_DIRNAME
    quarantine_dir.mkdir(exist_ok=True)
    dest = quarantine_dir / f.name
    suffix = 1
    while dest.exists():
        dest = quarantine_dir / f"{f.stem}.{suffix}{f.suffix}"
        suffix += 1
    f.rename(dest)
    return dest


def _check_sessions(fix: bool = False) -> Section:
    from tau.session.utils import build_session_info
    from tau.settings.paths import get_sessions_dir

    results: list[CheckResult] = []
    sessions_dir = get_sessions_dir()

    if not sessions_dir.is_dir():
        results.append(CheckResult("Session storage (~/.tau/sessions/)", "pass", "no sessions yet"))
        return Section("Sessions", results)

    jsonl_files = [f for f in sessions_dir.rglob("*.jsonl") if _QUARANTINE_DIRNAME not in f.parts]
    bad = [f for f in jsonl_files if build_session_info(f) is None]

    if not jsonl_files:
        results.append(CheckResult("Session storage (~/.tau/sessions/)", "pass", "no sessions yet"))
    elif not bad:
        results.append(
            CheckResult(
                "Session storage (~/.tau/sessions/)",
                "pass",
                f"{len(jsonl_files)} session file(s), all readable",
            )
        )
    elif fix:
        for f in bad:
            dest = _quarantine_session_file(f, sessions_dir)
            results.append(
                CheckResult(
                    f"Session file {f.name}",
                    "pass",
                    f"fixed: moved to {dest.relative_to(sessions_dir)}",
                    fixed=True,
                )
            )
    else:
        for f in bad[:_MAX_LISTED_BAD_SESSIONS]:
            results.append(
                CheckResult(
                    f"Session file {f.name}",
                    "warn",
                    "corrupt or missing header — unreadable, hidden from the session picker "
                    "(run `tau doctor --fix` to quarantine it)",
                )
            )
        if len(bad) > _MAX_LISTED_BAD_SESSIONS:
            results.append(
                CheckResult(
                    "...",
                    "warn",
                    f"and {len(bad) - _MAX_LISTED_BAD_SESSIONS} more corrupt session file(s)",
                )
            )

    return Section("Sessions", results)


# ---------------------------------------------------------------------------
# 6. Logs
# ---------------------------------------------------------------------------

_LOG_RECENT_WINDOW_S = 7 * 24 * 3600  # only scan logs touched in the last week
_MAX_SCANNED_LOGS = 20
_MAX_LISTED_BAD_LOGS = 5


def _check_logs() -> Section:
    import time

    from tau.settings.paths import get_logs_dir

    results: list[CheckResult] = []
    logs_dir = get_logs_dir()

    if not logs_dir.is_dir():
        results.append(CheckResult("Logs (~/.tau/logs/)", "pass", "no logs yet"))
        return Section("Logs", results)

    now = time.time()
    candidates = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidates = [p for p in candidates if now - p.stat().st_mtime <= _LOG_RECENT_WINDOW_S]
    candidates = candidates[:_MAX_SCANNED_LOGS]

    flagged: list[Path] = []
    for log_file in candidates:
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Traceback (most recent call last)" in text:
            flagged.append(log_file)

    if not flagged:
        results.append(CheckResult("Logs (~/.tau/logs/)", "pass", "no recent tracebacks"))
    else:
        for log_file in flagged[:_MAX_LISTED_BAD_LOGS]:
            results.append(CheckResult(f"Log {log_file.name}", "warn", "contains a traceback"))
        if len(flagged) > _MAX_LISTED_BAD_LOGS:
            results.append(
                CheckResult(
                    "...",
                    "warn",
                    f"and {len(flagged) - _MAX_LISTED_BAD_LOGS} more log(s) with tracebacks",
                )
            )

    return Section("Logs", results)


# ---------------------------------------------------------------------------
# 7. Environment
# ---------------------------------------------------------------------------

_MIN_PYTHON = (3, 12)  # matches pyproject.toml's requires-python


def _check_environment(cwd: Path) -> Section:
    import sys

    results: list[CheckResult] = []

    py_version = sys.version_info[:2]
    if py_version < _MIN_PYTHON:
        results.append(
            CheckResult(
                "Python version",
                "fail",
                f"{'.'.join(map(str, py_version))} is below the minimum "
                f"{'.'.join(map(str, _MIN_PYTHON))}",
            )
        )
    else:
        results.append(
            CheckResult(
                "Python version", "pass", f"{sys.version_info.major}.{sys.version_info.minor}"
            )
        )

    from tau.settings.paths import get_packages_venv

    venv_dir = get_packages_venv()
    if venv_dir.is_dir():
        from tau.extensions.loader import _venv_matches_current

        if _venv_matches_current(venv_dir):
            results.append(CheckResult("Packages venv (~/.tau/venv)", "pass"))
        else:
            results.append(
                CheckResult(
                    "Packages venv (~/.tau/venv)",
                    "warn",
                    "was built for a different Python version than the one currently running",
                )
            )

    from tau.agent.prompt.builder import _find_git_root

    git_root = _find_git_root(cwd)
    results.append(
        CheckResult(
            "Git repository", "pass", str(git_root) if git_root else "not inside a git repo"
        )
    )

    return Section("Environment", results)


# ---------------------------------------------------------------------------
# 8. Packages
# ---------------------------------------------------------------------------

# No --fix here: a stale entry's ``source`` string is the only thing that
# tells you how to reinstall it. Removing the settings entry would destroy
# that, and reinstalling automatically means running a network install
# without asking — neither is a "safe, reversible" repair in doctor's sense.


def _check_packages(sm, cwd: Path) -> Section:
    from tau.packages.manager import PackageManager
    from tau.settings.paths import get_packages_venv

    results: list[CheckResult] = []

    for local, scope in ((False, "global"), (True, "project")):
        packages = sm.get_packages(local=local)
        if not packages:
            continue
        venv_dir = get_packages_venv(cwd) if local else get_packages_venv()
        pkg_mgr = PackageManager(venv_dir)
        for pkg in packages:
            if not pkg.enabled:
                continue
            if pkg.installed_path and Path(pkg.installed_path).is_dir():
                continue
            if pkg_mgr.is_installed(pkg.name):
                continue  # importable even though the recorded path moved/vanished
            results.append(
                CheckResult(
                    f"{pkg.name} ({scope})",
                    "fail",
                    f"recorded as installed but missing from the venv ({venv_dir}) "
                    f"— run `tau install {pkg.source}` again",
                )
            )

    if not results:
        results.append(CheckResult("Packages", "pass", "no drift detected"))

    return Section("Packages", results)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_report(sections: list[Section], fix: bool = False) -> None:
    if fix:
        click.echo(click.style("Running with --fix: safe repairs will be applied.\n", fg="cyan"))

    for section in sections:
        click.echo(click.style(section.title, bold=True))
        for r in section.results:
            symbol = click.style("🔧" if r.fixed else _SYMBOL[r.status], fg=_COLOR[r.status])
            line = f"  {symbol} {r.name}"
            if r.detail:
                line += click.style(f" — {r.detail}", fg="bright_black")
            click.echo(line)
        click.echo()

    counts = {"pass": 0, "warn": 0, "fail": 0}
    fixed_count = 0
    for s in sections:
        for r in s.results:
            counts[r.status] += 1
            if r.fixed:
                fixed_count += 1
    summary = f"{counts['pass']} passed, {counts['warn']} warnings, {counts['fail']} failed"
    if fixed_count:
        summary += f", {fixed_count} fixed"
    color = "red" if counts["fail"] else ("yellow" if counts["warn"] else "green")
    click.echo(click.style(summary, fg=color, bold=True))


def _print_json(sections: list[Section]) -> None:
    payload = {
        "sections": [
            {
                "title": s.title,
                "results": [
                    {"name": r.name, "status": r.status, "detail": r.detail, "fixed": r.fixed}
                    for r in s.results
                ],
            }
            for s in sections
        ]
    }
    click.echo(json.dumps(payload, indent=2))

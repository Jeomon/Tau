"""Regression test: RuntimeContext.project_trusted must track in-session grants.

The flag was assigned once in __init__ and never updated, while the interactive
trust screen grants trust by calling SettingsManager.set_project_trusted(True)
(see tau/modes/interactive/app.py). Every consumer reading
``self._context.project_trusted`` therefore kept seeing the startup value: after
accepting the trust prompt, extensions/settings/skills reloaded correctly but
context files stayed disabled until restart, because
_reload_extensions_now() derives ``load_context_files`` from that stale flag
(tau/runtime/service.py).

The property now delegates to the settings manager, which is the source of truth
once the runtime is live.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tau.runtime.types import RuntimeContext
from tau.settings.manager import SettingsManager


def _context(settings_manager: SettingsManager | None, *, project_trusted: bool) -> RuntimeContext:
    """Build a RuntimeContext with only the fields this property touches."""
    return RuntimeContext(
        agent=SimpleNamespace(hooks=None),  # type: ignore[arg-type]
        llm=SimpleNamespace(),  # type: ignore[arg-type]
        engine=SimpleNamespace(),  # type: ignore[arg-type]
        session_manager=SimpleNamespace(),  # type: ignore[arg-type]
        settings_manager=settings_manager,
        hooks=SimpleNamespace(),  # type: ignore[arg-type]
        project_trusted=project_trusted,
    )


def test_project_trusted_reflects_in_session_grant(tmp_path: Path) -> None:
    sm = SettingsManager.create(cwd=tmp_path, config_dir=tmp_path / "cfg", project_trusted=False)
    ctx = _context(sm, project_trusted=False)

    assert ctx.project_trusted is False

    # The trust screen's grant path.
    sm.set_project_trusted(True)

    assert ctx.project_trusted is True


def test_project_trusted_reflects_in_session_revocation(tmp_path: Path) -> None:
    sm = SettingsManager.create(cwd=tmp_path, config_dir=tmp_path / "cfg", project_trusted=True)
    ctx = _context(sm, project_trusted=True)

    assert ctx.project_trusted is True

    sm.set_project_trusted(False)

    assert ctx.project_trusted is False


def test_project_trusted_falls_back_without_settings_manager() -> None:
    """Contexts built without a settings manager keep the constructor value."""
    assert _context(None, project_trusted=True).project_trusted is True
    assert _context(None, project_trusted=False).project_trusted is False

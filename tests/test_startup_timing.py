from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tau.runtime.dependencies import RuntimeDependencies
from tau.runtime.types import RuntimeConfig, RuntimeContext
from tau.session.manager import SessionManager
from tau.settings.manager import SettingsManager
from tau.utils import timing


class _Options:
    timeout = None
    max_retries = 0
    retry_base_delay_ms = 0


class _LLM:
    def __init__(self) -> None:
        self.model = SimpleNamespace(thinking=False, input_limit=100_000)
        self.api = SimpleNamespace(options=_Options())


def _config(tmp_path: Path) -> RuntimeConfig:
    def settings_factory(context: Any) -> SettingsManager:
        return SettingsManager.create(
            context.cwd, config_dir=context.config_dir, project_trusted=context.project_trusted
        )

    def llm_factory(_context: Any) -> _LLM:
        return _LLM()

    def session_factory(context: Any) -> SessionManager:
        return SessionManager(
            cwd=context.cwd,
            session_dir=context.session_dir,
            session_file=context.session_file,
            persist=context.persist,
        )

    return RuntimeConfig(
        cwd=tmp_path,
        config_dir=tmp_path / "config",
        persist_session=False,
        project_trusted=True,
        dependencies=RuntimeDependencies(
            settings=settings_factory,
            llm=llm_factory,  # type: ignore[arg-type]
            session_manager=session_factory,
        ),
    )


def teardown_function() -> None:
    # Don't leak timing state (enabled or otherwise) into unrelated tests.
    timing._enabled = False
    timing._marks = []


def test_mark_is_a_no_op_when_not_enabled(tmp_path: Path) -> None:
    asyncio.run(RuntimeContext.create(_config(tmp_path)))
    assert timing.report() == []


def test_startup_records_marks_in_order(tmp_path: Path) -> None:
    timing.enable()

    asyncio.run(RuntimeContext.create(_config(tmp_path)))

    labels = [label for label, _t in timing.report()]
    assert labels == ["settings", "llm", "session_manager", "resources", "extensions", "agent"]
    # Each mark's elapsed time is monotonically non-decreasing from enable().
    times = [t for _label, t in timing.report()]
    assert times == sorted(times)


def test_print_report_writes_each_mark(capsys) -> None:
    timing.enable()
    timing.mark("phase-a")
    timing.mark("phase-b")

    timing.print_report()

    err = capsys.readouterr().err
    assert "Startup Timings" in err
    assert "phase-a" in err
    assert "phase-b" in err


def test_print_report_silent_when_disabled(capsys) -> None:
    timing.print_report()
    assert capsys.readouterr().err == ""

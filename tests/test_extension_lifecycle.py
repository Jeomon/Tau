from __future__ import annotations

import asyncio
import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.extensions.api import LoadExtensionsResult, _RuntimeRef
from tau.extensions.context import ExtensionContext, StaleExtensionContextError
from tau.extensions.loader import ExtensionLoader
from tau.extensions.runtime import ExtensionRuntime
from tau.hooks.runtime import RuntimeReadyEvent
from tau.hooks.service import Hooks
from tau.runtime.service import Runtime
from tau.settings.types import ExtensionEntry


def _coordinator_runtime() -> tuple[Runtime, list[str]]:
    runtime = object.__new__(Runtime)
    calls: list[str] = []

    async def reload_now() -> LoadExtensionsResult:
        calls.append("reload")
        return LoadExtensionsResult()

    runtime._context = SimpleNamespace(agent=None)
    runtime._extension_callback_depth = 0
    runtime._extension_callbacks_idle = asyncio.Event()
    runtime._extension_callbacks_idle.set()
    runtime._reload_lock = asyncio.Lock()
    runtime._reload_pending = False
    runtime._reload_task = None
    runtime._stopped = False
    runtime._reload_extensions_now = reload_now
    return runtime, calls


def test_reload_from_extension_callback_is_deferred_and_coalesced() -> None:
    async def run() -> None:
        runtime, calls = _coordinator_runtime()
        runtime._begin_extension_callback()

        await runtime.reload_extensions()
        await runtime.reload_extensions()
        assert calls == []

        runtime._end_extension_callback()
        task = runtime._reload_task
        assert task is not None
        await task

        assert calls == ["reload"]

    asyncio.run(run())


def test_extension_runtime_tracks_callback_boundaries(tmp_path: Path) -> None:
    async def run() -> None:
        depth = 0
        observed: list[int] = []

        class _Runtime:
            session_manager = SimpleNamespace(cwd=tmp_path)
            settings_manager = None
            agent = None
            _layout = None

            def _begin_extension_callback(self) -> None:
                nonlocal depth
                depth += 1

            def _end_extension_callback(self) -> None:
                nonlocal depth
                depth -= 1

        def factory(tau) -> None:
            @tau.on("runtime_ready")
            def on_ready(_event: object, _context: object) -> None:
                observed.append(depth)

        from tau.extensions.loader import load_inline_extensions

        runtime_ref = _RuntimeRef()
        runtime_ref.runtime = _Runtime()
        result = await load_inline_extensions(
            [factory],
            llm=SimpleNamespace(),  # type: ignore[arg-type]
            settings=SimpleNamespace(),  # type: ignore[arg-type]
            cwd=tmp_path,
            runtime_ref=runtime_ref,
        )
        hooks = Hooks()
        extension_runtime = ExtensionRuntime(result, hooks, runtime_ref)

        await hooks.emit(RuntimeReadyEvent())
        extension_runtime.unsubscribe()

        assert observed == [1]
        assert depth == 0

    asyncio.run(run())


def test_reload_waits_for_busy_agent_to_become_idle() -> None:
    async def run() -> None:
        runtime, calls = _coordinator_runtime()

        class _Agent:
            def __init__(self) -> None:
                self.idle = False
                self.event = asyncio.Event()

            def is_idle(self) -> bool:
                return self.idle

            async def wait_for_idle(self) -> None:
                await self.event.wait()

        agent = _Agent()
        runtime._context.agent = agent
        await runtime.reload_extensions()
        assert calls == []

        agent.idle = True
        agent.event.set()
        task = runtime._reload_task
        assert task is not None
        await task

        assert calls == ["reload"]

    asyncio.run(run())


def test_captured_extension_context_is_rejected_after_generation_change(
    tmp_path: Path,
) -> None:
    runtime = SimpleNamespace(extension_generation=3)
    context = ExtensionContext(
        cwd=tmp_path,
        settings=None,
        model_id="model",
        provider_id="provider",
        runtime=runtime,  # type: ignore[arg-type]
    )
    assert context.cwd == tmp_path

    runtime.extension_generation += 1

    with pytest.raises(StaleExtensionContextError, match="stale"):
        _ = context.cwd
    with pytest.raises(StaleExtensionContextError, match="stale"):
        context.is_idle()


def test_file_reload_reexecutes_entry_but_not_imported_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependency = tmp_path / "reload_dependency.py"
    dependency.write_text(
        "import builtins\n"
        "builtins._tau_dependency_loads = "
        "getattr(builtins, '_tau_dependency_loads', 0) + 1\n"
    )
    extension = tmp_path / "extension.py"
    extension.write_text(
        "import builtins\n"
        "import reload_dependency\n"
        "builtins._tau_entry_loads = getattr(builtins, '_tau_entry_loads', 0) + 1\n"
        "def register(tau):\n"
        "    pass\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(builtins, "_tau_dependency_loads", 0, raising=False)
    monkeypatch.setattr(builtins, "_tau_entry_loads", 0, raising=False)
    sys.modules.pop("reload_dependency", None)

    loader = ExtensionLoader(
        extra_entries=[ExtensionEntry(path=str(extension))],
        llm=SimpleNamespace(),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        cwd=tmp_path,
        runtime_ref=_RuntimeRef(),
    )
    asyncio.run(loader.load())
    asyncio.run(loader.load())

    assert builtins._tau_entry_loads == 2  # type: ignore[attr-defined]
    assert builtins._tau_dependency_loads == 1  # type: ignore[attr-defined]
    sys.modules.pop("reload_dependency", None)


def test_shutdown_invalidates_context_and_unsubscribes_extensions(tmp_path: Path) -> None:
    async def run() -> None:
        events: list[str] = []
        unsubscribed = False

        class _Hooks:
            async def emit(self, event: object, *, timeout: float | None = None) -> list[object]:
                events.append(event.type)  # type: ignore[attr-defined]
                return []

        class _Extensions:
            def unsubscribe(self) -> None:
                nonlocal unsubscribed
                unsubscribed = True

        runtime = object.__new__(Runtime)
        runtime._context = SimpleNamespace(hooks=_Hooks(), ext_runtime=_Extensions())
        runtime._stopped = False
        runtime._extension_generation = 2
        runtime._reload_task = None
        runtime._reload_pending = False
        runtime.version_check_task = None
        context = ExtensionContext(
            cwd=tmp_path,
            settings=None,
            model_id="model",
            provider_id="provider",
            runtime=runtime,
        )

        await runtime.ashutdown()

        assert events == ["runtime_stop"]
        assert unsubscribed
        with pytest.raises(StaleExtensionContextError):
            _ = context.model_id

    asyncio.run(run())


def test_load_one_execs_extension_module_off_the_main_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """exec_module() runs an extension's top-level code — the first time any
    given third-party dependency is imported in this process, that pays the
    full import cost synchronously (measured: ~200ms for a builtin extension
    pulling in an HTTP client + search libs). Must run off the event loop
    thread so a fresh process start doesn't freeze the whole TUI for that span.
    """
    import threading

    extension = tmp_path / "thread_probe_extension.py"
    extension.write_text(
        "import builtins\n"
        "import threading\n"
        "builtins._tau_load_thread_name = threading.current_thread().name\n"
        "def register(tau):\n"
        "    pass\n"
    )
    monkeypatch.setattr(builtins, "_tau_load_thread_name", None, raising=False)

    loader = ExtensionLoader(
        extra_entries=[ExtensionEntry(path=str(extension))],
        llm=SimpleNamespace(),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        cwd=tmp_path,
        runtime_ref=_RuntimeRef(),
    )
    result = asyncio.run(loader.load())

    assert not result.errors
    assert builtins._tau_load_thread_name != threading.main_thread().name  # type: ignore[attr-defined]


# ── Late-registered tools across /reload ─────────────────────────────────────


class _FakeTool:
    def __init__(self, name: str, renderer: str = "") -> None:
        self.name = name
        self.render_result = renderer


def _ext(path: str, tools: dict) -> SimpleNamespace:
    return SimpleNamespace(path=path, tools=tools)


def _ext_runtime(extensions: list) -> SimpleNamespace:
    return SimpleNamespace(
        get_extensions=lambda: tuple(extensions),
        get_tools=lambda: [t for ext in extensions for t in ext.tools.values()],
    )


def test_reload_keeps_a_tool_registered_after_load() -> None:
    """A tool registered from a session_start handler lives on the extension
    object, and a reload builds a new one by calling register() again — nothing
    re-runs that handler, so replace_source used to drop the tool and its
    renderer with it."""
    old = {"a/ext.py": [_FakeTool("from_register"), _FakeTool("late", renderer="custom")]}
    new_ext = _ext_runtime([_ext("a/ext.py", {"from_register": _FakeTool("from_register")})])

    carried = Runtime._carry_over_late_tools(old, new_ext)

    assert [tool.name for tool in carried] == ["late"]
    assert carried[0].render_result == "custom"


def test_reload_drops_tools_of_an_extension_that_is_gone() -> None:
    """Disabling or deleting an extension must still remove its tools."""
    old = {"a/gone.py": [_FakeTool("orphan")]}
    new_ext = _ext_runtime([_ext("a/other.py", {})])

    assert Runtime._carry_over_late_tools(old, new_ext) == []


def test_a_re_registered_tool_wins_over_the_carried_copy() -> None:
    """The fresh load's own tool is authoritative; the stale one is not kept
    alongside it, or replace_source would see the name twice."""
    old = {"a/ext.py": [_FakeTool("shared", renderer="stale")]}
    new_ext = _ext_runtime([_ext("a/ext.py", {"shared": _FakeTool("shared", renderer="fresh")})])

    assert Runtime._carry_over_late_tools(old, new_ext) == []

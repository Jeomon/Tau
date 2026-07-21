"""External editor (ctrl+g): command resolution, TUI suspend/resume, round-trip."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from tau.settings.manager import SettingsManager
from tau.tui.input import KeyEvent, get_keybindings

# ── Command resolution ───────────────────────────────────────────────────────


def _manager(external_editor=None) -> SettingsManager:
    seed = {"external_editor": external_editor} if external_editor is not None else {}
    return SettingsManager.in_memory(seed)


class TestCommandResolution:
    def test_setting_wins_over_the_environment(self, monkeypatch):
        monkeypatch.setenv("VISUAL", "vim")
        monkeypatch.setenv("EDITOR", "emacs")

        assert _manager("code --wait").get_external_editor_command() == "code --wait"

    def test_visual_wins_over_editor(self, monkeypatch):
        monkeypatch.setenv("VISUAL", "vim")
        monkeypatch.setenv("EDITOR", "emacs")

        assert _manager().get_external_editor_command() == "vim"

    def test_editor_is_used_when_visual_is_unset(self, monkeypatch):
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", "emacs")

        assert _manager().get_external_editor_command() == "emacs"

    def test_falls_back_to_a_platform_default(self, monkeypatch):
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)

        expected = "notepad" if sys.platform == "win32" else "nano"
        assert _manager().get_external_editor_command() == expected

    def test_blank_values_are_ignored(self, monkeypatch):
        monkeypatch.setenv("VISUAL", "   ")
        monkeypatch.setenv("EDITOR", "emacs")

        assert _manager("  ").get_external_editor_command() == "emacs"


# ── Keybinding ───────────────────────────────────────────────────────────────


class TestKeybinding:
    def test_ctrl_g_is_bound(self):
        event = KeyEvent(key="g", char=None, ctrl=True)
        assert get_keybindings().matches(event, "app.editor.external")

    def test_other_keys_are_not(self):
        event = KeyEvent(key="o", char=None, ctrl=True)
        assert not get_keybindings().matches(event, "app.editor.external")


# ── Suspend / resume ─────────────────────────────────────────────────────────


class _FakeTerminal:
    """Records the terminal calls a suspend/resume cycle makes, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def _record(*_args, **_kwargs):
            self.calls.append(name)

        return _record


class _FakeStdin:
    """pytest's captured stdin has no fileno(); the reader only needs a number."""

    def fileno(self) -> int:
        return 0


def _tui_stub(terminal):
    from tau.tui.service import TUI

    tui = TUI.__new__(TUI)
    tui._terminal = terminal
    tui.terminal_bg = None
    tui._stdin_generation = 0
    tui._stdin_shutdown = asyncio.Event()
    tui._stdin_thread = None
    tui._renderer = SimpleNamespace(
        reset=lambda: terminal.calls.append("renderer.reset"),
        reset_with_clear=lambda: terminal.calls.append("renderer.reset_with_clear"),
    )
    tui._request_render = lambda force=False: terminal.calls.append("request_render")
    tui._on_stdin_ready = lambda: None
    return tui


class TestSuspend:
    @pytest.mark.asyncio
    async def test_releases_then_reclaims_the_terminal(self, monkeypatch):
        terminal = _FakeTerminal()
        tui = _tui_stub(terminal)
        monkeypatch.setattr(sys, "stdin", _FakeStdin())
        monkeypatch.setattr(asyncio.get_event_loop(), "add_reader", lambda *a: None)
        monkeypatch.setattr(asyncio.get_event_loop(), "remove_reader", lambda *a: None)

        async with tui.suspended():
            during = list(terminal.calls)

        # Released before the child runs: raw mode off, cursor visible again.
        assert "exit_raw_mode" in during
        assert "show_cursor" in during
        assert "disable_bracketed_paste" in during
        assert "disable_focus_reporting" in during
        assert "disable_kitty_keyboard" in during
        assert "enable_autowrap" in during
        assert "enter_raw_mode" not in during  # not yet reclaimed

        after = terminal.calls[len(during) :]
        assert "enter_raw_mode" in after
        assert "hide_cursor" in after
        assert "enable_bracketed_paste" in after
        assert "enable_focus_reporting" in after
        assert "enable_kitty_keyboard" in after

    @pytest.mark.asyncio
    async def test_forces_a_clear_and_redraw_on_resume(self, monkeypatch):
        terminal = _FakeTerminal()
        tui = _tui_stub(terminal)
        monkeypatch.setattr(sys, "stdin", _FakeStdin())
        monkeypatch.setattr(asyncio.get_event_loop(), "add_reader", lambda *a: None)
        monkeypatch.setattr(asyncio.get_event_loop(), "remove_reader", lambda *a: None)

        async with tui.suspended():
            pass

        # The child may have used the alternate screen, so a diff is not enough.
        assert "renderer.reset_with_clear" in terminal.calls
        assert terminal.calls[-1] == "request_render"

    @pytest.mark.asyncio
    async def test_the_terminal_is_restored_even_if_the_child_raises(self, monkeypatch):
        terminal = _FakeTerminal()
        tui = _tui_stub(terminal)
        monkeypatch.setattr(sys, "stdin", _FakeStdin())
        monkeypatch.setattr(asyncio.get_event_loop(), "add_reader", lambda *a: None)
        monkeypatch.setattr(asyncio.get_event_loop(), "remove_reader", lambda *a: None)

        with pytest.raises(RuntimeError):
            async with tui.suspended():
                raise RuntimeError("editor blew up")

        # Raw mode restored — otherwise the terminal is left unusable.
        assert "enter_raw_mode" in terminal.calls


# ── Round-trip through a real child process ──────────────────────────────────


class _Layout:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_editor_text(self) -> str:
        return self.text

    def set_editor_text(self, text: str) -> None:
        self.text = text


class _NoopTUI:
    """Stands in for the TUI: suspension is exercised separately above."""

    def __init__(self) -> None:
        self.renders = 0

    def suspended(self):
        import contextlib

        @contextlib.asynccontextmanager
        async def _cm():
            yield

        return _cm()

    def request_render(self) -> None:
        self.renders += 1


def _app(editor_command: str, text: str = "original"):
    """A minimally-wired InteractiveApp with just what the handler touches."""
    from tau.modes.interactive.app import App

    app = App.__new__(App)
    layout = _Layout(text)
    notes: list[str] = []
    settings = SimpleNamespace(get_external_editor_command=lambda: editor_command)
    app._runtime = SimpleNamespace(settings_manager=settings)
    app._layout = layout
    app._tui = _NoopTUI()
    app._ctx = lambda: SimpleNamespace(notify=notes.append)
    return app, layout, notes


def _writer_script(body: str) -> str:
    """An "editor" that is really a python one-liner over the temp file."""
    return f"{sys.executable} -c {body!r}"


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_a_clean_exit_replaces_the_prompt(self):
        editor = _writer_script(
            "import sys; open(sys.argv[1], 'w').write('edited in vim\\n')"
        )
        app, layout, notes = _app(editor)

        await app._open_external_editor()

        assert layout.text == "edited in vim"  # one trailing newline stripped
        assert notes == []

    @pytest.mark.asyncio
    async def test_the_editor_sees_the_current_prompt(self):
        editor = _writer_script(
            "import sys; p = sys.argv[1]; t = open(p).read(); open(p, 'w').write(t.upper())"
        )
        app, layout, _ = _app(editor, text="make me loud")

        await app._open_external_editor()

        assert layout.text == "MAKE ME LOUD"

    @pytest.mark.asyncio
    async def test_a_nonzero_exit_leaves_the_prompt_alone(self):
        # vim's :cq — write something, then bail out.
        editor = _writer_script(
            "import sys; open(sys.argv[1], 'w').write('discard me'); sys.exit(1)"
        )
        app, layout, _ = _app(editor, text="keep me")

        await app._open_external_editor()

        assert layout.text == "keep me"

    @pytest.mark.asyncio
    async def test_only_one_trailing_newline_is_stripped(self):
        editor = _writer_script(
            "import sys; open(sys.argv[1], 'w').write('para\\n\\nend\\n\\n')"
        )
        app, layout, _ = _app(editor)

        await app._open_external_editor()

        assert layout.text == "para\n\nend\n"

    @pytest.mark.asyncio
    async def test_a_missing_editor_warns_instead_of_raising(self):
        app, layout, notes = _app("definitely-not-an-editor-binary")

        await app._open_external_editor()

        assert layout.text == "original"
        assert notes and "Could not launch external editor" in notes[0]

    @pytest.mark.asyncio
    async def test_the_temp_file_is_always_cleaned_up(self, tmp_path, monkeypatch):
        import tempfile

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        app, _, _ = _app(_writer_script("import sys; sys.exit(1)"))

        await app._open_external_editor()

        assert list(tmp_path.glob("tau-editor-*")) == []

    @pytest.mark.asyncio
    async def test_editor_arguments_are_passed_through(self):
        # "editor --flag file" — the flag must not be swallowed into the path.
        editor = _writer_script(
            "import sys; open(sys.argv[2], 'w').write(sys.argv[1])"
        ) + " --wait"
        app, layout, _ = _app(editor)

        await app._open_external_editor()

        assert layout.text == "--wait"

    @pytest.mark.asyncio
    async def test_a_quoted_path_containing_spaces_survives(self, tmp_path):
        # shlex, not str.split — "C:\\Program Files\\..\\subl.exe --wait" must
        # not be chopped at the space inside the path.
        script = tmp_path / "my editor.py"
        script.write_text("import sys; open(sys.argv[1], 'w').write('quoted ok')")
        app, layout, notes = _app(f'{sys.executable} "{script}"')

        await app._open_external_editor()

        assert notes == []
        assert layout.text == "quoted ok"

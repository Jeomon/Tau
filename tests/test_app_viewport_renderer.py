"""AppViewportRenderer — composing and painting a frame in an owned region.

The behaviours worth pinning are the ones with user-visible consequences if
they regress: the mouse must only ever be captured between start() and stop(),
the bottom chrome (editor, status) must stay pinned to the bottom of the region,
and a frame must not depend on how long the session is.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tau.tui.app_viewport import AppViewportRenderer


class FakeTerminal:
    def __init__(self, width: int = 80, height: int = 10) -> None:
        self.width = width
        self.height = height
        self.writes: list[str] = []
        self.mouse_enabled = False
        self.resize_callbacks: list = []

    def on_resize(self, callback):
        self.resize_callbacks.append(callback)
        return lambda: self.resize_callbacks.remove(callback)

    def begin_sync(self) -> str:
        return ""

    def end_sync(self) -> str:
        return ""

    def write(self, data: str) -> None:
        self.writes.append(data)

    def write_flush(self, data: str) -> None:
        self.writes.append(data)

    def enable_mouse_tracking(self) -> None:
        self.mouse_enabled = True
        self.writes.append("\x1b[?1000h\x1b[?1006h")

    def disable_mouse_tracking(self) -> None:
        self.mouse_enabled = False
        self.writes.append("\x1b[?1006l\x1b[?1000l")

    @property
    def painted(self) -> str:
        return "".join(self.writes)


@dataclass
class FakeWindow:
    lines: list[str]
    units_rendered: int = 1
    reached_top: bool = False
    known_total_rows: int | None = None


class FakeTranscript:
    """Duck-types render_visible_window, like MessageList does."""

    def __init__(self, rows: list[str], total: int | None = None) -> None:
        self.rows = rows
        self.total = total
        self.calls: list[tuple[int, int, int]] = []

    def render_visible_window(self, width, height, scroll_rows=0):
        self.calls.append((width, height, scroll_rows))
        start = max(0, len(self.rows) - scroll_rows - height)
        stop = max(0, len(self.rows) - scroll_rows)
        return FakeWindow(
            lines=self.rows[start:stop],
            reached_top=start == 0,
            known_total_rows=self.total,
        )


class Chrome:
    """A plain component contributing a fixed number of rows."""

    def __init__(self, rows: list[str]) -> None:
        self._rows = rows

    def render_cells(self, area, buf) -> int:
        buf.grow_to(len(self._rows))
        for y, text in enumerate(self._rows):
            buf.set_string(0, y, text, max_width=area.width)
        return len(self._rows)


def rows_painted(term: FakeTerminal) -> int:
    return term.painted.count("\x1b[2K")


class TestMouseCaptureLifecycle:
    def test_mouse_is_not_captured_before_start(self) -> None:
        term = FakeTerminal()
        AppViewportRenderer(term)  # type: ignore[arg-type]
        assert not term.mouse_enabled
        assert term.writes == []

    def test_start_captures_the_mouse_and_claims_the_region(self) -> None:
        term = FakeTerminal(height=10)
        AppViewportRenderer(term).start()  # type: ignore[arg-type]
        assert term.mouse_enabled
        assert "\x1b[?1000h" in term.painted

    def test_stop_hands_the_mouse_back(self) -> None:
        term = FakeTerminal()
        r = AppViewportRenderer(term)  # type: ignore[arg-type]
        r.start()
        r.stop()
        assert not term.mouse_enabled
        assert "\x1b[?1000l" in term.painted

    def test_start_is_idempotent(self) -> None:
        term = FakeTerminal()
        r = AppViewportRenderer(term)  # type: ignore[arg-type]
        r.start()
        r.start()
        assert term.painted.count("\x1b[?1000h") == 1

    def test_stop_without_start_does_nothing(self) -> None:
        term = FakeTerminal()
        AppViewportRenderer(term).stop()  # type: ignore[arg-type]
        assert term.writes == []

    def test_render_before_start_paints_nothing(self) -> None:
        """Guards against a frame escaping while the flag is off."""
        term = FakeTerminal()
        AppViewportRenderer(term).render([FakeTranscript(["a"])])  # type: ignore[arg-type]
        assert term.writes == []


class TestFrameComposition:
    def _render(self, term: FakeTerminal, children):
        r = AppViewportRenderer(term)  # type: ignore[arg-type]
        r.start()
        term.writes.clear()
        r.render(children)
        return r

    def test_region_is_filled_exactly(self) -> None:
        term = FakeTerminal(height=10)
        self._render(term, [FakeTranscript([f"m{i}" for i in range(50)])])
        assert rows_painted(term) == 10

    def test_bottom_chrome_stays_pinned_to_the_bottom(self) -> None:
        """The editor must not float upward when the transcript is short."""
        term = FakeTerminal(height=10)
        self._render(term, [FakeTranscript(["only line"]), Chrome(["EDITOR"])])
        painted_rows = term.painted.split("\r\n")
        assert "EDITOR" in painted_rows[-1], "editor must occupy the last row"

    def test_top_chrome_is_rendered_above_the_transcript(self) -> None:
        term = FakeTerminal(height=10)
        self._render(term, [Chrome(["HEADER"]), FakeTranscript(["a", "b"]), Chrome(["EDITOR"])])
        rows = term.painted.split("\r\n")
        assert "HEADER" in rows[0]
        assert "EDITOR" in rows[-1]

    def test_transcript_is_asked_only_for_the_rows_that_fit(self) -> None:
        """The property the whole backend exists for."""
        term = FakeTerminal(height=10)
        transcript = FakeTranscript([f"m{i}" for i in range(10_000)])
        self._render(term, [Chrome(["HEADER"]), transcript, Chrome(["EDITOR"])])
        _width, asked_height, _scroll = transcript.calls[-1]
        assert asked_height == 8, "10 rows minus 1 header and 1 editor"

    def test_frame_cost_does_not_depend_on_transcript_length(self) -> None:
        for size in (10, 1_000, 100_000):
            term = FakeTerminal(height=10)
            transcript = FakeTranscript([f"m{i}" for i in range(size)])
            self._render(term, [transcript, Chrome(["EDITOR"])])
            assert transcript.calls[-1][1] == 9
            assert rows_painted(term) == 10

    def test_scroll_position_is_passed_through(self) -> None:
        term = FakeTerminal(height=10)
        r = AppViewportRenderer(term)  # type: ignore[arg-type]
        r.start()
        transcript = FakeTranscript([f"m{i}" for i in range(100)])
        r.viewport.scroll_up(12)
        r.render([transcript])
        assert transcript.calls[-1][2] == 12

    def test_chrome_taller_than_the_region_drops_the_header_first(self) -> None:
        """The editor is where the user is acting; a header is expendable."""
        term = FakeTerminal(height=4)
        self._render(
            term,
            [Chrome(["H1", "H2", "H3"]), FakeTranscript(["a"]), Chrome(["E1", "E2", "E3"])],
        )
        assert "H1" not in term.painted
        assert "E3" in term.painted

    def test_missing_transcript_still_paints_chrome(self) -> None:
        """A full-screen takeover replaces the transcript; don't blank the screen."""
        term = FakeTerminal(height=5)
        self._render(term, [Chrome(["ONLY"])])
        assert "ONLY" in term.painted
        assert rows_painted(term) == 5


class TestResize:
    def test_resize_reclaims_the_region_at_the_new_height(self) -> None:
        term = FakeTerminal(height=10)
        r = AppViewportRenderer(term)  # type: ignore[arg-type]
        r.start()
        transcript = FakeTranscript([f"m{i}" for i in range(100)])
        r.render([transcript])

        term.height = 20
        term.writes.clear()
        r.render([transcript])

        assert rows_painted(term) == 20
        assert transcript.calls[-1][1] == 20


class TestWheel:
    @pytest.mark.parametrize("button,expected", [(64, 3), (65, 0)])
    def test_wheel_moves_the_viewport(self, button: int, expected: int) -> None:
        r = AppViewportRenderer(FakeTerminal())  # type: ignore[arg-type]
        assert r.handle_mouse(button)
        assert r.viewport.anchor == expected

    def test_non_wheel_input_is_not_consumed(self) -> None:
        r = AppViewportRenderer(FakeTerminal())  # type: ignore[arg-type]
        assert not r.handle_mouse(0)


class TestOverlays:
    """Overlays were invisible under this backend until compositing was wired.

    Driven through the real TUI rather than hand-built OverlayEntry objects, so
    these break if overlay plumbing changes shape anywhere along the path.

    Async because ``show_overlay`` requests a render, and ``_request_render``
    reaches for the running event loop — without one these pass alone and fail
    in the full suite, depending on what an earlier test left behind.
    """

    def _tui(self, backend: str = "app-viewport"):
        from tau.tui.component import StaticComponent
        from tau.tui.service import TUI

        term = FakeTerminal(height=8)
        tui = TUI(terminal=term, render_backend=backend)  # type: ignore[arg-type]
        tui.children.append(StaticComponent(["base content"]))
        return tui, term, StaticComponent

    @pytest.mark.asyncio
    async def test_overlay_is_drawn(self) -> None:
        tui, term, Static = self._tui()
        tui.show_overlay(Static(["*** MODAL ***"]))
        term.writes.clear()
        tui._do_render()
        assert "*** MODAL ***" in term.painted
        tui.dispose()

    @pytest.mark.asyncio
    async def test_overlay_covers_the_content_beneath_it(self) -> None:
        tui, term, Static = self._tui()
        tui.show_overlay(Static(["OVERLAY"]))
        term.writes.clear()
        tui._do_render()
        painted = term.painted
        assert "OVERLAY" in painted
        assert painted.count("\x1b[2K") == 8, "region height must be unchanged"
        tui.dispose()

    @pytest.mark.asyncio
    async def test_no_overlay_leaves_the_frame_alone(self) -> None:
        tui, term, _ = self._tui()
        term.writes.clear()
        tui._do_render()
        assert "base content" in term.painted
        tui.dispose()

    @pytest.mark.asyncio
    async def test_closing_an_overlay_removes_it(self) -> None:
        tui, term, Static = self._tui()
        handle = tui.show_overlay(Static(["TRANSIENT"]))
        tui._do_render()
        assert "TRANSIENT" in term.painted

        handle.close()
        term.writes.clear()
        tui._do_render()

        assert "TRANSIENT" not in term.painted, "overlay must not ghost after closing"
        tui.dispose()

    @pytest.mark.asyncio
    async def test_both_backends_agree_that_an_overlay_is_visible(self) -> None:
        """The regression that motivated this: one backend drew it, one did not."""
        for backend in ("native-scrollback", "app-viewport"):
            tui, term, Static = self._tui(backend)
            tui.show_overlay(Static(["*** MODAL ***"]))
            term.writes.clear()
            tui._do_render()
            assert "*** MODAL ***" in term.painted, f"{backend} did not draw the overlay"
            tui.dispose()

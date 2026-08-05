"""Anything that renders must satisfy every contract its callers use.

``Component`` bridges ``render(width)`` and ``render_cells(area, buf)``, so a
subclass can implement either. Classes that are *not* Components inherit no
bridge and must provide whatever their callers actually call.

SelectorController is exactly that: a plain class, called directly by
``Layout.render_cells``. Migrating it to ``render()`` alone removed the method
Layout calls, and because ``TUI._do_render`` swallows exceptions to keep the
app alive, the result was a frozen screen with a healthy process — the failure
only visible in the session log. These tests make that mismatch fail loudly at
test time instead.
"""

from __future__ import annotations

import inspect

import pytest

from tau.tui.buffer import Buffer
from tau.tui.component import Component
from tau.tui.geometry import Rect

WIDTH = 40


def _render_capable_non_components() -> list[type]:
    """Classes that render but do not inherit Component's bridging."""
    import tau.modes.interactive.components.selector_controller as sc

    found = []
    for _name, obj in inspect.getmembers(sc, inspect.isclass):
        if obj.__module__ != sc.__name__:
            continue
        if issubclass(obj, Component):
            continue
        if hasattr(obj, "render") or hasattr(obj, "render_cells"):
            found.append(obj)
    return found


def test_selector_controller_is_covered_by_this_test() -> None:
    from tau.modes.interactive.components.selector_controller import SelectorController

    assert SelectorController in _render_capable_non_components()


@pytest.mark.parametrize("cls", _render_capable_non_components(), ids=lambda c: c.__name__)
def test_non_component_renderers_provide_both_contracts(cls: type) -> None:
    """No bridge is inherited, so both entry points must exist."""
    assert callable(getattr(cls, "render", None)), f"{cls.__name__} is missing render()"
    assert callable(getattr(cls, "render_cells", None)), (
        f"{cls.__name__} is missing render_cells(); Layout still calls it directly, "
        "and TUI._do_render swallows the AttributeError into a frozen screen"
    )


def test_selector_controller_renders_through_both_contracts_when_idle() -> None:
    from tau.modes.interactive.components.selector_controller import SelectorController

    ctl = SelectorController.__new__(SelectorController)
    ctl._active = None

    assert ctl.render(WIDTH) == []
    buf = Buffer.empty(Rect(0, 0, WIDTH, 0))
    assert ctl.render_cells(Rect(0, 0, WIDTH, 0), buf) == 0


def test_selector_controller_delegates_both_contracts_to_the_active_selector() -> None:
    from tau.modes.interactive.components.selector_controller import SelectorController

    class _Sel:
        def render(self, width: int) -> list[str]:
            return ["selector row"]

        def render_cells(self, area: Rect, buf: Buffer) -> int:
            from tau.tui.ansi_bridge import parse_ansi_wrapped_into

            return parse_ansi_wrapped_into(buf, area.x, area.y, "selector row", area.width)

    class _Active:
        selector = _Sel()

    ctl = SelectorController.__new__(SelectorController)
    ctl._active = _Active()

    assert ctl.render(WIDTH) == ["selector row"]
    buf = Buffer.empty(Rect(0, 0, WIDTH, 0))
    assert ctl.render_cells(Rect(0, 0, WIDTH, 0), buf) == 1


def test_layout_can_render_with_a_selector_open() -> None:
    """The path that froze: Layout.render_cells reaching SelectorController."""
    from tau.modes.interactive.components import layout as layout_mod

    src = inspect.getsource(layout_mod.Layout.render_cells)
    assert "_selectors.render_cells" in src, (
        "Layout no longer calls _selectors.render_cells — update this test, and "
        "check SelectorController still satisfies whatever replaced it"
    )

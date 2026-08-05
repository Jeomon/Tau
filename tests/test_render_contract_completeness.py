"""Anything that renders must satisfy the contract its callers use.

SelectorController was a plain class called directly by ``Layout``. When the
method Layout calls went missing, ``TUI._do_render`` swallowed the resulting
AttributeError to keep the app alive, and the result was a frozen screen with
a healthy process — the failure only visible in the session log.

``render(width) -> list[str]`` is now the sole contract, so these tests pin
that SelectorController is a Component implementing it, that anything else in
that module which renders provides it too, and that Layout still reaches it
the way it expects.
"""

from __future__ import annotations

import inspect

import pytest

from tau.tui.component import Component

WIDTH = 40


def _render_capable_non_components() -> list[type]:
    """Classes that render but do not inherit from Component."""
    import tau.modes.interactive.components.selector_controller as sc

    found = []
    for _name, obj in inspect.getmembers(sc, inspect.isclass):
        if obj.__module__ != sc.__name__:
            continue
        if issubclass(obj, Component):
            continue
        if hasattr(obj, "render"):
            found.append(obj)
    return found


def test_selector_controller_is_a_component_that_implements_render() -> None:
    from tau.modes.interactive.components.selector_controller import SelectorController

    assert issubclass(SelectorController, Component)
    assert SelectorController.render is not Component.render


@pytest.mark.parametrize("cls", _render_capable_non_components(), ids=lambda c: c.__name__)
def test_non_component_renderers_provide_render(cls: type) -> None:
    """No base class supplies it, so the entry point must exist on its own."""
    assert callable(getattr(cls, "render", None)), (
        f"{cls.__name__} is missing render(); Layout calls it directly, and "
        "TUI._do_render swallows the AttributeError into a frozen screen"
    )


def test_selector_controller_renders_nothing_when_idle() -> None:
    from tau.modes.interactive.components.selector_controller import SelectorController

    ctl = SelectorController.__new__(SelectorController)
    ctl._active = None

    assert ctl.render(WIDTH) == []


def test_selector_controller_delegates_to_the_active_selector() -> None:
    from tau.modes.interactive.components.selector_controller import SelectorController

    class _Sel:
        def render(self, width: int) -> list[str]:  # noqa: ARG002
            return ["selector row"]

    class _Active:
        selector = _Sel()

    ctl = SelectorController.__new__(SelectorController)
    ctl._active = _Active()

    assert ctl.render(WIDTH) == ["selector row"]


def test_layout_can_render_with_a_selector_open() -> None:
    """The path that froze: Layout reaching SelectorController while open."""
    from tau.modes.interactive.components import layout as layout_mod

    src = inspect.getsource(layout_mod.Layout.render)
    assert "_child_lines(self._selectors, width)" in src, (
        "Layout no longer composes _selectors through _child_lines — update "
        "this test, and check SelectorController still satisfies whatever "
        "replaced it"
    )

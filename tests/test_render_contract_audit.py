"""Every class in the render tree must satisfy both render contracts.

``Component`` bridges ``render(width)`` and ``render_cells(area, buf)``, so a
subclass can implement either and both callers work. A class that renders
*without* inheriting Component gets no bridge, so it satisfies exactly the one
contract it happens to implement — and any caller using the other raises
AttributeError, which ``TUI._do_render`` swallows into a frozen screen with a
perfectly healthy process.

That failure mode shipped twice during the string-renderer migration:
``SelectorController`` lost ``render_cells`` (froze every selector) and the
selectors themselves never had ``render`` (froze ``/resume``, ``/model``,
``/theme``). Both were invisible to the suite and only visible in the session
log, so this audit walks the package and fails at test time instead.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

from tau.tui.component import Component

# Renderers and the Widget protocol also define a `render`, but a different
# one: Widget.render(area, buf) -> None is the grid-drawing protocol, reaching
# the tree only wrapped in WidgetComponent, and the scrollback renderers are
# not components at all. Neither is ever called as Component.render(width).
_NOT_COMPONENTS = {
    "tau.tui.widget",
    "tau.tui.widgets",
    "tau.tui.frame",
    "tau.tui.scrollback",
    "tau.tui.service",
    "tau.tui.markdown",
}

_SKIP_PACKAGES = ("tau.console.cli", "tau.modes.rpc", "tau.inference")

# Classes that render but are never reached through the Component tree, so no
# caller can use the contract they lack.
_NOT_IN_THE_TREE = {
    # A per-message render helper owned by MessageList, which calls
    # block.render(width) directly. It is never added as a child.
    "tau.modes.interactive.components.message_list.MessageBlock",
}

# Classes allowed to define both contracts themselves.
_DUAL_BY_DESIGN = {
    # The bridge itself.
    "tau.tui.component.Component",
    # Defines both on purpose, so a container never forces a migrated child
    # back through cells while its siblings are still on render_cells.
    "tau.tui.component.Container",
    # Mid-migration: still carries the legacy render_cells path used by the
    # cell Renderer. Retires when that renderer is deleted. This set should
    # only ever shrink.
    "tau.modes.interactive.components.message_list.MessageList",
}


def _render_capable_classes() -> list[tuple[str, type]]:
    import tau

    found: dict[str, type] = {}
    for mod in pkgutil.walk_packages(tau.__path__, prefix="tau."):
        name = mod.name
        if any(name.startswith(s) for s in _SKIP_PACKAGES):
            continue
        if any(name == p or name.startswith(p + ".") for p in _NOT_COMPONENTS):
            continue
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        for _n, obj in inspect.getmembers(module, inspect.isclass):
            if getattr(obj, "__module__", "") != name:
                continue
            if inspect.isabstract(obj) or getattr(obj, "_is_protocol", False):
                continue  # Protocols declare a contract, they do not render
            if "render" in obj.__dict__ or "render_cells" in obj.__dict__:
                found[f"{name}.{obj.__name__}"] = obj
    return sorted(found.items())


_CANDIDATES = _render_capable_classes()


def test_the_audit_actually_found_things() -> None:
    """Guard against the walk silently finding nothing and passing vacuously."""
    names = [k for k, _ in _CANDIDATES]
    assert len(names) > 10, names
    assert any("selector_controller" in n for n in names), names


@pytest.mark.parametrize(
    ("name", "cls"), _CANDIDATES, ids=[n.rsplit(".", 1)[-1] for n, _ in _CANDIDATES]
)
def test_render_capable_class_satisfies_both_contracts(name: str, cls: type) -> None:
    if name in _NOT_IN_THE_TREE:
        pytest.skip("not reachable through the Component tree")
    has_render = callable(getattr(cls, "render", None))
    has_cells = callable(getattr(cls, "render_cells", None))
    assert has_render and has_cells, (
        f"{name} provides only "
        f"{'render()' if has_render else 'render_cells()'}. Inherit Component so "
        "the bridge supplies the other, or forward both explicitly if it is a "
        "transparent proxy — otherwise whichever caller uses the missing one "
        "raises, and TUI._do_render turns that into a frozen screen."
    )


@pytest.mark.parametrize(
    ("name", "cls"), _CANDIDATES, ids=[n.rsplit(".", 1)[-1] for n, _ in _CANDIDATES]
)
def test_render_cells_is_not_hand_written_on_a_component(name: str, cls: type) -> None:
    """A Component should implement one contract and inherit the other.

    Hand-writing both is how a migration quietly stops migrating: it removes
    the pressure to move, and leaves two implementations to keep in step.
    Proxies that must forward both are not Components, so they are exempt.
    """
    if not issubclass(cls, Component) or name in _DUAL_BY_DESIGN:
        return
    own_render = "render" in cls.__dict__
    own_cells = "render_cells" in cls.__dict__
    assert not (own_render and own_cells), (
        f"{name} defines both render() and render_cells(). Implement one and let "
        "Component bridge the other."
    )

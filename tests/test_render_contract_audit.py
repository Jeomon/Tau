"""Every class in the render tree must implement the render contract.

``render(width) -> list[str]`` is the sole contract. A class that renders
without providing it raises AttributeError in whichever caller renders it, and
``TUI._do_render`` swallows that into a frozen screen with a perfectly healthy
process.

That failure mode shipped twice during the string-renderer migration:
``SelectorController`` and then the selectors themselves were each missing the
method their caller used, freezing every selector and then ``/resume``,
``/model`` and ``/theme``. Both were invisible to the suite and only visible in
the session log, so this audit walks the package and fails at test time instead.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

# These define a `render`, but not the Component one: the widget library draws
# itself from explicit column maths, and the scrollback renderers are not
# components at all. Neither is ever called as Component.render(width).
_NOT_COMPONENTS = {
    "tau.tui.widgets",
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
            if "render" in obj.__dict__:
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
def test_render_capable_class_implements_render(name: str, cls: type) -> None:
    if name in _NOT_IN_THE_TREE:
        pytest.skip("not reachable through the Component tree")
    assert callable(getattr(cls, "render", None)), (
        f"{name} does not provide render(width) -> list[str], the sole render "
        "contract. Inherit Component and implement it, or forward it explicitly "
        "if this is a transparent proxy — otherwise whichever caller renders it "
        "raises, and TUI._do_render turns that into a frozen screen."
    )

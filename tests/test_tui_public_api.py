"""Tests for the standalone tau.tui package boundary and public API."""

from __future__ import annotations

import ast
from pathlib import Path

from tau.tui import (
    TUI,
    Component,
    InputParser,
    OverlayOptions,
    Renderer,
    Terminal,
    Text,
    TextInput,
)
from tests.render_helpers import render_cells_to_lines


def test_public_api_exports_core_tui_primitives() -> None:
    assert issubclass(TUI, Component)
    assert issubclass(Text, Component)
    assert issubclass(TextInput, Component)
    assert Renderer is not None
    assert Terminal is not None
    assert InputParser is not None
    assert OverlayOptions is not None


def _lines(component: Component, width: int) -> list[str]:
    return [line.rstrip() for line in render_cells_to_lines(component, width)]


def test_text_wraps_and_updates() -> None:
    text = Text("alpha beta")

    assert _lines(text, 6) == ["alpha", "beta"]

    text.set_text("updated")
    assert text.text == "updated"
    assert _lines(text, 20) == ["updated"]


def test_tui_package_has_no_application_layer_imports() -> None:
    tui_root = Path(__file__).parents[1] / "tau" / "tui"
    invalid: list[str] = []

    for path in tui_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tau."):
                if not node.module.startswith("tau.tui"):
                    invalid.append(f"{path.relative_to(tui_root)}: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("tau.") and not alias.name.startswith("tau.tui"):
                        invalid.append(f"{path.relative_to(tui_root)}: {alias.name}")

    assert invalid == []

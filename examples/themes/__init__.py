"""Built-in community themes extension — registers catppuccin, dracula, gruvbox, and nord."""

from __future__ import annotations


def register(tau: object) -> None:
    from pathlib import Path

    from tau.themes.loader import load_themes_from_dir

    themes_dir = Path(__file__).parent
    result = load_themes_from_dir(themes_dir)
    for name, theme in result.themes.items():
        tau.register_theme(name, theme)

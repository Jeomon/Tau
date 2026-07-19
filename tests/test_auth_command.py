"""Tests for the /login provider union (tau/tui/commands/auth.py)."""

from __future__ import annotations

from tau.modes.interactive.commands.auth import _all_providers


def _by_id() -> dict[str, tuple]:
    return {p[0]: p for p in _all_providers()}


class TestAllProviders:
    def test_deduped_by_id(self):
        provs = _all_providers()
        seen = [p[0] for p in provs]
        assert len(seen) == len(set(seen))  # openai appears once despite multiple modalities

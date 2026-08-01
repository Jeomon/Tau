"""Aggregate hook event types from application modules."""

from __future__ import annotations

from typing import Any, TypeAlias

from ..browser.hooks import BrowserEvent

HookEvent: TypeAlias = BrowserEvent | Any


from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class BaseTelemetryEvent(ABC):
    """Base class for structured PostHog telemetry events."""

    @property
    @abstractmethod
    def event_name(self) -> str: ...

    @property
    def properties(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k != "event_name"}


@dataclass
class InstallTelemetryEvent(BaseTelemetryEvent):
    version: str
    event_name: str = "tau"

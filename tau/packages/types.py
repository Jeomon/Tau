from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceType(StrEnum):
    PYPI = "pypi"
    GIT = "git"
    LOCAL = "local"
    URL = "url"


@dataclass
class ParsedSource:
    source: SourceType
    raw: str
    name: str
    version: str | None = None
    install_spec: str | None = None  # argument passed to pip install

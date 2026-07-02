from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class Block:
    """A normalized conversation block.

    ``kind`` discriminates the variant (mirrors vcc's ``NormalizedBlock``
    union). Only the fields relevant to a given kind are populated:

      - ``user`` / ``assistant``   → ``text``
      - ``tool_call``              → ``name``, ``args``
      - ``tool_result``            → ``name``, ``text``
      - ``bash``                   → ``command``, ``output``, ``exit_code``
    """

    kind: Literal["user", "assistant", "tool_call", "tool_result", "bash"]
    text: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    command: str = ""
    output: str = ""
    exit_code: int | None = None
    source_index: int | None = None

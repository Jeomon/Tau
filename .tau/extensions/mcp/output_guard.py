"""Cap oversized MCP tool results so they don't blow out the context window."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from config import OutputGuardConfig


def guard_text(text: str, guard: OutputGuardConfig, temp_dir: Path, *, label: str = "mcp") -> str:
    """Truncate `text` per `guard` limits, spilling the full body to a temp
    file under `temp_dir` when truncation happens. Returns the (possibly
    unmodified) text to show the model."""
    lines = text.split("\n")
    over_bytes = len(text.encode("utf-8")) > guard.max_bytes
    over_lines = len(lines) > guard.max_lines

    if not over_bytes and not over_lines:
        return text

    temp_dir.mkdir(parents=True, exist_ok=True)
    spill_path = temp_dir / f"{label}-{int(time.time())}-{uuid.uuid4().hex[:8]}.txt"
    spill_path.write_text(text, encoding="utf-8")

    if over_lines:
        preview_lines = lines[: guard.max_lines]
        preview = "\n".join(preview_lines)
    else:
        preview = text

    preview_bytes = preview.encode("utf-8")
    if len(preview_bytes) > guard.max_bytes:
        preview = preview_bytes[: guard.max_bytes].decode("utf-8", errors="ignore")

    return (
        f"{preview}\n\n"
        f"[output truncated — {len(lines)} lines / {len(text.encode('utf-8'))} bytes total; "
        f"full output saved to {spill_path}]"
    )

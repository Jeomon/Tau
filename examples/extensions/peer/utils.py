from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

# ----------------------------------------------------------------------
# Constants (mirrored from the original implementation)
# ----------------------------------------------------------------------
PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 32 * 1024
MAX_SOCKET_LINE_BYTES = 64 * 1024
PEER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


# ----------------------------------------------------------------------
# Low‑level helpers – pure functions, no state
# ----------------------------------------------------------------------
def _now() -> str:
    """Current UTC timestamp in ISO‑8601 format."""
    return datetime.now(UTC).isoformat()


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _validate_peer_name(name: str) -> str:
    normalized = name.strip()
    if not PEER_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Peer names must be 1-64 characters using letters, numbers, dots, underscores, or hyphens."
        )
    return normalized


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    """Write a JSON file atomically (mode 0o600, parent dirs 0o700)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


# ----------------------------------------------------------------------
# UI‑related helpers (used by the tool implementation)
# ----------------------------------------------------------------------
def _format_messages(messages: list["PeerMessage"]) -> str:
    """Render one or more PeerMessage objects as short paragraphs."""
    sections = []
    for m in messages:
        header = f"Message from {m.sender} (ID {m.id[:8]}) at {m.created_at}"
        if m.reply_to:
            header += f", replying to {m.reply_to[:8]}"
        sections.append(f"{header}:\n{m.body}")
    return "\n\n".join(sections)


def _shorten(value: str, limit: int = 72) -> str:
    txt = " ".join(value.split())
    return txt if len(txt) <= limit else txt[: limit - 3] + "..."


def _format_command_result(action: str, result: Any) -> str:
    if action == "status" and isinstance(result, dict):
        state = "joined" if result.get("joined") else "not joined"
        lines = [
            f"Peer: {result.get('name') or '-'}",
            f"Status: {state}",
            f"Active peers: {result.get('peers', 0)}",
            f"Storage: {result.get('root') or '-'}",
        ]
        if result.get("socket"):
            lines.append(f"Socket: {result['socket']}")
        return "\n".join(lines)
    if action == "list" and isinstance(result, list):
        if not result:
            return "No other Tau peers are active."
        out = [f"Active peers ({len(result)}):"]
        for it in result:
            name = it.get("name", "?")
            model = it.get("model") or "unknown model"
            cwd = it.get("cwd") or "unknown directory"
            out.append(f"  {name}  {model}  {cwd}")
        return "\n".join(out)
    if action == "join" and isinstance(result, dict):
        return f"Joined the peer mesh as {result.get('joined', '?')}."
    if action == "send" and isinstance(result, dict):
        delivery = "notified" if result.get("notified") else "queued for later delivery"
        mid = str(result.get("id", ""))[:8]
        return f"Message sent to {result.get('recipient', '?')} ({delivery}).\nMessage ID: {mid}"
    if action == "inbox" and isinstance(result, list):
        if not result:
            return "No delivered peer messages."
        out = [f"Recent peer messages ({len(result)}):"]
        for it in result:
            out.append(
                f"  {it.get('sender', '?')}  {it.get('created_at', '')}\n    {_shorten(str(it.get('body', '')))}"
            )
        return "\n".join(out)
    if action == "receipts" and isinstance(result, list):
        if not result:
            return "No delivery receipts."
        out = [f"Recent delivery receipts ({len(result)}):"]
        for it in result:
            out.append(
                f"  {str(it.get('message_id', ''))[:8]}  delivered to {it.get('recipient', '?')}  {it.get('delivered_at', '')}"
            )
        return "\n".join(out)
    if action == "leave":
        return "Left the peer mesh."
    return str(result)


def _render_peer_result(content: str, opts: Any) -> list[str]:
    """Render the complete peer result; the default TUI shell handles previews."""
    import json

    # Determine which action was performed – stored in the metadata by the tool.
    action = (getattr(opts, "metadata", {}) or {}).get("action", "")
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Fallback: treat raw content as plain text.
        return content.splitlines() or [content]

    formatted = _format_command_result(action, result)
    lines = formatted.splitlines()
    if not lines:
        return []

    return lines


def _argument_completions(peer: "Peer", text: str) -> list[Any]:
    from tau.tui.autocomplete import AutocompleteItem

    actions = {
        "status": "Show this peer's status",
        "join": "Join or rename this peer",
        "list": "List active peers",
        "send": "Send a direct message",
        "inbox": "Show received messages",
        "receipts": "Show delivery receipts",
        "leave": "Leave the peer mesh",
    }
    parts = text.split()
    if not parts:
        return [AutocompleteItem(label=a, description=d) for a, d in actions.items()]
    if len(parts) == 1 and not text.endswith(" "):
        prefix = parts[0]
        return [
            AutocompleteItem(label=a, description=d)
            for a, d in actions.items()
            if a.startswith(prefix)
        ]
    if parts[0] == "send" and (len(parts) == 1 or len(parts) == 2):
        prefix = parts[1] if len(parts) == 2 and not text.endswith(" ") else ""
        return [
            AutocompleteItem(
                label=f"send {p.name}",
                description=f"{p.model} in {p.cwd}",
                insert_text=f"send {p.name}",
            )
            for p in peer.list_peers()
            if p.name.startswith(prefix)
        ]
    return []


def _emit(ctx: Any, text: str, level: str = "info") -> None:
    if ctx.ui is not None:
        ctx.ui.notify(text, level)
    else:
        print(text)

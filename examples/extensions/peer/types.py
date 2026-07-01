from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import _validate_peer_name


@dataclass(frozen=True)
class PeerConfig:
    """Configuration for a peer mesh."""

    root: Path
    default_name: str | None = None
    auto_join: bool = True


@dataclass(frozen=True)
class PeerRegistration:
    version: int
    name: str
    instance_id: str
    pid: int
    socket_path: str
    cwd: str
    model: str
    started_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PeerRegistration:
        return cls(
            version=int(value["version"]),
            name=_validate_peer_name(str(value["name"])),
            instance_id=str(value["instance_id"]),
            pid=int(value["pid"]),
            socket_path=str(value["socket_path"]),
            cwd=str(value.get("cwd", "")),
            model=str(value.get("model", "")),
            started_at=str(value["started_at"]),
            updated_at=str(value["updated_at"]),
        )


@dataclass(frozen=True)
class PeerMessage:
    version: int
    id: str
    sender: str
    recipient: str
    body: str
    created_at: str
    reply_to: str | None = None
    requires_ack: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PeerMessage:
        msg = cls(
            version=int(value["version"]),
            id=str(value["id"]),
            sender=_validate_peer_name(str(value["sender"])),
            recipient=_validate_peer_name(str(value["recipient"])),
            body=str(value["body"]),
            created_at=str(value["created_at"]),
            reply_to=str(value["reply_to"]) if value.get("reply_to") else None,
            requires_ack=bool(value.get("requires_ack", True)),
        )
        if msg.version != 1:  # PROTOCOL_VERSION
            raise ValueError(f"Unsupported peer protocol version: {msg.version}")
        if not msg.body or len(msg.body.encode("utf-8")) > 32 * 1024:  # MAX_MESSAGE_BYTES
            raise ValueError("Peer message is empty or exceeds the size limit.")
        return msg

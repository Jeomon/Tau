from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .types import PeerConfig, PeerMessage, PeerRegistration
from .utils import (
    _atomic_json_write,
    _now,
    _read_json,
    _validate_peer_name,
    _is_process_alive,
    _format_messages,
    PROTOCOL_VERSION,
)

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 32 * 1024
MAX_SOCKET_LINE_BYTES = 64 * 1024


class Peer:
    """Own a local peer identity, socket server, and durable inbox."""

    def __init__(self, config: PeerConfig) -> None:
        self.config = config
        self.instance_id = uuid.uuid4().hex
        self.name = ""
        self.started_at = _now()
        self.cwd = ""
        self.model = ""
        self._server: asyncio.AbstractServer | None = None
        self._socket_path: Path | None = None
        self._context: Any | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._drain_lock = asyncio.Lock()
        self._stopping = False

    # ------------------------------------------------------------------
    #  Path helpers
    # ------------------------------------------------------------------
    @property
    def registry_dir(self) -> Path:
        return self.config.root / "registry"

    @property
    def sockets_dir(self) -> Path:
        return self.config.root / "sockets"

    @property
    def mailboxes_dir(self) -> Path:
        return self.config.root / "mailboxes"

    @property
    def receipts_dir(self) -> Path:
        return self.config.root / "receipts"

    @property
    def joined(self) -> bool:
        return bool(self.name and self._server is not None)

    # ------------------------------------------------------------------
    #  Internal utilities
    # ------------------------------------------------------------------
    def _prepare_directories(self) -> None:
        for p in (
            self.config.root,
            self.registry_dir,
            self.sockets_dir,
            self.mailboxes_dir,
            self.receipts_dir,
        ):
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
            with contextlib.suppress(OSError):
                p.chmod(0o700)

    async def start(self, ctx: Any) -> None:
        self._context = ctx
        self.cwd = str(ctx.cwd)
        self.model = ctx.model_id
        if self.joined or not self.config.auto_join:
            return
        req = self.config.default_name or f"tau-{os.getpid()}"
        await self.join(req, ctx)

    async def join(self, requested_name: str, ctx: Any) -> str:
        name = _validate_peer_name(requested_name)
        self._context = ctx
        self.cwd = str(ctx.cwd)
        self.model = ctx.model_id
        self._prepare_directories()

        if self._server is None:
            await self._start_server()

        if self.name == name:
            self._write_registration(exclusive=False)
            self._schedule_drain()
            return name

        previous_name = self.name
        self._claim_registration(name)
        self.name = name
        if previous_name:
            self._remove_registration(previous_name)
        self._schedule_drain()
        return name

    async def _start_server(self) -> None:
        self._stopping = False
        cand = self.sockets_dir / f"{self.instance_id[:20]}.sock"
        if len(os.fsencode(cand)) >= 100:
            fallback = Path("/private/tmp")
            if not fallback.is_dir():
                fallback = Path(tempfile.gettempdir())
            cand = fallback / f"tau-peer-{self.instance_id[:20]}.sock"
        with contextlib.suppress(FileNotFoundError):
            cand.unlink()
        self._server = await asyncio.start_unix_server(self._handle_connection, path=cand)
        self._socket_path = cand
        with contextlib.suppress(OSError):
            cand.chmod(0o600)

    def _registration(self, name: str | None = None) -> PeerRegistration:
        if self._socket_path is None:
            raise RuntimeError("Peer socket is not running.")
        ts = _now()
        return PeerRegistration(
            version=PROTOCOL_VERSION,
            name=name or self.name,
            instance_id=self.instance_id,
            pid=os.getpid(),
            socket_path=str(self._socket_path),
            cwd=self.cwd,
            model=self.model,
            started_at=self.started_at,
            updated_at=ts,
        )

    def _claim_registration(self, name: str) -> None:
        path = self.registry_dir / f"{name}.json"
        if path.exists():
            try:
                existing = PeerRegistration.from_dict(_read_json(path))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                existing = None
            if (
                existing
                and existing.instance_id != self.instance_id
                and _is_process_alive(existing.pid)
                and Path(existing.socket_path).exists()
            ):
                raise RuntimeError(f'Peer name "{name}" is already active.')
            with contextlib.suppress(OSError):
                path.unlink()

        reg = self._registration(name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(f'Peer name "{name}" was claimed concurrently.') from exc
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(reg), stream, ensure_ascii=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _write_registration(self, *, exclusive: bool) -> None:
        if not self.name:
            return
        if exclusive:
            self._claim_registration(self.name)
            return
        _atomic_json_write(
            self.registry_dir / f"{self.name}.json",
            asdict(self._registration()),
        )

    def _remove_registration(self, name: str) -> None:
        path = self.registry_dir / f"{name}.json"
        try:
            cur = PeerRegistration.from_dict(_read_json(path))
            if cur.instance_id == self.instance_id:
                path.unlink()
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return

    def list_peers(self, *, include_self: bool = False) -> list[PeerRegistration]:
        self._prepare_directories()
        out: list[PeerRegistration] = []
        for path in sorted(self.registry_dir.glob("*.json")):
            try:
                p = PeerRegistration.from_dict(_read_json(path))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if not _is_process_alive(p.pid) or not Path(p.socket_path).exists():
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            if include_self or p.instance_id != self.instance_id:
                out.append(p)
        return out

    def get_peer(self, name: str) -> PeerRegistration:
        norm = _validate_peer_name(name)
        for p in self.list_peers(include_self=True):
            if p.name == norm:
                return p
        raise RuntimeError(f'Peer "{norm}" is not active.')

    async def send(
        self,
        recipient: str,
        body: str,
        *,
        reply_to: str | None = None,
        requires_ack: bool = True,
    ) -> dict[str, Any]:
        if not self.joined:
            raise RuntimeError("Join the peer mesh before sending.")
        target = self.get_peer(recipient)
        if target.instance_id == self.instance_id:
            raise RuntimeError("Cannot send a peer message to yourself.")
        txt = body.strip()
        if not txt:
            raise ValueError("Message body is required.")
        if len(txt.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError(f"Message exceeds {MAX_MESSAGE_BYTES} bytes.")

        msg = PeerMessage(
            version=PROTOCOL_VERSION,
            id=uuid.uuid4().hex,
            sender=self.name,
            recipient=target.name,
            body=txt,
            created_at=_now(),
            reply_to=reply_to,
            requires_ack=requires_ack,
        )
        pending = self._pending_dir(target.name) / f"{msg.id}.json"
        _atomic_json_write(pending, asdict(msg))

        notified = await self._notify(target, msg.id)
        return {"id": msg.id, "recipient": target.name, "queued": True, "notified": notified}

    async def _notify(self, peer: PeerRegistration, message_id: str) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(peer.socket_path), timeout=1.0
            )
            writer.write(
                (
                    json.dumps(
                        {
                            "version": PROTOCOL_VERSION,
                            "type": "notify",
                            "recipient": peer.name,
                            "message_id": message_id,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            )
            await writer.drain()
            resp = await asyncio.wait_for(reader.readline(), timeout=1.0)
            writer.close()
            await writer.wait_closed()
            val = json.loads(resp)
            return bool(isinstance(val, dict) and val.get("accepted"))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            return False

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        response: dict[str, Any] = {"accepted": False}
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=2.0)
            if not raw or len(raw) > MAX_SOCKET_LINE_BYTES:
                raise ValueError("Invalid socket request.")
            req = json.loads(raw)
            if not isinstance(req, dict):
                raise ValueError("Invalid socket request.")
            if int(req.get("version", 0)) != PROTOCOL_VERSION:
                raise ValueError("Unsupported protocol version.")
            t = req.get("type")
            if t == "ping":
                response = {"accepted": True, "peer": self.name}
            elif t == "notify":
                if req.get("recipient") != self.name:
                    raise ValueError("Wrong message recipient.")
                self._schedule_drain()
                response = {"accepted": True}
            else:
                raise ValueError("Unknown socket request.")
        except (OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as exc:
            response = {"accepted": False, "error": str(exc)}
        finally:
            writer.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
            with contextlib.suppress(OSError, ConnectionError):
                await writer.drain()
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    def _mailbox_dir(self, name: str) -> Path:
        return self.mailboxes_dir / _validate_peer_name(name)

    def _pending_dir(self, name: str) -> Path:
        p = self._mailbox_dir(name) / "pending"
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        return p

    def _processing_dir(self, name: str) -> Path:
        p = self._mailbox_dir(name) / "processing"
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        return p

    def _delivered_dir(self, name: str) -> Path:
        p = self._mailbox_dir(name) / "delivered"
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        return p

    def _schedule_drain(self) -> None:
        if self._stopping or not self.name or self._context is None:
            return
        if self._drain_task and not self._drain_task.done():
            return
        self._drain_task = asyncio.create_task(self._drain_mailbox())
        self._drain_task.add_done_callback(self._drain_finished)

    def _drain_finished(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or self._stopping or not self.name:
            return
        with contextlib.suppress(Exception):
            task.result()
        if any(self._pending_dir(self.name).glob("*.json")):
            self._drain_task = None
            self._schedule_drain()

    async def _drain_mailbox(self) -> None:
        async with self._drain_lock:
            pend = self._pending_dir(self.name)
            proc = self._processing_dir(self.name)
            delivered = self._delivered_dir(self.name)
            claimed: list[tuple[Path, PeerMessage]] = []

            for path in sorted(pend.glob("*.json"))[:50]:
                proc_path = proc / path.name
                try:
                    os.replace(path, proc_path)
                    msg = PeerMessage.from_dict(_read_json(proc_path))
                    if msg.recipient != self.name:
                        raise ValueError("Mailbox recipient does not match active peer.")
                    claimed.append((proc_path, msg))
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    with contextlib.suppress(OSError):
                        proc_path.unlink()

            if not claimed or self._context is None:
                return

            content = _format_messages([m for _, m in claimed])
            try:
                await self._context.send_user_message(
                    content, deliver_as="follow_up", trigger_turn=True
                )
            except Exception:
                for proc_path, _ in claimed:
                    with contextlib.suppress(OSError):
                        os.replace(proc_path, pend / proc_path.name)
                return

            delivered_at = _now()
            for proc_path, msg in claimed:
                dst = delivered / proc_path.name
                with contextlib.suppress(OSError):
                    os.replace(proc_path, dst)
                if msg.requires_ack:
                    _atomic_json_write(
                        self.receipts_dir / msg.sender / f"{msg.id}.json",
                        {
                            "version": PROTOCOL_VERSION,
                            "message_id": msg.id,
                            "sender": msg.sender,
                            "recipient": msg.recipient,
                            "delivered_at": delivered_at,
                        },
                    )

    def inbox(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.name:
            return []
        paths = sorted(
            self._delivered_dir(self.name).glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        out = []
        for p in paths[: max(1, min(limit, 100))]:
            try:
                out.append(asdict(PeerMessage.from_dict(_read_json(p))))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return out

    def receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.name:
            return []
        dir_ = self.receipts_dir / self.name
        paths = sorted(dir_.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        res = []
        for p in paths[: max(1, min(limit, 100))]:
            with contextlib.suppress(OSError, ValueError, json.JSONDecodeError):
                res.append(_read_json(p))
        return res

    async def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
        if self.name:
            self._remove_registration(self.name)
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._socket_path:
            with contextlib.suppress(FileNotFoundError):
                self._socket_path.unlink()
            self._socket_path = None
        self.name = ""

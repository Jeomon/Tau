"""
RPC mode — JSON-lines stdin → stdout protocol.

Each line on stdin is a JSON object with a ``type`` field and an optional ``id``.
Each line on stdout is a JSON object (event or response).

Protocol matches the reference implementation (rpc-types.ts).
Commands are dispatched via :func:`run_rpc_mode`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import enum
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


class _ProtocolOutput:
    """Owns the real stdout so the JSON-lines stream cannot be corrupted.

    Two jobs:

    * **Guard** — ``install()`` dups fd 1 aside for protocol writes and points
      fd 1 at stderr, so a stray ``print`` from a tool, an extension, or a
      subprocess lands on stderr instead of in the middle of a JSON line.
    * **Backpressure** — once :meth:`start_async` has run, writes go through an
      ``asyncio`` pipe writer. :meth:`write` stays synchronous and never blocks
      the event loop; async callers ``await drain()`` to wait for a slow client
      to catch up instead of stalling the agent inside a blocking ``write``.

    When neither is installed (unit tests, unsupported platforms) writes fall
    back to the current ``sys.stdout``.
    """

    def __init__(self) -> None:
        self._raw: Any = None  # binary file object on the dup'd stdout fd
        self._restore_fd: int | None = None  # separate dup, kept for restore()
        self._saved_stdout: Any = None
        self._writer: asyncio.StreamWriter | None = None
        self._installed = False

    # ── Guard ────────────────────────────────────────────────────────────────

    def install(self) -> None:
        """Redirect fd 1 → fd 2 and keep the original stdout for protocol writes."""
        if self._installed:
            return
        try:
            dup_fd = os.dup(1)
            restore_fd = os.dup(1)
        except OSError:
            _log.warning("rpc: cannot duplicate stdout; protocol stream is unguarded")
            return
        try:
            raw = os.fdopen(dup_fd, "wb", buffering=0)
            os.dup2(2, 1)
        except OSError:
            _log.warning("rpc: cannot redirect stdout; protocol stream is unguarded")
            for fd in (dup_fd, restore_fd):
                with contextlib.suppress(OSError):
                    os.close(fd)
            return
        self._raw = raw
        self._restore_fd = restore_fd
        # Python-level writes hold their own buffer on the old fd 1; point them
        # at stderr too so nothing is flushed into the protocol stream later.
        self._saved_stdout = sys.stdout
        sys.stdout = sys.stderr
        self._installed = True

    def restore(self) -> None:
        """Undo :meth:`install` (best effort — called on the way out)."""
        if not self._installed:
            return
        self._installed = False
        if self._saved_stdout is not None:
            sys.stdout = self._saved_stdout
            self._saved_stdout = None
        writer, self._writer = self._writer, None
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
        raw, self._raw = self._raw, None
        if raw is not None and writer is None:
            # With a writer attached the transport owns (and closed) this fd.
            with contextlib.suppress(Exception):
                raw.close()
        restore_fd, self._restore_fd = self._restore_fd, None
        if restore_fd is not None:
            with contextlib.suppress(OSError):
                os.dup2(restore_fd, 1)
            with contextlib.suppress(OSError):
                os.close(restore_fd)

    # ── Backpressure ─────────────────────────────────────────────────────────

    async def start_async(self) -> None:
        """Attach an asyncio writer to the protocol fd (enables :meth:`drain`)."""
        if self._raw is None or self._writer is not None:
            return
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin, self._raw
            )
            self._writer = asyncio.StreamWriter(transport, protocol, None, loop)
        except (NotImplementedError, OSError, ValueError):
            # Windows Proactor loop and odd stdout targets (a regular file) do
            # not support pipe transports — keep the blocking path.
            _log.debug("rpc: async stdout writer unavailable", exc_info=True)
            self._writer = None

    async def drain(self) -> None:
        """Wait until the client has consumed what we buffered."""
        writer = self._writer
        if writer is None:
            return
        with contextlib.suppress(Exception):
            await writer.drain()

    # ── Writing ──────────────────────────────────────────────────────────────

    def write_line(self, line: str) -> None:
        if self._writer is not None:
            self._writer.write(line.encode("utf-8"))
        elif self._raw is not None:
            self._raw.write(line.encode("utf-8"))
        else:
            sys.stdout.write(line)
            sys.stdout.flush()


_OUTPUT = _ProtocolOutput()


def install_output_guard() -> None:
    """Claim stdout for the protocol as early as possible.

    The CLI calls this the moment it knows the run is RPC — before the runtime
    (and its extensions) is built, since anything they print would otherwise
    corrupt the stream. Idempotent: ``run_rpc_mode`` calls it again.
    """
    _OUTPUT.install()


def _json_default(value: object) -> Any:
    """Last-resort encoder so an exotic field can never kill the stream."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        with contextlib.suppress(Exception):
            return dataclasses.asdict(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes | bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _write(obj: dict) -> None:
    """Write a JSON line to stdout immediately."""
    _OUTPUT.write_line(json.dumps(obj, default=_json_default) + "\n")


def _shallow_asdict(event: object) -> dict:
    """``dataclasses.asdict`` without the deep copy (used when that one fails)."""
    return {f.name: getattr(event, f.name, None) for f in dataclasses.fields(event)}  # type: ignore[arg-type]


def _serialize_event(event: object) -> dict:
    """Turn an event object into the dict that goes on the wire.

    Field names stay Python ``snake_case`` — see docs/rpc.md. Non-dataclass
    events keep their payload (``vars``) instead of collapsing to a bare type,
    and a dataclass whose fields resist deep-copying degrades to a shallow dict
    rather than raising and dropping the event entirely.
    """
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        try:
            return dataclasses.asdict(event)
        except Exception:
            _log.debug("rpc: asdict failed for %s; using shallow dict", type(event).__name__)
            return _shallow_asdict(event)
    payload = getattr(event, "__dict__", None)
    event_type = getattr(event, "type", None)
    if isinstance(payload, dict) and payload:
        out = {k: v for k, v in payload.items() if not k.startswith("_")}
        out["type"] = event_type if isinstance(event_type, str) else type(event).__name__
        return out
    if isinstance(event_type, str):
        return {"type": event_type}
    return {"type": type(event).__name__}


# ---------------------------------------------------------------------------
# Extension UI context for RPC
# ---------------------------------------------------------------------------


class RpcExtensionUIContext:
    """
    Implements the extension UI API for RPC mode.

    Dialog methods (select, confirm, input, editor) emit an ``extension_ui_request``
    on stdout and block until the client sends back an ``extension_ui_response``.
    Fire-and-forget methods (notify, setStatus, setWidget, setTitle, set_editor_text)
    emit without waiting for a reply.
    """

    def __init__(self, pending: dict[str, asyncio.Future]) -> None:
        self._pending = pending
        self._next_id = 0

    def _new_req_id(self) -> str:
        self._next_id += 1
        return f"ui_{self._next_id}"

    async def _dialog(self, payload: dict, timeout: float | None = None) -> Any:
        """Emit a dialog request and wait for the client response.

        ``timeout`` is in seconds; it is advertised to the client in
        milliseconds (matching the protocol's ``timeout`` field) and enforced
        here, so a client that never answers cannot wedge the extension
        forever. A timeout resolves to ``None`` — the same value as a cancel.
        """
        req_id = self._new_req_id()
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        request = {"type": "extension_ui_request", "id": req_id, **payload}
        if timeout is not None:
            request["timeout"] = int(timeout * 1000)
        _write(request)
        try:
            if timeout is None:
                return await fut
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            _log.debug("rpc: extension UI request %s timed out", req_id)
            return None
        finally:
            self._pending.pop(req_id, None)

    def cancel_pending(self) -> None:
        """Resolve every waiting dialog with ``None`` (the client went away).

        Without this, an extension awaiting ``ctx.select`` blocks shutdown
        forever when the client disconnects mid-dialog.
        """
        for req_id, fut in list(self._pending.items()):
            self._pending.pop(req_id, None)
            if not fut.done():
                fut.set_result(None)

    def _fire(self, payload: dict) -> None:
        """Emit a fire-and-forget notification (no client response expected)."""
        req_id = self._new_req_id()
        _write({"type": "extension_ui_request", "id": req_id, **payload})

    async def select(
        self, title: str, options: list[str], timeout: float | None = None
    ) -> str | None:
        return await self._dialog(
            {"method": "select", "title": title, "options": options}, timeout
        )

    async def confirm(self, title: str, message: str = "", timeout: float | None = None) -> bool:
        result = await self._dialog(
            {"method": "confirm", "title": title, "message": message}, timeout
        )
        if isinstance(result, dict):
            if result.get("cancelled"):
                return False
            return bool(result.get("confirmed", False))
        return bool(result)

    async def input(
        self, title: str, placeholder: str = "", timeout: float | None = None
    ) -> str | None:
        return await self._dialog(
            {"method": "input", "title": title, "placeholder": placeholder}, timeout
        )

    async def editor(
        self, title: str, prefill: str = "", timeout: float | None = None
    ) -> str | None:
        return await self._dialog(
            {"method": "editor", "title": title, "prefill": prefill}, timeout
        )

    def notify(self, message: str, notify_type: str = "info") -> None:
        self._fire({"method": "notify", "message": message, "notifyType": notify_type})

    def set_status(self, status_key: str, status_text: str | None) -> None:
        self._fire({"method": "setStatus", "statusKey": status_key, "statusText": status_text})

    def set_widget(
        self, widget_key: str, widget_lines: list[str] | None, placement: str = "aboveEditor"
    ) -> None:
        self._fire(
            {
                "method": "setWidget",
                "widgetKey": widget_key,
                "widgetLines": widget_lines,
                "widgetPlacement": placement,
            }
        )

    def set_title(self, title: str) -> None:
        self._fire({"method": "setTitle", "title": title})

    def set_editor_text(self, text: str) -> None:
        self._fire({"method": "set_editor_text", "text": text})


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def _resolve_attachments(
    attachments: list[dict] | None,
) -> tuple[list, list, list, list]:
    """Turn RPC ``attachments`` into (images, audio, video, file) source lists.

    Each attachment carries exactly one source: base64 ``data`` (kept as a
    string — every content type accepts base64), a server-side ``path`` (read
    into bytes), or an image-only ``url``. The returned lists are ready to pass
    straight to :meth:`UserMessage.with_media` / ``PromptOptions``.

    Raises ValueError on a malformed attachment and OSError if a ``path`` can't
    be read.
    """
    from pathlib import Path

    buckets: dict[str, list] = {"image": [], "audio": [], "video": [], "file": []}
    for i, att in enumerate(attachments or []):
        if not isinstance(att, dict):
            raise ValueError(f"attachment[{i}] must be an object")
        kind = att.get("kind")
        if kind not in buckets:
            raise ValueError(f"attachment[{i}]: invalid or missing 'kind' ({kind!r})")
        present = [k for k in ("data", "path", "url") if att.get(k)]
        if len(present) != 1:
            raise ValueError(
                f"attachment[{i}]: exactly one of 'data', 'path', 'url' is required"
            )
        source = present[0]
        if source == "url" and kind != "image":
            raise ValueError(f"attachment[{i}]: 'url' is only supported for images")
        if source == "data":
            buckets[kind].append(att["data"])  # base64 string, accepted as-is
        elif source == "path":
            buckets[kind].append(Path(att["path"]).read_bytes())
        else:
            buckets[kind].append(att["url"])  # image URL
    return buckets["image"], buckets["audio"], buckets["video"], buckets["file"]


# ---------------------------------------------------------------------------
# Prompt dispatch
# ---------------------------------------------------------------------------

# Holds references to in-flight turns so the event loop cannot garbage-collect
# a task that nobody is awaiting any more.
_BACKGROUND: set[asyncio.Task] = set()


def _is_streaming(agent: Any) -> bool:
    """True when a turn is in flight.

    The agent exposes ``is_idle()``, not a ``_running`` flag — reading the
    latter silently reported "never streaming", which made ``streamingBehavior``
    inert and ``get_state.isStreaming`` always false.
    """
    if agent is None:
        return False
    is_idle = getattr(agent, "is_idle", None)
    if callable(is_idle):
        try:
            return not bool(is_idle())
        except Exception:
            _log.debug("rpc: is_idle() failed", exc_info=True)
    return bool(getattr(agent, "_running", False))


async def _start_prompt(
    runtime: Runtime,
    text: str,
    options: Any,
    ok: Any,
    err: Any,
) -> None:
    """Start a turn and respond as soon as it is under way, not when it ends.

    The ``prompt`` response means "accepted and started" — the client gets its
    ack immediately and follows the turn through the event stream. Anything
    that fails before the turn starts (no model, no session) is reported on the
    response instead; failures after that arrive as ``agent_error`` events.
    """
    invoke = runtime.invoke(text, options) if options is not None else runtime.invoke(text)

    hooks = getattr(runtime, "hooks", None)
    if hooks is None:
        # No event bus to tell us when the turn starts — stay synchronous.
        await invoke
        ok()
        return

    started: asyncio.Future[None] = asyncio.get_event_loop().create_future()

    async def _on_start(event: object) -> None:
        if not started.done():
            started.set_result(None)

    unsub = hooks.register("agent_start", _on_start)
    task = asyncio.ensure_future(invoke)
    _BACKGROUND.add(task)

    def _finished(t: asyncio.Task) -> None:
        _BACKGROUND.discard(t)
        # Consume the exception so asyncio does not report it as unretrieved;
        # the client already saw it as an agent_error event.
        if not t.cancelled() and t.exception() is not None:
            _log.error("rpc prompt turn failed", exc_info=t.exception())

    task.add_done_callback(_finished)

    try:
        await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        unsub()

    if started.done():
        ok()
        return

    # The turn ended without ever starting — report it on the response.
    exc = task.exception() if task.done() and not task.cancelled() else None
    if exc is not None:
        err(str(exc))
    else:
        ok()


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------


async def _handle_command(
    cmd: dict, runtime: Runtime, ui_pending: dict[str, asyncio.Future]
) -> None:
    """Dispatch one RPC command. Writes a response line when done."""
    cmd_type = cmd.get("type", "")
    cmd_id = cmd.get("id")

    def _ok(data: dict | None = None) -> None:
        resp: dict = {"type": "response", "command": cmd_type, "success": True}
        if cmd_id is not None:
            resp["id"] = cmd_id
        if data is not None:
            resp["data"] = data
        _write(resp)

    def _err(message: str) -> None:
        resp: dict = {"type": "response", "command": cmd_type, "success": False, "error": message}
        if cmd_id is not None:
            resp["id"] = cmd_id
        _write(resp)

    try:
        match cmd_type:
            # ── Prompting ────────────────────────────────────────────────────

            case "prompt":
                text = cmd.get("message", "")
                try:
                    images, audio, video, file = _resolve_attachments(cmd.get("attachments"))
                except (ValueError, OSError) as exc:
                    _err(f"invalid attachment: {exc}")
                    return
                has_media = bool(images or audio or video or file)
                if not text and not has_media:
                    _err("'message' or 'attachments' is required")
                    return
                streaming_behavior = cmd.get("streamingBehavior")
                agent = runtime.agent
                is_streaming = _is_streaming(agent)

                if is_streaming and streaming_behavior is None:
                    _err("Agent is streaming; specify streamingBehavior: 'steer' or 'followUp'")
                    return

                if is_streaming and streaming_behavior == "steer":
                    from tau.message.types import UserMessage

                    msg = UserMessage.with_media(
                        text, images or None, audio or None, video or None, file or None
                    )
                    await agent._engine.steer(msg)  # type: ignore[union-attr]
                elif is_streaming and streaming_behavior == "followUp":
                    from tau.message.types import UserMessage

                    msg = UserMessage.with_media(
                        text, images or None, audio or None, video or None, file or None
                    )
                    await agent._engine.follow_up(msg)  # type: ignore[union-attr]
                else:
                    prompt_options = None
                    if has_media:
                        from tau.agent.types import PromptOptions

                        prompt_options = PromptOptions(
                            images=images, audio=audio, video=video, file=file
                        )
                    await _start_prompt(runtime, text, prompt_options, _ok, _err)
                    return
                _ok()

            case "steer":
                text = cmd.get("message", "")
                try:
                    images, audio, video, file = _resolve_attachments(cmd.get("attachments"))
                except (ValueError, OSError) as exc:
                    _err(f"invalid attachment: {exc}")
                    return
                if not text and not (images or audio or video or file):
                    _err("'message' or 'attachments' is required")
                    return
                agent = runtime.agent
                if agent is None:
                    _err("No active agent")
                    return
                from tau.message.types import UserMessage

                msg = UserMessage.with_media(
                    text, images or None, audio or None, video or None, file or None
                )
                await agent._engine.steer(msg)
                _ok()

            case "follow_up":
                text = cmd.get("message", "")
                try:
                    images, audio, video, file = _resolve_attachments(cmd.get("attachments"))
                except (ValueError, OSError) as exc:
                    _err(f"invalid attachment: {exc}")
                    return
                if not text and not (images or audio or video or file):
                    _err("'message' or 'attachments' is required")
                    return
                agent = runtime.agent
                if agent is None:
                    _err("No active agent")
                    return
                from tau.message.types import UserMessage

                msg = UserMessage.with_media(
                    text, images or None, audio or None, video or None, file or None
                )
                await agent._engine.follow_up(msg)
                _ok()

            case "abort":
                agent = runtime.agent
                if agent is not None:
                    cancel_fn = getattr(agent, "cancel", None) or getattr(agent, "abort", None)
                    if callable(cancel_fn):
                        cancel_fn()
                _ok()

            case "new_session":
                cancelled = False
                try:
                    await runtime.new_session()
                except Exception:
                    _log.error("rpc new_session failed", exc_info=True)
                    cancelled = True
                _ok({"cancelled": cancelled})

            # ── State ────────────────────────────────────────────────────────

            case "get_state":
                agent = runtime.agent
                is_streaming = _is_streaming(agent)
                sm = runtime.session_manager

                llm = agent._engine.llm if agent is not None else None
                model_info = None
                if llm is not None:
                    model = getattr(llm, "model", None)
                    if model is not None:
                        model_info = {
                            "id": getattr(model, "id", ""),
                            "provider": getattr(model, "provider", ""),
                        }

                thinking_level = None
                if llm is not None:
                    opts = getattr(getattr(llm, "api", None), "options", None)
                    if opts is not None:
                        tl = getattr(opts, "thinking_level", None)
                        if tl is not None:
                            thinking_level = getattr(tl, "value", str(tl))

                session_id = getattr(sm, "session_id", None) if sm is not None else None
                session_file = (
                    str(getattr(sm, "session_file", "") or "") if sm is not None else None
                )

                msg_count = 0
                if sm is not None:
                    from tau.session.types import MessageEntry

                    msg_count = sum(1 for e in sm.get_branch() if isinstance(e, MessageEntry))

                auto_compact = True
                if agent is not None:
                    compaction_cfg = getattr(getattr(agent, "_config", None), "compaction", None)
                    if compaction_cfg is not None:
                        auto_compact = bool(getattr(compaction_cfg, "enabled", True))

                _ok(
                    {
                        "model": model_info,
                        "thinkingLevel": thinking_level,
                        "isStreaming": is_streaming,
                        "isCompacting": False,
                        "sessionFile": session_file,
                        "sessionId": session_id,
                        "autoCompactionEnabled": auto_compact,
                        "messageCount": msg_count,
                        "pendingMessageCount": 0,
                    }
                )

            # ── Model ────────────────────────────────────────────────────────

            case "set_model":
                model_id = cmd.get("modelId", "") or cmd.get("model_id", "")
                provider = cmd.get("provider")
                if not model_id:
                    _err("'modelId' is required")
                    return
                await runtime.set_model(model_id, provider)
                agent = runtime.agent
                model_info = None
                if agent is not None:
                    llm = agent._engine.llm
                    model = getattr(llm, "model", None)
                    if model is not None:
                        model_info = {
                            "id": getattr(model, "id", ""),
                            "provider": getattr(model, "provider", ""),
                        }
                _ok(model_info)

            case "cycle_model":
                # Cycle to the next available model
                agent = runtime.agent
                new_model_info = None
                if agent is not None:
                    try:
                        from tau.inference.api.text.service import TextLLM

                        llm = agent._engine.llm
                        current_id = getattr(getattr(llm, "model", None), "id", None)
                        all_models = TextLLM.list_available()
                        if all_models and current_id:
                            ids = [getattr(m, "id", None) for m in all_models]
                            try:
                                idx = ids.index(current_id)
                                next_model = all_models[(idx + 1) % len(all_models)]
                                next_id = getattr(next_model, "id", "")
                                next_provider = getattr(next_model, "provider", None)
                                await runtime.set_model(next_id, next_provider)
                                new_model_info = {"id": next_id, "provider": next_provider or ""}
                            except ValueError:
                                pass
                    except Exception:
                        pass
                _ok({"model": new_model_info} if new_model_info else None)

            case "get_available_models":
                models: list[dict] = []
                try:
                    from tau.inference.api.text.service import TextLLM

                    for m in TextLLM.list_available():
                        models.append(
                            {
                                "id": getattr(m, "id", str(m)),
                                "provider": getattr(m, "provider", ""),
                                "name": getattr(m, "name", "") or getattr(m, "id", ""),
                                "contextWindow": getattr(m, "context_length", None),
                            }
                        )
                except Exception:
                    _log.debug("rpc get_available_models failed", exc_info=True)
                _ok({"models": models})

            # ── Thinking ─────────────────────────────────────────────────────

            case "set_thinking_level":
                level = cmd.get("level", "")
                agent = runtime.agent
                if agent is not None:
                    try:
                        from tau.inference.types import ThinkingLevel

                        tl = ThinkingLevel(level)
                        llm = agent._engine.llm
                        set_fn = getattr(llm, "set_thinking_level", None)
                        if callable(set_fn):
                            set_fn(tl)
                    except Exception as exc:
                        _err(str(exc))
                        return
                _ok()

            case "cycle_thinking_level":
                agent = runtime.agent
                new_level = None
                if agent is not None:
                    try:
                        from tau.inference.types import ThinkingLevel

                        llm = agent._engine.llm
                        opts = getattr(getattr(llm, "api", None), "options", None)
                        if opts is not None:
                            levels = list(ThinkingLevel)
                            cur = getattr(opts, "thinking_level", ThinkingLevel.Off)
                            try:
                                idx = levels.index(cur)
                                next_tl = levels[(idx + 1) % len(levels)]
                            except ValueError:
                                next_tl = levels[0]
                            set_fn = getattr(llm, "set_thinking_level", None)
                            if callable(set_fn):
                                set_fn(next_tl)
                            new_level = getattr(next_tl, "value", str(next_tl))
                    except Exception:
                        _log.debug("rpc cycle_thinking_level failed", exc_info=True)
                _ok({"level": new_level} if new_level is not None else None)

            # ── Queue modes ──────────────────────────────────────────────────

            case "set_steering_mode":
                mode = cmd.get("mode", "one-at-a-time")
                # Accept both "one-at-a-time" and "one_at_a_time" (internal)
                py_mode = mode.replace("-", "_")
                from tau.engine.types import SteeringMode

                agent = runtime.agent
                if agent is not None:
                    engine = agent._engine
                    queue = getattr(engine, "steering_queue", None)
                    if queue is not None and hasattr(queue, "mode"):
                        with contextlib.suppress(ValueError):
                            queue.mode = SteeringMode(py_mode)
                _ok()

            case "set_follow_up_mode":
                mode = cmd.get("mode", "one-at-a-time")
                py_mode = mode.replace("-", "_")
                from tau.engine.types import FollowupMode

                agent = runtime.agent
                if agent is not None:
                    engine = agent._engine
                    queue = getattr(engine, "follow_up_queue", None)
                    if queue is not None and hasattr(queue, "mode"):
                        with contextlib.suppress(ValueError):
                            queue.mode = FollowupMode(py_mode)
                _ok()

            # ── Compaction ───────────────────────────────────────────────────

            case "compact":
                instructions = cmd.get("customInstructions")
                agent = runtime.agent
                result_data: dict | None = None
                if agent is not None:
                    compact_fn = getattr(agent, "compact", None)
                    if callable(compact_fn):
                        import inspect

                        result = compact_fn(custom_instructions=instructions)
                        if inspect.isawaitable(result):
                            compaction_result = await result
                            if compaction_result is not None:
                                result_data = {
                                    "summary": getattr(compaction_result, "summary", ""),
                                    "firstKeptEntryId": getattr(
                                        compaction_result, "first_kept_entry_id", None
                                    ),
                                    "tokensBefore": getattr(
                                        compaction_result, "tokens_before", None
                                    ),
                                }
                _ok(result_data)

            case "set_auto_compaction":
                enabled = bool(cmd.get("enabled", True))
                agent = runtime.agent
                if agent is not None:
                    compaction_cfg = getattr(getattr(agent, "_config", None), "compaction", None)
                    if compaction_cfg is not None:
                        compaction_cfg.enabled = enabled
                _ok()

            # ── Retry ────────────────────────────────────────────────────────

            case "set_auto_retry":
                enabled = bool(cmd.get("enabled", True))
                settings = runtime.settings_manager
                if settings is not None:
                    set_fn = getattr(settings, "set_retry_enabled", None)
                    if callable(set_fn):
                        set_fn(enabled)
                _ok()

            case "abort_retry":
                # Abort any in-progress retry delay
                agent = runtime.agent
                if agent is not None:
                    abort_fn = getattr(agent, "abort_retry", None)
                    if callable(abort_fn):
                        abort_fn()
                _ok()

            # ── Terminal ─────────────────────────────────────────────────────────

            case "terminal":
                terminal_cmd = cmd.get("command", "")
                exclude = bool(
                    cmd.get("excludeFromContext", cmd.get("exclude_from_context", False))
                )
                if not terminal_cmd:
                    _err("'command' is required")
                    return
                await runtime.execute_terminal(terminal_cmd, exclude=exclude)
                _ok()

            case "abort_terminal":
                # Abort a running terminal subprocess if possible
                agent = runtime.agent
                if agent is not None:
                    abort_fn = getattr(agent, "abort_terminal", None)
                    if callable(abort_fn):
                        abort_fn()
                _ok()

            # ── Session ──────────────────────────────────────────────────────

            case "get_session_stats":
                sm = runtime.session_manager
                if sm is None:
                    _ok({"sessionId": None, "totalMessages": 0, "cwd": None})
                    return
                entries = sm.get_branch()
                from tau.message.types import AssistantMessage, UserMessage
                from tau.session.types import MessageEntry

                user_count = 0
                asst_count = 0
                for e in entries:
                    if not isinstance(e, MessageEntry):
                        continue
                    if isinstance(e.message, UserMessage):
                        user_count += 1
                    elif isinstance(e.message, AssistantMessage):
                        asst_count += 1
                agent = runtime.agent
                context_usage = None
                if agent is not None:
                    stats_engine = getattr(agent, "_engine", None)
                    usage = getattr(stats_engine, "context_usage", None) if stats_engine else None
                    if usage is not None:
                        tokens = getattr(usage, "tokens", None)
                        window = getattr(usage, "context_window", None) or 0
                        percent = (tokens / window * 100) if (tokens and window) else None
                        context_usage = {
                            "tokens": tokens,
                            "contextWindow": window,
                            "percent": percent,
                        }
                _ok(
                    {
                        "sessionFile": str(getattr(sm, "session_file", "") or ""),
                        "sessionId": getattr(sm, "session_id", None),
                        "userMessages": user_count,
                        "assistantMessages": asst_count,
                        "totalMessages": user_count + asst_count,
                        "cwd": str(sm.cwd),
                        "contextUsage": context_usage,
                    }
                )

            case "export_html":
                # HTML export is not implemented; return a not-supported error
                _err("export_html is not supported in this build")

            case "switch_session":
                path = cmd.get("sessionPath", "") or cmd.get("path", "")
                if not path:
                    _err("'sessionPath' is required")
                    return
                from pathlib import Path as _Path

                cancelled = False
                try:
                    await runtime.resume_session(_Path(path))
                except Exception as exc:
                    _err(str(exc))
                    return
                _ok({"cancelled": cancelled})

            case "fork":
                entry_id = cmd.get("entryId", "") or cmd.get("entry_id", "")
                position = cmd.get("position", "at")
                if not entry_id:
                    _err("'entryId' is required")
                    return
                cancelled = False
                fork_text = ""
                try:
                    # Read the original prompt text before forking
                    sm = runtime.session_manager
                    if sm is not None:
                        from tau.message.types import TextContent, UserMessage
                        from tau.session.types import MessageEntry

                        for e in sm.get_branch():
                            if (
                                isinstance(e, MessageEntry)
                                and e.id == entry_id
                                and isinstance(e.message, UserMessage)
                            ):
                                for c in e.message.contents:
                                    if isinstance(c, TextContent):
                                        fork_text += c.content
                                break
                    await runtime.fork_session(entry_id, position=position)
                except Exception as exc:
                    _err(str(exc))
                    return
                _ok({"text": fork_text, "cancelled": cancelled})

            case "clone":
                sm = runtime.session_manager
                if sm is None:
                    _err("No active session")
                    return
                cancelled = False
                leaf_id = getattr(sm, "leaf_id", None)
                try:
                    if leaf_id:
                        await runtime.fork_session(leaf_id, position="at")
                except Exception as exc:
                    _err(str(exc))
                    return
                _ok({"cancelled": cancelled})

            case "get_fork_messages":
                sm = runtime.session_manager
                if sm is None:
                    _ok({"messages": []})
                    return
                from tau.message.types import TextContent, UserMessage
                from tau.session.types import MessageEntry

                fork_messages = []
                for e in sm.get_branch():
                    if not isinstance(e, MessageEntry) or not isinstance(e.message, UserMessage):
                        continue
                    parts = []
                    for c in e.message.contents:
                        if isinstance(c, TextContent):
                            parts.append(c.content)
                    fork_messages.append({"entryId": e.id, "text": "".join(parts)})
                _ok({"messages": fork_messages})

            case "get_last_assistant_text":
                sm = runtime.session_manager
                text = ""
                if sm is not None:
                    from tau.message.types import AssistantMessage, TextContent
                    from tau.session.types import MessageEntry

                    for entry in reversed(sm.get_branch()):
                        if isinstance(entry, MessageEntry) and isinstance(
                            entry.message, AssistantMessage
                        ):
                            for c in entry.message.contents:
                                if isinstance(c, TextContent):
                                    text += c.content
                            break
                _ok({"text": text or None})

            case "set_session_name":
                name = cmd.get("name", "")
                sm = runtime.session_manager
                if sm is not None:
                    set_name_fn = getattr(sm, "set_name", None)
                    if callable(set_name_fn):
                        set_name_fn(name)
                _ok()

            # ── Messages ─────────────────────────────────────────────────────

            case "get_messages":
                sm = runtime.session_manager
                if sm is None:
                    _ok({"messages": []})
                    return
                from tau.session.types import MessageEntry

                messages = []
                for entry in sm.get_branch():
                    if not isinstance(entry, MessageEntry):
                        continue
                    entry_message = entry.message
                    role = getattr(entry_message, "role", None)
                    if role is None:
                        continue
                    role_val = role.value if hasattr(role, "value") else str(role)
                    message_parts: list[str] = []
                    for c in getattr(entry_message, "contents", []):
                        content_str = getattr(c, "content", None)
                        if isinstance(content_str, str):
                            message_parts.append(content_str)
                    messages.append({"role": role_val, "text": "".join(message_parts)})
                _ok({"messages": messages})

            # ── Commands ─────────────────────────────────────────────────────

            case "get_commands":
                cmds = []
                for info in runtime.commands.list():
                    cmds.append(
                        {
                            "name": info.name,
                            "description": info.description,
                            "source": "extension",
                        }
                    )
                # Also include prompt templates and skills
                try:
                    from tau.prompts.registry import prompt_registry

                    for tmpl in prompt_registry.list():
                        cmds.append(
                            {"name": tmpl.name, "description": tmpl.description, "source": "prompt"}
                        )
                except Exception:
                    _log.debug("rpc get_commands: prompt registry failed", exc_info=True)
                try:
                    from tau.skills.registry import skill_registry

                    for skill in skill_registry.list():
                        cmds.append(
                            {
                                "name": f"skill:{skill.name}",
                                "description": skill.description or "",
                                "source": "skill",
                            }
                        )
                except Exception:
                    _log.debug("rpc get_commands: skill registry failed", exc_info=True)
                _ok({"commands": cmds})

            # ── Extension UI response (client → tau) ──────────────────────────

            case "extension_ui_response":
                req_id = cmd.get("id")
                if req_id and req_id in ui_pending:
                    fut = ui_pending.pop(req_id)
                    if not fut.done():
                        if cmd.get("cancelled"):
                            fut.set_result(None)
                        elif "confirmed" in cmd:
                            fut.set_result({"confirmed": cmd["confirmed"]})
                        else:
                            fut.set_result(cmd.get("value"))

            case _:
                _err(f"Unknown command type: '{cmd_type}'")

    except Exception as exc:
        _err(str(exc))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


# Events forwarded to the client. Every engine event a client needs to mirror
# the session must be here — `message_rollback` in particular, or a client that
# replays the transcript silently drifts after an interrupted tool turn.
_FORWARDED_EVENTS = (
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "message_rollback",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "tool_execution_failure",
    "agent_error",
    "compaction_start",
    "compaction_end",
    "compaction_cancelled",
    "compaction_failure",
    "queue_update",
    "settled",
    # Without these the `terminal` command is a black box: success: true and
    # no way to see what the command printed.
    "terminal_execution",
    "terminal_output",
)


def _extension_error_payload(error: object) -> dict:
    """Envelope for one extension load/dispatch failure."""
    return {
        "type": "extension_error",
        "extensionPath": str(getattr(error, "extension_path", "") or ""),
        "event": getattr(error, "event", "") or "",
        "error": getattr(error, "error", "") or "",
        "stack": getattr(error, "stack", "") or "",
    }


async def run_rpc_mode(runtime: Runtime) -> None:
    """Run the RPC mode loop — reads JSON lines from stdin, writes to stdout."""

    # Take stdout over before anything can write to it: from here on fd 1 is
    # ours alone and every stray print goes to stderr.
    _OUTPUT.install()
    await _OUTPUT.start_async()

    # Pending extension UI futures keyed by request id
    ui_pending: dict[str, asyncio.Future] = {}
    ui_context = RpcExtensionUIContext(ui_pending)
    runtime.set_extension_ui_bridge(ui_context)

    # ── Subscribe to agent events and stream them out ────────────────────────

    async def on_event(event: object) -> None:
        _write(_serialize_event(event))
        # Let a slow client apply backpressure here rather than inside a
        # blocking write that would stall the whole event loop.
        await _OUTPUT.drain()

    hooks = runtime.hooks
    unsubs = [hooks.register(name, on_event) for name in _FORWARDED_EVENTS]

    # ── Surface extension failures ───────────────────────────────────────────

    runtime.set_extension_error_callback(lambda error: _write(_extension_error_payload(error)))

    # ── Shutdown plumbing ────────────────────────────────────────────────────

    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _request_shutdown() -> None:
        """Cooperative stop — used by signals and by ``ctx.shutdown()``."""
        shutdown_event.set()

    def _on_signal() -> None:
        agent = runtime.agent
        if agent is not None:
            cancel_fn = getattr(agent, "cancel", None) or getattr(agent, "abort", None)
            if callable(cancel_fn):
                cancel_fn()
        _request_shutdown()

    # An extension calling ctx.shutdown() unwinds through here instead of
    # sys.exit(0), so buffered protocol output still gets flushed.
    runtime.set_shutdown_handler(_request_shutdown)

    import signal as _signal

    # SIGHUP does not exist on Windows (AttributeError), and add_signal_handler
    # is unsupported on the Proactor loop (NotImplementedError). Skip whatever the
    # platform lacks instead of failing.
    for _sig_name in ("SIGTERM", "SIGHUP", "SIGINT"):
        _sig = getattr(_signal, _sig_name, None)
        if _sig is None:
            continue
        # Windows / unsupported event loop → add_signal_handler raises.
        with contextlib.suppress(NotImplementedError, OSError):
            loop.add_signal_handler(_sig, _on_signal)

    # ── Announce ready ───────────────────────────────────────────────────────
    sm = runtime.session_manager
    _write(
        {
            "type": "ready",
            "sessionId": getattr(sm, "session_id", None) if sm is not None else None,
            "cwd": str(sm.cwd) if sm is not None else None,
        }
    )

    # Extensions that failed to load did so before the callback was installed.
    ext_runtime = getattr(runtime, "extension_runtime", None)
    for error in getattr(ext_runtime, "errors", ()) or ():
        _write(_extension_error_payload(error))

    # ── Stdin reader ─────────────────────────────────────────────────────────

    def _dispatch_line(line: str) -> None:
        """Parse one stdin line and start handling it."""
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(
                {
                    "type": "response",
                    "command": "parse",
                    "success": False,
                    "error": f"Failed to parse command: {exc}",
                }
            )
            return
        task = asyncio.ensure_future(_handle_command(obj, runtime, ui_pending))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)

    read_task: asyncio.Task | None = None
    try:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        try:
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        except Exception:
            # Fallback for environments that don't support connect_read_pipe
            async def _stdin_loop() -> None:
                import concurrent.futures

                executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                while not shutdown_event.is_set():
                    try:
                        raw = await loop.run_in_executor(executor, sys.stdin.readline)
                    except Exception:
                        break
                    if not raw:
                        shutdown_event.set()
                        break
                    _dispatch_line(raw.rstrip("\r\n"))

            await _stdin_loop()
            return

        async def _read_loop() -> None:
            while not shutdown_event.is_set():
                try:
                    raw = await reader.readline()
                except Exception:
                    break
                if not raw:
                    shutdown_event.set()
                    break
                _dispatch_line(raw.decode(errors="replace").rstrip("\r\n"))

        read_task = asyncio.ensure_future(_read_loop())
        await shutdown_event.wait()
    finally:
        if read_task is not None:
            read_task.cancel()
        # An extension blocked on a dialog would otherwise wait for a client
        # that is never going to answer.
        ui_context.cancel_pending()
        for unsub in unsubs:
            unsub()
        runtime.set_extension_error_callback(None)
        runtime.set_shutdown_handler(None)
        runtime.set_extension_ui_bridge(None)
        await _OUTPUT.drain()
        _OUTPUT.restore()

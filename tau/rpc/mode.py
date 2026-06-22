"""
RPC mode — JSON-lines stdin → stdout protocol.

Each line on stdin is a JSON object with a ``type`` field and an optional ``id``.
Each line on stdout is a JSON object (event or response).

Protocol matches the reference implementation (rpc-types.ts).
Commands are dispatched via :func:`run_rpc_mode`.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import sys
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tau.runtime.service import Runtime


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _write(obj: dict) -> None:
    """Write a JSON line to stdout immediately."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _serialize_event(event: object) -> dict:
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        return dataclasses.asdict(event)
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

    async def _dialog(self, payload: dict) -> Any:
        """Emit a dialog request and wait for the client response."""
        req_id = self._new_req_id()
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        _write({"type": "extension_ui_request", "id": req_id, **payload})
        try:
            return await fut
        finally:
            self._pending.pop(req_id, None)

    def _fire(self, payload: dict) -> None:
        """Emit a fire-and-forget notification (no client response expected)."""
        req_id = self._new_req_id()
        _write({"type": "extension_ui_request", "id": req_id, **payload})

    async def select(self, title: str, options: list[str]) -> str | None:
        return await self._dialog({"method": "select", "title": title, "options": options})

    async def confirm(self, title: str, message: str = "") -> bool:
        result = await self._dialog({"method": "confirm", "title": title, "message": message})
        if isinstance(result, dict):
            if result.get("cancelled"):
                return False
            return bool(result.get("confirmed", False))
        return bool(result)

    async def input(self, title: str, placeholder: str = "") -> str | None:
        return await self._dialog({"method": "input", "title": title, "placeholder": placeholder})

    async def editor(self, title: str, prefill: str = "") -> str | None:
        return await self._dialog({"method": "editor", "title": title, "prefill": prefill})

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
                if not text:
                    _err("'message' is required")
                    return
                streaming_behavior = cmd.get("streamingBehavior")
                agent = runtime.agent
                is_streaming = agent is not None and getattr(agent, "_running", False)

                if is_streaming and streaming_behavior is None:
                    _err("Agent is streaming; specify streamingBehavior: 'steer' or 'followUp'")
                    return

                if is_streaming and streaming_behavior == "steer":
                    from tau.message.types import TextContent, UserMessage

                    msg = UserMessage(contents=[TextContent(content=text)])
                    await agent._engine.steer(msg)  # type: ignore[union-attr]
                elif is_streaming and streaming_behavior == "followUp":
                    from tau.message.types import TextContent, UserMessage

                    msg = UserMessage(contents=[TextContent(content=text)])
                    await agent._engine.follow_up(msg)  # type: ignore[union-attr]
                else:
                    await runtime.invoke(text)
                _ok()

            case "steer":
                text = cmd.get("message", "")
                if not text:
                    _err("'message' is required")
                    return
                agent = runtime.agent
                if agent is None:
                    _err("No active agent")
                    return
                from tau.message.types import TextContent, UserMessage

                msg = UserMessage(contents=[TextContent(content=text)])
                await agent._engine.steer(msg)
                _ok()

            case "follow_up":
                text = cmd.get("message", "")
                if not text:
                    _err("'message' is required")
                    return
                agent = runtime.agent
                if agent is None:
                    _err("No active agent")
                    return
                from tau.message.types import TextContent, UserMessage

                msg = UserMessage(contents=[TextContent(content=text)])
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
                is_streaming = agent is not None and getattr(agent, "_running", False)
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
                    engine = getattr(agent, "_engine", None)
                    usage = getattr(engine, "context_usage", None) if engine else None
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
                    msg = entry.message
                    role = getattr(msg, "role", None)
                    if role is None:
                        continue
                    role_val = role.value if hasattr(role, "value") else str(role)
                    parts: list[str] = []
                    for c in getattr(msg, "contents", []):
                        content_str = getattr(c, "content", None)
                        if isinstance(content_str, str):
                            parts.append(content_str)
                    messages.append({"role": role_val, "text": "".join(parts)})
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


async def run_rpc_mode(runtime: Runtime) -> None:
    """Run the RPC mode loop — reads JSON lines from stdin, writes to stdout."""

    # Pending extension UI futures keyed by request id
    ui_pending: dict[str, asyncio.Future] = {}

    # ── Subscribe to agent events and stream them out ────────────────────────

    hook_names = [
        "agent_start",
        "agent_end",
        "turn_start",
        "turn_end",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
        "agent_error",
        "compaction_start",
        "compaction_end",
        "queue_update",
        "settled",
    ]

    async def on_event(event: object) -> None:
        _write(_serialize_event(event))

    hooks = runtime.hooks
    unsubs = [hooks.register(name, on_event) for name in hook_names if True]

    # ── Signal handling (SIGTERM / SIGHUP) ──────────────────────────────────
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _on_signal() -> None:
        agent = runtime.agent
        if agent is not None:
            cancel_fn = getattr(agent, "cancel", None) or getattr(agent, "abort", None)
            if callable(cancel_fn):
                cancel_fn()
        shutdown_event.set()

    import signal as _signal

    try:
        loop.add_signal_handler(_signal.SIGTERM, _on_signal)
        loop.add_signal_handler(_signal.SIGHUP, _on_signal)
    except (NotImplementedError, OSError):
        pass  # Windows

    # ── Announce ready ───────────────────────────────────────────────────────
    sm = runtime.session_manager
    _write(
        {
            "type": "ready",
            "sessionId": getattr(sm, "session_id", None) if sm is not None else None,
            "cwd": str(sm.cwd) if sm is not None else None,
        }
    )

    # ── Stdin reader ─────────────────────────────────────────────────────────
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
                line = raw.rstrip("\r\n")
                if not line:
                    continue
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
                    continue
                asyncio.ensure_future(_handle_command(obj, runtime, ui_pending))

        await _stdin_loop()
        for unsub in unsubs:
            unsub()
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
            line = raw.decode(errors="replace").rstrip("\r\n")
            if not line:
                continue
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
                continue
            asyncio.ensure_future(_handle_command(obj, runtime, ui_pending))

    read_task = asyncio.ensure_future(_read_loop())
    await shutdown_event.wait()
    read_task.cancel()

    for unsub in unsubs:
        unsub()

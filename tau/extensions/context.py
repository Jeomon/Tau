from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tau.agent.types import AgentPhase

if TYPE_CHECKING:
    from tau.message.types import AssistantMessage, LLMMessage
    from tau.modes.interactive.ui_context import UIContext
    from tau.runtime.service import Runtime
    from tau.session.manager import SessionManager
    from tau.session.types import SessionEntry
    from tau.settings.manager import SettingsManager

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session method option dataclasses
# ---------------------------------------------------------------------------


@dataclass
class NewSessionOptions:
    """Options for ExtensionContext.new_session()."""

    parent_session: str | None = None
    with_session: Callable[[ExtensionContext], Awaitable[None] | None] | None = None


@dataclass
class ForkOptions:
    """Options for ExtensionContext.fork()."""

    position: Literal["before", "at"] = "at"
    with_session: Callable[[ExtensionContext], Awaitable[None] | None] | None = None


@dataclass
class NavigateTreeOptions:
    """Options for ExtensionContext.navigate_tree()."""

    summarize: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass
class SwitchSessionOptions:
    """Options for ExtensionContext.switch_session()."""

    with_session: Callable[[ExtensionContext], Awaitable[None] | None] | None = None


class StaleExtensionContextError(RuntimeError):
    """Raised when an extension context outlives its runtime generation."""


class ExtensionContext:
    """
    Runtime context passed to extension command handlers and event handlers.

    Provides live session state rather than the static info available at
    registration time (which lives on ExtensionAPI).

    Handler signature:
        async def my_cmd(ctx: ExtensionContext, args: list[str]) -> None:
            print(ctx.cwd, ctx.model_id)
    """

    def __init__(
        self,
        cwd: Path,
        settings: SettingsManager | None,
        model_id: str,
        provider_id: str,
        session_manager: SessionManager | None = None,
        layout: object | None = None,
        runtime: Runtime | None = None,
        model_thinking: bool = False,
        llm: object | None = None,
    ) -> None:
        self._cwd = cwd
        self._settings = settings
        self._model_id = model_id
        self._provider_id = provider_id
        self._session_manager = session_manager
        self._layout = layout
        self._runtime_instance = runtime
        self._generation = getattr(runtime, "extension_generation", 0)
        self._model_thinking = model_thinking
        self._llm = llm

    @property
    def _runtime(self) -> Runtime | None:
        """Return the runtime after verifying this context is still current."""
        self._assert_active()
        return self._runtime_instance

    @_runtime.setter
    def _runtime(self, runtime: Runtime | None) -> None:
        """Bind a runtime and capture its current generation."""
        self._runtime_instance = runtime
        self._generation = getattr(runtime, "extension_generation", 0)

    def _assert_active(self) -> None:
        """Reject contexts captured before reload, replacement, or shutdown."""
        runtime = self._runtime_instance
        if runtime is None:
            return
        current = getattr(runtime, "extension_generation", self._generation)
        if current != self._generation:
            raise StaleExtensionContextError(
                "This extension context is stale after session replacement, "
                "extension reload, or runtime shutdown."
            )

    @classmethod
    def from_runtime(cls, runtime: Runtime) -> ExtensionContext:
        """Construct a context snapshot from the live runtime."""
        sm = runtime.session_manager
        agent = runtime.agent
        llm = agent._engine.llm if agent is not None else None
        return cls(
            cwd=sm.cwd if sm is not None else Path("."),
            settings=runtime.settings_manager,
            model_id=llm.model.id if llm is not None else "",
            provider_id=llm.provider_id if llm is not None else "",
            session_manager=sm,
            layout=getattr(runtime, "_layout", None),
            runtime=runtime,
            model_thinking=llm.model.thinking if llm is not None else False,
            llm=llm,
        )

    @property
    def cwd(self) -> Path:
        """Current working directory."""
        self._assert_active()
        return self._cwd

    @property
    def settings(self) -> SettingsManager | None:
        """Settings manager, or None if not available."""
        self._assert_active()
        return self._settings

    @property
    def model_id(self) -> str:
        """Active model identifier."""
        self._assert_active()
        return self._model_id

    @property
    def provider_id(self) -> str:
        """Active provider identifier."""
        self._assert_active()
        return self._provider_id

    @property
    def model_thinking(self) -> bool:
        """Whether the active model supports extended thinking."""
        self._assert_active()
        return self._model_thinking

    async def set_model(self, model_id: str, provider: str | None = None) -> bool:
        """Switch the active model, emitting ``model_select``.

        Returns ``True`` on success, ``False`` when there is no live agent or the
        model could not be constructed (unknown id, or missing credentials for
        its provider). Only safe to call when the agent is idle.

        Example::

            @tau.on("session_start")
            async def pin_model(event, ctx):
                await ctx.set_model("claude-sonnet-4-6")
        """
        if self._runtime is None:
            return False
        set_fn = getattr(self._runtime, "set_model", None)
        if set_fn is None:
            return False
        return bool(await set_fn(model_id, provider))

    @property
    def llm(self):
        """The active text LLM, or None outside a live agent session.

        Exposed so extensions can run their own model calls — for example, a custom
        ``before_compaction`` handler that summarises with a different prompt or model.
        Has an async ``invoke(LLMContext) -> list[LLMEvent]`` method.
        """
        self._assert_active()
        return self._llm

    @property
    def mode(self) -> str:
        """Execution mode: ``'tui'`` when running inside an interactive TUI session,
        ``'headless'`` otherwise.
        """
        self._assert_active()
        return "tui" if self._layout is not None else "headless"

    @property
    def session_entries(self) -> list[SessionEntry]:
        """All entries in the session file across every branch (the full tree).

        Use this when you need to inspect the complete history regardless of
        which branch the user is currently on.  Most extensions should prefer
        ``branch_entries`` instead.
        """
        self._assert_active()
        if self._session_manager is None:
            return []
        return self._session_manager.get_entries()

    @property
    def branch_entries(self) -> list[SessionEntry]:
        """Entries on the current branch only (root → current leaf, in order).

        This is the correct view for restoring per-branch extension state — it
        excludes entries that belong to abandoned branches, so you always see
        the data that is actually in scope for the current conversation.

        Example — restore saved state from the most recent custom entry::

            for entry in reversed(ctx.branch_entries):
                if isinstance(entry, CustomInfoEntry) and entry.custom_type == "my-ext":
                    restore(entry.data)
                    break
        """
        self._assert_active()
        if self._session_manager is None:
            return []
        return self._session_manager.get_branch()

    @property
    def ui(self) -> UIContext | None:
        """UI API. ``None`` only when there is no user-facing surface at all.

        In a TUI session this is the full interactive context. In RPC mode it is
        a protocol-backed stand-in: dialogs, notifications, status slots,
        widgets and titles reach the client, while anything needing a terminal
        grid degrades to a no-op. **Check ``ui.supports_components`` before
        calling ``custom``/``custom_inline``/``show_overlay``** — a non-``None``
        ``ui`` does not promise a surface to render on.
        """
        self._assert_active()
        if self._layout is None:
            bridge = self._ui_bridge
            if bridge is None:
                return None
            from tau.modes.rpc.ui_context import RpcUIContext

            return RpcUIContext(bridge)  # type: ignore[return-value]
        from tau.modes.interactive.ui_context import UIContext

        return UIContext(self._layout, settings=self._settings)  # type: ignore[arg-type]

    @property
    def has_ui(self) -> bool:
        """Exactly ``ctx.ui is not None`` — a convenience, not a second check.

        True for a TUI session and for an RPC client that answers
        ``extension_ui_request``; False in print/JSON modes. It says a user can
        be reached, *not* that components can be rendered — that question is
        ``ctx.ui.supports_components``.
        """
        return self.ui is not None

    @property
    def _ui_bridge(self) -> Any | None:
        """Non-TUI dialog backend installed by the host mode (RPC), if any."""
        return getattr(self._runtime_instance, "extension_ui_bridge", None)

    # ── Agent state ───────────────────────────────────────────────────────────

    @property
    def phase(self) -> AgentPhase:
        """Return the current observable agent phase."""
        if self._runtime is None:
            return AgentPhase.IDLE
        agent = getattr(self._runtime, "agent", None)
        return getattr(agent, "phase", AgentPhase.IDLE)

    def is_idle(self) -> bool:
        """Return True when the agent has no active lifecycle phase."""
        return self.phase is AgentPhase.IDLE

    @property
    def streaming_message(self) -> AssistantMessage | None:
        """Return the partial assistant message currently being streamed."""
        if self._runtime is None:
            return None
        agent = self._runtime.agent
        return agent.streaming_message if agent is not None else None

    @property
    def pending_tool_call_ids(self) -> frozenset[str]:
        """Return a snapshot of tool calls that have not finished."""
        if self._runtime is None:
            return frozenset()
        agent = self._runtime.agent
        return agent.pending_tool_call_ids if agent is not None else frozenset()

    @property
    def error_message(self) -> str | None:
        """Return the most recent engine error message."""
        if self._runtime is None:
            return None
        agent = self._runtime.agent
        return agent.error_message if agent is not None else None

    @property
    def queued_messages(self) -> dict[str, list[LLMMessage]]:
        """Return snapshots of the steering and follow-up queues."""
        if self._runtime is None:
            return {"steering": [], "followup": []}
        agent = self._runtime.agent
        return agent.queued_messages if agent is not None else {"steering": [], "followup": []}

    def abort(self) -> None:
        """Abort the current agent operation (no-op if idle)."""
        if self._runtime is None:
            return
        agent = getattr(self._runtime, "agent", None)
        if agent is None:
            return
        cancel_fn = getattr(agent, "cancel", None)
        if callable(cancel_fn):
            cancel_fn()

    def shutdown(self) -> None:
        """Gracefully shut down tau and exit.

        Modes that need to unwind cleanly (RPC flushes its protocol stream)
        register a handler and get a cooperative shutdown; everything else
        exits immediately.
        """
        runtime = self._runtime_instance
        request = getattr(runtime, "request_shutdown", None)
        if callable(request) and request():
            return

        import sys

        sys.exit(0)

    def get_context_usage(self) -> dict | None:
        """Return current context usage info or None if unavailable.

        Keys: ``tokens`` (int | None), ``context_window`` (int), ``percent`` (float | None).
        """
        if self._runtime is None:
            return None
        agent = getattr(self._runtime, "agent", None)
        if agent is None:
            return None
        usage = agent.get_context_usage()
        if usage is None:
            return None
        tokens = getattr(usage, "tokens", None)
        window = getattr(usage, "context_window", None) or 0
        percent = getattr(usage, "percent", None)
        return {"tokens": tokens, "context_window": window, "percent": percent}

    def compact(self, custom_instructions: str | None = None) -> None:
        """Trigger context compaction without waiting for completion."""
        import asyncio

        if self._runtime is None:
            return
        agent = getattr(self._runtime, "agent", None)
        if agent is None:
            return
        compact_fn = getattr(agent, "compact", None)
        if callable(compact_fn):
            import inspect

            result = compact_fn(custom_instructions=custom_instructions)
            if inspect.isawaitable(result):

                def _log_compact_error(task: asyncio.Task) -> None:
                    if not task.cancelled() and (exc := task.exception()):
                        _log.error("extension-triggered compaction failed", exc_info=exc)

                asyncio.ensure_future(result).add_done_callback(_log_compact_error)  # type: ignore[arg-type]

    def get_system_prompt(self) -> str:
        """Return the current effective system prompt."""
        if self._runtime is None:
            return ""
        agent = getattr(self._runtime, "agent", None)
        if agent is None:
            return ""
        return getattr(agent, "_system_prompt", "") or ""

    # ── Session control (command context) ─────────────────────────────────────

    async def wait_for_idle(self) -> None:
        """Suspend until the complete active invocation lifecycle finishes."""
        if self._runtime is None:
            return
        agent = self._runtime.agent
        if agent is None:
            return
        await agent.wait_for_idle()

    async def new_session(self, options: NewSessionOptions | None = None) -> dict:
        """Start a new session.  Returns ``{"cancelled": bool}``.

        ``options.with_session(ctx)`` runs inside the new session before the UI
        transitions — use it to inject initial messages via ``ctx.send_user_message``.
        """
        if self._runtime is None:
            return {"cancelled": True}
        opts = options or NewSessionOptions()
        await self._runtime.new_session(with_session=opts.with_session)
        return {"cancelled": False}

    async def fork(self, entry_id: str, options: ForkOptions | None = None) -> dict:
        """Fork from a specific entry.  Returns ``{"cancelled": bool}``."""
        if self._runtime is None:
            return {"cancelled": True}
        opts = options or ForkOptions()
        await self._runtime.fork_session(
            entry_id, position=opts.position, with_session=opts.with_session
        )
        return {"cancelled": False}

    async def navigate_tree(
        self,
        target_id: str,
        *,
        summarize: bool = False,
        custom_instructions: str | None = None,
        options: NavigateTreeOptions | None = None,
    ) -> dict:
        """Navigate the session tree.  Returns ``{"cancelled": bool}``."""
        if self._runtime is None:
            return {"cancelled": True}
        opts = options or NavigateTreeOptions(
            summarize=summarize, custom_instructions=custom_instructions
        )
        ok = await self._runtime.navigate_tree(
            target_id,
            summarize=opts.summarize,
            custom_instructions=opts.custom_instructions,
            replace_instructions=opts.replace_instructions,
            label=opts.label,
        )
        return {"cancelled": not ok}

    async def switch_session(
        self,
        session_path: str,
        options: SwitchSessionOptions | None = None,
    ) -> dict:
        """Switch to a different session file.  Returns ``{"cancelled": bool}``."""
        if self._runtime is None:
            return {"cancelled": True}
        opts = options or SwitchSessionOptions()
        await self._runtime.resume_session(Path(session_path), with_session=opts.with_session)
        return {"cancelled": False}

    async def send_message(self, content: str) -> None:
        """Append a plain text message to the current session as a user turn.

        Useful inside ``with_session`` callbacks to seed a new session with
        context before the agent processes its first real turn.
        """
        if self._runtime is None:
            return
        agent = getattr(self._runtime, "agent", None)
        if agent is None:
            return
        engine = getattr(agent, "_engine", None)
        if engine is None:
            return
        from tau.message.types import TextContent, UserMessage

        msg = UserMessage(contents=[TextContent(content=content)])
        await engine.steer(msg)

    async def send_user_message(
        self,
        content: str,
        deliver_as: Literal["steer", "follow_up"] = "steer",
        *,
        trigger_turn: bool = False,
    ) -> None:
        """Inject a user message into the active engine queue.

        ``deliver_as='steer'`` inserts immediately (mid-turn interception).
        ``deliver_as='follow_up'`` queues for after the current turn completes.
        When ``trigger_turn`` is true and the agent is idle, the message starts a
        new turn immediately instead of waiting in the queue.
        """
        if self._runtime is None:
            return
        agent = getattr(self._runtime, "agent", None)
        if agent is None:
            return
        if trigger_turn and agent.is_idle():
            await self._runtime.invoke(content, display=True)
            return
        engine = getattr(agent, "_engine", None)
        if engine is None:
            return
        from tau.message.types import TextContent, UserMessage

        msg = UserMessage(contents=[TextContent(content=content)])
        if deliver_as == "follow_up":
            await engine.follow_up(msg)
        else:
            await engine.steer(msg)

    async def is_project_trusted(self) -> bool | None:
        """Return the trust status of the current project directory.

        Checks in order:
        1. The ``project_trust`` hook (extensions can override).
        2. The settings manager's cached trust state (set at startup or by the prompt).
        3. Returns ``None`` if undecided.
        """
        if self._runtime is None:
            return None
        sm_session = getattr(self._runtime, "session_manager", None)
        cwd = str(sm_session.cwd) if sm_session is not None else ""

        from tau.hooks.runtime import ProjectTrustEvent, ProjectTrustResult

        results = await self._runtime.hooks.emit(ProjectTrustEvent(project_dir=cwd))
        for r in results:
            if isinstance(r, ProjectTrustResult) and r.trusted is not None:
                return r.trusted

        # Fall back to the settings manager's runtime trust state
        sm = getattr(self._runtime, "settings_manager", None)
        if sm is not None:
            return sm.is_project_trusted()
        return None

    def set_project_trusted(self, trusted: bool, *, remember: bool = False) -> None:
        """Set the project's trust state for this session.

        ``remember=True`` persists the decision to ``~/.tau/trust.json``
        so it survives future sessions.
        """
        sm = getattr(self._runtime, "settings_manager", None) if self._runtime else None
        if sm is None:
            return
        sm.set_project_trusted(trusted)
        if remember:
            from tau.trust.manager import trust_store

            sm_session = getattr(self._runtime, "session_manager", None)
            cwd = sm_session.cwd if sm_session is not None else None
            if cwd is not None:
                trust_store.set(str(cwd), trusted)

    async def reload(self) -> None:
        """Request an extension/resource reload.

        Requests made from an extension callback or while the agent is active
        are deferred until both the callback pipeline and agent lifecycle settle.
        """
        if self._runtime is None:
            return
        await self._runtime.reload_extensions()

    # ── Context inspection helpers ─────────────────────────────────────────────

    def has_pending_messages(self) -> bool:
        """Return True if any steering or follow-up messages are queued.

        Useful to avoid injecting a duplicate message when one is already
        waiting to be processed by the engine.
        """
        if self._runtime is None:
            return False
        agent = getattr(self._runtime, "agent", None)
        if agent is None:
            return False
        has_fn = getattr(agent, "has_pending_messages", None)
        if callable(has_fn):
            return bool(has_fn())
        return False

    @property
    def signal(self) -> object | None:
        """The current abort signal (``asyncio.Event``) while the agent is streaming.

        The event is *set* when the current operation has been aborted.
        Returns ``None`` when the agent is idle or unavailable.
        """
        if self._runtime is None:
            return None
        agent = getattr(self._runtime, "agent", None)
        if agent is None:
            return None
        return getattr(agent, "_signal", None)

    def get_system_prompt_options(self) -> dict:
        """Return metadata about how the active system prompt was assembled.

        Keys:
        - ``skills`` — list of skill names currently loaded
        - ``prompts`` — list of prompt template names currently loaded
        - ``tools`` — list of tool names registered with the engine
        - ``system_prompt_length`` — character length of the built prompt
        """
        from tau.prompts.registry import prompt_registry
        from tau.skills.registry import skill_registry

        skill_names = [s.name for s in skill_registry.list()]
        prompt_names = [p.name for p in prompt_registry.list()]

        tool_names: list[str] = []
        if self._runtime is not None:
            agent = getattr(self._runtime, "agent", None)
            if agent is not None:
                engine = getattr(agent, "_engine", None)
                if engine is not None:
                    tools = getattr(engine, "tools", None) or []
                    tool_names = [t.name for t in tools]

        prompt_len = len(self.get_system_prompt())

        return {
            "skills": skill_names,
            "prompts": prompt_names,
            "tools": tool_names,
            "system_prompt_length": prompt_len,
        }

    # ── Extension UI helpers ───────────────────────────────────────────────────

    async def select(self, title: str, options: list[str]) -> str | None:
        """Show an option picker to the user and return the selected string.

        Returns ``None`` if the user cancels or the app is running headless.

        Example::

            choice = await ctx.select("Pick an action", ["Summarize", "Explain", "Translate"])
            if choice == "Summarize":
                ...
        """
        import asyncio

        layout = self._layout
        if layout is None:
            bridge = self._ui_bridge
            if bridge is None:
                return None
            return await bridge.select(title, options)
        open_fn = getattr(layout, "open_extension_selector", None)
        if open_fn is None:
            return None

        loop = asyncio.get_event_loop()
        future: asyncio.Future[str | None] = loop.create_future()

        def _on_select(option: str) -> None:
            if not future.done():
                future.set_result(option)

        def _on_cancel() -> None:
            if not future.done():
                future.set_result(None)

        open_fn(title, options, _on_select, _on_cancel)
        return await future

    async def confirm(self, title: str, message: str = "") -> bool:
        """Show a Yes/No confirmation dialog and return True if the user picks Yes.

        Example::

            if await ctx.confirm("Delete branch?", "This cannot be undone."):
                ctx.notify("Branch deleted.")
        """
        if self._layout is None:
            bridge = self._ui_bridge
            if bridge is not None:
                return await bridge.confirm(title, message)
        prompt = f"{title}\n{message}" if message else title
        result = await self.select(prompt, ["Yes", "No"])
        return result == "Yes"

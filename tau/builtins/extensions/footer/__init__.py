"""Built-in footer status extension — git branch left, model/context right."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .git import GitBadge
from .model import ModelBadge

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI


_BRANCH_POLL_SECONDS = 2.0


def register(tau: ExtensionAPI) -> None:
    import asyncio

    from tau.tui.component import Row

    git_badge = GitBadge()
    model_badge = ModelBadge()
    row = Row([(git_badge, "left"), (model_badge, "right")])  # type: ignore[arg-type]
    watcher: dict[str, Any] = {"task": None, "ctx": None}

    def _request_render(ctx: Any) -> None:
        layout = getattr(ctx, "_layout", None)
        if layout is not None:
            layout._tui.request_render()

    async def _watch_branch() -> None:
        # The badge otherwise only refreshes on session events, so a branch
        # switched from another terminal while tau sits idle never shows up.
        from tau.extensions.context import StaleExtensionContextError

        while True:
            await asyncio.sleep(_BRANCH_POLL_SECONDS)
            ctx = watcher["ctx"]
            if ctx is None:
                continue
            try:
                if ctx.has_ui and git_badge.update(str(ctx.cwd)):
                    _request_render(ctx)
            except StaleExtensionContextError:
                # Session replaced or runtime shutting down — wait for the
                # fresh ctx delivered by the next session_start.
                watcher["ctx"] = None

    def _track(ctx: Any) -> None:
        watcher["ctx"] = ctx
        if watcher["task"] is None:
            watcher["task"] = asyncio.create_task(_watch_branch())

    def _stop_watcher() -> None:
        task = watcher["task"]
        if task is not None:
            task.cancel()
            watcher["task"] = None
        watcher["ctx"] = None

    def _mount(ctx: Any) -> None:
        footer = ctx._layout.footer
        if row not in footer.children:
            footer.add_child(row)
        git_badge.update(str(ctx.cwd))
        model_badge.update_from_ctx(ctx)
        _request_render(ctx)
        _track(ctx)

    @tau.on("tui_ready")
    def on_ready(event: Any, ctx: Any) -> None:
        _mount(ctx)

    @tau.on("extension_reloaded")
    def on_reloaded(event: Any, ctx: Any) -> None:
        # Extension reload builds a fresh row/badges in a new register() call,
        # but tui_ready never re-fires — without this the new row is never
        # added to the footer and the visible one is an orphan whose handlers
        # were unsubscribed, so the badge silently freezes (stale model /
        # effort / context% after any reload: project trust, /reload, ...).
        if ctx.has_ui:
            _mount(ctx)

    @tau.on("extension_unload")
    def on_unload(event: Any, ctx: Any) -> None:
        # Detach this instance's row — the replacement instance adds its own
        # on extension_reloaded. Leaving it behind renders stale badges.
        _stop_watcher()
        layout = getattr(ctx, "_layout", None)
        if layout is not None:
            layout.footer.remove_child(row)

    @tau.on("runtime_stop")
    def on_runtime_stop(event: Any, ctx: Any) -> None:
        _stop_watcher()

    @tau.on("session_start")
    def on_session_start(event: Any, ctx: Any) -> None:
        if ctx.has_ui:
            git_badge.update(str(ctx.cwd))
            model_badge.update_from_ctx(ctx)
            _request_render(ctx)
            _track(ctx)

    @tau.on("model_select")
    def on_model_select(event: Any, ctx: Any) -> None:
        if not ctx.has_ui:
            return
        model = getattr(event, "model", None)
        if model is not None:
            model_badge.set_model(
                getattr(model, "id", "") or "",
                getattr(model, "provider", "") or "",
                bool(getattr(model, "thinking", False)),
            )
        # The new model usually has a different context window, so the usage %
        # changes even though the token count didn't — refresh it immediately
        # instead of waiting for the next turn.
        model_badge.update_context_from_ctx(ctx)
        _request_render(ctx)

    @tau.on("agent_start")
    def on_agent_start(event: Any, ctx: Any) -> None:
        # Fires right after the user's message is appended to the session but
        # before the LLM call starts — without this, the badge kept showing
        # the previous turn's usage until the first response came back, so a
        # large pasted message wouldn't show up in the percentage until the
        # model had already replied.
        if ctx.has_ui:
            model_badge.update_context_from_ctx(ctx)
            _request_render(ctx)

    @tau.on("message_update")
    def on_message_update(event: Any, ctx: Any) -> None:
        if not ctx.has_ui:
            return
        from tau.message.types import TextContent, ThinkingContent

        msg = getattr(event, "message", None)
        if msg is None:
            return
        contents = getattr(msg, "contents", [])
        char_count = sum(
            len(item.content)
            for item in contents
            if isinstance(item, (TextContent, ThinkingContent))
        )
        model_badge.set_live_estimate(char_count // 4)
        _request_render(ctx)

    @tau.on("thinking_level_select")
    def on_thinking_level_select(event: Any, ctx: Any) -> None:
        if not ctx.has_ui:
            return
        model_badge.set_thinking_level(getattr(event, "level", None))
        _request_render(ctx)

    @tau.on("after_provider_response")
    def on_after_provider_response(event: Any, ctx: Any) -> None:
        if not ctx.has_ui:
            return
        model_badge.update_context_from_response(getattr(event, "response", None), ctx)
        _request_render(ctx)

    @tau.on("settled")
    def on_settled(event: Any, ctx: Any) -> None:
        if ctx.has_ui:
            git_badge.update(str(ctx.cwd))
            model_badge.update_context_from_ctx(ctx)
            _request_render(ctx)

    @tau.on("message_end")
    def on_message_end(event: Any, ctx: Any) -> None:
        if ctx.has_ui:
            git_badge.update(str(ctx.cwd))
            model_badge.update_context_from_ctx(ctx)
            _request_render(ctx)

    @tau.on("compaction_end")
    def on_compaction_end(event: Any, ctx: Any) -> None:
        if ctx.has_ui:
            model_badge.update_context_from_ctx(ctx)
            _request_render(ctx)

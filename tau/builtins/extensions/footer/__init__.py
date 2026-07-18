"""Built-in footer status extension — git branch left, model/context right."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .git import GitBadge
from .model import ModelBadge

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI


def register(tau: ExtensionAPI) -> None:
    from tau.tui.component import Row

    git_badge = GitBadge()
    model_badge = ModelBadge()
    row = Row([(git_badge, "left"), (model_badge, "right")])  # type: ignore[arg-type]

    def _request_render(ctx: Any) -> None:
        layout = getattr(ctx, "_layout", None)
        if layout is not None:
            layout._tui.request_render()

    @tau.on("tui_ready")
    def on_ready(event: Any, ctx: Any) -> None:
        ctx._layout.footer.add_child(row)
        git_badge.update(str(ctx.cwd))
        model_badge.update_from_ctx(ctx)
        _request_render(ctx)

    @tau.on("session_start")
    def on_session_start(event: Any, ctx: Any) -> None:
        if ctx.has_ui:
            git_badge.update(str(ctx.cwd))
            model_badge.update_from_ctx(ctx)
            _request_render(ctx)

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

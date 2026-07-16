from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.message.types import Role, TextContent
from tau.session.types import MessageEntry

if TYPE_CHECKING:
    from tau.session.types import SessionTreeNode

    from tau.runtime.service import Runtime


def _snippet(entry: MessageEntry, max_chars: int = 60) -> str:
    """Short single-line preview of a user message entry, for a tree node label."""
    contents = getattr(entry.message, "contents", [])
    text = "".join(c.content for c in contents if isinstance(c, TextContent))
    text = " ".join(text.split())
    if not text:
        return "(no text)"
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


def _filter_message_tree(nodes: list[SessionTreeNode]) -> list[dict]:
    """Reduce a full session tree to just user-message branch points.

    Non-message bookkeeping entries (model changes, thinking-level changes,
    compaction, labels, ...) are skipped, promoting their children up to the
    nearest user-message ancestor so the branch shape is preserved.
    """
    result: list[dict] = []
    for node in nodes:
        entry = node.entry
        if isinstance(entry, MessageEntry) and getattr(entry.message, "role", None) == Role.USER:
            result.append(
                {
                    "id": entry.id,
                    "label": _snippet(entry),
                    "children": _filter_message_tree(node.children),
                }
            )
        else:
            result.extend(_filter_message_tree(node.children))
    return result


class BranchNavigatorDialog:
    """Session fork/branch tree navigator, opened from the top bar."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._dialog: Any | None = None
        self._container: Any | None = None

    def render(self) -> None:
        """Build the (initially hidden) dialog."""
        with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[90vw] tau-settings-card"):
            ui.label("Branches").classes("w-full text-sm font-semibold text-[var(--text)] px-1")
            self._container = ui.column().classes(
                "w-full min-w-0 items-stretch gap-1 max-h-[65vh] overflow-auto"
            )
        self._dialog = dialog

    def open(self) -> None:
        """Refresh and show the dialog."""
        self._render_tree()
        if self._dialog is not None:
            self._dialog.open()

    def _render_tree(self) -> None:
        if self._container is None:
            return
        self._container.clear()

        sm = self._runtime.session_manager
        nodes = _filter_message_tree(sm.get_tree())
        current_leaf = sm.get_leaf_id()

        with self._container:
            if not nodes:
                ui.label("No branch points in this session yet.").classes(
                    "text-xs text-[var(--text-dim)] px-1"
                )
                return
            ui.tree(
                nodes,
                node_key="id",
                label_key="label",
                on_select=self._on_select,
            ).classes("w-full text-xs").props(
                f'selected="{current_leaf}"' if current_leaf else ""
            )

    async def _on_select(self, event: Any) -> None:
        target_id = getattr(event, "value", None)
        if not target_id:
            return
        ok = await self._runtime.navigate_tree(target_id, summarize=False)
        if ok:
            if self._dialog is not None:
                self._dialog.close()
        else:
            ui.notify("Could not switch branches.", type="negative")

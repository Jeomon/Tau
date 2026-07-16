from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from tau.builtins.tools.utils import detect_image_mime, looks_like_binary

if TYPE_CHECKING:
    from tau.runtime.service import Runtime

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
}
_MAX_NODES = 3000
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024
_WATCH_INTERVAL_S = 1.5

_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".sh": "bash",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".sql": "sql",
}


def _guess_language(suffix: str) -> str:
    return _LANGUAGE_BY_SUFFIX.get(suffix.lower(), "plaintext")


_ICON_BY_SUFFIX = {
    ".py": "code",
    ".js": "code",
    ".jsx": "code",
    ".ts": "code",
    ".tsx": "code",
    ".go": "code",
    ".rs": "code",
    ".rb": "code",
    ".java": "code",
    ".c": "code",
    ".cpp": "code",
    ".sh": "terminal",
    ".json": "data_object",
    ".yml": "settings",
    ".yaml": "settings",
    ".toml": "settings",
    ".md": "article",
    ".txt": "article",
    ".html": "html",
    ".css": "css",
    ".sql": "storage",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".svg": "image",
    ".pdf": "picture_as_pdf",
}
_DEFAULT_FILE_ICON = "insert_drive_file"
_FOLDER_ICON = "folder"


def _file_icon(suffix: str) -> str:
    return _ICON_BY_SUFFIX.get(suffix.lower(), _DEFAULT_FILE_ICON)


def _build_tree(root: Path) -> list[dict]:
    """Build a `ui.tree` node list rooted at `root`, skipping noisy directories."""
    count = 0

    def walk(dir_path: Path) -> list[dict]:
        nonlocal count
        nodes: list[dict] = []
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return nodes
        for entry in entries:
            if count >= _MAX_NODES or entry.name in _SKIP_DIRS:
                continue
            count += 1
            if entry.is_dir():
                nodes.append(
                    {"id": str(entry), "label": entry.name, "icon": _FOLDER_ICON, "children": walk(entry)}
                )
            else:
                nodes.append(
                    {"id": str(entry), "label": entry.name, "icon": _file_icon(entry.suffix)}
                )
        return nodes

    return walk(root)


class FileExplorerPanel:
    """Toggleable file tree + preview panel, scoped to the session's cwd."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._root = runtime.session_manager.cwd
        self._panel: Any | None = None
        self._tree_container: Any | None = None
        self._tab_bar_container: Any | None = None
        self._viewer_container: Any | None = None
        self._visible = False
        self._open_tabs: list[Path] = []
        self._active_tab: Path | None = None
        self._live_row: Any | None = None
        self._live_dot: Any | None = None
        self._live_label: Any | None = None
        self._watch_timer: Any | None = None
        self._watch_path: Path | None = None
        self._watch_mtime: float | None = None

    def render(self) -> None:
        """Render the (initially collapsed) panel."""
        with ui.column().classes("h-full min-h-0 gap-0 tau-file-panel").style("width: 0px") as panel:
            with ui.row().classes("w-full items-center justify-between p-2 tau-sidebar-header"):
                ui.label("Files").classes("text-sm font-semibold text-[var(--text)]")
                refresh_btn = ui.button(icon="refresh", on_click=self._refresh_tree).props(
                    "flat dense round size=sm"
                )
                refresh_btn.style("color: var(--text-muted) !important;")
            with (
                ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"),
                ui.scroll_area().classes("w-full h-full"),
            ):
                self._tree_container = ui.column().classes("w-full min-w-0 items-stretch gap-0 p-1")
            with ui.column().classes("w-full h-1/2 min-h-0 overflow-hidden tau-file-viewer"):
                self._tab_bar_container = ui.row().classes(
                    "w-full gap-0 overflow-x-auto flex-nowrap tau-tab-bar"
                )
                with ui.row().classes(
                    "w-full items-center gap-1 px-2 py-1 tau-file-live-row"
                ) as live_row:
                    self._live_dot = ui.element("span").classes("tau-live-dot")
                    self._live_label = ui.label("").classes("text-[10px] text-[var(--text-dim)]")
                live_row.set_visibility(False)
                self._live_row = live_row
                with (
                    ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"),
                    ui.scroll_area().classes("w-full h-full"),
                ):
                    viewer = ui.column().classes("w-full gap-1 p-2")
                    with viewer:
                        ui.label("Select a file to preview").classes(
                            "text-xs text-[var(--text-dim)] px-1"
                        )
                    self._viewer_container = viewer
        self._panel = panel
        self._refresh_tree()
        ui.context.client.on_disconnect(self._stop_watch)

    def toggle(self) -> None:
        """Show or hide the panel, sliding its width in/out."""
        self._visible = not self._visible
        if self._panel is not None:
            self._panel.style(f"width: {'340px' if self._visible else '0px'}")

    def _refresh_tree(self) -> None:
        if self._tree_container is None:
            return
        self._tree_container.clear()
        with self._tree_container:
            nodes = _build_tree(self._root)
            ui.tree(nodes, node_key="id", label_key="label", on_select=self._on_select).classes(
                "w-full text-xs"
            )

    def _on_select(self, event: Any) -> None:
        node_id = getattr(event, "value", None)
        if not node_id:
            return
        path = Path(node_id)
        if path.is_file():
            self._open_tab(path)

    def _open_tab(self, path: Path) -> None:
        """Open `path` as a tab (or switch to it if already open)."""
        if path not in self._open_tabs:
            self._open_tabs.append(path)
        self._active_tab = path
        self._render_tabs()
        self._preview_file(path)

    def _close_tab(self, path: Path) -> None:
        if path not in self._open_tabs:
            return
        index = self._open_tabs.index(path)
        self._open_tabs.remove(path)
        if self._active_tab == path:
            if self._open_tabs:
                self._active_tab = self._open_tabs[min(index, len(self._open_tabs) - 1)]
            else:
                self._active_tab = None
        self._render_tabs()
        if self._active_tab is not None:
            self._preview_file(self._active_tab)
        else:
            self._clear_viewer()

    def _render_tabs(self) -> None:
        if self._tab_bar_container is None:
            return
        self._tab_bar_container.clear()
        with self._tab_bar_container:
            for path in self._open_tabs:
                is_active = path == self._active_tab
                classes = (
                    "items-center flex-nowrap flex-shrink-0 gap-1 px-2 py-1 cursor-pointer tau-file-tab"
                    + (" tau-active" if is_active else "")
                )
                with ui.row().classes(classes).on("click", lambda p=path: self._open_tab(p)):
                    ui.label(path.name).classes("text-xs truncate max-w-[110px] text-[var(--text)]")
                    close_btn = ui.icon("close").classes("text-xs tau-file-tab-close")
                    close_btn.on("click.stop", lambda p=path: self._close_tab(p))

    def _clear_viewer(self) -> None:
        self._stop_watch()
        if self._viewer_container is None:
            return
        self._viewer_container.clear()
        with self._viewer_container:
            ui.label("Select a file to preview").classes("text-xs text-[var(--text-dim)] px-1")

    def _stop_watch(self) -> None:
        """Stop polling the previously-open file for on-disk changes."""
        if self._watch_timer is not None:
            self._watch_timer.cancel()
            self._watch_timer = None
        self._watch_path = None
        self._watch_mtime = None
        if self._live_row is not None:
            self._live_row.set_visibility(False)

    def _set_live_indicator(self, *, live: bool) -> None:
        if self._live_dot is not None:
            self._live_dot.classes(
                remove="tau-live-dot-on tau-live-dot-off",
                add="tau-live-dot-on" if live else "tau-live-dot-off",
            )
        if self._live_label is not None:
            self._live_label.text = "live" if live else "static"

    def _start_watch(self, path: Path) -> None:
        """Poll `path`'s mtime so an on-disk change (e.g. the agent editing it)
        refreshes the open preview automatically, mirroring pi-web's file
        watch indicator."""
        self._watch_path = path
        try:
            self._watch_mtime = path.stat().st_mtime
            live = True
        except OSError:
            self._watch_mtime = None
            live = False
        if self._live_row is not None:
            self._live_row.set_visibility(True)
        self._set_live_indicator(live=live)
        self._watch_timer = ui.timer(_WATCH_INTERVAL_S, lambda: self._check_watch(path))

    def _check_watch(self, path: Path) -> None:
        # A stale timer from a since-replaced tab/watch — let it die quietly.
        if path != self._watch_path or path != self._active_tab:
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._set_live_indicator(live=False)
            return
        self._set_live_indicator(live=True)
        if self._watch_mtime is not None and mtime != self._watch_mtime:
            self._preview_file(path)

    def _preview_file(self, path: Path) -> None:
        self._stop_watch()
        if self._viewer_container is None:
            return
        self._viewer_container.clear()
        with self._viewer_container:
            try:
                raw: bytes | None = path.read_bytes()
            except OSError as e:
                ui.label(f"Cannot read file: {e}").classes("text-xs text-[var(--text-dim)]")
                raw = None

            if raw is not None:
                mime = detect_image_mime(raw)
                if mime is not None:
                    b64 = base64.b64encode(raw).decode()
                    ui.image(f"data:{mime};base64,{b64}").classes("w-full")
                elif looks_like_binary(raw):
                    ui.label("Binary file — no preview available.").classes(
                        "text-xs text-[var(--text-dim)]"
                    )
                elif len(raw) > _MAX_PREVIEW_BYTES:
                    ui.label(f"File too large to preview ({len(raw)} bytes).").classes(
                        "text-xs text-[var(--text-dim)]"
                    )
                else:
                    text = raw.decode("utf-8", errors="replace")
                    ui.code(text, language=_guess_language(path.suffix)).classes("w-full text-xs")
        self._start_watch(path)

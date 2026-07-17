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



# `o_` is Quasar's prefix for the "Outlined" Material icon variant (maps to
# the bundled material-icons-outlined font) — matches pi-web's thin, flat,
# monochrome file icons much more closely than the default filled glyphs.
_ICON_BY_SUFFIX = {
    ".py": "o_code",
    ".js": "o_code",
    ".jsx": "o_code",
    ".ts": "o_code",
    ".tsx": "o_code",
    ".go": "o_code",
    ".rs": "o_code",
    ".rb": "o_code",
    ".java": "o_code",
    ".c": "o_code",
    ".cpp": "o_code",
    ".sh": "o_terminal",
    ".json": "o_data_object",
    ".yml": "o_settings",
    ".yaml": "o_settings",
    ".toml": "o_settings",
    ".md": "o_article",
    ".txt": "o_article",
    ".html": "o_html",
    ".css": "o_css",
    ".sql": "o_storage",
    ".png": "o_image",
    ".jpg": "o_image",
    ".jpeg": "o_image",
    ".gif": "o_image",
    ".webp": "o_image",
    ".svg": "o_image",
    ".pdf": "o_picture_as_pdf",
}
_DEFAULT_FILE_ICON = "o_insert_drive_file"
_FOLDER_ICON = "o_folder"


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
    """Preview-only file panel (tabs + viewer), scoped to the session's cwd.

    Browsing lives entirely in the always-visible Explorer tree in the left
    sidebar now (SessionSidebar, reusing `_build_tree` from this module) —
    this panel used to have its own duplicate tree plus a "Files" top-bar
    toggle, both removed. It now just opens on demand when a file is picked
    from the sidebar and auto-hides once every tab is closed, so it only
    ever takes up space when there's something to preview.
    """

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._root = runtime.session_manager.cwd
        self._panel: Any | None = None
        self._tab_bar_container: Any | None = None
        self._viewer_container: Any | None = None
        self._visible = False
        self._open_tabs: list[Path] = []
        self._active_tab: Path | None = None
        self._watch_timer: Any | None = None
        self._watch_path: Path | None = None
        self._watch_mtime: float | None = None

    def render(self) -> None:
        """Render the (initially collapsed) panel."""
        with ui.column().classes("h-full min-h-0 gap-0 tau-file-panel").style("width: 0px") as panel:
            self._tab_bar_container = ui.row().classes(
                "w-full gap-0 overflow-x-auto flex-nowrap tau-tab-bar"
            )
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
        ui.context.client.on_disconnect(self._stop_watch)

    def toggle(self) -> None:
        """Show or hide the panel, sliding its width in/out."""
        self._visible = not self._visible
        if self._panel is not None:
            # 42vw / min 300px matches pi-web's right-panel-container spec
            # (globals.css) — previously a fixed 340px, which cramped long
            # lines into constant horizontal scrolling.
            self._panel.style(
                f"width: {'max(42vw, 300px)' if self._visible else '0px'};"
                f" min-width: {'300px' if self._visible else '0px'};"
            )

    def open_file(self, path: Path) -> None:
        """Open `path` as a tab and ensure the panel is visible.

        Driven by the sidebar's Explorer tree, which owns browsing —
        this class only owns the tab bar and the preview.
        """
        if not self._visible:
            self.toggle()
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
            # Nothing left to preview — collapse back out of the way instead
            # of leaving an empty panel taking up space.
            if self._visible:
                self.toggle()

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

    def _start_watch(self, path: Path) -> None:
        """Poll `path`'s mtime so an on-disk change (e.g. the agent editing it)
        refreshes the open preview automatically."""
        self._watch_path = path
        try:
            self._watch_mtime = path.stat().st_mtime
        except OSError:
            self._watch_mtime = None
        self._watch_timer = ui.timer(_WATCH_INTERVAL_S, lambda: self._check_watch(path))

    def _check_watch(self, path: Path) -> None:
        # A stale timer from a since-replaced tab/watch — let it die quietly.
        if path != self._watch_path or path != self._active_tab:
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
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

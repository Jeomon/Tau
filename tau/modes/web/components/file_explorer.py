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
        self._viewer_container: Any | None = None
        self._visible = False

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
                self._tree_container = ui.column().classes("w-full gap-0 p-1")
            with (
                ui.column().classes("w-full h-1/2 min-h-0 overflow-hidden tau-file-viewer"),
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
            self._preview_file(path)

    def _preview_file(self, path: Path) -> None:
        if self._viewer_container is None:
            return
        self._viewer_container.clear()
        with self._viewer_container:
            try:
                label = str(path.relative_to(self._root))
            except ValueError:
                label = str(path)
            ui.label(label).classes("w-full truncate text-xs font-medium text-[var(--text)]")

            try:
                raw = path.read_bytes()
            except OSError as e:
                ui.label(f"Cannot read file: {e}").classes("text-xs text-[var(--text-dim)]")
                return

            mime = detect_image_mime(raw)
            if mime is not None:
                b64 = base64.b64encode(raw).decode()
                ui.image(f"data:{mime};base64,{b64}").classes("w-full")
                return

            if looks_like_binary(raw):
                ui.label("Binary file — no preview available.").classes(
                    "text-xs text-[var(--text-dim)]"
                )
                return

            if len(raw) > _MAX_PREVIEW_BYTES:
                ui.label(f"File too large to preview ({len(raw)} bytes).").classes(
                    "text-xs text-[var(--text-dim)]"
                )
                return

            text = raw.decode("utf-8", errors="replace")
            ui.code(text, language=_guess_language(path.suffix)).classes("w-full text-xs")

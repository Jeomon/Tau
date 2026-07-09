"""
LSP service — mirrors opencode's lsp/index.ts.

Manages a pool of LSPClients (one per server_id+root pair).
Lazily spawns servers on first file access and deduplicates
by (server_id, root) so the same server isn't started twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .client import LSPClient
from .server import BUILTIN_SERVERS, ServerDefinition

logger = logging.getLogger(__name__)

# Key: (server_id, root)
_ClientKey = tuple[str, str]

# Symbol kinds worth surfacing in workspaceSymbol results (mirrors opencode)
_USEFUL_SYMBOL_KINDS = {
    5,  # Class
    6,  # Method
    7,  # Property — sometimes useful
    9,  # Constructor
    10,  # Enum
    11,  # Interface
    12,  # Function
    13,  # Variable
    14,  # Constant
    23,  # Struct
    26,  # TypeParameter
}


@dataclass
class ServerStatus:
    id: str
    root: str
    status: Literal["connected", "error"]


class LSP:
    def __init__(self, cwd: Path, extra_servers: list[ServerDefinition] | None = None) -> None:
        # Resolve to an absolute path: server roots and document URIs are built
        # via Path(...).as_uri(), which raises "relative path can't be expressed
        # as a file URI" for any relative path. The prewarm/index walk derives
        # file paths from self._cwd, so a relative cwd here breaks every spawn.
        self._cwd = str(Path(cwd).resolve())
        self._servers: dict[str, ServerDefinition] = {s.id: s for s in BUILTIN_SERVERS}
        for s in extra_servers or []:
            self._servers[s.id] = s

        self._clients: dict[_ClientKey, LSPClient] = {}
        self._spawning: dict[_ClientKey, asyncio.Task[LSPClient | None]] = {}
        # key → monotonic timestamp after which a respawn attempt is allowed
        self._broken: dict[_ClientKey, float] = {}

    # ── Settings override ─────────────────────────────────────────────────────

    def apply_config(self, config: dict) -> None:
        """Apply settings from tau.config (extension settings.json block)."""
        servers_cfg: dict = config.get("servers", {})
        for name, item in servers_cfg.items():
            if not item.get("enabled", True):
                self._servers.pop(name, None)
                continue
            existing = self._servers.get(name)
            cmd = item.get("command")
            if cmd:
                from .server import _nearest_root
                from .types import ServerDefinition

                ext = item.get("extensions", getattr(existing, "extensions", []))
                self._servers[name] = ServerDefinition(
                    id=name,
                    extensions=ext,
                    command=cmd,
                    root_finder=_nearest_root([]),
                    initialization=item.get("initialization", {}),
                    env=item.get("env", {}),
                )

    # ── Client acquisition ────────────────────────────────────────────────────

    def status(self) -> list[ServerStatus]:
        """Return connected LSP servers — id, root (relative to cwd), and status."""
        out: list[ServerStatus] = []
        now = asyncio.get_event_loop().time()
        for (server_id, root), client in self._clients.items():
            _ = client
            try:
                rel_root = str(Path(root).relative_to(self._cwd))
            except ValueError:
                rel_root = root
            out.append(ServerStatus(id=server_id, root=rel_root, status="connected"))
        for key, retry_after in self._broken.items():
            server_id, root = key
            try:
                rel_root = str(Path(root).relative_to(self._cwd))
            except ValueError:
                rel_root = root
            remaining = max(0.0, retry_after - now)
            status: Literal["connected", "error"] = "error"
            _ = remaining  # exposed via logs; status field stays "error"
            out.append(ServerStatus(id=server_id, root=rel_root, status=status))
        return out

    async def get_clients(self, file: str) -> list[LSPClient]:
        # Boundary check — never activate LSP for files outside the project
        try:
            Path(file).resolve().relative_to(Path(self._cwd).resolve())
        except ValueError:
            return []

        ext = Path(file).suffix
        results: list[LSPClient] = []

        for server in self._servers.values():
            if not server.enabled:
                continue
            if server.extensions and ext not in server.extensions:
                continue
            if not server.is_available():
                continue

            root = await server.root_finder(file)
            if root is None:
                continue

            key: _ClientKey = (server.id, root)

            # Check backoff: skip if still in cool-down period
            retry_after = self._broken.get(key)
            if retry_after is not None:
                if asyncio.get_event_loop().time() < retry_after:
                    continue
                del self._broken[key]  # backoff expired — allow respawn

            # Already running — but check if the process has since died
            if key in self._clients:
                client = self._clients[key]
                if client._process.returncode is not None:
                    del self._clients[key]
                    self._broken[key] = asyncio.get_event_loop().time() + 10.0
                    logger.warning(
                        "lsp: %s crashed (exit %d), will retry in 10s",
                        server.id,
                        client._process.returncode,
                    )
                    continue
                results.append(client)
                continue

            # In-flight spawn — wait for it
            if key in self._spawning:
                client = await self._spawning[key]
                if client:
                    results.append(client)
                continue

            # Spawn a new client
            task = asyncio.create_task(self._spawn(server, root, key))
            self._spawning[key] = task
            task.add_done_callback(lambda _t, k=key: self._spawning.pop(k, None))

            client = await task
            if client:
                results.append(client)

        return results

    async def _spawn(
        self, server: ServerDefinition, root: str, key: _ClientKey
    ) -> LSPClient | None:
        try:
            client = await LSPClient.create(
                server_id=server.id,
                command=server.command,
                root=root,
                initialization=server.initialization,
                env=server.env or None,
            )
            self._clients[key] = client

            # Handle workspace/applyEdit server-initiated requests (triggered by executeCommand)
            async def _handle_apply_edit(params: dict) -> dict:
                edit = params.get("edit", {})
                await self.apply_workspace_edit(edit)
                return {"applied": True}

            client.on_server_request("workspace/applyEdit", _handle_apply_edit)
            logger.info("lsp: spawned %s at %s", server.id, root)
            return client
        except Exception as exc:
            # Exponential backoff: 10s → 30s → 90s → 270s, capped at 5 minutes
            prev = self._broken.get(key, 0.0)
            now = asyncio.get_event_loop().time()
            prev_delay = prev - now if prev > now else 0.0
            backoff = min(max(prev_delay * 3.0, 10.0), 300.0)
            self._broken[key] = now + backoff
            logger.warning("lsp: failed to spawn %s (retry in %.0fs): %s", server.id, backoff, exc)
            return None

    async def has_clients(self, file: str) -> bool:
        """Whether any installed server *can* handle this file type.

        This reports capability, not live-connection state. A server that is
        installed with a resolvable project root qualifies even if a recent
        spawn attempt is still in backoff — get_clients() enforces the backoff
        and respawns on access, so a transient spawn failure no longer surfaces
        to the user as "No LSP server available".
        """
        ext = Path(file).suffix
        now = asyncio.get_event_loop().time()
        for server in self._servers.values():
            if not server.enabled or not server.is_available():
                continue
            if server.extensions and ext not in server.extensions:
                continue
            root = await server.root_finder(file)
            if root is None:
                continue
            # A connected client whose process has since exited: move it to
            # _broken so get_clients() respawns it on the next access.
            key: _ClientKey = (server.id, root)
            if key in self._clients and self._clients[key]._process.returncode is not None:
                del self._clients[key]
                self._broken[key] = now + 10.0
                logger.warning("lsp: %s exited unexpectedly", server.id)
            return True
        return False

    async def touch_file(self, file: str, wait_for_diagnostics: bool = False) -> None:
        clients = await self.get_clients(file)
        await asyncio.gather(
            *[c.open_file(file, wait_for_diagnostics) for c in clients], return_exceptions=True
        )

    async def save_file(self, path: str) -> None:
        """Send willSave, apply willSaveWaitUntil edits, then didSave."""
        clients = await self.get_clients(path)
        if not clients:
            return

        # willSave notification (fire-and-forget)
        await asyncio.gather(*[c.will_save(path) for c in clients], return_exceptions=True)

        # willSaveWaitUntil — collect pre-save edits (e.g. organize-imports)
        pre_edits: list[dict] = []
        results = await asyncio.gather(
            *[c.will_save_wait_until(path) for c in clients], return_exceptions=True
        )
        for r in results:
            if isinstance(r, list):
                pre_edits.extend(r)

        if pre_edits:
            try:
                content = Path(path).read_text(encoding="utf-8")
                Path(path).write_text(self._apply_text_edits(content, pre_edits), encoding="utf-8")
                await self.touch_file(path, wait_for_diagnostics=False)
            except Exception as exc:
                logger.warning("save_file: pre-save edits for %s: %s", Path(path).name, exc)

        # didSave
        await asyncio.gather(*[c.save_file(path) for c in clients], return_exceptions=True)

    async def close_file(self, path: str) -> None:
        """Send textDocument/didClose to all clients tracking this file."""
        clients = await self.get_clients(path)
        await asyncio.gather(*[c.close_file(path) for c in clients], return_exceptions=True)

    async def did_change_configuration(self, settings: dict | None = None) -> None:
        """Notify all running language servers that configuration has changed."""
        await asyncio.gather(
            *[c.did_change_configuration(settings) for c in self._clients.values()],
            return_exceptions=True,
        )

    async def eager(self, server_ids: list[str] | None = None) -> None:
        """Pre-warm servers by spawning them immediately.

        server_ids=None (default, also when eager: []) →
                           auto-detect: walk project via pygments + gitignore,
                           start only servers whose languages are present
        server_ids=[...] → explicit: start exactly the listed server IDs

        Detection, availability checks (``shutil.which``), and the directory
        walk are all filesystem-heavy and synchronous; run them on a worker
        thread so they don't stall the event loop (and the just-launched
        TUI's render loop, which shares it) for the ~500ms this can take on a
        large tree.
        """
        files_to_touch = await asyncio.to_thread(self._resolve_eager_files, server_ids)
        for file_path in files_to_touch:
            asyncio.create_task(self.touch_file(file_path, wait_for_diagnostics=False))

    def _resolve_eager_files(self, server_ids: list[str] | None) -> list[str]:
        """Detect available servers and find one file per server to open, in a
        single directory walk (rather than one detection walk plus a separate
        re-walk per matched server)."""
        if server_ids is not None:
            targets = [
                self._servers[sid]
                for sid in server_ids
                if sid in self._servers and self._servers[sid].enabled and self._servers[sid].is_available()
            ]
            return self._find_first_matching_files(targets)
        return self._detect_and_resolve_servers()

    def _find_first_matching_files(self, targets: list[ServerDefinition]) -> list[str]:
        """Walk the project once, returning the first matching file per server."""
        import os

        remaining = {s.id: s for s in targets}
        found: dict[str, str] = {}
        for dirpath, _dirs, filenames in os.walk(self._cwd):
            if not remaining:
                break
            for fname in sorted(filenames):
                suffix = Path(fname).suffix
                for sid, server in list(remaining.items()):
                    if not server.extensions or suffix in server.extensions:
                        found[sid] = str(Path(dirpath) / fname)
                        del remaining[sid]
                if not remaining:
                    break
        return list(found.values())

    def _detect_and_resolve_servers(self) -> list[str]:
        """Walk the project once, detecting languages via pygments (respecting
        .gitignore) and picking the first matching file per server whose
        language is present — combining what used to be a detection walk plus
        a separate re-walk per matched server into a single pass.

        Availability (``shutil.which``, a PATH scan) is only checked for a
        server once its language is actually detected in the project — not
        upfront for every registered server — so servers for languages the
        project doesn't use (e.g. clangd/rust-analyzer/gopls in a pure-Python
        repo) never pay that PATH×PATHEXT scan at all.
        """
        import os

        from pygments.lexers import get_lexer_for_filename
        from pygments.util import ClassNotFound

        cwd = Path(self._cwd)
        try:
            import pathspec

            gitignore = cwd / ".gitignore"
            lines = gitignore.read_text().splitlines() if gitignore.is_file() else []
            spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        except ImportError:
            spec = None

        remaining = {sid: s for sid, s in self._servers.items() if s.enabled}
        found_files: dict[str, str] = {}
        found_exts: set[str] = set()
        checked_exts: set[str] = set()

        for dirpath, dirs, filenames in os.walk(self._cwd):
            if not remaining:
                break
            rel_dir = Path(dirpath).relative_to(cwd)
            if spec is not None:
                dirs[:] = [
                    d for d in dirs if not spec.match_file(str(rel_dir / d) + "/") and d != ".git"
                ]
            else:
                dirs[:] = [d for d in dirs if d != ".git"]

            for fname in filenames:
                ext = Path(fname).suffix
                if spec is not None and spec.match_file(str(rel_dir / fname)):
                    continue
                if ext not in found_exts:
                    if ext in checked_exts:
                        continue
                    try:
                        get_lexer_for_filename(fname)
                        found_exts.add(ext)
                    except ClassNotFound:
                        checked_exts.add(ext)
                        continue
                for sid, server in list(remaining.items()):
                    if not server.extensions or ext in server.extensions:
                        del remaining[sid]
                        if not server.is_available():
                            continue
                        found_files[sid] = str(Path(dirpath) / fname)
                        logger.info(
                            "lsp: eager start %s (detected %s in project)", sid, server.extensions
                        )
                if not remaining:
                    break

        return list(found_files.values())

    # ── LSP operations ────────────────────────────────────────────────────────

    async def _run(self, file: str, method: str, *args: Any) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[getattr(c, method)(file, *args) for c in clients], return_exceptions=True
        )
        out = []
        for r in results:
            if isinstance(r, Exception):
                continue
            if isinstance(r, list):
                out.extend(r)
            elif r is not None:
                out.append(r)
        return out

    async def hover(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "hover", line, character)

    async def definition(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "definition", line, character)

    async def references(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "references", line, character)

    async def implementation(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "implementation", line, character)

    async def document_symbol(self, file: str) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.document_symbol(file) for c in clients], return_exceptions=True
        )
        out = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
        return out

    async def workspace_symbol(self, query: str = "") -> list[Any]:
        out = []
        for client in self._clients.values():
            try:
                symbols = await client.workspace_symbol(query)
                # Keep only meaningful symbol kinds; drop File, Module, Namespace, Package noise
                filtered = [s for s in symbols if s.get("kind") in _USEFUL_SYMBOL_KINDS]
                out.extend(filtered)
            except Exception:
                pass
        return out

    async def incoming_calls(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "incoming_calls", line, character)

    async def outgoing_calls(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "outgoing_calls", line, character)

    async def declaration(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "declaration", line, character)

    async def type_definition(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "type_definition", line, character)

    # ── Edit application helpers ──────────────────────────────────────────────

    @staticmethod
    def _apply_text_edits(content: str, edits: list[dict]) -> str:
        """Apply LSP TextEdit[] to file content. Edits must be non-overlapping."""
        lines = content.split("\n")
        # Apply in reverse order so earlier positions aren't shifted by later edits
        for edit in sorted(
            edits,
            key=lambda e: (
                e["range"]["start"]["line"],
                e["range"]["start"]["character"],
            ),
            reverse=True,
        ):
            r = edit["range"]
            sl, sc = r["start"]["line"], r["start"]["character"]
            el, ec = r["end"]["line"], r["end"]["character"]
            new_text = edit.get("newText", "")
            prefix = lines[sl][:sc] if sl < len(lines) else ""
            suffix = lines[el][ec:] if el < len(lines) else ""
            replacement = (prefix + new_text + suffix).split("\n")
            lines[sl : el + 1] = replacement
        return "\n".join(lines)

    async def apply_workspace_edit(self, workspace_edit: dict) -> dict[str, int]:
        """Apply a WorkspaceEdit to disk. Returns {relative_path: edit_count}."""
        from urllib.parse import urlparse

        # Collect URI → edits from either documentChanges or changes
        file_edits: dict[str, list[dict]] = {}
        if "documentChanges" in workspace_edit:
            for change in workspace_edit["documentChanges"]:
                uri = change.get("textDocument", {}).get("uri", "")
                if uri:
                    file_edits.setdefault(uri, []).extend(change.get("edits", []))
        elif "changes" in workspace_edit:
            for uri, edits in workspace_edit["changes"].items():
                file_edits.setdefault(uri, []).extend(edits)

        applied: dict[str, int] = {}
        for uri, edits in file_edits.items():
            if not edits:
                continue
            path = Path(urlparse(uri).path) if uri.startswith("file://") else Path(uri)
            try:
                content = path.read_text(encoding="utf-8")
                path.write_text(self._apply_text_edits(content, edits), encoding="utf-8")
                await self.touch_file(str(path), wait_for_diagnostics=False)
                await self.save_file(str(path))
                rel = (
                    str(path.relative_to(self._cwd))
                    if path.is_relative_to(Path(self._cwd))
                    else path.name
                )
                applied[rel] = len(edits)
            except Exception as exc:
                logger.warning("apply_workspace_edit: %s: %s", path.name, exc)

        return applied

    async def apply_text_edits_to_file(self, file: str, edits: list[dict]) -> int:
        """Apply TextEdit[] to a single file on disk. Returns edit count applied."""
        if not edits:
            return 0
        path = Path(file)
        content = path.read_text(encoding="utf-8")
        path.write_text(self._apply_text_edits(content, edits), encoding="utf-8")
        await self.touch_file(file, wait_for_diagnostics=False)
        await self.save_file(file)
        return len(edits)

    # ── LSP edit operations ───────────────────────────────────────────────────

    async def rename(self, file: str, line: int, character: int, new_name: str) -> dict[str, int]:
        """Rename symbol and apply the WorkspaceEdit to disk. Returns {file: edit_count}."""
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.rename(file, line, character, new_name) for c in clients], return_exceptions=True
        )
        applied: dict[str, int] = {}
        for r in results:
            if not isinstance(r, dict):
                continue
            partial = await self.apply_workspace_edit(r)
            for f, count in partial.items():
                applied[f] = applied.get(f, 0) + count
        return applied

    async def code_action(
        self, file: str, line: int, character: int, end_line: int, end_char: int
    ) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.code_action(file, line, character, end_line, end_char) for c in clients],
            return_exceptions=True,
        )
        out = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
        return out

    async def execute_command(self, command: str, arguments: list[Any] | None = None) -> None:
        """Send workspace/executeCommand to all active clients.

        Some commands respond by sending workspace/applyEdit back to the client,
        which is handled automatically by the registered on_server_request callback.
        """
        for client in self._clients.values():
            with contextlib.suppress(Exception):
                await client.execute_command(command, arguments)
        # Yield so any pending workspace/applyEdit messages can be dispatched
        await asyncio.sleep(0.05)

    async def apply_code_action(self, action: dict) -> dict[str, int] | None:
        """Apply a single code action to disk.

        Returns {file: edit_count} for edit-type actions, {} for command-type
        actions (edits happen via workspace/applyEdit callback), or None if the
        action carries neither an edit nor a command.

        Lazy actions (missing both 'edit' and 'command') are resolved via
        codeAction/resolve before applying.
        """
        edit = action.get("edit")
        command = action.get("command")

        # Resolve lazy action — server may omit edit/command until resolve is called
        if not edit and not isinstance(command, dict):
            for client in self._clients.values():
                resolved = await client.resolve_code_action(action)
                if resolved is not None:
                    edit = resolved.get("edit")
                    command = resolved.get("command")
                    if edit or isinstance(command, dict):
                        action = resolved
                        break

        edit = action.get("edit")
        command = action.get("command")

        if edit:
            return await self.apply_workspace_edit(edit)
        if isinstance(command, dict):
            cmd = command.get("command", "")
            args = command.get("arguments", [])
            await self.execute_command(cmd, args)
            return {}  # edits applied asynchronously via workspace/applyEdit callback
        return None

    async def create_file(self, path: str, content: str = "") -> dict[str, int]:
        """Create a file on disk with full LSP notification support.

        1. workspace/willCreateFiles — server returns edits to apply first
        2. Write the file to disk
        3. workspace/didCreateFiles — notify server
        4. workspace/didChangeWatchedFiles (Created=1) via touch_file

        Returns {relative_path: edit_count} for any pre-create edits applied.
        """
        paths = [path]
        applied: dict[str, int] = {}

        # 1. Pre-create edits (servers rarely send these, but some do)
        for client in self._clients.values():
            edit = await client.will_create_files(paths)
            if isinstance(edit, dict):
                partial = await self.apply_workspace_edit(edit)
                for f, count in partial.items():
                    applied[f] = applied.get(f, 0) + count

        # 2. Create the file
        Path(path).write_text(content, encoding="utf-8")

        # 3. Notify server — didChangeWatchedFiles (Created) fires via touch_file
        await self.touch_file(path, wait_for_diagnostics=False)
        for client in self._clients.values():
            await client.did_create_files(paths)

        return applied

    async def delete_file(self, path: str) -> dict[str, int]:
        """Delete a file on disk with full LSP notification support.

        1. workspace/willDeleteFiles — server returns edits to apply first
        2. textDocument/didClose — close in all clients
        3. Unlink the file from disk
        4. workspace/didChangeWatchedFiles (Deleted=3)
        5. workspace/didDeleteFiles — notify server

        Returns {relative_path: edit_count} for any pre-delete edits applied.
        """
        paths = [path]
        applied: dict[str, int] = {}
        clients = await self.get_clients(path)

        # 1. Pre-delete edits (e.g. remove dead imports in other files)
        for client in clients:
            edit = await client.will_delete_files(paths)
            if isinstance(edit, dict):
                partial = await self.apply_workspace_edit(edit)
                for f, count in partial.items():
                    applied[f] = applied.get(f, 0) + count

        # 2. Close the document in all clients
        await asyncio.gather(*[c.close_file(path) for c in clients], return_exceptions=True)

        # 3. Delete the file
        Path(path).unlink()

        # 4. didChangeWatchedFiles (Deleted=3) + didDeleteFiles
        for client in clients:
            await client.notify_file_deleted(path)
            await client.did_delete_files(paths)

        return applied

    async def rename_file(self, old_path: str, new_path: str) -> dict[str, int]:
        """Move a file on disk with full LSP notification support.

        1. Calls workspace/willRenameFiles on all clients — servers return edits
           (e.g. updated import statements) that are applied before the move.
        2. Moves the file on disk.
        3. Calls workspace/didRenameFiles so servers update their internal state.

        Returns {relative_path: edit_count} for any edits applied.
        """
        renames = [(old_path, new_path)]
        applied: dict[str, int] = {}

        # 1. Pre-move edits (e.g. update imports in other files)
        for client in self._clients.values():
            edit = await client.will_rename_files(renames)
            if isinstance(edit, dict):
                partial = await self.apply_workspace_edit(edit)
                for f, count in partial.items():
                    applied[f] = applied.get(f, 0) + count

        # 2. Move the file
        Path(old_path).rename(new_path)

        # 3. Post-move notification
        for client in self._clients.values():
            await client.did_rename_files(renames)

        return applied

    async def signature_help(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "signature_help", line, character)

    async def formatting(self, file: str) -> dict[str, int]:
        """Format file and apply edits to disk. Returns {file: edit_count}."""
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.formatting(file) for c in clients], return_exceptions=True
        )
        edits: list[dict] = []
        for r in results:
            if isinstance(r, list):
                edits.extend(r)
        if not edits:
            return {}
        count = await self.apply_text_edits_to_file(file, edits)
        path = Path(file)
        rel = (
            str(path.relative_to(self._cwd)) if path.is_relative_to(Path(self._cwd)) else path.name
        )
        return {rel: count}

    async def range_formatting(
        self, file: str, line: int, character: int, end_line: int, end_char: int
    ) -> dict[str, int]:
        """Format a range and apply edits to disk. Returns {file: edit_count}."""
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.range_formatting(file, line, character, end_line, end_char) for c in clients],
            return_exceptions=True,
        )
        edits: list[dict] = []
        for r in results:
            if isinstance(r, list):
                edits.extend(r)
        if not edits:
            return {}
        count = await self.apply_text_edits_to_file(file, edits)
        path = Path(file)
        rel = (
            str(path.relative_to(self._cwd)) if path.is_relative_to(Path(self._cwd)) else path.name
        )
        return {rel: count}

    async def supertypes(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "supertypes", line, character)

    async def subtypes(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "subtypes", line, character)

    async def inlay_hints(self, file: str, start_line: int, end_line: int) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.inlay_hints(file, start_line, end_line) for c in clients], return_exceptions=True
        )
        out: list[Any] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
        return out

    async def code_lens(self, file: str) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.code_lens(file) for c in clients], return_exceptions=True
        )
        out: list[Any] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
        return out

    async def diagnostics(self) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}

        # Collect push diagnostics (publishDiagnostics notifications)
        for client in self._clients.values():
            for path, diags in client.diagnostics.items():
                result.setdefault(path, []).extend(diags)

        for client in self._clients.values():
            if not client.supports("diagnosticProvider"):
                continue
            # Try workspace-wide pull first (gives diagnostics for all files at once)
            ws_diags = await client.workspace_diagnostic()
            if ws_diags:
                for path, diags in ws_diags.items():
                    if path not in result:
                        result[path] = diags
                continue
            # Fall back to per-file pull for open files with no push data
            for path in client._file_versions:
                if path not in result:
                    pulled = await client.pull_diagnostics(path)
                    if pulled:
                        result[path] = pulled

        return result

    async def completion(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "completion", line, character)

    async def document_highlight(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "document_highlight", line, character)

    async def selection_range(self, file: str, positions: list[dict]) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.selection_range(file, positions) for c in clients], return_exceptions=True
        )
        out: list[Any] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
        return out

    async def folding_range(self, file: str) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.folding_range(file) for c in clients], return_exceptions=True
        )
        out: list[Any] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
        return out

    async def document_link(self, file: str) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.document_link(file) for c in clients], return_exceptions=True
        )
        out: list[Any] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
        return out

    async def semantic_tokens_full(self, file: str) -> dict | None:
        clients = await self.get_clients(file)
        for client in clients:
            result = await client.semantic_tokens_full(file)
            if result:
                return result
        return None

    async def semantic_tokens_range(
        self, file: str, line: int, character: int, end_line: int, end_char: int
    ) -> dict | None:
        clients = await self.get_clients(file)
        for client in clients:
            result = await client.semantic_tokens_range(file, line, character, end_line, end_char)
            if result:
                return result
        return None

    async def inline_value(
        self, file: str, line: int, character: int, end_line: int, end_char: int
    ) -> list[Any]:
        clients = await self.get_clients(file)
        results = await asyncio.gather(
            *[c.inline_value(file, line, character, end_line, end_char) for c in clients],
            return_exceptions=True,
        )
        out: list[Any] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
        return out

    async def moniker(self, file: str, line: int, character: int) -> list[Any]:
        return await self._run(file, "moniker", line, character)

    async def linked_editing_range(self, file: str, line: int, character: int) -> dict | None:
        clients = await self.get_clients(file)
        for client in clients:
            result = await client.linked_editing_range(file, line, character)
            if result is not None:
                return result
        return None

    async def set_trace(self, value: str = "off") -> None:
        """Broadcast $/setTrace to all running language servers."""
        await asyncio.gather(
            *[c.set_trace(value) for c in self._clients.values()], return_exceptions=True
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        await asyncio.gather(
            *[c.shutdown() for c in self._clients.values()], return_exceptions=True
        )
        self._clients.clear()

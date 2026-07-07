"""
LSP JSON-RPC 2.0 client over subprocess stdio.

Transport mirrors opencode's lsp/client.ts:
  - spawn language server as child process (stdin/stdout piped)
  - framing: Content-Length: N\\r\\n\\r\\n + UTF-8 JSON body
  - requests: integer IDs resolved via asyncio.Future
  - notifications: dispatched to registered handlers
  - server→client requests: answered automatically
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .language import EXTENSION_TO_LANGUAGE

logger = logging.getLogger(__name__)

# Maps serverCapabilities keys → LSP method name (for dynamic-registration lookup)
_CAP_TO_METHOD: dict[str, str] = {
    "hoverProvider": "textDocument/hover",
    "definitionProvider": "textDocument/definition",
    "declarationProvider": "textDocument/declaration",
    "typeDefinitionProvider": "textDocument/typeDefinition",
    "implementationProvider": "textDocument/implementation",
    "referencesProvider": "textDocument/references",
    "documentSymbolProvider": "textDocument/documentSymbol",
    "workspaceSymbolProvider": "workspace/symbol",
    "callHierarchyProvider": "textDocument/prepareCallHierarchy",
    "typeHierarchyProvider": "textDocument/prepareTypeHierarchy",
    "renameProvider": "textDocument/rename",
    "codeActionProvider": "textDocument/codeAction",
    "documentFormattingProvider": "textDocument/formatting",
    "documentRangeFormattingProvider": "textDocument/rangeFormatting",
    "inlayHintProvider": "textDocument/inlayHint",
    "codeLensProvider": "textDocument/codeLens",
    "signatureHelpProvider": "textDocument/signatureHelp",
    "executeCommandProvider": "workspace/executeCommand",
    "diagnosticProvider": "textDocument/diagnostic",
    "completionProvider": "textDocument/completion",
    "documentHighlightProvider": "textDocument/documentHighlight",
    "selectionRangeProvider": "textDocument/selectionRange",
    "foldingRangeProvider": "textDocument/foldingRange",
    "documentLinkProvider": "textDocument/documentLink",
    "semanticTokensProvider": "textDocument/semanticTokens/full",
    "inlineValueProvider": "textDocument/inlineValue",
    "monikerProvider": "textDocument/moniker",
    "linkedEditingRangeProvider": "textDocument/linkedEditingRange",
}


class LSPClient:
    def __init__(
        self,
        server_id: str,
        process: asyncio.subprocess.Process,
        root: str,
        initialization: dict[str, Any],
    ) -> None:
        self.server_id = server_id
        self.root = root
        self._process = process
        self._initialization = initialization
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notification_handlers: dict[str, list[Callable[[dict], None]]] = {}
        self.diagnostics: dict[str, list[dict]] = {}
        self._file_versions: dict[str, int] = {}  # path → current didOpen/didChange version
        self._reader_task: asyncio.Task | None = None
        self._diagnostics_events: dict[str, asyncio.Event] = {}
        # Server capabilities from the initialize response
        self._capabilities: dict[str, Any] = {}
        # Dynamically registered capabilities (client/registerCapability)
        self._dynamic_registrations: dict[str, dict] = {}  # lsp-method → registerOptions
        # Accumulated partial results keyed by partialResultToken
        self._partial_results: dict[str, list] = {}
        # Handlers for server → client requests (e.g. workspace/applyEdit)
        self._server_request_handlers: dict[str, Callable[..., Any]] = {}
        self._stderr_task: asyncio.Task | None = None
        # Diagnostic result IDs for incremental pull (LSP 3.17)
        self._ws_diag_result_ids: list[dict] = []  # previousResultIds for workspace/diagnostic
        self._ws_diag_cache: dict[str, list] = {}  # path → last-known full diagnostics
        self._pull_result_ids: dict[str, str] = {}  # path → resultId from textDocument/diagnostic
        self._pull_diag_cache: dict[str, list] = {}  # path → last-known pull diagnostics

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        server_id: str,
        command: list[str],
        root: str,
        initialization: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> LSPClient:
        import os

        merged_env = {**os.environ, **(env or {})}
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=root,
            env=merged_env,
        )
        client = cls(server_id, process, root, initialization or {})
        client._reader_task = asyncio.create_task(client._read_loop())
        client._stderr_task = asyncio.create_task(client._drain_stderr())
        await client._initialize()
        return client

    # ── Transport: read ───────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        assert self._process.stdout
        reader = self._process.stdout
        while True:
            try:
                headers: dict[str, str] = {}
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    decoded = line.decode("ascii", errors="replace").rstrip("\r\n")
                    if not decoded:
                        break
                    if ":" in decoded:
                        key, _, value = decoded.partition(":")
                        headers[key.strip()] = value.strip()

                length = int(headers.get("Content-Length", 0))
                if length == 0:
                    continue

                body = await reader.readexactly(length)
                msg: dict[str, Any] = json.loads(body.decode("utf-8"))
                self._dispatch(msg)

            except asyncio.IncompleteReadError:
                break
            except Exception as exc:
                logger.debug("lsp[%s] read error: %s", self.server_id, exc)
                break

    async def _drain_stderr(self) -> None:
        """Continuously read and log stderr to prevent pipe-buffer deadlocks."""
        assert self._process.stderr
        while True:
            try:
                line = await self._process.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if decoded:
                    # Errors and warnings from the server are surfaced at WARNING so
                    # they appear without requiring debug logging enabled.
                    lower = decoded.lower()
                    if any(
                        w in lower for w in ("error", "fatal", "crash", "traceback", "exception")
                    ):
                        logger.warning("lsp[%s] stderr: %s", self.server_id, decoded)
                    else:
                        logger.debug("lsp[%s] stderr: %s", self.server_id, decoded)
            except Exception:
                break

    def _dispatch(self, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        method = msg.get("method")

        # Response to our request
        if msg_id is not None and method is None:
            future = self._pending.pop(msg_id, None)
            if future and not future.done():
                if "error" in msg:
                    future.set_exception(Exception(str(msg["error"])))
                else:
                    future.set_result(msg.get("result"))
            return

        # Notification (no id)
        if method and msg_id is None:
            if method == "textDocument/publishDiagnostics":
                self._on_diagnostics(msg.get("params", {}))
            elif method == "$/progress":
                self._on_progress(msg.get("params", {}))
            elif method in ("window/showMessage", "window/logMessage"):
                self._on_server_message(msg.get("params", {}))
            elif method == "$/logTrace":
                logger.debug(
                    "lsp[%s] trace: %s",
                    self.server_id,
                    (msg.get("params") or {}).get("message", ""),
                )
            elif method == "window/workDoneProgress/cancel":
                pass  # client-side cancel; we drive no UI progress
            for handler in self._notification_handlers.get(method, []):
                with contextlib.suppress(Exception):
                    handler(msg.get("params", {}))
            return

        # Server → client request (has both method and id)
        if method and msg_id is not None:
            asyncio.create_task(self._answer_server_request(msg_id, method, msg.get("params")))

    def _on_progress(self, params: dict) -> None:
        """Accumulate $/progress partial-result chunks into _partial_results."""
        token = params.get("token")
        value = params.get("value")
        if token in self._partial_results and isinstance(value, list):
            self._partial_results[token].extend(value)
            return
        if isinstance(value, dict):
            kind = value.get("kind")
            if kind == "begin":
                logger.debug(
                    "lsp[%s] progress begin [%s]: %s", self.server_id, token, value.get("title", "")
                )
            elif kind == "end" and value.get("message"):
                logger.debug(
                    "lsp[%s] progress end [%s]: %s", self.server_id, token, value.get("message", "")
                )

    def _on_server_message(self, params: dict) -> None:
        """Log window/showMessage and window/logMessage notifications from the server."""
        level = params.get("type", 4)  # 1=error 2=warning 3=info 4=log
        message = params.get("message", "")
        if level == 1:
            logger.error("lsp[%s] server: %s", self.server_id, message)
        elif level == 2:
            logger.warning("lsp[%s] server: %s", self.server_id, message)
        else:
            logger.info("lsp[%s] server: %s", self.server_id, message)

    def on_server_request(self, method: str, handler: Callable[..., Any]) -> None:
        """Register an async handler for a server-initiated request (e.g. workspace/applyEdit)."""
        self._server_request_handlers[method] = handler

    async def _answer_server_request(self, req_id: Any, method: str, params: Any) -> None:
        result: Any = None
        if method == "workspace/configuration":
            result = [self._initialization]
        elif method == "workspace/workspaceFolders":
            result = [{"name": "workspace", "uri": Path(self.root).as_uri()}]
        elif method == "client/registerCapability":
            for reg in (params or {}).get("registrations", []):
                self._dynamic_registrations[reg["method"]] = reg.get("registerOptions", {})
            logger.debug(
                "lsp[%s] registered dynamic capabilities: %s",
                self.server_id,
                [r["method"] for r in (params or {}).get("registrations", [])],
            )
        elif method == "client/unregisterCapability":
            for unreg in (params or {}).get("unregistrations", []):
                self._dynamic_registrations.pop(unreg.get("method", ""), None)
        elif method == "window/showMessageRequest":
            params_ = params or {}
            logger.info(
                "lsp[%s] server [showMessageRequest]: %s",
                self.server_id,
                params_.get("message", ""),
            )
            # result stays None (no action selected — dialog dismissed)
        elif method == "window/workDoneProgress/create":
            pass  # acknowledge; result stays None — we track no UI progress state
        elif method == "window/showDocument":
            params_ = params or {}
            logger.info("lsp[%s] server [showDocument]: %s", self.server_id, params_.get("uri", ""))
            result = {"success": True}
        elif method in (
            "codeLens/refresh",
            "workspace/inlayHint/refresh",
            "workspace/semanticTokens/refresh",
            "workspace/inlineValue/refresh",
        ):
            logger.debug("lsp[%s] %s — acknowledged", self.server_id, method)
            # result stays None; we fetch fresh on every call so no cache to invalidate
        elif method in self._server_request_handlers:
            try:
                result = await self._server_request_handlers[method](params or {})
            except Exception as exc:
                logger.debug("lsp[%s] server-request handler %s: %s", self.server_id, method, exc)
        await self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    # ── Transport: write ──────────────────────────────────────────────────────

    async def _send(self, msg: dict[str, Any]) -> None:
        assert self._process.stdin
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        await self._process.stdin.drain()

    # ── JSON-RPC primitives ───────────────────────────────────────────────────

    async def request(self, method: str, params: Any, timeout: float = 10.0) -> Any:
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = future
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)
            with contextlib.suppress(Exception):
                await self._send(
                    {"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": req_id}}
                )
            raise

    async def request_partial(self, method: str, params: dict, timeout: float = 10.0) -> list:
        """Send a request with a partialResultToken; merge $/progress chunks with the response.

        Servers that support partial results send chunks via $/progress and may return null
        as the final response. We accumulate chunks and merge them.
        """
        token = str(uuid.uuid4())
        self._partial_results[token] = []
        try:
            result = await self.request(
                method, {**params, "partialResultToken": token}, timeout=timeout
            )
        except Exception:
            self._partial_results.pop(token, None)
            return []
        accumulated = self._partial_results.pop(token, [])
        if accumulated:
            if isinstance(result, list):
                accumulated.extend(result)
            return accumulated
        return _as_list(result)

    async def notify(self, method: str, params: Any) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def on_notification(self, method: str, handler: Callable[[dict], None]) -> None:
        self._notification_handlers.setdefault(method, []).append(handler)

    # ── Capability checks ─────────────────────────────────────────────────────

    def supports(self, capability: str) -> bool:
        """True if the server declared or dynamically registered this capability.

        capability: a serverCapabilities key, e.g. 'hoverProvider'.
        """
        if bool(self._capabilities.get(capability)):
            return True
        method = _CAP_TO_METHOD.get(capability)
        return method is not None and method in self._dynamic_registrations

    # ── LSP handshake ─────────────────────────────────────────────────────────

    async def _initialize(self) -> None:
        root_uri = Path(self.root).as_uri()
        result = await self.request(
            "initialize",
            {
                "rootUri": root_uri,
                "processId": None,
                "workspaceFolders": [{"name": "workspace", "uri": root_uri}],
                "initializationOptions": self._initialization,
                "capabilities": {
                    "textDocument": {
                        "publishDiagnostics": {"relatedInformation": True, "versionSupport": False},
                        "hover": {"contentFormat": ["plaintext", "markdown"]},
                        "definition": {"linkSupport": False},
                        "declaration": {"linkSupport": False},
                        "typeDefinition": {"linkSupport": False},
                        "references": {},
                        "implementation": {},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "callHierarchy": {},
                        "typeHierarchy": {},
                        "rename": {"prepareSupport": True},
                        "codeAction": {
                            "codeActionLiteralSupport": {
                                "codeActionKind": {
                                    "valueSet": [
                                        "quickfix",
                                        "refactor",
                                        "refactor.extract",
                                        "refactor.inline",
                                        "refactor.rewrite",
                                        "source",
                                    ]
                                },
                            },
                        },
                        "signatureHelp": {
                            "signatureInformation": {
                                "documentationFormat": ["plaintext", "markdown"],
                                "parameterInformation": {"labelOffsetSupport": True},
                            },
                        },
                        "synchronization": {
                            "willSave": True,
                            "willSaveWaitUntil": True,
                            "didSave": True,
                        },
                        "formatting": {},
                        "rangeFormatting": {},
                        "inlayHint": {"dynamicRegistration": True},
                        "codeLens": {"dynamicRegistration": True},
                        "diagnostic": {
                            "dynamicRegistration": False,
                            "relatedDocumentSupport": False,
                        },
                        "completion": {
                            "completionItem": {
                                "documentationFormat": ["plaintext", "markdown"],
                                "resolveSupport": {
                                    "properties": ["documentation", "detail", "additionalTextEdits"]
                                },
                            },
                            "completionItemKind": {"valueSet": list(range(1, 26))},
                        },
                        "documentHighlight": {},
                        "selectionRange": {},
                        "foldingRange": {
                            "rangeLimit": 5000,
                            "lineFoldingOnly": True,
                        },
                        "documentLink": {"tooltipSupport": False},
                        "semanticTokens": {
                            "requests": {"full": {"delta": True}, "range": True},
                            "tokenTypes": [],
                            "tokenModifiers": [],
                            "formats": ["relative"],
                            "augmentsSyntaxTokens": True,
                        },
                        "inlineValue": {},
                        "moniker": {},
                        "linkedEditingRange": {},
                    },
                    "workspace": {
                        "symbol": {"resolveSupport": {"properties": ["location.range"]}},
                        "workspaceFolders": True,
                        "configuration": True,
                        "executeCommand": {"dynamicRegistration": False},
                        "applyEdit": True,
                        "fileOperations": {
                            "willCreate": True,
                            "didCreate": True,
                            "willRename": True,
                            "didRename": True,
                            "willDelete": True,
                            "didDelete": True,
                        },
                    },
                    "window": {"workDoneProgress": True},
                },
            },
            timeout=30.0,
        )
        self._capabilities = (result or {}).get("capabilities", {})
        logger.debug("lsp[%s] capabilities: %s", self.server_id, list(self._capabilities))
        await self.notify("initialized", {})

    # ── File management ───────────────────────────────────────────────────────

    async def open_file(self, path: str, wait_for_diagnostics: bool = False) -> None:
        uri = Path(path).as_uri()
        text = Path(path).read_text(encoding="utf-8", errors="replace")

        if wait_for_diagnostics:
            event = asyncio.Event()
            self._diagnostics_events[path] = event

        version = self._file_versions.get(path)

        if version is not None:
            # Already open — notify the server that the file content changed
            await self.notify(
                "workspace/didChangeWatchedFiles",
                {
                    "changes": [{"uri": uri, "type": 2}]  # 2 = Changed
                },
            )
            next_version = version + 1
            self._file_versions[path] = next_version
            await self.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": next_version},
                    "contentChanges": [{"text": text}],
                },
            )
        else:
            # First touch — open the document in the server
            ext = Path(path).suffix
            lang = EXTENSION_TO_LANGUAGE.get(ext, "plaintext")
            self.diagnostics.pop(path, None)  # clear any stale diagnostics
            self._file_versions[path] = 0
            await self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": lang,
                        "version": 0,
                        "text": text,
                    }
                },
            )

        if wait_for_diagnostics:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=5.0)
            self._diagnostics_events.pop(path, None)

    def _on_diagnostics(self, params: dict) -> None:
        uri: str = params.get("uri", "")
        path = _uri_to_path(uri)
        self.diagnostics[path] = params.get("diagnostics", [])
        event = self._diagnostics_events.get(path)
        if event:
            event.set()

    async def pull_diagnostics(self, file: str) -> list:
        """textDocument/diagnostic (LSP 3.17 pull model). Returns diagnostic list."""
        if not self.supports("diagnosticProvider"):
            return []
        try:
            params: dict[str, Any] = {"textDocument": {"uri": Path(file).as_uri()}}
            prev_id = self._pull_result_ids.get(file)
            if prev_id:
                params["previousResultId"] = prev_id
            result = await self.request("textDocument/diagnostic", params)
            if not isinstance(result, dict):
                return []
            kind = result.get("kind")
            result_id = result.get("resultId")
            if kind == "full":
                diags = result.get("items", [])
                self._pull_diag_cache[file] = diags
                if result_id:
                    self._pull_result_ids[file] = result_id
                return diags
            if kind == "unchanged":
                return self._pull_diag_cache.get(file, [])
            return []
        except Exception:
            return []

    async def workspace_diagnostic(self) -> dict[str, list]:
        """workspace/diagnostic — pull diagnostics for all workspace files at once.

        Only available when serverCapabilities.diagnosticProvider.workspaceDiagnostics is true.
        Sends previousResultIds so servers can return 'unchanged' responses for unmodified files.
        Returns {absolute_path: [diagnostic, ...]}.
        """
        cap = self._capabilities.get("diagnosticProvider", {})
        if not (isinstance(cap, dict) and cap.get("workspaceDiagnostics")):
            return {}
        try:
            result = await self.request(
                "workspace/diagnostic",
                {
                    "previousResultIds": self._ws_diag_result_ids,
                },
                timeout=30.0,
            )
            out: dict[str, list] = {}
            new_result_ids: list[dict] = []
            for item in (result or {}).get("items", []):
                uri = item.get("uri", "")
                path = _uri_to_path(uri)
                result_id = item.get("resultId")
                if item.get("kind") == "full":
                    diags = item.get("items", [])
                    self._ws_diag_cache[path] = diags
                    if diags:
                        out[path] = diags
                    if result_id:
                        new_result_ids.append({"uri": uri, "value": result_id})
                elif item.get("kind") == "unchanged":
                    cached = self._ws_diag_cache.get(path, [])
                    if cached:
                        out[path] = cached
                    if result_id:
                        new_result_ids.append({"uri": uri, "value": result_id})
            if new_result_ids:
                self._ws_diag_result_ids = new_result_ids
            return out
        except Exception:
            return {}

    # ── LSP operations ────────────────────────────────────────────────────────

    def _pos_params(self, file: str, line: int, character: int) -> dict:
        return {
            "textDocument": {"uri": Path(file).as_uri()},
            "position": {"line": line, "character": character},
        }

    async def hover(self, file: str, line: int, character: int) -> Any:
        if not self.supports("hoverProvider"):
            return None
        try:
            return await self.request("textDocument/hover", self._pos_params(file, line, character))
        except Exception:
            return None

    async def definition(self, file: str, line: int, character: int) -> list:
        if not self.supports("definitionProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/definition", self._pos_params(file, line, character)
            )
            return _as_list(result)
        except Exception:
            return []

    async def references(self, file: str, line: int, character: int) -> list:
        if not self.supports("referencesProvider"):
            return []
        try:
            params = self._pos_params(file, line, character)
            params["context"] = {"includeDeclaration": True}
            return await self.request_partial("textDocument/references", params)
        except Exception:
            return []

    async def implementation(self, file: str, line: int, character: int) -> list:
        if not self.supports("implementationProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/implementation", self._pos_params(file, line, character)
            )
            return _as_list(result)
        except Exception:
            return []

    async def document_symbol(self, file: str) -> list:
        if not self.supports("documentSymbolProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/documentSymbol",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                },
            )
            return _as_list(result)
        except Exception:
            return []

    async def workspace_symbol(self, query: str = "") -> list:
        if not self.supports("workspaceSymbolProvider"):
            return []
        try:
            symbols = await self.request_partial("workspace/symbol", {"query": query})
        except Exception:
            return []
        # LSP 3.17: resolve symbols whose location has only a uri (no range)
        cap = self._capabilities.get("workspaceSymbolProvider", {})
        if isinstance(cap, dict) and cap.get("resolveProvider"):
            resolved: list = []
            for sym in symbols:
                loc = sym.get("location", {})
                if isinstance(loc, dict) and "uri" in loc and "range" not in loc:
                    try:
                        r = await self.request("workspaceSymbol/resolve", sym, timeout=5.0)
                        resolved.append(r if r else sym)
                    except Exception:
                        resolved.append(sym)
                else:
                    resolved.append(sym)
            return resolved
        return symbols

    async def prepare_call_hierarchy(self, file: str, line: int, character: int) -> list:
        if not self.supports("callHierarchyProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/prepareCallHierarchy", self._pos_params(file, line, character)
            )
            return _as_list(result)
        except Exception:
            return []

    async def incoming_calls(self, file: str, line: int, character: int) -> list:
        items = await self.prepare_call_hierarchy(file, line, character)
        if not items:
            return []
        try:
            return await self.request_partial("callHierarchy/incomingCalls", {"item": items[0]})
        except Exception:
            return []

    async def outgoing_calls(self, file: str, line: int, character: int) -> list:
        items = await self.prepare_call_hierarchy(file, line, character)
        if not items:
            return []
        try:
            return await self.request_partial("callHierarchy/outgoingCalls", {"item": items[0]})
        except Exception:
            return []

    async def declaration(self, file: str, line: int, character: int) -> list:
        if not self.supports("declarationProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/declaration", self._pos_params(file, line, character)
            )
            return _as_list(result)
        except Exception:
            return []

    async def type_definition(self, file: str, line: int, character: int) -> list:
        if not self.supports("typeDefinitionProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/typeDefinition", self._pos_params(file, line, character)
            )
            return _as_list(result)
        except Exception:
            return []

    async def rename(self, file: str, line: int, character: int, new_name: str) -> Any:
        if not self.supports("renameProvider"):
            return None
        # Pre-validate position if server supports prepareRename
        cap = self._capabilities.get("renameProvider", {})
        if isinstance(cap, dict) and cap.get("prepareProvider"):
            try:
                check = await self.request(
                    "textDocument/prepareRename",
                    self._pos_params(file, line, character),
                    timeout=5.0,
                )
                if check is None:
                    return None  # invalid rename position
            except Exception:
                return None  # server rejected this position
        try:
            params = self._pos_params(file, line, character)
            params["newName"] = new_name
            return await self.request("textDocument/rename", params)
        except Exception:
            return None

    async def code_action(
        self, file: str, line: int, character: int, end_line: int, end_char: int
    ) -> list:
        if not self.supports("codeActionProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/codeAction",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                    "range": {
                        "start": {"line": line, "character": character},
                        "end": {"line": end_line, "character": end_char},
                    },
                    "context": {"diagnostics": self.diagnostics.get(file, [])},
                },
            )
            return _as_list(result)
        except Exception:
            return []

    async def resolve_code_action(self, action: dict) -> dict | None:
        """codeAction/resolve — fill in edit/command for a lazy code action.

        Servers that declare resolveProvider may return code actions without 'edit'
        or 'command'. This request fills them in on demand.
        """
        cap = self._capabilities.get("codeActionProvider", {})
        if not (isinstance(cap, dict) and cap.get("resolveProvider")):
            return None
        try:
            return await self.request("codeAction/resolve", action, timeout=10.0)
        except Exception:
            return None

    async def signature_help(self, file: str, line: int, character: int) -> Any:
        if not self.supports("signatureHelpProvider"):
            return None
        try:
            return await self.request(
                "textDocument/signatureHelp", self._pos_params(file, line, character)
            )
        except Exception:
            return None

    async def save_file(self, path: str) -> None:
        """Send textDocument/didSave so servers that watch saves can react."""
        if path not in self._file_versions:
            return
        with contextlib.suppress(Exception):
            await self.notify(
                "textDocument/didSave",
                {
                    "textDocument": {"uri": Path(path).as_uri()},
                },
            )

    async def will_rename_files(self, renames: list[tuple[str, str]]) -> Any:
        """workspace/willRenameFiles — ask server for edits to apply before a file move.

        renames: list of (old_absolute_path, new_absolute_path).
        Returns a WorkspaceEdit dict, or None if not supported / no edits.
        """
        cap = self._capabilities.get("workspace", {}).get("fileOperations", {})
        if not cap.get("willRename"):
            return None
        files = [
            {"oldUri": Path(old).as_uri(), "newUri": Path(new).as_uri()} for old, new in renames
        ]
        try:
            return await self.request("workspace/willRenameFiles", {"files": files}, timeout=10.0)
        except Exception:
            return None

    async def did_rename_files(self, renames: list[tuple[str, str]]) -> None:
        """workspace/didRenameFiles — notify server that files were moved on disk."""
        cap = self._capabilities.get("workspace", {}).get("fileOperations", {})
        if not cap.get("didRename"):
            return
        files = [
            {"oldUri": Path(old).as_uri(), "newUri": Path(new).as_uri()} for old, new in renames
        ]
        with contextlib.suppress(Exception):
            await self.notify("workspace/didRenameFiles", {"files": files})

    async def will_save(self, path: str) -> None:
        """textDocument/willSave — fire-and-forget notification before saving."""
        cap = self._capabilities.get("textDocumentSync", {})
        if not (isinstance(cap, dict) and cap.get("willSave")):
            return
        if path not in self._file_versions:
            return
        with contextlib.suppress(Exception):
            await self.notify(
                "textDocument/willSave",
                {
                    "textDocument": {"uri": Path(path).as_uri()},
                    "reason": 1,  # 1=Manual, 2=AfterDelay, 3=FocusOut
                },
            )

    async def will_save_wait_until(self, path: str) -> list:
        """textDocument/willSaveWaitUntil — get edits to apply before writing to disk.

        Returns TextEdit[] (e.g. organize-imports edits) or [] if not supported.
        """
        cap = self._capabilities.get("textDocumentSync", {})
        if not (isinstance(cap, dict) and cap.get("willSaveWaitUntil")):
            return []
        if path not in self._file_versions:
            return []
        try:
            result = await self.request(
                "textDocument/willSaveWaitUntil",
                {
                    "textDocument": {"uri": Path(path).as_uri()},
                    "reason": 1,
                },
                timeout=5.0,
            )
            return _as_list(result)
        except Exception:
            return []

    async def close_file(self, path: str) -> None:
        """Send textDocument/didClose and stop tracking the file."""
        if path not in self._file_versions:
            return
        del self._file_versions[path]
        self.diagnostics.pop(path, None)
        self._diagnostics_events.pop(path, None)
        self._pull_result_ids.pop(path, None)
        self._pull_diag_cache.pop(path, None)
        with contextlib.suppress(Exception):
            await self.notify(
                "textDocument/didClose",
                {
                    "textDocument": {"uri": Path(path).as_uri()},
                },
            )

    async def will_create_files(self, paths: list[str]) -> Any:
        """workspace/willCreateFiles — ask server for edits before files are created."""
        cap = self._capabilities.get("workspace", {}).get("fileOperations", {})
        if not cap.get("willCreate"):
            return None
        files = [{"uri": Path(p).as_uri()} for p in paths]
        try:
            return await self.request("workspace/willCreateFiles", {"files": files}, timeout=10.0)
        except Exception:
            return None

    async def did_create_files(self, paths: list[str]) -> None:
        """workspace/didCreateFiles — notify server that files were created on disk."""
        cap = self._capabilities.get("workspace", {}).get("fileOperations", {})
        if not cap.get("didCreate"):
            return
        files = [{"uri": Path(p).as_uri()} for p in paths]
        with contextlib.suppress(Exception):
            await self.notify("workspace/didCreateFiles", {"files": files})

    async def will_delete_files(self, paths: list[str]) -> Any:
        """workspace/willDeleteFiles — ask server for edits before files are deleted."""
        cap = self._capabilities.get("workspace", {}).get("fileOperations", {})
        if not cap.get("willDelete"):
            return None
        files = [{"uri": Path(p).as_uri()} for p in paths]
        try:
            return await self.request("workspace/willDeleteFiles", {"files": files}, timeout=10.0)
        except Exception:
            return None

    async def did_delete_files(self, paths: list[str]) -> None:
        """workspace/didDeleteFiles — notify server that files were deleted on disk."""
        cap = self._capabilities.get("workspace", {}).get("fileOperations", {})
        if not cap.get("didDelete"):
            return
        files = [{"uri": Path(p).as_uri()} for p in paths]
        with contextlib.suppress(Exception):
            await self.notify("workspace/didDeleteFiles", {"files": files})

    async def notify_file_deleted(self, path: str) -> None:
        """workspace/didChangeWatchedFiles (Deleted=3) — file removed from disk."""
        with contextlib.suppress(Exception):
            await self.notify(
                "workspace/didChangeWatchedFiles",
                {"changes": [{"uri": Path(path).as_uri(), "type": 3}]},
            )

    async def did_change_configuration(self, settings: dict | None = None) -> None:
        """workspace/didChangeConfiguration — notify server that configuration changed.

        Pass settings=None to trigger servers to re-fetch via workspace/configuration.
        """
        with contextlib.suppress(Exception):
            await self.notify("workspace/didChangeConfiguration", {"settings": settings})

    async def execute_command(self, command: str, arguments: list[Any] | None = None) -> Any:
        """Send workspace/executeCommand. The server may respond via workspace/applyEdit."""
        if not self.supports("executeCommandProvider"):
            return None
        try:
            return await self.request(
                "workspace/executeCommand",
                {
                    "command": command,
                    "arguments": arguments or [],
                },
                timeout=30.0,
            )
        except Exception:
            return None

    async def formatting(self, file: str) -> list:
        if not self.supports("documentFormattingProvider"):
            return []
        from .utils import detect_indent

        tab_size, insert_spaces = detect_indent(file)
        try:
            result = await self.request(
                "textDocument/formatting",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                    "options": {"tabSize": tab_size, "insertSpaces": insert_spaces},
                },
            )
            return _as_list(result)
        except Exception:
            return []

    async def range_formatting(
        self, file: str, line: int, character: int, end_line: int, end_char: int
    ) -> list:
        if not self.supports("documentRangeFormattingProvider"):
            return []
        from .utils import detect_indent

        tab_size, insert_spaces = detect_indent(file)
        try:
            result = await self.request(
                "textDocument/rangeFormatting",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                    "range": {
                        "start": {"line": line, "character": character},
                        "end": {"line": end_line, "character": end_char},
                    },
                    "options": {"tabSize": tab_size, "insertSpaces": insert_spaces},
                },
            )
            return _as_list(result)
        except Exception:
            return []

    async def inlay_hints(self, file: str, start_line: int, end_line: int) -> list:
        if not self.supports("inlayHintProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/inlayHint",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                    "range": {
                        "start": {"line": start_line, "character": 0},
                        "end": {"line": end_line, "character": 0},
                    },
                },
            )
            hints = _as_list(result)
        except Exception:
            return []

        # Resolve hints that are missing tooltip when the server supports it
        cap = self._capabilities.get("inlayHintProvider", {})
        if isinstance(cap, dict) and cap.get("resolveProvider"):
            resolved: list = []
            for hint in hints:
                if "tooltip" not in hint:
                    try:
                        r = await self.request("inlayHint/resolve", hint, timeout=5.0)
                        resolved.append(r if r else hint)
                    except Exception:
                        resolved.append(hint)
                else:
                    resolved.append(hint)
            return resolved

        return hints

    async def code_lens(self, file: str) -> list:
        if not self.supports("codeLensProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/codeLens",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                },
            )
            lenses = _as_list(result)
        except Exception:
            return []

        # Resolve unresolved lenses (those without a command) if the server supports it
        cap = self._capabilities.get("codeLensProvider", {})
        if isinstance(cap, dict) and cap.get("resolveProvider"):
            resolved: list = []
            for lens in lenses:
                if "command" not in lens:
                    try:
                        r = await self.request("codeLens/resolve", lens, timeout=5.0)
                        resolved.append(r if r else lens)
                    except Exception:
                        resolved.append(lens)
                else:
                    resolved.append(lens)
            return resolved

        return lenses

    async def prepare_type_hierarchy(self, file: str, line: int, character: int) -> list:
        if not self.supports("typeHierarchyProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/prepareTypeHierarchy", self._pos_params(file, line, character)
            )
            return _as_list(result)
        except Exception:
            return []

    async def supertypes(self, file: str, line: int, character: int) -> list:
        items = await self.prepare_type_hierarchy(file, line, character)
        if not items:
            return []
        try:
            result = await self.request("typeHierarchy/supertypes", {"item": items[0]})
            return _as_list(result)
        except Exception:
            return []

    async def subtypes(self, file: str, line: int, character: int) -> list:
        items = await self.prepare_type_hierarchy(file, line, character)
        if not items:
            return []
        try:
            result = await self.request("typeHierarchy/subtypes", {"item": items[0]})
            return _as_list(result)
        except Exception:
            return []

    async def completion(self, file: str, line: int, character: int) -> Any:
        if not self.supports("completionProvider"):
            return None
        try:
            return await self.request(
                "textDocument/completion", self._pos_params(file, line, character)
            )
        except Exception:
            return None

    async def resolve_completion_item(self, item: dict) -> dict | None:
        cap = self._capabilities.get("completionProvider", {})
        if not (isinstance(cap, dict) and cap.get("resolveProvider")):
            return None
        try:
            return await self.request("completionItem/resolve", item, timeout=5.0)
        except Exception:
            return None

    async def document_highlight(self, file: str, line: int, character: int) -> list:
        if not self.supports("documentHighlightProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/documentHighlight", self._pos_params(file, line, character)
            )
            return _as_list(result)
        except Exception:
            return []

    async def selection_range(self, file: str, positions: list[dict]) -> list:
        if not self.supports("selectionRangeProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/selectionRange",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                    "positions": positions,
                },
            )
            return _as_list(result)
        except Exception:
            return []

    async def folding_range(self, file: str) -> list:
        if not self.supports("foldingRangeProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/foldingRange",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                },
            )
            return _as_list(result)
        except Exception:
            return []

    async def document_link(self, file: str) -> list:
        if not self.supports("documentLinkProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/documentLink",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                },
            )
            return _as_list(result)
        except Exception:
            return []

    async def resolve_document_link(self, link: dict) -> dict | None:
        cap = self._capabilities.get("documentLinkProvider", {})
        if not (isinstance(cap, dict) and cap.get("resolveProvider")):
            return None
        try:
            return await self.request("documentLink/resolve", link, timeout=5.0)
        except Exception:
            return None

    async def semantic_tokens_full(self, file: str) -> dict | None:
        cap = self._capabilities.get("semanticTokensProvider", {})
        if not (isinstance(cap, dict) and cap.get("full")):
            return None
        try:
            return await self.request(
                "textDocument/semanticTokens/full",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                },
            )
        except Exception:
            return None

    async def semantic_tokens_range(
        self, file: str, line: int, character: int, end_line: int, end_char: int
    ) -> dict | None:
        cap = self._capabilities.get("semanticTokensProvider", {})
        if not (isinstance(cap, dict) and cap.get("range")):
            return None
        try:
            return await self.request(
                "textDocument/semanticTokens/range",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                    "range": {
                        "start": {"line": line, "character": character},
                        "end": {"line": end_line, "character": end_char},
                    },
                },
            )
        except Exception:
            return None

    async def semantic_tokens_full_delta(self, file: str, previous_result_id: str) -> dict | None:
        cap = self._capabilities.get("semanticTokensProvider", {})
        full = cap.get("full", {}) if isinstance(cap, dict) else {}
        if not (isinstance(full, dict) and full.get("delta")):
            return None
        try:
            return await self.request(
                "textDocument/semanticTokens/full/delta",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                    "previousResultId": previous_result_id,
                },
            )
        except Exception:
            return None

    async def inline_value(
        self, file: str, line: int, character: int, end_line: int, end_char: int
    ) -> list:
        if not self.supports("inlineValueProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/inlineValue",
                {
                    "textDocument": {"uri": Path(file).as_uri()},
                    "range": {
                        "start": {"line": line, "character": character},
                        "end": {"line": end_line, "character": end_char},
                    },
                    "context": {
                        "frameId": 0,
                        "stoppedLocation": {
                            "start": {"line": line, "character": character},
                            "end": {"line": end_line, "character": end_char},
                        },
                    },
                },
            )
            return _as_list(result)
        except Exception:
            return []

    async def moniker(self, file: str, line: int, character: int) -> list:
        if not self.supports("monikerProvider"):
            return []
        try:
            result = await self.request(
                "textDocument/moniker", self._pos_params(file, line, character)
            )
            return _as_list(result)
        except Exception:
            return []

    async def linked_editing_range(self, file: str, line: int, character: int) -> dict | None:
        """textDocument/linkedEditingRange — paired ranges that change together "
        "(e.g. HTML open/close tags)."""
        if not self.supports("linkedEditingRangeProvider"):
            return None
        try:
            return await self.request(
                "textDocument/linkedEditingRange", self._pos_params(file, line, character)
            )
        except Exception:
            return None

    async def set_trace(self, value: str = "off") -> None:
        """$/setTrace — set server trace verbosity. value: 'off' | 'messages' | 'verbose'"""
        with contextlib.suppress(Exception):
            await self.notify("$/setTrace", {"value": value})

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        for path in list(self._file_versions.keys()):
            await self.close_file(path)
        try:
            await asyncio.wait_for(self.request("shutdown", None), timeout=2.0)
            await self.notify("exit", None)
        except Exception:
            pass
        # Stop the process and WAIT for it to be reaped. Without awaiting wait(),
        # the subprocess transport keeps a pending child-watcher callback that
        # fires after the event loop closes (on Ctrl+C / exit), producing
        # "Loop ... that handles pid ... is closed" warnings.
        if self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except (TimeoutError, ProcessLookupError, Exception):
                try:
                    self._process.kill()
                    await self._process.wait()
                except Exception:
                    pass
        # Cancel and drain the background tasks so their callbacks don't outlive
        # the loop either.
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._pending.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _uri_to_path(uri: str) -> str:
    if uri.startswith("file://"):
        return uri[7:]
    return uri


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]

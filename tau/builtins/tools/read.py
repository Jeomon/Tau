from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tau.builtins.tools.utils import (
    detect_binary_format,
    detect_image_mime,
    looks_like_binary,
    record_digests,
    resolve_tool_path,
    split_lines,
    stamp_lines,
)
from tau.tool.render import call_line
from tau.tool.types import (
    AbortSignal,
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolExecutionUpdateCallback,
    ToolInvocation,
    ToolKind,
    ToolResult,
)
from tau.utils.format import human_size


def _render_read_call(args: dict, _streaming: bool) -> list[str]:
    return call_line("read", args.get("path", ""))


_MAX_LINE_CHARS = 4000
# Generous enough for any real screenshot/photo while bounding the base64
# payload handed to the model — most vision APIs downscale well past this
# anyway, so there's little value in reading a larger file as an image.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


# Characters that ``str.splitlines`` treats as line breaks but a real file format
# does not — see utils._LINE_BREAK, which deliberately splits on \r\n, \r and \n
# only. Emitted raw into the read output they break the format's one-anchor-per-
# line invariant: a line containing a form feed is displayed as an anchored empty
# line followed by a phantom line with no anchor, and the character itself is
# invisible, so a model rewriting that line silently drops it.
#
# Escaping them is lossy in a different way — the displayed text is no longer
# byte-identical to the file — but it is VISIBLY lossy, and the footer says so.
# The anchor and the digest are still computed over the true content, so
# resolution and verification are unaffected.
_INVISIBLE_BREAKS = {
    "\v": "\\v",
    "\f": "\\f",
    "\x1c": "\\x1c",
    "\x1d": "\\x1d",
    "\x1e": "\\x1e",
    "\x85": "\\x85",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}
_INVISIBLE_BREAK_TABLE = str.maketrans(_INVISIBLE_BREAKS)


def _has_invisible_break(line: str) -> bool:
    return any(ch in line for ch in _INVISIBLE_BREAKS)


def _display_line(line: str) -> str:
    """Escape structure-breaking characters, then cap the displayed length.

    Stops one pathologically long line (a minified bundle, a one-line JSON
    blob) from dumping megabytes into the model's context through a single
    hashline anchor. The anchor hash is computed over the full untruncated
    line elsewhere, so this is purely a display cap — it doesn't affect
    anchor resolution for a later edit.
    """
    line = line.translate(_INVISIBLE_BREAK_TABLE)
    if len(line) <= _MAX_LINE_CHARS:
        return line
    truncated = line[:_MAX_LINE_CHARS]
    suffix = f"…[line truncated for display at {_MAX_LINE_CHARS} chars; {len(line)} chars total]"
    return truncated + suffix


class ReadParams(BaseModel):
    """Parameters for the read tool."""

    path: str = Field(
        description=(
            "Path to the UTF-8 text file to read. Prefer an absolute path; a relative "
            "value is resolved from the agent's working directory."
        ),
        examples=["/home/user/project/src/main.py", "/home/user/project/README.md"],
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of lines to skip before reading (0 reads from the first line).",
        examples=[0, 100, 250],
    )
    limit: int = Field(
        default=2000,
        ge=1,
        description="Maximum number of lines to read.",
        examples=[50, 100, 2000],
    )


def _render_read_result(content: str, opts: Any) -> list[str]:
    from tau.tui.utils import DIM, RESET

    if opts.is_error:
        return content.splitlines() or [content]

    metadata = opts.metadata or {}

    if metadata.get("is_image"):
        return [content]

    lines_returned = metadata.get("lines_returned", 0)

    line_word = "line" if lines_returned == 1 else "lines"
    result = [f"Read {lines_returned} {line_word}"]

    parsed = []
    for raw in content.splitlines():
        if "|" in raw:
            anchor, _, text = raw.partition("|")
            parsed.append((anchor, text))

    if not parsed:
        return result

    for num, text in parsed:
        result.append(f"{DIM}{num}{RESET}  {text}")

    return result


class ReadTool(Tool):
    """Tool for reading file contents with hashline anchors."""

    def __init__(self) -> None:
        super().__init__(
            name="read",
            description=(
                "Read a UTF-8 text file, replacing invalid byte sequences when decoding. "
                "Returns each line with a content-based hashline anchor in the format "
                "'<line>:<hash>|<content>'. Every line in the file gets a distinct anchor, "
                "including blank lines and repeated content. Use offset and limit to read "
                f"large files in chunks. A single line longer than {_MAX_LINE_CHARS} characters "
                "is truncated for display. A PNG, JPEG, GIF, or WEBP file (detected from its "
                "magic bytes, regardless of extension) is returned as image content instead of "
                f"text, up to {_MAX_IMAGE_BYTES // (1024 * 1024)} MiB; offset/limit don't apply "
                "to images, and this fails if the active model doesn't accept image input. "
                "Any other binary content is refused."
            ),
            schema=ReadParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
            render_result=_render_read_result,
            render_call=_render_read_call,
            render_shell="default",
            prompt_guidelines=(
                "Use grep first to locate the relevant section,"
                " then read with offset/limit instead of loading the entire file."
            ),
        )

    def get_display_name(self, args: dict[str, Any]) -> str:
        """Get a short display name for the read operation."""
        return args.get("path", "read")

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the file read operation."""
        params = ReadParams.model_validate(invocation.params)
        path = resolve_tool_path(params.path, invocation.cwd)

        try:
            exists, is_file = await asyncio.to_thread(lambda: (path.exists(), path.is_file()))
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot access file: {e}")
        if not exists:
            return ToolResult.error(invocation.id, f"File not found: {params.path}")
        if not is_file:
            return ToolResult.error(invocation.id, f"Not a file: {params.path}")

        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except OSError as e:
            return ToolResult.error(invocation.id, f"Cannot read file: {e}")

        mime = detect_image_mime(raw)
        if mime is not None:
            model = getattr(context, "llm", None)
            model = getattr(model, "model", None) if model is not None else None
            if model is not None:
                from tau.inference.model.types import Modality

                if Modality.Image not in model.input:
                    return ToolResult.error(
                        invocation.id,
                        f"'{params.path}' is a {mime} image, but the active model "
                        f"({model.name}) doesn't accept image input. Switch to a "
                        "vision-capable model to read this file.",
                    )
            if len(raw) > _MAX_IMAGE_BYTES:
                return ToolResult.error(
                    invocation.id,
                    f"'{params.path}' is a {human_size(len(raw))} {mime} image, over the "
                    f"{human_size(_MAX_IMAGE_BYTES)} limit for reading images.",
                )
            metadata = {
                "file_path": str(path),
                "is_image": True,
                "mime_type": mime,
                "byte_size": len(raw),
            }
            return ToolResult.with_images(
                invocation.id,
                f"Read image '{params.path}' ({mime}, {human_size(len(raw))})",
                images=[raw],
                metadata=metadata,
            )

        # Decoding, splitting and anchor hashing are all CPU-bound and scale
        # with file size, so they run off the event loop for the same reason
        # edit and write push their whole body to a thread: a multi-MiB file
        # would otherwise stall rendering and swallow input for the duration
        # (a 17 MiB file spends ~250ms in decode/splitlines alone).
        return await asyncio.to_thread(self._build_text_result, invocation, params, path, raw)

    def _build_text_result(
        self,
        invocation: ToolInvocation,
        params: ReadParams,
        path: Path,
        raw: bytes,
    ) -> ToolResult:
        """Decode, anchor and window the file body. Runs on a worker thread."""
        # Checked before the null-byte sniff: these formats lead with ASCII, so
        # the sniff either misses them entirely or — for a large one — lets them
        # through to fail later as "too many lines to anchor", which is true but
        # tells the caller nothing useful about what the file actually is.
        binary_format = detect_binary_format(raw)
        if binary_format is not None:
            return ToolResult.error(
                invocation.id,
                f"'{params.path}' is a {binary_format} file, not text. Extract its text "
                "first (pdftotext, or a library such as pypdf) and read that instead.",
            )

        if looks_like_binary(raw):
            return ToolResult.error(
                invocation.id,
                f"'{params.path}' appears to be a binary file (contains a null byte in the "
                "first 8 KiB) and cannot be read as text.",
            )

        lines = split_lines(raw.decode("utf-8", errors="replace"))

        total = len(lines)
        start = params.offset
        end = min(start + params.limit, total)
        chunk = lines[start:end]

        # Stamped over the whole file, not just this chunk. Two things depend on
        # that: a duplicated line's salt is derived from its neighbours, and the
        # token width is chosen from the total line count — so a given line must
        # get the same anchor no matter which window is being displayed. edit
        # re-derives the identical table when resolving.
        chunk_hashes = stamp_lines(lines)[start:end]

        # Retain a content digest per line so a later edit can check that the
        # line an anchor resolved to still says what was displayed here. Over the
        # whole file for the same reason the stamping is: an anchor does not
        # depend on the window it was shown in.
        record_digests(path, lines)

        numbered = "\n".join(
            f"{start + i + 1}:{h}|{_display_line(line)}"
            for i, (h, line) in enumerate(zip(chunk_hashes, chunk, strict=True))
        )

        footer = ""
        truncated = end < total
        if truncated:
            footer = (
                f"\n\n[Showing lines {start + 1}–{end} of {total}. Use offset={end} to read more.]"
            )

        # Say so when the display is not byte-identical to the file. Without
        # this the escape is indistinguishable from a file that really contains
        # a backslash, and rewriting the line would introduce one.
        if any(_has_invisible_break(line) for line in chunk):
            footer += (
                "\n\n[This file contains characters that would break the line "
                "structure of this output (form feed, vertical tab, or a Unicode "
                "line separator). They are shown escaped, e.g. \\f — the file "
                "itself holds the real character, one byte, not two. edit writes "
                "exactly what you give it, so rewriting such a line REPLACES the "
                "real character with the escape text. Edit other lines freely; "
                "use terminal (sed/python) to change a line that holds one.]"
            )

        metadata = {
            "file_path": str(path),
            "total_lines": total,
            "lines_returned": len(chunk),
            "offset": start,
            "truncated": truncated,
        }
        return ToolResult.ok(invocation.id, numbered + footer, metadata=metadata)

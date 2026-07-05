from __future__ import annotations

import base64
import io
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass

from tau.tui.ansi_bridge import parse_ansi_wrapped_into
from tau.tui.buffer import Buffer, RawWrite
from tau.tui.component import Component
from tau.tui.geometry import Rect

_log = logging.getLogger(__name__)


@dataclass
class ImageDimensions:
    width_px: int
    height_px: int


@dataclass
class ImageOptions:
    max_width_cells: int = 60
    max_height_cells: int | None = None
    filename: str | None = None
    image_id: int | None = None


def _allocate_image_id() -> int:
    return random.randint(1, 0xFFFFFFFE)


def _get_image_dimensions(data: bytes, mime_type: str) -> ImageDimensions | None:
    """Try to get image dimensions using Pillow."""
    try:
        import io

        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(data))
        return ImageDimensions(width_px=img.width, height_px=img.height)
    except Exception:
        return None


def _convert_to_png(data: bytes) -> bytes:
    """Convert supported image bytes to PNG for the Kitty protocol."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    image = ImageOps.exif_transpose(PILImage.open(io.BytesIO(data)))
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _calculate_cell_size(
    dims: ImageDimensions,
    max_width: int,
    max_height: int | None,
    cell_w: int,
    cell_h: int,
) -> tuple[int, int]:
    """Return (columns, rows) to display the image within the given cell bounds."""
    iw = max(1, dims.width_px)
    ih = max(1, dims.height_px)
    width_scale = (max_width * cell_w) / iw
    height_scale = (max_height * cell_h) / ih if max_height else width_scale
    scale = min(width_scale, height_scale)
    cols = max(1, min(max_width, int((iw * scale) / cell_w + 0.999)))
    rows_val = max(1, int((ih * scale) / cell_h + 0.999))
    rows = max(1, min(max_height, rows_val)) if max_height else rows_val
    return cols, rows


def _encode_kitty(b64: str, cols: int, rows: int, image_id: int | None) -> str:
    CHUNK = 4096
    params = ["a=T", "f=100", "q=2", "C=1", f"c={cols}", f"r={rows}"]
    if image_id is not None:
        params.append(f"i={image_id}")
    param_str = ",".join(params)

    if len(b64) <= CHUNK:
        return f"\x1b_G{param_str};{b64}\x1b\\"

    chunks = []
    offset = 0
    first = True
    while offset < len(b64):
        chunk = b64[offset : offset + CHUNK]
        is_last = offset + CHUNK >= len(b64)
        if first:
            chunks.append(f"\x1b_G{param_str},m=1;{chunk}\x1b\\")
            first = False
        elif is_last:
            chunks.append(f"\x1b_Gm=0;{chunk}\x1b\\")
        else:
            chunks.append(f"\x1b_Gm=1;{chunk}\x1b\\")
        offset += CHUNK
    return "".join(chunks)


def _encode_iterm2(b64: str, cols: int, filename: str | None) -> str:
    parts = ["inline=1", f"width={cols}", "height=auto", "preserveAspectRatio=1"]
    if filename:
        name_b64 = base64.b64encode(filename.encode()).decode()
        parts.append(f"name={name_b64}")
    return f"\x1b]1337;File={';'.join(parts)}:{b64}\x07"


class Image(Component):
    """
    Renders an image inline using Kitty or iTerm2 graphics protocols,
    with a text fallback on unsupported terminals.

    Usage::

        img = Image(image_bytes, "image/png")
        layout.set_widget("preview", img)
    """

    def __init__(
        self,
        data: bytes | str,
        mime_type: str = "image/png",
        fallback_color: Callable[[str], str] | None = None,
        options: ImageOptions | None = None,
        dimensions: ImageDimensions | None = None,
    ) -> None:
        if isinstance(data, str):
            self._b64 = data
            self._raw = base64.b64decode(data)
        else:
            self._raw = data
            self._b64 = base64.b64encode(data).decode()
        self._mime = mime_type
        self._fallback_color = fallback_color or (lambda s: s)
        self._opts = options or ImageOptions()
        self._dims = (
            dimensions or _get_image_dimensions(self._raw, mime_type) or ImageDimensions(800, 600)
        )
        self._image_id: int | None = self._opts.image_id
        # Cache holds either ("escape", row_within_block, rows, sequence) for
        # Kitty/iTerm2 (recomputed only when width changes — chunking a large
        # base64 payload isn't free) or ("fallback", 0, 1, text).
        self._cache: tuple[str, int, int, str] | None = None
        self._cache_width: int = 0

    def get_image_id(self) -> int | None:
        return self._image_id

    def invalidate(self) -> None:
        self._cache = None
        self._cache_width = 0

    def _compute(self, width: int) -> tuple[str, int, int, str]:
        """Return (kind, escape_row, rows, content) — recomputed only on width change.

        ``escape_row`` is which row (0-indexed within the block) actually
        carries the escape sequence — Kitty's is the top row; iTerm2's own
        protocol draws from wherever the cursor sits when it receives the
        sequence, so (matching the original string-renderer's approach) it's
        emitted on the last row with an embedded relative cursor-up move.
        """
        from tau.tui.terminal import get_capabilities, get_cell_dimensions

        caps = get_capabilities()
        cell = get_cell_dimensions()

        max_w = max(1, min(width - 2, self._opts.max_width_cells))
        default_max_h = max(1, int((max_w * cell.width_px) / cell.height_px + 0.999))
        max_h = self._opts.max_height_cells or default_max_h

        cols, rows = _calculate_cell_size(self._dims, max_w, max_h, cell.width_px, cell.height_px)

        if caps.images == "kitty":
            if self._image_id is None:
                self._image_id = _allocate_image_id()
            # Kitty requires PNG; convert if needed. Cache the result in _b64 so
            # subsequent renders (after invalidate) don't need _raw again.
            b64 = self._b64
            if self._mime != "image/png":
                try:
                    b64 = base64.b64encode(_convert_to_png(self._raw)).decode()
                    self._b64 = b64
                    self._mime = "image/png"
                except Exception:
                    _log.warning("image PNG conversion failed", exc_info=True)
            seq = _encode_kitty(b64, cols, rows, self._image_id)
            return ("escape", 0, rows, seq)
        if caps.images == "iterm2":
            seq = _encode_iterm2(self._b64, cols, self._opts.filename)
            move_up = f"\x1b[{rows - 1}A" if rows > 1 else ""
            return ("escape", rows - 1, rows, move_up + seq)
        return ("fallback", 0, 1, self._fallback_text())

    def render_cells(self, area: Rect, buf: Buffer) -> int:
        if self._cache is None or self._cache_width != area.width:
            self._cache = self._compute(area.width)
            self._cache_width = area.width
            self._raw = b""  # decoded bytes no longer needed after first render

        kind, escape_row, rows, content = self._cache

        buf.grow_to(area.y + rows)
        if kind == "fallback":
            line = self._fallback_color(content)
            return parse_ansi_wrapped_into(buf, area.x, area.y, line, area.width)

        # The whole block's cells are invisible to the terminal's normal text
        # grid — the terminal itself owns those pixels once it draws them, so
        # a stray SGR write or cell diff here would corrupt the image. Mark
        # every cell skip=True (blank symbol) rather than writing content.
        for yy in range(rows):
            for xx in range(area.width):
                buf.set(area.x + xx, area.y + yy, " ")
                buf.get(area.x + xx, area.y + yy).skip = True
        token = f"{self._mime}:{self._image_id}:{area.x}x{area.y}:{area.width}"
        buf.raw_writes.append(RawWrite(area.x, area.y + escape_row, content, token))
        return rows

    def _fallback_text(self) -> str:
        parts = []
        if self._opts.filename:
            parts.append(self._opts.filename)
        parts.append(f"[{self._mime}]")
        parts.append(f"{self._dims.width_px}x{self._dims.height_px}")
        return f"[Image: {' '.join(parts)}]"

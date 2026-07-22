from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

_PIL_MIME: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}

_AUDIO_MIME: dict[bytes, str] = {
    b"ID3": "audio/mpeg",
    b"\xff\xfb": "audio/mpeg",
    b"\xff\xf3": "audio/mpeg",
    b"\xff\xf2": "audio/mpeg",
    b"OggS": "audio/ogg",
    b"fLaC": "audio/flac",
    b"RIFF": "audio/wav",
}


def detect_image_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes; default to PNG if unknown.

    Args:
        data: Binary image data to detect.

    Returns:
        The MIME type string (e.g., 'image/jpeg', 'image/png').
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def detect_audio_mime(data: bytes) -> str:
    """Detect audio MIME type from magic bytes; default to MP3 if unknown.

    Args:
        data: Binary audio data to detect.

    Returns:
        The MIME type string (e.g., 'audio/mpeg', 'audio/wav', 'audio/ogg').
    """
    for magic, mime in _AUDIO_MIME.items():
        if data[: len(magic)] == magic:
            # WAV files use RIFF container with WAVE format code
            if magic == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":
                return "audio/wav"
            elif magic != b"RIFF":
                return mime
    return "audio/mpeg"


def image_to_base64(img: Any) -> tuple[str, str]:
    """Convert image to (base64_data, mime_type); URL strings passed through with empty mime.

    Args:
        img: A PIL Image, base64 string, raw bytes, or URL.

    Returns:
        A tuple of (base64_string, mime_type_string).
    """
    if isinstance(img, str):
        # URLs are passed through as-is
        if img.startswith("http"):
            return img, ""
        # Detect MIME type from base64 string magic bytes
        try:
            mime = detect_image_mime(base64.b64decode(img[:16] + "=="))
        except Exception:
            mime = "image/png"
        return img, mime
    if not isinstance(img, (str, bytes)):
        # PIL Image — import lazily; only reached when caller passes a PIL object
        from PIL import Image  # noqa: PLC0415

        if isinstance(img, Image.Image):
            fmt = (img.format or "PNG").upper()
            buf = io.BytesIO()
            img.save(buf, format=fmt)
            mime = _PIL_MIME.get(fmt, "image/png")
            return base64.b64encode(buf.getvalue()).decode(), mime
    # Raw bytes: detect MIME from magic bytes
    # At this point, img must be bytes (either originally or PIL.Image was handled above)
    mime = detect_image_mime(img)  # type: ignore[arg-type]
    return base64.b64encode(img).decode(), mime  # type: ignore[arg-type]


def audio_to_base64(item: bytes | str) -> tuple[str, str]:
    """Convert audio to (base64_data, mime_type); accepts bytes, base64, or 'file:' paths.

    Args:
        item: Raw audio bytes, base64-encoded string, or 'file:/path/to/audio'.

    Returns:
        A tuple of (base64_string, mime_type_string).
    """
    if isinstance(item, bytes):
        # Raw bytes: detect MIME from magic bytes
        mime = detect_audio_mime(item)
        return base64.b64encode(item).decode(), mime
    if item.startswith("file:"):
        # Load file from disk and encode
        data = Path(item[5:]).read_bytes()
        mime = detect_audio_mime(data)
        return base64.b64encode(data).decode(), mime
    # Assume base64 string; detect MIME from magic bytes
    try:
        mime = detect_audio_mime(base64.b64decode(item[:16] + "=="))
    except Exception:
        mime = "audio/mpeg"
    return item, mime


_OOXML_MIME: dict[str, str] = {
    "word/": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xl/": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt/": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def detect_file_mime(data: bytes) -> str:
    """Detect a document's MIME type from magic bytes; defaults to PDF if unknown.

    PDF is detected directly. Office Open XML formats (docx/xlsx/pptx) share
    the same ZIP magic bytes, so disambiguating them means peeking at the
    archive's top-level entry names (word/, xl/, ppt/) instead of a fixed
    byte offset — this only works with the complete file, not a truncated
    prefix, since ZIP's central directory lives at the end of the archive.
    """
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
            for prefix, mime in _OOXML_MIME.items():
                if any(n.startswith(prefix) for n in names):
                    return mime
        except Exception:
            pass
        return "application/zip"
    return "application/pdf"


def file_to_base64(item: bytes | str) -> tuple[str, str]:
    """Convert a file to (base64_data, mime_type); accepts bytes, base64, or 'file:' paths.

    Args:
        item: Raw file bytes, base64-encoded string, or 'file:/path/to/file'.

    Returns:
        A tuple of (base64_string, mime_type_string).
    """
    if isinstance(item, bytes):
        mime = detect_file_mime(item)
        return base64.b64encode(item).decode(), mime
    if item.startswith("file:"):
        data = Path(item[5:]).read_bytes()
        mime = detect_file_mime(data)
        return base64.b64encode(data).decode(), mime
    # Assume a complete base64-encoded file (this is what FileContent.__post_init__
    # normalizes raw bytes into) — decode it fully so the OOXML sub-type sniff
    # above still works; a truncated prefix would only support the PDF check.
    try:
        mime = detect_file_mime(base64.b64decode(item))
    except Exception:
        mime = "application/pdf"
    return item, mime


def video_to_base64(item: bytes | str) -> tuple[str, str]:
    """Convert video to (base64_data, mime_type); accepts bytes, base64, or 'file:' paths."""
    if isinstance(item, bytes):
        mime = (
            "video/mp4"
            if item[:4] in (b"ftyp", b"\x00\x00\x00\x18", b"\x00\x00\x00\x1c")
            else "video/mp4"
        )
        return base64.b64encode(item).decode(), mime
    if item.startswith("file:"):
        data = Path(item[5:]).read_bytes()
        return base64.b64encode(data).decode(), "video/mp4"
    return item, "video/mp4"


def filter_empty_assistant_messages(messages: list) -> list:
    """Remove assistant messages with no usable content (prevents provider 400 errors).

    Empty assistant messages (e.g. from persisted API errors) produce invalid {"role": "assistant"}
    with no content or tool_calls, causing all providers to reject the request.

    Args:
        messages: List of LLM messages.

    Returns:
        Filtered list with empty assistant messages removed.
    """
    from tau.message.types import Role, TextContent, ThinkingContent, ToolCallContent

    result = []
    for msg in messages:
        if getattr(msg, "role", None) == Role.ASSISTANT:
            contents = getattr(msg, "contents", [])
            # Check for at least one usable content type
            has_usable = any(
                isinstance(c, (TextContent, ToolCallContent, ThinkingContent)) for c in contents
            )
            if not has_usable:
                continue
        result.append(msg)
    return result


def strip_unusable_trailing_assistant(messages: list, session_manager: Any = None) -> list:
    """Remove trailing assistant message if it has unanswered tool calls.

    Crash recovery: handles sessions where the process died after the assistant
    message with tool calls was saved but before tool results were written.
    If session_manager is provided and a strip occurs, also removes the entry
    from the session file via remove_last_message().
    """
    from tau.message.types import AssistantMessage

    msgs = list(messages)
    if msgs and isinstance(msgs[-1], AssistantMessage) and msgs[-1].tool_calls():
        if session_manager is not None:
            is_removed = session_manager.remove_last_message(role="assistant")
            if is_removed:
                msgs.pop()
        else:
            msgs.pop()
    return msgs


_DANGLING_TOOL_CALL_PLACEHOLDER = "(no result recorded for this tool call)"


def close_dangling_tool_calls(
    messages: list, placeholder: str = _DANGLING_TOOL_CALL_PLACEHOLDER
) -> list:
    """Answer any unanswered tool calls with synthetic results.

    The non-destructive sibling of strip_unusable_trailing_assistant: instead
    of dropping the assistant message, every tool call that has no matching
    tool result in the following message gets a synthetic result carrying
    ``placeholder``. Providers (Anthropic in particular) reject a request
    outright when a ``tool_use`` block is not immediately answered — a state
    any caller that borrows session history mid tool execution will see (side
    channels, embedded agents forking a live session, and so on).

    Returns the input unchanged (same list object contents, new list) when the
    history is already well-formed; existing messages are never mutated —
    a ToolMessage missing some results is replaced by a rebuilt copy.
    """
    from tau.message.types import (
        AssistantMessage,
        ToolCallContent,
        ToolMessage,
        ToolResultContent,
    )

    patched: list = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        patched.append(msg)
        i += 1
        if not isinstance(msg, AssistantMessage):
            continue
        calls = [c for c in msg.contents if isinstance(c, ToolCallContent)]
        if not calls:
            continue
        tool_msg = None
        results: list[ToolResultContent] = []
        if i < len(messages) and isinstance(messages[i], ToolMessage):
            tool_msg = messages[i]
            results = [c for c in tool_msg.contents if isinstance(c, ToolResultContent)]
            i += 1
        answered = {r.id for r in results}
        pending = [c for c in calls if c.id not in answered]
        if not pending:
            if tool_msg is not None:
                patched.append(tool_msg)
        else:
            synthetic = [
                ToolResultContent(id=c.id, content=placeholder, tool_name=c.name)
                for c in pending
            ]
            patched.append(ToolMessage.from_results(results + synthetic))
    return patched

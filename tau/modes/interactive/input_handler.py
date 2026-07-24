from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tau.message.types import UserMessage
    from tau.modes.interactive.components.layout import Layout
    from tau.runtime.service import Runtime
    from tau.tui.service import TUI

_log = logging.getLogger(__name__)

# ── InputHandler ──────────────────────────────────────────────────────────────


class InputHandler:
    """Owns all user-input state and handling: submit, paste, clipboard, steer.

    Receives ``layout``, ``tui``, and ``runtime`` at construction. Bind to the
    layout callbacks once via ``bind()``.  The ``turn_has_content`` property
    lets the global key handler decide whether Escape is a pre- or mid-stream
    abort.
    """

    _LARGE_PASTE_LINES = 10
    _LARGE_PASTE_CHARS = 1000

    def __init__(self, runtime: Runtime, layout: Layout, tui: TUI) -> None:
        self._runtime = runtime
        self._layout = layout
        self._tui = tui

        self._invoke_task: asyncio.Task | None = None
        self._pending_tasks: set[asyncio.Task] = set()
        self._turn_has_content: bool = False
        self._last_user_text: str = ""

        # Inputs that require an idle agent are held here and replayed once the
        # turn settles. Commands explicitly marked as safe while busy bypass
        # this queue.
        self._deferred_inputs: list[str] = []
        self._draining_deferred: bool = False

        # Maps session counter → (uuid, absolute_path) for media stored in the project media dir.
        self._clipboard_images: dict[int, tuple[str, str]] = {}
        self._clipboard_image_notes: dict[int, str] = {}
        self._clipboard_image_counter: int = 0
        self._clipboard_audio: dict[int, tuple[str, str]] = {}
        self._clipboard_audio_counter: int = 0
        self._clipboard_video: dict[int, tuple[str, str]] = {}
        self._clipboard_video_counter: int = 0
        self._clipboard_files: dict[int, tuple[str, str]] = {}
        self._clipboard_file_counter: int = 0
        self._pasted_texts: dict[int, str] = {}
        self._paste_counter: int = 0

    def _track_task(self, task: asyncio.Task) -> asyncio.Task:
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    def shutdown(self) -> None:
        for task in self._pending_tasks:
            task.cancel()
        self._pending_tasks.clear()

    def bind(self) -> None:
        """Wire submit/followup/dequeue and clipboard callbacks onto the layout."""
        self._layout.on_submit(self._on_submit)
        self._layout.on_followup(self._on_followup)
        self._layout.on_dequeue(self._on_dequeue)
        self._layout.input.on_paste = self._on_paste
        self._layout.input.on_paste_text = self._on_paste_text
        self._layout.input.on_history_transform = self._transform_for_history

    @property
    def turn_has_content(self) -> bool:
        return self._turn_has_content

    def mark_turn_content(self) -> None:
        """Record that the assistant has produced output this turn.

        Once set, Escape becomes a mid-stream abort (keep the partial response)
        rather than a pre-stream undo (discard the user message and restore the
        editor). Called by the agent-hook handler on the first streamed token.
        """
        self._turn_has_content = True

    # ── Submit / followup / dequeue ───────────────────────────────────────────

    @staticmethod
    def _strip_media_markers(text: str) -> str:
        """Strip resolved media markers ([image #N], [audio:uuid], [video #N], [file #N]) from text.

        The LLM already receives the actual media bytes as separate content
        blocks, so it shouldn't also see the raw bracket placeholder that
        stood in for them in the input box. Falls back to the original text
        if stripping would leave nothing (a message that's only a marker).
        """
        stripped = re.sub(r"\[(?:image|audio|video|file)(?::[^\]]+| #\d+)\]", "", text).strip()
        return stripped if stripped else text

    def _notify(self, message: str | list[str], type: str = "info") -> None:  # noqa: A002
        """Show an inline system notification in the message list.

        Pass a str for plain text, or a list[str] of pre-rendered lines to get
        the same └ framing used by tool results (apply_render_shell) — a
        trailing "" entry renders as a blank line after the notification.
        """
        import time
        from typing import cast

        from tau.message.types import CustomMessage, ImageContent, LinesContent, TextContent

        custom_type = "tool" if type == "tool" else "system"
        contents: list[TextContent | ImageContent | LinesContent] = (
            [LinesContent(lines=message, notify_type=type)]
            if isinstance(message, list)
            else [TextContent(content=message)]
        )
        msg = CustomMessage(
            custom_type=custom_type,
            timestamp=time.time(),
            contents=cast(list[TextContent | ImageContent | LinesContent], contents),
        )
        self._layout.add_message(msg)
        self._tui.request_render()

    def _show_blocked_message(
        self,
        text: str,
        images: list[bytes],
        audio: list[bytes],
        video: list[bytes],
        file: list[bytes],
        reason: str,
    ) -> None:
        """Display a message blocked by a modality gate, then the error.

        ``add_message`` is a pure UI operation with no session-persistence
        side effect, so showing the attempted message (with its media
        placeholder) here is safe: it never reaches ``_invoke``/``_steer``,
        so nothing is sent to the model or written to the session file.
        """
        from tau.message.types import UserMessage

        user_msg = UserMessage.with_media(text, images, audio, video, file)
        self._layout.add_message(user_msg)
        self._notify([reason, ""], type="error")

    def _on_submit(self, text: str) -> None:
        from tau.message.types import UserMessage

        self.save_history()
        agent = self._runtime.agent

        if text.startswith("/") or text.startswith("!"):
            self._extract_clipboard_images(text)
            self._extract_clipboard_audio(text)
            self._extract_clipboard_video(text)
            self._extract_clipboard_file(text)
            # Slash/terminal input can carry large pastes too — e.g.
            # `/darwin <pasted 5KB brief>`. Expand them into the dispatched
            # text; clearing the buffer without expanding (the old behaviour)
            # sent the command the literal "[paste #N ...]" placeholder and
            # destroyed the content. Expansion clears the paste buffers.
            # The transcript keeps the compact original for display.
            expanded = self._expand_pasted_texts(text)
            if agent is not None and not agent.is_idle() and self._input_requires_idle(text):
                self._defer_input(expanded)
                return
            if text.startswith("/"):
                self._layout.add_message(self._make_slash_message(text))
                self._tui.request_render()
            self._track_task(asyncio.ensure_future(self._invoke(expanded)))
            return

        images, missing_images = self._extract_clipboard_images(text)
        if missing_images:
            plural = "s" if missing_images > 1 else ""
            self._notify(
                f"{missing_images} image{plural} could not be found —"
                f" the media file{plural} may have been deleted or moved.",
                type="error",
            )
            return

        audio = self._extract_clipboard_audio(text)
        video = self._extract_clipboard_video(text)
        file = self._extract_clipboard_file(text)
        expanded = self._expand_pasted_texts(text)

        if agent is not None and (images or audio or video or file):
            from tau.inference.model.types import Modality

            model = getattr(getattr(agent._engine, "llm", None), "model", None)
            if model is not None:
                attempted = [
                    (Modality.Image, "Image", bool(images)),
                    (Modality.Audio, "Audio", bool(audio)),
                    (Modality.Video, "Video", bool(video)),
                    (Modality.File, "File", bool(file)),
                ]
                unsupported = [
                    label for modality, label, present in attempted
                    if present and modality not in model.input
                ]
                if unsupported:
                    supported = [
                        label for modality, label, _ in attempted if modality in model.input
                    ]
                    verb = "is" if len(unsupported) == 1 else "are"
                    reason = (
                        f"{model.name} supports only {', '.join(supported)}. "
                        f"{', '.join(unsupported)} {verb} not supported."
                        if supported
                        else f"{model.name} does not support any media modalities."
                    )
                    self._show_blocked_message(text, images, audio, video, file, reason)
                    return

        if agent is not None and not agent.is_idle():
            self._track_task(
                asyncio.ensure_future(self._steer(expanded, images, audio, video, file))
            )
            return

        model_text = self._strip_media_markers(expanded)

        user_msg = UserMessage.with_media(text, images, audio, video, file)
        self._layout.add_message(user_msg)
        self._last_user_text = text
        self._turn_has_content = False
        self._tui.request_render()
        self._track_task(
            asyncio.ensure_future(
                self._invoke(self._expand_at_mentions(model_text), images, audio, video, file)
            )
        )

    def _on_followup(self, text: str) -> None:
        images, _ = self._extract_clipboard_images(text)
        audio = self._extract_clipboard_audio(text)
        video = self._extract_clipboard_video(text)
        file = self._extract_clipboard_file(text)
        expanded = self._expand_pasted_texts(text)
        self._track_task(
            asyncio.ensure_future(
                self._queue_followup(expanded, images, audio, video, file, display_text=text)
            )
        )

    # ── Deferred idle-only input ───────────────────────────────────────────────

    def _input_requires_idle(self, text: str) -> bool:
        """Return whether command or terminal input must wait for agent idle."""
        if not text.startswith("/"):
            return True
        parts = text[1:].strip().split(None, 1)
        if not parts:
            return True
        command = self._runtime.commands.get(parts[0].lower())
        return command is None or command.requires_idle

    def _defer_input(self, text: str) -> None:
        """Hold idle-only input until the turn settles, then replay it.

        No wakeup is scheduled here: the busy agent is in a turn that will emit
        ``settled`` when it finishes, which drives ``on_settled`` to drain this.
        """
        self._deferred_inputs.append(text)
        self._layout.set_deferred_queue(list(self._deferred_inputs))
        self._tui.request_render()

    async def on_settled(self) -> None:
        """Replay deferred /command + !terminal inputs once the turn has settled.

        Fired from the agent's ``settled`` event (same lifecycle point follow-up
        messages drain at). Each input is run to completion via ``_invoke`` so the
        next only starts after the previous turn/command fully finishes. A
        replayed prompt-style /command starts its own turn and re-emits
        ``settled``; the re-entrancy guard makes that nested call a no-op, and the
        loop here continues once ``_invoke`` returns.
        """
        if self._draining_deferred or not self._deferred_inputs:
            return
        agent = self._runtime.agent
        if agent is None or not agent.is_idle():
            # Not safe yet; a later settled (when the agent next goes idle) retries.
            return
        self._draining_deferred = True
        try:
            # Stop if the agent goes busy again (e.g. the abort path re-running
            # steering grabbed it); remaining inputs drain on the next settled.
            while self._deferred_inputs and agent.is_idle():
                text = self._deferred_inputs.pop(0)
                self._layout.set_deferred_queue(list(self._deferred_inputs))
                self._tui.request_render()
                if text.startswith("/"):
                    self._layout.add_message(self._make_slash_message(text))
                    self._tui.request_render()
                await self._invoke(text)
        finally:
            self._draining_deferred = False

    def _take_queued_texts(self) -> list[str]:
        """Snapshot and clear all pending steering/follow-up message texts.

        Returns the queued texts (oldest first) and empties both queues, so the
        caller can decide whether to restore them to the editor or run them.
        """
        from tau.message.types import TextContent

        agent = self._runtime.agent
        if agent is None:
            return []
        engine = agent._engine

        def _extract_texts(queue) -> list[str]:
            if queue is None:
                return []
            return [
                "".join(
                    c.content for c in getattr(msg, "contents", []) if isinstance(c, TextContent)
                )
                for msg in queue.snapshot()
            ]

        all_texts = _extract_texts(engine.state.steering_queue) + _extract_texts(
            engine.state.follow_up_queue
        )
        all_texts = [t for t in all_texts if t.strip()]
        if not all_texts:
            return []
        engine.clear_all_queues()
        self._layout.set_pending_queue([], [])
        return all_texts

    def _take_deferred_texts(self) -> list[str]:
        """Snapshot and clear pending deferred /command + !terminal inputs."""
        if not self._deferred_inputs:
            return []
        texts, self._deferred_inputs = self._deferred_inputs, []
        self._layout.set_deferred_queue([])
        return texts

    def _on_dequeue(self) -> None:
        all_texts = self._take_queued_texts() + self._take_deferred_texts()
        if not all_texts:
            return
        self._layout.restore_queued_to_editor(all_texts)
        self._tui.request_render()

    # ── Escape abort ──────────────────────────────────────────────────────────

    def escape_abort(self) -> None:
        """Escape pressed while agent is running.

        Pre-stream: undo the user message and restore editor.
        Mid-stream: keep partial response; signal via abort only.
        """
        agent = self._runtime.agent
        if agent is None:
            return

        had_content = self._turn_has_content
        # Anything typed while the agent ran was meant as the *next* task, not
        # part of the one being interrupted. Take it now and run it once the
        # aborted task goes idle, rather than discarding it to the editor.
        queued = self._take_queued_texts()
        agent.abort()

        if not had_content:
            # Pre-stream: no assistant output yet. Cancel the in-flight invoke,
            # drop the user message from the transcript and (if it was already
            # persisted) the session file, and put the text back in the editor.
            if self._invoke_task is not None and not self._invoke_task.done():
                self._invoke_task.cancel()
            self._layout.messages.remove_pending_user_turn()
            sm = self._runtime.session_manager
            if sm is not None:
                sm.remove_last_message(role="user")
            last_text = self._last_user_text
            self._last_user_text = ""
            if last_text:
                self._layout.input.set_text(last_text)
            self._clear_clipboard_caches()

        self._turn_has_content = False
        # Stop the spinner immediately. The pre-stream branch cancels the invoke
        # task, which interrupts the engine before it can emit AgentEndEvent (the
        # event that normally stops the spinner), so rely on this explicit stop.
        # If queued input runs next, _on_agent_start will start it again.
        self._layout.spinner.stop()
        if queued:
            self._track_task(asyncio.ensure_future(self._run_queued_next(queued)))
        self._tui.request_render()

    def _clear_clipboard_caches(self) -> None:
        """Clear all clipboard media caches."""
        self._clipboard_images.clear()
        self._clipboard_image_notes.clear()
        self._clipboard_image_counter = 0
        self._clipboard_audio.clear()
        self._clipboard_audio_counter = 0
        self._clipboard_video.clear()
        self._clipboard_video_counter = 0
        self._clipboard_files.clear()
        self._clipboard_file_counter = 0
        self._pasted_texts.clear()
        self._paste_counter = 0

    async def _run_queued_next(self, texts: list[str]) -> None:
        """Submit queued input as the next task once the aborted task is idle.

        Waits for the interrupted run to finish unwinding, then re-submits the
        combined queued text through the normal submit path so it renders and
        runs exactly as if freshly entered.
        """
        agent = self._runtime.agent
        if agent is None:
            return
        await agent.wait_for_idle()
        combined = "\n\n".join(texts).strip()
        if combined:
            self._on_submit(combined)

    # ── Invoke / steer / queue ────────────────────────────────────────────────

    async def _invoke(
        self,
        text: str,
        images: list[bytes] | None = None,
        audio: list[bytes] | None = None,
        video: list[bytes] | None = None,
        file: list[bytes] | None = None,
    ) -> None:
        self._invoke_task = asyncio.current_task()
        try:
            from tau.agent.types import PromptOptions

            if images or audio or video or file:
                options = PromptOptions(
                    images=images or [],
                    audio=audio or [],
                    video=video or [],
                    file=file or [],
                )
            else:
                options = None
            await self._runtime.user_input(text, options)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log.exception("Error during invoke")
            self._layout.spinner.set_label(f"error: {exc}")
            self._layout.spinner.stop()
            self._tui.request_render()
        finally:
            self._invoke_task = None

    @staticmethod
    def _build_user_message(
        text: str,
        images: list[bytes] | None = None,
        audio: list[bytes] | None = None,
        video: list[bytes] | None = None,
        file: list[bytes] | None = None,
    ) -> UserMessage:
        """Build a UserMessage from text plus any combination of media.

        Carries the same media a fresh turn would, so steering and follow-up
        messages match a freshly submitted message.
        """
        from tau.message.types import UserMessage

        return UserMessage.with_media(text, images, audio, video, file)

    async def _steer(
        self,
        text: str,
        images: list[bytes] | None = None,
        audio: list[bytes] | None = None,
        video: list[bytes] | None = None,
        file: list[bytes] | None = None,
    ) -> None:
        agent = self._runtime.agent
        if agent is None:
            return
        try:
            expanded = self._strip_media_markers(self._expand_at_mentions(text))
            msg = self._build_user_message(expanded, images, audio, video, file)
            await agent._engine.steer(msg)
        except Exception as exc:
            _log.exception("Error during steer")
            self._layout.spinner.set_label(f"error: {exc}")
            self._tui.request_render()

    async def _queue_followup(
        self,
        text: str,
        images: list[bytes] | None = None,
        audio: list[bytes] | None = None,
        video: list[bytes] | None = None,
        file: list[bytes] | None = None,
        display_text: str | None = None,
    ) -> None:
        shown = display_text if display_text is not None else text

        agent = self._runtime.agent
        if agent is None or agent.is_idle():
            user_msg = self._build_user_message(shown, images, audio, video, file)
            self._layout.add_message(user_msg)
            self._tui.request_render()
            model_text = self._strip_media_markers(self._expand_at_mentions(text))
            await self._invoke(model_text, images, audio, video, file)
        else:
            try:
                expanded = self._strip_media_markers(self._expand_at_mentions(text))
                msg = self._build_user_message(expanded, images, audio, video, file)
                await agent._engine.follow_up(msg)
            except Exception as exc:
                _log.exception("Error during follow-up")
                self._layout.spinner.set_label(f"error: {exc}")
                self._tui.request_render()

    # ── Paste handling ────────────────────────────────────────────────────────

    _AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".weba"}
    _VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv"}
    _IMAGE_SUFFIXES = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tiff",
        ".tif",
        ".heic",
        ".heif",
    }

    def _paste_file(self, src_path: str) -> None:
        """Detect file type by extension and route to the appropriate store method.

        A known image suffix (or no suffix at all — e.g. an extensionless
        clipboard grab) goes to the image path; audio/video suffixes go to
        their own paths; everything else (PDF, DOCX, XLSX, ...) goes to the
        generic file path rather than being force-fed to the image decoder,
        which would just fail with a confusing "could not store image" error.
        """
        try:
            suffix = "." + src_path.rsplit(".", 1)[-1].lower() if "." in src_path else ""
            with open(src_path, "rb") as f:
                data = f.read()
            if suffix in self._AUDIO_SUFFIXES:
                self._store_clipboard_audio(data, suffix)
            elif suffix in self._VIDEO_SUFFIXES:
                self._store_clipboard_video(data, suffix)
            elif suffix in self._IMAGE_SUFFIXES or not suffix:
                self._store_clipboard_image(data, suffix or ".png")
            else:
                self._store_clipboard_file(data, suffix)
        except Exception:
            _log.debug("Failed to paste file %r", src_path, exc_info=True)

    def _on_paste(self) -> None:
        """Handle Ctrl+V: read the OS clipboard directly (image or file paths).

        Uses pyxclip — unlike PIL's ImageGrab.grabclipboard(), which can only
        ever return an image or None on macOS (no file-list support there,
        unlike Windows), pyxclip reads file references natively on Windows,
        macOS, and Linux through a single cross-platform API.
        """
        try:
            import pyxclip

            item = pyxclip.paste()
        except Exception:
            _log.debug("Clipboard read failed", exc_info=True)
            return

        if isinstance(item, list):
            for p in item:
                self._paste_file(str(p))
        elif isinstance(item, dict):
            self._store_pyxclip_image(item)
        # A plain str (text) result isn't this handler's concern — Ctrl+V of
        # text doesn't reach here in practice, but there's nothing to do if it does.

    def _store_pyxclip_image(self, item: dict) -> None:
        """Convert pyxclip's raw RGBA clipboard image into a stored PNG."""
        try:
            import io

            from PIL import Image

            img = Image.frombytes("RGBA", (item["width"], item["height"]), item["bytes"])
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._store_clipboard_image(buf.getvalue(), ".png")
        except Exception:
            _log.debug("Failed to convert pyxclip image data to PNG", exc_info=True)

    def _get_media_dir(self) -> Path:
        sm = self._runtime.session_manager
        if sm is not None:
            return sm.session_dir / "media"
        from tau.settings.paths import CONFIG_DIR_PATH

        return CONFIG_DIR_PATH / "sessions" / "global" / "media"

    def _find_media_by_uuid(self, uid: str) -> Path | None:
        """Search all project session media dirs for a file matching the UUID.

        History is global across projects, so an image pasted in project A must
        still be resolvable when re-submitted from a session in project B.
        """
        from tau.settings.paths import get_sessions_dir

        try:
            for project_dir in get_sessions_dir().iterdir():
                if not project_dir.is_dir():
                    continue
                media_dir = project_dir / "media"
                if not media_dir.is_dir():
                    continue
                for p in media_dir.glob(f"{uid}.*"):
                    return p
        except OSError:
            _log.debug("failed to locate media by uuid %s", uid, exc_info=True)
        return None

    def _store_clipboard_image(self, raw: bytes, suffix: str) -> None:
        import uuid as _uuid

        try:
            from tau.utils.image_processing import process_image

            sm = self._runtime.settings_manager
            auto_resize = sm.get_image_auto_resize() if sm is not None else True
            result = process_image(raw, auto_resize=auto_resize)
            data = result.data
            suffix = ".png" if result.mime_type == "image/png" else suffix
            note = result.dimension_note()
            media_dir = self._get_media_dir()
            media_dir.mkdir(parents=True, exist_ok=True)
            file_uuid = str(_uuid.uuid4())
            media_path = media_dir / f"{file_uuid}{suffix}"
            media_path.write_bytes(data)
            self._clipboard_image_counter += 1
            idx = self._clipboard_image_counter
            self._clipboard_images[idx] = (file_uuid, str(media_path))
            if note:
                self._clipboard_image_notes[idx] = note
            self._layout.input.insert_at_cursor(f"[image #{idx}]")
            self._tui.request_render()
        except Exception as exc:
            _log.exception("Failed to store clipboard image")
            self._notify(f"Could not store image: {exc}", type="error")

    def _store_clipboard_audio(self, raw: bytes, suffix: str) -> None:
        import uuid as _uuid

        try:
            media_dir = self._get_media_dir()
            media_dir.mkdir(parents=True, exist_ok=True)
            file_uuid = str(_uuid.uuid4())
            media_path = media_dir / f"{file_uuid}{suffix}"
            media_path.write_bytes(raw)
            self._clipboard_audio_counter += 1
            idx = self._clipboard_audio_counter
            self._clipboard_audio[idx] = (file_uuid, str(media_path))
            self._layout.input.insert_at_cursor(f"[audio #{idx}]")
            self._tui.request_render()
        except Exception as exc:
            _log.exception("Failed to store clipboard audio")
            self._notify(f"Could not store audio: {exc}", type="error")

    def _store_clipboard_video(self, raw: bytes, suffix: str) -> None:
        import uuid as _uuid

        try:
            media_dir = self._get_media_dir()
            media_dir.mkdir(parents=True, exist_ok=True)
            file_uuid = str(_uuid.uuid4())
            media_path = media_dir / f"{file_uuid}{suffix}"
            media_path.write_bytes(raw)
            self._clipboard_video_counter += 1
            idx = self._clipboard_video_counter
            self._clipboard_video[idx] = (file_uuid, str(media_path))
            self._layout.input.insert_at_cursor(f"[video #{idx}]")
            self._tui.request_render()
        except Exception as exc:
            _log.exception("Failed to store clipboard video")
            self._notify(f"Could not store video: {exc}", type="error")

    def _store_clipboard_file(self, raw: bytes, suffix: str) -> None:
        import uuid as _uuid

        try:
            media_dir = self._get_media_dir()
            media_dir.mkdir(parents=True, exist_ok=True)
            file_uuid = str(_uuid.uuid4())
            media_path = media_dir / f"{file_uuid}{suffix}"
            media_path.write_bytes(raw)
            self._clipboard_file_counter += 1
            idx = self._clipboard_file_counter
            self._clipboard_files[idx] = (file_uuid, str(media_path))
            self._layout.input.insert_at_cursor(f"[file #{idx}]")
            self._tui.request_render()
        except Exception as exc:
            _log.exception("Failed to store clipboard file")
            self._notify(f"Could not store file: {exc}", type="error")

    def _extract_clipboard_audio(self, text: str) -> list[bytes]:
        audio: list[bytes] = []
        seen: set[int] = set()
        for m in re.finditer(r"\[audio #(\d+)\]", text):
            idx = int(m.group(1))
            if idx in seen:
                continue
            seen.add(idx)
            entry = self._clipboard_audio.get(idx)
            if entry is None:
                continue
            _, path = entry
            try:
                with open(path, "rb") as f:
                    audio.append(f.read())
            except OSError:
                _log.warning("failed to read clipboard audio %s", path, exc_info=True)
        # Also resolve persistent [audio:{uuid}] markers from history
        seen_uuids: set[str] = set()
        for m in re.finditer(r"\[audio:([^\]]+)\]", text):
            uid = m.group(1)
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
            p = self._find_media_by_uuid(uid)
            if p is not None:
                with contextlib.suppress(OSError):
                    audio.append(p.read_bytes())
        self._clipboard_audio.clear()
        self._clipboard_audio_counter = 0
        return audio

    def _extract_clipboard_video(self, text: str) -> list[bytes]:
        video: list[bytes] = []
        seen: set[int] = set()
        for m in re.finditer(r"\[video #(\d+)\]", text):
            idx = int(m.group(1))
            if idx in seen:
                continue
            seen.add(idx)
            entry = self._clipboard_video.get(idx)
            if entry is None:
                continue
            _, path = entry
            try:
                with open(path, "rb") as f:
                    video.append(f.read())
            except OSError:
                _log.warning("failed to read clipboard video %s", path, exc_info=True)
        # Also resolve persistent [video:{uuid}] markers from history
        seen_uuids: set[str] = set()
        for m in re.finditer(r"\[video:([^\]]+)\]", text):
            uid = m.group(1)
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
            p = self._find_media_by_uuid(uid)
            if p is not None:
                with contextlib.suppress(OSError):
                    video.append(p.read_bytes())
        self._clipboard_video.clear()
        self._clipboard_video_counter = 0
        return video

    def _extract_clipboard_file(self, text: str) -> list[bytes]:
        file: list[bytes] = []
        seen: set[int] = set()
        for m in re.finditer(r"\[file #(\d+)\]", text):
            idx = int(m.group(1))
            if idx in seen:
                continue
            seen.add(idx)
            entry = self._clipboard_files.get(idx)
            if entry is None:
                continue
            _, path = entry
            try:
                with open(path, "rb") as f:
                    file.append(f.read())
            except OSError:
                _log.warning("failed to read clipboard file %s", path, exc_info=True)
        # Also resolve persistent [file:{uuid}] markers from history
        seen_uuids: set[str] = set()
        for m in re.finditer(r"\[file:([^\]]+)\]", text):
            uid = m.group(1)
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
            p = self._find_media_by_uuid(uid)
            if p is not None:
                with contextlib.suppress(OSError):
                    file.append(p.read_bytes())
        self._clipboard_files.clear()
        self._clipboard_file_counter = 0
        return file

    # ESC[<code>;5u — control bytes some terminals (tmux popups with
    # extended-keys-format=csi-u) re-encode inside a bracketed paste.
    _CSI_U_CTRL_RE = re.compile(r"\x1b\[(\d+);5u")

    def _sanitize_paste(self, text: str) -> str:
        """Clean bracketed-paste text before it is stored or inserted.

        1. Decode CSI-u re-encoded control bytes back to their literal byte,
           so a pasted newline doesn't leak into the buffer as "[106;5u".
        2. Normalize line endings (CRLF/CR -> LF) and expand tabs to spaces.
        3. Drop remaining non-printable characters (keep newlines).
        4. Prepend a space when pasting a path right after a word character.
        """

        def _decode(m: re.Match[str]) -> str:
            cp = int(m.group(1))
            if 97 <= cp <= 122:  # ctrl+a..z
                return chr(cp - 96)
            if 65 <= cp <= 90:  # ctrl+A..Z
                return chr(cp - 64)
            return m.group(0)

        text = self._CSI_U_CTRL_RE.sub(_decode, text)
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
        text = "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)
        # Strip trailing newlines — copying a line from the terminal often includes
        # the newline, which would create a ghost empty line in the input box.
        text = text.rstrip("\n")

        # Readability: pasting a path (/, ~, .) right after a word char gets a space.
        if text[:1] in ("/", "~", "."):
            inp = self._layout.input
            buf, cur = inp.text, getattr(inp, "_cursor", 0)
            if cur > 0 and buf[cur - 1 : cur].isalnum():
                text = " " + text
        return text

    def _detect_pasted_file_path(self, text: str) -> str | None:
        """If the pasted text is exactly one existing file's path, return it.

        macOS has no clipboard signal Tau can read to distinguish "paste a
        file" from "paste text": Finder's Cmd+C + Cmd+V and drag-and-drop
        onto the terminal both surface as a plain-text bracketed paste of the
        file's path (Ctrl+V's ImageGrab.grabclipboard() route only ever
        returns real image data or None on macOS — Pillow has no file-list
        support there, unlike Windows). So a bare, whole-paste path that
        resolves to a real file is treated as a file paste instead of literal
        text — matching what the user actually did.

        Some Windows terminals (and Explorer drag-and-drop) hit the same gap:
        the path arrives as plain bracketed-paste text, wrapped in double
        quotes when it contains a space (the cmd.exe/PowerShell/Explorer
        convention). ``PureWindowsPath``/``PurePosixPath().is_absolute()`` —
        rather than a hand-rolled prefix check — recognize both a drive-letter
        (``C:\\...``) or UNC (``\\\\server\\share\\...``) path and a POSIX
        absolute path, using the stdlib's own path-shape rules instead of
        reimplementing them.
        """
        if "\n" in text:
            return None
        candidate = text.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
            candidate = candidate[1:-1]
        if not candidate:
            return None
        looks_like_path = (
            candidate.startswith("~")
            or PureWindowsPath(candidate).is_absolute()
            or PurePosixPath(candidate).is_absolute()
        )
        if not looks_like_path:
            return None
        path = Path(candidate).expanduser()
        if not path.is_file():
            return None
        return str(path)

    def _on_paste_text(self, text: str) -> None:
        text = self._sanitize_paste(text)
        if not text:
            return

        file_path = self._detect_pasted_file_path(text)
        if file_path is not None:
            self._paste_file(file_path)
            return

        lines = text.split("\n")
        if len(lines) > self._LARGE_PASTE_LINES or len(text) > self._LARGE_PASTE_CHARS:
            self._paste_counter += 1
            idx = self._paste_counter
            self._pasted_texts[idx] = text
            marker = (
                f"[paste #{idx} +{len(lines)} lines]"
                if len(lines) > self._LARGE_PASTE_LINES
                else f"[paste #{idx} {len(text)} chars]"
            )
            self._layout.input.insert_at_cursor(marker)
            self._tui.request_render()
        else:
            self._layout.input.insert_at_cursor(text)
            self._tui.request_render()

    def _expand_pasted_texts(self, text: str) -> str:
        if not self._pasted_texts:
            return text

        def _replace(m: re.Match) -> str:
            idx = int(m.group(1))
            return self._pasted_texts.get(idx) or m.group(0)

        expanded = re.sub(r"\[paste #(\d+)(?: \+\d+ lines| \d+ chars)\]", _replace, text)
        self._pasted_texts.clear()
        self._paste_counter = 0
        return expanded

    def _transform_for_history(self, text: str) -> str:
        """Replace session-scoped media markers with persistent [type:uuid] ones.

        Covers [image/audio/video/file #N].

        Paste markers are stripped entirely since their content is already expanded into the text
        before this is called (or they reference temp data that won't survive the session).
        """

        def _replace_image(m: re.Match) -> str:
            idx = int(m.group(1))
            entry = self._clipboard_images.get(idx)
            return f"[image:{entry[0]}]" if entry else ""

        def _replace_audio(m: re.Match) -> str:
            idx = int(m.group(1))
            entry = self._clipboard_audio.get(idx)
            return f"[audio:{entry[0]}]" if entry else ""

        def _replace_video(m: re.Match) -> str:
            idx = int(m.group(1))
            entry = self._clipboard_video.get(idx)
            return f"[video:{entry[0]}]" if entry else ""

        def _replace_file(m: re.Match) -> str:
            idx = int(m.group(1))
            entry = self._clipboard_files.get(idx)
            return f"[file:{entry[0]}]" if entry else ""

        result = re.sub(r"\[image #(\d+)\]", _replace_image, text)
        result = re.sub(r"\[audio #(\d+)\]", _replace_audio, result)
        result = re.sub(r"\[video #(\d+)\]", _replace_video, result)
        result = re.sub(r"\[file #(\d+)\]", _replace_file, result)
        result = re.sub(r"\[paste #\d+(?: \+\d+ lines| \d+ chars)\]", "", result)
        return result.strip()

    def _extract_clipboard_images(self, text: str) -> tuple[list[bytes], int]:
        """Extract image bytes from markers in text.

        Returns (images, missing_count) where missing_count is the number of
        persistent [image:uuid] markers whose media files could not be found.
        """
        images: list[bytes] = []
        seen: set[int] = set()
        for m in re.finditer(r"\[image #(\d+)\]", text):
            idx = int(m.group(1))
            if idx in seen:
                continue
            seen.add(idx)
            entry = self._clipboard_images.get(idx)
            if entry is None:
                continue
            _, path = entry
            try:
                with open(path, "rb") as f:
                    images.append(f.read())
            except OSError:
                _log.warning("failed to read clipboard image %s", path, exc_info=True)
        # Also resolve persistent [image:{uuid}] markers from history
        missing = 0
        seen_uuids: set[str] = set()
        for m in re.finditer(r"\[image:([^\]]+)\]", text):
            uid = m.group(1)
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
            p = self._find_media_by_uuid(uid)
            if p is not None:
                try:
                    images.append(p.read_bytes())
                except OSError:
                    missing += 1
            else:
                missing += 1
        self._clipboard_images.clear()
        self._clipboard_image_notes.clear()
        self._clipboard_image_counter = 0
        return images, missing

    def _extract_clipboard_image_contents(self, text: str) -> list[Any]:
        """Like _extract_clipboard_images but returns ImageContent with dimension notes."""
        from tau.message.types import ImageContent as _IC

        contents = []
        seen: set[int] = set()
        for m in re.finditer(r"\[image #(\d+)\]", text):
            idx = int(m.group(1))
            if idx in seen:
                continue
            seen.add(idx)
            entry = self._clipboard_images.get(idx)
            if entry is None:
                continue
            _, path = entry
            try:
                with open(path, "rb") as f:
                    data = f.read()
                note = self._clipboard_image_notes.get(idx)
                contents.append(_IC(images=[data], dimension_note=note))
            except OSError:
                _log.warning("failed to read clipboard image content %s", path, exc_info=True)
        # Also resolve persistent [image:{uuid}] markers from history
        seen_uuids: set[str] = set()
        for m in re.finditer(r"\[image:([^\]]+)\]", text):
            uid = m.group(1)
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
            p = self._find_media_by_uuid(uid)
            if p is not None:
                with contextlib.suppress(OSError):
                    contents.append(_IC(images=[p.read_bytes()]))
        self._clipboard_images.clear()
        self._clipboard_image_notes.clear()
        self._clipboard_image_counter = 0
        return contents

    # ── At-mentions ───────────────────────────────────────────────────────────

    def _expand_at_mentions(self, text: str) -> str:
        sm = self._runtime.session_manager
        cwd = sm.cwd if sm is not None else Path.cwd()
        pattern = re.compile(r"@([^\s@]+)")
        attachments: list[str] = []
        for m in pattern.finditer(text):
            raw_path = m.group(1)
            path = Path(raw_path) if Path(raw_path).is_absolute() else cwd / raw_path
            if path.is_file():
                try:
                    content = path.read_text(errors="replace")
                    attachments.append(f'<file path="{raw_path}">\n{content}\n</file>')
                except OSError:
                    _log.debug("failed to read @mention file %s", path, exc_info=True)
        if not attachments:
            return text
        return "\n".join(attachments) + "\n\n" + text

    # ── Slash message factory ─────────────────────────────────────────────────

    def _make_slash_message(self, text: str) -> object:
        from tau.message.types import SkillInvocationMessage, TemplateInvocationMessage, UserMessage

        if text.startswith("/skill:"):
            from tau.skills.registry import skill_registry

            skill_part = text[7:].strip().split(None, 1)
            skill_name = skill_part[0].lower() if skill_part else ""
            skill_args = skill_part[1] if len(skill_part) > 1 else ""
            skill = skill_registry.get(skill_name)
            if skill is not None:
                return SkillInvocationMessage(
                    name=skill_name, args=skill_args, content=skill.content
                )

        parts = text[1:].strip().split(None, 1)
        name = parts[0].lower() if parts else ""
        args_str = parts[1] if len(parts) > 1 else ""
        if self._runtime.commands.get(name) is None:
            from tau.prompts.registry import prompt_registry

            tmpl = prompt_registry.get(name)
            if tmpl is not None:
                expanded = prompt_registry.expand(name, args_str)
                if expanded is not None:
                    return TemplateInvocationMessage(
                        name=name, args=args_str, expanded_content=expanded
                    )

        return UserMessage.from_text(text)

    # ── History ───────────────────────────────────────────────────────────────

    def load_history(self) -> None:
        path = _history_path()
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            entries: list[str] = []
            current: list[str] = []
            for line in lines:
                if line == "\x00":
                    if current:
                        entries.append("\n".join(current))
                        current = []
                else:
                    current.append(line)
            if current:
                entries.append("\n".join(current))
            self._layout.input.replace_history(entries)
        except OSError:
            _log.debug("failed to load history", exc_info=True)

    def save_history(self) -> None:
        history = self._layout.input.history
        if not history:
            return
        path = _history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            chunks: list[str] = []
            for entry in history[-500:]:
                chunks.append(entry.replace("\x00", ""))
                chunks.append("\x00")
            path.write_text("\n".join(chunks), encoding="utf-8")
        except OSError:
            _log.debug("failed to save history", exc_info=True)


def _history_path():
    from tau.settings.paths import CONFIG_DIR_PATH

    return CONFIG_DIR_PATH / "history"

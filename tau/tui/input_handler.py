from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tau.runtime.service import Runtime
    from tau.tui.components.layout import Layout
    from tau.tui.tui import TUI


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
        self._turn_has_content: bool = False
        self._last_user_text: str = ""

        # Maps session counter → (uuid, absolute_path) for media stored in the project media dir.
        self._clipboard_images: dict[int, tuple[str, str]] = {}
        self._clipboard_image_notes: dict[int, str] = {}
        self._clipboard_image_counter: int = 0
        self._clipboard_audio: dict[int, tuple[str, str]] = {}
        self._clipboard_audio_counter: int = 0
        self._clipboard_video: dict[int, tuple[str, str]] = {}
        self._clipboard_video_counter: int = 0
        self._pasted_texts: dict[int, str] = {}
        self._paste_counter: int = 0

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

    def _notify(self, message: str, type: str = "info") -> None:  # noqa: A002
        import time
        from typing import cast
        from tau.message.types import CustomMessage, TextContent, LinesContent, ImageContent
        custom_type = "tool" if type == "tool" else "system"
        msg = CustomMessage(
            custom_type=custom_type,
            timestamp=time.time(),
            contents=cast(list[TextContent | ImageContent | LinesContent], [TextContent(content=message)]),
        )
        self._layout.add_message(msg)
        self._tui.request_render()

    def _on_submit(self, text: str) -> None:
        from tau.message.types import UserMessage

        self.save_history()
        agent = self._runtime.agent

        if text.startswith("/") or text.startswith("!"):
            self._extract_clipboard_images(text)
            self._extract_clipboard_audio(text)
            self._extract_clipboard_video(text)
            self._pasted_texts.clear()
            self._paste_counter = 0
            if text.startswith("/"):
                self._layout.add_message(self._make_slash_message(text))
                self._tui.request_render()
            asyncio.ensure_future(self._invoke(text))
            return

        images = self._extract_clipboard_images(text)
        audio = self._extract_clipboard_audio(text)
        video = self._extract_clipboard_video(text)
        expanded = self._expand_pasted_texts(text)

        if agent is not None and (images or audio or video):
            from tau.inference.model.types import Modality
            model = getattr(getattr(agent._engine, "llm", None), "model", None)
            if model is not None:
                if images and Modality.Image not in model.input:
                    self._notify(f"Image modality is not supported by {model.name}.", type="error")
                    return
                if audio and Modality.Audio not in model.input:
                    self._notify(f"Audio modality is not supported by {model.name}.", type="error")
                    return
                if video and Modality.Video not in model.input:
                    self._notify(f"Video modality is not supported by {model.name}.", type="error")
                    return

        if agent is not None and not agent.is_idle():
            asyncio.ensure_future(self._steer(expanded, images))
            return

        if images:
            user_msg = UserMessage.with_images(text, images)
        elif audio:
            user_msg = UserMessage.with_audio(text, [*audio])  # type: ignore[arg-type]
        elif video:
            user_msg = UserMessage.with_video(text, [*video])  # type: ignore[arg-type]
        else:
            user_msg = UserMessage.from_text(text)
        self._layout.add_message(user_msg)
        self._last_user_text = text
        self._turn_has_content = False
        self._tui.request_render()
        asyncio.ensure_future(self._invoke(self._expand_at_mentions(expanded), images, audio, video))

    def _on_followup(self, text: str) -> None:
        images = self._extract_clipboard_images(text)
        expanded = self._expand_pasted_texts(text)
        asyncio.ensure_future(self._queue_followup(expanded, images, display_text=text))

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
                "".join(c.content for c in getattr(msg, "contents", []) if isinstance(c, TextContent))
                for msg in queue.snapshot()
            ]

        all_texts = _extract_texts(engine.state.steering_queue) + _extract_texts(engine.state.follow_up_queue)
        all_texts = [t for t in all_texts if t.strip()]
        if not all_texts:
            return []
        engine.clear_all_queues()
        self._layout.set_pending_queue([], [])
        return all_texts

    def _on_dequeue(self) -> None:
        all_texts = self._take_queued_texts()
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
            asyncio.ensure_future(self._run_queued_next(queued))
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
    ) -> None:
        self._invoke_task = asyncio.current_task()
        try:
            from tau.agent.types import PromptOptions
            if images or audio or video:
                options = PromptOptions(
                    images=images or [],
                    audio=audio or [],
                    video=video or [],
                )
            else:
                options = None
            await self._runtime.user_input(text, options)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._layout.spinner.set_label(f"error: {exc}")
            self._layout.spinner.stop()
            self._tui.request_render()
        finally:
            self._invoke_task = None

    async def _steer(self, text: str, images: list[bytes] | None = None) -> None:
        agent = self._runtime.agent
        if agent is None:
            return
        from tau.message.types import UserMessage
        try:
            expanded = self._expand_at_mentions(text)
            msg = UserMessage.with_images(expanded, images) if images else UserMessage.from_text(expanded)
            await agent._engine.steer(msg)
        except Exception as exc:
            self._layout.spinner.set_label(f"error: {exc}")
            self._tui.request_render()

    async def _queue_followup(
        self,
        text: str,
        images: list[bytes] | None = None,
        display_text: str | None = None,
    ) -> None:
        from tau.message.types import UserMessage

        shown = display_text if display_text is not None else text

        def _make_msg() -> UserMessage:
            expanded = self._expand_at_mentions(text)
            return UserMessage.with_images(expanded, images) if images else UserMessage.from_text(expanded)

        agent = self._runtime.agent
        if agent is None or agent.is_idle():
            user_msg = UserMessage.with_images(shown, images) if images else UserMessage.from_text(shown)
            self._layout.add_message(user_msg)
            self._tui.request_render()
            await self._invoke(self._expand_at_mentions(text), images)
        else:
            try:
                await agent._engine.follow_up(_make_msg())
            except Exception as exc:
                self._layout.spinner.set_label(f"error: {exc}")
                self._tui.request_render()

    # ── Paste handling ────────────────────────────────────────────────────────

    _AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".weba"}
    _VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv"}

    def _paste_file(self, src_path: str) -> None:
        """Detect file type by extension and route to the appropriate store method."""
        try:
            suffix = "." + src_path.rsplit(".", 1)[-1].lower() if "." in src_path else ".png"
            with open(src_path, "rb") as f:
                data = f.read()
            if suffix in self._AUDIO_SUFFIXES:
                self._store_clipboard_audio(data, suffix)
            elif suffix in self._VIDEO_SUFFIXES:
                self._store_clipboard_video(data, suffix)
            else:
                self._store_clipboard_image(data, suffix)
        except Exception:
            pass

    def _on_paste(self) -> None:
        import io
        try:
            from PIL import ImageGrab
            item = ImageGrab.grabclipboard()
            if item is None:
                return
            if isinstance(item, list):
                for p in item:
                    self._paste_file(str(p))
                return
            buf = io.BytesIO()
            item.save(buf, format="PNG")
            self._store_clipboard_image(buf.getvalue(), ".png")
        except Exception:
            pass

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
            pass
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
        except Exception:
            pass

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
        except Exception:
            pass

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
        except Exception:
            pass

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
                pass
        # Also resolve persistent [audio:{uuid}] markers from history
        seen_uuids: set[str] = set()
        for m in re.finditer(r"\[audio:([^\]]+)\]", text):
            uid = m.group(1)
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
            p = self._find_media_by_uuid(uid)
            if p is not None:
                try:
                    audio.append(p.read_bytes())
                except OSError:
                    pass
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
                pass
        # Also resolve persistent [video:{uuid}] markers from history
        seen_uuids: set[str] = set()
        for m in re.finditer(r"\[video:([^\]]+)\]", text):
            uid = m.group(1)
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
            p = self._find_media_by_uuid(uid)
            if p is not None:
                try:
                    video.append(p.read_bytes())
                except OSError:
                    pass
        self._clipboard_video.clear()
        self._clipboard_video_counter = 0
        return video

    def _on_paste_text(self, text: str) -> None:
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
        """Replace session-scoped [image/audio/video #N] markers with persistent [type:{uuid}] ones.

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

        result = re.sub(r"\[image #(\d+)\]", _replace_image, text)
        result = re.sub(r"\[audio #(\d+)\]", _replace_audio, result)
        result = re.sub(r"\[video #(\d+)\]", _replace_video, result)
        result = re.sub(r"\[paste #\d+(?: \+\d+ lines| \d+ chars)\]", "", result)
        return result.strip()

    def _extract_clipboard_images(self, text: str) -> list[bytes]:
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
                pass
        # Also resolve persistent [image:{uuid}] markers from history
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
                    pass
        self._clipboard_images.clear()
        self._clipboard_image_notes.clear()
        self._clipboard_image_counter = 0
        return images

    def _extract_clipboard_image_contents(self, text: str) -> "list[Any]":
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
                pass
        # Also resolve persistent [image:{uuid}] markers from history
        seen_uuids: set[str] = set()
        for m in re.finditer(r"\[image:([^\]]+)\]", text):
            uid = m.group(1)
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
            p = self._find_media_by_uuid(uid)
            if p is not None:
                try:
                    contents.append(_IC(images=[p.read_bytes()]))
                except OSError:
                    pass
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
                    pass
        if not attachments:
            return text
        return "\n".join(attachments) + "\n\n" + text

    # ── Slash message factory ─────────────────────────────────────────────────

    def _make_slash_message(self, text: str) -> object:
        from tau.message.types import UserMessage, TemplateInvocationMessage, SkillInvocationMessage

        if text.startswith("/skill:"):
            from tau.skills.registry import skill_registry
            skill_part = text[7:].strip().split(None, 1)
            skill_name = skill_part[0].lower() if skill_part else ""
            skill_args = skill_part[1] if len(skill_part) > 1 else ""
            skill = skill_registry.get(skill_name)
            if skill is not None:
                return SkillInvocationMessage(name=skill_name, args=skill_args, content=skill.content)

        parts = text[1:].strip().split(None, 1)
        name = parts[0].lower() if parts else ""
        args_str = parts[1] if len(parts) > 1 else ""
        if self._runtime.commands.get(name) is None:
            from tau.prompts.registry import prompt_registry
            tmpl = prompt_registry.get(name)
            if tmpl is not None:
                expanded = prompt_registry.expand(name, args_str)
                if expanded is not None:
                    return TemplateInvocationMessage(name=name, args=args_str, expanded_content=expanded)

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
            self._layout.input._history = entries[-500:]
        except OSError:
            pass

    def save_history(self) -> None:
        history = self._layout.input._history
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
            pass


def _history_path():
    from tau.settings.paths import CONFIG_DIR_PATH
    return CONFIG_DIR_PATH / "history"

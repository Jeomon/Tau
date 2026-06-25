"""Space-hold voice recording state machine.

Talks to the editor only through the public ``ctx.ui`` (UIContext) surface —
no reaching into rendering internals — so it stays version-stable when
installed globally.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import audio, caret
from .config import RELEASE_GAP, VoiceConfig

_log = logging.getLogger(__name__)


class VoiceController:
    """State machine for space-hold voice recording.

    Flow
    ────
    1. Initial space press  → consume key, start 2 s timer + release-gap watcher.
       Mode: waiting.  Mic is NOT open yet.
    2. Space auto-repeats   → update last-event timestamp, stay consumed.
    3. 2 s elapses          → open mic, switch to mode: recording, pulse caret.
    4. Key released         → detected via Kitty release event OR a repeat-gap.
       • mode was waiting   → held < 2 s: type one space, go idle.
       • mode was recording → held ≥ 2 s: stop mic, transcribe.
    """

    def __init__(self, ui: Any, cfg: VoiceConfig, settings: Any = None) -> None:
        self._ui = ui
        self._cfg = cfg
        self._settings = settings

        # Toggled by the /voice command. When disabled, space behaves normally.
        self._enabled = True

        # Modes: idle | waiting | recording | transcribing
        self._mode = "idle"
        self._press_time: float = 0.0
        self._last_space_time: float = 0.0
        # Observed auto-repeat interval (seconds). Measured from the gap between
        # consecutive space events so the release watcher can trip just above the
        # real cadence instead of a flat, conservative delay. 0.0 = not measured.
        self._repeat_interval: float = 0.0

        self._activation_task: asyncio.Task | None = None
        self._watcher_task: asyncio.Task | None = None
        self._caret_task: asyncio.Task | None = None

        self._audio_frames: list[Any] = []
        self._stream: Any = None

    # ── Enable/disable + model resolution ──────────────────────────────────────

    def toggle(self) -> bool:
        """Flip voice capture on/off. Returns the new enabled state."""
        self._enabled = not self._enabled
        if not self._enabled and self._mode in ("waiting", "recording"):
            # Tear down any in-flight capture so a held space is released cleanly.
            self._handle_release()
        return self._enabled

    def _resolve_stt(self) -> tuple[str, str]:
        """Resolve the STT (voice) model/provider: settings ``model.voice`` first,
        then the extension's configured defaults.
        """
        if self._settings is not None:
            ref = self._settings.get_model_ref("voice")
            if ref is not None and ref.id:
                return ref.id, (ref.provider or self._cfg.stt_provider)
        return self._cfg.stt_model, self._cfg.stt_provider

    # ── Transient status placeholder (errors only) ─────────────────────────────

    def _set_status(self, text: str) -> None:
        self._ui.set_input_placeholder(text)

    def _clear_status(self) -> None:
        self._ui.reset_input_placeholder()

    # ── Caret animation ───────────────────────────────────────────────────────

    def _start_caret(self, ramp: list[int]) -> None:
        """Replace the text cursor with a pulsing, coloured block."""
        if self._caret_task and not self._caret_task.done():
            self._caret_task.cancel()
        self._caret_task = asyncio.ensure_future(self._animate_caret(ramp))

    def _stop_caret(self) -> None:
        """Stop the animation and restore the normal text cursor."""
        if self._caret_task and not self._caret_task.done():
            self._caret_task.cancel()
        self._caret_task = None
        self._ui.reset_input_cursor()

    async def _animate_caret(self, ramp: list[int]) -> None:
        i = 0
        while True:
            self._ui.set_input_cursor(caret.frame(ramp, i))
            i += 1
            await asyncio.sleep(caret.FRAME_INTERVAL)

    # ── Background tasks ──────────────────────────────────────────────────────

    def _cancel_task(self, task: asyncio.Task | None) -> None:
        if task and not task.done():
            task.cancel()

    async def _activation_timer(self) -> None:
        """Open the mic and start the recording caret after hold_ms."""
        await asyncio.sleep(self._cfg.hold_ms / 1000)
        if self._mode != "waiting":
            return
        if not self._open_stream():
            self._mode = "idle"
            return
        self._mode = "recording"
        self._start_caret(caret.RECORDING)

    def _release_gap(self) -> float:
        """Seconds of silence that mean the key was released.

        Once the auto-repeat cadence is known we trip just above it (≈2.5×,
        clamped to 0.25–RELEASE_GAP) so release feels immediate. Before any
        repeat is measured — i.e. still inside the OS initial-repeat delay — we
        stay at the conservative RELEASE_GAP to avoid a false release.
        """
        if self._repeat_interval <= 0.0:
            return RELEASE_GAP
        return max(0.25, min(RELEASE_GAP, self._repeat_interval * 2.5))

    async def _release_watcher(self) -> None:
        """Fallback release detector for terminals without Kitty key-up events.

        Polls every 50 ms. If no space event has arrived for the adaptive
        release gap the key has been released — trigger the same logic as a
        Kitty key-up.
        """
        while True:
            await asyncio.sleep(0.05)
            if self._mode not in ("waiting", "recording"):
                return
            if time.monotonic() - self._last_space_time >= self._release_gap():
                self._handle_release()
                return

    # ── Microphone ────────────────────────────────────────────────────────────

    def _open_stream(self) -> bool:
        try:
            import sounddevice as sd  # type: ignore[import-untyped]
        except ImportError:
            _log.error("sounddevice not installed — voice extension cannot record")
            self._set_status("Voice: sounddevice missing (check extension deps)")
            asyncio.ensure_future(self._clear_after(3.0))
            return False

        self._audio_frames = []

        def _cb(indata: Any, *_: Any) -> None:
            self._audio_frames.append(indata.copy())

        try:
            self._stream = sd.InputStream(
                samplerate=self._cfg.sample_rate,
                channels=1,
                dtype="int16",
                callback=_cb,
            )
            self._stream.start()
        except Exception as exc:
            _log.error("failed to open mic: %s", exc)
            self._set_status(f"Voice: mic error — {exc!s:.40}")
            asyncio.ensure_future(self._clear_after(3.0))
            return False

        return True

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ── Release logic (shared by Kitty and watcher) ───────────────────────────

    def _handle_release(self) -> None:
        if self._mode not in ("waiting", "recording"):
            return

        prior_mode = self._mode
        self._cancel_task(self._activation_task)
        self._cancel_task(self._watcher_task)
        self._activation_task = None
        self._watcher_task = None

        self._close_stream()
        self._stop_caret()

        if prior_mode == "waiting":
            # Short press — type the space we held back
            self._mode = "idle"
            self._ui.insert_input_text(" ")
        else:
            # Long press — send captured audio to STT
            asyncio.ensure_future(self._transcribe())

    # ── Transcription ─────────────────────────────────────────────────────────

    async def _transcribe(self) -> None:
        self._mode = "transcribing"
        self._start_caret(caret.TRANSCRIBING)

        try:
            if not self._audio_frames:
                self._stop_caret()
                self._mode = "idle"
                return

            wav_bytes = audio.encode_wav(self._audio_frames, self._cfg.sample_rate)
            model_id, provider = self._resolve_stt()
            text = await audio.transcribe_wav(wav_bytes, model_id, provider)

            self._stop_caret()

            if text.strip():
                self._ui.insert_input_text(text.strip())

            self._mode = "idle"

        except Exception as exc:
            _log.exception("voice transcription failed")
            self._stop_caret()
            msg = str(exc)
            suffix = "..." if len(msg) > 45 else ""
            self._set_status(f"Transcription failed: {msg[:45]}{suffix}")
            self._mode = "idle"
            asyncio.ensure_future(self._clear_after(3.0))

    async def _clear_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self._clear_status()

    # ── Input intercept ───────────────────────────────────────────────────────

    def on_key(self, event: Any) -> bool | None:
        from tau.tui.input import KeyEvent

        if not self._enabled or not isinstance(event, KeyEvent):
            return None

        is_space = event.matches("space")

        # Non-space key while active: abort cleanly and restore the held space
        if not is_space:
            if self._mode in ("waiting", "recording"):
                self._cancel_task(self._activation_task)
                self._cancel_task(self._watcher_task)
                self._activation_task = None
                self._watcher_task = None
                self._close_stream()
                self._stop_caret()
                self._mode = "idle"
                self._ui.insert_input_text(" ")
            return None

        # ── Space key ────────────────────────────────────────────────────────

        # Kitty key-release
        if event.released:
            self._handle_release()
            return True

        # Initial press (idle → waiting)
        if self._mode == "idle" and not event.repeat:
            self._press_time = time.monotonic()
            self._last_space_time = self._press_time
            # Forget the previous hold's cadence so the release watcher stays
            # conservative until this hold's repeats are measured — otherwise it
            # could false-trip during the OS initial-repeat delay.
            self._repeat_interval = 0.0
            self._mode = "waiting"
            self._activation_task = asyncio.ensure_future(self._activation_timer())
            self._watcher_task = asyncio.ensure_future(self._release_watcher())
            return True  # consume; space will be re-emitted if released early

        # Auto-repeat while waiting or recording — keep timestamp fresh and
        # learn the repeat cadence (used to size the release gap).
        if self._mode in ("waiting", "recording"):
            now = time.monotonic()
            gap = now - self._last_space_time
            # Ignore the long initial key-repeat delay and any outliers; only
            # steady-state intervals (< RELEASE_GAP) describe the real cadence.
            if 0.0 < gap < RELEASE_GAP:
                self._repeat_interval = gap
            self._last_space_time = now
            return True

        # Consume space during transcription too
        if self._mode == "transcribing":
            return True

        return None

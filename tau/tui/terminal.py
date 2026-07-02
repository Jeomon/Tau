from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

# termios/tty are POSIX-only. On Windows the console is driven through the
# Win32 console API instead (see the _*_windows helpers on Terminal).
# The guard tests ``sys.platform`` directly so type checkers can statically
# narrow the import as bound on POSIX targets.
_IS_WINDOWS = sys.platform == "win32"

if sys.platform != "win32":
    import termios
    import tty

# Windows has no SIGWINCH, so resize is detected by polling the console size.
_WIN_RESIZE_POLL_INTERVAL = 0.25

# ── Terminal Capabilities ─────────────────────────────────────────────────────

ImageProtocol = Literal["kitty", "iterm2"] | None


@dataclass
class TerminalCapabilities:
    images: ImageProtocol
    truecolor: bool
    hyperlinks: bool


@dataclass
class CellDimensions:
    width_px: int
    height_px: int


_cached: TerminalCapabilities | None = None
_cell_dims = CellDimensions(width_px=9, height_px=18)


def get_cell_dimensions() -> CellDimensions:
    return _cell_dims


def set_cell_dimensions(dims: CellDimensions) -> None:
    global _cell_dims
    _cell_dims = dims


def _probe_cell_dimensions() -> CellDimensions:
    """Read pixel and cell sizes from the terminal via TIOCGWINSZ."""
    try:
        import fcntl
        import struct
        import termios as _termios

        buf = struct.pack("HHHH", 0, 0, 0, 0)
        res = fcntl.ioctl(1, _termios.TIOCGWINSZ, buf)
        rows, cols, width_px, height_px = struct.unpack("HHHH", res)
        if rows > 0 and cols > 0 and width_px > 0 and height_px > 0:
            return CellDimensions(width_px=width_px // cols, height_px=height_px // rows)
    except Exception:
        pass
    return CellDimensions(width_px=9, height_px=18)


def _tmux_forwards_hyperlinks() -> bool:
    try:
        import subprocess

        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{client_termfeatures}"],
            capture_output=True,
            text=True,
            timeout=0.25,
        )
        return "hyperlinks" in result.stdout.split(",")
    except Exception:
        return False


def detect_capabilities() -> TerminalCapabilities:
    term = os.environ.get("TERM", "").lower()
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    terminal_emulator = os.environ.get("TERMINAL_EMULATOR", "").lower()
    color_term = os.environ.get("COLORTERM", "").lower()
    truecolor = color_term in ("truecolor", "24bit")

    if os.environ.get("TMUX") or term.startswith("tmux"):
        return TerminalCapabilities(
            images=None, truecolor=truecolor, hyperlinks=_tmux_forwards_hyperlinks()
        )

    if term.startswith("screen"):
        return TerminalCapabilities(images=None, truecolor=truecolor, hyperlinks=False)

    if os.environ.get("KITTY_WINDOW_ID") or term_program == "kitty":
        return TerminalCapabilities(images="kitty", truecolor=True, hyperlinks=True)

    if term_program == "ghostty" or "ghostty" in term or os.environ.get("GHOSTTY_RESOURCES_DIR"):
        return TerminalCapabilities(images="kitty", truecolor=True, hyperlinks=True)

    if os.environ.get("WEZTERM_PANE") or term_program == "wezterm":
        return TerminalCapabilities(images="kitty", truecolor=True, hyperlinks=True)

    if os.environ.get("ITERM_SESSION_ID") or term_program == "iterm.app":
        return TerminalCapabilities(images="iterm2", truecolor=True, hyperlinks=True)

    if os.environ.get("WT_SESSION"):
        return TerminalCapabilities(images=None, truecolor=True, hyperlinks=True)

    if term_program == "vscode":
        return TerminalCapabilities(images=None, truecolor=True, hyperlinks=True)

    if term_program == "alacritty":
        return TerminalCapabilities(images=None, truecolor=True, hyperlinks=False)

    if terminal_emulator == "jetbrains-jediterm":
        return TerminalCapabilities(images=None, truecolor=True, hyperlinks=False)

    return TerminalCapabilities(images=None, truecolor=truecolor, hyperlinks=False)


def get_capabilities() -> TerminalCapabilities:
    global _cached
    if _cached is None:
        _cached = detect_capabilities()
        set_cell_dimensions(_probe_cell_dimensions())
    return _cached


def reset_capabilities_cache() -> None:
    global _cached
    _cached = None


def is_image_line(line: str) -> bool:
    return "\x1b_G" in line or "\x1b]1337;" in line


# ── Terminal ──────────────────────────────────────────────────────────────────


class Terminal:
    """
    Owns stdin/stdout in raw mode for the duration of the TUI session.

    This class is a wrapper around low-level terminal operations using ANSI escape codes.
    It manages terminal initialization, cleanup, input/output, cursor control, and screen rendering.

    Key responsibilities:
    - Enter/restore raw mode (immediate keyboard input, no echo, no line buffering)
    - Switch to/from alternate screen buffer (clean workspace)
    - Write buffered output to terminal with flushing
    - Track terminal size and fire callbacks on resize
    - Hide/show cursor and control cursor position
    - Clear screen portions and manage display
    - Handle mouse events and special paste mode
    - Synchronize screen updates to prevent flicker

    Typical usage:
        with Terminal() as terminal:
            terminal.hide_cursor()
            # Build TUI display
            terminal.write("Hello")
            terminal.flush()
            # Read keyboard input
            key = terminal.read_raw()

    The Terminal class uses ANSI escape sequences (\x1b[...) to communicate with the terminal.
    These sequences tell the terminal what to do (move cursor, change colors, clear screen, etc).
    """

    def __init__(self) -> None:
        """
        Initialize the Terminal object.

        Sets up internal state:
        - _original_termios: Saves original terminal settings (restored on exit)
        - _resize_callbacks: List of functions to call when terminal is resized
        - _prev_sigwinch: Saves original resize signal handler
        - width/height: Current terminal dimensions in characters
        """
        self._original_termios: list | None = None
        self._resize_callbacks: list[Callable[[], None]] = []
        self._prev_sigwinch: Any = None
        # Saved Windows console modes (restored on exit); unused on POSIX.
        self._win_stdin_mode: int | None = None
        self._win_stdout_mode: int | None = None
        # Windows resize poller (no SIGWINCH there); unused on POSIX.
        self._win_resize_thread: threading.Thread | None = None
        self._win_resize_stop: threading.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.width, self.height = self._get_size()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def enter_raw_mode(self) -> None:
        """
        Switch stdin to raw mode and install SIGWINCH handler.

        Raw mode disables line buffering, echo, and signal processing.
        Characters are sent immediately without waiting for Enter.
        Also saves the original terminal settings and signal handlers for later restoration.
        """
        if _IS_WINDOWS:
            self._enter_raw_mode_windows()
            return
        fd = sys.stdin.fileno()
        self._original_termios = termios.tcgetattr(fd)  # Save original settings
        tty.setraw(fd)  # Switch to raw mode (no echo, no buffering)
        self._prev_sigwinch = signal.signal(
            signal.SIGWINCH, self._on_resize
        )  # Detect terminal resize

    def exit_raw_mode(self) -> None:
        """
        Restore terminal to its original state.

        Reverses the changes made by enter_raw_mode():
        - Restores original terminal settings (echo, buffering, signals re-enabled)
        - Restores the original signal handler for terminal resize events
        """
        if _IS_WINDOWS:
            self._exit_raw_mode_windows()
            return
        if self._original_termios is not None:
            # TCSADRAIN: wait for pending output to finish before restoring
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._original_termios)
            self._original_termios = None
        if self._prev_sigwinch is not None:
            # Restore the previous resize signal handler
            signal.signal(signal.SIGWINCH, self._prev_sigwinch)
            self._prev_sigwinch = None

    # -------------------------------------------------------------------------
    # Windows console backend
    # -------------------------------------------------------------------------

    # Win32 console-mode flags (see docs.microsoft.com SetConsoleMode).
    _WIN_ENABLE_PROCESSED_INPUT = 0x0001
    _WIN_ENABLE_LINE_INPUT = 0x0002
    _WIN_ENABLE_ECHO_INPUT = 0x0004
    _WIN_ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
    _WIN_ENABLE_PROCESSED_OUTPUT = 0x0001
    _WIN_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    _WIN_STD_INPUT_HANDLE = -10
    _WIN_STD_OUTPUT_HANDLE = -11

    def _enter_raw_mode_windows(self) -> None:
        """Put the Windows console into raw + VT mode via the Win32 console API.

        termios/tty/SIGWINCH do not exist on Windows, so instead we clear the
        console's line-input/echo/processing flags (raw keyboard input) and turn
        on virtual-terminal processing so ANSI escape sequences are honoured on
        both input and output. Resize is not delivered as a signal on Windows;
        the size is re-read lazily by ``_get_size()``.
        """
        import ctypes

        if sys.platform != "win32":  # pragma: no cover - keeps type checkers on win32 stubs
            return

        k32 = ctypes.windll.kernel32
        hin = k32.GetStdHandle(self._WIN_STD_INPUT_HANDLE)
        hout = k32.GetStdHandle(self._WIN_STD_OUTPUT_HANDLE)

        in_mode = ctypes.c_uint32()
        if k32.GetConsoleMode(hin, ctypes.byref(in_mode)):
            self._win_stdin_mode = in_mode.value
            new_in = (
                in_mode.value
                & ~(
                    self._WIN_ENABLE_PROCESSED_INPUT
                    | self._WIN_ENABLE_LINE_INPUT
                    | self._WIN_ENABLE_ECHO_INPUT
                )
            ) | self._WIN_ENABLE_VIRTUAL_TERMINAL_INPUT
            k32.SetConsoleMode(hin, new_in)

        out_mode = ctypes.c_uint32()
        if k32.GetConsoleMode(hout, ctypes.byref(out_mode)):
            self._win_stdout_mode = out_mode.value
            k32.SetConsoleMode(
                hout,
                out_mode.value
                | self._WIN_ENABLE_PROCESSED_OUTPUT
                | self._WIN_ENABLE_VIRTUAL_TERMINAL_PROCESSING,
            )

        self._start_resize_poller_windows()

    def _exit_raw_mode_windows(self) -> None:
        """Restore the Windows console modes saved by _enter_raw_mode_windows."""
        import ctypes

        if sys.platform != "win32":  # pragma: no cover - keeps type checkers on win32 stubs
            return

        self._stop_resize_poller_windows()

        k32 = ctypes.windll.kernel32
        if self._win_stdin_mode is not None:
            k32.SetConsoleMode(k32.GetStdHandle(self._WIN_STD_INPUT_HANDLE), self._win_stdin_mode)
            self._win_stdin_mode = None
        if self._win_stdout_mode is not None:
            k32.SetConsoleMode(k32.GetStdHandle(self._WIN_STD_OUTPUT_HANDLE), self._win_stdout_mode)
            self._win_stdout_mode = None

    def _start_resize_poller_windows(self) -> None:
        """Start a daemon thread that polls the console size (Windows has no SIGWINCH).

        Windows delivers no resize signal, so a background thread samples
        ``_get_size()`` on an interval and fires the registered resize callbacks
        (on the event loop thread) whenever the dimensions change.
        """
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._win_resize_stop = threading.Event()
        self._win_resize_thread = threading.Thread(
            target=self._win_resize_poll_loop,
            args=(self._win_resize_stop,),
            name="tau-tui-resize",
            daemon=True,
        )
        self._win_resize_thread.start()

    def _stop_resize_poller_windows(self) -> None:
        if self._win_resize_stop is not None:
            self._win_resize_stop.set()
        self._win_resize_thread = None
        self._win_resize_stop = None
        self._loop = None

    def _win_resize_poll_loop(self, stop: threading.Event) -> None:
        """Poll the terminal size and fire callbacks on change until ``stop`` is set."""
        while not stop.wait(_WIN_RESIZE_POLL_INTERVAL):
            new_size = self._get_size()
            if new_size == (self.width, self.height):
                continue
            self.width, self.height = new_size
            loop = self._loop
            if loop is None:
                for cb in list(self._resize_callbacks):
                    cb()
                continue
            for cb in list(self._resize_callbacks):
                try:
                    loop.call_soon_threadsafe(cb)
                except RuntimeError:
                    return  # event loop already closed

    def enter_alt_screen(self) -> None:
        """
        Switch to alternate screen buffer (like vim or less does).

        This creates a clean workspace separate from the main terminal.
        Your terminal history/scrollback is preserved underneath.
        Sends three ANSI codes:
        - \x1b[?1049h: Enable alternate screen buffer
        - \x1b[2J: Clear the screen completely
        - \x1b[H: Move cursor to top-left corner (home position)
        """
        self.write_flush("\x1b[?1049h\x1b[2J\x1b[H")

    def exit_alt_screen(self) -> None:
        """
        Switch back to the main screen buffer.

        Restores your terminal to how it was before enter_alt_screen().
        Your terminal history/scrollback is still there.
        Sends ANSI code:
        - \x1b[?1049l: Disable alternate screen buffer (restore main)
        """
        self.write_flush("\x1b[?1049l")

    def __enter__(self) -> Terminal:
        """
        Context manager entry: set up the terminal for TUI use.

        Enables raw mode only — no alternate screen.  Content renders into
        the main buffer so the terminal's native scrollback works.
        """
        self.enter_raw_mode()
        return self

    def __exit__(self, *_: object) -> None:
        """
        Context manager exit: restore the terminal to normal state.

        Shows the cursor and restores raw mode.  The caller is responsible
        for moving the cursor past the last rendered line before calling
        this so the shell prompt appears below the TUI output.
        """
        self.show_cursor()
        self.exit_raw_mode()

    # -------------------------------------------------------------------------
    # Output
    # -------------------------------------------------------------------------

    def write(self, data: str) -> None:
        """
        Write data to the output buffer (doesn't send to screen yet).

        The data is stored in a temporary buffer and waits for a flush() call.
        This allows batching multiple writes before sending to screen.
        """
        sys.stdout.write(data)

    def flush(self) -> None:
        """
        Send all buffered output to the terminal immediately.

        Forces everything in the buffer to be displayed on screen right away.
        Clears the buffer after sending.
        """
        sys.stdout.flush()

    def write_flush(self, data: str) -> None:
        """
        Write data and immediately send it to the terminal (convenience method).

        Combines write() + flush() in one call.
        Use this when you need instant output (status messages, clearing, etc).
        """
        self.write(data)
        self.flush()

    # -------------------------------------------------------------------------
    # Cursor
    # -------------------------------------------------------------------------

    def hide_cursor(self) -> None:
        """
        Hide the cursor on the terminal.

        Makes the blinking cursor invisible. Used during TUI rendering
        to prevent visual distraction.
        Sends ANSI code: \x1b[?25l (disable cursor visibility)
        """
        self.write("\x1b[?25l")

    def show_cursor(self) -> None:
        """
        Show the cursor on the terminal.

        Makes the cursor visible again. Called when TUI exits.
        Sends ANSI code: \x1b[?25h (enable cursor visibility)
        """
        self.write("\x1b[?25h")

    def move_cursor(self, row: int, col: int = 0) -> str:
        """
        Return ANSI sequence to move cursor to row (0-indexed), col (0-indexed).

        Args:
            row: Row number (0 = top, increases downward)
            col: Column number (0 = left, increases rightward)

        Returns:
            ANSI escape sequence to move cursor to specified position.
            Example: move_cursor(5, 10) returns \x1b[6;11H (converts to 1-indexed)
        """
        return f"\x1b[{row + 1};{col + 1}H"

    def move_up(self, n: int) -> str:
        """
        Return ANSI sequence to move cursor up n lines.

        Args:
            n: Number of lines to move up

        Returns:
            ANSI escape sequence, or empty string if n <= 0
            Example: move_up(3) returns \x1b[3A (move up 3 lines)
        """
        return f"\x1b[{n}A" if n > 0 else ""

    def move_down(self, n: int) -> str:
        """
        Return ANSI sequence to move cursor down n lines.

        Args:
            n: Number of lines to move down

        Returns:
            ANSI escape sequence, or empty string if n <= 0
            Example: move_down(2) returns \x1b[2B (move down 2 lines)
        """
        return f"\x1b[{n}B" if n > 0 else ""

    # -------------------------------------------------------------------------
    # Screen
    # -------------------------------------------------------------------------

    def clear_screen(self) -> str:
        """
        Return ANSI sequence to clear the entire screen.

        Wipes all content and moves cursor to top-left corner (home).
        Returns: \x1b[2J (clear screen) + \x1b[H (move to home)
        """
        return "\x1b[2J\x1b[H"

    def clear_line(self) -> str:
        """
        Return ANSI sequence to clear the entire current line.

        Erases all characters on the line where the cursor is.
        Returns: \x1b[2K (clear entire line)
        """
        return "\x1b[2K"

    def clear_to_end_of_line(self) -> str:
        """
        Return ANSI sequence to clear from cursor to end of line.

        Erases all characters from cursor position to the end of the current line.
        Cursor position stays the same.
        Returns: \x1b[K
        """
        return "\x1b[K"

    def clear_to_end_of_screen(self) -> str:
        """
        Return ANSI sequence to clear from cursor to end of screen.

        Erases all characters from cursor position to the bottom-right of screen.
        Everything above the cursor stays intact.
        Returns: \x1b[J
        """
        return "\x1b[J"

    def clear_scrollback(self) -> str:
        """
        Return ANSI sequence to clear the scrollback/history buffer.

        Deletes the terminal's scroll history (things you could scroll up to see).
        Note: Not supported on all terminals.
        Returns: \x1b[3J
        """
        return "\x1b[3J"

    # -------------------------------------------------------------------------
    # Bracketed paste
    # -------------------------------------------------------------------------

    def enable_bracketed_paste(self) -> None:
        """
        Enable bracketed paste mode on the terminal.

        When enabled, pasted text is wrapped with special markers so the app
        can distinguish between pasted text and manually typed characters.
        This allows handling large pastes differently than typing.
        Sends ANSI code: \x1b[?2004h (enable bracketed paste)
        """
        self.write("\x1b[?2004h")

    def disable_bracketed_paste(self) -> None:
        """
        Disable bracketed paste mode on the terminal.

        Turns off the special paste markers. Pasted text is treated like typing.
        Sends ANSI code: \x1b[?2004l (disable bracketed paste)
        """
        self.write("\x1b[?2004l")

    def enable_focus_reporting(self) -> None:
        """
        Enable terminal focus reporting (DECSET 1004).

        When enabled, the terminal emits \x1b[I when the window gains focus
        and \x1b[O when it loses focus. The app uses this to draw a hollow
        text cursor while unfocused and a solid one while focused.
        Sends ANSI code: \x1b[?1004h (enable focus reporting)
        """
        self.write("\x1b[?1004h")

    def disable_focus_reporting(self) -> None:
        """
        Disable terminal focus reporting (DECSET 1004).

        Stops the terminal from emitting focus in/out events.
        Sends ANSI code: \x1b[?1004l (disable focus reporting)
        """
        self.write("\x1b[?1004l")

    # -------------------------------------------------------------------------
    # Auto-wrap (DECAWM)
    # -------------------------------------------------------------------------

    def disable_autowrap(self) -> None:
        """
        Turn off the terminal's auto-wrap (DECAWM).

        The renderer positions the cursor manually with relative moves and
        inserts its own line breaks, truncating every line to the terminal
        width. With auto-wrap left on, a line that fills the last column makes
        the terminal insert a phantom physical row, desynchronising the
        renderer's logical cursor from the real one — which strands content
        (e.g. the streaming spinner) on the wrong row until a full redraw. With
        auto-wrap off the cursor simply stays at the last column, so manual
        positioning stays exact.
        Sends ANSI code: \x1b[?7l (reset DECAWM)
        """
        self.write("\x1b[?7l")

    def enable_autowrap(self) -> None:
        """
        Restore the terminal's auto-wrap (DECAWM) — pairs with disable_autowrap.
        Sends ANSI code: \x1b[?7h (set DECAWM)
        """
        self.write("\x1b[?7h")

    # -------------------------------------------------------------------------
    # Mouse tracking (SGR extended, needed for scroll wheel)
    # -------------------------------------------------------------------------

    def query_background_color(self) -> None:
        """Send an OSC 11 query to the terminal.

        The reply arrives asynchronously on stdin as
        ``ESC ] 11 ; rgb:RRRR/GGGG/BBBB BEL-or-ST``.
        ``InputParser`` converts it to a ``BgColorEvent``; ``TUI._dispatch``
        stores the result in ``tui.background_color``.
        """
        self.write_flush("\x1b]11;?\x1b\\")

    def set_background_color(self, color: str) -> None:
        """Set the terminal's background colour via OSC 11.

        ``color`` is a CSS-style hex string (``"#1e1e2e"``) or ``"rgb(r,g,b)"``.
        Most modern terminals (iTerm2, Kitty, Alacritty, WezTerm, Terminal.app)
        honour this. Unsupported terminals silently ignore it.

        Call ``reset_background_color()`` on exit to restore the original colour.
        """
        if color.startswith("#") and len(color) in (7, 4):
            h = color[1:]
            if len(h) == 3:
                h = h[0] * 2 + h[1] * 2 + h[2] * 2
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            osc_color = f"rgb:{r:02x}/{g:02x}/{b:02x}"
        elif color.startswith("rgb("):
            parts = color[4:-1].split(",")
            r, g, b = (int(p.strip()) for p in parts)
            osc_color = f"rgb:{r:02x}/{g:02x}/{b:02x}"
        else:
            osc_color = color  # pass through if already in OSC format
        self.write_flush(f"\x1b]11;{osc_color}\x1b\\")

    def reset_background_color(self) -> None:
        """Restore the terminal's background colour to its default (OSC 111)."""
        self.write_flush("\x1b]111;\x1b\\")

    def enable_kitty_keyboard(self) -> None:
        """Enable Kitty keyboard protocol (flags 1 + 2).

        Flags pushed (``\\x1b[>3u`` = 1 | 2):
        * 1 — Disambiguate escape codes: unambiguous encoding of modifier
          combinations.
        * 2 — Report event types: emit press / repeat / release events
          (``KeyEvent.repeat`` / ``KeyEvent.released``). Without this flag the
          terminal only reports presses, so key-up is never observed — which
          breaks anything relying on hold-and-release (e.g. the voice extension).

        Non-Kitty terminals silently ignore this sequence.
        """
        self.write("\x1b[>3u")

    def disable_kitty_keyboard(self) -> None:
        """Restore the keyboard protocol to the terminal default."""
        self.write("\x1b[<1u")

    def enable_mouse_tracking(self) -> None:
        """
        Enable mouse tracking on the terminal.

        Allows the TUI to detect mouse clicks, movement, and scroll wheel events.
        Sends two ANSI codes:
        - \x1b[?1000h: Enable basic mouse tracking
        - \x1b[?1006h: Enable SGR extended format (needed for scroll wheel)
        """
        self.write("\x1b[?1000h\x1b[?1006h")

    def disable_mouse_tracking(self) -> None:
        """
        Disable mouse tracking on the terminal.

        Turns off mouse event detection. Mouse input is no longer reported to app.
        Sends two ANSI codes:
        - \x1b[?1006l: Disable SGR extended format
        - \x1b[?1000l: Disable basic mouse tracking
        """
        self.write("\x1b[?1006l\x1b[?1000l")

    # -------------------------------------------------------------------------
    # Synchronized output (flicker prevention)
    # -------------------------------------------------------------------------

    def begin_sync(self) -> str:
        """
        Return ANSI sequence to begin synchronized output.

        Tells the terminal to buffer all output and NOT display it yet.
        Prevents visual flicker when redrawing the entire screen.
        Everything written after this is held until end_sync() is called.
        Returns: \x1b[?2026h (enable synchronized update mode)
        """
        return "\x1b[?2026h"

    def end_sync(self) -> str:
        """
        Return ANSI sequence to end synchronized output.

        Tells the terminal to flush and display all buffered output atomically.
        Everything between begin_sync() and end_sync() appears on screen at once.
        This prevents seeing partial/incomplete renders.
        Returns: \x1b[?2026l (disable synchronized update mode)
        """
        return "\x1b[?2026l"

    # -------------------------------------------------------------------------
    # Terminal title
    # -------------------------------------------------------------------------

    def set_title(self, title: str) -> None:
        """
        Set the terminal window title.

        Changes what appears in the terminal window's title bar.
        Format: ESC ] 0 ; title BELL
        - \x1b]0; : Start of title sequence
        - title : Your custom title text
        - \x07 : BELL character (marks end of sequence)

        Args:
            title: The new window title text
        """
        self.write(f"\x1b]0;{title}\x07")

    # -------------------------------------------------------------------------
    # Size
    # -------------------------------------------------------------------------

    def on_resize(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for terminal resize events. Returns an unsubscribe callable."""
        self._resize_callbacks.append(callback)

        import contextlib

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._resize_callbacks.remove(callback)

        return _unsub

    def _on_resize(self, *_: object) -> None:
        """
        Internal handler called when terminal is resized (SIGWINCH signal).

        Updates width and height, then safely calls all registered callbacks.
        Uses event loop to prevent issues with concurrent screen updates.
        """
        self.width, self.height = self._get_size()
        # Defer callbacks to the event loop — calling them inline from a signal
        # handler causes reentrant stdout writes if a render is already in flight.
        try:
            loop = asyncio.get_running_loop()
            for cb in self._resize_callbacks:
                loop.call_soon_threadsafe(cb)
        except RuntimeError:
            for cb in self._resize_callbacks:
                cb()

    @staticmethod
    def _get_size() -> tuple[int, int]:
        """
        Get the current terminal dimensions (width and height).

        Returns:
            Tuple of (width, height) in characters
            Falls back to 80x24 if terminal size can't be determined
        """
        try:
            size = os.get_terminal_size()
            return size.columns, size.lines
        except OSError:
            return 80, 24

    # -------------------------------------------------------------------------
    # Input
    # -------------------------------------------------------------------------

    def read_raw(self, n: int = 64) -> str:
        """
        Read keyboard input from stdin without waiting for Enter.

        In raw mode, characters are sent immediately without buffering.
        This is how the TUI gets instant keyboard input.

        Args:
            n: Maximum number of bytes to read (default 64)

        Returns:
            The input as a string. Invalid UTF-8 is replaced with replacement character.
            Example: User presses 'h' -> returns "h"
                     User presses left arrow -> returns "\x1b[D" (ANSI code)
        """
        return os.read(sys.stdin.fileno(), n).decode("utf-8", errors="replace")

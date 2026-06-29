from __future__ import annotations

import asyncio
import os
import signal
import sys
import termios
import tty
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

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
        if self._original_termios is not None:
            # TCSADRAIN: wait for pending output to finish before restoring
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._original_termios)
            self._original_termios = None
        if self._prev_sigwinch is not None:
            # Restore the previous resize signal handler
            signal.signal(signal.SIGWINCH, self._prev_sigwinch)
            self._prev_sigwinch = None

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

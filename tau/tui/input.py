from __future__ import annotations

import contextlib
from dataclasses import dataclass

# ── Key event ─────────────────────────────────────────────────────────────────

# Modifier name aliases → canonical name. Matching is order- and alias-independent,
# so "ctrl+shift+p", "shift+ctrl+p", and "control+shift+p" are all equivalent.
_MOD_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "opt": "alt",
    "option": "alt",
    "shift": "shift",
    "super": "meta",
    "cmd": "meta",
    "command": "meta",
    "win": "meta",
    "meta": "meta",
}

# Base-key name aliases → canonical name.
_KEY_ALIASES = {
    "esc": "escape",
    "return": "enter",
    "del": "delete",
    "spacebar": "space",
    " ": "space",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "pagedown": "pagedown",
    "pageup": "pageup",
    "page_up": "pageup",
    "page_down": "pagedown",  # parser emits the underscore form
}


def normalize_key_id(key_id: str) -> tuple[frozenset[str], str]:
    """Parse a key identifier like 'ctrl+shift+p' into (modifiers, base_key).

    Order- and alias-independent. Handles '+' as a base key (e.g. 'ctrl++')."""
    mods: set[str] = set()
    base_parts: list[str] = []
    for tok in key_id.split("+"):
        t = tok.strip().lower()
        if t in _MOD_ALIASES:
            mods.add(_MOD_ALIASES[t])
        else:
            base_parts.append(tok)
    base = "+".join(base_parts).lower()
    base = _KEY_ALIASES.get(base, base)
    return frozenset(mods), base


@dataclass
class KeyEvent:
    """A single parsed keyboard event."""

    key: str  # canonical name: "a", "enter", "up", "f1", etc.
    char: str | None = None  # printable character if the key produces one
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False  # super / cmd / win (Kitty keyboard protocol)
    released: bool = False  # True on key-up (Kitty keyboard protocol only)
    repeat: bool = False  # True on auto-repeat (Kitty keyboard protocol only)
    raw: str = ""  # original bytes received from stdin

    def __str__(self) -> str:
        parts = []
        if self.ctrl:
            parts.append("ctrl")
        if self.alt:
            parts.append("alt")
        if self.shift:
            parts.append("shift")
        if self.meta:
            parts.append("meta")
        parts.append(self.key)
        return "+".join(parts)

    def _signature(self) -> tuple[frozenset[str], str]:
        mods = {
            name
            for name, on in (
                ("ctrl", self.ctrl),
                ("alt", self.alt),
                ("shift", self.shift),
                ("meta", self.meta),
            )
            if on
        }
        base = _KEY_ALIASES.get(self.key.lower(), self.key.lower())
        return frozenset(mods), base

    def matches(self, *keys: str) -> bool:
        """True if this event matches any of the given key combos.

        Matching is exact on modifiers (so 'escape' does NOT match 'alt+escape')
        but modifier-order- and alias-independent: 'ctrl+shift+p', 'shift+ctrl+p'
        and 'control+shift+p' all match the same event.
        """
        sig = self._signature()
        return any(normalize_key_id(k) == sig for k in keys)


def matches_key(event: KeyEvent, *keys: str) -> bool:
    """Module-level convenience mirroring :meth:`KeyEvent.matches`."""
    return isinstance(event, KeyEvent) and event.matches(*keys)


class Key:
    """Ergonomic, typo-resistant key identifiers for use with ``matches`` / ``matches_key``.

    Use the constants for special/symbol keys and the modifier builders for
    combinations::

        event.matches(Key.ESCAPE)
        event.matches(Key.ctrl("c"))
        event.matches(Key.ctrl_shift("p"))   # order-independent with shift_ctrl

    Identifiers are plain strings, so they interoperate with literal combos.
    """

    # Special keys
    ESCAPE = "escape"
    ENTER = "enter"
    TAB = "tab"
    SPACE = "space"
    BACKSPACE = "backspace"
    DELETE = "delete"
    INSERT = "insert"
    HOME = "home"
    END = "end"
    PAGE_UP = "pageup"
    PAGE_DOWN = "pagedown"
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    F1 = "f1"
    F2 = "f2"
    F3 = "f3"
    F4 = "f4"
    F5 = "f5"
    F6 = "f6"
    F7 = "f7"
    F8 = "f8"
    F9 = "f9"
    F10 = "f10"
    F11 = "f11"
    F12 = "f12"

    @staticmethod
    def ctrl(key: str) -> str:
        return f"ctrl+{key}"

    @staticmethod
    def alt(key: str) -> str:
        return f"alt+{key}"

    @staticmethod
    def shift(key: str) -> str:
        return f"shift+{key}"

    @staticmethod
    def meta(key: str) -> str:
        return f"meta+{key}"

    @staticmethod
    def ctrl_shift(key: str) -> str:
        return f"ctrl+shift+{key}"

    @staticmethod
    def ctrl_alt(key: str) -> str:
        return f"ctrl+alt+{key}"

    @staticmethod
    def alt_shift(key: str) -> str:
        return f"alt+shift+{key}"

    @staticmethod
    def ctrl_shift_alt(key: str) -> str:
        return f"ctrl+shift+alt+{key}"


@dataclass
class PasteEvent:
    """Bracketed paste — text pasted into the terminal."""

    text: str
    raw: str = ""


@dataclass
class MouseEvent:
    """Mouse button press or release."""

    x: int
    y: int
    button: int
    pressed: bool
    raw: str = ""


@dataclass
class BgColorEvent:
    """Terminal background color response (OSC 11).

    Emitted when the terminal replies to an ``\\x1b]11;?\\x1b\\\\`` query.
    Each channel is normalised to 0–255.
    """

    r: int
    g: int
    b: int

    @property
    def is_dark(self) -> bool:
        """True when luminance < 0.5 (dark background)."""
        return (0.2126 * self.r + 0.7152 * self.g + 0.0722 * self.b) < 128


@dataclass
class FocusEvent:
    """Terminal window focus change (DECSET 1004).

    Emitted when the terminal reports ``\\x1b[I`` (focus gained) or
    ``\\x1b[O`` (focus lost).
    """

    focused: bool
    raw: str = ""


InputEvent = KeyEvent | PasteEvent | MouseEvent | BgColorEvent | FocusEvent


# ── CSI / SS3 lookup tables ───────────────────────────────────────────────────

# CSI final-byte → key name (no parameters)
_CSI_SIMPLE: dict[str, str] = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
    "Z": "tab",  # shift+tab (also sets shift below)
    "P": "f1",
    "Q": "f2",
    "R": "f3",
    "S": "f4",
}

# CSI tilde number → key name
_CSI_TILDE: dict[int, str] = {
    1: "home",
    2: "insert",
    3: "delete",
    4: "end",
    5: "page_up",
    6: "page_down",
    7: "home",
    8: "end",
    11: "f1",
    12: "f2",
    13: "f3",
    14: "f4",
    15: "f5",
    17: "f6",
    18: "f7",
    19: "f8",
    20: "f9",
    21: "f10",
    23: "f11",
    24: "f12",
}

# SS3 (ESC O) final-byte → key name
_SS3: dict[str, str] = {
    "P": "f1",
    "Q": "f2",
    "R": "f3",
    "S": "f4",
    "H": "home",
    "F": "end",
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "M": "enter",
}

# Kitty keypad / functional keys (Unicode private-use codes) → standard equivalents.
# int → an ASCII codepoint that continues normal char dispatch; str → a key name.
_KITTY_KP_EQUIV: dict[int, int | str] = {
    57399: 48,
    57400: 49,
    57401: 50,
    57402: 51,
    57403: 52,  # KP 0-4
    57404: 53,
    57405: 54,
    57406: 55,
    57407: 56,
    57408: 57,  # KP 5-9
    57409: 46,
    57410: 47,
    57411: 42,
    57412: 45,
    57413: 43,  # . / * - +
    57414: 13,
    57415: 61,
    57416: 44,  # KP Enter, =, ,
    57417: "left",
    57418: "right",
    57419: "up",
    57420: "down",
    57421: "page_up",
    57422: "page_down",
    57423: "home",
    57424: "end",
    57425: "insert",
    57426: "delete",
}


# Kitty / CSI modifier byte → (shift, alt, ctrl, meta)
# The modifier is encoded as: (value - 1) with bit flags
# bit 0 = shift, bit 1 = alt, bit 2 = ctrl, bit 3 = super (→ meta)
def _decode_modifier(mod: int) -> tuple[bool, bool, bool, bool]:
    m = mod - 1
    return bool(m & 1), bool(m & 2), bool(m & 4), bool(m & 8)


def _decode_mod_field(field: str) -> tuple[bool, bool, bool, bool, bool, bool]:
    """Decode a CSI modifier field, which may carry a Kitty ``:event_type``.

    Terminals that enable the Kitty "report event types" flag (Tau requests it
    via ``\\x1b[>3u``) append the event type to the modifier with a colon, e.g.
    ``1:1`` (press), ``1:2`` (repeat), ``1:3`` (release). Plain terminals send
    just the modifier (``1``). Ghostty uses the colon form even for unmodified
    arrows, so this must be parsed or the keypress is lost.

    Returns ``(shift, alt, ctrl, meta, released, repeat)``. Raises ``ValueError``
    when the modifier component is not an integer.
    """
    sub = field.split(":")
    shift, alt, ctrl, meta = _decode_modifier(int(sub[0]))
    event = 1
    if len(sub) >= 2 and sub[1]:
        with contextlib.suppress(ValueError):
            event = int(sub[1])
    return shift, alt, ctrl, meta, event == 3, event == 2


# Control characters → key names
_CTRL_CHARS: dict[str, tuple[str, bool]] = {
    "\x00": ("space", True),  # ctrl+space / ctrl+@
    "\x01": ("a", True),
    "\x02": ("b", True),
    "\x03": ("c", True),
    "\x04": ("d", True),
    "\x05": ("e", True),
    "\x06": ("f", True),
    "\x07": ("g", True),
    "\x08": ("backspace", False),  # ctrl+h / backspace on some terminals
    "\x09": ("tab", False),
    "\x0a": ("enter", False),
    "\x0b": ("k", True),
    "\x0c": ("l", True),
    "\x0d": ("enter", False),
    "\x0e": ("n", True),
    "\x0f": ("o", True),
    "\x10": ("p", True),
    "\x11": ("q", True),
    "\x12": ("r", True),
    "\x13": ("s", True),
    "\x14": ("t", True),
    "\x15": ("u", True),
    "\x16": ("v", True),
    "\x17": ("w", True),
    "\x18": ("x", True),
    "\x19": ("y", True),
    "\x1a": ("z", True),
    "\x1c": ("\\", True),
    "\x1d": ("]", True),
    "\x1e": ("6", True),
    "\x1f": ("-", True),
    "\x7f": ("backspace", False),
}


# ── Sequence completeness ─────────────────────────────────────────────────────


def _split_text_unit(buf: str) -> tuple[str, str]:
    """Peel one logical character unit off the front of a plain-text buffer.

    ASCII code points are returned one at a time so a burst of auto-repeated
    keys (a held Space, fast typing, or an unbracketed paste) splits into one
    event per character. A run of non-ASCII code points is kept together so
    multi-codepoint graphemes (ZWJ emoji sequences, combining marks) survive as
    a single event.

    Returns ``(unit, rest)``.
    """
    if ord(buf[0]) < 0x80:
        return buf[0], buf[1:]
    i = 1
    while i < len(buf) and ord(buf[i]) >= 0x80:
        i += 1
    return buf[:i], buf[i:]


def _complete_prefix_len(buf: str) -> int | None:
    """Return the length of the first complete escape sequence, or None."""
    if not buf.startswith("\x1b"):
        return 1

    if len(buf) == 1:
        return None  # bare ESC, could be start of sequence

    second = buf[1]

    # Some terminals encode Alt+navigation as an ESC prefix followed by the
    # ordinary escape sequence (for example Alt+Up is ESC ESC [ A).
    if second == "\x1b":
        inner_len = _complete_prefix_len(buf[1:])
        return inner_len + 1 if inner_len is not None else None

    # SS3: ESC O <char>
    if second == "O":
        return 3 if len(buf) >= 3 else None

    # CSI: ESC [ <params> <final>
    if second == "[":
        if len(buf) < 3:
            return None
        # Bracketed paste: ESC [ 2 0 0 ~ ... ESC [ 2 0 1 ~
        # Must be checked first because ~ is a valid CSI final byte and the
        # generic scan below would fire on \x1b[200~ before the content arrives.
        if buf.startswith("\x1b[200~"):
            end = buf.find("\x1b[201~")
            return end + 6 if end != -1 else None
        # Basic mouse: ESC [ M <button> <col> <row> — exactly 6 bytes.
        # Must be checked before the generic final-byte scan because 'M' is a
        # valid CSI final byte and would otherwise terminate prematurely,
        # leaving the 3 parameter bytes to be parsed as text.
        if buf[2] == "M":
            return 6 if len(buf) >= 6 else None
        # Final byte is 0x40–0x7E (@–~)
        for i in range(2, len(buf)):
            code = ord(buf[i])
            if 0x40 <= code <= 0x7E:
                return i + 1
        return None  # still accumulating params

    # APC: ESC _ ... ST  (zero-width cursor markers etc.)
    if second == "_":
        st = buf.find("\x1b\\", 2)
        bel = buf.find("\x07", 2)
        ends = [idx + (2 if idx == st else 1) for idx in (st, bel) if idx != -1]
        return min(ends) if ends else None

    # OSC: ESC ] ... BEL or ST
    if second == "]":
        st = buf.find("\x1b\\", 2)
        bel = buf.find("\x07", 2)
        ends = [idx + (2 if idx == st else 1) for idx in (st, bel) if idx != -1]
        return min(ends) if ends else None

    # DCS / PM / SOS / APC variants: ESC P/X/^ ... ST
    if second in ("P", "X", "^"):
        st = buf.find("\x1b\\", 2)
        return st + 2 if st != -1 else None

    # Other 2-char sequences (ESC <char>): complete
    return 2


def _is_complete(buf: str) -> bool | None:
    """
    Return True if buf starts with a complete escape sequence,
    False if it will never be one, None if it's incomplete (needs more data).
    """
    if not buf.startswith("\x1b"):
        return True  # not an escape — single char, always complete
    return True if _complete_prefix_len(buf) is not None else None


# ── Parser ────────────────────────────────────────────────────────────────────


class InputParser:
    """
    Stateful parser that converts raw stdin bytes into InputEvent objects.

    Usage:
        parser = InputParser()
        for event in parser.feed(raw_bytes):
            handle(event)
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, data: str) -> list[InputEvent]:
        self._buf += data
        events: list[InputEvent] = []
        while self._buf:
            # Plain text (not an escape sequence): peel off one character unit at
            # a time. A single read can batch several repeated bytes together —
            # e.g. holding Space floods the OS auto-repeat into one "   " chunk.
            # Splitting per character means each keypress becomes its own event,
            # so key matching (matches("space"), etc.) works and held keys don't
            # collapse into a single bogus multi-char event.
            if not self._buf.startswith("\x1b"):
                unit, self._buf = _split_text_unit(self._buf)
                event = self._parse_one(unit)
                if event is not None:
                    events.append(event)
                continue
            prefix_len = _complete_prefix_len(self._buf)
            if prefix_len is None:
                break  # need more data
            unit, self._buf = self._buf[:prefix_len], self._buf[prefix_len:]
            event = self._parse_one(unit)
            if event is not None:
                events.append(event)
        return events

    def flush(self) -> list[InputEvent]:
        """
        Force-parse whatever remains in the buffer.
        Called after a short read timeout to emit a bare ESC rather than
        waiting indefinitely for a sequence that never arrives.
        """
        if not self._buf:
            return []
        event = self._parse_one(self._buf)
        self._buf = ""
        return [event] if event is not None else []

    def _parse_one(self, raw: str) -> InputEvent | None:
        # ── Bracketed paste ───────────────────────────────────────────────────
        if raw.startswith("\x1b[200~"):
            # strip opening and closing markers
            text = raw[6:]
            if text.endswith("\x1b[201~"):
                text = text[:-6]
            return PasteEvent(text=text, raw=raw)

        # ── Control characters ────────────────────────────────────────────────
        if len(raw) == 1 and raw in _CTRL_CHARS:
            name, is_ctrl = _CTRL_CHARS[raw]
            return KeyEvent(key=name, char=None, ctrl=is_ctrl, raw=raw)

        # ── Printable ASCII / Unicode ─────────────────────────────────────────
        if len(raw) == 1 and raw >= " " and raw != "\x7f":
            return KeyEvent(key=raw.lower(), char=raw, shift=raw.isupper(), raw=raw)

        if not raw.startswith("\x1b"):
            # Multi-byte UTF-8 character (emoji, CJK, etc.)
            return KeyEvent(key=raw, char=raw, raw=raw)

        # ── Bare ESC ──────────────────────────────────────────────────────────
        if raw == "\x1b":
            return KeyEvent(key="escape", char=None, raw=raw)

        second = raw[1] if len(raw) > 1 else ""

        # ── Alt + escape sequence: ESC ESC [...] ──────────────────────────────
        if raw.startswith("\x1b\x1b"):
            inner = self._parse_one(raw[1:])
            if isinstance(inner, KeyEvent):
                inner.alt = True
                inner.raw = raw
                return inner
            return None

        # ── Alt + char: ESC <char> ────────────────────────────────────────────
        if len(raw) == 2 and second not in ("[", "O", "_", "]", "P", "X", "^"):
            inner = self._parse_one(second)
            if inner is not None and isinstance(inner, KeyEvent):
                inner.alt = True
                inner.raw = raw
                return inner
            return KeyEvent(key=second, char=second, alt=True, raw=raw)

        # ── SS3: ESC O <char> ─────────────────────────────────────────────────
        if second == "O" and len(raw) == 3:
            final = raw[2]
            name = _SS3.get(final)
            if name:
                return KeyEvent(key=name, char=None, raw=raw)

        # ── CSI: ESC [ ... ───────────────────────────────────────────────────
        if second == "[":
            return self._parse_csi(raw)

        # ── OSC 11 — terminal background-colour response ─────────────────────
        # Reply format: ESC ] 11 ; rgb:RRRR/GGGG/BBBB BEL-or-ST
        if second == "]":
            body = raw[2:]
            if body.endswith("\x07"):
                body = body[:-1]
            elif body.endswith("\x1b\\"):
                body = body[:-2]
            if body.startswith("11;rgb:"):
                try:
                    parts = body[7:].split("/")
                    r = int(parts[0], 16) >> 8  # 16-bit → 8-bit
                    g = int(parts[1], 16) >> 8
                    b = int(parts[2], 16) >> 8
                    return BgColorEvent(r=r, g=g, b=b)
                except (ValueError, IndexError):
                    pass

        # ── APC / other non-key sequences (ignore) ───────────────────────────
        return None

    def _parse_csi(self, raw: str) -> InputEvent | None:
        # raw = ESC [ <params> <final>
        payload = raw[2:]  # strip ESC [
        if not payload:
            return None

        final = payload[-1]
        params_str = payload[:-1]

        # ── Mouse: SGR — ESC [ < button ; col ; row M/m ─────────────────────
        if params_str.startswith("<"):
            return self._parse_mouse(raw, params_str[1:], final)

        # ── Mouse: basic — ESC [ M <button+32> <col+32> <row+32> ─────────────
        # Terminals that don't support SGR (mode 1006) fall back to this 6-byte
        # form. We must parse it here; if left unhandled, the 3 raw bytes leak
        # into the event queue as printable characters and corrupt the editor.
        if final == "M" and not params_str and len(raw) == 6:
            b = ord(raw[3]) - 32
            x = max(1, ord(raw[4]) - 32)
            y = max(1, ord(raw[5]) - 32)
            button = b & 0x03
            if b & 0x40:
                button += 64  # scroll wheel
            return MouseEvent(x=x, y=y, button=button, pressed=True, raw=raw)

        # ── Kitty protocol: ESC [ <cp> ; <mods> u ────────────────────────────
        if final == "u":
            return self._parse_kitty(raw, params_str)

        # ── Shift+Tab: ESC [ Z ────────────────────────────────────────────────
        if final == "Z" and not params_str:
            return KeyEvent(key="tab", char=None, shift=True, raw=raw)

        # ── Focus in/out: ESC [ I / ESC [ O (DECSET 1004) ────────────────────
        if final in ("I", "O") and not params_str:
            return FocusEvent(focused=final == "I", raw=raw)

        # ── Simple CSI: ESC [ <letter> (no params) ───────────────────────────
        if not params_str:
            name = _CSI_SIMPLE.get(final)
            if name:
                return KeyEvent(key=name, char=None, raw=raw)
            return None

        # ── CSI with params ───────────────────────────────────────────────────
        parts = params_str.split(";")

        # ESC [ 1 ; <mod>[:<event>] <letter>  — modified arrow/navigation.
        # The modifier field may carry a Kitty event-type sub-parameter; Ghostty
        # sends it even for unmodified arrows (e.g. ESC [ 1 ; 1 : 1 A).
        if len(parts) == 2 and parts[0] == "1":
            try:
                shift, alt, ctrl, meta, released, repeat = _decode_mod_field(parts[1])
            except ValueError:
                return None
            name = _CSI_SIMPLE.get(final)
            if name:
                return KeyEvent(
                    key=name,
                    char=None,
                    shift=shift,
                    alt=alt,
                    ctrl=ctrl,
                    meta=meta,
                    released=released,
                    repeat=repeat,
                    raw=raw,
                )

        # ESC [ <n> ~ — tilde sequences
        if final == "~" and len(parts) == 1:
            try:
                n = int(parts[0])
            except ValueError:
                return None
            name = _CSI_TILDE.get(n)
            if name:
                return KeyEvent(key=name, char=None, raw=raw)

        # ESC [ <n> ; <mod>[:<event>] ~ — modified tilde sequences (event-type aware)
        if final == "~" and len(parts) == 2:
            try:
                n = int(parts[0])
                shift, alt, ctrl, meta, released, repeat = _decode_mod_field(parts[1])
            except ValueError:
                return None
            name = _CSI_TILDE.get(n)
            if name:
                return KeyEvent(
                    key=name,
                    char=None,
                    shift=shift,
                    alt=alt,
                    ctrl=ctrl,
                    meta=meta,
                    released=released,
                    repeat=repeat,
                    raw=raw,
                )

        return None

    def _parse_kitty(self, raw: str, params_str: str) -> KeyEvent | None:
        # ESC [ <codepoint> u
        # ESC [ <codepoint> ; <mods> u
        # ESC [ <codepoint> ; <mods> ; <event_type> u  (Kitty keyboard protocol)
        #   event_type: 1=press  2=repeat  3=release
        # Kitty field layout: `key[:shifted:base] ; modifiers[:event] ; text`.
        parts = params_str.split(";")
        try:
            # The key field may carry alternate keys after colons; use the first.
            codepoint = int(parts[0].split(":")[0])
        except (ValueError, IndexError):
            return None

        shift = alt = ctrl = meta = False
        released = repeat = False
        if len(parts) >= 2 and parts[1]:
            # The event type is appended to the modifier field after a colon.
            mod_field = parts[1].split(":")
            with contextlib.suppress(ValueError, IndexError):
                shift, alt, ctrl, meta = _decode_modifier(int(mod_field[0]))
            event_str = (
                mod_field[1] if len(mod_field) >= 2 else (parts[2] if len(parts) >= 3 else "")
            )
            if event_str:
                try:
                    et = int(event_str)  # 1 = press, 2 = repeat, 3 = release
                    released = et == 3
                    repeat = et == 2
                except ValueError:
                    pass

        # Normalize keypad / functional keys to their standard equivalents.
        kp = _KITTY_KP_EQUIV.get(codepoint)
        if isinstance(kp, str):
            return KeyEvent(
                key=kp,
                char=None,
                shift=shift,
                alt=alt,
                ctrl=ctrl,
                meta=meta,
                released=released,
                repeat=repeat,
                raw=raw,
            )
        if isinstance(kp, int):
            codepoint = kp

        # Map codepoint to key name
        try:
            ch = chr(codepoint)
        except (ValueError, OverflowError):
            return None

        # Special codepoints
        _kitty_special: dict[int, str] = {
            27: "escape",
            13: "enter",
            9: "tab",
            127: "backspace",
            57358: "caps_lock",
            57359: "scroll_lock",
            57360: "num_lock",
            57361: "print_screen",
            57362: "pause",
            57363: "menu",
            57376: "f13",
            57377: "f14",
        }
        # Arrow / navigation codepoints (Kitty uses Unicode private area)
        _kitty_nav: dict[int, str] = {
            57352: "up",
            57353: "down",
            57354: "right",
            57355: "left",
            57356: "end",
            57357: "home",
            57358: "page_up",
            57359: "page_down",
            57399: "kp0",
            57400: "kp1",
            57401: "kp2",
            57402: "kp3",
        }

        if codepoint in _kitty_special:
            key = _kitty_special[codepoint]
            return KeyEvent(
                key=key,
                char=None,
                shift=shift,
                alt=alt,
                ctrl=ctrl,
                meta=meta,
                released=released,
                repeat=repeat,
                raw=raw,
            )

        if codepoint in _kitty_nav:
            key = _kitty_nav[codepoint]
            return KeyEvent(
                key=key,
                char=None,
                shift=shift,
                alt=alt,
                ctrl=ctrl,
                meta=meta,
                released=released,
                repeat=repeat,
                raw=raw,
            )

        # Regular character
        key = ch.lower()
        char = ch if ch.isprintable() else None
        return KeyEvent(
            key=key,
            char=char,
            shift=shift,
            alt=alt,
            ctrl=ctrl,
            meta=meta,
            released=released,
            repeat=repeat,
            raw=raw,
        )

    def _parse_mouse(self, raw: str, params: str, final: str) -> MouseEvent | None:
        # SGR mouse: ESC [ < button ; col ; row M/m
        try:
            parts = params.split(";")
            button, x, y = int(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            return None
        pressed = final == "M"
        return MouseEvent(x=x, y=y, button=button, pressed=pressed, raw=raw)


# ── Keybindings ───────────────────────────────────────────────────────────────

# Named action → list of key combo strings that trigger it
KeyMap = dict[str, list[str]]

_DEFAULTS: KeyMap = {
    # Selection / list navigation
    "tui.select.up": ["up", "ctrl+p"],
    "tui.select.down": ["down", "ctrl+n"],
    "tui.select.page_up": ["page_up"],
    "tui.select.page_down": ["page_down"],
    "tui.select.top": ["home"],
    "tui.select.bottom": ["end"],
    "tui.select.confirm": ["enter", "tab"],
    "tui.select.dismiss": ["escape"],
    # Text input
    "tui.input.submit": ["enter"],
    "tui.input.newline": ["shift+enter"],
    "tui.input.clear": ["ctrl+u"],
    "tui.input.word_back": ["ctrl+w"],
    # Message queuing
    "app.message.followup": ["alt+enter"],  # queue as follow-up (waits for agent to finish)
    "app.message.dequeue": ["ctrl+up"],  # restore queued messages into editor
    "app.details.toggle": ["ctrl+o"],
    "app.invocations.toggle": ["ctrl+e"],
    # App-level
    "tui.app.quit": ["ctrl+c", "ctrl+d"],
    "tui.app.abort": ["escape", "ctrl+c"],
    # Scroll (message list)
    "tui.scroll.up": ["page_up"],
    "tui.scroll.down": ["page_down"],
    "tui.scroll.top": ["home"],
    "tui.scroll.bottom": ["end"],
}


class KeybindingsManager:
    """
    Central registry mapping named actions to key combo strings.
    User overrides are merged on top of defaults at construction time.
    """

    def __init__(self, overrides: KeyMap | None = None) -> None:
        self._map: KeyMap = {k: list(v) for k, v in _DEFAULTS.items()}
        if overrides:
            for action, keys in overrides.items():
                self._map[action] = list(keys)

    def matches(self, event: KeyEvent, action: str) -> bool:
        """Return True if `event` triggers the named action.

        Uses KeyEvent.matches so user-supplied combos are modifier-order- and
        alias-independent ('shift+ctrl+x' == 'ctrl+shift+x' == 'control+shift+x').
        """
        combos = self._map.get(action, [])
        return event.matches(*combos)

    def keys_for(self, action: str) -> list[str]:
        """Return the key combo strings registered for `action`."""
        return list(self._map.get(action, []))

    def bind(self, action: str, keys: list[str]) -> None:
        """Replace all bindings for an action."""
        self._map[action] = list(keys)

    def add_binding(self, action: str, key: str) -> None:
        """Append an extra key combo for an action without removing existing ones."""
        self._map.setdefault(action, [])
        if key not in self._map[action]:
            self._map[action].append(key)

    def effective_map(self) -> KeyMap:
        """Return a copy of the effective action-to-keys mapping."""
        return {action: list(keys) for action, keys in self._map.items()}


_keybindings_instance: KeybindingsManager | None = None


def get_keybindings() -> KeybindingsManager:
    """Return the global KeybindingsManager singleton (created lazily)."""
    global _keybindings_instance
    if _keybindings_instance is None:
        _keybindings_instance = KeybindingsManager()
    return _keybindings_instance


def configure_keybindings(overrides: KeyMap) -> None:
    """Apply user overrides to the global singleton (call once at startup)."""
    global _keybindings_instance
    _keybindings_instance = KeybindingsManager(overrides)

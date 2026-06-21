from __future__ import annotations

from dataclasses import dataclass


# ── Key event ─────────────────────────────────────────────────────────────────

# Modifier name aliases → canonical name. Matching is order- and alias-independent,
# so "ctrl+shift+p", "shift+ctrl+p", and "control+shift+p" are all equivalent.
_MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "opt": "alt", "option": "alt",
    "shift": "shift",
    "super": "meta", "cmd": "meta", "command": "meta", "win": "meta", "meta": "meta",
}

# Base-key name aliases → canonical name.
_KEY_ALIASES = {
    "esc": "escape", "return": "enter", "del": "delete",
    "spacebar": "space", " ": "space",
    "pgup": "pageup", "pgdn": "pagedown", "pagedown": "pagedown", "pageup": "pageup",
}


def _normalize_keyid(key_id: str) -> tuple[frozenset[str], str]:
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
    key: str                 # canonical name: "a", "enter", "up", "f1", etc.
    char: str | None = None  # printable character if the key produces one
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False       # super / cmd / win (Kitty keyboard protocol)
    released: bool = False   # True on key-up (Kitty keyboard protocol only)
    raw: str = ""       # original bytes received from stdin

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
            name for name, on in (
                ("ctrl", self.ctrl), ("alt", self.alt),
                ("shift", self.shift), ("meta", self.meta),
            ) if on
        }
        base = _KEY_ALIASES.get(self.key.lower(), self.key.lower())
        return frozenset(mods), base

    def matches(self, *keys: str) -> bool:
        """True if this event matches any of the given key combos.

        Matching is modifier-order- and alias-independent: 'ctrl+shift+p',
        'shift+ctrl+p' and 'control+shift+p' all match the same event. A bare
        base name (e.g. 'up') matches the key regardless of modifiers, preserving
        the historical loose-match behaviour.
        """
        sig = self._signature()
        for k in keys:
            if _normalize_keyid(k) == sig:
                return True
            # Bare key name (no '+') matches regardless of held modifiers.
            if "+" not in k and _KEY_ALIASES.get(k.lower(), k.lower()) == sig[1]:
                return True
        return False


def matches_key(event: "KeyEvent", *keys: str) -> bool:
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
    ESCAPE = "escape"; ENTER = "enter"; TAB = "tab"; SPACE = "space"
    BACKSPACE = "backspace"; DELETE = "delete"; INSERT = "insert"
    HOME = "home"; END = "end"; PAGE_UP = "pageup"; PAGE_DOWN = "pagedown"
    UP = "up"; DOWN = "down"; LEFT = "left"; RIGHT = "right"
    F1 = "f1"; F2 = "f2"; F3 = "f3"; F4 = "f4"; F5 = "f5"; F6 = "f6"
    F7 = "f7"; F8 = "f8"; F9 = "f9"; F10 = "f10"; F11 = "f11"; F12 = "f12"

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


InputEvent = KeyEvent | PasteEvent | MouseEvent | BgColorEvent


# ── CSI / SS3 lookup tables ───────────────────────────────────────────────────

# CSI final-byte → key name (no parameters)
_CSI_SIMPLE: dict[str, str] = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
    "Z": "tab",     # shift+tab (also sets shift below)
    "P": "f1",
    "Q": "f2",
    "R": "f3",
    "S": "f4",
}

# CSI tilde number → key name
_CSI_TILDE: dict[int, str] = {
    1:  "home",
    2:  "insert",
    3:  "delete",
    4:  "end",
    5:  "page_up",
    6:  "page_down",
    7:  "home",
    8:  "end",
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

# Kitty / CSI modifier byte → (shift, alt, ctrl, meta)
# The modifier is encoded as: (value - 1) with bit flags
# bit 0 = shift, bit 1 = alt, bit 2 = ctrl, bit 3 = super (→ meta)
def _decode_modifier(mod: int) -> tuple[bool, bool, bool, bool]:
    m = mod - 1
    return bool(m & 1), bool(m & 2), bool(m & 4), bool(m & 8)


# Control characters → key names
_CTRL_CHARS: dict[str, tuple[str, bool]] = {
    "\x00": ("space", True),    # ctrl+space / ctrl+@
    "\x01": ("a",     True),
    "\x02": ("b",     True),
    "\x03": ("c",     True),
    "\x04": ("d",     True),
    "\x05": ("e",     True),
    "\x06": ("f",     True),
    "\x07": ("g",     True),
    "\x08": ("backspace", False),  # ctrl+h / backspace on some terminals
    "\x09": ("tab",   False),
    "\x0a": ("enter", False),
    "\x0b": ("k",     True),
    "\x0c": ("l",     True),
    "\x0d": ("enter", False),
    "\x0e": ("n",     True),
    "\x0f": ("o",     True),
    "\x10": ("p",     True),
    "\x11": ("q",     True),
    "\x12": ("r",     True),
    "\x13": ("s",     True),
    "\x14": ("t",     True),
    "\x15": ("u",     True),
    "\x16": ("v",     True),
    "\x17": ("w",     True),
    "\x18": ("x",     True),
    "\x19": ("y",     True),
    "\x1a": ("z",     True),
    "\x1c": ("\\",    True),
    "\x1d": ("]",     True),
    "\x1e": ("6",     True),
    "\x1f": ("-",     True),
    "\x7f": ("backspace", False),
}


# ── Sequence completeness ─────────────────────────────────────────────────────

def _is_complete(buf: str) -> bool | None:
    """
    Return True if buf is a complete escape sequence,
    False if it will never be one, None if it's incomplete (needs more data).
    """
    if not buf.startswith("\x1b"):
        return True  # not an escape — single char, always complete

    if len(buf) == 1:
        return None  # bare ESC, could be start of sequence

    second = buf[1]

    # SS3: ESC O <char>
    if second == "O":
        if len(buf) < 3:
            return None
        return True

    # CSI: ESC [ <params> <final>
    if second == "[":
        if len(buf) < 3:
            return None
        # Basic mouse: ESC [ M <button> <col> <row> — exactly 6 bytes.
        # Must be checked before the generic final-byte scan because 'M' is a
        # valid CSI final byte and would otherwise terminate prematurely,
        # leaving the 3 parameter bytes to be parsed as text.
        if buf[2] == "M":
            return len(buf) >= 6
        # Final byte is 0x40–0x7E (@–~)
        for i in range(2, len(buf)):
            c = buf[i]
            code = ord(c)
            if 0x40 <= code <= 0x7E:
                return True
        return None  # still accumulating params

    # APC: ESC _ ... ST  (zero-width cursor markers etc.)
    if second == "_":
        if buf.endswith("\x1b\\") or buf.endswith("\x07"):
            return True
        return None

    # OSC: ESC ] ... BEL or ST
    if second == "]":
        if buf.endswith("\x07") or buf.endswith("\x1b\\"):
            return True
        return None

    # DCS / PM / SOS / APC variants: ESC P/X/^ ... ST
    if second in ("P", "X", "^"):
        if buf.endswith("\x1b\\"):
            return True
        return None

    # Other 2-char sequences (ESC <char>): complete
    return True


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
            complete = _is_complete(self._buf)
            if complete is None:
                break  # need more data
            event = self._parse_one(self._buf)
            if event is not None:
                events.append(event)
            self._buf = ""
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
                    r = int(parts[0], 16) >> 8   # 16-bit → 8-bit
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
                button += 64   # scroll wheel
            return MouseEvent(x=x, y=y, button=button, pressed=True, raw=raw)

        # ── Kitty protocol: ESC [ <cp> ; <mods> u ────────────────────────────
        if final == "u":
            return self._parse_kitty(raw, params_str)

        # ── Shift+Tab: ESC [ Z ────────────────────────────────────────────────
        if final == "Z" and not params_str:
            return KeyEvent(key="tab", char=None, shift=True, raw=raw)

        # ── Simple CSI: ESC [ <letter> (no params) ───────────────────────────
        if not params_str:
            name = _CSI_SIMPLE.get(final)
            if name:
                return KeyEvent(key=name, char=None, raw=raw)
            return None

        # ── CSI with params ───────────────────────────────────────────────────
        parts = params_str.split(";")

        # ESC [ 1 ; <mod> <letter>  — modified arrow/navigation
        if len(parts) == 2 and parts[0] == "1":
            try:
                mod = int(parts[1])
            except ValueError:
                return None
            shift, alt, ctrl, meta = _decode_modifier(mod)
            name = _CSI_SIMPLE.get(final)
            if name:
                return KeyEvent(key=name, char=None, shift=shift, alt=alt, ctrl=ctrl, meta=meta, raw=raw)

        # ESC [ <n> ~ — tilde sequences
        if final == "~" and len(parts) == 1:
            try:
                n = int(parts[0])
            except ValueError:
                return None
            name = _CSI_TILDE.get(n)
            if name:
                return KeyEvent(key=name, char=None, raw=raw)

        # ESC [ <n> ; <mod> ~ — modified tilde sequences
        if final == "~" and len(parts) == 2:
            try:
                n, mod = int(parts[0]), int(parts[1])
            except ValueError:
                return None
            name = _CSI_TILDE.get(n)
            if name:
                shift, alt, ctrl, meta = _decode_modifier(mod)
                return KeyEvent(key=name, char=None, shift=shift, alt=alt, ctrl=ctrl, meta=meta, raw=raw)

        return None

    def _parse_kitty(self, raw: str, params_str: str) -> KeyEvent | None:
        # ESC [ <codepoint> u
        # ESC [ <codepoint> ; <mods> u
        # ESC [ <codepoint> ; <mods> ; <event_type> u  (Kitty keyboard protocol)
        #   event_type: 1=press  2=repeat  3=release
        parts = params_str.split(";")
        try:
            codepoint = int(parts[0])
        except ValueError:
            return None

        shift = alt = ctrl = meta = False
        if len(parts) >= 2 and parts[1]:
            try:
                shift, alt, ctrl, meta = _decode_modifier(int(parts[1]))
            except ValueError:
                pass

        released = False
        if len(parts) >= 3 and parts[2]:
            try:
                released = int(parts[2]) == 3   # 3 = key-up
            except ValueError:
                pass

        # Map codepoint to key name
        try:
            ch = chr(codepoint)
        except (ValueError, OverflowError):
            return None

        # Special codepoints
        _kitty_special: dict[int, str] = {
            27:   "escape",
            13:   "enter",
            9:    "tab",
            127:  "backspace",
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
            57352: "up",    57353: "down",  57354: "right", 57355: "left",
            57356: "end",   57357: "home",  57358: "page_up", 57359: "page_down",
            57399: "kp0",   57400: "kp1",   57401: "kp2",   57402: "kp3",
        }

        if codepoint in _kitty_special:
            key = _kitty_special[codepoint]
            return KeyEvent(key=key, char=None, shift=shift, alt=alt, ctrl=ctrl, meta=meta, released=released, raw=raw)

        if codepoint in _kitty_nav:
            key = _kitty_nav[codepoint]
            return KeyEvent(key=key, char=None, shift=shift, alt=alt, ctrl=ctrl, meta=meta, released=released, raw=raw)

        # Regular character
        key = ch.lower()
        char = ch if ch.isprintable() else None
        return KeyEvent(key=key, char=char, shift=shift, alt=alt, ctrl=ctrl, meta=meta, released=released, raw=raw)

    def _parse_mouse(self, raw: str, params: str, final: str) -> MouseEvent | None:
        # SGR mouse: ESC [ < button ; col ; row M/m
        try:
            parts = params.split(";")
            button, x, y = int(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            return None
        pressed = final == "M"
        return MouseEvent(x=x, y=y, button=button, pressed=pressed, raw=raw)

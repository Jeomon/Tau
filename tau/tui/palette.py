"""Named color palettes, mirroring ratatui's ``style::palette`` module.

Access as ``tailwind.SLATE.c500`` / ``material.INDIGO.c500`` — same shape as
ratatui's ``palette::tailwind::SLATE.c500``. Values are ``(r, g, b)``
truecolor triples, i.e. already a valid ``style.Color``.

Not exhaustive — Tailwind ships 22 families x 11 shades; this covers the
families most likely to get reached for (grays + one color per hue), in the
same table shape, so more rows are a one-line addition each.
"""

from __future__ import annotations

from dataclasses import dataclass


def _hex(h: str) -> tuple[int, int, int]:
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


@dataclass(frozen=True, slots=True)
class _Shades:
    c50: tuple[int, int, int]
    c100: tuple[int, int, int]
    c200: tuple[int, int, int]
    c300: tuple[int, int, int]
    c400: tuple[int, int, int]
    c500: tuple[int, int, int]
    c600: tuple[int, int, int]
    c700: tuple[int, int, int]
    c800: tuple[int, int, int]
    c900: tuple[int, int, int]
    c950: tuple[int, int, int] | None = None


def _row(hexes: str) -> _Shades:
    """Build a shade row from a space-separated string of hex codes (50 -> 900[-950])."""
    rgb = [_hex(h) for h in hexes.split()]
    if len(rgb) == 10:
        rgb.append(None)  # type: ignore[arg-type]
    if len(rgb) != 11:
        raise ValueError(f"Expected 10 or 11 shades, got {len(rgb)}")
    return _Shades(
        c50=rgb[0],
        c100=rgb[1],
        c200=rgb[2],
        c300=rgb[3],
        c400=rgb[4],
        c500=rgb[5],
        c600=rgb[6],
        c700=rgb[7],
        c800=rgb[8],
        c900=rgb[9],
        c950=rgb[10],
    )


class tailwind:
    """Tailwind CSS default color palette (v3), 50-950 shades."""

    SLATE = _row("f8fafc f1f5f9 e2e8f0 cbd5e1 94a3b8 64748b 475569 334155 1e293b 0f172a 020617")
    GRAY = _row("f9fafb f3f4f6 e5e7eb d1d5db 9ca3af 6b7280 4b5563 374151 1f2937 111827 030712")
    RED = _row("fef2f2 fee2e2 fecaca fca5a5 f87171 ef4444 dc2626 b91c1c 991b1b 7f1d1d 450a0a")
    ORANGE = _row("fff7ed ffedd5 fed7aa fdba74 fb923c f97316 ea580c c2410c 9a3412 7c2d12 431407")
    AMBER = _row("fffbeb fef3c7 fde68a fcd34d fbbf24 f59e0b d97706 b45309 92400e 78350f 451a03")
    YELLOW = _row("fefce8 fef9c3 fef08a fde047 facc15 eab308 ca8a04 a16207 854d0e 713f12 422006")
    GREEN = _row("f0fdf4 dcfce7 bbf7d0 86efac 4ade80 22c55e 16a34a 15803d 166534 14532d 052e16")
    EMERALD = _row("ecfdf5 d1fae5 a7f3d0 6ee7b7 34d399 10b981 059669 047857 065f46 064e3b 022c22")
    TEAL = _row("f0fdfa ccfbf1 99f6e4 5eead4 2dd4bf 14b8a6 0d9488 0f766e 115e59 134e4a 042f2e")
    CYAN = _row("ecfeff cffafe a5f3fc 67e8f9 22d3ee 06b6d4 0891b2 0e7490 155e75 164e63 083344")
    BLUE = _row("eff6ff dbeafe bfdbfe 93c5fd 60a5fa 3b82f6 2563eb 1d4ed8 1e40af 1e3a8a 172554")
    INDIGO = _row("eef2ff e0e7ff c7d2fe a5b4fc 818cf8 6366f1 4f46e5 4338ca 3730a3 312e81 1e1b4b")
    VIOLET = _row("f5f3ff ede9fe ddd6fe c4b5fd a78bfa 8b5cf6 7c3aed 6d28d9 5b21b6 4c1d95 2e1065")
    PURPLE = _row("faf5ff f3e8ff e9d5ff d8b4fe c084fc a855f7 9333ea 7e22ce 6b21a8 581c87 3b0764")
    PINK = _row("fdf2f8 fce7f3 fbcfe8 f9a8d4 f472b6 ec4899 db2777 be185d 9d174d 831843 500724")
    ROSE = _row("fff1f2 ffe4e6 fecdd3 fda4af fb7185 f43f5e e11d48 be123c 9f1239 881337 4c0519")


class material:
    """A representative subset of the Material Design color palette, 50-900 shades."""

    RED = _row("ffebee ffcdd2 ef9a9a e57373 ef5350 f44336 e53935 d32f2f c62828 b71c1c")
    PINK = _row("fce4ec f8bbd0 f48fb1 f06292 ec407a e91e63 d81b60 c2185b ad1457 880e4f")
    PURPLE = _row("f3e5f5 e1bee7 ce93d8 ba68c8 ab47bc 9c27b0 8e24aa 7b1fa2 6a1b9a 4a148c")
    INDIGO = _row("e8eaf6 c5cae9 9fa8da 7986cb 5c6bc0 3f51b5 3949ab 303f9f 283593 1a237e")
    BLUE = _row("e3f2fd bbdefb 90caf9 64b5f6 42a5f5 2196f3 1e88e5 1976d2 1565c0 0d47a1")
    TEAL = _row("e0f2f1 b2dfdb 80cbc4 4db6ac 26a69a 009688 00897b 00796b 00695c 004d40")
    GREEN = _row("e8f5e9 c8e6c9 a5d6a7 81c784 66bb6a 4caf50 43a047 388e3c 2e7d32 1b5e20")
    AMBER = _row("fff8e1 ffecb3 ffe082 ffd54f ffca28 ffc107 ffb300 ffa000 ff8f00 ff6f00")
    ORANGE = _row("fff3e0 ffe0b2 ffcc80 ffb74d ffa726 ff9800 fb8c00 f57c00 ef6c00 e65100")
    GREY = _row("fafafa f5f5f5 eeeeee e0e0e0 bdbdbd 9e9e9e 757575 616161 424242 212121")

"""The canonical colour model, shared by every supported terminal (§5)."""

from __future__ import annotations

import colorsys
import re

PALETTE = [f"palette-{i}" for i in range(16)]
NAMED = ["background", "foreground", "cursor-color", "cursor-text",
         "selection-background", "selection-foreground"]
SLOTS = PALETTE + NAMED

MISSING = "#808080"


# --------------------------------------------------------------------------
# colour maths
# --------------------------------------------------------------------------

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def rgb_to_hsv(rgb):
    return colorsys.rgb_to_hsv(*[c / 255 for c in rgb])


def hsv_to_rgb(h, s, v):
    return tuple(c * 255 for c in colorsys.hsv_to_rgb(h, s, v))


def luminance(value: str) -> float:
    r, g, b = hex_to_rgb(value)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable_fg(value: str) -> str:
    """Black or white, whichever stays legible on `value`."""
    return "#000000" if luminance(value) > 140 else "#ffffff"


def is_hex(value: str) -> bool:
    return bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", value.strip()))


def normalize_hex(value: str) -> str:
    return "#" + value.strip().lstrip("#").lower()

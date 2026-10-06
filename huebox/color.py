"""The canonical colour model, shared by every supported terminal (§5)."""

from __future__ import annotations

import colorsys
import re

PALETTE = [f"palette-{i}" for i in range(16)]
NAMED = ["background", "foreground", "cursor-color", "cursor-text",
         "selection-background", "selection-foreground"]
SLOTS = PALETTE + NAMED

MISSING = "#808080"

#: One nudge of the adjust keys (§4.3): a degree of hue, or two percent of
#: sat/val, times the session's step. Pure and named so the Textual shell
#: (`app.py`, migration phase 2) and `editor._adjust` cannot drift apart on
#: what a key press does to a colour — the shell had its own 0.01 steps, which
#: would have made `w/e` feel different under Textual than under huebox.
HUE_STEP = 1 / 360
CHANNEL_STEP = 0.02


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


def step_hsv(value: str, channel: str, direction: int, mult: float) -> str:
    """`value` nudged one step along `channel` — "h", "s" or "v".

    Hue wraps, so stepping below 0 comes round rather than clamping; sat and val
    clamp, because a negative one is not a colour.
    """
    hue, sat, val = rgb_to_hsv(hex_to_rgb(value))
    if channel == "h":
        hue = (hue + direction * HUE_STEP * mult) % 1.0
    elif channel == "s":
        sat = max(0.0, min(1.0, sat + direction * CHANNEL_STEP * mult))
    else:
        val = max(0.0, min(1.0, val + direction * CHANNEL_STEP * mult))
    return rgb_to_hex(hsv_to_rgb(hue, sat, val))


def readable_fg(value: str) -> str:
    """Black or white, whichever stays legible on `value`."""
    return "#000000" if luminance(value) > 140 else "#ffffff"


def is_hex(value: str) -> bool:
    return bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", value.strip()))


def normalize_hex(value: str) -> str:
    return "#" + value.strip().lstrip("#").lower()

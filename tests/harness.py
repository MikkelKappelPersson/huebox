"""pty environment helpers for the Rust harness (`test_pty_rust.py`).

Retired-Python remainder: the migration closure harness left with the
Python tree, and the only helper the keeper needs is `color_depth_env`,
which pins the child's colour depth.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                      # runnable as a script too
    sys.path.insert(0, _HERE)


def color_depth_env(depth):
    """Environment overrides that pin the terminal to a colour depth.

    `pyte` normalises `38;5;196` and `38;2;255;0;0` to the same hex, so
    a widget degrading to the 256-colour palette is hard to see. Running
    the binary at a pinned depth is what keeps that honest.
    """
    if depth == "truecolor":
        return {"COLORTERM": "truecolor", "TERM": "xterm-256color"}
    if depth == "256":
        return {"TERM": "xterm-256color", "COLORTERM": ""}
    raise ValueError(f"unknown depth: {depth}")

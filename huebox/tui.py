"""Terminal I/O: the live size query, and nothing else (§15).

This was where raw mode, key parsing, the SIGWINCH flag and `MIN_COLS`/`MIN_ROWS`
lived. The editor is the Textual shell now, and Textual owns input, resize and
raw mode — so a second copy here would be a second chance to get them wrong, and
`app.py` would have two sources of truth for each. `read_key` and its escape
parser went with the migration's phase 3; the minimum size went to `render.py`,
because it is layout policy about what a frame can be, and this module is the
terminal-I/O side of that edge.

What is left is the one thing `render.py` cannot do for itself: ask the
terminal how big it is. `render.py` stays pure — it renders from the size it is
given and never from the terminal itself — which is the property that makes the
§14.1 tests simple and the whole frame testable at four sizes without a tty.
"""

from __future__ import annotations

import os


def term_size(default=(80, 24)) -> tuple:
    """The terminal's size, never a non-positive value.

    The fallback matters: `cli show` sizes its preview from this, and a zero
    width would blank the whole frame rather than clip it.
    """
    try:
        size = os.get_terminal_size()
        cols, rows = size.columns, size.lines
    except OSError:
        cols, rows = 0, 0
    if cols <= 0 or rows <= 0:
        cols, rows = default
    return cols, rows

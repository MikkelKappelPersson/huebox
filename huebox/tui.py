"""Terminal I/O: live size, raw mode and keypress reading (§15)."""

from __future__ import annotations

import os
import select
import sys


def term_size(default=(80, 24)) -> tuple[int, int]:
    """Terminal size, never returning a non-positive value.

    The layout truncates to `cols`, so a bogus 0 would blank the whole UI.
    """
    try:
        size = os.get_terminal_size()
        cols, rows = size.columns, size.lines
    except OSError:
        cols, rows = 0, 0
    if cols <= 0 or rows <= 0:
        cols, rows = default
    return cols, rows


def enter_raw():
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    return fd, saved


def exit_raw(fd, saved):
    import termios
    termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def read_key(fd):
    """One keypress.

    Two traps this avoids:

    * os.read on the fd, never sys.stdin.read -- sys.stdin is buffered, so it
      would pull a whole escape sequence into userspace and leave select()
      looking at an empty fd, which reads as a lone Escape.
    * read the sequence one byte at a time and stop at its final byte. A
      greedy drain also swallows the *next* keystroke, so pressing an arrow
      twice quickly would arrive as one unmatched sequence.
    """
    try:
        first = os.read(fd, 1)
    except OSError:
        return "esc"
    if not first:
        return "esc"
    if first != b"\033":
        return first.decode("latin1")

    if not select.select([fd], [], [], 0.05)[0]:
        return "esc"                      # a bare Escape
    lead = os.read(fd, 1)
    if lead == b"O":                     # SS3: OA/OB/OC/OD
        if not select.select([fd], [], [], 0.05)[0]:
            return "esc"
        return {"A": "up", "B": "down", "C": "right",
                "D": "left"}.get(os.read(fd, 1).decode("latin1"), "esc")
    if lead != b"[":
        return "esc"

    seq = b"["
    while select.select([fd], [], [], 0.05)[0]:
        char = os.read(fd, 1)
        if not char:
            break
        seq += char
        if 0x40 <= char[0] <= 0x7E:       # CSI final byte
            break
        if len(seq) > 24:
            break
    return {"[A": "up", "[B": "down", "[C": "right", "[D": "left",
            "[H": "home", "[F": "end"}.get(seq.decode("latin1"), "esc")

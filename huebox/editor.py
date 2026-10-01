"""The interactive editor: draw loop, keys and the staged buffer (§4.3, §14).

The writer is injected by cli so this module never touches the format
registry — saving is the caller's decision, drawing is ours.
"""

from __future__ import annotations

import os
import shutil
import signal
import sys

from .color import (MISSING, NAMED, SLOTS, hex_to_rgb, hsv_to_rgb, is_hex,
                    normalize_hex, readable_fg, rgb_to_hex, rgb_to_hsv)
from .render import (BOLD, DIM, RESET, bg, clip, fg, pack, sample_lines)
from .tui import (MIN_COLS, MIN_ROWS, _on_winch, enter_raw, exit_raw, read_key,
                  term_size)

ADJUST = {
    "q": ("h", -1), "w": ("h", +1),
    "a": ("s", -1), "s": ("s", +1),
    "z": ("v", -1), "x": ("v", +1),
    "h": ("h", -1), "l": ("h", +1),
    "j": ("v", -1), "k": ("v", +1),
    "H": ("s", -1), "L": ("s", +1),
}
MULT_STEPS = [1, 5, 20]


def too_small_frame(cols):
    """The fallback frame (§15.4): one centred hint, clipped to the width.

    The hint is 31 columns — it must stay shorter than MIN_COLS, or the
    actionable size it names is clipped away exactly when it matters.
    """
    hint = f"terminal too small — need {MIN_COLS}x{MIN_ROWS}"
    return " " * max(0, (cols - len(hint)) // 2) + clip(hint, cols)


def draw_editor(fmt, path, slots, sel, undo, status, mult):
    cols, rows = term_size()
    sys.stdout.write("\033[H\033[2J")
    if cols < MIN_COLS or rows < MIN_ROWS:
        # No layout fits: say so instead of drawing a garbled frame.
        sys.stdout.write(too_small_frame(cols) + "\r\n")
        sys.stdout.flush()
        return
    body = []

    head = f"  {BOLD}huebox{RESET}  {BOLD}{fmt}{RESET}"
    if path and len(path) + len(head) + 2 <= cols:
        head += f"  {DIM}{path}{RESET}"
    body.append(head)
    body.append("")

    cell_full, cell_min = 13, 6
    for per_row, cellw in ((8, cell_full), (8, cell_min), (4, cell_full),
                           (4, cell_min), (2, cell_full), (1, cell_full)):
        if len("  ") + per_row * cellw <= cols:
            break
    show_hex = cellw >= cell_full

    def swatch(index, selected):
        value = slots.get(f"palette-{index}", MISSING)
        mark = ">" if selected else " "
        label = (f" {mark}{index:>2} {value} " if show_hex else f" {mark}{index:>2}")
        return (f"{bg(value)}{fg(readable_fg(value))}"
                f"{BOLD if selected else ''}{label.ljust(cellw)}{RESET}")

    body.append(f"  {BOLD}palette{RESET}")
    for start in range(0, 16, per_row):
        body.append(("  " + "".join(
            swatch(i, sel == i) for i in range(start, start + per_row)
            if i < 16)).rstrip())
    body.append(f"  {DIM}0-7 base   8-15 bright{RESET}")
    body.append("")

    per = 2 if len("  ") + 2 * 32 + 2 <= cols else 1
    body.append(f"  {BOLD}interface{RESET}")
    for start in range(0, len(NAMED), per):
        cells = []
        for key in NAMED[start:start + per]:
            index = SLOTS.index(key)
            value = slots.get(key, MISSING)
            mark = ">" if sel == index else " "
            style = BOLD if sel == index else ""
            cells.append(f"{bg(value)}{fg(readable_fg(value))}{style}"
                         f" {mark}{key:<21} {value} {RESET}")
        body.append(("  " + "  ".join(cells)).rstrip())
    body.append("")

    key = SLOTS[sel]
    value = slots.get(key, MISSING)
    h, s, v = rgb_to_hsv(hex_to_rgb(value))
    body.append(f"  {BOLD}selected{RESET}  {key}  {value}   {DIM}"
                f"hue {h * 360:5.1f}  sat {s * 100:4.1f}%  val {v * 100:4.1f}%{RESET}")
    body.append(f"    {fg(value)}AaBbCc 0123 {RESET}")
    body.append("")

    tail = [f"  {DIM}{line}{RESET}" for line in pack(
        ["arrows move", "q/w hue", "a/s sat", "z/x light", f"f x{mult}",
         "i hex", "^S save", f"u undo({len(undo)})", "r revert", "Esc quit"],
        cols - 2)]
    if status:
        tail.append(f"  {BOLD}{status}{RESET}")

    extra = []
    budget = rows - len(body) - len(tail)
    if budget >= 4:
        rendered = sample_lines(slots)
        room = min(len(rendered), budget - 2)
        if room >= 1:
            extra.append(f"  {BOLD}live code{RESET} "
                         f"{DIM}(truecolor, no reload needed){RESET}")
            extra.extend("    " + line.replace(RESET, RESET + "    ")
                         for line, _ in rendered[:room])
            extra.append("")

    out = body + extra + tail
    if len(out) > rows:
        out = out[:rows - len(tail)] + tail
    # CRLF: raw mode disables ONLCR, so a bare \n would not reset the column
    sys.stdout.write("\r\n".join(clip(line, cols) for line in out) + "\r\n")
    sys.stdout.flush()


def edit(fmt, path, slots, write):
    original = dict(slots)
    sel, undo, status = 0, [], ""
    mult = MULT_STEPS[0]

    backup = f"{path}.huebox.bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)

    fd, saved = enter_raw()
    previous_winch = None
    try:
        # §15.1 — resize wakes the loop through a flag, not a redraw callback
        try:
            previous_winch = signal.signal(signal.SIGWINCH, _on_winch)
        except (OSError, ValueError, TypeError):
            pass                      # no winch here; the flag never fires
        while True:
            draw_editor(fmt, path, slots, sel, undo, status, mult)
            key = read_key(fd)
            if key == "resize":
                continue        # no key consumed: the loop just redraws
            name = SLOTS[sel]
            value = slots.get(name)
            if value is None:
                continue
            hue, sat, val = rgb_to_hsv(hex_to_rgb(value))
            status = ""

            if key in ("esc", "Q", "\x03"):
                break
            elif key in ("up", "down", "left", "right"):
                sel = max(0, min(len(SLOTS) - 1, sel + {
                    "up": -8, "down": 8, "left": -1, "right": 1}[key]))
            elif key in ADJUST:
                channel, direction = ADJUST[key]
                if channel == "h":
                    hue = (hue + direction / 360 * mult) % 1.0
                elif channel == "s":
                    sat = max(0.0, min(1.0, sat + direction * 0.02 * mult))
                else:
                    val = max(0.0, min(1.0, val + direction * 0.02 * mult))
                slots[name] = rgb_to_hex(hsv_to_rgb(hue, sat, val))
                undo.append((name, value))
            elif key == "f":
                mult = MULT_STEPS[(MULT_STEPS.index(mult) + 1) % len(MULT_STEPS)]
                status = f"step size x{mult}"
            elif key == "u":
                if undo:
                    slot, previous = undo.pop()
                    slots[slot] = previous
                    status = f"undid {slot}"
            elif key == "r":
                undo.clear()
                slots = dict(original)
                status = "reverted to original"
            elif key in ("i", "X"):
                exit_raw(fd, saved)
                sys.stdout.write("\r\033[2J\033[H")
                try:
                    typed = input(f"  new hex for {name}: ").strip()
                except EOFError:
                    typed = None
                fd, saved = enter_raw()
                if typed is None:
                    continue
                if is_hex(typed):
                    slots[name] = normalize_hex(typed)
                    undo.append((name, value))
                    status = f"{name} = {slots[name]}"
                else:
                    status = "not a valid 6-digit hex - ignored"
            elif key == "\x13":
                status = "saved"

            write(path, slots)
    finally:
        # termios first: nothing may prevent leaving raw mode, and the
        # signal restore must never raise out of the finally (review P1)
        exit_raw(fd, saved)
        if previous_winch is not None:
            try:
                signal.signal(signal.SIGWINCH, previous_winch)
            except (OSError, ValueError, TypeError):
                pass

    print(f"  updated {path}")
    print(f"  backup of the starting state: {backup}")
    print("  reload your terminal to see the change\n")

"""The interactive editor: draw loop, keys and the staged buffer (§4.3, §14).

Keystrokes mutate an in-memory buffer; nothing reaches disk until Ctrl+S
(§14.2). The writer is injected by cli so this module never touches the
format registry — saving is the caller's decision, drawing is ours.
"""

from __future__ import annotations

import os
import shutil
import signal
import sys

from .color import (MISSING, NAMED, SLOTS, hex_to_rgb, hsv_to_rgb, is_hex,
                    normalize_hex, readable_fg, rgb_to_hex, rgb_to_hsv)
from .render import (BOLD, DIM, RESET, bg, clip, example_lines, fg, pack,
                     sample_lines)
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
MOVE = {"up": -8, "down": 8, "left": -1, "right": 1}
QUIT_KEYS = ("esc", "Q", "\x03")
SAVE_KEY = "\x13"


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

    # §14.1 — the examples strip and the code sample share the leftover
    # rows: examples sit above the sample, both shrink as the terminal does.
    extra = []

    def room_left():
        return rows - len(body) - len(tail) - len(extra)

    if room_left() >= 4:          # header + three rows, no trailing blank
        extra.append(f"  {BOLD}examples{RESET} "
                     f"{DIM}(live buffer: background / selection / cursor){RESET}")
        extra.extend(example_lines(slots, cols - 2))
    if room_left() >= 3:          # header + at least one sample line
        rendered = sample_lines(slots)
        shown = min(len(rendered), room_left() - 2)
        if shown >= 1:
            extra.append(f"  {BOLD}live code{RESET} "
                         f"{DIM}(truecolor, no reload needed){RESET}")
            extra.extend("    " + line.replace(RESET, RESET + "    ")
                         for line, _ in rendered[:shown])
            extra.append("")

    out = body + extra + tail
    if len(out) > rows:
        out = out[:rows - len(tail)] + tail
    # CRLF: raw mode disables ONLCR, so a bare \n would not reset the column
    sys.stdout.write("\r\n".join(clip(line, cols) for line in out) + "\r\n")
    sys.stdout.flush()


class EditorState:
    """Everything one editor session mutates (§14.2).

    `slots` is the buffer: it renders every frame and reaches disk only via
    the injected `write`, called from the Ctrl+S branch of `apply_key`.
    `saved` is the last-save snapshot, so `dirty()` means "the buffer
    differs from what is on disk" — that is what arms Esc.
    """

    def __init__(self, slots, write, prompt_hex=None, backup_path=None):
        self.slots = dict(slots)
        self.saved = dict(self.slots)
        self.sel = 0
        self.undo: list = []
        self.status = ""
        self.mult = MULT_STEPS[0]
        self.armed = False                  # second-Esc pending
        self.written = False                # any disk write this session
        self.backup_path = backup_path      # None: huebox owns the file, no .bak
        self.backup_made = False
        self.write = write                  # write(slots) -> status | None
        self.prompt_hex = prompt_hex        # prompt_hex(name) -> str | None
        self.quit = False

    def dirty(self) -> bool:
        return self.slots != self.saved


def ensure_backup(path):
    """`<path>.huebox.bak` — the pre-save snapshot, once per session (§14.2).

    Written on the first save of a session and never refreshed: an existing
    backup stays the "before huebox ever touched this" copy. Returns whether
    it was created.
    """
    if not path:
        return False
    backup = f"{path}.huebox.bak"
    if os.path.exists(backup):
        return False
    try:
        shutil.copy2(path, backup)
    except OSError:
        return False
    return True


def save_state(st):
    """Ctrl+S — the one path from buffer to disk (§14.2).

    A failed write leaves `saved` untouched: the buffer stays dirty and Esc
    keeps guarding it. The backup is best-effort and never blocks a save.
    """
    if st.backup_path and not st.written:
        st.backup_made = ensure_backup(st.backup_path)
    try:
        message = st.write(st.slots)
    except OSError as error:
        st.status = f"write failed: {error}"
        return
    st.saved = dict(st.slots)
    st.written = True
    st.status = message or "saved"


def _adjust(st, key):
    name = SLOTS[st.sel]
    value = st.slots[name]
    hue, sat, val = rgb_to_hsv(hex_to_rgb(value))
    channel, direction = ADJUST[key]
    if channel == "h":
        hue = (hue + direction / 360 * st.mult) % 1.0
    elif channel == "s":
        sat = max(0.0, min(1.0, sat + direction * 0.02 * st.mult))
    else:
        val = max(0.0, min(1.0, val + direction * 0.02 * st.mult))
    st.undo.append((name, value))
    st.slots[name] = rgb_to_hex(hsv_to_rgb(hue, sat, val))


def _prompt(st):
    """`i` / `X` — hex entry, re-injected so `apply_key` stays testable."""
    name = SLOTS[st.sel]
    typed = st.prompt_hex(name) if st.prompt_hex else None
    if typed is None:                        # cancelled: leave the buffer be
        return
    typed = typed.strip()
    if is_hex(typed):
        st.undo.append((name, st.slots[name]))
        st.slots[name] = normalize_hex(typed)
        st.status = f"{name} = {st.slots[name]}"
    else:
        st.status = "not a valid 6-digit hex - ignored"


def apply_key(key, st):
    """One keypress against the buffer (§14.2).

    Pure apart from disk: the injected `write` callback fires on Ctrl+S
    (and the session's first save also snapshots `<path>.huebox.bak`), and
    `prompt_hex` on i/X drops out of raw mode for one line — so the key
    surface itself is testable without a terminal. Sets `st.quit` when the
    session is done: a clean Esc (or Ctrl+C) quits at once, a dirty one
    arms and takes a second press.
    """
    if key in QUIT_KEYS:
        if st.dirty() and not st.armed:
            st.armed = True
            st.status = "unsaved changes - Esc again to discard"
            return
        st.quit = True
        return

    st.armed = False                # any other key disarms a pending discard
    st.status = ""

    name = SLOTS[st.sel]
    value = st.slots.get(name)

    if key in MOVE:
        st.sel = max(0, min(len(SLOTS) - 1, st.sel + MOVE[key]))
    elif key == "f":
        st.mult = MULT_STEPS[(MULT_STEPS.index(st.mult) + 1) % len(MULT_STEPS)]
        st.status = f"step size x{st.mult}"
    elif key == SAVE_KEY:
        save_state(st)
    elif key == "u":
        if st.undo:
            slot, previous = st.undo.pop()
            st.slots[slot] = previous
            st.status = f"undid {slot}"
    elif key == "r":
        st.undo.clear()
        st.slots = dict(st.saved)
        st.status = "reverted to last save" if st.written else "reverted to start"
    elif value is None:
        return                  # slot absent from this config: nothing to do
    elif key in ADJUST:
        _adjust(st, key)
    elif key in ("i", "X"):
        _prompt(st)


def edit(fmt, path, slots, write, backup=True, theme=None):
    """Run one editor session: staged buffer, save on Ctrl+S (§14.2).

    `backup` is False for files huebox owns (theme files, §13.2 — no .bak
    there); the terminal-config session snapshots `<path>.huebox.bak` on its
    first save. `theme` names the theme being edited: same loop, same keys,
    a truth-file writer instead of the terminal one, and closing lines that
    do not claim a terminal reloaded itself — the push is §13.6.
    """
    fd = saved = None
    previous_winch = None

    def prompt_hex(name):
        """Hex entry drops out of raw mode for one line, then comes back."""
        nonlocal fd, saved
        exit_raw(fd, saved)
        sys.stdout.write("\r\033[2J\033[H")
        try:
            return input(f"  new hex for {name}: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None          # Ctrl+C inside a prompt cancels the prompt
        finally:
            fd, saved = enter_raw()

    st = EditorState(slots, lambda values: write(path, values), prompt_hex,
                     path if backup else None)

    fd, saved = enter_raw()
    try:
        # §15.1 — resize wakes the loop through a flag, not a redraw callback
        try:
            previous_winch = signal.signal(signal.SIGWINCH, _on_winch)
        except (OSError, ValueError, TypeError):
            pass                      # no winch here; the flag never fires
        while True:
            draw_editor(fmt, path, st.slots, st.sel, st.undo, st.status,
                        st.mult)
            key = read_key(fd)
            if key == "resize":
                continue        # no key consumed: the loop just redraws
            apply_key(key, st)
            if st.quit:
                break
    finally:
        # termios first: nothing may prevent leaving raw mode, and the
        # signal restore must never raise out of the finally (review P1)
        exit_raw(fd, saved)
        if previous_winch is not None:
            try:
                signal.signal(signal.SIGWINCH, previous_winch)
            except (OSError, ValueError, TypeError):
                pass

    if st.written:
        if theme is not None:
            print(f"  saved theme {theme}  {path}")
            print("  the terminal is unchanged - pushing is not wired up yet\n")
        else:
            print(f"  saved {path}")
            if st.backup_made:
                print(f"  backup of the pre-save state: {st.backup_path}.huebox.bak")
            print("  reload your terminal to see the change\n")
    elif st.dirty():
        print("  nothing saved - the buffer was discarded\n")
    else:
        print("  no changes\n")
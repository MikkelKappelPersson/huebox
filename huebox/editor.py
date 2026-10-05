"""The editing session: buffer, key surface, picker, and what it saves
(§4.3, §13.7, §14).

This is huebox's behaviour, not its terminal. `EditorState` holds the buffer,
`apply_key` is the whole key surface, `draw_editor` is the frame, and the
module's job is to keep them answerable without a terminal attached — which is
what let 400-odd tests drive a session by feeding it a key list, and what lets
the Textual shell (`app.py`) be the editor rather than a second implementation
of it. The migration deleted `edit()`, the raw-mode loop that used to tie all
three to a real tty; nothing about the behaviour went with it.

Keystrokes mutate an in-memory buffer; nothing reaches disk until Ctrl+S
(§14.2). The writer is injected by cli so this module never touches the
format registry — saving is the caller's decision, drawing is ours.

The theme picker (§13.7) follows the same rule one level up: the library
arrives as an injected `Library` (list / load / create) rather than an
import, so a switch can re-target a save mid-session without a cycle.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
from typing import NamedTuple

from .color import (MISSING, NAMED, PALETTE, SLOTS, hex_to_rgb, is_hex,
                    normalize_hex, readable_fg, rgb_to_hsv, step_hsv)
from .render import (BOLD, CHROME_MUTED, MIN_COLS, MIN_ROWS, RESET, backdrop,
                     banner_lines, bg, chrome, clip,
                     diff_lines, example_lines, fg, hint_line, hsv_numbers,
                     hsv_readout, mini_banner_lines, sample_lines, title, visible, wordmark)
from .tui import term_size

ADJUST = {
    "q": ("h", -1), "w": ("h", +1),
    "a": ("s", -1), "s": ("s", +1),
    "z": ("v", -1), "x": ("v", +1),
    "h": ("h", -1), "l": ("h", +1),
    "j": ("v", -1), "k": ("v", +1),
    "H": ("s", -1), "L": ("s", +1),
}
MULT_STEPS = [1, 5, 20]
ARROWS = ("up", "down", "left", "right")
QUIT_KEYS = ("esc", "Q", "\x03")
SAVE_KEY = "\x13"
ENTER_KEYS = ("\r", "\n")

# §13.7 — the picker takes the frame over while it is up (decision 19), so
# it shares the editor's minimum size and header width instead of adding a
# box of its own. `●` is the spec's own dirty mark, not an ASCII stand-in.
DIRTY_MARK = "●"
# the picker footer as `(key, what)` pairs — the frame paints the key and
# its label separately (§8.1), and `pack` never folds between a key and
# the label it belongs to
THEME_HINTS = [("arrows", "move"), ("Enter", "use"), ("n", "new from buffer"),
               ("N", "save as new"), ("t / Esc", "back")]

# §15 — what a short terminal spends, in order. The frame sheds its
# decoration (the palette legend, then the blank separators nearest the
# widgets) before it sheds a widget, and the three live widgets share what
# is left: the strip gives up rows first, then the diff, and the sample's
# tail goes before either, because the sample is the widget the editor
# exists to show (§9).
PALETTE_LEGEND = "  0-7 base   8-15 bright"
EXAMPLES_ROWS = 4                    # the strip whole: header + three rows
EXAMPLES_FLOOR = 2                   # header + one row; below this it goes
DIFF_ROWS = 6                        # the diff whole: header + hunk + two pairs
DIFF_FLOOR = 4                       # header + hunk + one removed/added pair
SAMPLE_FLOOR = 4                     # the code block: header + three lines
# what the frame keeps free for the widgets before it touches a widget:
# the strip whole, the sample at its floor, and a row of air under it.
# The diff is not counted: it is drawn only out of rows the sample did not
# need (§15), so promising its rows here would spend decoration on a widget
# the frame cannot show.
WIDGET_BUDGET = EXAMPLES_ROWS + SAMPLE_FLOOR + 1

# a state starts at the width `term_size()` falls back to; the draw loop
# replaces it with the real geometry on every frame (§4.3)
FALLBACK_COLS = 80
# a palette swatch, with and without its hex value; the frame drops the hex
# before it drops a swatch, and gives up cells before width (§15.2)
CELL_FULL, CELL_MIN = 13, 6
# the width of one interface cell: `mark name hex`, the name padded to 21
NAMED_COL_W = 32
# the side-by-side top block (§8.1, decision 36): the wordmark stands in a
# fixed left column and the theme subject plus the selected readout in the
# right one. `TOP_LEFT_W` is the indent, the six letters and the gap between
# the columns; below `TOP_MIN_COLS` the right column cannot hold even a bare
# readout (the longest slot name plus its hex), so the frame keeps the
# stacked header instead.
TOP_LEFT_W = 12
TOP_MIN_COLS = 60


class Grid(NamedTuple):
    """The two colour grids as a frame of `cols` columns draws them (§15)."""

    palette_cols: int      # palette swatches per row
    named_cols: int        # interface cells per row
    show_hex: bool         # a swatch is wide enough to print its hex value


def grid_geometry(cols: int) -> Grid:
    """The `Grid` a frame `cols` wide draws, and the arrows move in (§4.3).

    One ladder for both (§15.2): the frame renders what this says and the
    keys step through what this says, so a selection can never walk a row
    the user cannot see — which is what a fixed slot stride did once the
    palette dropped to four or two to a row.
    """
    cell_full, cell_min = CELL_FULL, CELL_MIN
    for per_row, cellw in ((8, cell_full), (8, cell_min), (4, cell_full),
                           (4, cell_min), (2, cell_full), (1, cell_full)):
        if len("  ") + per_row * cellw <= cols:
            named = 2 if len("  ") + 2 * NAMED_COL_W + 2 <= cols else 1
            return Grid(per_row, named, cellw >= cell_full)
    return Grid(1, 1, True)         # narrower than one cell: one of each


def _bottom_row(base: int, size: int, cols: int, column: int) -> int:
    """A block's last row, for a column that may be wider than the block."""
    return base + (-(-size // cols) - 1) * cols + min(column, cols - 1)


def move_slot(sel: int, key: str, grid: Grid) -> int:
    """Where an arrow lands, in the grid the frame was drawn in (§4.3).

    Left and right stay in the row; up and down stay in the column. The two
    blocks are stacked, so a vertical key that runs off a block crosses to
    the other in the same column — the palette's bottom row is the row
    directly above the interface's first — while the outer ends of the frame
    (`palette-0`, `selection-foreground`) simply stay put.
    """
    palette = sel < len(PALETTE)
    base = 0 if palette else len(PALETTE)
    size = len(PALETTE) if palette else len(NAMED)
    cols = grid.palette_cols if palette else grid.named_cols
    row, column = divmod(sel - base, cols)

    if key in ("left", "right"):
        column += -1 if key == "left" else 1
        return base + row * cols + column if 0 <= column < cols else sel
    if key == "up":
        if row:
            return base + (row - 1) * cols + column
        if palette:
            return sel                       # palette-0 tops the frame
        return _bottom_row(0, len(PALETTE), grid.palette_cols, column)
    if row + 1 < -(-size // cols):
        return base + (row + 1) * cols + column
    if palette:
        return len(PALETTE) + min(column, grid.named_cols - 1)
    return sel                               # the last named slot is the floor


class Library:
    """The three things the picker may ask of the theme library (§13.7).

    Injected by cli and never imported: this module must not know how a
    theme is stored, and a test can hand it three lambdas. Failure is
    reported by return value, never by an exception into the draw loop —
    `names()` is a (possibly empty) list, `load()` gives `(slots, path)` or
    `None`, `create()` gives `(path, problem)` with `problem` empty on
    success.
    """

    def __init__(self, listing=None, loader=None, creator=None):
        self.listing = listing        # () -> [name, ...]
        self.loader = loader          # (name) -> (slots, path) | None
        self.creator = creator        # (name, slots, force) -> (path, problem)

    def names(self) -> list:
        if not self.listing:
            return []
        try:
            return list(self.listing())
        except OSError:               # a library that vanished mid-session
            return []

    def load(self, name: str):
        if not self.loader:
            return None
        return self.loader(name)

    def create(self, name: str, slots: dict, force: bool = False):
        if not self.creator:
            return "", "no theme library in this session"
        return self.creator(name, slots, force)


def too_small_frame(cols):
    """The fallback frame (§15.4): one centred hint, clipped to the width.

    The hint is 31 columns — it must stay shorter than MIN_COLS, or the
    actionable size it names is clipped away exactly when it matters.
    """
    hint = f"terminal too small — need {MIN_COLS}x{MIN_ROWS}"
    return " " * max(0, (cols - len(hint)) // 2) + clip(hint, cols)


def head_label(st) -> str:
    """The status bar's subject: `<theme> ● <fmt>`, or `direct:<path>` (§13.7).

    A theme session names the theme it is editing plus the push target the
    command line named — with no `-f`/`--to` there is no target to name
    until a save resolves one, and the save's own status line names every
    format it pushed. The `●` appears only while the buffer differs from
    the last save. A legacy direct-mode session names the config instead,
    because that is the file its Ctrl+S writes (§13.4).
    """
    if st.theme is None:
        return f"direct:{st.path}"
    parts = [st.theme]
    if st.dirty():
        parts.append(DIRTY_MARK)
    parts.append(st.fmt)
    return " ".join(part for part in parts if part)


def session_path(st) -> str:
    """The dim path after the subject — theme sessions only.

    `direct:<path>` already carries the config's path; printing it twice
    reads like two files.
    """
    return st.path if st.theme is not None else ""


def _theme_row(name: str, selected: bool, current: bool, slots: dict) -> str:
    """One picker row: `> name`, `*` on the library's current theme.

    Both marks share the two columns in front of the name, so the names
    line up and `>* name` reads as "selected, and the current one". The
    marks are chrome (§8.1): the `>` in the label colour because it is the
    row you are on, the `*` muted because it is a fact about the library.
    """
    mark = chrome(">", "foreground", slots, bold=True) if selected else ""
    flag = chrome("*", CHROME_MUTED, slots) if current else ""
    painted = chrome(name, "foreground", slots, bold=selected)
    return f"  {mark}{flag} {painted}"


def theme_lines(names, index, current, cols, rows, status="", slots=None,
                hits=None):
    """The theme picker as a frame of lines (§13.7).

    Pure, like the editor frame: rows are the library's names, the session's
    subject is marked `*` (the theme this buffer came from — after `n`/Enter
    it is also the library's current, but the mark follows the session, not
    the state file), the selected row `>`, and the hint footer folds
    through `pack` so it can never widen the frame. Every line is `clip`ped
    and the list is trimmed to `rows` with a window that keeps the
    selection visible — a library with more themes than rows scrolls, it
    never wraps. The status line is the last row and is never the row that
    gets cut, exactly as in the editor frame.
    """
    slots = slots or {}
    out = ["  " + wordmark(slots) + "  " + title("themes", slots), ""]
    footer = ["  " + line
              for line in hint_line(slots, THEME_HINTS, cols - 2)]
    if status:
        footer.append(f"  {BOLD}{status}{RESET}")

    if not names:
        out.append("  " + chrome("no themes yet - N makes one from"
                                 " this buffer", CHROME_MUTED, slots))
    else:
        index = max(0, min(index, len(names) - 1))
        # two rows of the budget: the "x-y of n" counter and the blank
        # before the footer — the footer itself is never trimmed
        room = max(1, rows - len(out) - len(footer) - 2)
        start = max(0, min(index - room + 1, len(names) - room))
        for row in range(start, min(len(names), start + room)):
            name = names[row]
            # the clickable cell, announced by the row that draws it — a second
            # description of this window would drift from it the moment either
            # changed, and a click would land on the wrong theme
            if hits is not None:
                hits.append(Hit(len(out), 2, cols - 1, row))
            out.append(_theme_row(name, row == index, name == current, slots))
        if start or len(names) > start + room:
            out.append("  " + chrome(f"{start + 1}-"
                                     f"{min(len(names), start + room)} "
                                     f"of {len(names)}", CHROME_MUTED, slots))
    out.append("")
    out.extend(footer)
    if len(out) > rows:                    # belt and braces: keep the footer
        out = out[:len(out) - len(footer)] + footer
    return [clip(line, cols) for line in out[:rows]]


class Hit(NamedTuple):
    """One clickable cell: the frame row `y`, columns `x0`..`x1`, and the slot.

    Column-inclusive at both ends, because a cell that answered to every column
    but its last would leave a sliver no one can hit.
    """
    y: int
    x0: int
    x1: int
    slot: int


def frame_hits(cols: int, rows: int = 24, fmt="ghostty", path="", slots=None,
               sel=0, undo=(), status="", mult=1, head=None,
               use_banner=None) -> list:
    """Every colour cell a frame `cols` wide draws, as `Hit`s.

    The rows are the frame's own: `draw_editor` announces each cell as it paints
    it (`hits=`), so a click and a swatch cannot disagree about where one is —
    which is the whole risk of having a hit map at all.
    """
    found: list = []
    with contextlib.redirect_stdout(io.StringIO()):
        draw_editor(fmt, path, slots or {}, sel, list(undo), status, mult,
                    head=head, hits=found, size=(cols, rows or 24),
                    use_banner=use_banner)
    return found


def theme_hits(names, index, current, cols, rows, slots=None, status="") -> list:
    """Every picker row, as `Hit`s whose `slot` is the row in the library.

    The window is the point of this one: a row's frame position depends on how
    far down the library it is, so asking this at the wrong moment describes a
    picker that is not the one on screen.
    """
    found: list = []
    theme_lines(names, index, current, cols, rows, status=status,
                slots=slots, hits=found)
    return found


def slot_at(hits, x: int, y: int):
    """The slot a click at (`x`, `y`) lands on, or None for the chrome."""
    for hit in hits:
        if hit.y == y and hit.x0 <= x <= hit.x1:
            return hit.slot
    return None


def _pad_left(text: str) -> str:
    """One left-column cell, padded out to `TOP_LEFT_W` visible columns.

    The pad is plain air: the column holds the wordmark on the first top
    row and nothing on the other two, so the theme subject beside it starts
    in the same column on every row of the block.
    """
    return text + " " * max(0, TOP_LEFT_W - visible(text))


def top_right_rows(label, path, slots, sel, cols):
    """The right column's three rows: theme, selected, specimen (§8.1).

    Pure, like every widget here: the theme subject, the selected slot's
    prefix plus whatever rung of the hsv ladder fits the right column, and
    the specimen with the exact reading where it fits. The caller joins
    each row to its left-column cell; `cols` is the full frame width, so
    the right column is `cols - TOP_LEFT_W` wide.
    """
    right_w = cols - TOP_LEFT_W
    key = SLOTS[sel]
    value = slots.get(key, MISSING)
    right0 = chrome(label, "foreground", slots, bold=True)
    if path:
        tail = "  " + chrome(path, CHROME_MUTED, slots)
        if visible(right0) + visible(tail) <= right_w:
            right0 = right0 + tail
    prefix = (title("selected", slots) + "  "
              + chrome(key, "foreground", slots) + "  "
              + chrome(value, CHROME_MUTED, slots) + "   ")
    right1 = prefix + hsv_readout(slots, value,
                                  right_w - visible(prefix))
    specimen = f"  {fg(value)}AaBbCc 0123 {RESET}"
    exact = hsv_numbers(*rgb_to_hsv(hex_to_rgb(value)))
    suffix = "   " + chrome(exact, CHROME_MUTED, slots)
    right2 = (specimen + suffix
              if visible(specimen) + visible(suffix) <= right_w
              else specimen)
    return right0, right1, right2


def draw_editor(fmt, path, slots, sel, undo, status, mult, head=None,
                grid=None, hits=None, size=None, regions=None,
                use_banner=None):
    """The frame, written to stdout.

    `size` overrides the terminal query. Textual knows the size it was given —
    the pty's — and passing it is both cheaper and more honest than asking the
    terminal a second time, and it is what lets `frame_hits(cols)` describe the
    frame at `cols` rather than at whatever the terminal happens to be. Without
    it the hit map and the frame disagreed at every width but one, which the
    cross-check in `test_editor` caught.
    """
    cols, rows = size or term_size()
    sys.stdout.write("\033[H\033[2J")
    if cols < MIN_COLS or rows < MIN_ROWS:
        # No layout fits: say so instead of drawing a garbled frame.
        sys.stdout.write(too_small_frame(cols) + "\r\n")
        sys.stdout.flush()
        return
    if grid is None:
        grid = grid_geometry(cols)      # §4.3 — what this frame draws, and
                                        # what the arrows step through
    # §13.7 — the picker is no longer a second mode of this function. It was
    # `overlay=` for the whole migration, and phase 5 gave it a widget; the
    # frame it used to be handed on the side is now `theme_lines`, which is
    # what it always was underneath.
    # §8.1 — the hints are `(key, what)` pairs: the key is the bright half,
    # its label the quiet one. Same widths as the plain strings they
    # replaced, so this row count is unchanged at every terminal size.
    # Built before the first body row: the banner insertion below needs
    # the whole frame — body, widgets and `tail` — before it can know
    # whether the rows below leave room.
    tail = ["  " + line for line in hint_line(slots, [
        ("arrows", "move"), ("q/w", "hue"), ("a/s", "sat"), ("z/x", "light"),
        ("f", f"x{mult}"), ("i", "hex"), ("^S", "save"),
        ("u", f"undo({len(undo)})"), ("r", "revert"), ("t", "themes"),
        ("N", "as new"), ("Esc", "quit")],
        # two spaces, not three: the picker added two keys to this line and
        # one more row here would come out of the examples strip's budget
        cols - 2)]
    if status:
        tail.append(f"  {BOLD}{status}{RESET}")
    body = []
    # Checkpoints: `(name, first row of the frame)`, each block running until
    # the next. `at_body` counts rows in `body`; `at_extra` counts rows in the
    # widgets, which are appended after `body` and so are offset by its length.
    # Shifted with the hits when decoration is dropped, resolved to spans once
    # the frame is final.
    marks = []
    at_body = lambda: len(body)                      # noqa: E731
    at_extra = lambda: len(body) + len(extra)        # noqa: E731

    label = fmt if head is None else head
    # §8.1 (decision 36) — the top block stacks side by side where the
    # right column holds a readout: the wordmark in a fixed left column,
    # the theme subject plus the selected readout on its right. Below
    # `TOP_MIN_COLS` the right column cannot hold even a bare readout, so
    # the frame keeps the stacked header and the selected block below the
    # interface grid instead.
    use_side = cols >= TOP_MIN_COLS
    right0 = right1 = right2 = ""
    if use_side:
        right0, right1, right2 = top_right_rows(label, path, slots,
                                                 sel, cols)
        marks.append(("header", at_body()))
        body.append(clip(_pad_left("  " + wordmark(slots)) + right0,
                         cols))
        marks.append(("selected", at_body()))
        body.append(clip(_pad_left("") + right1, cols))
        body.append(clip(_pad_left("") + right2, cols))
        body.append("")
    else:
        first = ("  " + wordmark(slots) + "  "
                 + chrome(label, "foreground", slots, bold=True))
        if path and len("  huebox  ") + len(label) + 2 + len(path) <= cols:
            first += "  " + chrome(path, CHROME_MUTED, slots)
        marks.append(("header", at_body()))
        body.append(first)
        body.append("")

    per_row, show_hex = grid.palette_cols, grid.show_hex
    cellw = CELL_FULL if show_hex else CELL_MIN

    def swatch(index, selected):
        value = slots.get(f"palette-{index}", MISSING)
        mark = ">" if selected else " "
        label = (f" {mark}{index:>2} {value} " if show_hex else f" {mark}{index:>2}")
        return (f"{bg(value)}{fg(readable_fg(value))}"
                f"{BOLD if selected else ''}{label.ljust(cellw)}{RESET}")

    marks.append(("palette", at_body()))
    body.append("  " + title("palette", slots))
    for start in range(0, len(PALETTE), per_row):
        # `len(body)` is this row's index in the frame: `out` is `body` plus
        # whatever follows, so the index holds. Announcing the cell here rather
        # than recomputing it elsewhere is what keeps a click and a swatch from
        # disagreeing about where one is.
        y = len(body)
        cells = [i for i in range(start, start + per_row) if i < len(PALETTE)]
        body.append(("  " + "".join(
            swatch(i, sel == i) for i in cells)).rstrip())
        if hits is not None:
            for column, index in enumerate(cells):
                x0 = 2 + column * cellw
                hits.append(Hit(y, x0, x0 + cellw - 1, index))
    legend = chrome(PALETTE_LEGEND, CHROME_MUTED, slots)
    body.append(legend)
    body.append("")

    marks.append(("interface", at_body()))
    per = grid.named_cols
    body.append("  " + title("interface", slots))
    for start in range(0, len(NAMED), per):
        y = len(body)
        cells = []
        for key in NAMED[start:start + per]:
            index = SLOTS.index(key)
            value = slots.get(key, MISSING)
            mark = ">" if sel == index else " "
            style = BOLD if sel == index else ""
            cells.append(f"{bg(value)}{fg(readable_fg(value))}{style}"
                         f" {mark}{key:<21} {value} {RESET}")
        body.append(("  " + "  ".join(cells)).rstrip())
        if hits is not None:
            for column in range(len(cells)):
                x0 = 2 + column * (NAMED_COL_W + 2)
                hits.append(Hit(y, x0, x0 + NAMED_COL_W - 1,
                                len(PALETTE) + start + column))
    body.append("")

    if not use_side:
        marks.append(("selected", at_body()))
        key = SLOTS[sel]
        value = slots.get(key, MISSING)
        # §8.3 — the reading of the slot's own colour. It is bars where
        # the row has room for them and the numbers they replace where it
        # does not, so `room` is whatever the subject leaves — the row
        # itself never grows and §15's budget does not move
        subject = ("  " + title("selected", slots)
                   + "  " + chrome(key, "foreground", slots)
                   + "  " + chrome(value, CHROME_MUTED, slots) + "   ")
        # §8.3 — the reading of the slot's own colour comes in two parts:
        # the bars, which are the glance, on this row, and the exact
        # numbers — `hue 207.0  sat 59.4%  val 93.7%`, which are the truth
        # and cost a degree and a percent of rounding — on the specimen row
        # below. Neither part costs a row, and neither gives up a row for
        # the other.
        exact = hsv_numbers(*rgb_to_hsv(hex_to_rgb(value)))
        specimen = f"    {fg(value)}AaBbCc 0123 {RESET}"
        body.append(subject + hsv_readout(slots, value,
                                          cols - visible(subject)))
        body.append(specimen + ("   " + chrome(exact, CHROME_MUTED, slots)
                                if len(exact) + 3 <= cols - visible(specimen)
                                else ""))
        body.append("")

    # §14.1 / §15 — the examples strip and the code sample share the
    # leftover rows, each with a floor, and the frame spends its decoration
    # before it spends a widget.
    spare = rows - len(body) - len(tail)
    while spare < WIDGET_BUDGET:
        decoration = ([i for i, line in enumerate(body) if line == legend]
                      or [i for i, line in enumerate(body) if not line])
        if not decoration:
            break                   # nothing left to spend; the widgets go
        # the legend is a courtesy (the grid is numbered), so the blanks
        # are the decoration proper — nearest the widgets first, leaving
        # the air at the top of the frame
        dropped = decoration[-1]
        del body[dropped]
        # A cell recorded above the deleted row keeps its index; one below it
        # moves up by one. Without this a short frame's hit map points a row
        # past where its cells were painted, and clicking selects whatever is
        # actually there — a silent wrong answer, which is what the cross-check
        # in `test_editor` exists to catch.
        if hits is not None:
            for index, hit in enumerate(hits):
                if hit.y > dropped:
                    hits[index] = hit._replace(y=hit.y - 1)
        for index, (_name, row) in enumerate(marks):
            if row > dropped:
                marks[index] = (_name, row - 1)
        spare += 1

    extra = []

    def room_left():
        return spare - len(extra)

    examples = min(EXAMPLES_ROWS, room_left() - SAMPLE_FLOOR)
    if examples >= EXAMPLES_FLOOR:
        marks.append(("examples", at_extra()))
        extra.append("  " + title("examples", slots,
                             "(live buffer: background / selection / cursor)"))
        extra.extend(example_lines(slots, cols - 2)[:examples - 1])
    # §15 — the diff is the last widget to get a row and the first to give
    # one back: it grows out of what the sample did not need, so where the
    # two compete the sample stays whole and the hunk does not appear
    diff = []
    if room_left() >= 2:             # header plus at least one line
        code = [line for line, _ in sample_lines(slots)]
        if code and not code[-1].strip():
            code.pop()               # the lex's trailing newline, not a line
        # a truncated block drops its least informative lines rather than
        # stopping mid-program: the leading comment (the label above already
        # says what the block is), the closing brace, and the blank inside
        # it — in a short frame a row that shows nothing is the most
        # expensive row there is
        code = (code if room_left() - 1 >= len(code)
                else [line for line in code[1:-1] if line.strip()])
        take = min(len(code), max(1, room_left() - 1))   # -1 for the header
        if take:
            spare_rows = room_left() - 1 - take
            # the body is the `@@` line and whole removed/added pairs, so a
            # cut frame loses pairs and never shows half of one
            rows_left = min(DIFF_ROWS - 1, max(0, spare_rows - 1))
            rows_left -= (rows_left - 1) % 2
            # §15 — the blanks that separate the widget blocks are
            # decoration, and decoration is spent out of what the hunk did
            # not ask for: one row of air above it, one below it, and never
            # a hunk row to buy either
            lead = 1 if spare_rows - rows_left - 1 >= 2 else 0
            if rows_left >= DIFF_FLOOR - 1:
                marks.append(("diff", at_extra() + (1 if lead else 0)))
                if lead:
                    extra.append("")
                diff = ["  " + title("live diff", slots,
                           "(git-style: + added, - removed)")]
                diff.extend(diff_lines(slots, cols - 2)[:rows_left])
                if spare_rows - lead - rows_left - 1 >= 1:   # a row to spare
                    diff.append("")   # the separator is a row of its own
            extra.extend(diff)      # the hunk draws above the sample
            marks.append(("sample", at_extra()))
            extra.append("  " + title("live code", slots,
                                  "(truecolor, no reload needed)"))
            extra.extend("    " + line.replace(RESET, RESET + "    ")
                         for line in code[:take])
            if room_left() - take >= 2:  # rows to spare: the separator
                extra.append("")

    # The hints and the status are last, and `extra` is not known until the
    # widgets have had their rows, so these two checkpoints can only be taken
    # here rather than where the rows themselves are built.
    marks.append(("hints", at_extra()))
    if status:
        marks.append(("status", at_extra() + len(tail) - 1))

    # The header draws only out of leftover (decision 33): the frame above
    # is built plain, and where the rows below it leave room a logo goes in
    # above them — the raster banner where it fits, else the mini banner,
    # else the wordmark the frame was built with. Every mark and hit below
    # shifts down by the logo's rows and nothing below changes — not a
    # widget, not the air between them. `use_banner=True` forces the raster
    # banner (an overflowing frame trims like any other); `False` keeps the
    # wordmark; `None` walks the ladder. The mini banner is auto-only:
    # forcing means the raster one, and where even the mini one does not
    # fit the wordmark stands back in.
    spare = rows - len(body) - len(extra) - len(tail)
    art = banner_lines(slots, cols)
    if art and (use_banner or (use_banner is None and spare >= len(art))):
        logo = art
    else:
        mark = mini_banner_lines(slots, cols)
        logo = (mark if mark and use_banner is None and spare >= len(mark)
                else [])
    if logo:
        shift = len(logo)
        if use_side:
            # The banner above is the only huebox on screen now: the top
            # block's wordmark stands back to air, and the right column
            # stays where it was, one banner below.
            palette_row = next(row for name, row in marks
                               if name == "palette")
            rights = [right0, right1, right2]
            # Content rows keep the right column; air stays air: a padded
            # blank is twelve spaces of fill-reopen rather than the floor,
            # and the banner test reads the paint, not the plain text.
            blanked = [clip(_pad_left("") + rights[i], cols)
                         for i in range(min(palette_row, 3))]
            blanked += [""] * max(0, palette_row - 3)
            body[0:palette_row] = logo + blanked
        else:
            subject = "  " + chrome(label, "foreground", slots,
                                       bold=True)
            if path and len("  ") + len(label) + 2 + len(path) <= cols:
                subject += "  " + chrome(path, CHROME_MUTED, slots)
            body[0:2] = logo + [subject, ""]
        marks[:] = [(name, row if name == "header" else row + shift)
                    for name, row in marks]
        if hits is not None:
            for index, hit in enumerate(hits):
                hits[index] = hit._replace(y=hit.y + shift)

    out = body + extra + tail
    if len(out) > rows:
        out = out[:rows - len(tail)] + tail
    if regions is not None:
        # A checkpoint past the last row is a block the frame cut off. It is
        # dropped from the list rather than merely not reported, because it is
        # also every later block's *end*: keeping it would give the block below
        # a height reaching past the frame, which is a widget claiming rows the
        # frame never painted — the unpainted-cell failure §8.2 exists to
        # prevent, one level up. At 40x12 that is the difference between a
        # frame of six widgets and one of nine, four of them off the bottom.
        live = [(name, row) for name, row in marks if row < len(out)]
        edges = [row for _, row in live] + [len(out)]
        regions.extend((name, row, stop - row)
                       for (name, row), stop in zip(live, edges[1:]))
    if hits is not None:
        # The trim happened after the cells announced themselves, so a short
        # frame still claims rows it never painted — and a click there would
        # select something the user cannot see. Off they go.
        del hits[len(out):]
    # CRLF: raw mode disables ONLCR, so a bare \n would not reset the column
    # §8.2 — every row stands on the buffer's own background, so the frame
    # *is* the theme: the floor, the air between widgets and the column after
    # the last hint all read `background` out of the live buffer
    #
    # The trailing CRLF is withheld when the frame fills the screen. Written on
    # the bottom row it scrolls the terminal: the frame loses its top row (at
    # 80x24 the `huebox` wordmark) and the terminal's own line appears below.
    # `out` never exceeds `rows`, so `len(out) == rows` is the only scrolling
    # case; a row short of the bottom makes the newline harmless and it stays,
    # keeping the cursor off the frame's last line.
    sys.stdout.write("\r\n".join(backdrop(line, slots, cols) for line in out)
                     + ("\r\n" if len(out) < rows else ""))
    sys.stdout.flush()


class EditorState:
    """Everything one editor session mutates (§14.2, §13.7).

    `slots` is the buffer: it renders every frame and reaches disk only via
    the injected `write`, called from the Ctrl+S branch of `apply_key`.
    `saved` is the last-save snapshot, so `dirty()` means "the buffer
    differs from what is on disk" — that is what arms Esc, and what blocks
    a theme switch (decision 12).

    `theme` is the name of the theme being edited (`None` in a legacy
    direct-mode session), and
    `library` the injected seam the picker asks for themes. Each of those
    can change mid-session, which is why the writer is bound to the state
    and not to `edit()`'s arguments. `grid` is the frame's shape: the
    editor's draw loop replaces it with the live geometry on every frame,
    because the arrow keys move through the grid the user can see (§4.3).
    """

    def __init__(self, slots, write, prompt_hex=None, backup_path=None,
                 theme=None, fmt="", library=None, path=""):
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
        self.theme = theme                  # theme name, None: direct mode
        self.fmt = fmt                      # push target label (§13.7)
        self.path = path                    # the file a save writes
        self.library = library              # Library | None: the picker seam
        self.prompt_name = None             # prompt_name(label) -> str | None
        self.created = None                 # name created via n/N this session
        self.overlay = None                 # [name, ...] while the picker is up
        self.overlay_index = 0
        self.grid = grid_geometry(FALLBACK_COLS)   # refreshed every frame

    def dirty(self) -> bool:
        return self.slots != self.saved

    def picker_frame(self):
        """`(names, index, current)` for the draw call, or None when closed.

        `current` is the theme the buffer holds, so it is only marked while
        it is one of the rows on screen.
        """
        if self.overlay is None:
            return None
        return (self.overlay, self.overlay_index,
                self.theme if self.theme in self.overlay else "")


def report_session(st, report=None, notes=None):
    """What the session has to say once the frame is done (§13.6, §13.7).

    A function rather than a driver, because the wording is huebox's and not the
    compositor's: `app.py` runs the session and calls this afterwards, as the
    raw-mode loop used to, so the migration did not quietly reword what a user
    reads on exit.

    The push report and the picker's complaints go to stderr, after the frame and
    never inside it, where they would scroll through the editor. The report's
    wording is the caller's: it knows what it pushed and what it could not read.
    """
    if st.written:
        if st.theme is not None:
            print(f"  saved theme {st.theme}  {st.path}")
            print("")
        else:
            print(f"  saved {st.path}")
            if st.backup_made:
                print(f"  backup of the pre-save state: "
                      f"{st.backup_path}.huebox.bak")
            print("  reload your terminal to see the change\n")
    elif st.dirty():
        print("  nothing saved - the buffer was discarded\n")
    elif st.created:
        print(f"  created theme {st.created}  {st.path}")
        print("  Ctrl+S saves it to the terminal\n")
    else:
        print("  no changes\n")

    for line in list(report or []) + list(notes or []):
        print(f"huebox: {line}", file=sys.stderr)


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
    channel, direction = ADJUST[key]
    st.undo.append((name, st.slots[name]))
    st.slots[name] = step_hsv(st.slots[name], channel, direction, st.mult)


def _prompt(st):
    """`i` / `X` — hex entry, re-injected so `apply_key` stays testable."""
    name = SLOTS[st.sel]
    typed = st.prompt_hex(f"  new hex for {name}: ") if st.prompt_hex else None
    if typed is None:                        # cancelled: leave the buffer be
        return
    typed = typed.strip()
    if is_hex(typed):
        st.undo.append((name, st.slots[name]))
        st.slots[name] = normalize_hex(typed)
        st.status = f"{name} = {st.slots[name]}"
    else:
        st.status = "not a valid 6-digit hex - ignored"


# --------------------------------------------------------------------------
# the theme picker (§13.7)
# --------------------------------------------------------------------------

NEW_THEME_LABEL = "  new theme name: "


def _ask(st, label: str) -> str:
    """One line of input, or "" when the prompt was cancelled (§13.7).

    The prompt is the same injected raw-mode closure hex entry uses, so a
    cancelled read (Ctrl+C, EOF) returns to the editor with the buffer and
    the library exactly as they were.
    """
    typed = st.prompt_name(label) if st.prompt_name else None
    return (typed or "").strip()


def _taken(st, name: str) -> bool:
    """Is this name in the library? Case-folded, as `create` compares (§13.3)."""
    library = st.library
    return bool(library) and name.lower() in {row.lower()
                                              for row in library.names()}


def _adopt(st, name: str, slots: dict, path: str) -> None:
    """Make `name` the subject of the session (§13.7).

    A different theme means a different buffer, a different file to save,
    and no history to carry over: `saved` is what was loaded, the undo log
    is empty, the selection starts at slot 0, and the pending-discard arm
    is dropped — a fresh theme must never inherit a half-armed Esc.
    """
    st.slots = dict(slots)
    st.saved = dict(slots)
    st.undo.clear()
    st.sel = 0
    st.armed = False
    st.theme = name
    st.path = path
    st.backup_path = None           # huebox owns theme files: no .bak (§13.2)


def _create_theme(st, label: str = NEW_THEME_LABEL):
    """Ask for a name and write the buffer as that theme (§13.7).

    Text-based the whole way, no modal (decision 10's spirit): a name that
    is taken gets one more line saying so, where `y` overwrites it and any
    other name is used instead. Returns `(name, path)`, or `None` with the
    reason on `st.status` and nothing written.
    """
    if st.library is None:
        st.status = "no theme library in this session"
        return None
    name = _ask(st, label)
    if not name:
        st.status = "cancelled - no theme created"
        return None
    force = False
    if _taken(st, name):
        answer = _ask(st, f"  {name} exists - y overwrites it, "
                          f"or type another name: ")
        if not answer:
            st.status = f"cancelled - {name} is untouched"
            return None
        if answer.lower() in ("y", "yes"):
            force = True
        else:
            name = answer
            if _taken(st, name):
                st.status = f"{name} exists too - nothing created"
                return None
    path, problem = st.library.create(name, st.slots, force)
    if problem:
        st.status = problem
        return None
    return name, path


def _open_overlay(st) -> None:
    """`t` — the picker, with the session's own theme selected (§13.7)."""
    if st.library is None:
        st.status = "no theme library in this session"
        return
    names = st.library.names()
    if not names:
        st.status = "no themes yet - N makes one from this buffer"
        return
    st.overlay = names
    st.overlay_index = names.index(st.theme) if st.theme in names else 0
    st.status = ""


def _open_selected(st) -> None:
    """Enter in the picker: open the selected theme, and make it live.

    Choosing a theme is choosing it for the terminal too (§13.6), so the
    switch runs the ordinary save path: the truth file is written, the
    theme is pushed and the terminal is asked to re-read its config. The
    status line says which target took it, and the push report lands after
    the session like every other one. `Ctrl+S` still saves the *buffer*;
    after a switch there is simply nothing left to press, because the theme
    you picked is already what the terminal is showing.
    """
    if st.dirty():
        st.status = "save (Ctrl+S) or revert (r) first"      # decision 12
        return
    name = st.overlay[st.overlay_index]
    loaded = st.library.load(name) if st.library else None
    if loaded is None:
        st.status = f"{name} could not be opened"
        return
    slots, path = loaded
    _adopt(st, name, slots, path)
    st.overlay = None
    save_state(st)


def _picker_new(st) -> None:
    """`n` in the picker: the buffer becomes a new theme (§13.7).

    Creating is not switching — the buffer is exactly what gets written,
    so a dirty one is fine — but the new theme becomes the session's
    subject, which is what makes this a way out of a legacy direct-mode
    session (§13.4). The picker stays up with the new row selected.
    """
    made = _create_theme(st)
    if made is None:
        return
    name, path = made
    _adopt(st, name, st.slots, path)
    st.created = name
    _open_overlay(st)                 # re-read the library, land on the new row
    st.status = f"created {name} - current now"


def _overlay_key(key, st) -> None:
    """Keys while the picker is up: it owns the surface (§13.7).

    Nothing else can fire — no colour changes, no save, no quit — so a key
    meant for the editor cannot do damage behind a list the user is
    reading. `Esc`, `t`, `Q` and `Ctrl+C` all just put the editor back.
    """
    st.armed = False
    st.status = ""
    if key == "up":
        st.overlay_index = max(0, st.overlay_index - 1)
    elif key == "down":
        st.overlay_index = min(len(st.overlay) - 1, st.overlay_index + 1)
    elif key in ENTER_KEYS:
        _open_selected(st)
    elif key == "n":
        _picker_new(st)
    elif key == "t" or key in QUIT_KEYS:
        st.overlay = None


def _save_as_new(st) -> None:
    """`N` — the buffer becomes a new theme, and then it is saved (§13.7).

    The migration path out of a legacy direct-config session (§13.4): one
    prompt, `create` + `set_current`, then the ordinary save pipeline, so a
    theme made here is pushed exactly like one saved all session (§13.6).
    """
    made = _create_theme(st)
    if made is None:
        return
    name, path = made
    _adopt(st, name, st.slots, path)
    save_state(st)


def apply_key(key, st):
    """One keypress against the buffer (§14.2) or the picker (§13.7).

    Pure apart from disk and prompts: the injected `write` callback fires
    on Ctrl+S (and the session's first save also snapshots
    `<path>.huebox.bak`), and `prompt_hex` / `prompt_name` drop out of raw
    mode for one line — so the key surface itself is testable without a
    terminal. Sets `st.quit` when the session is done: a clean Esc (or
    Ctrl+C) quits at once, a dirty one arms and takes a second press. An
    open picker takes the whole key surface first, so quitting and colour
    edits cannot happen behind it.
    """
    if st.overlay is not None:
        _overlay_key(key, st)
        return

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

    if key in ARROWS:
        st.sel = move_slot(st.sel, key, st.grid)
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
    elif key == "t":
        _open_overlay(st)
    elif key == "N":
        _save_as_new(st)
    elif value is None:
        return                  # slot absent from this config: nothing to do
    elif key in ADJUST:
        _adjust(st, key)
    elif key in ("i", "X"):
        _prompt(st)

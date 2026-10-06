"""Editor tests: the too-small floor, frame determinism, staged saves (§15, §14)."""

import io
import os
import re
import signal
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import session  # noqa: E402
from huebox import editor  # noqa: E402
from huebox.color import (NAMED, SLOTS, hex_to_rgb,  # noqa: E402
                         rgb_to_hsv)
from huebox.render import (BOLD, ESCAPE_END, HSV_COMPACT,  # noqa: E402
                           HSV_FULL, HSV_TIGHT, RESET, bg, fg, hsv_numbers,
                           visible)

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FULL_SLOTS = {name: "#3f7a3f" for name in SLOTS}
HINT = ("terminal too small — need "
        f"{editor.MIN_COLS}x{editor.MIN_ROWS}")
SAVE = editor.SAVE_KEY


def frame(cols, rows, sel=3, undo=(), status="", mult=1, slots=None,
          banner=None):
    """draw_editor's output at a chosen size, captured as a string."""
    out = io.StringIO()
    with mock.patch.object(editor, "term_size", return_value=(cols, rows)), \
            mock.patch.object(sys, "stdout", out):
        editor.draw_editor("ghostty", "/tmp/huebox.conf",
                           FULL_SLOTS if slots is None else slots, sel,
                           list(undo), status, mult, use_banner=banner)
    return out.getvalue()


def picker_frame(cols=80, rows=24, names=("ash", "ember"), index=0,
                 current="ash", status="", slots=None):
    """The picker frame as `app.Picker` composes it: `theme_lines` + backdrop.

    `draw_editor` grew an `overlay=` mode for the whole migration and phase 5
    took it away again, giving the picker its own widget. The frame is those
    two calls in that order, and this is where that lives now — if `backdrop`
    ever goes missing again the floor tests below catch it, which is exactly
    how it was caught the first time.
    """
    slots = FULL_SLOTS if slots is None else slots
    out = [editor.backdrop(line, slots, cols)
           for line in editor.theme_lines(list(names), index, current,
                                          cols, rows, status, slots)]
    return "\r\n".join(out)


def plain(line):
    """One drawn row without its SGR escapes — what layout reads."""
    return ANSI.sub("", line)


def lines(text):
    """The frame's rows.

    The frame ends with a newline only when it does **not** fill the screen:
    written on the bottom row that newline scrolls the frame away, losing the
    wordmark row (§4.8 of the migration spec, fixed in `draw_editor`). So the
    trailing element is dropped when it is empty, not unconditionally.
    """
    rows = text.split("\r\n")
    if rows and rows[-1] == "":
        rows.pop()
    return rows


def plain_rows(text):
    """The frame's rows, escapes stripped: layout is a property of the text.

    §8.2 paints every row — the floor, the swatches, the chrome — so a
    matcher that reached for `line.startswith("    ")` would be reading an
    escape sequence and nothing else.
    """
    return [plain(line) for line in lines(text)]


def is_floor(line):
    """True for a row that is nothing but the frame's own floor (§8.2).

    A blank row is the floor painted once (§8.2's `backdrop`); a blank row
    *inside* a widget is content — the code block's own indent, the sample's
    reset — and reopens the fill after it. The two look alike on screen and
    this is the one matcher that has to look at the paint.
    """
    return line.count("48;2;") == 1 and not plain(line).strip()


def width(line):
    return len(plain(line))


def _block_rows(body, head):
    """The block under `head`'s own rows, plain: blanks inside kept.

    §8.2 paints every row out to `cols`, so a row of floor wears the same
    four columns of indent a widget wears — indentation alone can no longer
    end a block, but a row that is *only* floor still can.
    """
    start = next((i for i, line in enumerate(body) if head in plain(line)),
                 None)
    if start is None:
        return []
    out = []
    for line in body[start + 1:]:
        if is_floor(line) or not plain(line).startswith("    "):
            break
        out.append(plain(line))
    return out


def example_rows(body):
    """How many of the strip's three rows this frame drew."""
    return sum(1 for line in body if re.match(
        r"^  (background|selection-background|cursor-color)/", plain(line)))


def _code_rows(body):
    return _block_rows(body, "live code")


def code_lines(body):
    """How many rows of the code sample this frame drew."""
    return len(_code_rows(body))


def _diff_rows(body):
    return _block_rows(body, "live diff")


def diff_lines(body):
    """How many rows of the diff hunk this frame drew."""
    return len(_diff_rows(body))


def diff_block(body):
    """The hunk's own lines, plain: no header row, no indent."""
    return [line.strip() for line in _diff_rows(body)]


def code_block(body):
    """The code sample's own lines, plain: no header row, no blank ones."""
    return [line.strip() for line in _code_rows(body) if line.strip()]


def unpainted(row: str) -> list:
    """The columns a terminal would paint in its *own* background (§8.2).

    A walk, not a regex: SGR 0 clears the background as well as the
    foreground, so a row is only whole if something repaints the floor
    after every reset. Returns the column numbers that would show through.
    """
    holes, set_bg, i, column = [], False, 0, 0
    while i < len(row):
        if row[i] == "\033":
            j = i + 1
            while j < len(row) and row[j] not in ESCAPE_END:
                j += 1
            sgr = row[i:j + 1]
            if sgr == RESET:
                set_bg = False
            elif sgr.startswith("\033[48;2;"):
                set_bg = True
            i = j + 1
            continue
        if not set_bg:
            holes.append(column)
        column += 1
        i += 1
    return holes


class Floor(unittest.TestCase):
    """The editor frame stands on the buffer's own background (§8.2)."""

    CLEAR = "\033[H\033[2J"

    def test_every_row_reaches_the_width_and_opens_in_the_fill(self):
        fill = bg(FULL_SLOTS["background"])
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            with self.subTest(size=(cols, rows)):
                for line in lines(frame(cols, rows)):
                    self.assertEqual(width(line), cols)
                    self.assertTrue(line.replace(self.CLEAR, "", 1)
                                    .startswith(fill), repr(line[:24]))
                    self.assertTrue(line.endswith(RESET))

    def test_no_column_of_the_frame_shows_the_terminals_own_background(self):
        # §8.2 — every reset in a row reopens the fill. Without that, the
        # run after it (a wordmark letter, a parenthetical beside a
        # header, a gap between hints) sits on the terminal's background,
        # which is a hole in the middle of a frame painted all one colour.
        for cols, rows in ((120, 40), (100, 30), (80, 24), (60, 16),
                           (40, 12)):
            with self.subTest(size=(cols, rows)):
                for row, line in enumerate(lines(frame(cols, rows))):
                    self.assertEqual(unpainted(line.replace(self.CLEAR, "", 1)),
                                     [], f"row {row}")

    def test_the_picker_frame_has_no_hole_either(self):
        for line in lines(picker_frame()):
            self.assertEqual(unpainted(line.replace(self.CLEAR, "", 1)), [])

    def test_moving_the_background_moves_the_whole_frame(self):
        # §14.1 — the floor is live: one buffer edit with no save in between
        # repaints every row, the air between widgets included
        moved = "#1c1f26"
        edited = dict(FULL_SLOTS, background=moved)
        before = lines(frame(80, 24,
                             slots=dict(FULL_SLOTS, background="#101014")))
        after = lines(frame(80, 24, slots=edited))
        self.assertEqual(len(before), len(after))
        for row in range(len(before)):
            with self.subTest(row=row):
                self.assertIn(bg(moved), after[row])
                self.assertNotEqual(before[row], after[row])

    def test_the_picker_frame_stands_on_it_too(self):
        # §13.7 — the picker takes the frame over, and the floor is the
        # frame's, so it takes the floor as well
        for line in lines(picker_frame()):
            self.assertEqual(width(line), 80)
            self.assertTrue(line.replace(self.CLEAR, "", 1)
                            .startswith(bg(FULL_SLOTS["background"])))

    def test_the_too_small_frame_is_left_unpainted(self):
        # §8.2 — the fallback is about the window, not the theme
        body = lines(frame(editor.MIN_COLS - 1, 24))
        self.assertNotIn("48;2;", body[0])
        self.assertLessEqual(width(body[0]), editor.MIN_COLS - 1)


class Readout(unittest.TestCase):
    """The selected row's reading: bars with the numbers beside them (§8.3)."""

    SLOTS = dict(FULL_SLOTS, background="#101014", foreground="#e6e6ea",
                 **{"palette-4": "#61afef"})
    # the narrowest row that can still carry each part, computed from the
    # strings rather than remembered: the subject and the readings' own
    # chrome on one, the specimen on the other (§8.3). The subject pins the
    # slot name to `SELECTED_KEY_W` with single spaces and no trailing air,
    # so it is exactly the indent plus the title plus the longest name plus
    # the hex: 40 columns, on screen at `MIN_COLS`.
    SUBJECT = 2 + len("selected") + 1 + editor.SELECTED_KEY_W + 1 + 7
    SPECIMEN = len("    AaBbCc 0123 ")
    EXACT = SPECIMEN + len(hsv_numbers(207 / 360, 0.594, 0.937)) + 3

    def selected(self, cols, rows=30, sel=4, slots=None, painted=False):
        # §8.1 (decision 36) — the readout sits in the right column of
        # the side-by-side top block where it fits, so the row carries the
        # left column's air before the `selected` title; strip it first.
        body = lines(frame(cols, rows, sel=sel, slots=slots or self.SLOTS))
        row = next(line for line in body
                   if plain(line).strip().startswith("selected "))
        return row if painted else plain(row)

    CELL = re.compile(r"\x1b\[48;2;(\d+);(\d+);(\d+)m"
                      r"(?:\x1b\[38;2;(\d+);(\d+);(\d+)m)?([^\x1b])")
    FILL = hex_to_rgb("#101014")        # SLOTS' background, the floor of §8.2

    def bars(self, cols, slots=None, row=None):
        """Every bar cell of the selected row, painted, in order.

        A bar cell carries no ink unless it is the hairline, so the
        foreground is optional — which is also how the frame says so. The
        row's own floor is painted in the same background and is not a bar.
        """
        painted = row or self.selected(cols, slots=slots, painted=True)
        return [cell for cell in self.CELL.findall(painted)
                if (tuple(int(v) for v in cell[:3]) != self.FILL
                    or cell[6] != " ")]

    def specimen(self, cols, rows=30, slots=None, painted=False):
        row = next(line for line in lines(frame(cols, rows, sel=4,
                                               slots=slots or self.SLOTS))
                   if "AaBbCc" in plain(line))
        return row if painted else plain(row)

    def exact(self, value="#61afef"):
        """The reading as the frame spells it out, below the bars."""
        return hsv_numbers(*rgb_to_hsv(hex_to_rgb(value)))

    def narrowest(self):
        """The first terminal width at which the row still draws bars."""
        return next(cols for cols in range(40, 130)
                    if self.bars(cols))

    @mock.patch.dict(os.environ, {"HUEBOX_TOP_NEW": "0"})
    def test_both_readings_are_on_screen_where_the_row_fits(self):
        # the bars are the glance, the exact numbers are the truth, and
        # neither of them gives up a row for the other (§8.3). The full
        # rung needs the right column's width (decision 36); the slot-name
        # field is pinned, so 100 carries the tight rung where it used to
        # carry compact.
        wide = self.selected(120)
        self.assertEqual(len(self.bars(120)), sum(HSV_FULL))
        for reading in ("hue 207°", "sat  59%", "val  94%"):
            self.assertIn(reading, wide)          # beside its bar
        self.assertIn(self.exact(), self.specimen(120))
        self.assertNotIn(self.exact(), wide)
        self.assertEqual(len(self.bars(100)), sum(HSV_TIGHT))

    @mock.patch.dict(os.environ, {"HUEBOX_TOP_NEW": "0"})
    def test_a_bar_carries_the_sweep_and_the_line_and_nothing_else(self):
        # the arrangement that lets the number and the hairline both be
        # complete: the reading is beside its bar, so the bar has only the
        # sweep and the line in it, and there is nothing for the line to take
        cells = self.bars(120)
        self.assertEqual(len(cells), sum(HSV_FULL))
        self.assertEqual({cell[6] for cell in cells} - {" "}, {"\u258f"})
        # the hairline is the only ink in a bar, and it is one per bar
        self.assertEqual(len([cell for cell in cells if cell[3]]), 3)

    def test_the_two_readings_appear_and_yield_in_their_own_time(self):
        # the numbers never move row: they are under the bars when the bars
        # are there, and under a bare subject when they are not
        edge = self.narrowest()
        for cols, bars in ((120, True), (100, True), (edge, True),
                           (edge - 1, False), (self.EXACT, False),
                           (self.EXACT - 1, False)):
            with self.subTest(size=(cols, 30)):
                self.assertEqual(bool(self.bars(cols)), bars)
                self.assertEqual(self.exact() in self.specimen(cols),
                                 cols >= self.EXACT)
        # and the subject is never the thing that gets cut
        for cols in (120, 100, 80, 66, 60, 50, 45, 40):
            with self.subTest(size=(cols, 30)):
                text = self.selected(cols)
                self.assertLessEqual(visible(text), cols)
                self.assertIn("#61afef", text)
                self.assertLess(text.index("palette-4"), text.index("#61afef"))

    @mock.patch.dict(os.environ, {"HUEBOX_TOP_NEW": "0"})
    def test_the_row_does_not_move_when_a_reading_grows_a_digit(self):
        # §8.3 — the readings are in fixed fields, so the bars start in the
        # same columns whatever the slot says; the row is stable while the
        # reader is trying to look at the colours
        at = None
        for slots in (self.SLOTS,
                      dict(self.SLOTS, **{"palette-4": "#00ff00"}),
                      dict(self.SLOTS, **{"palette-4": "#090000"}),
                      dict(self.SLOTS, **{"palette-4": "#61aaff"})):
            body = plain_rows(frame(120, 30, sel=4, slots=slots))
            row = next(line for line in body
                       if line.strip().startswith("selected "))
            self.assertLessEqual(visible(row), 120)
            bars = self.bars(120, slots=slots)
            self.assertEqual(len(bars), sum(HSV_FULL))
            if at is None:
                at = visible(row) - len(bars)
            self.assertEqual(visible(row) - len(bars), at)

    def test_the_reading_is_never_on_screen_twice(self):
        for cols in (120, 100, 90, 80, 66, 60, 50):
            with self.subTest(size=(cols, 30)):
                body = "".join(plain_rows(frame(cols, 30, sel=4,
                                                slots=self.SLOTS)))
                self.assertEqual(body.count(self.exact()), 1)

    def test_nudging_the_hue_moves_the_line_and_the_exact_reading(self):
        # the wheel is the whole wheel and does not move; the hairline on it
        # does, and so does the reading below (§14.1)
        before = self.selected(120)
        st = editor.EditorState(dict(self.SLOTS), lambda values: None)
        st.sel, st.grid = 4, editor.grid_geometry(120)
        for key in ("f", "f", "w"):
            editor.apply_key(key, st)
        after = self.selected(120, slots=st.slots)
        self.assertNotEqual(before, after)
        self.assertNotIn("#61afef", after)              # the hex moved
        self.assertNotEqual(self.bars(120), self.bars(120, slots=st.slots))
        self.assertNotEqual(hsv_numbers(*rgb_to_hsv(hex_to_rgb("#61afef"))),
                            hsv_numbers(*rgb_to_hsv(hex_to_rgb(
                                st.slots["palette-4"]))))

    def test_no_column_of_the_row_shows_the_terminals_own_background(self):
        # §8.2 — a bar is a run of cells with one hairline in it, and every
        # reset in the row has to be followed by the fill again
        for cols in (100, 80, 70, 60):
            with self.subTest(size=(cols, 30)):
                self.assertEqual(unpainted(self.selected(cols, painted=True)),
                                 [])

    def test_the_readout_never_costs_a_row(self):
        # §15 — the readout is a string on a row the frame already drew, so
        # two sizes a rung apart keep the same frame and the same widgets
        wide, narrow = frame(111, 30), frame(100, 30)
        self.assertNotEqual(len(self.bars(111)), len(self.bars(100)))
        self.assertEqual(len(lines(wide)), len(lines(narrow)))
        self.assertEqual(code_lines(lines(wide)), code_lines(lines(narrow)))
        self.assertEqual(example_rows(lines(wide)),
                         example_rows(lines(narrow)))


class ReadoutSplit(unittest.TestCase):
    """The default readout: metadata left, one HSV axis per row.

    `HUEBOX_TOP_NEW` defaults on: each of the three top rows is
    `[metadata | one HSV axis]`, so the three bars share one width and
    the row count never moves. The single-line ladder (`HSV_FULL` etc.)
    is the `HUEBOX_TOP_NEW=0` opt-out `Readout` pins above.
    """

    SLOTS = dict(FULL_SLOTS, background="#101014", foreground="#e6e6ea",
                 **{"palette-4": "#61afef"})

    def painted_top(self, cols, rows=30, sel=4, slots=None):
        body = lines(frame(cols, rows, sel=sel, slots=slots or self.SLOTS))
        return body[:3]

    def plain_top(self, cols, rows=30, sel=4, slots=None):
        return [plain(line)
                for line in self.painted_top(cols, rows, sel, slots)]

    def bars_in(self, painted_row):
        return [cell for cell in Readout.CELL.findall(painted_row)
                if (tuple(int(v) for v in cell[:3]) != Readout.FILL
                    or cell[6] != " ")]

    def exact(self, value="#61afef"):
        return hsv_numbers(*rgb_to_hsv(hex_to_rgb(value)))

    def test_split_rows_carry_one_axis_each(self):
        first, second, third = self.plain_top(120)
        # metadata left: theme, selected subject, specimen + exact.
        self.assertIn("ghostty", first)
        self.assertIn("selected", second)
        self.assertIn("palette-4", second)
        self.assertIn("AaBbCc", third)
        self.assertIn(self.exact(), third)
        self.assertNotIn(self.exact(), first)
        self.assertNotIn(self.exact(), second)
        # one axis right: hue, sat, val each beside its own bar.
        self.assertIn("hue", first)
        self.assertNotIn("sat", first)
        self.assertNotIn("val", first)
        self.assertIn("sat", second)
        self.assertNotIn("hue", second)
        # `val` names the bar; the exact below it names all three, so
        # only the bar's presence is asserted on the third row.
        self.assertIn("val", third)

    def test_bars_share_one_width(self):
        # One width for all three axes, spending what the metadata left:
        # 47 / 38 / 27 / 7 cells at 120 / 111 / 100 / 80, nothing at 60
        # where even a minimal bar does not fit.
        for cols, want in ((120, 47), (111, 38), (100, 27), (80, 7)):
            with self.subTest(cols=cols):
                painted = self.painted_top(cols)
                counts = [len(self.bars_in(row)) for row in painted]
                self.assertEqual(counts, [want] * 3)
        self.assertEqual([len(self.bars_in(row))
                          for row in self.painted_top(60)], [0] * 3)

    def test_no_bars_where_split_does_not_fit(self):
        # At 60 the split returns `None` and the rows are bare metadata:
        # the subject and the exact reading survive, the bars do not.
        first, second, third = self.plain_top(60)
        self.assertIn("ghostty", first)
        self.assertIn("#61afef", second)
        self.assertIn(self.exact(), third)
        self.assertNotIn("hue 207\u00b0", first)
        self.assertNotIn("sat", second)

    def test_each_bar_carries_its_sweep_and_its_line(self):
        # Like the single-line bar: the sweep plus one hairline, and
        # nothing else — but one axis per row, so one ink per row.
        for row in self.painted_top(120):
            with self.subTest(row=plain(row)[:24]):
                cells = self.bars_in(row)
                self.assertTrue(cells)
                self.assertEqual({cell[6] for cell in cells} - {" "},
                                 {"\u258f"})
                self.assertEqual(len([cell for cell in cells if cell[3]]),
                                 1)


class TopBlock(unittest.TestCase):
    """The side-by-side top block: wordmark left, theme readout right.

    Decision 36 — `[huebox] [theme/path, selected]`: the header and the
    selected readout share three rows at the top of the frame instead of
    five scattered ones, so the palette grid moves up a row and the
    widgets below gain it. Below `TOP_MIN_COLS` the right column cannot
    hold even a bare readout and the frame keeps the stacked header.
    """

    SLOTS = dict(FULL_SLOTS, background="#101014", foreground="#e6e6ea",
                 **{"palette-4": "#61afef"})

    def top(self, cols, rows=30, sel=4, slots=None):
        return plain_rows(frame(cols, rows, sel=sel,
                                slots=slots or self.SLOTS))[:4]

    def test_the_wordmark_and_the_theme_share_the_first_row(self):
        row = self.top(100)[0]
        self.assertTrue(row.startswith("  huebox"))
        self.assertIn("ghostty", row)
        self.assertIn("/tmp/huebox.conf", row)
        # the left column is the indent, the six letters and the gap
        self.assertEqual(row[:editor.TOP_LEFT_W], "  huebox    ")

    def test_the_selected_readout_sits_beside_the_wordmark(self):
        first, second, third = self.top(100)[:3]
        self.assertIn("selected", second)
        self.assertIn("palette-4", second)
        self.assertIn("#61afef", second)
        self.assertIn("AaBbCc", third)
        self.assertIn("hue 207.0", third)
        # the readout rows carry the left column's air, not a second copy
        self.assertTrue(second.startswith(" " * editor.TOP_LEFT_W))
        self.assertNotIn("huebox", second)

    @mock.patch.dict(os.environ, {"HUEBOX_TOP_NEW": "0"})
    def test_the_right_column_keeps_the_ladder(self):
        # full where the right column has the columns, compact a rung
        # down, nothing where even the tight rung does not fit — the same
        # ladder as §8.3, measured against the column, not the frame. The
        # slot-name field is pinned to the longest slot, so the rungs want
        # 119 / 111 / 103 columns: 100 is bare where it used to be compact.
        def bars(cols):
            painted = lines(frame(cols, 30, sel=4, slots=self.SLOTS))
            row = next(line for line in painted
                       if plain(line).strip().startswith("selected "))
            cells = Readout.CELL.findall(row)
            return [cell for cell in cells
                    if (tuple(int(v) for v in cell[:3]) != Readout.FILL
                        or cell[6] != " ")]
        self.assertEqual(len(bars(120)), sum(HSV_FULL))
        self.assertEqual(len(bars(111)), sum(HSV_COMPACT))
        self.assertEqual(len(bars(100)), sum(HSV_TIGHT))
        self.assertEqual(bars(80), [])

    def test_below_the_floor_the_header_stacks_again(self):
        body = self.top(40, rows=24)
        self.assertTrue(body[0].startswith("  huebox  ghostty"))
        self.assertNotIn("selected", body[0])
        # the readout stays below the interface grid, as it always was
        full = plain_rows(frame(40, 24, sel=4, slots=self.SLOTS))
        selected = next(i for i, line in enumerate(full)
                        if line.strip().startswith("selected "))
        interface = next(i for i, line in enumerate(full)
                         if line.strip() == "interface")
        self.assertGreater(selected, interface)

    def test_the_pin_restores_the_stacked_header(self):
        # `HUEBOX_TOP=0` pins the stacked header at any width: the
        # wordmark row names the format again, and the readout sits below
        # the interface grid, as it does under `TOP_MIN_COLS`.
        with mock.patch.dict(os.environ, {"HUEBOX_TOP": "0"}):
            body = self.top(100, rows=24)
        self.assertTrue(body[0].startswith("  huebox  ghostty"))
        self.assertNotIn("selected", body[0])
        with mock.patch.dict(os.environ, {"HUEBOX_TOP": "0"}):
            full = plain_rows(frame(100, 24, sel=4, slots=self.SLOTS))
        selected = next(i for i, line in enumerate(full)
                        if line.strip().startswith("selected "))
        interface = next(i for i, line in enumerate(full)
                         if line.strip() == "interface")
        self.assertGreater(selected, interface)

    def test_the_top_names_both_of_its_blocks(self):
        found = []
        with mock.patch.object(sys, "stdout", io.StringIO()):
            editor.draw_editor("ghostty", "", dict(FULL_SLOTS), 0, [],
                               "", 1, regions=found, size=(100, 30))
        names = [name for name, _, _ in found]
        self.assertEqual(names[:2], ["header", "selected"])
        self.assertEqual(found[0][1], 0)
        self.assertEqual(found[0][1] + found[0][2], found[1][1])

    def test_the_banner_keeps_the_right_column(self):
        # the raster banner above is the only huebox on screen: the top
        # block's wordmark stands back to air, the theme readout stays
        auto = plain_rows(frame(120, 55))
        # the banner is block art and the top block stood its wordmark
        # back to air: the theme row starts with the left column's air,
        # not the letters (the path still names huebox, as it should)
        theme = next(line for line in auto if "ghostty" in line)
        self.assertEqual(theme[:editor.TOP_LEFT_W],
                          " " * editor.TOP_LEFT_W)
        selected = next(line for line in auto
                        if line.strip().startswith("selected "))
        self.assertIn("palette-3", selected)


class TopLayout(unittest.TestCase):
    """The unified top's width shares (§8.1, decision 40).

    One arrangement at every size it fits — bare logo, the flexible info
    column, the bordered `editor` box — so `info` never carries the
    editor's controls and the two never merge. As the window narrows the
    logo steps down its ladder first, then info truncates its path, then
    the head stacks: past the tightest share the caller keeps the bare
    stack rather than squeezing a panel below usable.
    """

    SLOTS = dict(FULL_SLOTS, background="#101014", foreground="#e6e6ea")
    LONG = "/tmp/some/deeply/nested/theme.toml"

    def test_shares_tile_the_width(self):
        # `(left, info, editor)`: three columns, no air, at every width
        # the layout fits — short label or long.
        for width in (200, 160, 120, 100, 80, 60):
            for path in ("/tmp/x", self.LONG):
                with self.subTest(width=width, path=path):
                    lay = editor.top_layout(width, "ghostty", path,
                                            self.SLOTS, 0)
                    self.assertIsNotNone(lay)
                    left_w, info_w, editor_outer, _stacked = lay
                    self.assertEqual(left_w + info_w + editor_outer,
                                     width)
                    self.assertIn(left_w, (editor.BANNER_LEFT_W,
                                           editor.MINI_LEFT_W,
                                           editor.TOP_LEFT_W))

    def test_the_logo_steps_down_first(self):
        # Banner while it fits, then mini, then the wordmark — the same
        # ladder the frame stands up, walked widest first.
        shorts = [editor.top_layout(w, "ghostty", "/tmp/x", self.SLOTS,
                                    0)[0]
                  for w in (160, 100, 80)]
        self.assertEqual(shorts, [editor.BANNER_LEFT_W,
                                  editor.MINI_LEFT_W,
                                  editor.TOP_LEFT_W])

    def test_info_shrinks_while_the_editor_stays_wide(self):
        # The user's case: a long path truncates (`…` keeps the tail)
        # instead of holding air while the editor squeezes — at 120 the
        # banner still stands, info gives up 24 columns, and the head
        # stays on one row.
        lay = editor.top_layout(120, "ghostty", self.LONG, self.SLOTS,
                                0)
        self.assertEqual(lay[0], editor.BANNER_LEFT_W)
        self.assertLess(lay[1], len("ghostty  " + self.LONG))
        self.assertFalse(lay[3], "the head stacked before info shrank")
        meta, head, meta_w = editor.top_editor_meta(
            "ghostty", self.LONG, self.SLOTS, 0, meta_w=lay[1],
            stacked=lay[3])
        from huebox.render import visible as _visible
        self.assertEqual(meta_w, lay[1])
        self.assertLessEqual(_visible(meta[0]), lay[1])
        self.assertIn("…", plain(meta[0]))
        content = lay[2] - 2 - 2 * editor.EDITOR_PAD_X
        self.assertLessEqual(max(_visible(row) for row in head), content)

    def test_stacked_where_wide_cannot_fit(self):
        # At 60 even the wordmark leaves no room for the one-row head,
        # so the chip and the specimen stack instead of clipping.
        lay = editor.top_layout(60, "ghostty", "/tmp/x", self.SLOTS, 0)
        self.assertTrue(lay[3])
        _meta, head, _w = editor.top_editor_meta(
            "ghostty", "/tmp/x", self.SLOTS, 0, meta_w=lay[1],
            stacked=True)
        self.assertEqual(len(head), 2)
        content = lay[2] - 2 - 2 * editor.EDITOR_PAD_X
        from huebox.render import visible as _visible
        self.assertLessEqual(max(_visible(row) for row in head), content)

    def test_none_below_usable(self):
        # Past the tightest share — wordmark, truncated label, stacked
        # head — the caller keeps the bare stack, and `HUEBOX_TOP=0`
        # pins that stack past the layout like the bare frame.
        self.assertIsNone(editor.top_layout(50, "ghostty", "/tmp/x",
                                            self.SLOTS, 0))
        self.assertIsNone(editor.top_layout(40, "ghostty", "/tmp/x",
                                            self.SLOTS, 0))
        with mock.patch.dict(os.environ, {"HUEBOX_TOP": "0"}):
            self.assertIsNone(editor.top_layout(160, "ghostty", "/tmp/x",
                                                self.SLOTS, 0))

    def test_the_readout_does_not_move_with_the_selection(self):
        # `SELECTED_KEY_W` pins the slot-name field to the longest slot, so
        # the hex, the bars and the specimen start in the same column whether
        # the selection is `palette-0` or `selection-foreground`.
        from huebox.render import visible as _visible
        rows_short = editor.top_right_panel_rows(
            "ghostty", "/tmp/x", self.SLOTS, 0, 68)
        rows_long = editor.top_right_panel_rows(
            "ghostty", "/tmp/x", self.SLOTS,
            editor.SLOTS.index("selection-foreground"), 68)
        for short, long in zip(rows_short, rows_long):
            self.assertEqual(_visible(short), _visible(long))


class TooSmall(unittest.TestCase):
    def test_hint_text_and_centering(self):
        self.assertEqual(editor.too_small_frame(120).strip(), HINT)
        centred = editor.too_small_frame(80)
        self.assertTrue(centred.startswith(" " * ((80 - len(HINT)) // 2)))
        self.assertEqual(len(ANSI.sub("", editor.too_small_frame(20))), 20)

    def test_hint_replaces_the_frame(self):
        # the FULL hint must survive at MIN_COLS-1: a truncated hint would
        # hide the very size it tells the user to enlarge to (review P1)
        for size in ((editor.MIN_COLS - 1, 24), (80, editor.MIN_ROWS - 1)):
            with self.subTest(size=size):
                out = frame(*size)
                self.assertIn(HINT, out)
                self.assertNotIn("palette", out)
                self.assertNotIn("\033[48;2;", out)   # no swatches drawn

    def test_frame_is_one_line_on_a_cleared_screen(self):
        sizes = ((editor.MIN_COLS - 1, 24), (60, 8), (24, 10), (10, 4))
        for cols, rows in sizes:
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))   # painted: the clear is one
                self.assertEqual(len(body), 1)
                self.assertTrue(body[0].startswith("\033[H\033[2J"))
                self.assertLessEqual(width(body[0]), cols)

    def test_at_the_minimum_the_editor_draws(self):
        out = frame(editor.MIN_COLS, editor.MIN_ROWS)
        self.assertNotIn("too small", out)
        self.assertIn("palette", out)


class NormalFrame(unittest.TestCase):
    def test_eight_by_twentyfour_is_the_full_editor(self):
        out = frame(80, 24, status="saved")
        self.assertNotIn("too small", out)
        self.assertIn("huebox", out)
        self.assertIn("palette", out)
        self.assertIn("interface", out)
        self.assertIn("saved", out)
        self.assertLessEqual(len(lines(out)), 24)

    def test_frames_are_byte_identical(self):
        args = dict(sel=17, undo=[("palette-3", "#ffffff")], status="saved",
                    mult=5)
        self.assertEqual(frame(80, 24, **args), frame(80, 24, **args))
        self.assertEqual(frame(60, 16, **args), frame(60, 16, **args))

    def test_layout_holds_at_several_sizes(self):
        # (40, 10) is below-min on rows and belongs to the TooSmall tests
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            with self.subTest(size=(cols, rows)):
                for line in lines(frame(cols, rows)):
                    self.assertLessEqual(width(line), cols)

    def test_a_wider_terminal_spreads_the_palette(self):
        narrow = max(width(line) for line in lines(frame(60, 16)))
        wide = max(width(line) for line in lines(frame(100, 30)))
        self.assertGreater(wide, narrow)

    def test_frame_follows_the_buffer_not_disk(self):
        # §14.1 — the frame renders unsaved state: one buffer mutation with
        # no save in between must change what is drawn
        before = frame(80, 24)
        edited = dict(FULL_SLOTS, background="#101014", foreground="#e6e6ea")
        after = frame(80, 24, slots=edited)
        self.assertNotEqual(before, after)
        self.assertIn("#101014", ANSI.sub("", after))
        self.assertNotIn("#101014", ANSI.sub("", before))
        # and the same buffer twice is still byte-identical
        self.assertEqual(after, frame(80, 24, slots=edited))

    def test_examples_sit_above_the_code_sample(self):
        body = plain_rows(frame(100, 30))
        heads = {line.split()[0] if line.split() else "" for line in body}
        self.assertIn("examples", heads)
        self.assertIn("live", heads)
        self.assertLess(body.index(next(l for l in body if "examples" in l)),
                        body.index(next(l for l in body if "live code" in l)))

    def test_the_diff_sits_between_the_strip_and_the_sample(self):
        # §14.4 — the hunk is a block of its own, and it reads as the
        # change to the sample drawn below it
        body = lines(frame(120, 40))
        at = [next(i for i, l in enumerate(body) if head in plain(l))
              for head in ("examples", "live diff", "live code")]
        self.assertEqual(at, sorted(at))
        self.assertTrue(diff_block(body))

    def test_the_diff_spends_red_and_green_from_the_buffer(self):
        # §14.1 — the hunk is live: one buffer edit repaints both sides on
        # the same frame, and nothing about the frame is cached
        def drawn(slots):
            return frame(120, 40, slots=dict(FULL_SLOTS, **slots))
        before = drawn({"palette-1": "#ff0000", "palette-2": "#00ff00"})
        after = drawn({"palette-1": "#ff00ff", "palette-2": "#00ffff"})
        self.assertNotEqual(before, after)
        for line in lines(after):
            plain = ANSI.sub("", line)
            if plain.strip().startswith("-var"):
                self.assertIn(fg("#ff00ff"), line)
            if plain.strip().startswith("+var"):
                self.assertIn(fg("#00ffff"), line)
        # and back to the first buffer, the same pixels
        self.assertEqual(before, drawn({"palette-1": "#ff0000",
                                        "palette-2": "#00ff00"}))

    def test_the_diff_never_shows_half_a_pair(self):
        # a hunk is the `@@` line and whole removed/added pairs: the rows
        # after the header always come in twos
        for cols, rows in ((120, 44), (120, 40), (110, 36)):
            with self.subTest(size=(cols, rows)):
                hunk = diff_block(lines(frame(cols, rows)))
                self.assertTrue(hunk[0].startswith("@@"))
                self.assertEqual((len(hunk) - 1) % 2, 0)
                signs = [line[0] for line in hunk[1:]]
                self.assertEqual(signs, ["-", "+"] * (len(signs) // 2))

    def test_the_diff_grows_out_of_the_rows_the_sample_did_not_need(self):
        # §15 — the hunk fills spare room and never takes a row from the
        # sample: whole where the frame has a pair to spare, at its floor
        # where it has one, and absent everywhere else
        for cols, rows, code, hunk in ((120, 40, 10, 5), (120, 44, 10, 5),
                                       (112, 40, 10, 5),
                                       (110, 36, 10, editor.DIFF_FLOOR - 1)):
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertEqual(code_lines(body), code)
                self.assertEqual(diff_lines(body), hunk)
                self.assertEqual(example_rows(body), 3)

    def test_a_frame_too_short_for_the_diff_is_the_frame_without_it(self):
        # §15 — where the sample needs every row the hunk is simply not
        # drawn, and the other two widgets keep the rows the v1 ladder gave
        # them: (cols, rows, strip rows, sample lines)
        # Decision 36's side-by-side top saves a row, and at 80x26 the
        # sample spends it (five lines where four used to fit).
        for cols, rows, strip, code in ((100, 30, 3, 7), (80, 30, 3, 7),
                                        (80, 26, 3, 5), (80, 24, 3, 4),
                                        (80, 22, 3, 4), (80, 20, 3, 3),
                                        (80, 16, 0, 3), (60, 24, 3, 4)):
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertEqual(diff_lines(body), 0)
                self.assertEqual(example_rows(body), strip)
                self.assertEqual(code_lines(body), code)

    def test_examples_and_the_block_share_the_leftover_rows(self):
        # §15 — the strip gives up rows before the code block loses a line,
        # and both survive at every size spec §15.4 tests
        for cols, rows, strip, code, hunk in (
                (100, 30, 3, 7, 0), (80, 24, 3, 4, 0), (60, 24, 3, 4, 0)):
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertEqual(example_rows(body), strip)
                self.assertEqual(code_lines(body), code)
                self.assertEqual(diff_lines(body), hunk)

    def test_the_block_outlives_the_strip(self):
        # the sample is the widget the editor exists to show (§9), so where
        # the two cannot both fit the strip gives up rows first — and goes
        # before the block does, including at the sizes where the block
        # used to vanish entirely
        for cols, rows, strip, code in ((80, 20, 3, 3), (80, 18, 1, 3),
                                        (80, 16, 0, 3), (60, 20, 3, 3)):
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertEqual(example_rows(body), strip)
                self.assertEqual(code_lines(body), code)
                self.assertEqual(diff_lines(body), 0)

    def test_a_truncated_block_drops_its_least_useful_lines(self):
        # a short frame spends rows on code: the leading comment, the
        # closing brace and the blank inside the block go first
        block = code_block(lines(frame(80, 24)))
        self.assertTrue(block[0].startswith("const huebox"))
        self.assertNotIn("}", block)
        self.assertFalse(any("live preview" in line for line in block))

    def test_the_frame_sheds_decoration_before_a_widget(self):
        # §15 — the palette legend is a courtesy (the grid is numbered), so
        # a tight frame spends it, and the blank separators after it, before
        # it spends a widget
        legend = plain(editor.PALETTE_LEGEND).strip()
        tall = [line.strip() for line in plain_rows(frame(80, 40))]
        self.assertIn(legend, tall)
        tight = [line.strip() for line in plain_rows(frame(80, 24))]
        self.assertNotIn(legend, tight)
        for keep in ("palette", "interface", "AaBbCc", "examples", "live code"):
            self.assertTrue(any(keep in line for line in tight), keep)

    def test_the_widget_blocks_are_separated_by_a_row_of_air(self):
        # §15.6 — the blanks around the hunk are decoration, and decoration
        # is spent out of what the hunk did not ask for: two spare rows get
        # air above and below, one gets the blank below it (the last row
        # given up, after the widget it follows), and the floor gets neither
        def blanks(cols, rows):
            body = lines(frame(cols, rows))
            head = next(i for i, line in enumerate(body)
                        if "live diff" in plain(line))
            hunk = next(i for i, line in enumerate(body)
                        if plain(line) == _diff_rows(body)[-1])
            return (not plain(body[head - 1]).strip(),
                    not plain(body[hunk + 1]).strip())

        # Decision 36's saved row moves the whole ladder one row down:
        # the air that used to start at 120x38 starts at 120x36 instead.
        for cols, rows, (above, below) in ((120, 44, (True, True)),
                                           (120, 40, (True, True)),
                                           (112, 40, (True, True)),
                                           (120, 38, (True, True)),
                                           (120, 36, (False, False)),
                                           (110, 36, (False, True))):
            with self.subTest(size=(cols, rows)):
                self.assertEqual(blanks(cols, rows), (above, below))

    def test_the_frame_chrome_is_drawn_from_the_buffer(self):
        # §8.1 — the frame says itself in the buffer's own colours: keys in
        # palette-11, their labels in palette-14, the furniture (path, hex,
        # counters, legend) in palette-8, so editing one of them repaints
        # the chrome on the same frame as everything else
        amber, teal, grey = "#e0c06c", "#6cc0c0", "#d0d0d8"
        base = dict(FULL_SLOTS, **{"palette-11": amber, "palette-14": teal,
                                   "palette-8": grey})

        def row(**slots):
            body = lines(frame(100, 30, slots=dict(base, **slots)))
            return next(l for l in body if needle in ANSI.sub("", l))

        needle = "arrows"
        hints = row()
        self.assertIn(fg(amber), hints)            # the key
        self.assertIn(fg(teal), hints)             # its label
        self.assertNotEqual(hints, row(**{"palette-11": "#ff00ff"}))

        needle = "0-7 base"
        legend = row()
        self.assertIn(fg(grey), legend)
        self.assertNotEqual(legend, row(**{"palette-8": "#ff00ff"}))

        # a header, by contrast, wears the theme's own foreground and
        # nothing else — moving a palette slot must leave it alone.
        # The title row is matched exactly: the side-by-side top block
        # (decision 36) puts a `palette-N` readout above it, and a
        # substring search would stop on that row instead of the title.
        def title_row(**slots):
            found = lines(frame(100, 30, slots=dict(base, **slots)))
            return next(line for line in found
                        if ANSI.sub("", line).strip() == "palette")
        head = title_row()
        self.assertIn(f"{BOLD}{fg(base['foreground'])}palette{RESET}", head)
        for slot in ("palette-11", "palette-14", "palette-8"):
            self.assertEqual(head, title_row(**{slot: "#ff00ff"}))

        # the wordmark is the one ornament, and it is live: one letter, one
        # colour, and moving a slot that spells it moves the frame
        needle = "huebox"
        mark = row()
        for slot in ("palette-9", "palette-10", "palette-11", "palette-12",
                     "palette-13", "palette-14"):
            self.assertIn(fg(base[slot]), mark, slot)
        self.assertNotEqual(mark, row(**{"palette-9": "#ff00ff"}))

    def test_the_header_walks_the_ladder_out_of_leftover(self):
        # decision 33 — the header draws out of the rows below the frame,
        # never out of a widget: the four sizes, and tall sizes whose
        # diff is whole, keep the wordmark; a taller terminal stands the
        # mini banner up; a tall one the raster banner — and the diff is
        # whole throughout, so no rung ever costs a widget a row.
        # Decision 36's side-by-side top saves the frame a row, so each
        # rung stands up one row earlier than it used to (mini at 41, the
        # raster one at 44): taller leftover, honestly spent.
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12),
                           (120, 40)):
            with self.subTest(size=(cols, rows)):
                self.assertIn("huebox", plain_rows(frame(cols, rows))[0])
        for cols, rows in ((120, 41), (120, 43)):
            with self.subTest(size=(cols, rows)):
                body = plain_rows(frame(cols, rows))
                self.assertNotIn("huebox", body[0])
                # the mini rung's own glyph: the raster banner spends
                # `█` and the bevel, never half-blocks
                self.assertIn("▀", "\n".join(body[:3]))
                self.assertIn("live diff", "\n".join(body))
                # forced off, the wordmark stands back in
                self.assertIn("huebox",
                                plain_rows(frame(cols, rows,
                                                 banner=False))[0])
        for cols, rows in ((120, 44), (120, 55), (100, 60)):
            with self.subTest(size=(cols, rows)):
                body = plain_rows(frame(cols, rows))
                self.assertNotIn("huebox", body[0])
                self.assertIn("live diff", "\n".join(body))

    def test_the_banner_leaves_everything_below_where_it_was(self):
        # the banner is an insertion above the frame, not a reallocation:
        # every row below the header is the wordmark frame's, shifted down
        rows = len(editor.banner_lines(FULL_SLOTS, 120)) + 2
        auto = lines(frame(120, 55))
        forced = lines(frame(120, 55, banner=False))
        self.assertEqual(auto[rows:], forced[2:])

    def test_the_banner_shifts_the_hit_map_with_the_frame(self):
        # every hit moves down by the banner's rows and still lands on
        # the `>` marker the frame paints for its slot — a click must aim
        # where the swatch went, not where it was
        shift = len(editor.banner_lines(FULL_SLOTS, 120))
        plain = {hit.slot: hit
                 for hit in editor.frame_hits(120, 55, use_banner=False)}
        raised = editor.frame_hits(120, 55)
        self.assertEqual(len(plain), len(raised))
        for hit in raised:
            with self.subTest(slot=hit.slot):
                twin = plain[hit.slot]
                self.assertEqual((hit.x0, hit.x1), (twin.x0, twin.x1))
                self.assertEqual(hit.y, twin.y + shift)
                marked = _rows(120, 55, hit.slot)
                self.assertEqual(marked[hit.y][hit.x0 + 1:hit.x0 + 2], ">")

    def test_the_mini_banner_leaves_everything_below_where_it_was(self):
        # like the raster banner: an insertion above the frame at 120x43,
        # not a reallocation — every row below the header is the wordmark
        # frame's, shifted down by the mini banner's three rows. (43, not
        # 44: decision 36's saved row stands the raster banner up at 44.)
        rows = len(editor.mini_banner_lines(FULL_SLOTS, 120)) + 2
        auto = lines(frame(120, 43))
        forced = lines(frame(120, 43, banner=False))
        self.assertEqual(auto[rows:], forced[2:])

    def test_the_mini_banner_shifts_the_hit_map_with_the_frame(self):
        # every hit moves down by the mini banner's rows and still lands
        # on the `>` marker the frame paints for its slot
        shift = len(editor.mini_banner_lines(FULL_SLOTS, 120))
        plain = {hit.slot: hit
                 for hit in editor.frame_hits(120, 43, use_banner=False)}
        raised = editor.frame_hits(120, 43)
        self.assertEqual(len(plain), len(raised))
        for hit in raised:
            with self.subTest(slot=hit.slot):
                twin = plain[hit.slot]
                self.assertEqual((hit.x0, hit.x1), (twin.x0, twin.x1))
                self.assertEqual(hit.y, twin.y + shift)
                marked = _rows(120, 43, hit.slot)
                self.assertEqual(marked[hit.y][hit.x0 + 1:hit.x0 + 2], ">")

    def test_a_fold_never_splits_a_key_from_its_label(self):
        # the hint row is painted a token at a time; the fold lands between
        # whole hints, so no row ends with a key looking orphaned
        whole = {"arrows move", "w/e hue", "s/d sat", "x/c light", "f x5",
                 "i hex", "^S save", "u undo(1)", "r revert", "t themes",
                 "N as new", "Esc quit"}
        for cols in (120, 100, 80, 60, 40):
            with self.subTest(cols=cols):
                rows = [ANSI.sub("", line).strip()
                        for line in lines(frame(cols, 24, mult=5,
                                                 undo=[("#fff", 0)]))]
                hints = [row for row in rows if row
                         and all(part in whole for part in row.split("  "))]
                self.assertTrue(hints)
                for row in hints:
                    self.assertLessEqual(width(row) + 2, cols)

    def test_a_tall_frame_spends_its_spare_rows_on_the_diff(self):
        # the other end of the same ladder: decoration stays, and what the
        # sample did not need becomes the hunk
        tall = [line.strip() for line in plain_rows(frame(120, 40))]
        self.assertIn(editor.PALETTE_LEGEND.strip(), tall)
        self.assertTrue(any("live diff" in line for line in tall))

    def test_no_size_overflows_its_rows(self):
        for cols, rows in ((120, 44), (100, 40), (100, 30), (80, 30), (80, 28),
                           (80, 24), (80, 22), (80, 20), (80, 18), (80, 16),
                           (80, 14), (60, 24), (60, 20), (60, 16), (60, 14)):
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertLessEqual(len(body), rows)
                self.assertTrue(all(width(line) <= cols for line in body))


class EditLoop(unittest.TestCase):
    """The loop itself: draw / read / apply, and only save writes (§14.2)."""

    def run_session(self, keys, path=None, slots=None, cols=None):
        """Drive `edit()` with a scripted key stream; return the writes."""
        writes, draws, handlers = [], [], []

        def record(name, target, values):
            # §13.7 — the writer is handed the subject every save, because
            # the picker can move it mid-session; a legacy session has none
            writes.append((name, target, dict(values)))

        def note_handler(*args, **kwargs):
            draws.append({"slots": dict(args[2]), "status": args[5],
                          "head": kwargs.get("head"),
                          "overlay": kwargs.get("overlay"),
                          "grid": kwargs.get("grid")})
            handlers.append(signal.getsignal(signal.SIGWINCH))

        stream = iter(keys)
        target = path or os.path.join(self.tmp.name, "kitty.conf")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("# colours\n")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            session.drive(stream, "kitty", target, dict(slots or FULL_SLOTS),
                          record, size=((cols or 80), 24), draw=note_handler)
        return writes, draws, handlers, target

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_resize_redraws_without_consuming_a_key_or_writing(self):
        # A resize still redraws and still eats no key — the guarantee §15.1
        # describes. The SIGWINCH handler half of this test went with
        # `_on_winch`: Textual delivers a `Resize` event and owns the signal, so
        # asserting huebox installed a handler would assert something the
        # migration deliberately removed.
        writes, draws, _handlers, _ = self.run_session(["resize", "f", "esc"])
        self.assertEqual(writes, [])        # §14.2: nothing but Ctrl+S writes
        self.assertEqual(len(draws), 3)      # the resize redrew, ate no key
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp.name, "kitty.conf.huebox.bak")))

    def test_the_loop_draws_from_the_buffer_and_saves_once(self):
        writes, draws, _, path = self.run_session(["w", SAVE, "esc"])
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:2], (None, path))   # direct mode: no theme
        # the frame drawn after "w" already carried the adjusted value,
        # before any save happened (palette-0 is the selected slot)
        self.assertNotEqual(draws[0]["slots"]["palette-0"],
                            draws[1]["slots"]["palette-0"])
        self.assertEqual(draws[1]["slots"]["palette-0"],
                         writes[0][2]["palette-0"])

    def test_the_head_names_the_config_in_a_direct_session(self):
        _, draws, _, path = self.run_session(["esc"])
        # §13.7 — `direct:<path>` says a save writes the config itself,
        # and the dim path is dropped so it is not printed twice
        self.assertEqual(draws[0]["head"], f"direct:{path}")
        self.assertIsNone(draws[0]["overlay"])

    def test_a_dirty_quit_takes_two_escapes(self):
        writes, draws, _, _ = self.run_session(["w", "esc", "esc"])
        self.assertEqual(writes, [])        # discarded, not saved
        self.assertIn("unsaved changes", draws[-1]["status"])
        self.assertEqual(len(draws), 3)      # armed frame is drawn once

    def test_the_frame_and_the_keys_are_given_one_geometry(self):
        # §4.3 / §15.2 — the grid the loop computes per frame is the one the
        # frame draws and the arrows walk, so the two cannot disagree
        for cols in (80, 60, 40):
            writes, draws, _, _ = self.run_session(["esc"], cols=cols)
            self.assertEqual(writes, [])
            self.assertTrue(draws)
            for draw in draws:
                self.assertEqual(draw["grid"], editor.grid_geometry(cols))

    def test_the_keys_follow_the_terminal_the_frame_is_drawn_for(self):
        # 40 columns puts four swatches to a row, so one arrow down is
        # palette-4 — not the palette-8 a fixed eight-slot stride would pick
        writes, _, _, _ = self.run_session(["down", "s", SAVE, "esc"], cols=40)
        self.assertEqual(len(writes), 1)
        self.assertNotEqual(writes[0][2]["palette-4"], FULL_SLOTS["palette-4"])
        self.assertEqual(writes[0][2]["palette-8"], FULL_SLOTS["palette-8"])


class ApplyKey(unittest.TestCase):
    """`apply_key` on its own: no terminal, no loop, just state (§14.2)."""

    def state(self, prompt=None, backup=None, slots=None, sel=0):
        writes = []
        st = editor.EditorState(slots or dict(FULL_SLOTS),
                                lambda values: writes.append(dict(values)),
                                prompt, backup)
        st.sel = sel
        return st, writes

    def background_state(self, **kwargs):
        """A state with the `background` slot selected, so nudges show."""
        return self.state(sel=SLOTS.index("background"), **kwargs)

    def dirty_and_adjust(self, st, key="w"):
        name = SLOTS[st.sel]
        start = st.slots[name]
        editor.apply_key(key, st)
        self.assertNotEqual(st.slots[name], start)
        return start

    def test_adjust_marks_dirty_and_writes_nothing(self):
        st, writes = self.background_state()
        self.dirty_and_adjust(st)
        self.assertTrue(st.dirty())
        self.assertFalse(st.written)
        self.assertEqual(writes, [])

    def test_save_writes_once_and_cleans_the_buffer(self):
        st, writes = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key(SAVE, st)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0], dict(st.slots))
        self.assertFalse(st.dirty())
        self.assertTrue(st.written)
        self.assertEqual(st.status, "saved")
        self.assertEqual(st.saved, dict(st.slots))

    def test_every_buffer_edit_is_written_only_on_save(self):
        st, writes = self.background_state()
        for key in ("e", "e", "d", "c"):
            editor.apply_key(key, st)
        self.assertEqual(writes, [])
        editor.apply_key(SAVE, st)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["background"], st.slots["background"])

    def test_dirty_esc_arms_and_armed_esc_quits(self):
        st, _ = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key("esc", st)
        self.assertTrue(st.armed)
        self.assertFalse(st.quit)
        self.assertIn("discard", st.status)
        editor.apply_key("esc", st)
        self.assertTrue(st.quit)

    def test_another_key_disarms(self):
        st, _ = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key("esc", st)
        editor.apply_key("f", st)
        self.assertFalse(st.armed)
        self.assertEqual(st.status, "step size x5")
        editor.apply_key("esc", st)         # still dirty: it arms again
        self.assertTrue(st.armed)
        self.assertFalse(st.quit)

    def test_a_clean_esc_quits_at_once(self):
        st, _ = self.state()
        editor.apply_key("esc", st)
        self.assertTrue(st.quit)
        self.assertFalse(st.armed)
        self.assertEqual(st.status, "")

    def test_ctrl_c_takes_the_esc_path(self):
        st, _ = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key("\x03", st)
        self.assertTrue(st.armed)
        self.assertFalse(st.quit)
        editor.apply_key("\x03", st)
        self.assertTrue(st.quit)
        clean, _ = self.state()
        editor.apply_key("\x03", clean)
        self.assertTrue(clean.quit)

    def test_revert_goes_to_the_last_save_not_session_start(self):
        st, writes = self.background_state()
        start = dict(st.slots)
        self.dirty_and_adjust(st)           # dirty
        editor.apply_key(SAVE, st)          # checkpoint
        checkpoint = dict(st.slots)
        self.assertNotEqual(checkpoint, start)
        self.dirty_and_adjust(st)           # dirty again, past the save
        editor.apply_key("r", st)
        self.assertEqual(st.slots, checkpoint)
        self.assertEqual(st.saved, checkpoint)
        self.assertNotEqual(st.slots, start)
        self.assertFalse(st.dirty())
        self.assertEqual(st.undo, [])
        self.assertEqual(st.status, "reverted to last save")
        self.assertEqual(len(writes), 1)

    def test_revert_before_any_save_returns_to_the_start(self):
        st, writes = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key("r", st)
        self.assertEqual(st.slots, FULL_SLOTS)
        self.assertEqual(st.status, "reverted to start")
        self.assertEqual(writes, [])

    def test_undo_survives_a_save(self):
        st, writes = self.background_state()
        start = self.dirty_and_adjust(st)
        middle = st.slots["background"]
        self.dirty_and_adjust(st)
        editor.apply_key(SAVE, st)          # saving does not clear the log
        self.assertEqual(len(st.undo), 2)
        editor.apply_key("u", st)
        self.assertEqual(st.slots["background"], middle)
        editor.apply_key("u", st)
        self.assertEqual(st.slots["background"], start)
        self.assertEqual(st.undo, [])
        self.assertEqual(len(writes), 1)

    def test_hex_entry_goes_through_the_prompt_callback(self):
        asked = []

        def prompt(name):
            asked.append(name)
            return "#AbCdEf"

        st, writes = self.background_state(prompt=prompt)
        self.dirty_and_adjust(st)
        editor.apply_key("i", st)
        # the prompt carries the slot, not just a bare name (P5 review)
        self.assertEqual(asked, ["  new hex for background: "])
        self.assertEqual(st.slots["background"], "#abcdef")
        self.assertEqual(len(st.undo), 2)   # the nudge plus the typed hex
        self.assertEqual(st.status, "background = #abcdef")
        editor.apply_key("u", st)
        self.assertEqual(st.status, "undid background")
        self.assertEqual(writes, [])
        editor.apply_key("X", st)
        self.assertEqual(asked[-1], "  new hex for background: ")

    def test_hex_entry_rejects_junk_and_cancellation(self):
        st, _ = self.background_state(prompt=lambda name: "not a colour")
        self.dirty_and_adjust(st)
        before = dict(st.slots)
        editor.apply_key("i", st)
        self.assertEqual(st.slots, before)
        self.assertIn("valid", st.status)

        cancelled, _ = self.state(prompt=lambda name: None)
        editor.apply_key("i", cancelled)
        self.assertEqual(cancelled.status, "")
        self.assertFalse(cancelled.dirty())

    def test_without_a_prompt_callback_hex_entry_is_a_no_op(self):
        st, _ = self.background_state()
        self.dirty_and_adjust(st)
        before = dict(st.slots)
        editor.apply_key("i", st)
        self.assertEqual(st.slots, before)

    def test_movement_and_step_size_stay_clean(self):
        st, writes = self.state()
        editor.apply_key("down", st)
        self.assertEqual(SLOTS[st.sel], "palette-8")
        editor.apply_key("up", st)
        self.assertEqual(st.sel, 0)
        editor.apply_key("right", st)
        self.assertEqual(st.sel, 1)
        editor.apply_key("left", st)
        editor.apply_key("f", st)
        self.assertEqual(st.mult, 5)
        self.assertFalse(st.dirty())
        self.assertEqual(writes, [])

    def test_an_absent_slot_is_not_adjustable(self):
        partial = {"palette-0": "#123456"}
        st, writes = self.state(slots=partial)
        editor.apply_key("right", st)       # palette-1: no value in this file
        editor.apply_key("w", st)
        editor.apply_key("i", st)
        self.assertEqual(st.slots, partial)
        editor.apply_key(SAVE, st)
        self.assertEqual(writes, [partial])
        self.assertFalse(st.dirty())
        editor.apply_key("esc", st)
        self.assertTrue(st.quit)            # Esc still quits


class GridArrows(unittest.TestCase):
    """The arrows walk the grid the frame draws, row for row (§4.3).

    The old movement was a fixed slot stride, which only ever matched the
    80-column frame: the six interface cells are drawn as three rows of two
    and were stepped through as a single list, so left/right read as "next
    in the list" and down fell off the end of it.
    """

    def land(self, name, *keys, cols=80):
        """The slot `keys` reach from `name`, in the grid at `cols`."""
        index = SLOTS.index(name)
        grid = editor.grid_geometry(cols)
        for key in keys:
            index = editor.move_slot(index, key, grid)
        return SLOTS[index]

    def swatch_rows(self, cols, rows=16):
        """How many swatches the frame actually put on each palette row."""
        grid = editor.grid_geometry(cols)
        # a swatch row is nothing but cells — labels, hexes, spaces. The
        # legend, the headers and the blank rows of §8.2 are not, and each
        # swatch cell paints one foreground of its own, so the paint counts
        # cells and the text tells the row where the grid ends
        cells = r"[ #>\da-f]+" if grid.show_hex else r"[ #>\d]+"
        raw = lines(frame(cols, rows, sel=0))
        start = next(i for i, line in enumerate(raw)
                     if plain(line).strip() == "palette")
        out = []
        for line in raw[start + 1:]:
            count = line.count("38;2;")
            if not count or not re.fullmatch(cells, plain(line).strip()):
                break
            out.append(count)
        return out

    def marked(self, name, cols, rows=16):
        """A frame with `name` selected, as plain text."""
        return "".join(ANSI.sub("", line) for line in lines(
            frame(cols, rows, sel=SLOTS.index(name))))

    def mark(self, name):
        """How the frame prints `>` on a palette swatch (`> 8 `, `>12 `)."""
        return f">{int(name.split('-')[-1]):>2} "

    def test_the_interface_grid_is_three_rows_of_two(self):
        self.assertEqual((editor.grid_geometry(80).named_cols, len(NAMED)),
                         (2, 6))
        # right crosses the pair; down walks the column it is in
        self.assertEqual(self.land("background", "right"), "foreground")
        self.assertEqual(self.land("foreground", "left"), "background")
        self.assertEqual(self.land("background", "down"), "cursor-color")
        self.assertEqual(self.land("background", "down", "down"),
                         "selection-background")
        self.assertEqual(self.land("foreground", "down"), "cursor-text")
        self.assertEqual(self.land("cursor-text", "down"),
                         "selection-foreground")
        # down and right from one cell meet at the same place as the other
        self.assertEqual(self.land("background", "down", "right"),
                         self.land("background", "right", "down"))

    def test_left_and_right_stay_in_their_row(self):
        self.assertEqual(self.land("cursor-text", "left"), "cursor-color")
        # a row's edge is the frame's edge — no wrap into the next row
        self.assertEqual(self.land("foreground", "right"), "foreground")
        self.assertEqual(self.land("cursor-color", "left"), "cursor-color")

    def test_down_crosses_from_the_palette_into_the_interface(self):
        # the palette's bottom row sits directly above the interface's first
        self.assertEqual(self.land("palette-8", "down"), "background")
        self.assertEqual(self.land("palette-9", "down"), "foreground")

    def test_up_crosses_back_out_of_the_interface(self):
        self.assertEqual(self.land("background", "up"), "palette-8")
        self.assertEqual(self.land("foreground", "up"), "palette-9")
        self.assertEqual(self.land("cursor-color", "up"), "background")

    def test_the_outer_ends_of_the_frame_stay_put(self):
        self.assertEqual(self.land("palette-0", "up"), "palette-0")
        self.assertEqual(self.land("palette-0", "left"), "palette-0")
        self.assertEqual(self.land("selection-foreground", "down"),
                         "selection-foreground")
        self.assertEqual(self.land("selection-foreground", "right"),
                         "selection-foreground")

    def test_a_narrow_palette_row_is_one_key_not_two(self):
        # 40 columns puts four swatches to a row: down is the cell below
        # the selection, not the cell two rows down (and the interface
        # holds its two abbreviated columns there — §15.2)
        grid = editor.grid_geometry(40)
        self.assertEqual((grid.palette_cols, grid.named_cols), (4, 2))
        self.assertEqual(self.land("palette-0", "down", cols=40), "palette-4")
        self.assertEqual(self.land("palette-3", "down", cols=40), "palette-7")
        self.assertEqual(self.land("palette-12", "down", cols=40), "background")
        self.assertEqual(self.land("background", "up", cols=40), "palette-12")

    def test_a_narrow_interface_holds_two_abbreviated_columns(self):
        # two full interface cells need 68 columns; below that the frame
        # holds two abreast by printing the abbreviations (§15.2) — the
        # columns stay two, only the names narrow
        self.assertEqual(editor.grid_geometry(80).named_cols, 2)
        self.assertEqual(editor.grid_geometry(60).named_cols, 2)
        grid = editor.grid_geometry(60)
        self.assertTrue(grid.named_abbrev)
        self.assertTrue(grid.named_show_hex)
        self.assertEqual(self.land("background", "right", cols=60),
                         "foreground")
        self.assertEqual(self.land("foreground", "left", cols=60),
                         "background")
        self.assertEqual(self.land("background", "down", cols=60),
                         "cursor-color")
        self.assertEqual(self.land("cursor-text", "down", cols=60),
                         "selection-foreground")
        self.assertEqual(self.land("foreground", "right", cols=60),
                         "foreground")      # a row's edge is the edge

    def test_the_interface_ladder_sheds_names_before_columns(self):
        # like the palette sheds its hex before its cells: full names
        # two abreast where 68 columns allow, abbreviations below that,
        # one to a row past 30, and the hex goes last — past what any
        # frame at or above MIN_COLS reaches
        wide = editor.grid_geometry(80)
        self.assertEqual((wide.named_cols, wide.named_abbrev,
                          wide.named_show_hex), (2, False, True))
        for cols in (60, 40):
            with self.subTest(cols=cols):
                narrow = editor.grid_geometry(cols)
                self.assertEqual((narrow.named_cols, narrow.named_abbrev,
                                  narrow.named_show_hex), (2, True, True))
        single = editor.grid_geometry(20)
        self.assertEqual((single.named_cols, single.named_abbrev,
                          single.named_show_hex), (1, True, True))
        floor = editor.grid_geometry(10)
        self.assertEqual((floor.named_cols, floor.named_abbrev,
                          floor.named_show_hex), (1, True, False))

    def test_abbreviated_interface_cells_keep_mark_and_hex(self):
        slots = dict(FULL_SLOTS)
        value = FULL_SLOTS["background"]
        full = ANSI.sub("", editor.named_cell(slots, "background", 0))
        self.assertEqual(full, "  %-21s %s " % ("background", value))
        short = ANSI.sub("", editor.named_cell(slots, "background", 0,
                                                 abbrev=True))
        self.assertEqual(short, "  BG %s " % value)
        bare = ANSI.sub("", editor.named_cell(slots, "background", 0,
                                                abbrev=True,
                                                show_hex=False))
        self.assertEqual(bare, "  BG")
        # the selected cell keeps its mark in every shape
        sel = SLOTS.index("background")
        for abbrev, show_hex in ((False, True), (True, True),
                                 (True, False)):
            marked = ANSI.sub("", editor.named_cell(
                slots, "background", sel, abbrev, show_hex))
            self.assertEqual(marked[1], ">", marked)
        self.assertEqual((editor.named_cell_width(False, True),
                          editor.named_cell_width(True, True),
                          editor.named_cell_width(True, False)),
                         (32, 13, 4))

    def test_narrow_interface_hits_are_the_abbreviated_cell_wide(self):
        # two abbreviated cells abreast: the hits are 13 wide, joined by
        # two — the same rule as the full cells, at the narrow width
        for cols in (60, 40):
            with self.subTest(cols=cols):
                hits = {hit.slot: hit
                        for hit in editor.frame_hits(cols, 30)}
                first, second = hits[16], hits[17]
                self.assertEqual(first.x1 - first.x0 + 1, 13)
                self.assertEqual(second.x0, first.x0 + 15)
                self.assertEqual(first.y, second.y)

    def test_the_frame_and_the_keys_agree_on_the_grid(self):
        for cols in (80, 60, 40):
            grid = editor.grid_geometry(cols)
            self.assertEqual(set(self.swatch_rows(cols)),
                             {grid.palette_cols}, cols)
            # the `>` the frame draws lands on the slot the arrows reach
            above = self.land("background", "up", cols=cols)
            self.assertNotEqual(above, "palette-0")
            self.assertNotIn(self.mark(above), self.marked("palette-0", cols))
            self.assertIn(self.mark(above), self.marked(above, cols))

    def test_the_key_surface_moves_by_the_frame_geometry(self):
        # apply_key, not move_slot: the arrows as the loop actually feeds them
        st = editor.EditorState(dict(FULL_SLOTS), lambda values: None)
        st.grid = editor.grid_geometry(80)
        st.sel = SLOTS.index("background")
        for key, expected in (("down", "cursor-color"),
                              ("down", "selection-background"),
                              ("right", "selection-foreground"),
                              ("up", "cursor-text"),
                              ("up", "foreground")):
            editor.apply_key(key, st)
            self.assertEqual(SLOTS[st.sel], expected)
        self.assertFalse(st.dirty())        # navigation writes nothing


class VerticalArrows(unittest.TestCase):
    """The arrows walk the side layout's pairs, column for column (§4.3).

    The left panel draws `(0, 8)` down to `(7, 15)`, then the named slots
    two-up — so left/right change the column and up/down the row, the
    transpose of the stacked frame. Crossings stay in the column: `7`
    drops to `background`, `foreground` climbs back to `15`.
    """

    def land(self, name, *keys):
        index = SLOTS.index(name)
        grid = editor.side_grid()
        for key in keys:
            index = editor.move_slot(index, key, grid)
        return SLOTS[index]

    def test_left_and_right_change_the_column(self):
        self.assertEqual(self.land("palette-0", "right"), "palette-8")
        self.assertEqual(self.land("palette-8", "left"), "palette-0")
        self.assertEqual(self.land("palette-7", "right"), "palette-15")
        # a column's edge is the panel's edge — no wrap, no stay-across
        self.assertEqual(self.land("palette-0", "left"), "palette-0")
        self.assertEqual(self.land("palette-8", "right"), "palette-8")
        self.assertEqual(self.land("background", "right"), "foreground")
        self.assertEqual(self.land("foreground", "left"), "background")
        self.assertEqual(self.land("foreground", "right"), "foreground")

    def test_up_and_down_walk_the_column(self):
        self.assertEqual(self.land("palette-0", "down"), "palette-1")
        self.assertEqual(self.land("palette-1", "up"), "palette-0")
        self.assertEqual(self.land("palette-8", "down"), "palette-9")
        self.assertEqual(self.land("background", "down"), "cursor-color")
        self.assertEqual(self.land("background", "down", "down"),
                         "selection-background")

    def test_down_crosses_into_the_interface_in_the_same_column(self):
        self.assertEqual(self.land("palette-7", "down"), "background")
        self.assertEqual(self.land("palette-15", "down"), "foreground")

    def test_up_crosses_back_out_of_the_interface(self):
        self.assertEqual(self.land("background", "up"), "palette-7")
        self.assertEqual(self.land("foreground", "up"), "palette-15")
        self.assertEqual(self.land("cursor-color", "up"), "background")

    def test_the_outer_ends_stay_put(self):
        self.assertEqual(self.land("palette-0", "up"), "palette-0")
        self.assertEqual(self.land("palette-8", "up"), "palette-8")
        self.assertEqual(self.land("selection-background", "down"),
                         "selection-background")
        self.assertEqual(self.land("selection-foreground", "down"),
                         "selection-foreground")
        self.assertEqual(self.land("selection-foreground", "right"),
                         "selection-foreground")

    def test_the_bare_grid_is_untouched(self):
        # `vertical` defaults off: every existing GridArrows walk reads the
        # same grid it always did.
        grid = editor.grid_geometry(80)
        self.assertFalse(grid.vertical)
        self.assertEqual(editor.move_slot(0, "right", grid), 1)

    def test_the_key_surface_walks_pairs(self):
        st = editor.EditorState(dict(FULL_SLOTS), lambda values: None)
        st.grid = editor.side_grid()
        st.sel = 0
        for key, expected in (("right", 8), ("down", 9), ("left", 1),
                              ("down", 2)):
            editor.apply_key(key, st)
            self.assertEqual(st.sel, expected)
        self.assertFalse(st.dirty())


class SidePanels(unittest.TestCase):
    """The side-by-side content rows: pairs left, live blocks right.

    Pure editor tests for what `app.py` mounts: the left panel's shape and
    pairs, the right panel's budget, and the one rule the whole layout
    rests on — a side cell says exactly what the bare frame's cell says.
    """

    def test_the_left_panel_is_two_columns_of_pairs(self):
        rows = editor.side_left_rows(dict(FULL_SLOTS), 0)
        self.assertEqual(len(rows), editor.SIDE_LEFT_ROWS)
        text = [ANSI.sub("", row) for row in rows]
        self.assertEqual(text[1].split(), [">", "0", "#3f7a3f",
                                            "8", "#3f7a3f"])
        self.assertEqual(text[8].split(), ["7", "#3f7a3f",
                                            "15", "#3f7a3f"])
        # then the interface, paired the same way but name over hex:
        # two rows per pair, the hex row carrying no name
        self.assertEqual(text[10].strip(), "interface")
        self.assertIn("background", text[11])
        self.assertIn("foreground", text[11])
        self.assertNotIn("background", text[12])
        self.assertEqual(text[12].count("#3f7a3f"), 2)
        # The hex starts where its name starts: same column, row below.
        for name in ("background", "foreground"):
            at = text[11].index(name)
            self.assertEqual(text[12][at:at + 7], "#3f7a3f")
        self.assertIn("selection-background", text[15])
        self.assertIn("selection-foreground", text[15])
        for row in rows:
            self.assertLessEqual(visible(row), editor.SIDE_LEFT_W)

    def test_a_side_cell_matches_the_bare_frame_s_cell(self):
        # One implementation of a swatch: the side panel asks `swatch_cell`
        # and `named_cell`, and so does `draw_editor` now. A wide bare
        # frame draws hex swatches eight across; every side pair must read
        # among them, escape codes and all.
        slots = dict(FULL_SLOTS)
        side = editor.side_left_rows(slots, 3)
        bare = frame(120, 30, sel=3, slots=slots, banner=False)
        for index in (0, 3, 8, 15):
            cell = editor.swatch_cell(slots, index, 3, True)
            self.assertIn(cell, bare,
                          "side swatch %d is not the bare one" % index)
            # The side panel pads the same swatch to the interface column
            # width, so the bare cell meets the side row without its reset.
            self.assertTrue(any(cell[:-len(RESET)] in row for row in side))
        for key in ("background", "selection-foreground"):
            cell = editor.named_cell(slots, key, 3)
            self.assertIn(cell, bare)

    def test_the_side_hits_cover_every_slot_once(self):
        found = []
        editor.side_left_rows(dict(FULL_SLOTS), 0, hits=found, y0=0)
        # Palette cells answer once; two-row interface cells answer twice —
        # once per row — and both rows carry the slot.
        once = sorted(hit.slot for hit in found if hit.slot < 16)
        self.assertEqual(once, list(range(16)))
        twice = sorted(hit.slot for hit in found if hit.slot >= 16)
        self.assertEqual(twice, [16, 16, 17, 17, 18, 18,
                                 19, 19, 20, 20, 21, 21])
        pairs = {(hit.y, hit.slot) for hit in found if hit.slot < 16}
        # same row, eight apart: (0, 8) down to (7, 15)
        for row in range(8):
            slots = sorted(slot for y, slot in pairs if y == row + 1)
            self.assertEqual(slots, [row, row + 8])
        # the two rows of one interface cell share the slot
        for slot in range(16, 22):
            rows = sorted(hit.y for hit in found if hit.slot == slot)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1] - rows[0], 1)

    def test_palette_and_interface_share_their_columns(self):
        # One pair of columns for the whole panel: the palette's swatches
        # are padded to the interface cell width, so the second column
        # starts at the same cell in every row — paint and hits alike.
        found = []
        editor.side_left_rows(dict(FULL_SLOTS), 0, hits=found, y0=0)
        left = {hit.slot for hit in found
                if hit.x0 == 2 and hit.slot in (0, 16)}
        self.assertEqual(left, {0, 16})
        for hit in found:
            column = 0 if hit.slot in (0, 1, 2, 3, 4, 5, 6, 7, 16, 18, 20) \
                else 1
            self.assertEqual(hit.x0, 2 + column * (editor.SIDE_NAMED_W + 2))
            self.assertEqual(hit.x1, hit.x0 + editor.SIDE_NAMED_W - 1)

    def test_the_right_panel_spends_a_fixed_budget(self):
        slots = dict(FULL_SLOTS)
        rows = editor.side_live_rows(slots, 48, 14)
        self.assertEqual(len(rows), 14)
        text = "\n".join(ANSI.sub("", row) for row in rows)
        self.assertIn("examples", text)
        self.assertIn("live diff", text)
        self.assertIn("live code", text)
        short = editor.side_live_rows(slots, 48, 6)
        self.assertEqual(len(short), 6)
        self.assertNotIn("live diff",
                         "\n".join(ANSI.sub("", row) for row in short))
        self.assertIsNone(editor.side_live_rows(slots, 48, 5))
        for row in rows:
            self.assertLessEqual(visible(row), 48)

    def test_both_panels_stand_on_the_buffer(self):
        # §8.2, both columns: every row backed out to its panel's width,
        # so no column shows the terminal's background.
        from huebox.render import backdrop as _backdrop  # noqa: F401
        slots = dict(FULL_SLOTS)
        for row in editor.side_left_rows(slots, 0):
            self.assertEqual(visible(row), editor.SIDE_LEFT_W)
        for row in editor.side_live_rows(slots, 48, 14):
            self.assertEqual(visible(row), 48)


class Backup(unittest.TestCase):
    """`<path>.huebox.bak` moves from editor-open to first-save (§14.2)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "kitty.conf")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("background #101014\n")
        self.backup = self.path + ".huebox.bak"

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_first_save_snapshots_the_pre_save_file(self):
        st, writes = self.state()
        editor.apply_key(SAVE, st)
        self.assertEqual(len(writes), 1)
        self.assertTrue(os.path.exists(self.backup))
        self.assertEqual(self.read(self.backup), "background #101014\n")
        self.assertTrue(st.backup_made)

    def test_the_backup_is_never_overwritten(self):
        st, _ = self.state()
        editor.apply_key(SAVE, st)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("background #000000\n")     # something else rewrote it
        editor.apply_key("w", st)
        editor.apply_key(SAVE, st)
        self.assertEqual(self.read(self.backup), "background #101014\n")

    def test_edits_alone_never_create_a_backup(self):
        st, _ = self.state()
        for key in ("e", "d", "c", "w"):
            editor.apply_key(key, st)
        editor.apply_key("esc", st)
        editor.apply_key("esc", st)
        self.assertFalse(os.path.exists(self.backup))

    def test_a_session_that_never_saves_leaves_no_backup(self):
        st, _ = self.state()
        editor.apply_key("esc", st)
        self.assertFalse(os.path.exists(self.backup))
        self.assertFalse(st.written)

    def test_theme_files_get_no_backup(self):
        writes = []
        theme = editor.EditorState(dict(FULL_SLOTS),
                                   lambda values: writes.append(dict(values)),
                                   None, None)      # huebox owns it: no .bak
        editor.apply_key(SAVE, theme)
        self.assertEqual(len(writes), 1)
        self.assertFalse(os.path.exists(self.backup))
        self.assertFalse(theme.backup_made)

    def state(self):
        writes = []
        return editor.EditorState(dict(FULL_SLOTS),
                                  lambda values: writes.append(dict(values)),
                                  None, self.path), writes


class ThemeSession(unittest.TestCase):
    """Theme mode: same keys, truth file, no `.bak`, push report on
    stderr (§13.2, §13.6)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.theme = os.path.join(self.tmp.name, "ember.toml")
        with open(self.theme, "w", encoding="utf-8") as handle:
            handle.write('[colors]\nbackground = "#000000"\n')

    def session(self, keys, report=None):
        writes = []
        stream = iter(keys)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out),\
                mock.patch.object(sys, "stderr", err):
            session.drive(stream, "ghostty", self.theme, dict(FULL_SLOTS),
                          lambda name, path, values: writes.append(
                              (name, path, dict(values))),
                          backup=False, theme="ember", report=report,
                          size=(100, 30))
        return writes, out.getvalue(), err.getvalue()

    def test_saving_writes_the_truth_file_and_nothing_else(self):
        writes, out, err = self.session(["w", SAVE, "esc"])
        self.assertEqual(len(writes), 1)
        # the writer is told the subject, so a picker switch retargets it
        self.assertEqual(writes[0][:2], ("ember", self.theme))
        self.assertNotEqual(writes[0][2]["palette-0"],
                            FULL_SLOTS["palette-0"])
        # huebox owns the file: no pre-save backup, and no claim about the
        # terminal — the writer's push report says what reached it (§13.6)
        self.assertFalse(os.path.exists(self.theme + ".huebox.bak"))
        self.assertIn("saved theme ember", out)
        self.assertNotIn("reload your terminal", out)
        self.assertNotIn("backup of the pre-save state", out)
        self.assertEqual(err, "")

    def test_the_push_report_is_stderr_not_frame_noise(self):
        # printed after raw mode is over, so a save can say what it pushed
        # without the report scrolling through the editor frame (§13.6)
        report = ["ghostty: pushed to /tmp/config", "reload your terminal"]
        _, out, err = self.session(["w", SAVE, "esc"], report)
        self.assertIn("saved theme ember", out)
        self.assertNotIn("pushed to", out)
        self.assertEqual(err.splitlines(),
                         ["huebox: ghostty: pushed to /tmp/config",
                          "huebox: reload your terminal"])

    def test_a_theme_session_that_saves_nothing_says_so(self):
        _, out, _ = self.session(["esc"])
        self.assertIn("no changes", out)

    def test_the_head_names_the_theme_and_the_dirty_dot(self):
        _, out, _ = self.session(["w", "esc", "esc"])
        # §13.7 — `ember ghostty` while clean, a `●` between them once the
        # buffer differs from the last save
        plain = [ANSI.sub("", frame)
                 for frame in out.split("\033[H\033[2J")]
        self.assertTrue(any("  ember ghostty" in frame for frame in plain))
        self.assertTrue(any(f"  ember {editor.DIRTY_MARK} ghostty" in frame
                            for frame in plain))
        self.assertTrue(any("/tmp" in frame or "ember.toml" in frame
                            for frame in plain))


def theme_slots(seed: int) -> dict:
    """A recognisable slot dict per theme, so a switch cannot hide behind a
    load that returns what was already in the buffer."""
    return dict(FULL_SLOTS, background=f"#{seed:02x}2f4f",
                foreground=f"#{seed:02x}bad0")


class FakeLibrary:
    """The three library calls the picker makes, with no disk behind them.

    `problem` is what `create` complains with, `misses` the names `load`
    cannot open — the two failure paths that have to stay on the status bar
    instead of raising into the draw loop.
    """

    def __init__(self, names=("ash", "ember", "frost"), problem="",
                 misses=()):
        self.names_in = list(names)
        self.problem = problem
        self.misses = set(misses)
        self.calls = []

    def build(self):
        return editor.Library(listing=lambda: list(self.names_in),
                              loader=self.load, creator=self.create)

    def load(self, name):
        self.calls.append(("load", name))
        if name in self.misses:
            return None
        return theme_slots(sum(ord(ch) for ch in name)), f"/themes/{name}.toml"

    def create(self, name, slots, force=False):
        self.calls.append(("create", name, force, dict(slots)))
        if self.problem:
            return "", self.problem
        self.names_in.append(name)
        return f"/themes/{name}.toml", ""


class PickerCase(unittest.TestCase):
    """Shared fixture: a theme session with a library and a scripted prompt."""

    def picker_state(self, names=("ash", "ember", "frost"), theme="ember",
                     prompts=(), problem="", misses=(), writes=None):
        writes = [] if writes is None else writes
        self.labels = []
        self.library = FakeLibrary(names, problem=problem, misses=misses)
        answers = list(prompts)
        st = editor.EditorState(dict(FULL_SLOTS), None,
                                theme=theme, fmt="ghostty",
                                library=self.library.build(),
                                path="/themes/ember.toml")

        def prompt_name(label):
            self.labels.append(label)
            return answers.pop(0) if answers else None

        def save(values):
            writes.append((st.theme, st.path, dict(values)))
            return f"saved {st.theme} → ghostty"

        st.prompt_name = prompt_name
        st.write = save
        self.writes = writes
        return st


class PickerKeys(PickerCase):
    """`t` and what the overlay does with keys (§13.7)."""

    def test_t_opens_the_picker_on_the_session_theme(self):
        st = self.picker_state()
        editor.apply_key("t", st)
        self.assertEqual(st.overlay, ["ash", "ember", "frost"])
        self.assertEqual(st.overlay_index, 1)      # ember, what we are editing
        self.assertEqual(st.status, "")
        self.assertEqual(st.picker_frame(),
                         (["ash", "ember", "frost"], 1, "ember"))

    def test_a_direct_session_opens_on_the_first_row(self):
        st = self.picker_state(theme=None)
        editor.apply_key("t", st)
        self.assertEqual(st.overlay_index, 0)
        self.assertEqual(st.picker_frame()[2], "")   # nothing to mark yet

    def test_arrows_move_the_selection_and_stop_at_the_edges(self):
        st = self.picker_state()
        editor.apply_key("t", st)
        editor.apply_key("up", st)
        self.assertEqual(st.overlay_index, 0)
        editor.apply_key("up", st)
        self.assertEqual(st.overlay_index, 0)
        editor.apply_key("down", st)
        editor.apply_key("down", st)
        editor.apply_key("down", st)
        self.assertEqual(st.overlay_index, 2)
        self.assertEqual(st.sel, 0)                # slot selection is untouched

    def test_enter_opens_the_theme_saves_it_and_resets_the_session(self):
        st = self.picker_state()
        editor.apply_key("down", st)               # a selection to lose
        editor.apply_key("w", st)                  # and an edit to lose
        editor.apply_key("r", st)                  # clean again
        editor.apply_key("t", st)
        editor.apply_key("down", st)
        st.armed = True        # a pending discard must not follow the switch
        editor.apply_key("\r", st)
        self.assertEqual(st.theme, "frost")
        self.assertEqual(st.path, "/themes/frost.toml")
        self.assertEqual(st.slots, theme_slots(sum(ord(c) for c in "frost")))
        self.assertEqual(st.saved, st.slots)       # a fresh theme is clean
        self.assertEqual(st.undo, [])
        self.assertEqual(st.sel, 0)
        self.assertFalse(st.armed)                 # P2 review: no inherited arm
        self.assertIsNone(st.overlay)
        # the switch is the save: the theme is what the terminal gets
        self.assertEqual(self.writes,
                         [("frost", "/themes/frost.toml", st.slots)])
        self.assertEqual(st.status, "saved frost → ghostty")
        self.assertEqual(self.library.calls,
                         [("load", "frost")])

    def test_a_dirty_switch_is_blocked_with_the_exact_status(self):
        st = self.picker_state()
        editor.apply_key("w", st)                  # dirty
        dirty = dict(st.slots)
        editor.apply_key("t", st)
        editor.apply_key("down", st)
        editor.apply_key("\r", st)
        self.assertEqual(st.status, "save (Ctrl+S) or revert (r) first")
        self.assertEqual(st.theme, "ember")        # decision 12
        self.assertEqual(st.slots, dirty)
        self.assertIsNotNone(st.overlay)            # the picker stays up
        self.assertEqual(self.library.calls, [])   # nothing was even loaded
        self.assertEqual(self.writes, [])

    def test_esc_and_t_put_the_editor_back_without_quitting(self):
        for closer in ("esc", "t", "Q", "\x03"):
            with self.subTest(key=closer):
                st = self.picker_state()
                editor.apply_key("w", st)          # dirty: Esc must not quit
                editor.apply_key("t", st)
                editor.apply_key(closer, st)
                self.assertIsNone(st.overlay)
                self.assertFalse(st.quit)
                self.assertFalse(st.armed)
                self.assertEqual(st.status, "")
                self.assertEqual(st.theme, "ember")

    def test_the_picker_swallows_the_rest_of_the_key_surface(self):
        st = self.picker_state()
        before = dict(st.slots)
        editor.apply_key("t", st)
        for key in ("e", "d", "c", "u", "r", "f", SAVE, "i", "N"):
            editor.apply_key(key, st)
        self.assertEqual(st.slots, before)
        self.assertEqual(st.mult, 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(st.status, "")            # the picker answered, not `i`
        self.assertFalse(st.quit)
        self.assertEqual(st.overlay_index, 1)      # only arrows moved it

    def test_a_theme_that_will_not_open_keeps_the_buffer(self):
        st = self.picker_state(misses=("frost",))
        editor.apply_key("t", st)
        editor.apply_key("down", st)
        editor.apply_key("\r", st)
        self.assertEqual(st.status, "frost could not be opened")
        self.assertEqual(st.theme, "ember")
        self.assertEqual(st.slots, FULL_SLOTS)
        self.assertIsNotNone(st.overlay)

    def test_an_empty_library_says_so_and_opens_nothing(self):
        st = self.picker_state(names=())
        editor.apply_key("t", st)
        self.assertIsNone(st.overlay)
        self.assertIn("no themes yet", st.status)

    def test_without_a_library_the_picker_is_a_no_op(self):
        st = editor.EditorState(dict(FULL_SLOTS), lambda values: None)
        editor.apply_key("t", st)
        self.assertIsNone(st.overlay)
        self.assertEqual(st.status, "no theme library in this session")
        editor.apply_key("N", st)
        self.assertEqual(st.status, "no theme library in this session")
        self.assertIsNone(st.theme)


class PickerCreates(PickerCase):
    """`n` in the picker and `N` in the editor (§13.7)."""

    def test_n_creates_a_theme_from_the_buffer_and_stays_up(self):
        st = self.picker_state(prompts=["dusk"])
        editor.apply_key("w", st)                  # dirty: creation is fine
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        name, force, values = self.library.calls[0][1:]
        self.assertEqual((name, force), ("dusk", False))
        self.assertEqual(values, st.slots)         # the buffer, unsaved and all
        self.assertEqual(st.theme, "dusk")         # the session follows it
        self.assertEqual(st.path, "/themes/dusk.toml")
        self.assertEqual(st.overlay[-1], "dusk")   # the new row is selected
        self.assertEqual(st.overlay_index, len(st.overlay) - 1)
        self.assertEqual(st.status, "created dusk - current now")
        self.assertFalse(st.dirty())
        self.assertEqual(self.labels, ["  new theme name: "])

    def test_n_keeps_the_overlay_usable(self):
        st = self.picker_state(prompts=["dusk"])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        editor.apply_key("esc", st)
        self.assertIsNone(st.overlay)
        self.assertFalse(st.quit)

    def test_a_taken_name_is_confirmed_in_words_not_a_modal(self):
        st = self.picker_state(prompts=["ember", "y"])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(self.library.calls,
                         [("create", "ember", True, dict(st.slots))])
        self.assertIn("ember exists", self.labels[1])
        self.assertIn("y overwrites", self.labels[1])

    def test_another_name_at_the_prompt_replaces_the_answer(self):
        st = self.picker_state(prompts=["ember", "dusk"])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(self.library.calls[0][1:3], ("dusk", False))
        self.assertEqual(st.theme, "dusk")

    def test_a_cancelled_confirm_leaves_the_taken_theme_alone(self):
        for answer in ("", None):
            with self.subTest(answer=answer):
                st = self.picker_state(prompts=["ember", answer])
                editor.apply_key("t", st)
                editor.apply_key("n", st)
                self.assertEqual(self.library.calls, [])
                self.assertEqual(st.status, "cancelled - ember is untouched")
                self.assertEqual(st.theme, "ember")

    def test_a_cancelled_prompt_creates_nothing(self):
        st = self.picker_state(prompts=[""])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(self.library.calls, [])
        self.assertEqual(st.status, "cancelled - no theme created")

    def test_a_retype_that_is_also_taken_creates_nothing(self):
        st = self.picker_state(prompts=["ember", "frost"])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(self.library.calls, [])
        self.assertIn("frost exists too", st.status)

    def test_a_refusal_comes_back_as_a_status_not_an_exception(self):
        problem = "invalid theme name (letters, digits, - and _, 64 max)"
        st = self.picker_state(prompts=["not a name"], problem=problem)
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(st.status, problem)
        self.assertEqual(st.theme, "ember")
        self.assertEqual(self.writes, [])

    def test_capital_n_saves_the_buffer_as_a_new_theme(self):
        st = self.picker_state(prompts=["dusk"])
        editor.apply_key("w", st)
        editor.apply_key("N", st)
        name, force, values = self.library.calls[0][1:]
        self.assertEqual((name, force), ("dusk", False))
        self.assertEqual(values, st.slots)
        # ... and then through the ordinary save pipeline, push included
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0][:2], ("dusk", "/themes/dusk.toml"))
        self.assertEqual(self.writes[0][2], st.slots)
        self.assertEqual(st.status, "saved dusk → ghostty")
        self.assertTrue(st.written)
        self.assertFalse(st.dirty())
        self.assertEqual(st.undo, [])

    def test_capital_n_without_a_name_writes_nothing(self):
        st = self.picker_state(prompts=[None])
        editor.apply_key("w", st)
        editor.apply_key("N", st)          # prompt cancelled: no name
        self.assertEqual(self.writes, [])
        self.assertFalse(st.written)
        self.assertTrue(st.dirty())         # the buffer is untouched
        self.assertEqual(st.status, "cancelled - no theme created")
        self.assertEqual(st.theme, "ember")
        self.assertEqual(self.library.calls, [])

    def test_capital_n_migrates_a_direct_session_off_the_config(self):
        # §13.4 — `N` is the way out of a legacy direct-mode session
        st = self.picker_state(theme=None, prompts=["dusk"])
        st.path = "/home/you/.config/kitty/kitty.conf"
        st.backup_path = "/home/you/.config/kitty/kitty.conf"
        editor.apply_key("w", st)
        editor.apply_key("N", st)
        self.assertEqual(st.theme, "dusk")
        self.assertEqual(st.path, "/themes/dusk.toml")
        self.assertIsNone(st.backup_path)         # huebox owns it now: no .bak
        self.assertEqual(self.writes[0][:2], ("dusk", "/themes/dusk.toml"))
        self.assertEqual(editor.head_label(st), "dusk ghostty")  # saved


class StatusBar(unittest.TestCase):
    """`<theme> ● <fmt>` and `direct:<path>` (§13.7)."""

    def state(self, theme="ember", fmt="ghostty", path="/themes/ember.toml"):
        return editor.EditorState(dict(FULL_SLOTS),
                                  lambda values: "saved",
                                  theme=theme, fmt=fmt, path=path)

    def test_a_clean_theme_session_names_the_theme_and_the_target(self):
        self.assertEqual(editor.head_label(self.state()), "ember ghostty")

    def test_a_dirty_buffer_puts_a_dot_between_them(self):
        st = self.state()
        editor.apply_key("w", st)
        self.assertTrue(st.dirty())
        self.assertEqual(editor.head_label(st), "ember ● ghostty")
        editor.apply_key(SAVE, st)                # saved: clean again
        self.assertEqual(editor.head_label(st), "ember ghostty")

    def test_with_no_named_target_only_the_theme_is_shown(self):
        self.assertEqual(editor.head_label(self.state(fmt="")), "ember")
        st = self.state(fmt="")
        editor.apply_key("w", st)
        self.assertEqual(editor.head_label(st), "ember ●")

    def test_a_direct_session_names_the_config(self):
        st = self.state(theme=None, path="/tmp/kitty.conf")
        self.assertEqual(editor.head_label(st), "direct:/tmp/kitty.conf")
        self.assertEqual(editor.session_path(st), "")      # not printed twice
        self.assertEqual(editor.session_path(self.state()),
                         "/themes/ember.toml")

    def test_the_head_is_width_aware(self):
        # the dim path is dropped when it does not fit beside the subject
        out = io.StringIO()
        with mock.patch.object(editor, "term_size", return_value=(40, 24)), \
                mock.patch.object(sys, "stdout", out):
            editor.draw_editor("", "/themes/a-very-long-theme-name.toml",
                               FULL_SLOTS, 0, [], "", 1,
                               head=editor.head_label(
                                   self.state(path="/themes/a-very-long-"
                                                  "theme-name.toml")))
        self.assertNotIn("a-very-long-theme-name.toml",
                         ANSI.sub("", out.getvalue()))


class OverlayFrame(unittest.TestCase):
    """The picker frame inside the width and height budget (§13.7, §15)."""

    MANY = [f"theme-{index:02d}" for index in range(34)]

    def overlay_frame(self, cols, rows, names=("ash", "ember", "frost"),
                      index=0, current="ember", status=""):
        """The picker frame, composed the way `app.Picker` composes it."""
        return "\r\n".join(
            editor.backdrop(line, FULL_SLOTS, cols)
            for line in editor.theme_lines(list(names), index, current,
                                           cols, rows, status,
                                           dict(FULL_SLOTS)))

    def test_the_frame_names_the_themes_and_marks_the_current_one(self):
        body = ANSI.sub("", self.overlay_frame(80, 24))
        self.assertIn("themes", body)
        self.assertIn("* ember", body)             # the library's current
        self.assertIn("ash", body)
        self.assertIn("> ash", body)               # and the selection
        self.assertIn("Enter use", body)

    def test_every_line_holds_the_width_and_the_frame_the_height(self):
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            with self.subTest(size=(cols, rows)):
                body = lines(self.overlay_frame(cols, rows, self.MANY, 17))
                self.assertLessEqual(len(body), rows)
                for line in body:
                    self.assertLessEqual(width(line), cols)

    def test_a_long_library_scrolls_with_the_selection_in_view(self):
        body = ANSI.sub("", self.overlay_frame(40, 12, self.MANY, 17,
                                               status="opened theme-17"))
        self.assertIn("> theme-17", body)          # the selection is on screen
        self.assertIn("of 34", body)               # and the window is counted
        self.assertLessEqual(body.count("theme-"), 12)
        # the last row is reachable too: the window follows, it never runs off
        last = ANSI.sub("", self.overlay_frame(40, 12, self.MANY, 33))
        self.assertIn("34 of 34", last)

    def test_the_hint_footer_folds_instead_of_overflowing(self):
        for cols in (40, 60, 80):
            with self.subTest(cols=cols):
                body = lines(self.overlay_frame(cols, 12))
                hints = [ANSI.sub("", line) for line in body
                         if "move" in ANSI.sub("", line)
                         or "open" in ANSI.sub("", line)
                         or "new" in ANSI.sub("", line)
                         or "back" in ANSI.sub("", line)]
                self.assertTrue(hints)
                for line in hints:
                    self.assertLessEqual(width(line), cols)
        # at the minimum width the footer cannot fit on one row, so it wraps
        # (§13.7 - pack(), not a clipped single line)
        narrow = lines(self.overlay_frame(40, 24))
        self.assertGreaterEqual(
            len([line for line in narrow if "move" in ANSI.sub("", line)
                 or "back" in ANSI.sub("", line)]), 2)

    def test_a_64_character_name_is_clipped_not_wrapped(self):
        long = "x" * 64
        body = lines(self.overlay_frame(40, 12, [long], 0))
        self.assertLessEqual(len(body), 12)
        for line in body:
            self.assertLessEqual(width(line), 40)
        self.assertIn("x" * 30, ANSI.sub("", "\r\n".join(body)))

    def test_an_empty_library_says_how_to_start_one(self):
        body = ANSI.sub("", self.overlay_frame(80, 24, []))
        self.assertIn("no themes yet", body)
        self.assertIn("N makes one from this buffer", body)

    def test_the_status_line_is_the_last_row(self):
        body = lines(self.overlay_frame(80, 24, status="save (Ctrl+S) or "
                                                     "revert (r) first"))
        self.assertIn("save (Ctrl+S) or revert (r) first", ANSI.sub("", body[-1]))

    def test_the_frame_is_byte_identical_for_identical_input(self):
        self.assertEqual(self.overlay_frame(60, 16, self.MANY, 4, "theme-04"),
                         self.overlay_frame(60, 16, self.MANY, 4, "theme-04"))

    def test_below_the_minimum_the_frame_is_a_list_and_nothing_else(self):
        """What the picker does with no room, now that it is its own widget.

        §13.7 — the picker shares the editor's minimum size, so below it the
        two frames cannot both be wrong about what to draw. That check used to
        live in `draw_editor`'s overlay branch; with the branch gone it lives
        in `Editor.redraw`, and `tests/test_app` is where the assertion is now.
        What is left here is the picker's own side of the contract: `theme_lines`
        is a *frame*, and asked for fewer rows than it has, it keeps the rows it
        can and drops the rest rather than raising or inventing any.
        """
        for cols, rows in ((editor.MIN_COLS - 1, 24), (80, editor.MIN_ROWS - 1)):
            with self.subTest(size=(cols, rows)):
                got = self.overlay_frame(cols, rows, self.MANY)
                self.assertLessEqual(got.count("\r\n"), rows,
                                     "the picker wrote past the screen")


def _rows(cols, rows, sel, mult=False, status=""):
    """The editor frame's rows as plain text — what a click lands on."""
    out = io.StringIO()
    with mock.patch.object(sys, "stdout", out):
        editor.draw_editor("ghostty", "", FULL_SLOTS, sel, [], status, mult,
                           size=(cols, rows))
    return plain_rows(out.getvalue())


def _picker_rows(names, index, current, cols, rows):
    """The picker frame's rows as plain text."""
    return [plain(line) for line in
            editor.theme_lines(names, index, current, cols, rows)]


class HitMap(unittest.TestCase):
    """Every clickable cell, cross-checked against the frame that paints it.

    `frame_hits` is a second description of where the grids are, and that is a
    real risk: a layout change that moves a row would leave it pointing at the
    wrong cell, and clicking would select something the user is not looking at,
    with nothing in the log. So no hit is believed until the painted frame is
    asked — a hit counts only if the cell it claims carries that slot's marker.
    """

    def test_every_hit_claims_a_cell_the_frame_marks(self):
        for cols, rows in ((120, 30), (100, 30), (80, 24), (60, 16), (40, 12)):
            hits = editor.frame_hits(cols, rows)
            # A short frame shows fewer slots, and a slot it does not show has
            # no cell to click — 15 of 22 at 60x16, 12 at 40x12. The map is
            # exactly the prefix the frame paints, and claiming a cell below the
            # trim would mean selecting something the user cannot see.
            self.assertEqual([hit.slot for hit in hits],
                             list(range(len(hits))))
            self.assertLessEqual(len(hits), len(SLOTS))
            for hit in hits:
                with self.subTest(cols=cols, slot=hit.slot):
                    painted = _rows(cols, rows, hit.slot)
                    self.assertLess(hit.y, len(painted),
                                    "hit points past the end of the frame")
                    cell = painted[hit.y][hit.x0 + 1:hit.x0 + 2]
                    self.assertEqual(
                        cell, ">",
                        "row %d col %d does not carry the `>` marker for slot "
                        "%d (%r)" % (hit.y, hit.x0, hit.slot, cell))

    def test_a_hit_is_the_cell_that_cell_wide(self):
        for cols in (120, 100, 80, 60, 40):
            hits = {hit.slot: hit for hit in editor.frame_hits(cols, 30)}
            grid = editor.grid_geometry(cols)
            cellw = editor.CELL_FULL if grid.show_hex else editor.CELL_MIN
            self.assertEqual(hits[0].x0, 2)
            self.assertEqual(hits[1].x0, 2 + cellw)
            self.assertEqual(hits[0].x1, hits[0].x0 + cellw - 1)

    def test_the_interface_cells_sit_below_the_palette(self):
        for cols in (120, 80, 40):
            hits = {hit.slot: hit for hit in editor.frame_hits(cols)}
            self.assertGreater(hits[len(editor.PALETTE)].y,
                               hits[len(editor.PALETTE) - 1].y,
                               "the interface grid overlaps the palette's")

    def test_chrome_is_not_clickable(self):
        hits = editor.frame_hits(80, 24)
        for x, y in ((0, 0), (0, 1), (2, 2), (79, 0)):
            self.assertIsNone(editor.slot_at(hits, x, y),
                              "(%d,%d) is chrome, not a colour" % (x, y))

    def test_a_click_anywhere_in_a_cell_selects_that_slot(self):
        for cols in (120, 80, 40):
            hits = editor.frame_hits(cols)
            for hit in hits:
                with self.subTest(cols=cols, slot=hit.slot):
                    self.assertEqual(editor.slot_at(hits, hit.x0, hit.y),
                                     hit.slot)
                    self.assertEqual(editor.slot_at(hits, hit.x1, hit.y),
                                     hit.slot,
                                     "the last column of a cell must still "
                                     "hit it, or it has a dead sliver")

    def test_the_picker_windows_so_a_click_lands_on_what_is_shown(self):
        names = [f"theme-{n:02d}" for n in range(40)]
        for index in (0, 5, 20, 39):
            hits = editor.theme_hits(names, index, "", 80, 24)
            self.assertTrue(hits, "a 40-theme library must be clickable")
            for hit in hits:
                with self.subTest(index=index, row=hit.slot):
                    painted = _picker_rows(names, index, "", 80, 24)
                    self.assertIn(names[hit.slot], painted[hit.y])

    def test_an_empty_picker_has_nothing_to_hit(self):
        self.assertEqual(editor.theme_hits([], 0, "", 80, 24), [])


class Regions(unittest.TestCase):
    """The frame names its blocks, and names them where they are (§5.6).

    Phase 5 mounts one widget per block. The only thing standing between that
    and a frame whose widgets disagree with the frame is this map, so it is
    pinned hard: the blocks must tile the frame exactly — first at row 0, no
    gaps, no overlaps, no block reaching past the bottom — at every size and
    with and without a status line, because the status line is the one block
    that appears and disappears.
    """

    def _regions(self, cols, rows, status=""):
        found = []
        original = editor.term_size
        editor.term_size = lambda default=(80, 24): (cols, rows)
        try:
            with mock.patch.object(sys, "stdout", io.StringIO()):
                editor.draw_editor("ghostty", "", dict(FULL_SLOTS), 0, [],
                                   status, 1, regions=found,
                                   size=(cols, rows))
        finally:
            editor.term_size = original
        return found

    def test_the_blocks_tile_the_frame(self):
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            for status in ("", "reverted to start"):
                with self.subTest(size=f"{cols}x{rows}", status=status):
                    found = self._regions(cols, rows, status)
                    self.assertTrue(found, "no regions reported")
                    self.assertEqual(found[0][1], 0,
                                     "the first block does not start at row 0")
                    for before, after in zip(found, found[1:]):
                        self.assertEqual(
                            before[1] + before[2], after[1],
                            "%s and %s are not adjacent" % (before[0], after[0]))

    def test_no_block_reaches_past_the_bottom(self):
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            for status in ("", "reverted to start"):
                with self.subTest(size=f"{cols}x{rows}", status=status):
                    for name, first, count in self._regions(cols, rows, status):
                        self.assertGreater(count, 0,
                                           "%s is an empty block" % name)
                        self.assertLessEqual(first + count, rows,
                                             "%s reaches past the frame" % name)

    def test_the_blocks_total_the_rows_the_frame_painted(self):
        """The count is the frame's height, not the screen's.

        These differ: at 60x16 the frame is fifteen rows and the screen is
        sixteen. Asserting the blocks reach the bottom of the *screen* would
        demand a row the frame never drew — which is precisely the failure the
        second test above exists to forbid."""
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            with self.subTest(size=f"{cols}x{rows}"):
                painted = len(lines(frame(cols, rows)))
                found = self._regions(cols, rows)
                self.assertEqual(sum(count for _, _, count in found), painted)

    def test_a_status_line_is_its_own_block_and_the_last(self):
        found = self._regions(80, 24, "reverted to start")
        names = [name for name, _, _ in found]
        self.assertIn("status", names)
        self.assertEqual(names[-1], "status")
        self.assertNotIn("status",
                         [name for name, _, _ in self._regions(80, 24)])

    def test_a_short_frame_drops_its_widgets_before_its_grid(self):
        """§15 — the frame spends decoration before it spends a widget.

        At 40x12 there is no room for the code sample or the examples strip, so
        the blocks stop after the grids and the selected readout the narrowed
        interface leaves room for (§15.2). A map that reported the widgets
        anyway would be a map describing a frame that is not the one on
        screen."""
        names = [name for name, _, _ in self._regions(40, 12)]
        self.assertEqual(names, ["header", "palette", "interface",
                                 "selected"])

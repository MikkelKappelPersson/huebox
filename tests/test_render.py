"""Layout tests: clipping, wide glyphs, the examples strip and the preview grid."""

import os
import re
import sys
import unicodedata
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402
from huebox.color import (hex_to_rgb, hsv_to_rgb,  # noqa: E402
                         readable_fg, rgb_to_hex, rgb_to_hsv)
from huebox.render import (MINI_LETTERS, MINI_WIDTH, BANNER_FACE_SLOTS, BANNER_LETTERS, BANNER_SHADOWS, BANNER_WIDTH, BOLD, CALL_SLOT, CHROME_KEY,  # noqa: E402
                           CHROME_LABEL, CHROME_MUTED, CURSOR_CHAR, DIFF_ADDED,
                           DIFF_BODY, DIFF_CONTEXT, DIFF_HUNK, DIFF_MARKS,
                           DIFF_REMOVED, EXAMPLE_PHRASE, HSV_COMPACT,
                           HSV_CHROME, HSV_FULL, HSV_TIGHT, MARK,
                           LABEL_WIDTH, PAIR_MIN_COLS,
                           PAIR_WIDTH, SELECTED_TEXT, TOKEN_SLOTS, WORDMARK,
                           WORDMARK_SLOTS, _sample, mini_banner_lines,
                           backdrop, banner_lines, bg,
                           chrome, fg,
                           hint_line, hsv_numbers, hsv_readout, key_hint,
                           pack,
                           pair_label, title, visible, wordmark)
from huebox import render  # noqa: E402
from huebox.render import _marker  # noqa: E402
from huebox.render import RESET  # noqa: E402


def _plain(text):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def _banner_cells(row):
    """Painted cells of a banner row as `(column, glyph, bold)`."""
    found, column, bold, ink = [], 0, False, False
    for token in re.findall(r"\x1b\[[0-9;]*m|[^\x1b]", row):
        if token == BOLD:
            bold = True
        elif token == RESET:
            bold, ink = False, False
        elif token.startswith("\x1b["):
            ink = True
        elif ink:
            found.append((column, token, bold))
            column += 1
        else:
            column += 1
    return found


class Sample(unittest.TestCase):
    """The live code sample: which palette slots it spends, and why (§8)."""

    # every slot the zig lexer can express, with the vocabulary that earns
    # it — see TOKEN_SLOTS in render.py and the coverage note in spec §8
    BASE = {"palette-2", "palette-3", "palette-5", "palette-6"}
    BRIGHT = {"palette-8", "palette-11", "palette-12", "palette-14"}

    def setUp(self):
        self.runs = _sample()
        self.slots_used = {slot for slot, _ in self.runs}

    def slot_of(self, text):
        """The slot the sample paints `text` in."""
        for slot, run in self.runs:
            if run == text:
                return slot
        self.fail(f"{text!r} is not in the sample")

    def test_the_sample_spends_the_whole_lexer_vocabulary(self):
        # the point of the mapping: a zig sample reaches eight palette
        # slots, not four. The other eight have no token class to wear.
        self.assertEqual(self.slots_used & set(huebox.PALETTE),
                         self.BASE | self.BRIGHT)
        self.assertIn("foreground", self.slots_used)

    def test_syntax_is_the_base_half_and_semantics_the_bright_half(self):
        # the split is the design, not a coincidence: keywords, operators,
        # strings, numbers and types stay on the base half, and the bright
        # half carries what the base half had no room for — the muted grey
        # for comments (palette-8 is bright black) plus escapes, calls and
        # builtins. Four and four, no slot borrowed across the line.
        for slot in self.BASE:
            with self.subTest(slot=slot):
                self.assertLess(int(slot.split("-")[1]), 8)
        for slot in self.BRIGHT:
            with self.subTest(slot=slot):
                self.assertGreaterEqual(int(slot.split("-")[1]), 8)

    def test_no_row_in_the_table_is_dead(self):
        # every row earns its place: a class the sample never emits is a
        # promise the palette does not keep
        table = {slot for _, slot in TOKEN_SLOTS}
        self.assertTrue(table <= self.slots_used,
                        f"unreachable rows: {sorted(table - self.slots_used)}")

    def test_a_type_wears_cyan_and_a_builtin_the_bright_cyan(self):
        self.assertEqual(self.slot_of("u32"), "palette-6")
        self.assertEqual(self.slot_of("void"), "palette-6")
        self.assertEqual(self.slot_of("@import"), "palette-14")

    def test_an_escape_wears_the_bright_yellow_inside_its_string(self):
        # the escape sits in the middle of the string it belongs to
        self.assertEqual(self.slot_of("\\n"), "palette-11")
        self.assertEqual(self.slot_of('{d} colours'), "palette-2")

    def test_a_called_name_wears_the_bright_blue_and_a_declared_one_does_not(self):
        # `print(...)` is a call; `fn main()` is a definition, and the zig
        # lexer cannot tell them apart on its own
        self.assertEqual(self.slot_of("print"), CALL_SLOT)
        for name in ("main", "huebox", "std", "count"):
            self.assertEqual(self.slot_of(name), "foreground")

    def test_the_sample_fits_a_default_eighty_column_terminal(self):
        # the editor indents the block by four; anything wider would be
        # clipped in the terminal huebox ships into
        for line, _ in huebox.sample_lines({name: "#ff8800"
                                            for name in huebox.SLOTS}):
            self.assertLessEqual(len(_plain(line)) + 4, 80, line)

    def painted_runs(self, line):
        """Split one painted line into [(colour escape, text)] runs."""
        out = []
        for part in re.split(r"(\x1b\[[0-9;?]*[A-Za-z])", line):
            if not part:
                continue
            if part.startswith("\x1b"):
                out.append([part, ""])
            elif out:
                out[-1][1] += part
            else:
                out.append(["", part])
        return [tuple(run) for run in out]

    def test_one_slot_change_repaints_its_runs_and_nothing_else(self):
        # §14.1 — the mapping is resolved once, but the colours are read
        # per frame: changing the bright blue moves `print` and nothing
        # else in the block
        base = {name: "#3f7a3f" for name in huebox.SLOTS}
        before = [line for line, _ in huebox.sample_lines(base)]
        after = [line for line, _ in
                 huebox.sample_lines(dict(base, **{CALL_SLOT: "#ff00ff"}))]
        self.assertEqual(len(before), len(after))
        moved = []
        for old, new in zip(before, after):
            self.assertEqual(_plain(old), _plain(new))
            for (_, text), (colour, _) in zip(self.painted_runs(old),
                                              self.painted_runs(new)):
                if text == "print":
                    self.assertEqual(colour, fg("#ff00ff"))
                moved.append(text)
        self.assertEqual(moved.count("print"), 1)

    def test_the_sample_is_unchanged_between_frames_apart_from_colour(self):
        # same token stream, same text: only the escape prefixes differ
        base = {name: "#3f7a3f" for name in huebox.SLOTS}
        one = huebox.sample_lines(base)
        two = huebox.sample_lines(dict(base, **{"palette-5": "#010203"}))
        self.assertEqual([_plain(line) for line, _ in one],
                         [_plain(line) for line, _ in two])


class Geometry(unittest.TestCase):
    def test_clip_respects_wide_glyphs(self):
        # a CJK glyph occupies two columns
        self.assertLessEqual(len(huebox.clip("ab", 10)) - 0, 12)
        self.assertTrue(huebox.clip("一二三四五", 4).startswith("\033[0m")
                        or "\033[0m" in huebox.clip("一二三四五", 4))

    def test_preview_has_no_wide_glyphs(self):
        # the banner is box drawing, not ascii — but every glyph is still
        # one column where `clip`/`visible` count it, so the preview's
        # columns hold wherever ambiguous counts narrow (decision 33 notes
        # the UTF-8 assumption this keeps).
        slots = {name: "#ff8800" for name in huebox.SLOTS}
        text = huebox.render_preview("ghostty", "/tmp/x", slots, cols=120, rows=40)
        for line in text.split("\n"):
            for char in line:
                self.assertNotIn(unicodedata.east_asian_width(char), ("W", "F"),
                                 f"wide glyph in preview: {line!r}")

    def test_narrow_preview_has_no_wide_glyphs(self):
        # the mini banner stands in at 40 columns: block art, but every
        # glyph is still one column, so the piped preview holds its width
        slots = {name: "#ff8800" for name in huebox.SLOTS}
        text = huebox.render_preview("ghostty", "/tmp/x", slots, cols=40)
        for line in text.split("\n"):
            width = len(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line))
            self.assertLessEqual(width, 40, f"width {width} > 40")
            for char in line:
                self.assertNotIn(unicodedata.east_asian_width(char), ("W", "F"),
                                 f"wide glyph in narrow preview: {line!r}")

    def test_tiny_preview_stays_ascii(self):
        # below the mini banner the wordmark stands back in, and the
        # whole preview is ascii again — piped tiny output is unchanged
        slots = {name: "#ff8800" for name in huebox.SLOTS}
        text = huebox.render_preview("ghostty", "/tmp/x", slots, cols=20)
        for line in text.split("\n"):
            self.assertTrue(all(ord(ch) < 128 for ch in line),
                            f"non-ascii in tiny preview: {line!r}")

    def test_preview_never_exceeds_width(self):
        slots = {name: "#ff8800" for name in huebox.SLOTS}
        for cols in (200, 120, 100, 80, 70, 60, 50, 40, 30, 24):
            text = huebox.render_preview("ghostty", "/tmp/x", slots, cols=cols)
            for line in text.split("\n"):
                width = len(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line))
                self.assertLessEqual(width, cols,
                                     f"width {width} > {cols}")


class Floor(unittest.TestCase):
    """The frame's own floor: every row stands on the buffer (§8.2)."""

    SLOTS = {"background": "#101014", "foreground": "#e6e6ea"}

    def test_a_row_reaches_exactly_the_width(self):
        # the floor costs no content: a short row is padded, never trimmed
        for text in ("", "  palette", "  a", fg("#ff8800") + "aa" + RESET):
            for cols in (10, 40, 80, 120):
                row = backdrop(text, self.SLOTS, cols)
                self.assertEqual(visible(row), cols, repr(text))

    def test_the_floor_is_the_buffers_own_background(self):
        row = backdrop("  palette", self.SLOTS, 40)
        self.assertTrue(row.startswith(bg("#101014")))
        self.assertTrue(row.endswith(RESET))
        # a missing slot paints MISSING in place, like every other widget
        self.assertTrue(backdrop("x", {}, 10).startswith(bg(huebox.MISSING)))

    def test_a_missing_background_is_read_per_call(self):
        # §14.1 — no colour is cached between frames
        first = backdrop("x", {"background": "#111111"}, 20)
        second = backdrop("x", {"background": "#222222"}, 20)
        self.assertNotEqual(first, second)

    def test_a_blank_row_is_the_floor_and_a_content_row_is_not(self):
        # the same 40 columns of space on screen, and the paint is what
        # tells them apart: the frame's own air is the fill written once
        blank = backdrop("", self.SLOTS, 40)
        self.assertEqual(blank.count("48;2;"), 1)
        self.assertEqual(blank, bg("#101014") + " " * 40 + RESET)
        widget = backdrop("    ", self.SLOTS, 40)      # a block's blank line
        self.assertEqual(widget.count("48;2;"), 2)
        self.assertTrue(widget.startswith(bg("#101014") + "    "))

    def test_a_reset_reopens_the_fill(self):
        # §8.2 — SGR 0 clears the background too, so a row painted once at
        # its head leaves every run after the first reset on the terminal's
        # own background. This is the hole the frame showed: the rest of
        # the wordmark, the parenthetical beside a header, the gap between
        # two hints.
        row = chrome("aa", CHROME_MUTED, self.SLOTS) + "  bb"
        painted = backdrop(row, self.SLOTS, 20)
        # the fill opens the row, reopens after the chrome run's reset, and
        # closes before the pad: three times for one reset
        self.assertIn(RESET + bg("#101014") + "  bb", painted)
        self.assertEqual(painted.count(bg("#101014")), 3)

    def test_a_row_too_wide_is_clipped_not_padded(self):
        row = backdrop("x" * 60, self.SLOTS, 20)
        self.assertEqual(visible(row), 20)
        self.assertEqual(_plain(row), "x" * 20)

    def test_a_widgets_colour_never_reaches_the_frame_own_columns(self):
        # after the content's own reset the row is floor again: the padding
        # to the edge belongs to the buffer, not to the last thing painted
        row = backdrop(chrome("aa", CHROME_MUTED, self.SLOTS), self.SLOTS, 30)
        self.assertEqual(row.split(RESET)[-2],
                         bg("#101014") + " " * 28)


class HsvReadout(unittest.TestCase):
    """The slot's reading: a name, a number and a bar, per axis (§8.3)."""

    SLOTS = {"background": "#101014", "foreground": "#e6e6ea",
             "palette-11": "#e0c06c", "palette-8": "#d0d0d8"}
    # one bar cell: its background, its foreground if it has one, and its
    # one character — a bar cell carries no ink unless it is the hairline
    CELL = re.compile(r"\x1b\[48;2;(\d+);(\d+);(\d+)m"
                      r"(?:\x1b\[38;2;(\d+);(\d+);(\d+)m)?([^\x1b])")
    WIDE = sum(HSV_FULL) + HSV_CHROME    # the chrome is fixed — see below

    def cells(self, line):
        """Every bar cell of the readout: (bg, fg or None, character)."""
        return [(tuple(int(v) for v in (r, g, b)),
                 tuple(int(v) for v in (fr, fg, fb)) if fr else None, char)
                for r, g, b, fr, fg, fb, char in self.CELL.findall(line)]

    def chip(self, line, index):
        """One bar's cells, by position in the readout."""
        cells, at = self.cells(line), 0
        for width in HSV_FULL[:index]:
            at += width
        return cells[at:at + HSV_FULL[index]]

    def reading(self, value, axis):
        """The slot's own reading of one axis, 0..1."""
        return rgb_to_hsv(hex_to_rgb(value))[axis]

    def marks(self, cells):
        """Where the hairline is: the one cell wearing the mark."""
        return [i for i, (_, _, char) in enumerate(cells) if char == MARK]

    def test_the_readings_are_in_fixed_fields_so_the_bars_never_move(self):
        # `hue   9°`, `hue  10°`, `hue 120°`: a reading that grows a digit
        # must not move the bar after it, or the whole row jumps a column
        # every time a number crosses a power of ten (§8.3)
        for value in ("#090000", "#ff0000", "#00ff00", "#00ffff", "#ffffff",
                      "#010101", "#61afef"):
            line = hsv_readout(self.SLOTS, value, self.WIDE)
            with self.subTest(value=value):
                # the bars are the last `sum(HSV_FULL)` columns, so what is
                # left for the readings is the same whatever they say
                self.assertEqual(visible(line) - sum(HSV_FULL), HSV_CHROME)
                self.assertEqual(visible(line), self.WIDE)
        self.assertEqual(HSV_CHROME, 31)          # a constant, not a range
        self.assertTrue(_plain(hsv_readout(self.SLOTS, "#00ff00", self.WIDE))
                        .startswith("hue 120° "))
        self.assertTrue(_plain(hsv_readout(self.SLOTS, "#090000", self.WIDE))
                        .startswith("hue   0° "))

    def test_a_wide_room_gives_three_bars_with_their_readings(self):
        line = hsv_readout(self.SLOTS, "#61afef", self.WIDE)
        text = _plain(line)
        readings = (("hue", "207°"), ("sat", " 59%"), ("val", " 94%"))
        for label, reading in readings:
            self.assertIn(f"{label} {reading} ", text)
        self.assertEqual(len(self.cells(line)), sum(HSV_FULL))

    def test_the_reading_sits_beside_its_bar_and_never_inside_it(self):
        # the arrangement that lets the number and the hairline both be
        # complete: `hue 207° [bar]`, the bar carrying only the sweep and
        # the line, so there is nothing for the line to take (§8.3)
        line = hsv_readout(self.SLOTS, "#61afef", self.WIDE)
        readings = (("hue", "207°"), ("sat", " 59%"), ("val", " 94%"))
        for label, reading in readings:
            with self.subTest(label=label):
                self.assertIn(f"{label} {reading} ", _plain(line))
        cells = self.cells(line)
        for index in range(3):
            bar = self.chip(line, index)
            self.assertEqual({char for _, _, char in bar}, {" ", MARK})
        self.assertEqual(len(cells), sum(HSV_FULL))

    def test_every_bar_is_a_sweep_of_its_whole_axis(self):
        # the hue bar is the wheel at the slot's own sat/val, the sat bar
        # grey -> colour, the val bar black -> colour, each of the last two
        # painted at the slot's own hue: the bar is the axis (§8.3)
        hue, sat, val = rgb_to_hsv(hex_to_rgb("#61afef"))
        bars = self.cells(hsv_readout(self.SLOTS, "#61afef", self.WIDE))
        at = 0
        for width, colour in ((HSV_FULL[0], lambda t: hsv_to_rgb(t, sat, val)),
                              (HSV_FULL[1], lambda t: hsv_to_rgb(hue, t, val)),
                              (HSV_FULL[2],
                               lambda t: hsv_to_rgb(hue, sat, t))):
            bar = bars[at:at + width]
            with self.subTest(width=width):
                self.assertEqual(len(bar), width)
                sweep = [hex_to_rgb(rgb_to_hex(colour(i / (width - 1))))
                         for i in range(width)]
                self.assertEqual([cell[0] for cell in bar], sweep)
            at += width

    def test_every_bar_carries_one_hairline_and_nothing_else(self):
        # a line, not a stripe: one cell, one glyph, an eighth of it ink,
        # always inside the bar, always readable
        for value in ("#61afef", "#ff0000", "#ffffff", "#000000", "#f5f5dc",
                      "#010203"):
            bars = self.cells(hsv_readout(self.SLOTS, value, self.WIDE))
            at = 0
            for axis, width in enumerate(HSV_FULL):
                bar = bars[at:at + width]
                with self.subTest(value=value, width=width):
                    want = min(width - 1, max(0, int(self.reading(value, axis)
                                                    * (width - 1) + 0.5)))
                    self.assertEqual(self.marks(bar), [want])
                    line = want
                    self.assertEqual(bar[line][2], MARK)
                    self.assertEqual(bar[line][1],
                                     hex_to_rgb(readable_fg(rgb_to_hex(
                                         bar[line][0]))))
                    # every other cell is bare: no ink at all
                    self.assertEqual([cell[1] for i, cell in enumerate(bar)
                                      if i != line], [None] * (width - 1))
                at += width

    def test_the_hairline_is_where_the_reading_is(self):
        # never more than half a cell off, and in the end cell at the ends
        for value in ("#61afef", "#00c000", "#ff0000", "#ffffff", "#0000ff",
                      "#f5f5dc", "#010203"):
            bar = self.chip(hsv_readout(self.SLOTS, value, self.WIDE), 0)
            with self.subTest(value=value):
                line = self.marks(bar)[0]
                want = self.reading(value, 0) * (HSV_FULL[0] - 1)
                self.assertLessEqual(abs(line - want), 1.0)
                self.assertGreaterEqual(line, 0)
                self.assertLessEqual(line, HSV_FULL[0] - 1)

    def test_the_hairline_hugs_the_ends_of_an_axis(self):
        # `MARK` is drawn an eighth into a cell, so the two ends of the axis
        # land in the first and the last cell and nowhere else
        self.assertEqual(_marker(0, 9), {0: MARK})
        self.assertEqual(_marker(-1, 9), {0: MARK})
        self.assertEqual(_marker(8, 9), {8: MARK})
        self.assertEqual(_marker(99, 9), {8: MARK})
        self.assertEqual(_marker(4, 9), {4: MARK})
        self.assertEqual(_marker(4.4, 9), {4: MARK})
        self.assertEqual(_marker(4.6, 9), {5: MARK})

    def test_the_bars_follow_the_reading_they_are_about(self):
        # `q` moves the hue: the reading moves, the wheel does not (it is the
        # whole wheel, at the slot's own sat and val), and the other two bars
        # — which are painted at the slot's hue — do (§14.1)
        before = hsv_readout(self.SLOTS, "#61afef", self.WIDE)
        after = hsv_readout(self.SLOTS, "#ef8b61", self.WIDE)   # 28°, same s/v
        self.assertNotEqual(_plain(before), _plain(after))
        self.assertEqual([cell[0] for cell in self.chip(after, 0)],
                         [cell[0] for cell in self.chip(before, 0)])
        self.assertNotEqual(self.marks(self.chip(before, 0)),
                            self.marks(self.chip(after, 0)))
        self.assertNotEqual([cell[0] for cell in self.chip(before, 1)],
                            [cell[0] for cell in self.chip(after, 1)])
        self.assertEqual(before, hsv_readout(self.SLOTS, "#61afef", self.WIDE))

    def test_nothing_in_a_bar_is_a_terminal_attribute(self):
        line = hsv_readout(self.SLOTS, "#61afef", self.WIDE)
        self.assertNotIn("\033[1m", line)          # no bold: §8.1
        self.assertNotIn("\033[2m", line)          # no dim either
        self.assertNotIn("\033[7m", line)          # no reverse either
        # and the only ink inside a bar is the hairline's, which is computed
        self.assertEqual(len([cell for _, cell in
                              ((c[0], c[1]) for c in self.cells(line))
                              if cell is not None]), 3)

    def test_the_ladder_and_never_a_rung_cut_after_the_fact(self):
        # three rungs, each picked at the narrowest row that fits it and
        # filling that row exactly — the readings' chrome is paid for on
        # every one of them
        for sizes in (HSV_FULL, HSV_COMPACT, HSV_TIGHT):
            with self.subTest(sizes=sizes):
                picked = [cols for cols in range(40, self.WIDE + 1)
                          if len(self.cells(hsv_readout(self.SLOTS, "#61afef",
                                                       cols))) == sum(sizes)]
                self.assertTrue(picked)
                line = hsv_readout(self.SLOTS, "#61afef", picked[0])
                self.assertEqual(visible(line), picked[0])
        # below the tightest rung the row carries the exact reading below
        # and nothing else
        self.assertEqual(hsv_readout(self.SLOTS, "#61afef", 40), "")
        for value in ("#61afef", "#000000", "#ffffff", "#808080", "#010203"):
            for cols in range(0, self.WIDE + 4):
                line = hsv_readout(self.SLOTS, value, cols)
                self.assertLessEqual(visible(line), cols, (value, cols))


class Typography(unittest.TestCase):
    """The frame's own vocabulary: keys, labels, muted furniture (§8.1)."""

    HINTS = [("arrows", "move"), ("w/e", "hue"), ("s/d", "sat"),
             ("x/c", "light"), ("f", "x5"), ("i", "hex"), ("^S", "save"),
             ("u", "undo(1)"), ("r", "revert"), ("t", "themes"),
             ("N", "as new"), ("Esc", "quit")]
    AMBER, TEAL, GREY = "#e0c06c", "#6cc0c0", "#d0d0d8"

    def slots(self):
        words = dict(zip(("palette-9", "palette-10", "palette-11",
                          "palette-12", "palette-13", "palette-14"),
                         ("#e06c6c", "#6cc06c", "#e0c06c", "#6c9ce0",
                          "#e06c9c", "#6cc0c0")))
        words.update({"palette-8": self.GREY, "foreground": "#e6e6ea",
                      "background": "#101014"})
        return words

    def test_visible_counts_columns_and_not_escape_bytes(self):
        # what `clip` cuts at and `pack` folds at: one number, both ways
        self.assertEqual(visible(""), 0)
        self.assertEqual(visible("abc"), 3)
        self.assertEqual(visible("一二"), 4)              # wide glyphs are two
        self.assertEqual(visible(f"{fg('#ff8800')}abc{RESET}"), 3)
        self.assertEqual(visible("  " + chrome("move", CHROME_LABEL, {})), 6)

    def test_pack_folds_painted_items_by_their_columns(self):
        # the frame's hints are painted, so folding on raw string length
        # would count every escape as columns and cut the row short
        items = [key_hint(self.slots(), key, what) for key, what in self.HINTS]
        self.assertGreater(len(items[0]), len("arrows move"))  # longer painted
        for cols in (120, 80, 60, 40, 24):
            with self.subTest(cols=cols):
                for row in pack(items, cols, sep="  "):
                    self.assertLessEqual(visible(row), cols)

    def test_a_header_is_the_themes_own_foreground(self):
        # §8.1 — a header takes no colour of its own: `foreground` is the
        # one colour the user chose for text, and a header that wore a
        # palette slot would compete with the widget it introduces
        slots = self.slots()
        slots["foreground"] = "#eeeeee"
        self.assertEqual(title("palette", slots),
                         f"{BOLD}{fg('#eeeeee')}palette{RESET}")
        self.assertEqual(title("examples", slots, "(live buffer: ...)"),
                         f"{BOLD}{fg('#eeeeee')}examples{RESET} "
                         f"{fg(self.GREY)}(live buffer: ...){RESET}")

    def test_the_wordmark_is_one_letter_one_colour(self):
        # the frame's only ornament, at the top where it is read once: six
        # letters, the six bright hues, in palette order
        slots = self.slots()
        painted = [fg(slots[slot]) for slot in WORDMARK_SLOTS]
        self.assertEqual(len(WORDMARK), len(WORDMARK_SLOTS))
        self.assertEqual(wordmark(slots),
                         "".join(f"{BOLD}{colour}{letter}{RESET}"
                                 for letter, colour in zip(WORDMARK, painted)))
        # and it is live: the wordmark follows the buffer like the rest
        moved = dict(slots, **{WORDMARK_SLOTS[0]: "#ff00ff"})
        self.assertIn(fg("#ff00ff"), wordmark(moved))
        self.assertNotEqual(wordmark(slots), wordmark(moved))

    def banner_slots(self):
        # the banner spans both palette rows, so its tests need both:
        # the base row for the faces, the bright row for the shadows
        slots = self.slots()
        slots.update(dict(zip(
            ("palette-1", "palette-2", "palette-3",
             "palette-4", "palette-5", "palette-6"),
            ("#c05c5c", "#5cc05c", "#c0a05c",
             "#5c8cc0", "#c05c8c", "#5cc0c0"))))
        return slots

    def test_the_banner_faces_wear_the_base_row_in_order(self):
        # §8.1 — face letter `i` wears `BANNER_FACE_SLOTS[i]` from the
        # base row, bold like the one-line wordmark
        slots = self.banner_slots()
        raw = "\n".join(banner_lines(slots, 96))
        for letter, slot in zip(WORDMARK, BANNER_FACE_SLOTS):
            with self.subTest(letter=letter):
                self.assertIn(f"{BOLD}{fg(slots[slot])}", raw)

    def test_the_banner_shadow_is_the_same_hue_one_row_brighter(self):
        # face in the base row, shadow in the same hue from the bright
        # row — and nothing else anywhere on the rows
        slots = self.banner_slots()
        raw = "\n".join(banner_lines(slots, 96))
        spent = {rgb_to_hex(tuple(int(part) for part in found))
                 for found in re.findall(r"38;2;(\d+);(\d+);(\d+)", raw)}
        self.assertEqual(spent, {slots[slot]
                                 for slot in BANNER_FACE_SLOTS + WORDMARK_SLOTS})

    def test_the_banner_shadow_takes_a_slot_name(self):
        # `"palette-8"` for a grey drop shadow: the fringe rows carry it
        slots = self.slots()
        raw = "\n".join(banner_lines(slots, 96, shadow="palette-8"))
        self.assertIn(fg(self.GREY), raw)

    def test_the_banner_shadow_stands_below_the_face(self):
        # the copy peeks out from under the face and nowhere else: the
        # top row opens on a face glyph, and the last row is shadow-only
        # fringe — painted, and never bold
        slots = self.slots()
        art = banner_lines(slots, 96)
        self.assertTrue(_plain(art[0]).startswith("█"))
        self.assertTrue(_plain(art[-1]).strip())
        self.assertNotIn(BOLD, art[-1])
        self.assertIn(BOLD, art[0])

    def test_the_banner_shadow_stands_outside_the_letterforms(self):
        # flood fill from the border over the faces marks the outside;
        # no shadow cell may stand on air the fill cannot reach — a
        # shadow inside a counter reads as a second stroke (decision 33)
        slots = self.banner_slots()
        art = banner_lines(slots, 96)
        face, shad = set(), set()
        for y, row in enumerate(art):
            for column, _glyph, bold in _banner_cells(row):
                (face if bold else shad).add((column, y))
        outside = set()
        stack = [(x, y) for x in range(BANNER_WIDTH)
                 for y in (0, len(art) - 1)]
        stack += [(x, y) for y in range(len(art))
                  for x in (0, BANNER_WIDTH - 1)]
        while stack:
            x, y = stack.pop()
            if not (0 <= x < BANNER_WIDTH and 0 <= y < len(art)):
                continue
            if (x, y) in outside or (x, y) in face:
                continue
            outside.add((x, y))
            stack.extend(((x + 1, y), (x - 1, y),
                          (x, y + 1), (x, y - 1)))
        self.assertTrue(shad, "the banner casts no shadow at all")
        self.assertEqual(sorted(shad - outside), [])

    def test_the_face_is_solid_block_and_the_shadow_keeps_the_bevel(self):
        # the stripped edging survives as the shadow's texture: every
        # face cell is `█`, and the shadow still spends `╗║╝╔═`
        self.assertEqual(len(BANNER_LETTERS), len(WORDMARK))
        self.assertEqual(len(BANNER_SHADOWS), len(WORDMARK))
        slots = self.banner_slots()
        face_glyphs, shad_glyphs = set(), set()
        for row in banner_lines(slots, 96):
            for _column, glyph, bold in _banner_cells(row):
                (face_glyphs if bold else shad_glyphs).add(glyph)
        self.assertEqual(face_glyphs, {"█"})
        self.assertTrue(shad_glyphs, shad_glyphs)
        self.assertTrue(shad_glyphs <= set("╗║╝╚╔═"), shad_glyphs)

    def test_the_banner_is_live(self):
        # the two rows answer to the buffer independently: editing a face
        # slot moves the letter, editing its bright moves the shadow
        slots = self.banner_slots()
        moved = dict(slots, **{BANNER_FACE_SLOTS[2]: "#ff00ff"})
        self.assertNotEqual(banner_lines(slots, 96),
                            banner_lines(moved, 96))
        self.assertIn(fg("#ff00ff"), "\n".join(banner_lines(moved, 96)))
        shaded = dict(slots, **{WORDMARK_SLOTS[2]: "#00ff00"})
        self.assertNotEqual(banner_lines(slots, 96),
                            banner_lines(shaded, 96))
        self.assertIn(fg("#00ff00"), "\n".join(banner_lines(shaded, 96)))

    def test_the_banner_fits_its_width_or_yields(self):
        # narrower than `BANNER_WIDTH` gives `[]`, and the caller falls
        # back to `wordmark` — a clipped banner is half a letter
        slots = self.slots()
        self.assertEqual(banner_lines(slots, 40), [])
        self.assertEqual(banner_lines(slots, BANNER_WIDTH - 1), [])
        art = banner_lines(slots, BANNER_WIDTH)
        self.assertEqual(len(art), 6)
        self.assertEqual(max(visible(line) for line in art), BANNER_WIDTH)
        for line in art:
            self.assertLessEqual(visible(line), BANNER_WIDTH)

    def test_the_mini_banner_is_the_pasted_block(self):
        # the strings are the source: six three-wide letters split at the
        # pasted block's single-space gap columns, so the plain art is the
        # pasted block byte-for-byte (sans trailing air)
        slots = self.slots()
        self.assertEqual([_plain(line)
                          for line in mini_banner_lines(slots, None)],
                         ["█ █ █ █ █▀▀ █▀▄ █▀█ █ █",
                          "█▀█ █ █ █▀  █▀▄ █ █  █",
                          "█ █ █▄█ █▄▄ █▄▀ █▄█ █ █"])
        self.assertEqual(MINI_WIDTH, 23)
        self.assertEqual(len(MINI_LETTERS), len(WORDMARK))

    def test_the_mini_banner_faces_wear_the_base_row_in_order(self):
        # §8.1 — letter `i` wears `BANNER_FACE_SLOTS[i]`, bold like the
        # raster banner's faces and the one-line `wordmark`
        slots = self.banner_slots()
        raw = "\n".join(mini_banner_lines(slots, 96))
        for letter, slot in zip(WORDMARK, BANNER_FACE_SLOTS):
            with self.subTest(letter=letter):
                self.assertIn(f"{BOLD}{fg(slots[slot])}", raw)

    def test_the_mini_banner_is_live(self):
        # editing a face slot moves the letter on the same frame as
        # everything else (§14.1)
        slots = self.banner_slots()
        moved = dict(slots, **{BANNER_FACE_SLOTS[2]: "#ff00ff"})
        self.assertNotEqual(mini_banner_lines(slots, 96),
                            mini_banner_lines(moved, 96))
        self.assertIn(fg("#ff00ff"),
                      "\n".join(mini_banner_lines(moved, 96)))

    def test_the_mini_banner_is_narrow_block_art(self):
        # the middle rung is block art, not ascii — but every glyph is
        # still one column, so the piped preview holds its width wherever
        # the mini banner stands in
        slots = self.slots()
        art = mini_banner_lines(slots, MINI_WIDTH)
        self.assertEqual(len(art), 3)
        for line in art:
            plain = _plain(line)
            self.assertTrue(set(plain) <= set("█▀▄ "),
                            f"stray glyph in mini banner: {plain!r}")
            for char in plain:
                self.assertNotIn(unicodedata.east_asian_width(char),
                                 ("W", "F"),
                                 f"wide glyph in mini banner: {plain!r}")

    def test_the_mini_banner_fits_its_width_or_yields(self):
        # narrower than `MINI_WIDTH` gives `[]`, and the caller falls
        # back to `wordmark` — a clipped banner is half a letter
        slots = self.slots()
        self.assertEqual(mini_banner_lines(slots, 22), [])
        self.assertEqual(mini_banner_lines(slots, MINI_WIDTH - 1), [])
        art = mini_banner_lines(slots, MINI_WIDTH)
        self.assertEqual(len(art), 3)
        self.assertEqual(max(visible(line) for line in art), MINI_WIDTH)
        for line in art:
            self.assertLessEqual(visible(line), MINI_WIDTH)

    def test_a_key_is_bright_and_its_label_is_teal(self):
        # the key is what the finger has to find; the label explains it
        hint = key_hint(self.slots(), "arrows", "move")
        self.assertEqual(hint, f"{fg(self.AMBER)}arrows{RESET} "
                               f"{fg(self.TEAL)}move{RESET}")

    def test_the_chrome_slots_are_the_ones_the_sample_already_spends(self):
        # §8 — no slot is spent twice over: the frame's three colours are
        # read out of the sample's own vocabulary, so editing one repaints
        # syntax and chrome on the same frame
        spent = {slot for slot, _ in _sample()}
        for slot in (CHROME_KEY, CHROME_LABEL, CHROME_MUTED):
            self.assertIn(slot, spent, slot)

    def test_chrome_is_a_painted_run_from_the_buffer(self):
        slots = self.slots()
        self.assertEqual(chrome("hi", CHROME_MUTED, slots),
                         f"{fg(self.GREY)}hi{RESET}")
        self.assertEqual(chrome("hi", CHROME_MUTED, slots, bold=True),
                         f"{BOLD}{fg(self.GREY)}hi{RESET}")
        # a slot the buffer has not got paints MISSING in place, never a
        # colour of its own
        self.assertEqual(chrome("hi", "palette-99", slots),
                         f"{fg(huebox.MISSING)}hi{RESET}")

    def test_a_fold_never_splits_a_key_from_its_label(self):
        # the hint line is painted a token at a time; the fold lands
        # between whole hints, so no row ends with a key looking orphaned
        whole = {f"{key} {what}" for key, what in self.HINTS}
        for cols in (120, 100, 80, 60, 40, 30):
            with self.subTest(cols=cols):
                rows = hint_line(self.slots(), self.HINTS, cols)
                self.assertTrue(rows)
                for row in rows:
                    self.assertLessEqual(visible(row), cols)
                    for part in _plain(row).split("  "):
                        self.assertIn(part.strip(), whole)


class Examples(unittest.TestCase):
    """The live examples strip (§14.1) and the live-everything property."""

    def plain(self, row):
        return _plain(row)

    def rows(self, slots, cols=None):
        return huebox.example_lines(slots, cols)

    def test_three_rows_label_their_slot_pair_and_no_hex(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        rows = self.rows(slots, 100)
        self.assertEqual(len(rows), 3)
        pairs = [pair_label("background", "foreground"),
                 pair_label("selection-background", "selection-foreground"),
                 pair_label("cursor-color", "cursor-text")]
        self.assertEqual(pairs, ["background/foreground",
                                 "selection-background/foreground",
                                 "cursor-color/text"])
        for row, label in zip(rows, pairs):
            self.assertIn(label, self.plain(row))
            # the colour *is* the readout: no hex column in the strip
            self.assertNotIn("#", self.plain(row))

    def test_pair_label_shares_one_prefix(self):
        # the shared prefix is printed once; no shared prefix prints both
        self.assertEqual(pair_label("background", "foreground"),
                         "background/foreground")
        self.assertEqual(
            pair_label("selection-background", "selection-foreground"),
            "selection-background/foreground")
        self.assertEqual(pair_label("cursor-color", "cursor-text"),
                         "cursor-color/text")

    def test_the_label_column_fits_the_longest_pair(self):
        # a longer slot name must not break the alignment the widths promise
        pairs = [pair_label("background", "foreground"),
                 pair_label("selection-background", "selection-foreground"),
                 pair_label("cursor-color", "cursor-text")]
        self.assertEqual(max(len(p) for p in pairs), PAIR_WIDTH - 1)
        self.assertEqual(len("selection-background"), LABEL_WIDTH - 1)

    def test_narrow_rows_fall_back_to_the_short_names(self):
        # below PAIR_MIN_COLS the pair names would eat the sentence, so the
        # single slot name comes back and the sample gets the columns
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        below = self.rows(slots, PAIR_MIN_COLS - 1)
        at = self.rows(slots, PAIR_MIN_COLS)
        self.assertIn("selection-background  ", self.plain(below[1]))
        self.assertIn("selection-background/foreground ", self.plain(at[1]))
        for row in below:
            self.assertNotIn("/", self.plain(row))   # short names only

    def test_the_three_rows_carry_the_six_named_colours(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        text = "\n".join(rows for rows in (self.plain(r)
                                          for r in self.rows(slots, 100)))
        self.assertIn("The quick brown fox", text)     # background row
        self.assertIn(SELECTED_TEXT, text)              # selection row
        self.assertIn(CURSOR_CHAR, text)                # under the cursor

    def test_selection_text_rides_on_selection_background(self):
        # the selected run of words sits inside the sentence, in
        # selection-foreground on the selection background
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        row = self.rows(slots, 100)[1]
        self.assertIn(f"{bg('#2a2a34')}{fg('#f0f0f8')}{SELECTED_TEXT}", row)
        self.assertIn(f"{bg('#101014')}{fg('#e6e6ea')}The ", row)
        self.assertIn(f"{bg('#101014')}{fg('#e6e6ea')} jumps over", row)

    def test_cursor_text_rides_on_cursor_color(self):
        # the cursor cell carries the character it covers: cursor-text on a
        # cursor-color background, one cell wide, inside the sentence
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        row = self.rows(slots, 100)[2]
        self.assertEqual(len(CURSOR_CHAR), 1)
        self.assertIn(f"{bg('#101014')}{fg('#e6e6ea')}The quic{chr(27)}[0m",
                      row)
        self.assertIn(f"{bg('#e6e6ea')}{fg('#101014')}{CURSOR_CHAR}{chr(27)}[0m",
                      row)
        # the rest of the sentence stays on the theme background
        self.assertIn(f"{bg('#101014')}{fg('#e6e6ea')} brown fox", row)

    def test_the_pair_names_are_the_themes_own_text_colour(self):
        # §8.1 — the label names the row but takes no colour of its own: it
        # is the theme's foreground, so the sentence it introduces stays the
        # loudest thing on the row
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        text = fg(slots["foreground"])
        for row, label in zip(self.rows(slots, 100), (
                "background/foreground",
                "selection-background/foreground",
                "cursor-color/text")):
            self.assertTrue(row.startswith("  " + text + label), row[:60])

    def test_the_row_fill_is_the_background_not_missing(self):
        # the fill is a resolved value: looking it up again as a slot name
        # would pad every row with the MISSING grey
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        rows = self.rows(slots, 100)
        for row in rows:
            self.assertNotIn(huebox.MISSING, self.plain(row))
        # §14.1 — what is left of a row is padded in the buffer's
        # background: the fill is the row filling the width it was given,
        # not the demonstrated colour bleeding past the sample
        pad = 100 - (PAIR_WIDTH + 3) - len(EXAMPLE_PHRASE)
        for row in rows:
            self.assertTrue(row.endswith(f"{bg('#101014')}{' ' * pad}{RESET}"),
                            row[-40:])

    def test_the_selection_run_stops_at_the_last_selected_word(self):
        # a demonstrated colour is exactly the span that demonstrates it:
        # the selected run ends with the words, and the rest of the
        # sentence — and of the frame — is background, so nothing reads as
        # selected past the text
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        row = self.rows(slots, 100)[1]
        self.assertEqual(row.count(bg("#2a2a34")), 1)      # the run alone
        after = row[row.index(SELECTED_TEXT) + len(SELECTED_TEXT):]
        self.assertNotIn(bg("#2a2a34"), after)
        self.assertIn(bg("#101014"), after)         # the tail, then the fill

    def test_one_slot_change_moves_its_row(self):
        # §14.1 — nothing is cached between calls: the rows that own a slot
        # change the moment that slot changes in the buffer, and no other
        # row moves. All three rows show the sample sentence, so
        # background and foreground reach every row.
        base = {name: "#3f7a3f" for name in huebox.SLOTS}
        owners = {"background": {0, 1, 2}, "foreground": {0, 1, 2},
                  "selection-background": {1}, "selection-foreground": {1},
                  "cursor-color": {2}, "cursor-text": {2}}
        for name, rows in owners.items():
            with self.subTest(slot=name):
                before = self.rows(base, 100)
                after = self.rows(dict(base, **{name: "#ff00ff"}), 100)
                for row in range(3):
                    if row in rows:
                        self.assertNotEqual(before[row], after[row])
                    else:
                        self.assertEqual(before[row], after[row])

    def test_a_changed_buffer_changes_every_row(self):
        dark = {name: "#101014" for name in huebox.SLOTS}
        bright = {name: "#f0f0f8" for name in huebox.SLOTS}
        first = self.rows(dark, 100)
        for index in range(3):
            self.assertNotEqual(first[index], self.rows(bright, 100)[index])
        # no colour survives a frame: back to the first buffer, same pixels
        self.assertEqual(first, self.rows(dark, 100))

    def test_rows_never_exceed_the_width(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        for cols in (120, 100, 80, 60, 40, 38, 30, 24, 20, 10):
            with self.subTest(cols=cols):
                for row in self.rows(slots, cols):
                    self.assertLessEqual(len(self.plain(row)), cols)

    def test_without_a_width_the_rows_are_labelled_but_unfilled(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        labels = [self.plain(row).split()[0] for row in self.rows(slots)]
        self.assertEqual(labels, [pair_label("background", "foreground"),
                                  pair_label("selection-background",
                                             "selection-foreground"),
                                  pair_label("cursor-color", "cursor-text")])
        # no fill padding: the rows stop where the sample text stops
        self.assertEqual(self.plain(self.rows(slots)[1]).strip(),
                         "selection-background/foreground  " + EXAMPLE_PHRASE)

    def test_missing_slots_render_as_grey(self):
        rows = self.rows({}, 80)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIn(bg(huebox.MISSING), row)

    def test_the_cursor_cell_survives_the_narrowest_editor_width(self):
        # tui.MIN_COLS is 40 and the editor spends two columns on padding:
        # the covered character must still be drawn there, not folded away
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        row = self.rows(slots, render.MIN_COLS - 2)[2]
        self.assertIn(f"{bg('#e6e6ea')}{fg('#101014')}{CURSOR_CHAR}", row)

    def test_rows_are_ascii(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        for row in self.rows(slots, 100):
            self.assertTrue(all(ord(ch) < 128 for ch in self.plain(row)),
                            f"non-ascii in examples row: {row!r}")


class Diff(unittest.TestCase):
    """The live diff hunk: removed red, added green (§14.4)."""

    def plain(self, row):
        return _plain(row)

    def rows(self, slots, cols=None):
        return huebox.diff_lines(slots, cols)

    def test_the_hunk_is_a_git_hunk(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        rows = [self.plain(row) for row in self.rows(slots, 100)]
        self.assertEqual(len(rows), len(DIFF_BODY) + 1)
        self.assertEqual(rows[0], "    " + " ".join(DIFF_HUNK))
        self.assertTrue(rows[1].strip().startswith("-"))
        self.assertTrue(rows[2].strip().startswith("+"))

    def test_the_signs_wear_the_base_red_and_green(self):
        # the two loudest slots in a diff are the ones a lexer has no use
        # for (§8): the sample spends neither
        self.assertEqual((DIFF_REMOVED, DIFF_ADDED), ("palette-1", "palette-2"))
        self.assertEqual(DIFF_MARKS, "palette-6")
        self.assertEqual(DIFF_CONTEXT, "palette-8")
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({DIFF_REMOVED: "#ff0000", DIFF_ADDED: "#00ff00",
                      DIFF_MARKS: "#00ffff", DIFF_CONTEXT: "#808080"})
        rows = self.rows(slots, 100)
        self.assertEqual(rows[0], "    " + fg("#00ffff") + "@@" + RESET
                         + fg("#808080")
                         + " -6,2 +6,2 @@ pub fn main() !void {" + RESET)
        self.assertEqual(rows[1],
                         "    " + fg("#ff0000") + "-" + DIFF_BODY[0][1]
                         + RESET)
        self.assertEqual(rows[2],
                         "    " + fg("#00ff00") + "+" + DIFF_BODY[1][1]
                         + RESET)

    def test_one_slot_change_moves_only_the_lines_that_wear_it(self):
        # §14.1 — like every other live widget the diff reads the buffer on
        # the call: red moves the removed line and nothing else
        base = {name: "#3f7a3f" for name in huebox.SLOTS}
        after = dict(base, **{DIFF_REMOVED: "#ff00ff"})
        for index in (1, 3):           # the two removed lines
            self.assertNotEqual(self.rows(base, 100)[index],
                                self.rows(after, 100)[index])
        for index in (0, 2, 4):        # header, added line, added line
            self.assertEqual(self.rows(base, 100)[index],
                             self.rows(after, 100)[index])

    def test_a_changed_buffer_changes_every_row(self):
        dark = {name: "#101014" for name in huebox.SLOTS}
        bright = {name: "#f0f0f8" for name in huebox.SLOTS}
        first = self.rows(dark, 100)
        for index, row in enumerate(first):
            self.assertNotEqual(row, self.rows(bright, 100)[index])
        self.assertEqual(first, self.rows(dark, 100))

    def test_the_rows_fit_the_default_terminal(self):
        # the widest row is the print call; the editor clips at cols - 2
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        for row in self.rows(slots, 100):
            self.assertLessEqual(len(self.plain(row)), 78)

    def test_rows_never_exceed_the_width(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        for cols in (120, 80, 60, 40, 38, 30, 24, 20, 10):
            with self.subTest(cols=cols):
                for row in self.rows(slots, cols):
                    self.assertLessEqual(len(self.plain(row)), cols)

    def test_missing_slots_render_as_grey(self):
        rows = self.rows({}, 80)
        self.assertTrue(all(fg(huebox.MISSING) in row for row in rows))

    def test_rows_are_ascii(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        for row in self.rows(slots, 100):
            self.assertTrue(all(ord(ch) < 128 for ch in self.plain(row)),
                            f"non-ascii in diff row: {row!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

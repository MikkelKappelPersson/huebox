"""Layout tests: clipping, wide glyphs, the examples strip and the preview grid."""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402
from huebox.color import (hex_to_rgb, hsv_to_rgb,  # noqa: E402
                         readable_fg, rgb_to_hex, rgb_to_hsv)
from huebox.render import (BOLD, CALL_SLOT, CHROME_KEY,  # noqa: E402
                           CHROME_LABEL, CHROME_MUTED, CURSOR_CHAR, DIFF_ADDED,
                           DIFF_BODY, DIFF_CONTEXT, DIFF_HUNK, DIFF_MARKS,
                           DIFF_REMOVED, EXAMPLE_PHRASE, HSV_COMPACT,
                           HSV_FULL, HSV_LABELS, MARK,
                           LABEL_WIDTH, PAIR_MIN_COLS,
                           PAIR_WIDTH, SELECTED_TEXT, TOKEN_SLOTS, WORDMARK,
                           WORDMARK_SLOTS, _sample, backdrop, bg, chrome, fg,
                           hint_line, hsv_numbers, hsv_readout, key_hint,
                           pack,
                           pair_label, title, visible, wordmark)
from huebox.render import _marker  # noqa: E402
from huebox.render import RESET  # noqa: E402


def _plain(text):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


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

    def test_preview_is_pure_ascii(self):
        slots = {name: "#ff8800" for name in huebox.SLOTS}
        text = huebox.render_preview("ghostty", "/tmp/x", slots, cols=120, rows=40)
        body = text
        for line in body.split("\n"):
            self.assertTrue(all(ord(ch) < 128 for ch in line),
                            f"non-ascii in preview: {line!r}")

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
    """The slot's reading: three bars, each wearing its own value (§8.3)."""

    SLOTS = {"background": "#101014", "foreground": "#e6e6ea",
             "palette-11": "#e0c06c", "palette-8": "#d0d0d8",
             "cursor-color": "#ef707a", "cursor-text": "#1c1f26"}
    # one chip cell: its background, its foreground, its one character
    CELL = re.compile(r"\x1b\[48;2;(\d+);(\d+);(\d+)m"
                      r"\x1b\[38;2;(\d+);(\d+);(\d+)m(.)")
    WIDE = HSV_LABELS + sum(HSV_FULL)

    def cells(self, line):
        """Every chip cell of the readout: (bg, fg, character), in order."""
        return [(tuple(int(v) for v in (r, g, b)),
                 tuple(int(v) for v in (fr, fg, fb)), char)
                for r, g, b, fr, fg, fb, char in self.CELL.findall(line)]

    def chip(self, line, index):
        """One bar's cells, by position in the readout."""
        cells, at = self.cells(line), 0
        for width in HSV_FULL[:index]:
            at += width
        return cells[at:at + HSV_FULL[index]]

    def worn(self, bar):
        """The digits a bar shows: its number, less what the line took."""
        line = set(self.marks(bar))
        return "".join(char for i, (_, _, char) in enumerate(bar)
                       if char != " " and i not in line)

    def marks(self, cells):
        """Where the hairline is: the one cell wearing the mark."""
        return [i for i, (_, _, char) in enumerate(cells) if char == MARK]

    CLEAR = "#1c1c1c"          # grey: every hairline is clear of a number

    def test_a_wide_room_gives_the_three_bars_with_their_values(self):
        cells = self.cells(hsv_readout(self.SLOTS, self.CLEAR, self.WIDE))
        self.assertEqual(len(cells), sum(HSV_FULL))
        at = 0
        for width, value in zip(HSV_FULL, ("0°", "0%", "11%")):
            bar = cells[at:at + width]
            with self.subTest(value=value):
                self.assertEqual(self.worn(bar), value)
            at += width

    def test_every_bar_is_a_sweep_of_its_whole_axis(self):
        # the hue bar is the wheel at the slot's own sat/val, the sat bar
        # grey -> colour, the val bar black -> colour, each of the last two
        # painted at the slot's own hue: the bar is the axis, and the
        # reading is what the number and the block say (§8.3)
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
                wanted = [hex_to_rgb(rgb_to_hex(colour(i / (width - 1))))
                          for i in range(width)]
                for i, (cell_bg, _, _) in enumerate(bar):
                    self.assertEqual(cell_bg, wanted[i], i)
            at += width

    def test_the_value_is_centred_on_its_bar(self):
        # the cells the number wears are the middle ones — measured on a
        # colour whose hairlines are clear of them, since where the number
        # sits and where the line falls are two different questions (§8.3)
        cells = self.cells(hsv_readout(self.SLOTS, self.CLEAR, self.WIDE))
        at = 0
        for width, value in zip(HSV_FULL, ("0°", "0%", "11%")):
            bar = cells[at:at + width]
            line = set(self.marks(bar))
            worn = [i for i in range(width)
                    if bar[i][2] != " " and i not in line]
            with self.subTest(value=value):
                self.assertEqual("".join(bar[i][2] for i in worn), value)
                middle = (worn[0] + worn[-1]) / 2
                self.assertAlmostEqual(middle, (width - 1) / 2, delta=0.5)
            at += width

    def test_a_number_takes_one_ink_for_the_whole_number(self):
        # per-character ink turns a sweep into two numbers: the digits go
        # light on the dark half and dark on the light half, and the number
        # stops reading as one thing (§8.3). The digits the block is
        # carrying are the block's own ink, by design — cursor-text on
        # cursor-color, which is what a terminal does with a cursor
        line = hsv_readout(self.SLOTS, "#61afef", self.WIDE)
        cells = self.cells(line)
        at = 0
        for width in HSV_FULL:
            with self.subTest(width=width):
                mark = set(self.marks(cells[at:at + width]))
                worn = [cells[at + i][1] for i in range(width)
                        if cells[at + i][2] != " " and i not in mark]
                self.assertTrue(worn)
                self.assertEqual(len(set(worn)), 1)
                # and it is the readable side of the cells it sits on
                covered = [cells[at + i][0] for i in range(width)
                           if cells[at + i][2] != " "]
                average = tuple(sum(rgb[i] for rgb in covered) / len(covered)
                                for i in range(3))
                self.assertEqual(worn[0],
                                 hex_to_rgb(readable_fg(
                                     rgb_to_hex(average))))
            at += width

    def test_the_ink_is_legible_whatever_the_axis_is_doing(self):
        for value in ("#61afef", "#ffff00", "#ffffff", "#000000", "#808080",
                      "#010203"):
            line = hsv_readout(self.SLOTS, value, self.WIDE)
            with self.subTest(value=value):
                for _, cell_fg, _ in self.cells(line):
                    self.assertIn(cell_fg, ((0, 0, 0), (255, 255, 255)))
        # no slot colour reaches a bar: the gradient and its ink are both
        # computed, because no slot holds a sweep
        self.assertNotIn(fg(self.SLOTS["palette-11"]), line)
        self.assertNotIn("\033[1m", line)          # no bold: §8.1
        self.assertNotIn("\033[2m", line)          # no dim either

    def test_every_bar_carries_one_hairline_and_nothing_else(self):
        # a line, not a stripe: one cell, one glyph, an eighth of it ink,
        # always inside the bar
        for value in ("#61afef", "#ff0000", "#ffffff", "#000000", "#f5f5dc",
                      "#010203"):
            cells = self.cells(hsv_readout(self.SLOTS, value, self.WIDE))
            at = 0
            for axis, width in enumerate(HSV_FULL):
                bar = cells[at:at + width]
                with self.subTest(value=value, width=width):
                    self.assertEqual(len(bar), width)
                    want = min(width - 1, max(0, int(self.reading(value, axis)
                                                    * (width - 1) + 0.5)))
                    self.assertEqual(self.marks(bar), [want])
                    line = self.marks(bar)[0]
                    self.assertEqual(bar[line][2], MARK)
                    # the line is readable wherever the sweep puts it
                    self.assertEqual(bar[line][1],
                                     hex_to_rgb(readable_fg(rgb_to_hex(
                                         bar[line][0]))))
                at += width

    def reading(self, value, axis):
        """The slot's own reading of one axis, 0..1."""
        return rgb_to_hsv(hex_to_rgb(value))[axis]

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

    def test_the_line_takes_the_one_character_it_shares(self):
        # the price of a line thinner than a character: a cell holds one
        # of them, so a reading inside the number costs one digit — which is
        # why the exact reading is on the row below (§8.3)
        cells = self.cells(hsv_readout(self.SLOTS, "#61afef", self.WIDE))
        at, crossed = 0, []
        for width, value in zip(HSV_FULL, ("207°", "59%", "94%")):
            bar = cells[at:at + width]
            line = set(self.marks(bar))
            first = max(0, (width - len(value) + 1) // 2)
            number = set(range(first, first + len(value)))
            worn = [i for i in range(width)
                    if bar[i][2] != " " and i not in line]
            taken = sorted(line & number)
            with self.subTest(value=value):
                self.assertLessEqual(len(taken), 1)
                # every digit still on screen is the digit that belongs
                # there, and exactly the one the line took is missing
                self.assertEqual(
                    [bar[i][2] for i in worn],
                    [value[i - first] for i in range(width)
                     if bar[i][2] != " " and i not in line])
                self.assertEqual(len(worn), len(value) - len(taken))
            crossed += taken
            at += width
        # the `7` of `207°` under the hue line, the `%` of `59%` under the
        # saturation one
        self.assertEqual(crossed, [8, 5])

    def test_the_bars_follow_the_reading_they_are_about(self):
        # `q` moves the hue: the number on the hue bar moves with it, the
        # wheel itself does not (it is the whole wheel, at the slot's own
        # sat and val), and the other two bars — which are painted at the
        # slot's hue — do (§14.1)
        before = hsv_readout(self.SLOTS, "#61afef", self.WIDE)
        after = hsv_readout(self.SLOTS, "#ef8b61", self.WIDE)   # 27°, same s/v
        self.assertNotEqual(_plain(before), _plain(after))   # 207° -> 27°
        self.assertNotEqual(before, after)                   # the cells moved
        # the wheel itself is the whole wheel: the same colour in the same
        # cell, under both readings — what moves is the block and the number
        self.assertEqual([cell[0] for cell in self.chip(after, 0)],
                         [cell[0] for cell in self.chip(before, 0)])
        self.assertNotEqual(self.marks(self.chip(before, 0)),
                            self.marks(self.chip(after, 0)))
        # the other two bars are painted at the slot's hue, so they do move
        self.assertNotEqual([cell[0] for cell in self.chip(before, 1)],
                            [cell[0] for cell in self.chip(after, 1)])
        # the same call twice is the same pixels
        self.assertEqual(before, hsv_readout(self.SLOTS, "#61afef", self.WIDE))

    def test_the_ladder_and_never_a_rung_cut_after_the_fact(self):
        self.assertEqual(
            len(self.cells(hsv_readout(self.SLOTS, "#61afef", self.WIDE))),
            sum(HSV_FULL))
        compact = hsv_readout(self.SLOTS, "#61afef", self.WIDE - 8)
        self.assertEqual(len(self.cells(compact)), sum(HSV_COMPACT))
        # below the compact bars there is no reading on this row at all —
        # the exact one belongs to the row below (§8.3)
        self.assertEqual(hsv_readout(self.SLOTS, "#61afef", 40), "")
        for value in ("#61afef", "#000000", "#ffffff", "#808080", "#010203"):
            for cols in range(0, self.WIDE + 4):
                line = hsv_readout(self.SLOTS, value, cols)
                self.assertLessEqual(visible(line), cols, (value, cols))


class Typography(unittest.TestCase):
    """The frame's own vocabulary: keys, labels, muted furniture (§8.1)."""

    HINTS = [("arrows", "move"), ("q/w", "hue"), ("a/s", "sat"),
             ("z/x", "light"), ("f", "x5"), ("i", "hex"), ("^S", "save"),
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
        row = self.rows(slots, huebox.tui.MIN_COLS - 2)[2]
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

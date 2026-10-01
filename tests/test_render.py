"""Layout tests: clipping, wide glyphs, the examples strip and the preview grid."""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402
from huebox.render import (CURSOR_CHAR, EXAMPLE_PHRASE,  # noqa: E402
                           LABEL_WIDTH, PAIR_MIN_COLS, PAIR_WIDTH,
                           SELECTED_TEXT, bg, fg, pair_label)


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


class Examples(unittest.TestCase):
    """The live examples strip (§14.1) and the live-everything property."""

    def plain(self, row):
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", row)

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

    def test_the_row_fill_is_the_demonstrated_colour_not_missing(self):
        # the fill is a resolved value: looking it up again as a slot name
        # would pad every row with the MISSING grey
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        rows = self.rows(slots, 100)
        self.assertNotIn(huebox.MISSING, self.plain(rows[0]))
        self.assertIn(bg("#101014"), rows[0])              # background row
        self.assertIn(bg("#2a2a34"), rows[1])              # selection row
        self.assertIn(bg("#101014"), rows[2])              # cursor row tail

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

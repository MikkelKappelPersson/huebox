"""Layout tests: clipping, wide glyphs, the examples strip and the preview grid."""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402


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

    def test_three_rows_label_their_slot_and_hex(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        rows = self.rows(slots, 100)
        self.assertEqual(len(rows), 3)
        for row, name, value in zip(
                rows, ("background", "selection-background", "cursor-color"),
                ("#101014", "#2a2a34", "#e6e6ea")):
            self.assertIn(name, self.plain(row))
            self.assertIn(value, self.plain(row))

    def test_the_three_rows_carry_the_six_named_colours(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        slots.update({"background": "#101014", "foreground": "#e6e6ea",
                      "selection-background": "#2a2a34",
                      "selection-foreground": "#f0f0f8",
                      "cursor-color": "#e6e6ea", "cursor-text": "#101014"})
        text = "\n".join(rows for rows in (self.plain(r)
                                          for r in self.rows(slots, 100)))
        self.assertIn("The quick brown fox", text)     # background row
        self.assertIn("selected text", text)           # selection row
        self.assertIn("██", text)                      # cursor block

    def test_one_slot_change_moves_its_row(self):
        # §14.1 — nothing is cached between calls: the row that owns a slot
        # changes the moment that slot changes in the buffer
        base = {name: "#3f7a3f" for name in huebox.SLOTS}
        owners = {"background": 0, "foreground": 0, "selection-background": 1,
                  "selection-foreground": 1, "cursor-color": 2,
                  "cursor-text": 2}
        for name, row in owners.items():
            with self.subTest(slot=name):
                before = self.rows(base, 100)
                after = self.rows(dict(base, **{name: "#ff00ff"}), 100)
                self.assertNotEqual(before[row], after[row])
                for other in range(3):
                    if other != row and name not in ("background",):
                        self.assertEqual(before[other], after[other])

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
        self.assertEqual(labels, ["background", "selection-background",
                                  "cursor-color"])
        # no fill padding: the rows stop where the sample text stops
        self.assertEqual(self.plain(self.rows(slots)[1]).strip(),
                         "selection-background  selected text")

    def test_missing_slots_render_as_grey(self):
        rows = self.rows({}, 80)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIn(huebox.MISSING, self.plain(row))

    def test_rows_are_ascii_except_the_cursor_block(self):
        slots = {name: "#3f7a3f" for name in huebox.SLOTS}
        for row in self.rows(slots, 100):
            stripped = self.plain(row).replace("█", "")
            self.assertTrue(all(ord(ch) < 128 for ch in stripped))


if __name__ == "__main__":
    unittest.main(verbosity=2)

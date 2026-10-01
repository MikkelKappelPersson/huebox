"""Round-trip tests: every format must read all 22 slots and write them back
without touching a single unrelated byte."""

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402

GHOSTTY = """\
# a comment that must survive
font-family = "Some Font"

palette = 0=#0a0a13
palette = 1=#ff0067
palette = 15=#f8f8ff

background = #0f0f1a
foreground = #ededfe
cursor-color = #ededfe
cursor-text = #0f0f1a
selection-background = #202036
selection-foreground = #ededff
"""

KITTY = """\
# kitty theme
font_family      SauceCodePro Nerd Font

background            #0f0f1a
foreground            #ededfe
cursor                #ededfe
selection_background  #202036
selection_foreground  #ededff
color0  #0a0a13
color1  #ff0067
color15 #f8f8ff
"""

ALACRITTY_TOML = """\
# alacritty config
[general]
live_config_reload = true

[colors.primary]
background = "#0f0f1a"
foreground = "#ededfe"

[colors.cursor]
text    = "#0f0f1a"
cursor  = "#ededfe"

[colors.selection]
background = "#202036"
foreground = "#ededff"

[colors.normal]
"0"  = "#0a0a13"
"1"  = "#ff0067"
"15" = "#f8f8ff"
"""

ALACRITTY_DOTTED = """\
[colors]
primary = { background = "#0f0f1a", foreground = "#ededfe" }
normal = { "0" = "#0a0a13" }
"""

CASES = [
    ("ghostty", GHOSTTY, {"palette-0", "palette-1", "palette-15", "background",
                          "foreground", "cursor-color", "cursor-text",
                          "selection-background", "selection-foreground"}),
    ("kitty", KITTY, {"palette-0", "palette-1", "palette-15", "background",
                      "foreground", "cursor-color", "selection-background",
                      "selection-foreground"}),
    ("alacritty", ALACRITTY_TOML, {"palette-0", "palette-1", "palette-15",
                                    "background", "foreground", "cursor-color",
                                    "cursor-text", "selection-background",
                                    "selection-foreground"}),
]


class RoundTrip(unittest.TestCase):
    def _write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False)
        handle.write(text)
        handle.close()
        return handle.name

    def _read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_reads_every_expected_slot(self):
        for fmt, text, expected in CASES:
            with self.subTest(format=fmt):
                path = self._write(text)
                slots = huebox.FORMATS[fmt]["read"](path)
                os.unlink(path)
                missing = expected - set(slots)
                self.assertEqual(missing, set(),
                                 f"{fmt} did not read {sorted(missing)}")

    def test_values_are_correct(self):
        for fmt, text, _ in CASES:
            with self.subTest(format=fmt):
                path = self._write(text)
                slots = huebox.FORMATS[fmt]["read"](path)
                os.unlink(path)
                self.assertEqual(slots.get("background"), "#0f0f1a")
                self.assertEqual(slots.get("palette-0"), "#0a0a13")
                self.assertEqual(slots.get("palette-15"), "#f8f8ff")

    def test_write_is_a_no_op_when_nothing_changed(self):
        for fmt, text, _ in CASES:
            with self.subTest(format=fmt):
                path = self._write(text)
                slots = huebox.FORMATS[fmt]["read"](path)
                # an old stamp makes a rewrite impossible to miss: §6.2
                # rule 4 says a no-op must not touch the file *at all*,
                # which is a promise about mtime, not about bytes
                stamp = os.path.getmtime(path) - 60
                os.utime(path, (stamp, stamp))
                huebox.FORMATS[fmt]["write"](path, slots)
                self.assertEqual(self._read(path), text,
                                 f"{fmt} no-op write changed the file")
                self.assertEqual(os.path.getmtime(path), stamp,
                                 f"{fmt} no-op write rewrote the file")
                os.unlink(path)

    def test_write_changes_only_colour_lines(self):
        for fmt, text, _ in CASES:
            with self.subTest(format=fmt):
                path = self._write(text)
                slots = huebox.FORMATS[fmt]["read"](path)
                slots["background"] = "#010203"
                slots["palette-0"] = "#040506"
                huebox.FORMATS[fmt]["write"](path, slots)
                after = self._read(path)
                os.unlink(path)
                self.assertIn("#010203", after)
                self.assertIn("#040506", after)
                before_lines = text.splitlines()
                after_lines = after.splitlines()
                self.assertEqual(len(before_lines), len(after_lines))
                changed = [i for i, (a, b)
                           in enumerate(zip(before_lines, after_lines)) if a != b]
                self.assertEqual(len(changed), 2,
                                 f"{fmt} touched unexpected lines: "
                                 f"{[before_lines[i] for i in changed]}")

    def test_comments_survive_edits(self):
        for fmt, text, _ in CASES:
            with self.subTest(format=fmt):
                path = self._write(text)
                slots = huebox.FORMATS[fmt]["read"](path)
                slots["foreground"] = "#ffffff"
                huebox.FORMATS[fmt]["write"](path, slots)
                after = self._read(path)
                os.unlink(path)
                self.assertIn("# a comment that must survive"
                              if fmt == "ghostty"
                              else ("# kitty theme" if fmt == "kitty"
                                    else "# alacritty config"), after)

    def test_alacritty_dotted_style(self):
        path = self._write(ALACRITTY_DOTTED)
        slots = huebox.FORMATS["alacritty"]["read"](path)
        os.unlink(path)
        self.assertEqual(slots.get("background"), "#0f0f1a")
        self.assertEqual(slots.get("palette-0"), "#0a0a13")


if __name__ == "__main__":
    unittest.main(verbosity=2)

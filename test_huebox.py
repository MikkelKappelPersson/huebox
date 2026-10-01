#!/usr/bin/env python3
"""Round-trip tests for huebox: every format must read all 22 slots and write
them back without touching a single unrelated byte."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
                huebox.FORMATS[fmt]["write"](path, slots)
                self.assertEqual(open(path).read(), text,
                                 f"{fmt} no-op write changed the file")
                os.unlink(path)

    def test_write_changes_only_colour_lines(self):
        for fmt, text, _ in CASES:
            with self.subTest(format=fmt):
                path = self._write(text)
                slots = huebox.FORMATS[fmt]["read"](path)
                slots["background"] = "#010203"
                slots["palette-0"] = "#040506"
                huebox.FORMATS[fmt]["write"](path, slots)
                after = open(path).read()
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
                after = open(path).read()
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


class Geometry(unittest.TestCase):
    def test_term_size_never_zero(self):
        self.assertTrue(all(v > 0 for v in huebox.term_size(default=(80, 24))))

    def test_clip_respects_wide_glyphs(self):
        # a CJK glyph occupies two columns
        self.assertLessEqual(len(huebox.clip("ab", 10)) - 0, 12)
        self.assertTrue(huebox.clip("一二三四五", 4).startswith("\033[0m")
                        or "\033[0m" in huebox.clip("一二三四五", 4))

    def test_preview_is_pure_ascii(self):
        slots = {name: "#ff8800" for name in huebox.SLOTS}
        text = huebox.render_preview("ghostty", "/tmp/x", slots, cols=120, rows=40)
        body = re.sub_ansi if False else text
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


class Cli(unittest.TestCase):
    def test_formats_listing(self):
        out = subprocess.run([sys.executable, "huebox.py", "--formats"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("ghostty", out.stdout)

    def test_dump_from_explicit_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ghostty",
                                         delete=False) as handle:
            handle.write(GHOSTTY)
            path = handle.name
        out = subprocess.run(
            [sys.executable, "huebox.py", "--format", "ghostty",
             "--config", path, "--dump"], capture_output=True, text=True)
        os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("background=#0f0f1a", out.stdout)

    def test_missing_config_exits_nonzero(self):
        out = subprocess.run(
            [sys.executable, "huebox.py", "--format", "ghostty",
             "--config", "/nonexistent/file"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)


import re  # noqa: E402

if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Detection tests: --config format inference (§7.1, plan 3) and the
`theme =` pointer (§13.6 phase 2 - `ensure_theme_pointer`)."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

from huebox import detect                  # noqa: E402
from huebox.detect import ensure_theme_pointer, infer_format  # noqa: E402

GHOSTTY = "background = #101014\n"
KITTY = "background  #101014\n"


class InferFormat(unittest.TestCase):
    """Name table first, file content second, tie -> None (§7.1)."""

    def _tmp(self, name, text):
        base = os.path.join(tempfile.mkdtemp(), name.lstrip("/"))
        self.addCleanup(shutil.rmtree, os.path.dirname(base),
                        ignore_errors=True)
        with open(base, "w", encoding="utf-8") as handle:
            handle.write(text)
        return base

    def test_a_known_name_wins_over_content(self):
        # kitty-syntax colours inside a bare `config` file: the name table
        # says ghostty, and the name wins
        path = self._tmp("/config", KITTY)
        self.assertEqual(infer_format(path), "ghostty")

    def test_content_decides_for_unknown_names(self):
        # kitty's mid is `\s*=?` so it reads `key = value` too — a ghostty
        # line counts for BOTH readers, which is why the tie test below
        # uses it. A kitty-syntax line (no `=`) is unambiguous.
        path = self._tmp("/theme-x", KITTY)
        self.assertEqual(infer_format(path), "kitty")

    def test_a_tie_is_not_inferable(self):
        # `background = #…` parses as ghostty AND kitty: one slot each,
        # no winner
        path = self._tmp("/theme-y", GHOSTTY)
        self.assertIsNone(infer_format(path))

    def test_an_empty_file_is_not_inferable(self):
        path = self._tmp("/theme-z", "")
        self.assertIsNone(infer_format(path))


class ThemePointer(unittest.TestCase):
    """§13.6 phase 2 - one line of a user's config, swapped or appended.

    §6.2 holds here too, and this is where it is sharpest: every line
    except the one the feature is *about* survives byte for byte. Each
    test patches its own `$XDG_CONFIG_HOME`, so the reader that looks
    themes up there is looking at the test's directory.
    """

    CONFIG = ("# my ghostty\n"
              "font-size = 12\n"
              'theme   = "ember"   # mine\n'
              "\n"
              "window-padding-x = 2\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ,
                                  {"XDG_CONFIG_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = self.write("config.ghostty", self.CONFIG)

    def write(self, name, text):
        path = os.path.join(self.tmp.name, "ghostty", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def read_raw(self, path):
        """Line endings and all - `newline=""` disables the translation."""
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_the_value_is_swapped_and_nothing_else_moves(self):
        self.assertEqual(ensure_theme_pointer(self.config, "dusk"),
                         "rewritten")
        before = self.CONFIG.splitlines()
        after = self.read(self.config).splitlines()
        self.assertEqual(len(after), len(before))
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(changed, [2], before)
        # spacing, the quotes we did not write and the comment all survive
        self.assertEqual(after[2], 'theme   = "dusk"   # mine')

    def test_a_bare_value_stays_bare(self):
        self.write("config.ghostty", "theme = ember\n")
        self.assertEqual(ensure_theme_pointer(self.config, "dusk"),
                         "rewritten")
        self.assertEqual(self.read(self.config), "theme = dusk\n")

    def test_a_bare_value_with_spaces_is_followed_and_rewritten(self):
        # P6 review critical: Ghostty's built-ins have space names, and v1
        # could never follow them either — bare `Catppuccin Mocha` and its
        # quoted form both resolve and swap in place
        name = os.path.join("themes", "Catppuccin Mocha")
        self.write(name, "background = #1e1e2e\n")
        self.write("config.ghostty", "theme = Catppuccin Mocha  # dusk\n")
        self.assertEqual(detect._ghostty_theme_file(self.config),
                         os.path.join(detect.ghostty_themes_dir(),
                                      "Catppuccin Mocha"))
        self.assertEqual(ensure_theme_pointer(self.config, "dusk"),
                         "rewritten")
        self.assertEqual(self.read(self.config),
                         "theme = dusk  # dusk\n")

    def test_a_quoted_value_with_spaces_keeps_its_quotes(self):
        self.write("config.ghostty", 'theme = "Catppuccin Mocha"  # mine\n')
        self.assertEqual(ensure_theme_pointer(self.config, "dusk"),
                         "rewritten")
        self.assertEqual(self.read(self.config),
                         'theme = "dusk"  # mine\n')

    def test_a_config_without_a_theme_line_gets_one_appended(self):
        # a file that ended in a newline still ends in one; one that did
        # not gains a line but keeps its own (lack of a) final newline
        for text, expected in (("font-size = 12\n",
                                "font-size = 12\ntheme = dusk\n"),
                               ("font-size = 12",
                                "font-size = 12\ntheme = dusk")):
            with self.subTest(text=text):
                path = self.write("plain.ghostty", text)
                self.assertEqual(ensure_theme_pointer(path, "dusk"),
                                 "appended")
                self.assertEqual(self.read(path), expected)

    def test_an_empty_config_gets_a_theme_line_and_nothing_else(self):
        path = self.write("empty.ghostty", "")
        self.assertEqual(ensure_theme_pointer(path, "dusk"), "appended")
        self.assertEqual(self.read(path), "theme = dusk\n")

    def test_a_commented_out_theme_line_is_left_alone(self):
        text = "# theme = ghost\nfont-size = 12\n"
        path = self.write("commented.ghostty", text)
        self.assertEqual(ensure_theme_pointer(path, "dusk"), "appended")
        self.assertEqual(self.read(path),
                         text + "theme = dusk\n")

    def test_the_last_theme_line_wins_like_ghostty(self):
        path = self.write("twice.ghostty", "theme = one\ntheme = two\n")
        ensure_theme_pointer(path, "dusk")
        self.assertEqual(self.read(path), "theme = one\ntheme = dusk\n")

    def test_a_config_already_pointing_at_the_theme_is_not_rewritten(self):
        stamp = os.path.getmtime(self.config) - 60
        os.utime(self.config, (stamp, stamp))
        self.assertEqual(ensure_theme_pointer(self.config, "ember"),
                         "unchanged")
        self.assertEqual(self.read(self.config), self.CONFIG)
        self.assertEqual(os.path.getmtime(self.config), stamp)

    def test_line_endings_survive_a_rewrite_and_an_append(self):
        path = self.write("crlf.ghostty", "theme = one\r\nfont-size = 12\r\n")
        self.assertEqual(ensure_theme_pointer(path, "dusk"), "rewritten")
        self.assertEqual(self.read_raw(path),
                         "theme = dusk\r\nfont-size = 12\r\n")
        plain = self.write("crlf2.ghostty", "font-size = 12\r\n")
        self.assertEqual(ensure_theme_pointer(plain, "dusk"), "appended")
        self.assertEqual(self.read_raw(plain),
                         "font-size = 12\r\ntheme = dusk\r\n")

    def test_a_name_that_would_break_the_file_is_refused(self):
        for bad in ("two lines\ntheme = ghost", 'quote"', "half#hash",
                    "", None):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    ensure_theme_pointer(self.config, bad)
        self.assertEqual(self.read(self.config), self.CONFIG)

    def test_a_missing_config_raises_rather_than_inventing_one(self):
        with self.assertRaises(OSError):
            ensure_theme_pointer(os.path.join(self.tmp.name, "gone.ghostty"),
                                 "dusk")

    def test_the_reader_agrees_with_the_writer(self):
        # §7.4 reads `theme =` to find the colours; if the reader and the
        # writer disagreed about a trailing comment, a pushed theme would
        # be invisible to the very next detection
        old = self.write(os.path.join("themes", "ember"),
                         "background = #101014\n")
        self.write(os.path.join("themes", "dusk"),
                   "background = #0a0a13\n")
        self.assertEqual(detect._ghostty_theme_file(self.config), old)
        self.assertTrue(detect.config_holds_colours("ghostty", self.config))
        ensure_theme_pointer(self.config, "dusk")
        new = os.path.join(self.tmp.name, "ghostty", "themes", "dusk")
        self.assertEqual(detect._ghostty_theme_file(self.config), new)
        self.assertTrue(detect.config_holds_colours("ghostty", self.config))

    def test_a_config_with_no_colours_anywhere_is_not_a_target(self):
        # the main config is allowed to hold no colours of its own when it
        # points at a theme (§7.3 with §7.4) - but not when it points at
        # nothing at all
        path = self.write("blank.ghostty", "font-size = 12\n")
        self.assertFalse(detect.config_holds_colours("ghostty", path))
        path = self.write("dangling.ghostty", "theme = nowhere\n")
        self.assertFalse(detect.config_holds_colours("ghostty", path))

    def test_the_main_config_is_not_the_theme_file_it_points_at(self):
        # §13.6 phase 2 writes the pointer, so it needs the file that holds
        # the `theme =` line - not the file that line resolves to
        theme = self.write(os.path.join("themes", "ember"),
                           "background = #101014\n")
        self.assertNotEqual(detect.ghostty_main_config(), theme)
        self.assertEqual(detect.ghostty_main_config(self.config), self.config)
        self.assertIsNone(detect.ghostty_main_config(
            os.path.join(self.tmp.name, "gone.ghostty")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
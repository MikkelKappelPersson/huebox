"""Detection tests: --config format inference (§7.1, plan 3), the env
probes and candidate paths (§7.2, plan appendix A), `config-file`
includes (§7.4) and the `theme =` pointer (§13.6 phase 2 -
`ensure_theme_pointer`)."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402
from huebox import detect                  # noqa: E402
from huebox.detect import ensure_theme_pointer, infer_format  # noqa: E402

GHOSTTY = "background = #101014\n"
KITTY = "background  #101014\n"
#: the alacritty candidate table as `detect` left it at import, so a test
#: that patches it can prove it put it back
_ALACRITTY_DEFAULTS = list(huebox.FORMATS["alacritty"]["defaults"])


class EnvProbe(unittest.TestCase):
    """§7.2 probes env before paths; the spellings are upstream's.

    plan appendix A: kitty documents `KITTY_CONFIG_DIRECTORY` and never
    `KITTY_CONFIG_DIR` (the old huebox spelling, kept for one release),
    and alacritty documents no env var at all for the config path.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for var in ("KITTY_CONFIG_DIRECTORY", "KITTY_CONFIG_DIR",
                    "ALACRITTY_CONFIG", "ALACRITTY_CONFIG_DIR"):
            patcher = mock.patch.dict(os.environ, {var: ""})
            patcher.start()
            self.addCleanup(patcher.stop)

    def _conf(self, where, name="kitty.conf", text=KITTY):
        directory = os.path.join(self.tmp.name, where)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_the_documented_kitty_spelling_is_probed_first(self):
        path = self._conf("kitty-home")
        with mock.patch.dict(os.environ,
                             {"KITTY_CONFIG_DIRECTORY":
                              os.path.dirname(path)}):
            candidates = list(detect._candidate_paths("kitty"))
        self.assertEqual(candidates[0], path)

    def test_the_old_kitty_spelling_still_works_for_one_release(self):
        # deprecated, not removed: v1 shipped it and a 2.0 user should not
        # lose their override on upgrade
        path = self._conf("kitty-old")
        with mock.patch.dict(os.environ,
                             {"KITTY_CONFIG_DIR": os.path.dirname(path)}):
            self.assertIn(path, list(detect._candidate_paths("kitty")))

    def test_the_documented_spelling_wins_when_both_are_set(self):
        new = self._conf("kitty-new")
        self._conf("kitty-old")
        with mock.patch.dict(os.environ,
                             {"KITTY_CONFIG_DIRECTORY": os.path.dirname(new),
                              "KITTY_CONFIG_DIR":
                              os.path.join(self.tmp.name, "kitty-old")}):
            self.assertEqual(list(detect._candidate_paths("kitty"))[0], new)

    def test_a_config_var_pointing_at_the_file_itself_works_too(self):
        path = self._conf("kitty-file")
        with mock.patch.dict(os.environ, {"KITTY_CONFIG_DIRECTORY": path}):
            self.assertEqual(list(detect._candidate_paths("kitty"))[0], path)

    def test_alacritty_has_no_env_probe_left(self):
        # nothing upstream documents, so nothing invented: --config is the
        # only override (§7.1)
        self.assertEqual(huebox.FORMATS["alacritty"]["env"], {})
        path = self._conf("alacritty-home", "alacritty.toml")
        for var in ("ALACRITTY_CONFIG", "ALACRITTY_CONFIG_DIR"):
            with mock.patch.dict(os.environ, {var: os.path.dirname(path)}):
                candidates = list(detect._candidate_paths("alacritty"))
            self.assertNotIn(path, candidates)


class AlacrittySearchOrder(unittest.TestCase):
    """§7.2 / plan appendix A: upstream's own search order.

    The `~/.config/...` entries are rewritten to `$XDG_CONFIG_HOME` when
    `detect` loads, so the expected paths are built the same way here
    rather than hard-coded to a machine's home.
    """

    def _home(self):
        return detect._config_home()

    def test_the_xdg_root_config_is_the_second_stop(self):
        # upstream: $XDG_CONFIG_HOME/alacritty/alacritty.toml, then
        # $XDG_CONFIG_HOME/alacritty.toml, then the ~/.config ones
        defaults = huebox.FORMATS["alacritty"]["defaults"]
        self.assertEqual(defaults[0],
                         os.path.join(self._home(),
                                      "alacritty/alacritty.toml"))
        self.assertEqual(defaults[2],
                         os.path.join(self._home(), "alacritty.toml"))

    def test_the_legacy_paths_are_still_searched(self):
        defaults = huebox.FORMATS["alacritty"]["defaults"]
        self.assertIn(os.path.join(self._home(), "alacritty/alacritty.yml"),
                      defaults)                     # huebox's legacy yaml
        self.assertIn("~/.alacritty.toml", defaults)  # upstream's legacy

    def test_the_xdg_root_config_is_actually_read(self):
        # the table assertion above is about where the new entry sits;
        # this one is what it does. `_prefer_xdg` is the bit that rewrites
        # `~/.config/x` into the XDG home at load time, so drive that with
        # a temporary XDG root and ask detection about the file
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home}):
            path = os.path.join(home, "alacritty.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('[colors.primary]\nbackground = "#0f0f1a"\n')
            defaults = detect._prefer_xdg(
                ["~/.config/alacritty.toml",
                 "~/.config/alacritty/alacritty.toml"])
            self.assertEqual(defaults[0], path)
            with mock.patch.dict(huebox.FORMATS["alacritty"],
                                 {"defaults": defaults}):
                self.assertIn(path, list(detect._candidate_paths("alacritty")))
                self.assertEqual(detect._resolve_for("alacritty"), path)
            self.assertEqual(huebox.FORMATS["alacritty"]["read"](path)
                             ["background"], "#0f0f1a")
        # and the real table is untouched by any of that
        self.assertEqual(huebox.FORMATS["alacritty"]["defaults"],
                         _ALACRITTY_DEFAULTS)


class Includes(unittest.TestCase):
    """§7.4 - `config-file` includes are followed to the colours.

    v1 read the rest of the line as the path, so a commented include
    (`config-file = x.conf  # mine`) pointed at a file that does not
    exist and the colours inside it went unfound - the same bug the P6
    `THEME_LINE` fix removed for `theme =`.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "ghostty")
        os.makedirs(self.root, exist_ok=True)

    def write(self, name, text):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_a_commented_include_is_followed(self):
        included = self.write("colours.conf", GHOSTTY)
        main = self.write("config.ghostty",
                          "font-size = 12\n"
                          f"config-file = {included}  # mine\n")
        self.assertEqual(detect._ghostty_includes(main), [included])
        # and it is the colours that answer the §7.3 question, so the
        # main config counts as a terminal
        self.assertTrue(detect.config_holds_colours("ghostty", main))

    def test_a_quoted_include_with_spaces_is_followed(self):
        included = self.write("my colours.conf", GHOSTTY)
        main = self.write("config.ghostty",
                          f'config-file = "{included}"\n')
        self.assertEqual(detect._ghostty_includes(main), [included])

    def test_the_bare_relative_form_is_resolved_against_the_config(self):
        # `?path` is Ghostty's own spelling for "relative to this file"
        included = self.write("colours.conf", GHOSTTY)
        main = self.write("config.ghostty", "config-file = ?colours.conf\n")
        self.assertEqual(detect._ghostty_includes(main), [included])

    def test_a_commented_out_include_is_ignored(self):
        included = self.write("colours.conf", GHOSTTY)
        main = self.write("config.ghostty",
                          f"# config-file = {included}\n")
        self.assertEqual(detect._ghostty_includes(main), [])

    def test_a_missing_include_is_skipped_not_invented(self):
        main = self.write("config.ghostty", "config-file = ?nowhere.conf\n")
        self.assertEqual(detect._ghostty_includes(main), [])
        self.assertFalse(detect.config_holds_colours("ghostty", main))


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
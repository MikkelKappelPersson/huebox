"""Theme library: names, the canonical writer, gap-tolerant reads, state,
push (§13.1-13.6), and the CLI surface of §13.5.

Every test gets its own `XDG_CONFIG_HOME`, so nothing here reads or writes
the developer's real `~/.config/huebox`. Push tests name the target
explicitly (`path=`/`--config`) because the format candidate paths are
computed at import time, from whatever home was current then; only a
subprocess sees a temp home in the detection order.
"""

import io
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402
import session  # noqa: E402
from huebox import cli, editor, themes  # noqa: E402
from huebox.color import MISSING, SLOTS  # noqa: E402

REPO = os.path.dirname(_HERE)

#: 22 distinct values, so a mix-up between two slots cannot hide
FULL = {slot: f"#{i:02x}{i:02x}{i:02x}" for i, slot in enumerate(SLOTS)}
NAMED_TAIL = ["background", "foreground", "cursor-color", "cursor-text",
              "selection-background", "selection-foreground"]


def ghostty_text(slots=None):
    """A complete ghostty config fixture (§6.1 flat dialect)."""
    slots = slots or FULL
    lines = ["# fixture config", "font-size = 12", ""]
    for i in range(16):
        lines.append(f"palette = {i}={slots[f'palette-{i}']}")
    for name in NAMED_TAIL:
        lines.append(f"{name} = {slots[name]}")
    return "\n".join(lines) + "\n"


#: kitty's dialect has no `cursor-text` at all, and a sparse palette - so
#: this fixture is both a push target and the missing-key report (§13.6).
def kitty_text():
    return ("# kitty fixture\n"
            "font_family      SauceCodePro Nerd Font\n\n"
            "background            #0f0f1a\n"
            "foreground            #ededfe\n"
            "cursor                #ededfe\n"
            "selection_background  #202036\n"
            "selection_foreground  #ededff\n"
            "color0  #0a0a13\n"
            "color1  #ff0067\n"
            "color15 #f8f8ff\n")


class LibraryHome(unittest.TestCase):
    """An isolated `$XDG_CONFIG_HOME` plus a fixture config on disk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        patcher = mock.patch.dict(os.environ,
                                  {"XDG_CONFIG_HOME": self.root})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = os.path.join(self.root, "config.ghostty")
        self.write(self.config, ghostty_text())

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def theme_file(self, name):
        return os.path.join(self.root, "huebox", "themes", f"{name}.toml")

    def drop(self, name):
        os.unlink(self.theme_file(name))

    def xdg(self, relative, text):
        """A config where a subprocess's detection will actually look."""
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return self.write(path, text)

    def run_cli(self, *args):
        """`python -m huebox` with a home that has no terminal in it.

        `--no-reload` is not optional here: the reload reaches for the
        *machine's* ghostty process, and a test has no business reloading
        the terminal it is running inside. The reload is tested against a
        mocked `/proc` instead (`TestReload`).
        """
        env = {key: value for key, value in os.environ.items()
           if not any(mark in key for mark in
                      ("GHOSTTY", "KITTY", "ALACRITTY", "WEZTERM",
                       "TERM_PROGRAM"))}
        env["TERM_PROGRAM"] = ""           # probes must see no terminal
        env["XDG_CONFIG_HOME"] = self.root
        env["HOME"] = self.root
        return subprocess.run([sys.executable, "-m", "huebox", *args,
                               "--no-reload"],
                              capture_output=True, text=True, cwd=REPO,
                              env=env)


class Names(unittest.TestCase):
    """§13.3 - one name rule, 64 characters at most."""

    def test_legal_names(self):
        for name in ("ember", "a", "0", "A", "my-theme_2", "x" * 64,
                     "0123456789"):
            with self.subTest(name=name):
                self.assertTrue(themes.valid_name(name))

    def test_illegal_names(self):
        for name in ("", None, 7, "-lead", "_lead", ".hidden", "a b", "a.b",
                     "a/b", "a.toml", "a\\b", "émber", "x" * 65, "a\nb",
                     "a;b", "a:b", "../escape"):
            with self.subTest(name=name):
                self.assertFalse(themes.valid_name(name))

    def test_a_theme_file_name_is_the_theme_name(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/hb"}):
            self.assertEqual(themes.theme_path("ember"),
                             os.path.join("/tmp/hb", "huebox", "themes",
                                          "ember.toml"))


class Home(LibraryHome):
    """§13.1 - config home, created lazily."""

    def test_home_is_xdg_aware(self):
        self.assertEqual(themes.home(), os.path.join(self.root, "huebox"))
        self.assertEqual(themes.themes_dir(),
                         os.path.join(self.root, "huebox", "themes"))
        self.assertEqual(themes.state_path(),
                         os.path.join(self.root, "huebox", "state.toml"))

    def test_home_falls_back_to_dot_config(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "",
                                          "HOME": self.root}):
            self.assertEqual(themes.home(),
                             os.path.join(self.root, ".config", "huebox"))

    def test_reading_never_creates_anything(self):
        self.assertIsNone(themes.current())
        self.assertEqual(themes.list_themes(), [])
        with self.assertRaises(themes.ThemeError):
            themes.load("ember")
        self.assertFalse(os.path.exists(os.path.join(self.root, "huebox")))

    def test_create_makes_the_directories(self):
        themes.create("ember", FULL)
        self.assertTrue(os.path.isfile(self.theme_file("ember")))


class WriteRead(LibraryHome):
    """§13.2 - one canonical layout out, a tolerant reader in."""

    def test_round_trip_keeps_all_22_slots(self):
        themes.create("ember", FULL)
        self.assertEqual(themes.load("ember"), FULL)

    def test_the_file_is_the_canonical_layout(self):
        themes.create("ember", FULL, source="ghostty:/tmp/config")
        lines = self.read(self.theme_file("ember")).splitlines()
        self.assertEqual(lines[0], themes.HEADER)
        self.assertEqual(lines[1], "[theme]")
        self.assertEqual(lines[2], 'name = "ember"')
        self.assertTrue(lines[3].startswith('created = "'))
        self.assertTrue(lines[4].startswith('modified = "'))
        self.assertEqual(lines[5],
                         'source = "ghostty:/tmp/config"  '
                         "# import origin, informational")
        self.assertEqual(lines[6], "")
        self.assertEqual(lines[7], "[colors]")
        self.assertEqual(lines[8:], [f'{slot} = "{FULL[slot]}"'
                                     for slot in SLOTS])
        self.assertEqual(len(lines), 8 + len(SLOTS))

    def test_a_hand_made_theme_without_a_source_loads(self):
        path = self.theme_file("bare")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write(path, '[colors]\nbackground = "#010203"\n')
        self.assertEqual(themes.load("bare")["background"], "#010203")
        themes.save("bare", themes.load("bare"))
        self.assertNotIn("source =", self.read(path))

    def test_gaps_load_as_missing_grey_with_a_warning(self):
        path = self.theme_file("hollow")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write(path, '[colors]\nbackground = "#010203"\n'
                         'palette-7 = "#040506"\n')
        warnings = []
        slots = themes.load("hollow", warnings)
        self.assertEqual(len(slots), 22)
        self.assertEqual(slots["background"], "#010203")
        self.assertEqual(slots["palette-7"], "#040506")
        self.assertEqual(slots["palette-0"], MISSING)
        self.assertEqual(sum(1 for slot in SLOTS if slots[slot] == MISSING), 20)
        self.assertEqual(len(warnings), 1)
        self.assertIn("hollow", warnings[0])
        self.assertIn("20 slot(s)", warnings[0])

    def test_saving_heals_the_gaps(self):
        path = self.theme_file("hollow")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write(path, '[colors]\nbackground = "#010203"\n')
        gaps = themes.load("hollow")
        themes.save("hollow", gaps)
        self.assertEqual(themes.load("hollow"), gaps)
        text = self.read(path)
        self.assertIn('palette-15 = "#808080"', text)
        self.assertEqual(text.splitlines()[-len(SLOTS):],
                         [f'{slot} = "{gaps[slot]}"' for slot in SLOTS])

    def test_unknown_keys_and_sections_are_counted(self):
        path = self.theme_file("messy")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write(path,
                   '[theme]\nname = "messy"\nauthor = "me"\n'
                   '[colors]\nbackground = "#010203"\nsparkle = "#040506"\n'
                   '[extra]\nwhatever = 1\n')
        warnings = []
        slots = themes.load("messy", warnings)
        self.assertEqual(slots["background"], "#010203")
        self.assertNotIn("sparkle", slots)
        self.assertTrue(any("3 unknown key" in line for line in warnings),
                        warnings)

    def test_an_unknown_key_in_the_theme_block_is_counted_too(self):
        path = self.theme_file("messy")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write(path, '[theme]\nname = "messy"\n'
                         'description = "hi"\n[colors]\n')
        warnings = []
        themes.load("messy", warnings)
        self.assertTrue(any("1 unknown key" in line for line in warnings),
                        warnings)

    def test_a_value_that_is_not_a_colour_is_ignored(self):
        path = self.theme_file("odd")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write(path, '[colors]\nbackground = "chartreuse"\n'
                         'foreground = 0x101014\n')
        warnings = []
        slots = themes.load("odd", warnings)
        self.assertEqual(slots["background"], MISSING)
        self.assertEqual(slots["foreground"], MISSING)
        self.assertEqual(len(warnings), 3)      # two values + the gap summary

    def test_bare_values_extra_whitespace_and_comments_are_accepted(self):
        path = self.theme_file("loose")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write(path, "# a comment\n\n[colors]\n"
                         "   background   =   #0A0B0C   # trailing\n"
                         "foreground = \"#D0D1D2\"\n"
                         '"cursor-color" = \'#e0e1e2\'\n')
        slots = themes.load("loose")
        self.assertEqual(slots["background"], "#0a0b0c")
        self.assertEqual(slots["foreground"], "#d0d1d2")
        self.assertEqual(slots["cursor-color"], "#e0e1e2")

    def test_a_hash_inside_a_quoted_value_is_not_a_comment(self):
        sections, dropped = themes._parse('[colors]\nbg = "#123456"  # ok\n')
        self.assertEqual(dropped, 0)
        self.assertEqual(sections["colors"]["bg"], "#123456")

    def test_created_is_preserved_and_modified_moves(self):
        with mock.patch.object(themes, "_now",
                               side_effect=["2026-10-01T12:00:00",
                                            "2026-10-01T12:34:56"]):
            themes.create("ember", FULL)
            themes.save("ember", dict(FULL, background="#010203"))
        head = themes.meta("ember")
        self.assertEqual(head["created"], "2026-10-01T12:00:00")
        self.assertEqual(head["modified"], "2026-10-01T12:34:56")
        self.assertEqual(themes.load("ember")["background"], "#010203")

    def test_a_re_save_keeps_the_source(self):
        themes.create("ember", FULL, source="kitty:/tmp/kitty.conf")
        themes.save("ember", FULL)
        self.assertEqual(themes.source_of("ember"), "kitty:/tmp/kitty.conf")

    def test_the_write_is_atomic(self):
        themes.create("ember", FULL)
        themes.save("ember", FULL)
        siblings = os.listdir(os.path.dirname(self.theme_file("ember")))
        self.assertEqual(siblings, ["ember.toml"])

    def test_a_missing_theme_raises(self):
        with self.assertRaises(themes.ThemeError) as caught:
            themes.load("nope")
        self.assertIn("no such theme: nope", str(caught.exception))

    def test_an_illegal_name_raises_before_touching_disk(self):
        with self.assertRaises(themes.ThemeError) as caught:
            themes.load("no/slashes")
        self.assertEqual(str(caught.exception), "invalid theme name")
        with self.assertRaises(themes.ThemeError):
            themes.create("../escape", FULL)
        self.assertFalse(os.path.exists(os.path.join(self.root, "huebox")))


class CreateRules(LibraryHome):
    """§13.3 + plan appendix B - names are files, so they collide like files."""

    def test_create_refuses_an_existing_theme(self):
        themes.create("ember", FULL)
        with self.assertRaises(themes.ThemeError) as caught:
            themes.create("ember", dict(FULL, background="#010203"))
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(themes.load("ember"), FULL)      # untouched

    def test_force_replaces(self):
        themes.create("ember", FULL)
        edited = dict(FULL, background="#010203")
        themes.create("ember", edited, force=True)
        self.assertEqual(themes.load("ember"), edited)

    def test_collision_is_case_insensitive(self):
        themes.create("ember", FULL)
        for name in ("Ember", "EMBER", "eMbEr"):
            with self.subTest(name=name):
                with self.assertRaises(themes.ThemeError):
                    themes.create(name, FULL)
        self.assertEqual(sorted(themes.list_themes())[0][0], "ember")


class State(LibraryHome):
    """§13.1 / §13.4 - state.toml is a pointer, and it is allowed to lie."""

    def test_current_is_none_without_a_state_file(self):
        self.assertIsNone(themes.current())

    def test_set_current_writes_two_lines_max(self):
        themes.set_current("ember")
        self.assertEqual(self.read(themes.state_path()),
                         'current = "ember"\n')
        self.assertEqual(themes.current(), "ember")

    def test_a_corrupt_state_reads_as_none(self):
        for text in ("garbage", "current =", "current = [1]",
                     "[theme]\ncurrent = 7\n", 'current = "bad name"', "",
                     'current = "a/b"'):
            with self.subTest(text=text):
                os.makedirs(themes.home(), exist_ok=True)
                self.write(themes.state_path(), text)
                self.assertIsNone(themes.current())

    def test_the_next_set_current_repairs_a_corrupt_state(self):
        os.makedirs(themes.home(), exist_ok=True)
        self.write(themes.state_path(), "]] not toml\n")
        themes.set_current("ember")
        self.assertEqual(themes.current(), "ember")
        self.assertEqual(self.read(themes.state_path()),
                         'current = "ember"\n')

    def test_a_deleted_theme_leaves_the_pointer_alone(self):
        # rm/mv on theme files must keep working: the state is a pointer,
        # and the editor falls back to direct mode when it dangles
        themes.create("ember", FULL)
        themes.set_current("ember")
        self.drop("ember")
        self.assertEqual(themes.current(), "ember")

    def test_set_current_refuses_an_illegal_name(self):
        with self.assertRaises(themes.ThemeError):
            themes.set_current("no/slashes")


class Listing(LibraryHome):
    """The library as a table: name, path, age, and where it came from."""

    def test_list_themes_sorts_by_name(self):
        for name in ("zinc", "amber", "ember"):
            themes.create(name, FULL, source=f"ghostty:/tmp/{name}.conf")
        rows = themes.list_themes()
        self.assertEqual([name for name, _, _ in rows],
                         ["amber", "ember", "zinc"])
        for name, path, mtime in rows:
            self.assertEqual(path, self.theme_file(name))
            self.assertIsInstance(mtime, float)

    def test_source_of_reports_the_import_origin(self):
        themes.create("ember", FULL, source="alacritty:/tmp/alacritty.toml")
        self.assertEqual(themes.source_of("ember"),
                         "alacritty:/tmp/alacritty.toml")
        themes.create("hand", FULL)
        self.assertEqual(themes.source_of("hand"), "")

    def test_stray_files_are_not_themes(self):
        os.makedirs(themes.themes_dir(), exist_ok=True)
        self.write(os.path.join(themes.themes_dir(), "notes.txt"), "hi")
        self.write(os.path.join(themes.themes_dir(), ".hidden.toml"), "hi")
        self.write(os.path.join(themes.themes_dir(), "ember.toml.tmp"), "hi")
        self.assertEqual(themes.list_themes(), [])

    def test_read_terminal_is_the_import_source(self):
        self.assertEqual(themes.read_terminal("ghostty", self.config), FULL)


class Push(LibraryHome):
    """§13.6 - truth -> terminal: one line-level write, per target, no keys
    invented, no truth rolled back. Targets are named here (never detected)
    because a candidate-path search in-process would find the developer's
    real configs; the detection path is covered by the `Cli` subprocesses."""

    def setUp(self):
        super().setUp()
        self.kitty = self.write(os.path.join(self.root, "kitty.conf"),
                                kitty_text())

    def test_push_lands_the_values_and_nothing_else(self):
        edited = dict(FULL, background="#010203", **{"palette-0": "#040506"})
        result = themes.push(edited, to="ghostty", path=self.config)
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.config),))
        after = self.read(self.config)
        self.assertIn("background = #010203", after)
        self.assertIn("palette = 0=#040506", after)
        self.assertIn("# fixture config", after)
        self.assertIn("font-size = 12", after)
        before = ghostty_text().splitlines()
        changed = [i for i, (a, b) in enumerate(zip(before, after.splitlines()))
                   if a != b]
        self.assertEqual(len(changed), 2, [before[i] for i in changed])
        self.assertIn(f"ghostty: pushed to {self.config}", result.lines)

    def test_a_no_op_push_does_not_touch_the_config_at_all(self):
        # §6.2 rule 4 as the user meets it: pushing a theme that already
        # matches the config leaves the bytes *and* the mtime alone, for
        # every dialect the writer covers
        for fmt, path in (("ghostty", self.config), ("kitty", self.kitty)):
            with self.subTest(format=fmt):
                slots = themes.read_terminal(fmt, path)
                stamp = os.path.getmtime(path) - 60
                os.utime(path, (stamp, stamp))
                result = themes.push(slots, to=fmt, path=path)
                self.assertFalse(result.failed)
                self.assertEqual(os.path.getmtime(path), stamp,
                                 f"{fmt} no-op push rewrote the config")

    def test_a_write_failure_is_reported_and_never_rolls_truth_back(self):
        # §10 asks for a read-only / unwritable config case; the honest
        # one does not depend on being a non-root user, so the writer
        # raises what the kernel would
        with mock.patch.dict(
                themes.FORMATS["ghostty"],
                {"write": mock.Mock(side_effect=OSError(13, "Denied"))}):
            result = themes.push(dict(FULL, background="#010203"),
                                 to="ghostty", path=self.config)
        self.assertTrue(result.failed)
        self.assertEqual(result.pushed, ())
        self.assertIn("Denied", "\n".join(result.lines))
        self.assertEqual(self.read(self.config), ghostty_text())

    def test_push_keeps_the_kitty_dialect(self):
        edited = dict(FULL, background="#010203",
                      **{"palette-0": "#040506", "palette-15": "#0a0b0c"})
        result = themes.push(edited, to=["kitty"], path=self.kitty)
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("kitty", self.kitty),))
        after = self.read(self.kitty)
        self.assertIn("background            #010203", after)   # spacing kept
        self.assertIn("color0  #040506", after)
        self.assertIn("color15 #0a0b0c", after)
        self.assertIn("font_family      SauceCodePro Nerd Font", after)

    def test_missing_keys_are_reported_and_never_inserted(self):
        slots = dict(themes.read_terminal("kitty", self.kitty))
        slots["background"] = "#010203"
        result = themes.push(slots, to="kitty", path=self.kitty)
        self.assertFalse(result.failed)        # a report, not a failure
        report = "\n".join(result.lines)
        self.assertIn("not carried by this config:", report)
        self.assertIn("cursor-text", report)   # kitty has no such key
        self.assertIn("palette-2", report)
        after = self.read(self.kitty)
        before = kitty_text().splitlines()
        self.assertEqual(len(after.splitlines()), len(before))
        changed = [i for i, (a, b) in enumerate(zip(before, after.splitlines()))
                   if a != b]
        self.assertEqual(len(changed), 1, [before[i] for i in changed])
        self.assertNotIn("color2", after)      # no key was invented
        self.assertIn("background            #010203", after)

    def test_a_no_op_push_is_byte_identical(self):
        # §6.2 rule 3 — a second push of the same slots changes nothing
        first = themes.push(FULL, to="ghostty", path=self.config)
        self.assertFalse(first.failed)
        before = self.read(self.config)
        second = themes.push(FULL, to="ghostty", path=self.config)
        self.assertFalse(second.failed)
        self.assertEqual(self.read(self.config), before)

    def test_an_explicit_path_with_several_targets_is_an_error(self):
        # P4 review: the API refuses what the CLI refuses — no silent drop
        with self.assertRaises(ValueError):
            themes.push(FULL, to="ghostty,kitty", path=self.config)

    def test_a_config_without_colours_is_never_a_target(self):
        blank = self.write(os.path.join(self.root, "blank.ghostty"),
                           "font-size = 12\n")
        result = themes.push(FULL, to="ghostty", path=blank)
        self.assertTrue(result.failed)
        self.assertEqual(result.pushed, ())
        self.assertIn("no colours in", "\n".join(result.lines))
        self.assertEqual(self.read(blank), "font-size = 12\n")

    def test_a_target_without_a_config_is_reported(self):
        with mock.patch.object(huebox.detect, "_candidate_paths",
                               return_value=iter(())):
            result = themes.push(FULL, to="alacritty")
        self.assertTrue(result.failed)
        self.assertEqual(result.pushed, ())
        self.assertIn("no alacritty config with colours found",
                      "\n".join(result.lines))

    def test_the_default_target_is_the_terminal_you_are_in(self):
        with mock.patch.object(themes, "resolve",
                               return_value=("ghostty", self.config, None)) as ask:
            result = themes.push(dict(FULL, background="#010203"))
        ask.assert_called_once_with(None, None)     # no format, no path: today
        self.assertEqual(result.pushed, (("ghostty", self.config),))
        self.assertIn("background = #010203", self.read(self.config))

    def test_every_target_is_tried_even_after_one_fails(self):
        blank = self.write(os.path.join(self.root, "empty.kitty.conf"),
                           "font-size 12\n")

        def fake(name, path):
            return {"ghostty": ("ghostty", self.config, None),
                    "kitty": ("kitty", blank, None)}[name]

        with mock.patch.object(themes, "resolve", side_effect=fake):
            result = themes.push(dict(FULL, background="#010203"),
                                 to="ghostty,kitty")
        self.assertTrue(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.config),))
        self.assertIn("background = #010203", self.read(self.config))
        self.assertEqual(self.read(blank), "font-size 12\n")
        report = "\n".join(result.lines)
        self.assertIn("ghostty: pushed to", report)
        self.assertIn("kitty: no colours in", report)

    def test_an_unknown_target_is_refused(self):
        for bad in ("wezterm", "ghostty,wezterm", ["kitty", "nope"]):
            with self.subTest(to=bad):
                with self.assertRaises(themes.ThemeError) as caught:
                    themes.push(FULL, to=bad)
                self.assertIn("unknown format", str(caught.exception))
        with self.assertRaises(themes.ThemeError) as caught:
            themes.push(FULL, to=" , ")
        self.assertIn("--to needs at least one format", str(caught.exception))

    def test_no_push_touches_nothing(self):
        before = self.read(self.config)
        stamp = os.path.getmtime(self.config)
        result = themes.push(dict(FULL, background="#010203"),
                             to="ghostty", path=self.config, no_push=True)
        self.assertEqual(result, themes.PushResult((), (), False))
        self.assertEqual(self.read(self.config), before)
        self.assertEqual(os.path.getmtime(self.config), stamp)

    def test_an_explicit_path_pins_one_target(self):
        with mock.patch.object(themes, "resolve",
                               return_value=("ghostty", self.config, None)) as ask:
            themes.push(FULL, to="ghostty", path=self.config)
        ask.assert_called_once_with("ghostty", self.config)

    def test_an_explicit_path_of_an_unknown_format_is_reported(self):
        # `background = #…` parses as ghostty *and* kitty: §7.1 says that
        # is not guessable, so the push says so instead of picking one
        odd = self.write(os.path.join(self.root, "theme-x"),
                         "background = #101014\n")
        result = themes.push(FULL, path=odd)
        self.assertTrue(result.failed)
        self.assertEqual(result.pushed, ())
        self.assertIn("cannot tell which format", "\n".join(result.lines))
        self.assertEqual(self.read(odd), "background = #101014\n")


class GhosttyNative(LibraryHome):
    """§13.6 — a Ghostty theme file of ours plus one `theme =` line.

    The export is the default where the config is organised by theme
    (decision 26), so everything the in-place path guarantees has to keep
    holding here too: a theme file only in Ghostty's own themes dir, a
    config that moves exactly one line, and not one word of the buffer's
    spelling invented.
    """

    def setUp(self):
        super().setUp()
        self.native = os.path.join(self.root, "ghostty", "themes", "ember")

    def theme_file_on_disk(self):
        with open(self.native, encoding="utf-8") as handle:
            return handle.read()

    def test_the_export_is_a_ghostty_theme_file_that_round_trips(self):
        path = themes.export_ghostty_native("ember", FULL)
        self.assertEqual(path, self.native)
        self.assertEqual(themes.read_terminal("ghostty", path), FULL)
        lines = self.theme_file_on_disk().splitlines()
        self.assertEqual(lines[0], themes.GHOSTTY_HEADER)
        self.assertEqual(lines[1:17], [f"palette = {i}={FULL[f'palette-{i}']}"
                                      for i in range(16)])
        self.assertEqual(lines[17:], [f"{slot} = {FULL[slot]}"
                                      for slot in SLOTS[16:]])
        self.assertEqual(len(lines), 23)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_a_re_export_replaces_the_file_whole(self):
        themes.export_ghostty_native("ember", FULL)
        themes.export_ghostty_native("ember", dict(FULL, background="#010203"))
        self.assertEqual(themes.read_terminal("ghostty", self.native),
                         dict(FULL, background="#010203"))
        self.assertEqual(len(self.theme_file_on_disk().splitlines()), 23)

    def test_an_illegal_name_raises_before_anything_is_written(self):
        for bad in ("bad name", "../escape", "a/b", ""):
            with self.subTest(name=bad):
                with self.assertRaises(themes.ThemeError):
                    themes.export_ghostty_native(bad, FULL)
        self.assertFalse(os.path.isdir(os.path.join(self.root, "ghostty")))

    def test_a_gap_becomes_the_missing_grey_a_push_sends(self):
        # §13.6: the truth file says all 22 slots have a value, so the
        # terminal gets the same grey the editor showed
        sparse = {slot: value for slot, value in FULL.items()
                  if slot != "cursor-text"}
        themes.export_ghostty_native("ember", sparse)
        self.assertEqual(themes.read_terminal("ghostty", self.native)
                         ["cursor-text"], MISSING)

    def test_a_native_push_exports_and_points_the_main_config(self):
        edited = dict(FULL, background="#010203")
        result = themes.push(edited, to="ghostty", path=self.config,
                             ghostty_native=True, name="ember")
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.native),))
        report = "\n".join(result.lines)
        self.assertIn(f"ghostty: exported {self.native}", report)
        self.assertIn(f"theme = ember appended in {self.config}", report)
        self.assertIn(f"background = #010203",
                      self.theme_file_on_disk())
        # the config itself gained the pointer line and nothing else
        before = ghostty_text().splitlines()
        after = self.read(self.config).splitlines()
        self.assertEqual(after[:-1], before)
        self.assertEqual(after[-1], "theme = ember")

    def test_a_native_push_rewrites_the_pointer_it_finds(self):
        config = self.write(os.path.join(self.root, "pointed.ghostty"),
                            "# mine\ntheme   = \"ember\"   # keep\n"
                            "font-size = 12\n")
        # the main config holds no colours of its own; the theme file it
        # points at does — which is exactly what §7.4 is for
        old = os.path.join(self.root, "ghostty", "themes", "ember")
        os.makedirs(os.path.dirname(old), exist_ok=True)
        self.write(old, "background = #101014\n")
        result = themes.push(FULL, to="ghostty", path=config,
                             ghostty_native=True, name="dusk")
        self.assertFalse(result.failed)
        lines = self.read(config).splitlines()
        self.assertEqual(lines, ["# mine", 'theme   = "dusk"   # keep',
                                 "font-size = 12"])
        self.assertIn("ghostty: theme = dusk rewritten in",
                      "\n".join(result.lines))
        # the theme file the config pointed at is not ours and is untouched
        self.assertEqual(self.read(old), "background = #101014\n")

    def test_a_no_op_native_push_leaves_the_config_alone(self):
        themes.push(FULL, to="ghostty", path=self.config,
                    ghostty_native=True, name="ember")
        stamp = os.path.getmtime(self.config) - 60
        os.utime(self.config, (stamp, stamp))
        result = themes.push(FULL, to="ghostty", path=self.config,
                             ghostty_native=True, name="ember")
        self.assertFalse(result.failed)
        self.assertIn("theme = ember unchanged", "\n".join(result.lines))
        self.assertEqual(os.path.getmtime(self.config), stamp)

    def test_the_flag_leaves_kitty_and_alacritty_alone(self):
        # a multi-target run with the flag exports for ghostty and pushes
        # kitty the ordinary way — the flag is ghostty-scoped
        kitty = self.write(os.path.join(self.root, "kitty.conf"), kitty_text())

        def fake(name, path):
            return {"ghostty": ("ghostty", self.config, None),
                    "kitty": ("kitty", kitty, None)}[name]

        with mock.patch.object(themes, "resolve", side_effect=fake), \
                mock.patch.object(themes, "ghostty_main_config",
                                  return_value=self.config):
            result = themes.push(dict(FULL, background="#010203"),
                                 to="ghostty,kitty", ghostty_native=True,
                                 name="ember")
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.native),
                                         ("kitty", kitty)))
        self.assertIn("background = #010203", self.theme_file_on_disk())
        self.assertIn("background            #010203", self.read(kitty))
        self.assertIn("# kitty fixture", self.read(kitty))

    def test_alacritty_is_pushed_the_ordinary_way(self):
        alacritty = self.write(
            os.path.join(self.root, "alacritty.toml"),
            "[colors.primary]\nbackground = \"#101014\"\n"
            "foreground = \"#eeeeee\"\n")
        result = themes.push(dict(FULL, background="#010203"), to="alacritty",
                             path=alacritty, ghostty_native=True, name="ember")
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("alacritty", alacritty),))
        self.assertIn('background = "#010203"', self.read(alacritty))
        self.assertFalse(os.path.exists(self.native))

    def test_a_push_with_no_theme_name_edits_the_config_in_place(self):
        # §13.6: a name is what an export is *called*, so a push without
        # one (a direct session) writes the colours where they live
        result = themes.push(dict(FULL, background="#010203"),
                             to="ghostty", path=self.config,
                             ghostty_native=True)
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.config),))
        self.assertIn("background = #010203", self.read(self.config))
        self.assertFalse(os.path.exists(self.native))
        self.assertNotIn("theme =", self.read(self.config))

    def test_a_config_with_no_colours_is_still_not_a_target(self):
        blank = self.write(os.path.join(self.root, "blank.ghostty"),
                           "font-size = 12\n")
        result = themes.push(FULL, to="ghostty", path=blank,
                             ghostty_native=True, name="ember")
        self.assertTrue(result.failed)
        self.assertEqual(result.pushed, ())
        self.assertIn("no colours in", "\n".join(result.lines))
        self.assertEqual(self.read(blank), "font-size = 12\n")
        self.assertFalse(os.path.exists(self.native))

    def test_no_push_still_wins_over_the_flag(self):
        before = self.read(self.config)
        result = themes.push(FULL, to="ghostty", path=self.config,
                             ghostty_native=True, name="ember", no_push=True)
        self.assertEqual(result, themes.PushResult((), (), False))
        self.assertEqual(self.read(self.config), before)
        self.assertFalse(os.path.exists(self.native))


class ThemeOrganisedGhostty(LibraryHome):
    """A ghostty config that points at a theme file — the common shape.

    §13.6 as it now stands: the config is organised by theme, so a save is
    exported under the theme's own name and the pointer is repointed. The
    file the pointer used to name is left exactly as it was, which is the
    whole point — a theme's colours never land in another theme's file.
    """

    def setUp(self):
        super().setUp()
        self.themedir = os.path.join(self.root, "ghostty", "themes")
        os.makedirs(self.themedir, exist_ok=True)
        self.old = self.write(os.path.join(self.themedir, "Nightspice"),
                              ghostty_text())
        self.config = self.write(
            os.path.join(self.root, "ghostty", "config.ghostty"),
            "# mine\nfont-size = 12\ntheme = Nightspice\n")
        self.old_before = self.read(self.old)

    def resolve_here(self):
        """This test's config home, candidates included.

        The candidate table is built at import time, so an in-process test
        points it at the temporary home by hand; the colours a real run
        would find are the same ones this finds.
        """
        return mock.patch.dict(themes.FORMATS["ghostty"],
                               {"defaults": [self.config]})

    def push(self, slots, **kwargs):
        kwargs.setdefault("to", "ghostty")
        with self.resolve_here():
            return themes.push(slots, **kwargs)

    def test_a_save_exports_its_own_theme_file_and_repoints(self):
        edited = dict(FULL, background="#010203")
        result = self.push(edited, name="test")
        self.assertFalse(result.failed)
        exported = os.path.join(self.themedir, "test")
        self.assertEqual(result.pushed, (("ghostty", exported),))
        self.assertEqual(themes.read_terminal("ghostty", exported), edited)
        self.assertIn("theme = test", self.read(self.config))
        # the file the pointer used to name is untouched, byte for byte
        self.assertEqual(self.read(self.old), self.old_before)
        self.assertIn(f"ghostty: exported {exported}", "\n".join(result.lines))

    def test_a_save_of_the_theme_the_config_already_uses_stays_put(self):
        # the case that used to work by accident: same name, so the export
        # lands on the file the config already points at
        edited = dict(FULL, background="#010203")
        result = self.push(edited, name="Nightspice")
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.old),))
        self.assertEqual(themes.read_terminal("ghostty", self.old), edited)
        self.assertIn("theme = Nightspice unchanged",
                      "\n".join(result.lines))

    def test_in_place_is_refused_when_the_file_is_another_themes(self):
        # the invariant, not a default: even asked to, huebox will not
        # write theme `test` into the file that belongs to `Nightspice`
        edited = dict(FULL, background="#010203")
        result = self.push(edited, name="test", ghostty_in_place=True)
        self.assertTrue(result.failed)
        self.assertEqual(result.pushed, ())
        self.assertEqual(self.read(self.old), self.old_before)
        report = "\n".join(result.lines)
        self.assertIn("refusing to write theme 'test'", report)
        self.assertIn("drop --ghostty-in-place", report)

    def test_in_place_is_allowed_when_the_push_changes_nothing(self):
        # nothing would be written, so nothing can be crossed: the report
        # is an ordinary push and the file keeps its mtime (§6.2 rule 4)
        stamp = os.path.getmtime(self.old)
        result = self.push(FULL, name="test", ghostty_in_place=True)
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.old),))
        self.assertEqual(os.path.getmtime(self.old), stamp)

    def test_in_place_is_allowed_for_a_config_with_inline_colours(self):
        # no theme name in play at all, so the colours are edited where
        # they live and the config's layout is none of huebox's business
        inline = self.write(os.path.join(self.root, "inline.ghostty"),
                            ghostty_text())
        with self.resolve_here():
            result = themes.push(dict(FULL, background="#010203"),
                                 to="ghostty", path=inline, name="test")
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", inline),))
        self.assertIn("background = #010203", self.read(inline))
        self.assertNotIn("theme =", self.read(inline))

    def test_in_place_never_touches_the_pointer(self):
        result = self.push(dict(FULL, background="#010203"),
                           name="Nightspice", ghostty_in_place=True)
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.old),))
        self.assertIn("theme = Nightspice", self.read(self.config))

    def test_the_export_says_when_inline_colours_are_shadowed(self):
        inline = self.write(os.path.join(self.root, "inline.ghostty"),
                            ghostty_text())
        with self.resolve_here():
            result = themes.push(dict(FULL, background="#010203"),
                                 to="ghostty", path=inline, name="test",
                                 ghostty_native=True)
        self.assertFalse(result.failed)
        self.assertIn("are now shadowed by", "\n".join(result.lines))
        self.assertIn(f"background = {FULL['background']}", self.read(inline))
        self.assertIn("theme = test", self.read(inline))

    def test_a_dangling_pointer_is_repaired_by_the_save(self):
        # §7.3 with the one exception that matters: a `theme =` naming a
        # file that is not there is a broken chain, not a colourless
        # config - Ghostty calls it a configuration error on reload, and
        # the export is the only thing that puts the colours back
        os.unlink(self.old)
        result = self.push(dict(FULL, background="#010203"), name="Nightspice")
        self.assertFalse(result.failed)
        self.assertEqual(themes.read_terminal("ghostty", self.old),
                         dict(FULL, background="#010203"))
        self.assertIn("theme = Nightspice", self.read(self.config))
        self.assertIn("which was not on disk", "\n".join(result.lines))

    def test_a_dangling_pointer_saves_any_theme_not_just_the_named_one(self):
        os.unlink(self.old)
        result = self.push(dict(FULL, background="#010203"), name="test")
        self.assertFalse(result.failed)
        exported = os.path.join(self.themedir, "test")
        self.assertEqual(result.pushed, (("ghostty", exported),))
        self.assertIn("theme = test", self.read(self.config))

    def test_in_place_cannot_repair_a_dangling_pointer(self):
        # the colours have nowhere to live, so there is nothing for the
        # in-place write to write - and the report names the missing file
        # and the way out, instead of the bare "no colours found"
        os.unlink(self.old)
        result = self.push(dict(FULL, background="#010203"), name="test",
                           ghostty_in_place=True)
        self.assertTrue(result.failed)
        self.assertFalse(os.path.exists(self.old))
        report = "\n".join(result.lines)
        self.assertIn("points at theme 'Nightspice', which is not on disk",
                      report)
        self.assertIn("drop --ghostty-in-place", report)

    def test_a_direct_session_says_what_a_dangling_pointer_needs(self):
        # no theme name to export under, so the save cannot repair it -
        # the note says what would
        os.unlink(self.old)
        result = self.push(dict(FULL, background="#010203"))
        self.assertTrue(result.failed)
        self.assertIn("save the buffer as a theme (N)",
                      "\n".join(result.lines))
        self.assertFalse(os.path.exists(self.old))

    def test_another_format_does_not_reach_for_ghostty(self):
        # the repair is ghostty's, and only for a ghostty target: a kitty
        # that is not there is still a kitty that is not there
        blank = self.write(os.path.join(self.root, "kitty.conf"),
                           "font_family  monospace\n")
        result = self.push(dict(FULL, background="#010203"), to="kitty",
                           path=blank, name="test")
        self.assertTrue(result.failed)
        self.assertIn("no colours in", "\n".join(result.lines))
        self.assertIn("theme = Nightspice", self.read(self.config))

    def test_a_dangling_pointer_is_repaired_end_to_end(self):
        # the reported state, through the CLI and no flags at all: the
        # config points at a theme that is not there, and `use` puts the
        # colours back in a file of the theme's own name
        threedir = os.path.join(self.root, "ghostty", "themes")
        os.makedirs(threedir, exist_ok=True)
        config = self.xdg("ghostty/config.ghostty",
                          "# mine\nfont-size = 12\ntheme = test\n")
        self.assertFalse(os.path.exists(os.path.join(threedir, "test")))
        themes.create("Nightspice", dict(FULL, background="#010203"))
        out = self.run_cli("use", "Nightspice")
        self.assertEqual(out.returncode, 0, out.stderr)
        exported = os.path.join(threedir, "Nightspice")
        self.assertIn(f"ghostty: exported {exported}", out.stderr)
        self.assertIn("which was not on disk", out.stderr)
        self.assertIn("theme = Nightspice", self.read(config))
        self.assertNotIn("theme = test", self.read(config))
        self.assertEqual(themes.read_terminal("ghostty", exported),
                         dict(FULL, background="#010203"))


class FakeTTY:
    """Just enough of a terminal for the editor's isatty check."""

    def isatty(self):
        return True

    def fileno(self):
        raise OSError("no fd: the test drives the key stream")



class FakeOut(io.StringIO):
    """stdout that claims to be a terminal and still captures the frame."""

    def isatty(self):
        return True


class EditorWiring(LibraryHome):
    """The whole path: an editor session in theme mode writes the truth."""

    def session(self, keys, spec=None):
        out, err = FakeOut(), io.StringIO()
        with mock.patch.object(sys, "stdin", FakeTTY()), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err):
            cli._run_editor(cli.Target("ember", "theme ember",
                                       self.theme_file("ember"),
                                       themes.load("ember")), spec,
                            driver=session.driver_factory(keys,
                                                         size=(100, 30)))
        return out.getvalue(), err.getvalue()

    def test_ctrl_s_writes_the_theme_file(self):
        themes.create("ember", FULL)
        # palette-0 starts black: a hue nudge would not show on a grey
        self.session(["x", editor.SAVE_KEY, "esc"])
        saved = themes.load("ember")
        self.assertNotEqual(saved["palette-0"], FULL["palette-0"])
        self.assertIn(f'palette-0 = "{saved["palette-0"]}"',
                      self.read(self.theme_file("ember")))
        self.assertFalse(os.path.exists(self.theme_file("ember")
                                        + ".huebox.bak"))

    def test_quitting_without_saving_leaves_the_file_alone(self):
        themes.create("ember", FULL)
        original = self.read(self.theme_file("ember"))
        self.session(["x", "esc", "esc"])
        self.assertEqual(self.read(self.theme_file("ember")), original)


class _PushSession(LibraryHome):
    """Shared Ctrl+S harness for the push-on-save suites (§13.6).

    The phase-1 tests live on `PushOnSave`, the native ones on
    `GhosttyNativeOnSave` — the harness holds only the fixture and the
    session runner so neither suite re-runs the other's cases.
    """

    def setUp(self):
        super().setUp()
        self.kitty = self.write(os.path.join(self.root, "kitty.conf"),
                                kitty_text())
        self.before = self.read(self.kitty)
        themes.create("ember", FULL)

    def session(self, keys, spec):
        out, err = FakeOut(), io.StringIO()
        with mock.patch.object(sys, "stdin", FakeTTY()), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err), \
                mock.patch.object(editor, "term_size",
                                  return_value=(100, 30)):
            status = cli._run_editor(
                cli.Target("ember", "theme ember", self.theme_file("ember"),
                           themes.load("ember")), spec,
                driver=session.driver_factory(keys, size=(100, 30)))
        return status, out.getvalue(), err.getvalue()


class PushOnSave(_PushSession):
    """§13.6 - Ctrl+S in a theme session is truth first, terminal second."""

    def test_ctrl_s_writes_truth_then_pushes(self):
        spec = cli.PushSpec(("kitty",), None, self.kitty, False)
        status, out, err = self.session(["x", editor.SAVE_KEY, "esc"], spec)
        self.assertEqual(status, 0)
        saved = themes.load("ember")
        self.assertNotEqual(saved["palette-0"], FULL["palette-0"])
        self.assertIn(f'palette-0 = "{saved["palette-0"]}"',
                      self.read(self.theme_file("ember")))
        after = self.read(self.kitty)
        self.assertIn(f"color0  {saved['palette-0']}", after)
        self.assertIn("# kitty fixture", after)
        self.assertIn("saved ember → kitty", out)          # the status line
        self.assertIn(f"huebox: kitty: pushed to {self.kitty}", err)
        self.assertIn("huebox: reload your terminal", err)
        self.assertIn("not carried by this config", err)   # cursor-text, ...

    def test_the_status_names_every_target(self):
        def fake(name, path):
            return {"ghostty": ("ghostty", self.config, None),
                    "kitty": ("kitty", self.kitty, None)}[name]

        spec = cli.PushSpec(("ghostty", "kitty"), None, None, False)
        with mock.patch.object(themes, "resolve", side_effect=fake):
            status, out, _ = self.session(["x", editor.SAVE_KEY, "esc"], spec)
        self.assertEqual(status, 0)
        self.assertIn("saved ember → ghostty, kitty", out)
        saved = themes.load("ember")
        self.assertIn(f"background = {saved['background']}",
                      self.read(self.config))
        self.assertIn(f"background            {saved['background']}",
                      self.read(self.kitty))

    def test_no_push_leaves_the_config_bytes_and_mtime_alone(self):
        stamp = os.path.getmtime(self.kitty)
        spec = cli.PushSpec((), None, self.kitty, True)
        status, out, err = self.session(["x", editor.SAVE_KEY, "esc"], spec)
        self.assertEqual(status, 0)
        self.assertNotEqual(themes.load("ember")["palette-0"],
                            FULL["palette-0"])
        self.assertEqual(self.read(self.kitty), self.before)
        self.assertEqual(os.path.getmtime(self.kitty), stamp)
        self.assertIn("saved ember (truth only)", out)
        self.assertIn("--no-push", err)

    def test_a_failed_push_keeps_the_truth_and_exits_1(self):
        # decision 7: the theme file is written first and never rolled back
        blank = self.write(os.path.join(self.root, "blank.ghostty"),
                           "font-size = 12\n")
        spec = cli.PushSpec(("ghostty",), None, blank, False)
        status, out, err = self.session(["x", editor.SAVE_KEY, "esc"], spec)
        self.assertEqual(status, 1)
        saved = themes.load("ember")
        self.assertNotEqual(saved["palette-0"], FULL["palette-0"])
        self.assertIn(f'palette-0 = "{saved["palette-0"]}"',
                      self.read(self.theme_file("ember")))
        self.assertEqual(self.read(blank), "font-size = 12\n")
        self.assertIn("saved ember - push failed", out)
        self.assertIn("huebox: ghostty: no colours in", err)

    def test_a_session_with_no_spec_pushes_nothing(self):
        # the programmatic default: `edit()` without a command line writes
        # truth only, and never reaches for a terminal nobody named
        status, out, _ = self.session(["x", editor.SAVE_KEY, "esc"], None)
        self.assertEqual(status, 0)
        self.assertEqual(self.read(self.kitty), self.before)
        self.assertIn("saved ember (truth only)", out)


class GhosttyNativeOnSave(_PushSession):
    """The flag through the editor: Ctrl+S exports and points, in that order.

    Same harness, native spec — the phase-1 guarantee lives on `PushOnSave`.
    """

    def setUp(self):
        super().setUp()
        self.native = os.path.join(self.root, "ghostty", "themes", "ember")

    def native_spec(self):
        return cli.PushSpec(("ghostty",), None, self.config, False, True)

    def test_ctrl_s_exports_a_theme_file_and_points_the_config(self):
        status, out, err = self.session(["x", editor.SAVE_KEY, "esc"],
                                        self.native_spec())
        self.assertEqual(status, 0)
        saved = themes.load("ember")             # truth first, always
        self.assertNotEqual(saved["palette-0"], FULL["palette-0"])
        self.assertEqual(themes.read_terminal("ghostty", self.native),
                         saved)
        # no inline colour was touched: the config grew one pointer line
        before = ghostty_text().splitlines()
        after = self.read(self.config).splitlines()
        self.assertEqual(after[:-1], before)
        self.assertEqual(after[-1], "theme = ember")
        self.assertIn("saved ember → ghostty", out)
        self.assertIn(f"huebox: ghostty: exported {self.native}", err)
        self.assertIn(f"huebox: ghostty: theme = ember appended in "
                      f"{self.config}", err)

    def test_the_report_never_lands_inside_the_frame(self):
        # §13.7: the report is printed after the session, never from the
        # draw loop - so the frame cannot contain it
        _status, out, err = self.session(["x", editor.SAVE_KEY, "esc"],
                                         self.native_spec())
        self.assertNotIn("exported", out.replace("saved ember → ghostty", ""))
        self.assertIn("exported", err)

    def test_no_push_beats_the_flag_in_a_session_too(self):
        spec = cli.PushSpec(("ghostty",), None, self.config, True, True)
        before = self.read(self.config)
        status, out, err = self.session(["x", editor.SAVE_KEY, "esc"], spec)
        self.assertEqual(status, 0)
        self.assertEqual(self.read(self.config), before)
        self.assertFalse(os.path.exists(self.native))
        self.assertIn("saved ember (truth only)", out)
        self.assertIn("--no-push", err)

    def test_ctrl_s_exports_by_default_when_the_config_is_on_a_theme(self):
        # the reported bug, in the editor: a config on `frost`, a session
        # editing `ember`, and no flag anywhere - the save must not put
        # ember's colours in frost's file
        threedir = os.path.join(self.root, "ghostty", "themes")
        os.makedirs(threedir, exist_ok=True)
        frost = self.write(os.path.join(threedir, "frost"), ghostty_text())
        config = self.write(os.path.join(self.root, "pointed.ghostty"),
                            "# mine\ntheme = frost\n")
        before = self.read(frost)
        spec = cli.PushSpec(("ghostty",), None, config, False)
        with mock.patch.dict(themes.FORMATS["ghostty"],
                             {"defaults": [config]}):
            status, out, err = self.session(["x", editor.SAVE_KEY, "esc"],
                                            spec)
        self.assertEqual(status, 0)
        saved = themes.load("ember")          # truth first, still
        self.assertNotEqual(saved["palette-0"], FULL["palette-0"])
        exported = os.path.join(threedir, "ember")
        self.assertEqual(themes.read_terminal("ghostty", exported), saved)
        self.assertEqual(self.read(frost), before)      # not one byte moved
        self.assertIn("theme = ember", self.read(config))
        self.assertIn(f"huebox: ghostty: exported {exported}", err)
        self.assertIn("saved ember → ghostty", out)


class Picker(LibraryHome):
    """The picker and save-as-new against a real library (§13.7)."""

    def setUp(self):
        super().setUp()
        themes.create("ember", FULL)
        themes.create("frost", dict(FULL, background="#0a0a13"))
        themes.set_current("ember")
        self.spec = cli.PushSpec(("ghostty",), None, self.config, False)

    def theme_target(self, name):
        return cli.Target(name, f"theme {name}", self.theme_file(name),
                          themes.load(name))

    def direct_target(self):
        """A v1 session: no theme, the config itself is the subject (§13.4)."""
        return cli.Target(None, "ghostty", self.config,
                          themes.read_terminal("ghostty", self.config))

    def session(self, keys, target=None, spec=None, answers=()):
        drawn = []
        real = editor.draw_editor
        answers = list(answers)

        def record(*args, **kwargs):
            drawn.append({"status": args[5], "head": kwargs.get("head"),
                          "overlay": kwargs.get("overlay")})
            return real(*args, **kwargs)

        def ask(label):
            return answers.pop(0) if answers else ""

        out, err = FakeOut(), io.StringIO()
        with mock.patch.object(sys, "stdin", FakeTTY()), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err), \
                mock.patch("builtins.input", side_effect=ask):
            status = cli._run_editor(target or self.theme_target("ember"),
                                     spec if spec is not None else self.spec,
                                     driver=session.driver_factory(
                                         keys, size=(80, 24), draw=record))
        return status, drawn, out.getvalue(), err.getvalue()

    def test_a_direct_session_says_why_the_flag_cannot_apply(self):
        # §13.4: a direct session has no theme, and a theme file needs a
        # name - so the config is written and the note says so once
        spec = cli.PushSpec(("ghostty",), None, self.config, False, True)
        status, _drawn, _out, err = self.session(
            ["x", editor.SAVE_KEY, "esc"], self.direct_target(), spec)
        self.assertEqual(status, 0)
        self.assertEqual(err.count("--ghostty-native needs a theme"), 1)
        self.assertIn("background = ", self.read(self.config))
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "ghostty", "themes", "ember")))

    def test_t_opens_the_picker_with_the_current_theme_marked(self):
        _, drawn, _, _ = self.session(["t", "esc", "esc"])
        overlay = next(d["overlay"] for d in drawn if d["overlay"])
        self.assertEqual(overlay, (["ember", "frost"], 0, "ember"))
        self.assertEqual(overlay[0][overlay[1]], "ember")

    def test_opening_a_theme_pushes_it_without_waiting_for_a_save(self):
        # §13.7: choosing a theme is choosing it for the terminal too, so
        # the switch runs the save path and the terminal is already on it
        status, drawn, out, err = self.session(["t", "down", "\r", "esc"])
        self.assertEqual(status, 0)
        self.assertIn("saved frost → ghostty", out)
        self.assertIn("huebox: ghostty: pushed to", err)
        # frost's colours, not the ones the session started on
        self.assertIn(f"palette = 0={themes.load('frost')['palette-0']}",
                      self.read(self.config))
        self.assertNotIn("●", drawn[-1]["head"])          # nothing to save

    def test_opening_another_theme_switches_the_buffer_and_the_save(self):
        # the whole point: the subject can change mid-session, so Ctrl+S
        # writes the new truth file and pushes that (§13.6, §13.7)
        status, drawn, out, err = self.session(["t", "down", "\r", "x",
                                                editor.SAVE_KEY, "esc"])
        self.assertEqual(status, 0)
        self.assertEqual(themes.current(), "frost")
        self.assertEqual(drawn[-1]["head"], "frost ghostty")
        self.assertEqual(drawn[-2]["head"], "frost ● ghostty")   # dirty dot
        self.assertEqual(themes.load("ember"), FULL)     # the old one untouched
        saved = themes.load("frost")
        self.assertNotEqual(saved["palette-0"], FULL["palette-0"])
        self.assertIn(f'palette-0 = "{saved["palette-0"]}"',
                      self.read(self.theme_file("frost")))
        self.assertIn(f"palette = 0={saved['palette-0']}", self.read(self.config))
        self.assertIn("saved theme frost", out)
        self.assertIn("huebox: ghostty: pushed to", err)

    def test_a_dirty_switch_is_blocked_and_says_exactly_why(self):
        before = self.read(self.theme_file("frost"))
        status, drawn, _, err = self.session(["x", "t", "down", "\r",
                                              "esc", "r", "esc"])
        self.assertEqual(status, 0)
        self.assertEqual(themes.current(), "ember")       # decision 12
        self.assertEqual(self.read(self.theme_file("frost")), before)
        self.assertEqual(themes.load("ember"), FULL)
        self.assertIn("save (Ctrl+S) or revert (r) first",
                      [d["status"] for d in drawn])
        self.assertEqual(err, "")

    def test_n_makes_a_theme_from_the_buffer_and_the_next_save_pushes_it(self):
        status, drawn, _, err = self.session(
            ["x", "t", "n", "esc", editor.SAVE_KEY, "esc"], answers=["dusk"])
        self.assertEqual(status, 0)
        self.assertTrue(os.path.isfile(self.theme_file("dusk")))
        self.assertEqual(themes.current(), "dusk")
        self.assertNotEqual(themes.load("dusk")["palette-0"],
                            FULL["palette-0"])
        self.assertEqual(drawn[-1]["head"], "dusk ghostty")
        self.assertIn(f"palette = 0={themes.load('dusk')['palette-0']}",
                      self.read(self.config))
        self.assertIn("huebox: ghostty: pushed to", err)

    def test_save_as_new_migrates_a_direct_session_onto_the_library(self):
        # §13.4 — `N` is the documented way out of a v1 direct-mode session
        status, drawn, out, err = self.session(
            ["x", "N", "esc"], target=self.direct_target(), answers=["dusk"])
        self.assertEqual(status, 0)
        self.assertEqual(themes.current(), "dusk")
        expected = themes.load("dusk")
        self.assertNotEqual(expected["palette-0"], FULL["palette-0"])
        self.assertEqual(expected["background"], FULL["background"])
        self.assertIn(f"palette = 0={expected['palette-0']}",
                      self.read(self.config))
        self.assertEqual(drawn[-1]["head"], "dusk ghostty")
        self.assertIn("saved theme dusk", out)
        self.assertIn("huebox: ghostty: pushed to", err)

    def test_save_as_new_asks_before_replacing_a_taken_name(self):
        original = self.read(self.theme_file("ember"))
        status, drawn, _, err = self.session(["x", "N", "esc"],
                                             answers=["ember", "y"])
        self.assertEqual(status, 0)
        self.assertNotEqual(self.read(self.theme_file("ember")), original)
        self.assertNotEqual(themes.load("ember")["palette-0"],
                            FULL["palette-0"])
        self.assertIn("huebox: ghostty: pushed to", err)

    def test_cancelling_the_confirm_leaves_the_taken_theme_alone(self):
        original = self.read(self.theme_file("ember"))
        config_before = self.read(self.config)
        status, drawn, out, err = self.session(["x", "N", "esc", "esc"],
                                               answers=["ember", ""])
        self.assertEqual(status, 0)
        self.assertEqual(self.read(self.theme_file("ember")), original)
        self.assertEqual(self.read(self.config), config_before)
        self.assertEqual(themes.current(), "ember")
        self.assertIn("cancelled - ember is untouched",
                      [d["status"] for d in drawn])
        self.assertNotIn("saved theme", out)
        self.assertEqual(err, "")

    def test_a_theme_with_gaps_reports_them_after_the_session(self):
        # §13.2 — a hand-written theme loads with MISSING grey and says so;
        # the picker cannot print inside raw mode, so it reports at exit
        self.write(self.theme_file("gaps"),
                   '# owned by huebox\n[colors]\nbackground = "#123456"\n')
        _, drawn, _, err = self.session(["t", "down", "down", "\r", "esc"])
        self.assertEqual(themes.current(), "gaps")
        self.assertEqual(drawn[-1]["head"], "gaps ghostty")
        self.assertIn("huebox: gaps: 21 slot(s) have no value", err)

    def test_an_illegal_name_comes_back_as_a_status_line(self):
        _, drawn, _, err = self.session(["N", "esc"], answers=["not a name"])
        self.assertEqual(themes.current(), "ember")
        self.assertTrue(any("invalid theme name" in status
                            for status in (d["status"] for d in drawn)))
        self.assertEqual(err, "")

    def test_no_themes_yet_says_so_in_the_status_bar(self):
        self.drop("ember")
        self.drop("frost")
        os.unlink(themes.state_path())
        _, drawn, _, _ = self.session(["t", "esc"], target=self.direct_target())
        self.assertTrue(any("no themes yet" in status
                            for status in (d["status"] for d in drawn)))


class TestReload(LibraryHome):
    """§13.6 - a push that succeeds ends with the terminal showing it.

    The reload is best effort and never load-bearing: a terminal that is
    not there leaves the report with the advice line, and a save that
    failed is not reloaded at all.
    """

    def test_ghostty_is_asked_with_the_signal_its_own_app_handles(self):
        # ghostty 1.3 has no CLI reload action; its application reloads
        # on SIGUSR2, which is what ctrl+shift+, ends up doing anyway
        with mock.patch.object(themes, "ghostty_app_pid", return_value=4242), \
                mock.patch.object(themes.os, "kill") as kill:
            self.assertEqual(themes.reload_terminal("ghostty"),
                             "ghostty: reloaded (config re-read, pid 4242)")
        kill.assert_called_once_with(4242, signal.SIGUSR2)

    def test_a_ghostty_that_is_not_running_is_simply_not_reloaded(self):
        with mock.patch.object(themes, "ghostty_app_pid", return_value=None), \
                mock.patch.object(themes.os, "kill") as kill:
            self.assertEqual(themes.reload_terminal("ghostty"), "")
        kill.assert_not_called()

    def test_a_signal_that_lands_nowhere_is_not_a_failure(self):
        with mock.patch.object(themes, "ghostty_app_pid", return_value=9), \
                mock.patch.object(themes.os, "kill",
                                  side_effect=ProcessLookupError):
            self.assertEqual(themes.reload_terminal("ghostty"), "")

    def test_the_application_is_found_and_not_a_surface(self):
        # a build with per-window processes has more than one `ghostty`
        # in /proc; only the single-instance application is signalled
        def proc(tmp, entries):
            root = os.path.join(tmp, "proc")
            for pid, comm, cmdline in entries:
                os.makedirs(os.path.join(root, pid))
                with open(os.path.join(root, pid, "comm"), "w",
                          encoding="utf-8") as handle:
                    handle.write(comm)
                with open(os.path.join(root, pid, "cmdline"), "wb") as handle:
                    handle.write(cmdline.encode())
            return root

        with tempfile.TemporaryDirectory() as tmp:
            root = proc(tmp, [
                ("7", "ghostty\n",
                 "/usr/bin/ghostty\x00--gtk-single-instance=true\x00"),
                ("11", "ghostty\n",
                 "/usr/bin/ghostty\x00--initial-window=false\x00"),
                ("12", "bash\n", "bash\x00"),
            ])
            with mock.patch.object(themes, "_PROC", root, create=True):
                self.assertEqual(themes.ghostty_app_pid(), 7)

    def test_kitty_is_asked_through_its_own_remote_control(self):
        done = subprocess.CompletedProcess(["kitty"], 0)
        with mock.patch.object(themes.subprocess, "run",
                               return_value=done) as run:
            self.assertEqual(themes.reload_terminal("kitty"),
                             "kitty: reloaded (kitty @ load-config)")
        command = run.call_args[0][0]
        self.assertEqual(command, ["kitty", "@", "load-config"])
        # and it never touches the terminal's tty: a reload that stole a
        # keystroke or printed into the editor's frame is worse than none
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(run.call_args[1][stream], subprocess.DEVNULL)

    def test_a_reload_that_fails_leaves_the_advice_line(self):
        for result, problem in ((subprocess.CompletedProcess(["kitty"], 1),
                                 None),
                                (None, FileNotFoundError("kitty"))):
            with self.subTest(result=result, problem=problem):
                side = problem or None
                with mock.patch.object(themes.subprocess, "run",
                                       return_value=result, side_effect=side):
                    self.assertEqual(themes.reload_terminal("kitty"), "")

    def test_a_format_with_no_interface_is_never_asked(self):
        with mock.patch.object(themes.subprocess, "run") as run:
            self.assertEqual(themes.reload_terminal("alacritty"), "")
        run.assert_not_called()

    def test_a_successful_push_reloads_only_what_it_pushed(self):
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        edited = dict(FULL, background="#010203")
        with mock.patch.object(themes, "reload_terminal",
                               return_value="ghostty: reloaded") as reload:
            result = themes.push(edited, to="ghostty", path=config,
                                 name="ember", reload=True)
        reload.assert_called_once_with("ghostty")
        self.assertEqual(result.reloaded, ("ghostty",))
        self.assertIn("ghostty: reloaded", result.lines)

    def test_a_failed_push_does_not_reload_the_terminal(self):
        blank = self.write(os.path.join(self.root, "blank.ghostty"),
                           "font-size = 12\n")
        with mock.patch.object(themes, "reload_terminal") as reload:
            result = themes.push(FULL, to="ghostty", path=blank, name="ember",
                                 reload=True)
        self.assertTrue(result.failed)
        self.assertEqual(result.reloaded, ())
        reload.assert_not_called()

    def test_a_programmatic_push_does_not_reach_for_a_signal(self):
        # the default is inert: only a command line that says so reloads
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        with mock.patch.object(themes, "reload_terminal") as reload:
            result = themes.push(dict(FULL, background="#010203"),
                                 to="ghostty", path=config, name="ember")
        self.assertEqual(result.reloaded, ())
        reload.assert_not_called()

    def test_the_command_line_reloads_by_default_and_no_reload_opts_out(self):
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        base = ["use", "ember", "--to", "ghostty", "--config", config]
        for argv, wanted in ((base, True), (base + ["--no-reload"], False)):
            with self.subTest(reload=wanted):
                err = io.StringIO()
                with mock.patch.object(themes, "reload_terminal",
                                       return_value="ghostty: reloaded") as reload, \
                        mock.patch.object(sys, "stdout", io.StringIO()), \
                        mock.patch.object(sys, "stderr", err):
                    self.assertEqual(cli.main(argv), 0, err.getvalue())
                self.assertEqual(reload.called, wanted)
                # and the report says which, rather than the advice line
                self.assertEqual("reloaded" in err.getvalue(), wanted)
                self.assertEqual("reload your terminal" in err.getvalue(),
                                 not wanted)


class Ramp(unittest.TestCase):
    """The fallback palette `new` seeds from (spec §13.8 question 3)."""

    def test_ramp_covers_every_slot_with_a_colour(self):
        self.assertEqual(sorted(themes.RAMP), sorted(SLOTS))
        for slot, value in themes.RAMP.items():
            with self.subTest(slot=slot):
                self.assertEqual(value, value.lower())     # §5 value form
                self.assertEqual(len(value), 7)             # #rrggbb
                self.assertTrue(huebox.color.is_hex(value))


class Cli(LibraryHome):
    """The command surface of §13.5, end to end in a subprocess."""

    def test_binary_garbage_theme_loads_with_a_warning(self):
        # robustness pin (P3 review): a malformed theme never crashes, it
        # loads as MISSING slots and warns about the dropped lines
        os.makedirs(themes.themes_dir(), exist_ok=True)
        with open(self.theme_file("garbage"), "wb") as handle:
            handle.write(b"\x00\xff\xfe garbage [\x1b[31m no key\n")
        out = self.run_cli("--dump", "garbage")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("huebox:", out.stderr)
        self.assertIn("background=#808080", out.stdout)

    def test_use_with_ghostty_native_exports_and_points(self):
        # §13.6 end to end: a theme file in ghostty's own dir and
        # one new line in the config, which keeps every colour it had
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        native = os.path.join(self.root, "ghostty", "themes", "ember")
        themes.create("ember", dict(FULL, background="#010203"))
        out = self.run_cli("use", "ember", "--to", "ghostty",
                           "--config", config, "--ghostty-native")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("current theme: ember", out.stdout)
        self.assertIn(f"ghostty: exported {native}", out.stderr)
        self.assertIn(f"ghostty: theme = ember appended in {config}",
                      out.stderr)
        self.assertEqual(themes.read_terminal("ghostty", native),
                         dict(FULL, background="#010203"))
        after = self.read(config)
        self.assertEqual(after.splitlines()[:-1],
                         ghostty_text().splitlines())   # not one colour moved
        self.assertEqual(after.splitlines()[-1], "theme = ember")
        # and the terminal finds its colours through the pointer now
        found = self.run_cli("show")
        self.assertIn("#010203", found.stdout)

    def test_ghostty_native_without_a_push_still_writes_nothing(self):
        # `--no-push` is the answer for "do not touch the terminal", and
        # an export is a push like any other: honouring one costs no flag
        # conflict, so the save writes truth and stops there
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        before, stamp = self.read(config), os.path.getmtime(config)
        out = self.run_cli("use", "ember", "--to", "ghostty",
                           "--config", config, "--ghostty-native", "--no-push")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("no push requested", out.stderr)
        self.assertEqual(self.read(config), before)
        self.assertEqual(os.path.getmtime(config), stamp)
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "ghostty", "themes", "ember")))

    def test_the_two_ghostty_flags_are_refused_together(self):
        out = self.run_cli("use", "ember", "--ghostty-native",
                           "--ghostty-in-place")
        self.assertEqual(out.returncode, 1)
        self.assertIn("opposite things", out.stderr)

    def test_use_writes_a_theme_under_its_own_name_not_the_current_one(self):
        # §13.6 end to end, and the bug this rule exists for: the config
        # is on `Nightspice`, the theme being used is `test`, and the two
        # files stay two files - the pointer moves, Nightspice does not
        threedir = os.path.join(self.root, "ghostty", "themes")
        os.makedirs(threedir, exist_ok=True)
        nightspice = self.write(os.path.join(threedir, "Nightspice"),
                                ghostty_text())
        before = self.read(nightspice)
        config = self.xdg("ghostty/config.ghostty",
                          "# mine\nfont-size = 12\ntheme = Nightspice\n")
        themes.create("test", dict(FULL, background="#010203"))
        out = self.run_cli("use", "test", "--to", "ghostty")
        self.assertEqual(out.returncode, 0, out.stderr)
        exported = os.path.join(threedir, "test")
        self.assertIn(f"ghostty: exported {exported}", out.stderr)
        self.assertIn(f"ghostty: theme = test rewritten in {config}",
                      out.stderr)
        self.assertEqual(themes.read_terminal("ghostty", exported),
                         dict(FULL, background="#010203"))
        self.assertEqual(self.read(nightspice), before)   # untouched
        self.assertIn("theme = test", self.read(config))
        # the terminal now reads `test` through the pointer
        self.assertIn("#010203", self.run_cli("show").stdout)

    def test_in_place_will_not_cross_two_themes_names(self):
        # the same run with the opt-out still refuses: `test`'s colours
        # are never `Nightspice`'s file, on purpose or not
        threedir = os.path.join(self.root, "ghostty", "themes")
        os.makedirs(threedir, exist_ok=True)
        nightspice = self.write(os.path.join(threedir, "Nightspice"),
                                ghostty_text())
        before = self.read(nightspice)
        self.xdg("ghostty/config.ghostty",
                 "# mine\nfont-size = 12\ntheme = Nightspice\n")
        themes.create("test", dict(FULL, background="#010203"))
        out = self.run_cli("use", "test", "--to", "ghostty",
                           "--ghostty-in-place")
        self.assertEqual(out.returncode, 1)
        self.assertIn("refusing to write theme 'test'", out.stderr)
        self.assertEqual(self.read(nightspice), before)
        self.assertIn("theme = Nightspice",
                      self.read(os.path.join(self.root, "ghostty",
                                             "config.ghostty")))

    def test_edit_accepts_the_flag_and_a_pipe_pushes_nothing(self):
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        out = self.run_cli("edit", "ember", "--to", "ghostty",
                           "--config", config, "--ghostty-native")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("not a terminal", out.stderr)
        self.assertNotIn("Traceback", out.stderr)
        self.assertEqual(self.read(config), ghostty_text())
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "ghostty", "themes", "ember")))

    def test_import_snapshots_the_config_without_touching_current(self):
        out = self.run_cli("import", "ember", "--config", self.config)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("imported ember", out.stdout)
        slots = themes.load("ember")
        self.assertEqual(slots, FULL)
        self.assertEqual(themes.source_of("ember"),
                         f"ghostty:{self.config}")
        self.assertFalse(os.path.exists(themes.state_path()))

    def test_import_refuses_to_overwrite_without_force(self):
        self.assertEqual(self.run_cli("import", "ember",
                                      "--config", self.config).returncode, 0)
        again = self.run_cli("import", "ember", "--config", self.config)
        self.assertEqual(again.returncode, 1)
        self.assertIn("huebox: theme ember already exists", again.stderr)
        forced = self.run_cli("import", "ember", "--config", self.config,
                              "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(themes.load("ember"), FULL)

    def test_import_from_an_explicit_format(self):
        out = self.run_cli("import", "ember", "--from", "ghostty",
                           "--config", self.config)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(themes.load("ember"), FULL)

    def test_import_without_a_config_fails_cleanly(self):
        out = self.run_cli("import", "ember")
        self.assertEqual(out.returncode, 1)
        self.assertTrue(out.stderr.startswith("huebox: "), out.stderr)
        self.assertNotIn("Traceback", out.stderr)

    def test_new_seeds_from_the_detected_terminal(self):
        out = self.run_cli("new", "ember", "--format", "ghostty",
                           "--config", self.config)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(themes.load("ember"), FULL)
        self.assertEqual(themes.current(), "ember")
        self.assertIn("seeded from ghostty:", out.stdout)

    def test_new_falls_back_to_the_ramp(self):
        out = self.run_cli("new", "slate")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("built-in ramp", out.stdout)
        self.assertEqual(themes.load("slate"), themes.RAMP)
        self.assertEqual(themes.current(), "slate")
        self.assertEqual(themes.source_of("slate"), "")

    def test_new_refuses_an_existing_name_and_illegal_names(self):
        self.assertEqual(self.run_cli("new", "ember", "--config",
                                      self.config).returncode, 0)
        again = self.run_cli("new", "ember", "--config", self.config)
        self.assertEqual(again.returncode, 1)
        self.assertIn("already exists", again.stderr)
        for name in ("bad name", "a" * 65, "a.b"):
            with self.subTest(name=name):
                out = self.run_cli("new", name)
                self.assertEqual(out.returncode, 1)
                self.assertIn("invalid theme name", out.stderr)
        for args in (("new",), ("new", ""), ("import",), ("use",)):
            with self.subTest(args=args):
                out = self.run_cli(*args)
                self.assertEqual(out.returncode, 1)
                self.assertIn("needs a theme name", out.stderr)

    def test_list_marks_the_current_theme(self):
        self.assertIn("no themes yet", self.run_cli("list").stdout)
        self.run_cli("import", "ember", "--config", self.config)
        self.run_cli("import", "slate", "--config", self.config)
        self.run_cli("use", "ember")
        out = self.run_cli("list").stdout.splitlines()
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0].startswith("* ember"), out)
        self.assertIn("ghostty", out[0])
        self.assertTrue(out[1].startswith("  slate"), out)
        self.assertIn("-", out[1])                # no source recorded

    def test_use_sets_current_and_pushes_to_the_detected_terminal(self):
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        self.run_cli("import", "ember", "--config", self.config)
        themes.save("ember", dict(FULL, background="#010203"))
        out = self.run_cli("use", "ember")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(themes.current(), "ember")
        self.assertIn("current theme: ember", out.stdout)
        self.assertIn(f"ghostty: pushed to {config}", out.stderr)
        self.assertIn("reload your terminal", out.stderr)
        after = self.read(config)
        self.assertIn("background = #010203", after)
        changed = [i for i, (a, b)
                   in enumerate(zip(ghostty_text().splitlines(),
                                    after.splitlines())) if a != b]
        self.assertEqual(len(changed), 1, changed)

    def test_use_without_a_terminal_fails_but_keeps_the_theme_current(self):
        # the fixture sits outside the XDG tree, so there is no terminal to
        # detect: truth first, report second, exit 1 (decision 7)
        self.run_cli("import", "ember", "--config", self.config)
        out = self.run_cli("use", "ember")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(themes.current(), "ember")
        self.assertIn("no terminal config with colours", out.stderr)
        self.assertNotIn("Traceback", out.stderr)

    def test_use_no_push_writes_truth_only(self):
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        self.run_cli("import", "ember", "--config", self.config)
        before, stamp = self.read(config), os.path.getmtime(config)
        out = self.run_cli("use", "ember", "--no-push")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(themes.current(), "ember")
        self.assertEqual(self.read(config), before)
        self.assertEqual(os.path.getmtime(config), stamp)
        self.assertIn("--no-push", out.stderr)

    def test_use_to_several_targets_pushes_all_of_them(self):
        ghostty = self.xdg("ghostty/config.ghostty", ghostty_text())
        kitty = self.xdg("kitty/kitty.conf", kitty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        out = self.run_cli("use", "ember", "--to", "ghostty,kitty")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("background = #010203", self.read(ghostty))
        self.assertIn("background            #010203", self.read(kitty))

    def test_use_to_a_deduplicated_list_pushes_each_format_once(self):
        # P4 review: `--to ghostty,ghostty` must behave like `--to ghostty`
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        out = self.run_cli("use", "ember", "--to", "ghostty,ghostty")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr.count("pushed to"), 1)
        self.assertIn("background = #010203", self.read(config))

    def test_use_to_an_empty_list_is_refused(self):
        # P4 review: `--to ""` must not silently fall back to detection
        themes.create("ember", dict(FULL))
        out = self.run_cli("use", "ember", "--to", "")
        self.assertEqual(out.returncode, 1)
        self.assertIn("--to needs at least one format", out.stderr)

    def test_use_config_with_a_single_to_pushes_that_one_file(self):
        # P4 review critical: the README's example is single --to + --config
        # and must work; only SEVERAL --to targets with --config are refused
        dotfiles = self.xdg("dotfiles/ghostty-config", ghostty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        out = self.run_cli("use", "ember", "--to", "ghostty",
                           "--config", dotfiles)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(f"ghostty: pushed to {dotfiles}", out.stderr)
        self.assertIn("background = #010203", self.read(dotfiles))

    def test_use_config_with_several_to_is_refused_before_any_write(self):
        # several --to targets with --config is an ambiguity, refused before
        # any write (P4 review: single --to + --config stays legal)
        dotfiles = self.xdg("dotfiles/ghostty-config", ghostty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        before, stamp = self.read(dotfiles), os.path.getmtime(dotfiles)
        out = self.run_cli("use", "ember", "--to", "ghostty,kitty",
                           "--config", dotfiles)
        self.assertEqual(out.returncode, 1)
        self.assertIn("--config pushes one format", out.stderr)
        self.assertEqual(self.read(dotfiles), before)
        self.assertEqual(os.path.getmtime(dotfiles), stamp)
        self.assertFalse(os.path.exists(themes.state_path()))

    def test_to_is_validated_before_anything_is_written(self):
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        before = self.read(config)
        for bad in ("wezterm", "ghostty,nope"):
            with self.subTest(to=bad):
                out = self.run_cli("use", "ember", "--to", bad)
                self.assertEqual(out.returncode, 1)
                self.assertIn("unknown format", out.stderr)
        blank = self.run_cli("use", "ember", "--to", " , ")
        self.assertEqual(blank.returncode, 1)
        self.assertIn("--to needs at least one format", blank.stderr)
        self.assertEqual(self.read(config), before)
        self.assertFalse(os.path.exists(themes.state_path()))

    def test_to_with_config_asks_for_one_file(self):
        out = self.run_cli("use", "ember", "--to", "ghostty,kitty",
                           "--config", self.config)
        self.assertEqual(out.returncode, 1)
        self.assertIn("--config pushes one format", out.stderr)
        self.assertFalse(os.path.exists(themes.state_path()))

    def test_a_save_side_flag_never_pushes_on_dump(self):
        config = self.xdg("ghostty/config.ghostty", ghostty_text())
        themes.create("ember", dict(FULL, background="#010203"))
        stamp = os.path.getmtime(config)
        out = self.run_cli("--dump", "ember", "--to", "ghostty")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("background=#010203", out.stdout)
        self.assertEqual(self.read(config), ghostty_text())
        self.assertEqual(os.path.getmtime(config), stamp)

    def test_use_refuses_a_missing_or_illegal_theme(self):
        for name, args in (("ghost", ()), ("bad name", ()), ((), ())):
            with self.subTest(name=name):
                bad = self.run_cli("use", *args)
                self.assertEqual(bad.returncode, 1)
        self.assertIn("no such theme", self.run_cli("use", "ghost").stderr)
        self.assertIn("invalid theme name",
                      self.run_cli("use", "bad name").stderr)
        self.assertIn("needs a theme name", self.run_cli("use").stderr)

    def test_dump_of_a_theme_is_header_plus_every_slot(self):
        self.run_cli("import", "ember", "--config", self.config)
        out = self.run_cli("--dump", "ember")
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[0], f"# theme ember {self.theme_file('ember')}")
        self.assertEqual(lines[1:], [f"{slot}={FULL[slot]}" for slot in SLOTS])

    def test_show_renders_a_theme_and_a_missing_one_fails(self):
        self.run_cli("import", "ember", "--config", self.config)
        out = self.run_cli("show", "ember")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("theme ember", out.stdout)
        self.assertIn(FULL["background"], out.stdout)   # the interface row
        self.assertIn(FULL["selection-background"], out.stdout)
        missing = self.run_cli("show", "ghost")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no such theme: ghost", missing.stderr)

    def test_edit_falls_back_to_direct_mode_when_state_dangles(self):
        self.run_cli("import", "ember", "--config", self.config)
        self.run_cli("use", "ember")
        self.drop("ember")
        out = self.run_cli("edit", "--format", "ghostty",
                           "--config", self.config)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("current theme ember no longer exists", out.stderr)
        self.assertIn("not a terminal", out.stderr)

    def test_edit_without_a_current_theme_stays_on_the_config(self):
        os.makedirs(themes.themes_dir(), exist_ok=True)
        themes.create("ember", FULL)
        out = self.run_cli("edit", "--format", "ghostty",
                           "--config", self.config)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("no current theme set", out.stderr)

    def test_a_theme_load_warns_about_gaps_and_unknown_keys(self):
        path = self.theme_file("hollow")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write(path, '[colors]\nbackground = "#010203"\n'
                         'sparkle = "#040506"\n')
        out = self.run_cli("--dump", "hollow")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("unknown key", out.stderr)
        self.assertIn("no value", out.stderr)

    def test_bare_huebox_follows_the_current_theme(self):
        self.run_cli("import", "ember", "--config", self.config)
        self.run_cli("use", "ember")
        out = self.run_cli()          # piped: a preview, of the current theme
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("theme ember", out.stdout)
        # an explicit `show` still means the terminal config
        explicit = self.run_cli("show", "--config", self.config)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertIn("ghostty", explicit.stdout)
        self.assertIn(self.config, explicit.stdout)
        self.assertNotIn("theme ember", explicit.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

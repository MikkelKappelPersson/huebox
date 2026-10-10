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

    def test_the_default_pushes_every_terminal_holding_colours(self):
        def ask(wanted, _explicit):
            if wanted == "ghostty":
                return ("ghostty", self.config, None)
            if wanted == "kitty":
                return ("kitty", self.kitty, None)
            return (None, None, "no alacritty config with colours found")
        with mock.patch.object(themes, "resolve", side_effect=ask):
            result = themes.push(dict(FULL, background="#010203"))
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed,
                         (("ghostty", self.config), ("kitty", self.kitty)))
        self.assertIn("background = #010203", self.read(self.config))

    def test_the_default_skips_terminals_you_do_not_have(self):
        def ask(wanted, _explicit):
            if wanted == "ghostty":
                return ("ghostty", self.config, None)
            return (None, None, f"no {wanted} config with colours found")
        with mock.patch.object(themes, "resolve", side_effect=ask):
            result = themes.push(dict(FULL, background="#010203"))
        self.assertFalse(result.failed)
        self.assertEqual(result.pushed, (("ghostty", self.config),))

    def test_the_default_with_no_terminals_at_all_fails(self):
        with mock.patch.object(themes, "resolve",
                               return_value=(None, None, "nothing here")), \
                mock.patch.object(themes, "ghostty_main_config",
                                  return_value=None):
            result = themes.push(FULL)
        self.assertTrue(result.failed)
        self.assertEqual(result.pushed, ())
        self.assertIn("found no terminal config with colours",
                      "\n".join(result.lines))

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
        # palette-5 starts near-black: a hue nudge would not show on a grey
        self.session(["c", editor.SAVE_KEY, "esc"])
        saved = themes.load("ember")
        self.assertNotEqual(saved["palette-5"], FULL["palette-5"])
        self.assertIn(f'palette-5 = "{saved["palette-5"]}"',
                      self.read(self.theme_file("ember")))
        self.assertFalse(os.path.exists(self.theme_file("ember")
                                        + ".huebox.bak"))

    def test_quitting_without_saving_leaves_the_file_alone(self):
        themes.create("ember", FULL)
        original = self.read(self.theme_file("ember"))
        self.session(["c", "esc", "esc"])
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
        # the fixture carries color0: walk back from INITIAL_SEL to hit it
        status, out, err = self.session(["left", "left", "left", "left",
                                         "left", "c", editor.SAVE_KEY,
                                         "esc"], spec)
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

        # a hermetic `~`: the push asks `ghostty_main_config` for the
        # config holding the `theme =` line, and ghostty has no env
        # override — only `~/.config/...` defaults. Without this the test
        # reads the machine's real home and passes only where a ghostty
        # config happens to exist (and takes the export path where none
        # does, which fails the save).
        main = os.path.join(self.root, ".config", "ghostty", "config")
        os.makedirs(os.path.dirname(main), exist_ok=True)
        self.write(main, ghostty_text())
        spec = cli.PushSpec(("ghostty", "kitty"), None, None, False)
        with mock.patch.dict(os.environ, {"HOME": self.root}), \
                mock.patch.object(themes, "resolve", side_effect=fake):
            status, out, _ = self.session(["c", editor.SAVE_KEY, "esc"], spec)
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
        status, out, err = self.session(["c", editor.SAVE_KEY, "esc"], spec)
        self.assertEqual(status, 0)
        self.assertNotEqual(themes.load("ember")["palette-5"],
                            FULL["palette-5"])
        self.assertEqual(self.read(self.kitty), self.before)
        self.assertEqual(os.path.getmtime(self.kitty), stamp)
        self.assertIn("saved ember (truth only)", out)
        self.assertIn("--no-push", err)

    def test_a_failed_push_keeps_the_truth_and_exits_1(self):
        # decision 7: the theme file is written first and never rolled back
        blank = self.write(os.path.join(self.root, "blank.ghostty"),
                           "font-size = 12\n")
        spec = cli.PushSpec(("ghostty",), None, blank, False)
        status, out, err = self.session(["c", editor.SAVE_KEY, "esc"], spec)
        self.assertEqual(status, 1)
        saved = themes.load("ember")
        self.assertNotEqual(saved["palette-5"], FULL["palette-5"])
        self.assertIn(f'palette-5 = "{saved["palette-5"]}"',
                      self.read(self.theme_file("ember")))
        self.assertEqual(self.read(blank), "font-size = 12\n")
        self.assertIn("saved ember - push failed", out)
        self.assertIn("huebox: ghostty: no colours in", err)

    def test_a_session_with_no_spec_pushes_nothing(self):
        # the programmatic default: `edit()` without a command line writes
        # truth only, and never reaches for a terminal nobody named
        status, out, _ = self.session(["c", editor.SAVE_KEY, "esc"], None)
        self.assertEqual(status, 0)
        self.assertEqual(self.read(self.kitty), self.before)
        self.assertIn("saved ember (truth only)", out)

    def test_ctrl_a_pushes_without_touching_truth(self):
        spec = cli.PushSpec(("kitty",), None, self.kitty, False)
        original = self.read(self.theme_file("ember"))
        status, out, err = self.session(["left", "left", "left", "left",
                                         "left", "c", editor.APPLY_KEY,
                                         "esc", "esc"], spec)
        self.assertEqual(status, 0)
        self.assertEqual(self.read(self.theme_file("ember")), original)
        self.assertEqual(themes.load("ember"), FULL)
        after = self.read(self.kitty)
        self.assertNotEqual(after, self.before)
        self.assertIn("applied (unsaved) → kitty", out)
        self.assertIn(f"huebox: kitty: pushed to {self.kitty}", err)

    def test_ctrl_a_with_no_push_pushes_nothing(self):
        spec = cli.PushSpec((), None, self.kitty, True)
        status, out, _ = self.session(["c", editor.APPLY_KEY,
                                       "esc", "esc"], spec)
        self.assertEqual(status, 0)
        self.assertEqual(self.read(self.kitty), self.before)
        self.assertEqual(themes.load("ember"), FULL)
        self.assertIn("apply blocked (--no-push)", out)

    def test_ctrl_a_failed_push_exits_1_and_keeps_truth(self):
        blank = self.write(os.path.join(self.root, "blank.ghostty"),
                           "font-size = 12\n")
        spec = cli.PushSpec(("ghostty",), None, blank, False)
        status, out, err = self.session(["c", editor.APPLY_KEY,
                                         "esc", "esc"], spec)
        self.assertEqual(status, 1)
        self.assertEqual(themes.load("ember"), FULL)
        self.assertEqual(self.read(blank), "font-size = 12\n")
        self.assertIn("applied (unsaved) - push failed", out)
        self.assertIn("huebox: ghostty: no colours in", err)


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
        status, out, err = self.session(["c", editor.SAVE_KEY, "esc"],
                                        self.native_spec())
        self.assertEqual(status, 0)
        saved = themes.load("ember")             # truth first, always
        self.assertNotEqual(saved["palette-5"], FULL["palette-5"])
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
        real_lines = editor.theme_lines
        answers = list(answers)

        def record(*args, **kwargs):
            drawn.append({"status": args[5], "head": kwargs.get("head"),
                          "overlay": None})
            return real(*args, **kwargs)

        def record_picker(names, index, current, cols, rows, *args, **kwargs):
            # §13.7 — since phase 5 the picker is its own frame, drawn by
            # `theme_lines` and not by `draw_editor`, so a hook on `draw_editor`
            # alone stops seeing the picker. That is not a smaller hook: the
            # blocked-switch status is *reported on a picker frame*, and a hook
            # that missed those frames would say the message was never written.
            status = args[0] if args else ""
            drawn.append({"status": status, "head": None,
                          "overlay": (names, index, current)})
            return real_lines(names, index, current, cols, rows, *args,
                              **kwargs)

        editor.theme_lines = record_picker

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
        editor.theme_lines = real_lines
        return status, drawn, out.getvalue(), err.getvalue()

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

    def test_a_dirty_switch_is_blocked_and_says_exactly_why(self):
        before = self.read(self.theme_file("frost"))
        status, drawn, _, err = self.session(["c", "t", "down", "\r",
                                              "esc", "r", "esc"])
        self.assertEqual(status, 0)
        self.assertEqual(themes.current(), "ember")       # decision 12
        self.assertEqual(self.read(self.theme_file("frost")), before)
        self.assertEqual(themes.load("ember"), FULL)
        self.assertIn("save (Ctrl+S) or revert (r) first",
                      [d["status"] for d in drawn])
        self.assertEqual(err, "")

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

    def test_kitty_is_asked_with_the_signal_its_own_app_handles(self):
        # `kill -SIGUSR1 $KITTY_PID` is kitty's documented manual reload;
        # a signal needs no tty, while `kitty @` without a socket talks
        # through the controlling terminal's escape codes — under the
        # editor that channel is the app's own input.
        with mock.patch.dict(os.environ, {"KITTY_PID": "4242"}), \
                mock.patch.object(themes.os, "kill") as kill:
            self.assertEqual(themes.reload_terminal("kitty"),
                             "kitty: reloaded (config re-read, pid 4242)")
        kill.assert_called_once_with(4242, signal.SIGUSR1)

    def test_kitty_without_its_pid_falls_back_to_remote_control(self):
        done = subprocess.CompletedProcess(["kitty"], 0)
        with mock.patch.dict(os.environ, {"KITTY_PID": ""}), \
                mock.patch.object(themes, "_in_kitty", return_value=True), \
                mock.patch.object(themes.subprocess, "run",
                                  return_value=done) as run:
            self.assertEqual(themes.reload_terminal("kitty"),
                             "kitty: reloaded (kitty @ load-config)")
        command = run.call_args[0][0]
        self.assertEqual(command, ["kitty", "@", "load-config"])
        # and it never touches the terminal's tty: a reload that stole a
        # keystroke or printed into the editor's frame is worse than none
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(run.call_args[1][stream], subprocess.DEVNULL)

    def test_kitty_outside_kitty_skips_remote_control(self):
        """No kitty around means no tty fallback: from another terminal
        the escape-code channel has nobody to answer, so trying it is a
        doomed wait for the whole subprocess timeout per save."""
        with mock.patch.dict(os.environ, {"KITTY_PID": "", "KITTY_LISTEN_ON": ""}), \
                mock.patch.object(themes, "_in_kitty", return_value=False), \
                mock.patch.object(themes.subprocess, "run") as run:
            self.assertEqual(themes.reload_terminal("kitty"), "")
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

    def test_import_snapshots_the_config_without_touching_current(self):
        out = self.run_cli("import", "ember", "--config", self.config)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("imported ember", out.stdout)
        slots = themes.load("ember")
        self.assertEqual(slots, FULL)
        self.assertEqual(themes.source_of("ember"),
                         f"ghostty:{self.config}")
        self.assertFalse(os.path.exists(themes.state_path()))

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

if __name__ == "__main__":
    unittest.main(verbosity=2)

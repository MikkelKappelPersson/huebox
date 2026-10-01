"""Theme library: names, the canonical writer, gap-tolerant reads, state,
and the CLI surface of §13.1-13.5.

Every test gets its own `XDG_CONFIG_HOME`, so nothing here reads or writes
the developer's real `~/.config/huebox`.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402
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

    def run_cli(self, *args):
        """`python -m huebox` with a home that has no terminal in it."""
        env = {key: value for key, value in os.environ.items()
           if not any(mark in key for mark in
                      ("GHOSTTY", "KITTY", "ALACRITTY", "WEZTERM",
                       "TERM_PROGRAM"))}
        env["TERM_PROGRAM"] = ""           # probes must see no terminal
        env["XDG_CONFIG_HOME"] = self.root
        env["HOME"] = self.root
        return subprocess.run([sys.executable, "-m", "huebox", *args],
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

    def session(self, keys):
        stream = iter(keys)
        with mock.patch.object(sys, "stdin", FakeTTY()), \
                mock.patch.object(sys, "stdout", FakeOut()), \
                mock.patch.object(editor, "enter_raw",
                                  return_value=(7, None)), \
                mock.patch.object(editor, "exit_raw"), \
                mock.patch.object(editor, "read_key",
                                  side_effect=lambda fd: next(stream)):
            cli._run_editor(cli.Target("ember", "theme ember",
                                       self.theme_file("ember"),
                                       themes.load("ember")))

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

    def test_use_sets_current_and_stops_there(self):
        self.run_cli("import", "ember", "--config", self.config)
        out = self.run_cli("use", "ember")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(themes.current(), "ember")
        self.assertIn("current theme: ember", out.stdout)
        for name, args in (("ghost", ()), ("bad name", ()), ((), ())):
            with self.subTest(name=name):
                bad = self.run_cli("use", *args)
                self.assertEqual(bad.returncode, 1)
        self.assertIn("no such theme", self.run_cli("use", "ghost").stderr)
        self.assertIn("invalid theme name",
                      self.run_cli("use", "bad name").stderr)

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

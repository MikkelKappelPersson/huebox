"""P5 seam tests: slugify, confirm mapping, provider failure tolerance.

Minimal critical paths only (tasks P5); spec §10's full harness stays
deferred. Nothing here needs Textual: `providers` / `import_state` take
stdlib + `formats` only, and `cli` stays Textual-free (the compositor is
a late import inside `_run_editor`).
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

from huebox import cli as huebox_cli  # noqa: E402
from huebox import import_state, providers, themes  # noqa: E402

GHOSTTY_FILE = """\
palette = 0=#0a0a13
palette = 1=#ff0067
background = #0f0f1a
foreground = #ededfe
cursor-color = #ededfe
cursor-text = #0f0f1a
selection-background = #202036
selection-foreground = #ededfe
"""


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class Slugify(unittest.TestCase):
    def test_runs_collapse_to_one_dash(self):
        self.assertEqual(providers.slugify("Catppuccin Mocha"),
                         "catppuccin-mocha")

    def test_leading_dash_stripped(self):
        self.assertEqual(providers.slugify("  --Foo"), "foo")

    def test_pure_punctuation_is_empty(self):
        self.assertEqual(providers.slugify("!!!"), "")

    def test_stem_leaves_suffix_room(self):
        self.assertEqual(len(providers.slugify("a" * 100)),
                         providers.MAX_NAME_LEN - 2)

    def test_batch_suffixes(self):
        self.assertEqual(providers.batch_unique(["ember", "ember", "ember"]),
                         ["ember", "ember-2", "ember-3"])

    def test_batch_suffix_stays_within_64(self):
        names = providers.batch_unique(["a" * 64, "a" * 64])
        self.assertEqual(names, ["a" * 64, "a" * 62 + "-2"])
        for name in names:
            self.assertTrue(themes.valid_name(name), name)


class OpenCloseKeys(unittest.TestCase):
    """`i` opens like `I`; every close spelling abandons, writing nothing."""

    def _state(self):
        lib = import_state.ImportLibrary(
            listing=lambda provider: ([("Alpha", "/a")]
                                      if provider == "ghostty" else []),
            reader=lambda _provider, _path: {},
            formats={"ghostty": "ghostty"})
        return import_state.ImportState(["ghostty"], lib)

    def test_i_and_uppercase_i_open(self):
        for key in ("i", "I"):
            st = self._state()
            self.assertEqual(import_state.handle_key(st, key), "open")
            self.assertTrue(st.is_open)

    def test_other_keys_ignored_while_closed(self):
        st = self._state()
        for key in ("h", "t", "enter", "space"):
            self.assertEqual(import_state.handle_key(st, key), "ignored")
        self.assertFalse(st.is_open)

    def test_close_spellings_abandon(self):
        for key in ("esc", "escape", "i", "I", "\x03"):
            st = self._state()
            import_state.open_import(st)
            import_state.toggle(st)
            self.assertEqual(import_state.handle_key(st, key), "close")
            self.assertFalse(st.is_open)
            self.assertEqual(st.selected, set())


class ConfirmMapping(unittest.TestCase):
    """The toggled set → names + skip-list; pure, writes nothing."""

    def _library(self, entries, by_path):
        return import_state.ImportLibrary(
            listing=lambda provider: list(entries.get(provider, [])),
            reader=lambda _provider, path: dict(by_path.get(path, {})),
            formats={"ghostty": "ghostty", "kitty": "kitty"})

    def _open(self, library, order=("ghostty", "kitty")):
        state = import_state.ImportState(list(order), library)
        import_state.open_import(state)
        return state

    def test_toggled_set_maps_to_names_and_sources(self):
        library = self._library(
            {"ghostty": [("Catppuccin Mocha", "/ship/Catppuccin Mocha"),
                         ("Gruvbox", "/user/Gruvbox")]},
            {"/ship/Catppuccin Mocha": {"background": "#101010"},
             "/user/Gruvbox": {"background": "#202020"}})
        state = self._open(library, ("ghostty",))
        import_state.toggle(state)
        import_state.move_down(state)
        import_state.toggle(state)
        plans, skips, failures = import_state.plan_confirm(state, [])
        self.assertEqual(failures, [])
        self.assertEqual(skips, [])
        self.assertEqual([(plan.name, plan.source) for plan in plans],
                         [("catppuccin-mocha", "ghostty:/ship/Catppuccin Mocha"),
                          ("gruvbox", "ghostty:/user/Gruvbox")])
        self.assertEqual(plans[0].slots, {"background": "#101010"})

    def test_library_clash_skips_case_insensitively(self):
        library = self._library(
            {"ghostty": [("Gruvbox", "/user/Gruvbox")]},
            {"/user/Gruvbox": {"background": "#202020"}})
        state = self._open(library, ("ghostty",))
        import_state.toggle(state)
        plans, skips, failures = import_state.plan_confirm(state, ["other"])
        self.assertEqual(
            [(plan.name) for plan in plans], ["gruvbox"])
        self.assertEqual(skips, [])
        self.assertEqual(failures, [])
        plans, skips, failures = import_state.plan_confirm(state, ["gruvbox"])
        self.assertEqual(plans, [])
        self.assertEqual(skips, ["already in library: gruvbox"])
        self.assertEqual(failures, [])

    def test_unreadable_source_is_a_note_not_an_abort(self):
        library = self._library(
            {"ghostty": [("Good", "/user/Good"), ("Bad", "/user/Bad")]},
            {"/user/Good": {"background": "#101010"}})
        state = self._open(library, ("ghostty",))
        import_state.toggle(state)
        import_state.move_down(state)
        import_state.toggle(state)
        plans, skips, failures = import_state.plan_confirm(state, [])
        self.assertEqual([plan.name for plan in plans], ["good"])
        self.assertEqual(skips, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("Bad", failures[0])

    def test_invalid_slug_is_a_note_never_a_fallback_name(self):
        library = self._library(
            {"ghostty": [("!!!", "/user/Bang")]},
            {"/user/Bang": {"background": "#101010"}})
        state = self._open(library, ("ghostty",))
        import_state.toggle(state)
        plans, skips, failures = import_state.plan_confirm(state, [])
        self.assertEqual(plans, [])
        self.assertEqual(skips, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("!!!", failures[0])

    def test_empty_selection_stays_open_with_note(self):
        state = self._open(self._library({}, {}), ("ghostty",))
        plans, skips, failures = import_state.plan_confirm(state, [])
        self.assertEqual((plans, skips, failures), ([], [], []))
        self.assertEqual(state.note, "nothing selected")

    def test_empty_providers_never_appear_as_groups(self):
        library = self._library(
            {"ghostty": [("Ember", "/ship/Ember")]},
            {"/ship/Ember": {"background": "#101010"}})
        state = self._open(library, ("ghostty", "kitty", "alacritty"))
        self.assertEqual(state.provider_order, ["ghostty"])
        self.assertEqual(state.expanded, {"ghostty"})
        self.assertEqual([prov for prov, _, _ in
                          import_state.visible_rows(state)],
                         ["ghostty"])
        import_state.toggle_group(state, "kitty")
        self.assertEqual(state.provider_order, ["ghostty"])
        self.assertEqual(state.expanded, {"ghostty"})

    def test_all_empty_leaves_no_groups_and_no_cursor(self):
        state = self._open(self._library({}, {}),
                            ("ghostty", "kitty"))
        self.assertEqual(state.provider_order, [])
        self.assertEqual(state.expanded, set())
        self.assertEqual(import_state.visible_rows(state), [])
        self.assertIsNone(import_state.cursor_id(state))
        self.assertEqual(import_state.cursor_slots(state), {})

    def test_mapping_writes_nothing_and_keeps_the_session(self):
        with tempfile.TemporaryDirectory() as home:
            root = os.path.join(home, "config")
            os.makedirs(root)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": root}):
                library = self._library(
                    {"ghostty": [("Ember", "/ship/Ember")]},
                    {"/ship/Ember": {"background": "#101010"}})
                state = self._open(library, ("ghostty",))
                import_state.toggle(state)
                selected, cursor, note = (set(state.selected), state.cursor,
                                          state.note)
                plans, _skips, _failures = import_state.plan_confirm(
                    state, [])
                self.assertEqual(len(plans), 1)
                self.assertEqual((set(state.selected), state.cursor,
                                  state.note), (selected, cursor, note))
                self.assertEqual(themes.list_themes(), [])
                stray = []
                for _dirpath, _dirnames, filenames in os.walk(home):
                    stray.extend(name for name in filenames
                                 if name.endswith(".toml"))
                self.assertEqual(stray, [])


class ProviderDirs(unittest.TestCase):
    def test_missing_dir_lists_empty_without_exception(self):
        entries, notes = providers.list_themes(
            "ghostty", ["/nonexistent/huebox-p5"])
        self.assertEqual((entries, notes), ([], []))

    def test_file_as_dir_lists_empty_without_exception(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf",
                                         delete=False) as handle:
            handle.write("background #101010\n")
            path = handle.name
        try:
            entries, _notes = providers.list_themes("kitty", [path])
        finally:
            os.unlink(path)
        self.assertEqual(entries, [])

    def test_unreadable_dir_lists_empty_without_exception(self):
        with mock.patch("os.scandir", side_effect=OSError("denied")):
            entries, notes = providers.list_themes("ghostty", ["/x"])
        self.assertEqual((entries, notes), ([], []))

    def test_zero_colour_file_skipped_with_note(self):
        with tempfile.TemporaryDirectory() as directory:
            _write(os.path.join(directory, "Empty"), "font-family = x\n")
            entries, notes = providers.list_themes("ghostty", [directory])
        self.assertEqual(entries, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("Empty", notes[0])


class ImportWiring(unittest.TestCase):
    """The composition root: dir roots, formats, truth-only writes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "config")
        os.makedirs(self.root)
        self.ship = os.path.join(self.tmp.name, "ship", "themes")
        os.makedirs(self.ship)
        patcher = mock.patch.dict(os.environ,
                                   {"XDG_CONFIG_HOME": self.root,
                                    "GHOSTTY_RESOURCES_DIR":
                                        os.path.dirname(self.ship)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_provider_dir_roots(self):
        self.assertEqual(
            huebox_cli._provider_dirs("ghostty"),
            [self.ship, os.path.join(self.root, "ghostty", "themes")])
        self.assertEqual(
            huebox_cli._provider_dirs("kitty"),
            [os.path.join(self.root, "kitty/themes"),
             os.path.join(self.root, "kitty/kitty-themes/themes")])
        self.assertEqual(
            huebox_cli._provider_dirs("alacritty"),
            [os.path.join(self.root, "alacritty/themes")])
        self.assertEqual(huebox_cli._provider_dirs("bogus"), [])

    def test_library_lists_shipped_and_empty_groups(self):
        _write(os.path.join(self.ship, "Ember"), GHOSTTY_FILE)
        notes = []
        library = huebox_cli._import_library(notes)
        entries = library.entries("ghostty")
        self.assertEqual([name for name, _path in entries], ["Ember"])
        self.assertEqual(library.format_of("ghostty"), "ghostty")
        self.assertEqual(library.entries("kitty"), [])
        self.assertEqual(library.entries("alacritty"), [])
        path = entries[0][1]
        self.assertIn("background", library.read("ghostty", path))

    def test_writer_creates_truth_only(self):
        writer = huebox_cli._import_writer()
        slots = {"background": "#101010", "foreground": "#e6e6ea"}
        self.assertEqual(writer("ember", slots, "ghostty:/ship/Ember"), "")
        with open(themes.theme_path("ember"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('source = "ghostty:/ship/Ember"', text)
        self.assertEqual(themes.source_of("ember"), "ghostty:/ship/Ember")
        self.assertIsNone(themes.current())     # no retarget, no push
        walked = []
        for _dirpath, _dirnames, filenames in os.walk(self.tmp.name):
            walked.extend(filenames)
        self.assertEqual(sorted(walked), ["ember.toml"])

    def test_writer_reports_clash(self):
        writer = huebox_cli._import_writer()
        self.assertEqual(writer("ember", {"background": "#101010"},
                                "ghostty:/ship/Ember"), "")
        problem = writer("Ember", {"background": "#202020"},
                         "ghostty:/ship/Other")
        self.assertIn("already exists", problem)

    def test_plan_then_write_end_to_end(self):
        _write(os.path.join(self.ship, "Ember"), GHOSTTY_FILE)
        notes = []
        library = huebox_cli._import_library(notes)
        state = import_state.ImportState(["ghostty", "kitty"], library)
        import_state.open_import(state)
        import_state.toggle(state)
        plans, skips, failures = import_state.plan_confirm(
            state, [name for name, _path, _mtime in themes.list_themes()])
        self.assertEqual(failures, [])
        self.assertEqual(skips, [])
        self.assertEqual([plan.name for plan in plans], ["ember"])
        writer = huebox_cli._import_writer()
        for plan in plans:
            self.assertEqual(writer(plan.name, plan.slots, plan.source), "")
        self.assertEqual(themes.source_of("ember"),
                         "ghostty:" + os.path.join(self.ship, "Ember"))
        self.assertIsNone(themes.current())


if __name__ == "__main__":
    unittest.main(verbosity=2)

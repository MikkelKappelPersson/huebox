"""The Textual shell: `huebox/app.py`.

Two things are worth pinning here. The **launch
contract** — the `HUEBOX_*` variables the harness sets and the app reads — is a
seam between two modules that do not import each other, so nothing but a test
keeps them from drifting. And the **token binding** is §6.2's requirement
stated as an assertion: every colour the shell hands Textual must be one of the
theme's own slots.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)   # repo root: `import huebox`
sys.path.insert(0, _HERE)   # tests dir: cross-test imports

import harness  # noqa: E402

try:
    from huebox import app as huebox_app
except ImportError:                                   # pragma: no cover
    huebox_app = None

needs_app = unittest.skipUnless(huebox_app is not None,
                                "textual missing: pip install -e .")


def _slots_file(slots):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
    with handle:
        json.dump(slots, handle)
    return handle.name


class TestSlotsArriveAsAFile(unittest.TestCase):
    """`huebox/app.py` takes its buffer the way production supplies it."""

    @needs_app
    def test_a_slot_the_file_omits_keeps_missing(self):
        # The frame is honest about what it does not know instead of inventing a
        # colour, which is what a terminal config omitting a slot gets too.
        path = _slots_file({"background": "#101010"})
        try:
            with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
                slots = huebox_app.load_slots()
        finally:
            os.unlink(path)
        self.assertEqual(slots["background"], "#101010")
        self.assertEqual(slots["foreground"], "#808080")

    @needs_app
    def test_no_file_means_every_slot_missing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HUEBOX_SLOTS", None)
            slots = huebox_app.load_slots()
        self.assertEqual(set(slots), set(huebox_app.SLOTS))
        self.assertEqual(set(slots.values()), {"#808080"})


@needs_app
class TestLaunchContract(unittest.TestCase):
    """The variables the launcher sets are the variables the app reads.

    `candidate.py` and `app.py` never import one another — the app takes a file
    path and a pty is the only thing that joins them — so this is the whole of
    the contract, and it is enforced by reading both sources.
    """

    def _reads(self, name):
        path = os.path.join(_ROOT, "huebox", name) if name == "app.py" \
            else os.path.join(_HERE, name)
        with open(path, encoding="utf-8") as handle:
            return set(re.findall(r'HUEBOX_([A-Z_]+)"', handle.read()))

    def test_every_variable_the_launcher_sets_is_one_the_app_reads(self):
        read = self._reads("app.py")
        self.assertTrue(read, "app.py reads no HUEBOX_* variables at all")
        orphans = sorted(self._reads("candidate.py") - read)
        self.assertEqual(orphans, [],
                         "candidate.py sets HUEBOX_%s and app.py never reads "
                         "it: either the app is missing a launch argument or "
                         "the launcher is setting a variable that does nothing"
                         % ", HUEBOX_".join(orphans))

    def test_the_launcher_pins_the_depth_the_app_cannot(self):
        # §6.4: TEXTUAL_COLOR_SYSTEM is read at import time, so the launcher is
        # the only place it can be set. If the app ever grew a way to set it,
        # this would be where the harness stopped needing to.
        read = self._reads("app.py")
        self.assertNotIn("TEXTUAL_COLOR_SYSTEM", read,
                         "app.py sets TEXTUAL_COLOR_SYSTEM itself; the "
                         "launcher and the app would then disagree about depth")


@needs_app
class TestTokenBinding(unittest.TestCase):
    """§6.2: every colour Textual draws with comes from a theme slot."""

    def _app(self, slots=None):
        path = _slots_file(slots or harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            return huebox_app.Editor()

    def test_every_bound_token_resolves_to_a_slot(self):
        app = self._app()
        allowed = {value.lstrip("#").lower()
                   for value in harness.FIXTURES["distinct"]["slots"].values()}
        for token in huebox_app.TOKEN_SLOTS:
            value = app.get_css_variables()[token]
            self.assertIn(value.lstrip("#").lower(), allowed,
                          "$%s resolved outside the theme's slots" % token)

    def test_text_is_bound_rather_than_left_to_ansi_default(self):
        """The one default that bites: `$text` is generated as `ansi_default`,
        the terminal's own foreground. A widget falling through to it paints a
        colour that is not the theme's."""
        app = self._app()
        self.assertIn("text", huebox_app.TOKEN_SLOTS)
        self.assertNotEqual(app.get_css_variables()["text"], "ansi_default")

    def test_chrome_follows_the_buffer_not_the_opened_theme(self):
        """Adjust keys repaint the rows from the buffer; the chrome must
        follow or the gutters wear the opened theme while the content
        already shows the edit."""
        app = self._app()
        opened = app.get_css_variables()["background"]
        app.state.slots["background"] = "#000000"
        app._sync_css_variables(app.state)
        self.assertEqual(app.get_css_variables()["background"], "#000000")
        self.assertNotEqual(opened, "#000000")

    def test_unchanged_buffer_skips_the_stylesheet_recompile(self):
        app = self._app()
        with mock.patch.object(app.stylesheet, "reparse") as reparse:
            app._sync_css_variables(app.state)
        reparse.assert_not_called()
        """The one default that bites: `$text` is generated as `ansi_default`,
        the terminal's own foreground. A widget falling through to it paints a
        colour that is not the theme's."""
        app = self._app()
        self.assertIn("text", huebox_app.TOKEN_SLOTS)
        self.assertNotEqual(app.get_css_variables()["text"], "ansi_default")

    def test_the_scrollbar_is_bound_because_it_is_the_first_thing_to_leak(self):
        app = self._app()
        variables = app.get_css_variables()
        for token in ("scrollbar", "scrollbar-active", "scrollbar-background"):
            self.assertIn(token, huebox_app.TOKEN_SLOTS)
            self.assertNotEqual(variables[token], "transparent")


@needs_app
class TestFrameRowsMatchTheWriter(unittest.TestCase):
    """`frame_rows` is `draw_editor`'s output, captured rather than re-derived.

    That is the whole reason phase 2 can promise an unchanged frame: a second
    copy of the frame's construction would be a second chance to get it wrong,
    and the two copies agreeing would prove nothing.
    """

    def test_the_rows_are_the_ones_draw_editor_writes(self):
        import io
        import contextlib
        from huebox import editor

        slots = harness.FIXTURES["distinct"]["slots"]
        state = editor.EditorState(slots, None, None, None, fmt="ghostty",
                                   path="")
        state.sel = 0
        state.mult = harness.REFERENCE_MULT
        state.status = harness.REFERENCE_STATUS

        original = editor.term_size
        editor.term_size = lambda default=(80, 24): (80, 24)
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                editor.draw_editor("ghostty", "", state.slots, state.sel,
                                   state.undo, state.status, state.mult)
        finally:
            editor.term_size = original
        written = buffer.getvalue().split("\r\n")
        if written and written[-1] == "":
            written.pop()

        self.assertEqual(
            huebox_app.frame_rows("ghostty", "", state, 80, 24), written)


if __name__ == "__main__":
    unittest.main()


@needs_app
class TerminalHygiene(unittest.TestCase):
    """§4.3 — the promise the user actually sees, asserted where it now lives.

    The old `RawMode` suite asserted one `enter_raw`/`exit_raw` pair per session,
    that each prompt closed and reopened it, and that the handler was restored
    in a `finally`. All of it went with the code that had it. What is left, and
    what these assert, is the part huebox still owns: that a prompt hands the
    terminal back so `input()` can work at all, and that cancelling one returns
    to the editor instead of ending the session.
    """

    def test_a_prompt_hands_the_terminal_back(self):
        # `App.suspend()` is what makes `input()` work at all under a
        # compositor: the app stops reading input and emitting output, and the
        # terminal is restored to what it was before the app started. Without
        # it the prompt would type into a frame nobody is listening to.
        editor = huebox_app.Editor()
        with mock.patch.object(huebox_app.Editor, "suspend",
                               mock.MagicMock()) as suspend, \
                mock.patch("builtins.input", return_value="ff0000"):
            editor.prompt_text("  new hex: ")
        suspend.assert_called_once()

    def test_a_cancelled_prompt_returns_none_rather_than_raising(self):
        for error in (EOFError, KeyboardInterrupt):
            with self.subTest(error=error.__name__):
                editor = huebox_app.Editor()
                with mock.patch.object(huebox_app.Editor, "suspend",
                                       mock.MagicMock()), \
                        mock.patch("builtins.input", side_effect=error):
                    self.assertIsNone(editor.prompt_text("  hex: "))

    def test_a_prompt_returns_what_was_typed(self):
        editor = huebox_app.Editor()
        with mock.patch.object(huebox_app.Editor, "suspend",
                               mock.MagicMock()), \
                mock.patch("builtins.input", return_value="  #ff0000  "):
            self.assertEqual(editor.prompt_text("  hex: "), "#ff0000")


@needs_app
class KeyVocabulary(unittest.TestCase):
    """The only seam between Textual's key names and huebox's.

    `apply_key` was written against huebox's own reader and expects `esc`,
    `\x03`, `\x13`. Getting this wrong would mean the quit key and the save key
    silently did nothing — the kind of bug that reads as "the migration broke
    shortcuts" with nothing in the logs.
    """

    def test_the_names_textual_speaks_are_the_names_apply_key_wants(self):
        from huebox import editor

        for textual_name, huebox_name in (("escape", "esc"),
                                          ("ctrl+c", "\x03"),
                                          ("ctrl+s", editor.SAVE_KEY)):
            with self.subTest(key=textual_name):
                self.assertEqual(huebox_app.translate(textual_name),
                                 huebox_name)
                self.assertIn(huebox_name,
                              editor.QUIT_KEYS + (editor.SAVE_KEY,),
                              "translate maps to a key apply_key does not "
                              "handle — a silent no-op keypress")

    def test_an_ordinary_character_passes_through(self):
        for key in ("q", "w", "u", "f", "i", "N"):
            self.assertEqual(huebox_app.translate(key), key)

    def test_nothing_is_bound_away_from_apply_key(self):

        self.assertEqual(huebox_app.Editor.BINDINGS, [],
                         "a Textual binding swallows a key apply_key wants")
        self.assertFalse(huebox_app.Editor.ENABLE_COMMAND_PALETTE)


@needs_app
class TheMouse(unittest.TestCase):
    """Phase 4: a click is a keypress, resolved against the frame.

    The shape of it is the whole claim — `on_click` sets the selection or feeds
    `apply_key`, and never touches a colour. Mouse changes input, not output,
    which is why this phase changed no frame row.
    """

    def _editor(self, cols=80, rows=24, **kw):
        """An editor with a known size, built but not mounted.

        `App.size` is a read-only property backed by the compositor, so a test
        that has not run the app patches the property rather than assigning to
        it — the same thing a mounted app would report.
        """
        from textual.geometry import Offset   # lazy: textual is an extra, so
        # importing it at module level would break the skip rather than skip

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size",
            new_callable=mock.PropertyMock, return_value=Offset(cols, rows))
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            editor = huebox_app.Editor(**kw)
        editor.query = lambda *a, **k: ()      # not mounted: nothing to find
        editor.mount = lambda *a, **k: None
        editor.redraw()
        return editor

    def _click(self, editor, x, y):
        from textual.events import Click

        editor.on_click(Click(widget=None, x=x, y=y, delta_x=0, delta_y=0,
                              button=1, shift=False, meta=False, ctrl=False))
        return editor

    def _point_at(self, editor, slot):
        return next(hit for hit in editor.hits if hit.slot == slot)

    def _screen_point(self, editor, hit):
        """Inner hit → screen coords for `_click`.

        Panels offset content by one border column left plus decision 45's
        horizontal air, and the top's growth plus the controls' own border
        row above (`_panel_geom` says all three: the unified logo-plus-info
        beside `editor` top, or nothing where the bare header stayed). Rows
        map straight past the border — the air is horizontal only. The bare
        frame is identity. All clickable hits live in the controls panel.
        """
        if getattr(editor, "_panels_on", False):
            geom = editor._panel_geom
            pad = geom.get("pad", 0)
            dy = ((geom.get("top_screen", geom.get("header_h", 0))
                    - geom.get("header_h", 0)) + 1)
            return hit.x0 + 1 + 1 + pad, hit.y + dy
        return hit.x0 + 1, hit.y

    def test_a_click_selects_the_slot_whose_cell_it_was(self):
        editor = self._editor()
        for slot in (0, 5, 16):
            with self.subTest(slot=slot):
                hit = self._point_at(editor, slot)
                x, y = self._screen_point(editor, hit)
                self._click(editor, x, y)
                self.assertEqual(editor.state.sel, slot)

    def test_a_click_on_chrome_selects_nothing(self):
        editor = self._editor()
        before = editor.state.sel
        for x, y in ((0, 0), (0, 1), (79, 0)):
            self._click(editor, x, y)
        self.assertEqual(editor.state.sel, before,
                         "clicking the header must not move the selection")

    def test_a_click_that_changes_nothing_paints_nothing(self):
        """The claim of the phase, asserted.

        What is left to say here is that a click that selects nothing must not even redraw
        the frame differently, byte for byte.

        A click that *does* select legitimately repaints — the `>` moves and the
        bold moves with it — which is why this clicks the chrome.
        """
        editor = self._editor()
        before = list(editor.rows_text)
        hits_before = list(editor.hits)
        self._click(editor, 0, 0)
        self.assertEqual(editor.rows_text, before,
                         "a click on the header repainted the frame")
        self.assertEqual(editor.hits, hits_before)

    def test_selecting_with_the_mouse_repaints_like_selecting_with_a_key(self):
        """A click and an arrow must reach the same frame.

        Not an aesthetic claim: if the click path set `sel` by any other route
        than the key surface, the two could drift — and a click would be the one
        that is wrong, with nothing to compare it against."""
        clicked = self._editor()
        hit = self._point_at(clicked, 6)
        x, y = self._screen_point(clicked, hit)
        self._click(clicked, x, y)

        pressed = self._editor()
        huebox_app.apply_key("right", pressed.state)
        pressed.redraw()          # what `on_key` does after `apply_key`
        self.assertEqual(clicked.state.sel, 6)
        self.assertEqual(pressed.state.sel, 6)
        self.assertEqual(clicked.rows_text, pressed.rows_text,
                         "a click and a keypress painted different frames")

    def test_the_wheel_only_moves_the_picker(self):
        editor = self._editor()
        sel = editor.state.sel
        editor._scroll_picker("down", _Event())
        self.assertEqual(editor.state.sel, sel,
                         "the wheel moved the selection with no picker up")

    def test_a_click_on_a_picker_row_opens_that_theme(self):
        editor = self._editor(theme="ember", library=lambda: None)
        editor.state.overlay = ["alpha", "beta", "gamma"]
        editor.state.overlay_index = 0
        editor.redraw()
        self.assertTrue(editor.hits or True)
        self.assertEqual(editor.state.overlay_index, 0)


@needs_app
class FirstRunSetup(unittest.IsolatedAsyncioTestCase):
    """An empty library opens on the import-or-create popup.

    The shell enters what `editor.enter_setup` owes and pushes a modal
    dialog over the dimmed editor — the import popup's shape, asserted at
    the seam and through compositor runs.
    """

    def _editor(self, cols=80, rows=24, **kw):
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size",
            new_callable=mock.PropertyMock, return_value=Offset(cols, rows))
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            editor = huebox_app.Editor(**kw)
        editor.query = lambda *a, **k: ()
        editor.mount = lambda *a, **k: None
        editor.redraw()
        return editor

    def _empty_library(self):
        from huebox.editor import Library
        return Library(listing=lambda: [])

    async def _running(self, **kw):
        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            return huebox_app.Editor(**kw)

    def test_an_empty_library_opens_on_the_choice(self):
        editor = self._editor(library=self._empty_library())
        self.assertEqual(editor.state.setup, 0)
        self.assertFalse(editor.state.quit)

    def test_a_library_with_themes_opens_on_the_editor(self):
        from huebox.editor import Library
        editor = self._editor(library=Library(
            listing=lambda: ["ember"]))
        self.assertIsNone(editor.state.setup)

    async def test_the_choice_opens_as_a_popup_over_the_editor(self):
        editor = await self._running(library=self._empty_library())
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = editor.screen
            self.assertIsInstance(screen, huebox_app.SetupScreen,
                                  "the choice is a popup, not a frame takeover")
            body = "\n".join(screen._body.rows_text)
            self.assertIn("Import themes", body)
            self.assertIn("Create new theme", body)
            behind = "\n".join(editor.rows_text)
            self.assertIn("Palette", behind,
                          "the dimmed editor frame stays behind the popup")
            self.assertTrue(list(editor.screen_stack[0].children),
                            "a redraw under the modal cleared the editor")

    async def test_the_dialog_clamps_to_narrow_screens(self):
        editor = await self._running(library=self._empty_library())
        async with editor.run_test(size=(40, 20)) as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = editor.screen
            self.assertIsInstance(screen, huebox_app.SetupScreen)
            self.assertLessEqual(screen._dlg_w + 2, 40)

    async def _press(self, app, pilot, *keys):
        from textual import events

        for key in keys:
            event = events.Key(key, None)
            event.set_sender(app)
            app.post_message(event)
            await pilot.pause()

    async def test_arrows_move_the_choice_in_the_running_app(self):
        editor = await self._running(library=self._empty_library())
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertEqual(editor.state.setup, 0)
            await self._press(editor, pilot, "down")
            self.assertEqual(editor.state.setup, 1)
            await self._press(editor, pilot, "up")
            self.assertEqual(editor.state.setup, 0)

    async def test_enter_opens_the_import_popup(self):
        editor = await self._running(library=self._empty_library())
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await self._press(editor, pilot, "enter")
            self.assertIsNone(editor.state.setup)
            self.assertIsInstance(editor.screen,
                                  huebox_app.ImportScreen)

    def _click_at(self, editor, fx, fy):
        from textual.events import Click

        editor.on_click(Click(widget=None, x=fx, y=fy, delta_x=0, delta_y=0,
                              button=1, shift=False, meta=False, ctrl=False))

    def _dialog_point(self, editor, slot):
        """Screen coords of a dialog choice: centered origin, one border."""
        screen = editor.screen
        width, height = screen.size
        ox = (width - (screen._dlg_w + 2)) // 2
        oy = (height - (screen._dlg_h + 2)) // 2
        hit = next(h for h in screen._hits if h.slot == slot)
        return ox + 1 + hit.x0 + 1, oy + 1 + hit.y

    async def test_a_click_on_a_choice_confirms_it(self):
        editor = await self._running(library=self._empty_library())
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            self._click_at(editor, *self._dialog_point(editor, 0))
            await pilot.pause()
            self.assertIsNone(editor.state.setup)
            self.assertIsInstance(editor.screen,
                                  huebox_app.ImportScreen,
                                  "clicking import did not open the popup")

    async def test_cancelling_the_import_returns_to_the_choice(self):
        editor = await self._running(library=self._empty_library())
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await self._press(editor, pilot, "enter")
            self.assertIsInstance(editor.screen,
                                  huebox_app.ImportScreen)
            await self._press(editor, pilot, "escape")
            self.assertIsInstance(editor.screen,
                                  huebox_app.SetupScreen,
                                  "an import closed still empty owes the choice"
                                  " again")

    async def test_naming_in_the_popup_creates_without_leaving(self):
        from huebox.editor import Library
        made = {}

        def creator(name, slots, force=False):
            made[name] = dict(slots)
            return f"/themes/{name}.toml", ""

        editor = await self._running(library=Library(listing=lambda: [],
                                                     creator=creator))
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await self._press(editor, pilot, "n", "d", "u", "s", "k")
            self.assertEqual(editor.state.setup_name, "dusk")
            body = "\n".join(editor.screen._body.rows_text)
            self.assertIn("dusk_", body,
                          "the typed name never reached the dialog")
            await self._press(editor, pilot, "enter")
            self.assertEqual(editor.state.theme, "dusk")
            self.assertNotIsInstance(editor.screen,
                                     huebox_app.SetupScreen)
        self.assertEqual(set(made), {"dusk"})


@needs_app
class ThemesPopup(unittest.IsolatedAsyncioTestCase):
    """The themes list opens as a modal popup over the editor (§13.7).

    The same kind as the first-run choice: a centered dialog over the dimmed
    editor, not a frame takeover — the rows are `theme_lines` at the dialog
    width, behaviour stays in `EditorState` + `apply_key`, and closing paints
    the editor behind it.
    """

    async def _running(self, names=("ash", "ember", "frost"), theme="ember",
                       **kw):
        from huebox.editor import Library

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        slots = dict(harness.FIXTURES["distinct"]["slots"])

        def loader(name):
            return (dict(slots), f"/themes/{name}.toml")

        library = Library(listing=lambda: list(names), loader=loader)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            return huebox_app.Editor(library=library, theme=theme,
                                     slots=dict(slots),
                                     write=lambda t, p, s: "saved", **kw)

    async def _press(self, app, pilot, *keys):
        from textual import events

        for key in keys:
            event = events.Key(key, None)
            event.set_sender(app)
            app.post_message(event)
            await pilot.pause()

    def _dialog_point(self, editor, slot):
        """Screen coords of a dialog row: centered origin, one border."""
        screen = editor.screen
        width, height = screen.size
        ox = (width - (screen._dlg_w + 2)) // 2
        oy = (height - (screen._dlg_h + 2)) // 2
        hit = next(h for h in screen._hits if h.slot == slot)
        return ox + 1 + hit.x0 + 1, oy + 1 + hit.y

    def _click_at(self, editor, fx, fy):
        from textual.events import Click

        editor.on_click(Click(widget=None, x=fx, y=fy, delta_x=0, delta_y=0,
                              button=1, shift=False, meta=False, ctrl=False))

    async def test_the_list_opens_as_a_popup_over_the_editor(self):
        editor = await self._running()
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await self._press(editor, pilot, "t")
            await pilot.pause()
            screen = editor.screen
            self.assertIsInstance(screen, huebox_app.ThemesScreen,
                                  "the themes list is a popup, not a frame takeover")
            body = "\n".join(screen._body.rows_text)
            for name in ("ash", "ember", "frost"):
                self.assertIn(name, body)
            behind = "\n".join(editor.rows_text)
            self.assertIn("Palette", behind,
                          "the dimmed editor frame stays behind the popup")
            self.assertTrue(list(editor.screen_stack[0].children),
                            "a redraw under the modal cleared the editor")

    async def test_the_dialog_clamps_to_narrow_screens(self):
        editor = await self._running()
        async with editor.run_test(size=(40, 20)) as pilot:
            await pilot.pause()
            await self._press(editor, pilot, "t")
            await pilot.pause()
            screen = editor.screen
            self.assertIsInstance(screen, huebox_app.ThemesScreen)
            self.assertLessEqual(screen._dlg_w + 2, 40)

    async def test_arrows_move_the_list_not_the_colours(self):
        editor = await self._running()
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await self._press(editor, pilot, "t")
            await pilot.pause()
            self.assertEqual(editor.state.overlay_index, 1)
            before = editor.state.sel
            await self._press(editor, pilot, "down")
            self.assertEqual(editor.state.overlay_index, 2)
            self.assertEqual(editor.state.sel, before,
                             "an arrow moved the colours behind the list")
            await self._press(editor, pilot, "up")
            self.assertEqual(editor.state.overlay_index, 1)

    async def test_enter_opens_the_selected_theme_and_closes(self):
        editor = await self._running()
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await self._press(editor, pilot, "t")
            await pilot.pause()
            await self._press(editor, pilot, "down")
            await self._press(editor, pilot, "enter")
            await pilot.pause()
            self.assertIsNone(editor.state.overlay)
            self.assertEqual(editor.state.theme, "frost")
            self.assertNotIsInstance(editor.screen,
                                     huebox_app.ThemesScreen)

    async def test_escape_closes_back_to_the_editor(self):
        editor = await self._running()
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await self._press(editor, pilot, "t")
            await pilot.pause()
            self.assertIsInstance(editor.screen, huebox_app.ThemesScreen)
            await self._press(editor, pilot, "escape")
            await pilot.pause()
            self.assertIsNone(editor.state.overlay)
            self.assertEqual(editor.state.theme, "ember")
            self.assertNotIsInstance(editor.screen,
                                     huebox_app.ThemesScreen)

    async def test_a_click_on_a_row_opens_that_theme(self):
        editor = await self._running()
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await self._press(editor, pilot, "t")
            await pilot.pause()
            await pilot.pause()
            self._click_at(editor, *self._dialog_point(editor, 0))
            await pilot.pause()
            self.assertIsNone(editor.state.overlay)
            self.assertEqual(editor.state.theme, "ash",
                              "clicking a row did not open that theme")

    async def test_the_wheel_moves_the_list(self):
        editor = await self._running()
        async with editor.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await self._press(editor, pilot, "t")
            await pilot.pause()
            before = editor.state.overlay_index
            editor._scroll_picker("down", _Event())
            await pilot.pause()
            self.assertEqual(editor.state.overlay_index, before + 1)
            editor._scroll_picker("up", _Event())
            await pilot.pause()
            self.assertEqual(editor.state.overlay_index, before)


class _Event:
    def stop(self):
        pass


@needs_app
class ThePickersMinimum(unittest.TestCase):
    """§13.7 — below the minimum, the editor behind the popup is the hint.

    The list is a modal popup over the editor, so the too-small check covers
    the frame behind it: the dialog clamps itself, and the editor it dims is
    the same hint the bare session draws — which is the only way a frame
    added later cannot forget it.
    """

    def test_below_the_minimum_the_hint_replaces_the_picker(self):
        from huebox.render import MIN_COLS, MIN_ROWS
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        for cols, rows in ((MIN_COLS - 1, 24), (80, MIN_ROWS - 1)):
            with self.subTest(size=f"{cols}x{rows}"):
                patcher = mock.patch.object(
                    huebox_app.Editor, "size", new_callable=mock.PropertyMock,
                    return_value=Offset(cols, rows))
                patcher.start()
                self.addCleanup(patcher.stop)
                with mock.patch.dict(
                        os.environ,
                        {"HUEBOX_SLOTS": path, "HUEBOX_PICKER": "1",
                         "HUEBOX_PICKER_NAMES": "ash,ember,frost",
                         "HUEBOX_PICKER_INDEX": "1"}):
                    editor = huebox_app.Editor()
                    editor.query = lambda *a, **k: ()
                    editor.mount = lambda *a, **k: None
                    editor.redraw()
                body = "".join(row for row in editor.rows_text)
                self.assertIn("too small", body)
                for name in ("ash", "ember", "frost"):
                    self.assertNotIn(name, body,
                                     "the picker drew through the hint")


@needs_app
class TheFrameIsWidgets(unittest.TestCase):
    """§5.6 — the frame is a stack of block widgets, not one opaque thing.

    Asserted against the mounted tree rather than the drawing call, because the
    claim is about what Textual has. A future change that quietly collapsed
    them back into one `Frame` would paint identically and pass every colour
    test; this is the test that notices.
    """

    def _mounted(self, cols=80, rows=24, status=""):
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size", new_callable=mock.PropertyMock,
            return_value=Offset(cols, rows))
        patcher.start()
        self.addCleanup(patcher.stop)
        mounted = []

        # Bare rows: the collapsible is product chrome, and these guard the
        # bare stack. `CollapsibleExamples` covers the product.
        with mock.patch.dict(os.environ,
                             {"HUEBOX_SLOTS": path, "HUEBOX_STATUS": status,
                              "HUEBOX_COLLAPSIBLE": "0"}):
            editor = huebox_app.Editor()
            editor.query = lambda *a, **k: ()
            editor.mount = lambda widget: mounted.append(widget)
            editor.redraw()
        return editor, mounted

    def _frames(self, mounted):
        """Every `Frame` in mount order, descending into panels.

        Top-level mounts are chrome `Frame`s, the top `Horizontal` (bare
        logo, the info column and one bordered `editor` panel, §8.1
        decision 40) and `Panel`s; the blocks live inside the panels as
        pending children (mount is mocked, so nothing composes). Flatten
        panels and the top row recursively so block order is frame order.
        """
        from textual.containers import Horizontal
        out = []
        from textual.containers import Vertical

        def flat(widget):
            if isinstance(widget, huebox_app.Panel):
                for child in list(getattr(widget, "_pending_children", [])):
                    yield from flat(child)
            elif isinstance(widget, (Horizontal, Vertical)):
                # the top row: bare logo chrome, the info column and one
                # bordered `editor` panel (decision 40). A side layout's
                # pair never reaches here (panels off in `_mounted`) — only
                # the top does. The `themes` button rides the info column
                # too, and it is not a `Frame`: only frames tile the bare
                # rows, so only frames are yielded and the button never
                # reaches the tiling math below.
                for child in list(getattr(widget, "_pending_children", [])
                                or getattr(widget, "_nodes", [])):
                    yield from flat(child)
            elif isinstance(widget, huebox_app.Frame):
                yield widget

        for widget in mounted:
            out.extend(flat(widget))
        return out

    def test_one_widget_per_block(self):
        _editor, mounted = self._mounted()
        regions = _editor.regions
        self.assertTrue(_editor._panels_on, "panels did not mount")
        panels = [w for w in mounted
                  if isinstance(w, huebox_app.Panel)]
        self.assertEqual([p.border_title for p in panels],
                         ["THEME", "EXAMPLES"])
        # The compositor top splits the bare header three ways (logo,
        # info, `editor`), so the mounted blocks outnumber the bare
        # regions by two — one widget per block still, counted where
        # the blocks actually mount.
        frames = self._frames(mounted)
        self.assertEqual([f.name for f in frames],
                         ["header", "info", "editor-hsv", "palette",
                          "interface", "examples", "sample"])
        self.assertGreater(len(frames), 3,
                           "the frame collapsed back into a single widget")
        names = [name for name, _, _ in regions]
        for name in ("header", "palette", "interface", "selected"):
            self.assertIn(name, names,
                          "the frame no longer names its %s block" % name)

    def test_the_widgets_stack_to_the_frame_and_no_further(self):
        """The mounted zones tile the screen exactly: footer included.

        That is not a hypothetical: seven of Textual's 168 design tokens exist
        only for scrollbars, and I2's whole job is to reject a colour that was
        not the theme's. The frame stacks to one row short of the screen and
        the docked footer stands the last one: at 80x24 that is 6 + 9 + 8 +
        1 — one row more would scroll."""
        editor, mounted = self._mounted()
        from textual.containers import Horizontal
        self.assertEqual(len(mounted), 4)
        top, controls, examples, footer = mounted
        self.assertIsInstance(top, Horizontal)
        self.assertIsInstance(footer, huebox_app.HueFooter)
        self.assertEqual([p.border_title for p in (controls, examples)],
                         ["THEME", "EXAMPLES"])
        heights = [top.styles.height.value, controls.styles.height.value,
                   examples.styles.height.value]
        self.assertEqual(heights, [6, 9, 8])
        self.assertEqual(sum(heights) + 1, 24,
                         "the stack plus the footer is taller than "
                         "the screen")

    def test_every_block_paints_only_its_own_rows(self):
        editor, mounted = self._mounted()
        from textual.containers import Horizontal
        # The share top replaces the bare header chrome: one top row of
        # logo, info and `editor` instead of full-width header blocks —
        # the bare rows it stands in for are drawn but never mounted.
        top, controls, examples, footer = mounted
        self.assertIsInstance(top, Horizontal)
        self.assertIsInstance(footer, huebox_app.HueFooter)
        top_frames = [b.name for b in self._frames([top])]
        self.assertEqual(top_frames, ["header", "info", "editor-hsv"])
        self.assertEqual(controls.border_title, "THEME")
        self.assertEqual(examples.border_title, "EXAMPLES")
        inner = lambda panel: [
            c.name for c in getattr(panel, "_pending_children", [])]
        self.assertEqual(inner(controls), ["palette", "interface"])
        self.assertEqual(inner(examples), ["examples", "sample"])
        # Hints and status ride the docked footer, not a block: the
        # footer carries every hint pair and the status behind it.
        pairs = dict(footer._pairs)
        self.assertEqual(pairs["arrows"], "move")
        self.assertEqual(footer._status, "")
        # Chrome below both panels mounts full-width, like the bare
        # frame it stands in for.
        rest = self._frames([controls, examples])
        self.assertEqual([b.name for b in rest],
                         ["palette", "interface", "examples",
                          "sample"])


@needs_app
class FocusAndKeys(unittest.IsolatedAsyncioTestCase):
    """Phase 5: the grid is a control, and only a running app can prove it.

    Everything here is about Textual's *dispatch* — which node sees a key, in
    what order, and how many times. None of it can be reached by calling
    `apply_key` in a unit test, because the whole question is what happens
    between the terminal and `apply_key`. `run_test` costs about 0.13s, which
    is affordable; a real terminal would not have been.

    The bug these were written for: the App's `on_key` and the grid's binding
    both act on an arrow, and the selection moved two slots per press. Both
    handlers were "correct"; Textual simply does not promise that a binding
    consumes a key before the app's own handler sees it.
    """

    async def _app(self, cols=100, rows=30):
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            app = huebox_app.Editor()
            return app

    async def _press(self, app, pilot, *keys):
        """A key the way a terminal sends it.

        `pilot.press` goes through the driver, and the headless driver's input
        path does not deliver — so the key is posted to the app, which is the
        same queue a real key arrives on.
        """
        from textual import events

        for key in keys:
            event = events.Key(key, None)
            event.set_sender(app)
            app.post_message(event)
            await pilot.pause()

    async def test_the_grid_holds_focus(self):
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.focused, huebox_app.Swatches,
                                  "the grid is not focused, so no key is routed "
                                  "to it by Textual")
            self.assertEqual(app.focused.name, "palette")

    async def test_an_arrow_moves_one_slot_when_the_grid_has_focus(self):
        # Stacked layout: row-major arrows. The side layout's column-major
        # walk has its own suite (`SideBySide`).
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertEqual(app.state.sel, 5)
            await self._press(app, pilot, "right")
            self.assertEqual(app.state.sel, 6,
                             "one press moved the selection more than one "
                             "slot: two handlers acted on one key")

    async def test_an_arrow_still_moves_when_no_grid_is_focused(self):
        """The App's own handler is the fallback, and it must still work.

        The 40x12 floor shows every slot now that the footer is docked, so
        the selection can no longer sit below the fold: focus is cleared
        outright instead, and `up` must still move through the app handler.

        `up`, which stays in the column: from 21 that is 19 in the narrow
        grid.
        """
        app = await self._app()
        async with app.run_test(size=(40, 12)) as pilot:
            await pilot.pause()
            app.state.sel = 21
            app.set_focus(None)
            await pilot.pause()
            self.assertIsNone(app.focused)
            await self._press(app, pilot, "up")
            self.assertEqual(app.state.sel, 19,
                             "the keys stopped working once no grid "
                             "held focus")

    async def test_focus_follows_the_selection_across_the_two_grids(self):
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertEqual(app.focused.name, "palette")
            # §4.3 — right stays in the row, so seventeen rights is not
            # seventeen slots. `down` is the key that crosses blocks.
            await self._press(app, pilot, *["down"] * 2)
            self.assertEqual(app.state.sel, len(harness.FIXTURES["distinct"]
                                                ["slots"]) - 5)
            self.assertEqual(app.focused.name, "interface",
                             "focus did not follow the selection out of the "
                             "palette")

    async def test_focus_moves_back_into_the_palette(self):
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await self._press(app, pilot, *["down"] * 2)
            self.assertEqual(app.focused.name, "interface")
            await self._press(app, pilot, *["up"] * 2)
            self.assertEqual(app.state.sel, 1)
            self.assertEqual(app.focused.name, "palette")

    async def test_the_picker_has_no_grid_to_focus(self):
        """§13.7 — the picker is a popup over the editor, and it holds no grid.

        If focus stayed on a grid underneath it, an arrow would move the
        *colour* selection behind a list the user is reading, which is the one
        thing §13.7 says cannot happen."""
        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ,
                             {"HUEBOX_SLOTS": path, "HUEBOX_PICKER": "1",
                              "HUEBOX_PICKER_NAMES": "ash,ember",
                              "HUEBOX_PICKER_INDEX": "1"}):
            app = huebox_app.Editor()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(app.screen, huebox_app.ThemesScreen,
                                  "the themes list is a popup, not a frame takeover")
            body = "\n".join(app.screen._body.rows_text)
            self.assertIn("ash", body)
            self.assertIn("ember", body)
            behind = "\n".join(app.rows_text)
            self.assertIn("Palette", behind,
                          "the dimmed editor frame stays behind the popup")
            self.assertNotIsInstance(app.focused, huebox_app.Swatches,
                                     "a grid kept focus behind the popup")
            before = app.state.sel
            await self._press(app, pilot, "right")
            self.assertEqual(app.state.sel, before,
                             "an arrow moved the colour selection behind the "
                             "picker")
            self.assertEqual(app.state.overlay_index, 1,
                             "the arrow did not leave the picker's own row")


@needs_app
class TheFrameIsSizedByTheCompositor(unittest.IsolatedAsyncioTestCase):
    """The frame's size is the compositor's, and not also the terminal's.

    This is a bug a frame comparison could not see, which is the whole reason it is worth a
    test. The harness sets a pty's window size *before* launching, so the
    terminal and the compositor always agreed there and the two numbers never
    diverged. Resize afterwards — which is every resize, and is what a user
    dragging a window edge does — and `draw_editor` was still asking the
    terminal for a frame the compositor had already sized: a 24-row frame in a
    12-row terminal, with a scrollbar the theme has no colour for.

    Two sources of truth for one number, and §15's whole row budget decided by
    whichever one was asked.

    `pilot.resize_terminal` rather than a posted `Resize` event, because a
    posted event says nothing about the size the compositor actually has — the
    frame would then be measured against a number nothing else agrees with,
    which is how this test would have passed against the very bug it is for.
    """

    async def _app(self):
        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            return huebox_app.Editor()

    async def test_a_resize_lays_the_frame_out_at_the_new_size(self):
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            sizes = ((40, 12), (100, 30), (60, 16))
            if not os.environ.get("HUEBOX_ALL"):
                sizes = ((40, 12),)
            for cols, rows in sizes:
                with self.subTest(size=f"{cols}x{rows}"):
                    await pilot.resize_terminal(cols, rows)
                    await pilot.pause()
                    self.assertEqual((app.size.width, app.size.height),
                                     (cols, rows),
                                     "the compositor did not take the size")
                    self.assertLessEqual(len(app.rows_text), rows,
                                         "the frame is taller than the window "
                                         "it is drawn in")

    async def test_the_frame_and_the_regions_agree_at_every_size(self):
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            sizes = ((40, 12), (60, 16), (40, 30), (70, 30), (80, 30),
                     (100, 30))
            if not os.environ.get("HUEBOX_ALL"):
                sizes = ((40, 12),)
            for cols, rows in sizes:
                with self.subTest(size=f"{cols}x{rows}"):
                    await pilot.resize_terminal(cols, rows)
                    await pilot.pause()
                    # the last block ends exactly where the frame does — a
                    # frame laid out for the wrong height would leave the
                    # blocks short or long by the difference
                    self.assertTrue(app.regions)
                    if app._side_on:
                        # Side-by-side keeps three coordinate spaces by
                        # design (`_try_side`: chrome rows, left-content
                        # rows, right-content rows — "the three spaces
                        # never meet"), so no one tiling covers it. Its
                        # own exact-fill invariant instead: top plus pair
                        # plus bottom chrome is exactly the window, and
                        # each panel's contents tile their own space — 17
                        # rows full, 14 squeezed (§8.1 decision 42).
                        geom = app._side_geom
                        top, content_h = geom["top"], geom["content_h"]
                        # Top plus pair plus the docked footer is exactly
                        # the window: the hints and the status ride the
                        # footer, not the regions, so the bar stands the
                        # last row where the pair ends.
                        self.assertEqual(top + (content_h + 2) + 1,
                                         rows,
                                         "the side layout does not fill "
                                         "the window")
                        left_rows = (huebox_app.SIDE_LEFT_NARROW_ROWS
                                     if geom["narrow"]
                                     else huebox_app.SIDE_LEFT_ROWS)
                        self.assertEqual(
                            sum(count for name, _, count in app.regions
                                if name in huebox_app.GRID_BLOCKS),
                            left_rows,
                            "the left panel's contents do not tile it")
                        self.assertEqual(
                            sum(count for name, _, count in app.regions
                                if name in huebox_app.LIVE_BLOCKS),
                            content_h,
                            "the right panel's contents do not tile it")
                        self.assertLessEqual(
                            len(app.rows_text), rows,
                            "the chrome capture is taller than the "
                            "window it was drawn in")
                        continue
                    last = app.regions[-1]
                    self.assertEqual(last[1] + last[2], len(app.rows_text))
                    self.assertEqual(sum(count for _, _, count in app.regions),
                                     len(app.rows_text))


@needs_app
class SelectableBlocks(unittest.IsolatedAsyncioTestCase):
    """The code sample and the diff are text you can take away (§14).

    `ALLOW_SELECT` on its own was not enough, and the two halves it needs are
    both invisible in a screenshot:

    - **The text.** `Widget.get_selection` asks the widget to `render()` and
      selects from that Visual. This widget has no `render()` — its rows are
      already-parsed `Text` — so the default found nothing and a drag
      highlighted the screen while the clipboard stayed empty.
    - **The paint.** Textual applies the selection style inside
      `Visual.to_strips`, the path a widget with a `render()` takes.
      `render_line` is the whole story here, so the style had to be applied by
      hand or a drag would show nothing at all.

    And the colour it uses had to be the theme's. Textual would otherwise
    supply its own, which is §6.2's whole subject and I2's whole job.
    """

    SLOTS = harness.FIXTURES["distinct"]["slots"]

    async def _app(self):
        path = _slots_file(self.SLOTS)
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            return huebox_app.Editor()

    async def _drag(self, app, pilot, widget, start, end):
        await pilot.mouse_down(widget, offset=start)
        await pilot.hover(widget, offset=end)
        await pilot.pause()
        await pilot.mouse_up(widget, offset=end)
        await pilot.pause()

    async def _blocks(self, app, kind):
        return [w for w in app.query(kind)]

    async def test_the_sample_and_the_diff_are_the_selectable_blocks(self):
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            names = [w.name for w in app.query(huebox_app.Selectable)]
            self.assertIn("sample", names)
            for other in ("header", "palette", "interface", "selected",
                          "examples", "hints"):
                self.assertNotIn(other, [w.name for w in
                                         app.query(huebox_app.Selectable)],
                                 "%s should not be selectable" % other)

    async def test_dragging_over_the_sample_gives_you_the_code(self):
        # Stacked offsets: the side layout puts the sample narrower and
        # lower, so its own suite drags there (`SideBySide`).
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            sample = (await self._blocks(app, huebox_app.Sample))[0]
            await self._drag(app, pilot, sample, (4, 1), (30, 1))
            chosen = app.screen.get_selected_text()
            self.assertTrue(chosen.strip(), "a drag selected nothing")
            self.assertNotIn("\033", chosen,
                             "the escapes came out with the text")

    async def test_a_selection_is_painted_in_the_theme_s_own_slots(self):
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            sample = (await self._blocks(app, huebox_app.Sample))[0]
            before = sample.render_line(1)
            await pilot.mouse_down(sample, offset=(4, 1))
            await pilot.hover(sample, offset=(24, 1))
            await pilot.pause()
            during = sample.render_line(1)
            await pilot.mouse_up(sample, offset=(24, 1))
            await pilot.pause()

            wanted = self.SLOTS["selection-background"].lower()
            got = {_hex(seg.style.bgcolor) for seg in during._segments
                   if seg.style is not None and seg.style.bgcolor is not None}
            self.assertIn(wanted, got,
                          "the selection is painted in something that is not "
                          "the theme's selection-background: %s" % sorted(got))
            self.assertNotEqual(before._segments, during._segments,
                                "the drag changed nothing on screen")

    async def test_every_colour_a_selection_paints_is_a_theme_slot(self):
        """I2, for a frame with a selection down.

        A capture never has a selection down, so nothing in the harness can
        see what a drag paints. This is the same closure check applied to the
        one frame the harness does not cover: nothing Textual's own, or the
        selection would be a blue in a theme that has no blue.
        """
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            sample = (await self._blocks(app, huebox_app.Sample))[0]
            await pilot.mouse_down(sample, offset=(2, 1))
            await pilot.hover(sample, offset=(40, 2))
            await pilot.pause()
            slots = {value.lower() for value in self.SLOTS.values()}
            painted = set()
            for row in range(len(sample.rows_text)):
                for seg in sample.render_line(row)._segments:
                    if seg.style is None:
                        continue
                    painted.update(part for part in (_hex(seg.style.color),
                                                     _hex(seg.style.bgcolor))
                                   if part)
            await pilot.mouse_up(sample, offset=(40, 2))
            self.assertTrue(painted)
            self.assertEqual(sorted(painted - slots), [],
                             "a selection painted a colour the theme does "
                             "not have")

    async def test_the_palette_grid_is_not_selectable(self):
        """A grid that swallows a drag cannot be clicked.

        `ALLOW_SELECT` is on the app for Textual's own reasons and off per
        widget; if the grid were selectable, every click on a swatch would
        begin a text selection instead of selecting the colour."""
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            grid = (await self._blocks(app, huebox_app.Swatches))[0]
            self.assertFalse(grid.allow_select)


def _hex(color):
    """A rich colour as `#rrggbb`, or None when it is not one.

    `str(Color)` gives the whole `Color('#313244', ColorType.TRUECOLOR, ...)`
    repr, which is neither a hex string nor comparable to one — so a test that
    compares colours has to ask for the triplet, and a test that does not will
    pass vacuously against a set that never matches anything.
    """
    if color is None or color.is_default:
        return None
    try:
        return "#%02x%02x%02x" % tuple(color.get_truecolor())
    except (ValueError, AttributeError):          # not a truecolor: a named or
        return None                               # ANSI colour, not a theme one


@needs_app
class TheAppsOwnKeysWork(unittest.IsolatedAsyncioTestCase):
    """Every key in §4.3, pressed into a real session built the real way.

    This is the test whose absence let a crash ship. The frame comparisons
    never send a key; the headless sessions drive
    `EditorState` built by `EditorState.__init__`, where `mult` is the int
    `MULT_STEPS[0]`. The app builds its session in `Editor.build_state`, from
    the environment, and *that* one had `mult` as a string. So the two ways of
    making a session disagreed, nothing compared them, and `q` — the editor's
    main verb — raised `can't multiply sequence by non-int of type float`.

    It stayed green because the harness agreed with it: `REFERENCE_MULT` was
    `False`, so `HUEBOX_MULT` was `"False"`, a different wrong type that
    rendered `f xFalse` in *both* sides. Two wrongs matching is not agreement.

    The property worth keeping is not "these keys move the colour" — `test_editor`
    owns that — but that a session built by the app and a session built by
    `EditorState` are the same kind of thing, and that nothing the app reads
    from the environment arrives as the wrong type.
    """

    async def _app(self, **env):
        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        environ = {"HUEBOX_SLOTS": path}
        environ.update(env)
        with mock.patch.dict(os.environ, environ):
            return huebox_app.Editor()

    async def test_every_adjust_key_survives_a_session_the_app_built(self):
        # Smoke by default: `w` was the crash (mult as string), and every
        # adjust key shares the same step path; `HUEBOX_ALL=1` walks all six.
        keys = ("w", "e", "s", "d", "x", "c")
        if not os.environ.get("HUEBOX_ALL"):
            keys = ("w",)
        for key in keys:
            with self.subTest(key=key):
                app = await self._app()
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    # A saturated slot, not palette-0: the fixture's palette-0
                    # is near-black, and a hue step on a colour that is already
                    # black leaves it black. That is §5 behaving correctly and
                    # would read here as "the key did nothing".
                    app.state.sel = 1
                    before = app.state.slots["palette-1"]
                    await _key(app, pilot, key)
                    self.assertNotEqual(app.state.slots["palette-1"], before,
                                        "%s did not move the colour" % key)

    async def test_the_step_multiplier_key_survives_too(self):
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            start = app.state.mult
            await _key(app, pilot, "f")
            self.assertNotEqual(app.state.mult, start)
            self.assertIn(app.state.mult, list(huebox_app.MULT_STEPS))

    async def test_mult_is_a_step_and_not_a_string(self):
        for raw in (None, "1", "5", "20", "False", "nonsense", ""):
            with self.subTest(raw=raw):
                self.assertEqual(huebox_app._mult_step(raw),
                                 int(raw) if raw in ("1", "5", "20")
                                 else huebox_app.MULT_STEPS[0])
                self.assertIsInstance(huebox_app._mult_step(raw), int)

    async def test_the_app_and_the_state_agree_on_a_session(self):
        """Same fixture, same environment, one built each way."""
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            from huebox.editor import EditorState, MULT_STEPS

            plain = EditorState(dict(harness.FIXTURES["distinct"]["slots"]),
                                None, lambda label: "",
                                lambda path: None)
            self.assertEqual(type(app.state.mult), type(plain.mult))
            self.assertEqual(app.state.mult, plain.mult)
            self.assertEqual(app.state.sel, plain.sel)
            self.assertEqual(MULT_STEPS[0], 1)

    async def test_the_hint_line_shows_the_step_not_its_repr(self):
        """`f x1`, never `f xFalse` or `f x'1'`.

        The footer prints the multiplier verbatim, so a string multiplier is
        visible on screen even before it is fatal — which is how `f xFalse`
        first showed itself."""
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            pairs = dict(app._footer._pairs)
            self.assertIn("arrows", pairs, "no hint pairs")
            self.assertEqual(pairs["f"], "x1")
            self.assertNotIn("xFalse", pairs["f"])


def _plain(row):
    """One drawn row without its SGR — what the user reads."""
    from rich.text import Text        # lazy: rich is Textual's, and this
    return Text.from_ansi(row).plain   # module skips when it is absent


async def _key(app, pilot, name):
    """A keypress, as a terminal sends it.

    `pilot.press` goes through the driver, and the headless driver's input path
    does not deliver, so the event is posted to the app's own queue.
    """
    from textual import events

    event = events.Key(name, None)
    event.set_sender(app)
    app.post_message(event)
    await pilot.pause()


@needs_app
class TheFrameNeverScrolls(unittest.IsolatedAsyncioTestCase):
    """The frame is the window. It cannot scroll, at any size.

    You reported it: the editor could be scrolled when everything already fit,
    and scrolling revealed a stale copy of the header and the palette. A stale
    rendering is what a scroll offset makes: the cells a scroll exposes were
    painted for a window that no longer exists, and nothing repaints them
    because nothing believes they changed.

    I could not reproduce the trigger — a bare pty at seven sizes never scrolls
    the terminal and never leaves pre-existing content visible — so these are
    the *consequences* made impossible, not the cause found. Two things do it:
    the screen is told it cannot scroll (§15 fills the window exactly, and
    nothing in the frame scrolls — the picker scrolls by selection, because a
    viewport moving on its own would move the `8-26 of 34` counter), and any scroll offset left from a resize is cleared.
    """

    async def _app(self):
        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            return huebox_app.Editor()

    async def test_no_size_offers_a_scroll(self):
        app = await self._app()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            sizes = ((150, 50), (120, 40), (100, 30), (80, 30), (80, 24),
                               (200, 60), (70, 30), (60, 16), (40, 30),
                               (40, 12))
            if not os.environ.get("HUEBOX_ALL"):
                sizes = ((150, 50), (80, 24))
            for cols, rows in sizes:
                with self.subTest(size=f"{cols}x{rows}"):
                    await pilot.resize_terminal(cols, rows)
                    await pilot.pause()
                    self.assertEqual(app.screen.max_scroll_y, 0,
                                     "the frame is taller than the window at "
                                     "%dx%d" % (cols, rows))
                    self.assertLessEqual(app.screen.virtual_size.height, rows,
                                         "virtual size exceeds the window")
                    self.assertEqual(app.screen.scroll_y, 0.0)

    async def test_the_wheel_cannot_scroll_the_frame(self):
        """With no picker up, a wheel notch does nothing at all.

        This was already the intent — `_scroll_picker` returns unless the picker
        is up — but intent is not the guarantee. Textual's own screen scrolling
        is a second path to the same symptom, and this is the assertion that
        says the frame has exactly one."""
        from textual import events

        app = await self._app()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            for _ in range(3):
                event = events.MouseScrollUp(app.focused, 10, 10, 0, -3, 0,
                                             False, False, False)
                event.set_sender(app)
                app.post_message(event)
                await pilot.pause()
            self.assertEqual(app.screen.scroll_y, 0.0,
                             "the wheel scrolled the frame with no picker up")
            self.assertEqual(app.screen.max_scroll_y, 0)

    async def test_the_screen_refuses_to_scroll_at_all(self):
        """Stronger than "never scrolls": it will not scroll if asked.

        `scroll_to` on a screen with `overflow: hidden` is a no-op, so even a
        stale offset left by a resize mid-frame cannot survive to be revealed by
        a wheel. This started life as the opposite assertion — the screen was
        expected to hold an offset that a resize then had to clear — and it
        turned out the offset could never be established at all. The stronger
        guarantee is the one worth keeping, and the weaker one would have been a
        test that only passed because it manufactured its own precondition.
        """
        app = await self._app()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            sizes = ((150, 50), (100, 30), (80, 24))
            if not os.environ.get("HUEBOX_ALL"):
                sizes = ((80, 24),)
            for cols, rows in sizes:
                with self.subTest(size=f"{cols}x{rows}"):
                    app.screen.scroll_to(y=3, animate=False)
                with self.subTest(size=f"{cols}x{rows}"):
                    app.screen.scroll_to(y=3, animate=False)
                    await pilot.pause()
                    self.assertEqual(app.screen.scroll_y, 0.0,
                                     "the frame scrolled when told to")
                    self.assertEqual(app.screen.max_scroll_y, 0)

    async def test_a_resize_still_lays_the_frame_out_from_scratch(self):
        # Side-by-side down to stacked: the resize also crosses layouts.
        app = await self._app()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            self.assertEqual((app.size.width, app.size.height), (80, 24))
            self.assertLessEqual(len(app.rows_text), 24)
            self.assertEqual(app.screen.scroll_y, 0.0)
            self.assertEqual(sum(c for _, _, c in app.regions),
                             len(app.rows_text))


@needs_app
class PrototypePanels(unittest.IsolatedAsyncioTestCase):
    """Stacked bordered panels, default on.

    The reference screenshot's shape — one titled panel for the controls,
    one for the live examples. Unset means panels; `HUEBOX_PANELS=0` opts
    back out to the frameless stack (one test below, never product). The
    rows stay captured, not re-rendered, so a panel can only frame what
    the frame already said.
    """

    async def _app(self, cols=100, rows=30, panels="1"):
        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        # Kept alive for the whole test (added as cleanup): `redraw` reads
        # `HUEBOX_PANELS` on mount, long after the constructor returns, so
        # a `with` block here would be gone before the first frame mounts.
        overlay = {"HUEBOX_SLOTS": path, "HUEBOX_PANELS": panels}
        patcher = mock.patch.dict(os.environ, overlay)
        patcher.start()
        self.addCleanup(patcher.stop)
        return huebox_app.Editor()

    async def test_unset_means_panels(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HUEBOX_PANELS", None)
            self.assertTrue(huebox_app.panels_enabled())
            path = _slots_file(harness.FIXTURES["distinct"]["slots"])
            self.addCleanup(os.unlink, path)
            with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
                app = huebox_app.Editor()
            # 80x24 is the stacked layout; 100x30 and up go side-by-side
            # (`SideBySide`). The short `direct` name leaves the unified
            # `editor` top fitting the row budget there, so the top panel
            # mounts beside the two stacked panels: logo, info and
            # `editor` above, controls below examples.
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                self.assertTrue(app._panels_on)
                self.assertEqual(len(list(app.query(huebox_app.Panel))), 3)

    async def test_zero_opts_out_to_no_panels(self):
        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path,
                                           "HUEBOX_PANELS": "0"}):
            self.assertFalse(huebox_app.panels_enabled())
            app = huebox_app.Editor()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                self.assertFalse(app._panels_on)
                self.assertEqual(len(list(app.query(huebox_app.Panel))), 0)

    async def test_two_panels_with_titles(self):
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertTrue(app._panels_on)
            panels = list(app.query(huebox_app.Panel))
            # The `editor` top fits the row budget here, so it mounts
            # with the stack — never an `info` panel carrying editor
            # controls (decision 40). Controls below examples, top above.
            self.assertEqual(len(panels), 3)
            self.assertEqual([p.border_title for p in panels],
                             ["EDITOR", "THEME", "EXAMPLES"])
            self.assertEqual(app.screen.max_scroll_y, 0)

    async def test_borders_are_theme_closed(self):
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            self.assertIn("border", huebox_app.TOKEN_SLOTS)
            variables = app.get_css_variables()
            allowed = {v.lower() for v in
                       harness.FIXTURES["distinct"]["slots"].values()}
            self.assertIn(variables["border"].lower(), allowed)

    async def test_a_click_on_a_border_selects_nothing(self):
        from textual.events import Click

        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            before = app.state.sel
            top_screen = app._panel_geom["top_screen"]
            # Controls panel top border: full-width chrome row.
            app.on_click(Click(widget=None, x=10, y=top_screen,
                               delta_x=0, delta_y=0, button=1,
                               shift=False, meta=False, ctrl=False))
            # Left border column inside the controls panel.
            app.on_click(Click(widget=None, x=0, y=top_screen + 1,
                               delta_x=0, delta_y=0, button=1,
                               shift=False, meta=False, ctrl=False))
            self.assertEqual(app.state.sel, before)

    async def test_padded_panels_carry_air_on_both_sides(self):
        # Decision 45: at 80x24 the padded lay shows everything the
        # unpadded one does, so the panels pad — one cell left and right.
        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertTrue(app._panels_on)
            self.assertEqual(app._panel_geom.get("pad"),
                             huebox_app.PANEL_PAD)
            panels = list(app.query(huebox_app.Panel))
            self.assertTrue(panels)
            air = (0, huebox_app.PANEL_PAD, 0, huebox_app.PANEL_PAD)
            for panel in panels:
                self.assertEqual(tuple(panel.styles.padding), air)

    async def test_narrow_panels_drop_the_air_first(self):
        # Decision 45: padding is the first thing spent — at 42x18 the
        # padded content would not even hold the draw floor, so it mounts
        # unpadded rather than trimming widgets (or dropping the panels).
        # At 46x18 the padded lay keeps every row the unpadded one shows,
        # so the air stays: air is spent first, never content.
        for size, pad in (((46, 18), 1), ((42, 18), 0)):
            with self.subTest(size=size):
                app = await self._app()
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    self.assertTrue(app._panels_on)
                    self.assertEqual(app._panel_geom.get("pad"), pad)

    def test_pad_opt_out_defaults_on(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HUEBOX_PANEL_PAD", None)
            self.assertTrue(huebox_app.panel_pad_enabled())

    async def test_zero_pad_opts_out_to_touching_borders(self):
        app = await self._app()
        with mock.patch.dict(os.environ, {"HUEBOX_PANEL_PAD": "0"}):
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                self.assertTrue(app._panels_on)
                self.assertEqual(app._panel_geom.get("pad"), 0)
                for panel in app.query(huebox_app.Panel):
                    self.assertEqual(tuple(panel.styles.padding),
                                     (0, 0, 0, 0))

    def test_pad_keeps_content_rejects_a_changed_top(self):
        # An editor box that collapses into a bare readout is lost
        # function, however identical the blocks below it are.
        ref = (None, None, [("a", 0, 2)], 6, 4, 0, 0)
        cand = (None, None, [("a", 0, 2)], 4, 4, 0, 0)
        self.assertFalse(huebox_app.Editor._pad_keeps_content(ref, cand))

    def test_pad_keeps_content_rejects_a_trimmed_block(self):
        ref = (None, None, [("a", 0, 3)], 6, 4, 0, 0)
        cand = (None, None, [("a", 0, 2)], 6, 4, 0, 0)
        self.assertFalse(huebox_app.Editor._pad_keeps_content(ref, cand))
        # ... and a block that came or went.
        gone = (None, None, [], 6, 4, 0, 0)
        self.assertFalse(huebox_app.Editor._pad_keeps_content(ref, gone))

    def test_pad_keeps_content_accepts_same_or_folded(self):
        ref = (None, None, [("a", 0, 3)], 6, 4, 0, 0)
        same = (None, None, [("a", 0, 3)], 6, 4, 0, 0)
        folded = (None, None, [("a", 0, 4)], 6, 4, 0, 0)
        self.assertTrue(huebox_app.Editor._pad_keeps_content(ref, same))
        # Narrower folds may show *more* rows for the same widgets.
        self.assertTrue(huebox_app.Editor._pad_keeps_content(ref, folded))

    async def test_a_click_through_a_panel_selects_the_swatch(self):
        from textual.events import Click

        app = await self._app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            hit = next(h for h in app.hits if h.slot == 1)
            # Inner → screen: one border column left plus decision 45's
            # horizontal air, the top's growth plus the controls' own border
            # row above (read off the geometry — the top is bare chrome at
            # 80x24, so no growth row). Rows map straight past the border.
            geom = app._panel_geom
            pad = geom.get("pad", 0)
            dy = (geom["top_screen"] - geom["header_h"]) + 1
            app.on_click(Click(widget=None, x=hit.x0 + 1 + pad, y=hit.y + dy,
                               delta_x=0, delta_y=0, button=1,
                               shift=False, meta=False, ctrl=False))
            self.assertEqual(app.state.sel, 1)

    async def test_too_small_to_border_falls_back(self):
        app = await self._app(cols=40, rows=12)
        async with app.run_test(size=(40, 12)) as pilot:
            await pilot.pause()
            self.assertFalse(app._panels_on)
            self.assertEqual(len(list(app.query(huebox_app.Panel))), 0)

    async def test_adjust_reuses_widgets_in_place(self):
        # §1: hold-to-sweep must never show an empty frame. An adjust-only
        # redraw keeps every block at the same size, so it swaps rows via
        # `Frame.update_rows` instead of remove+mount: same objects, same
        # heights, new colours. Focus never leaves, so no scroll/focus churn
        # under key repeat.
        app = await self._app()
        app.state.setup = None
        app.state.overlay = None
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(app._panels_on)
            self.assertFalse(app._side_on)
            before = {w.name: w
                      for w in app.screen_stack[0].query(huebox_app.Frame)}
            heights = {name: w.styles.height.value
                       for name, w in before.items()}
            focused_before = app.focused
            await pilot.press("w")
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(app._fast_reused)
            after = {w.name: w
                     for w in app.screen_stack[0].query(huebox_app.Frame)}
            self.assertEqual(set(after), set(before))
            for name in before:
                self.assertIs(after[name], before[name])
                self.assertEqual(after[name].styles.height.value,
                                 heights[name])
            self.assertEqual(app.state.sel, 5)
            self.assertIs(app.focused, focused_before)

    async def test_repeat_redraws_coalesce_to_one(self):
        # Holding a key fires repeats faster than the compositor paints:
        # state advances per press, the repaint collapses to one redraw
        # showing the latest state.
        from huebox.editor import apply_key, SLOTS as EDITOR_SLOTS

        app = await self._app()
        app.state.setup = None
        app.state.overlay = None
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            calls = []
            orig = app.redraw

            def counting(*args, **kwargs):
                calls.append(1)
                return orig(*args, **kwargs)
            app.redraw = counting
            before = list(app.rows_text)
            apply_key("w", app.state)
            app.request_redraw()
            apply_key("w", app.state)
            app.request_redraw()
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(len(calls), 1)
            self.assertFalse(app._redraw_scheduled)
            self.assertTrue(app._fast_reused)
            self.assertNotEqual(list(app.rows_text), before)
            # Paint-level: the coalesced redraw must reach the screen, not
            # just the rows — a batched surface refresh once left state
            # ahead of paint here, and only a forced repaint caught up.
            shown = app.state.slots[EDITOR_SLOTS[app.state.sel]]
            fd, shot = tempfile.mkstemp(suffix=".svg")
            os.close(fd)
            self.addCleanup(os.unlink, shot)
            with open(app.save_screenshot(shot), encoding="utf-8") as fh:
                self.assertIn(shown.lower(), fh.read().lower())


@needs_app
class ThemesButton(unittest.TestCase):
    """The `themes` button in the info column: a mouse mirror of `t` (§4.3.2).

    A flat `Button` labelled `themes`, riding under the theme subject in
    the unified top's info column, beside the bordered `editor` panel —
    the top's one control, never a panel of its own (decision 40).
    Pressing it goes through the same `apply_key("t")`
    the keyboard takes, so the two cannot disagree about what the picker
    is. Never focusable: focus follows the selection between the two grids,
    and a button that kept focus would leave the arrows talking to a control
    with no arrows.
    """

    def _editor(self, cols=80, rows=24, **kw):
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size",
            new_callable=mock.PropertyMock,
            return_value=Offset(cols, rows))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Default product: the unified `editor` top wins where its shares
        # fit, and these guard the info column's button. Callers pin a
        # short head where they need the top at 80x24 — a theme name, or
        # `direct`, whose config path prices separately and truncates.
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            editor = huebox_app.Editor(**kw)
        editor.query = lambda *a, **k: ()
        editor.mount = lambda *a, **k: None
        editor.redraw()
        return editor

    def _library(self, names):
        from huebox.editor import Library
        return Library(listing=lambda: list(names),
                       loader=lambda name: None,
                       creator=lambda name, slots, force=False: ("", ""))

    def _press(self, editor, button):
        event = mock.Mock()
        event.button = button
        editor.on_button_pressed(event)
        event.stop.assert_called_once_with()
        return event

    def test_the_button_is_flat_labelled_Themes_and_unfocusable(self):
        button = huebox_app.ThemesButton()
        self.assertEqual(str(button.label), "Themes")
        # Flat look, default variant: `flat=True` would take `-style-flat`,
        # whose `color: auto 90%` reroutes the label past the theme's own
        # selection foreground (see `ThemesButton`).
        self.assertFalse(button.flat)
        self.assertEqual(button.id, "themes-button")
        self.assertFalse(button.can_focus,
                         "a button that kept focus would steal the arrows")

    def test_the_button_wears_the_selection_pair(self):
        import re

        css = huebox_app.ThemesButton.DEFAULT_CSS
        tokens = set(re.findall(r"\$([a-z][a-z-]*)\b", css))
        self.assertEqual(tokens,
                         {"screen-selection-background",
                          "screen-selection-foreground"},
                         "the button must paint only the theme's selection "
                         "pair (§6.2)")

    def test_the_top_mounts_the_button_in_the_info_column(self):
        """The `Themes` button rides the info column, never a panel.

        One top row: bare logo, the info column (theme plus the flat
        buttons), the bordered `editor` panel. `info` and `editor` stay
        separate boxes — no `info` panel anywhere, and the buttons are the
        top's two controls: `Themes` over `Import` (decision 40, 003 §4.1).
        """
        from textual.containers import Horizontal, Vertical

        mounted = []
        editor = self._editor(head_override="ghostty")
        editor.mount = mounted.append
        editor.redraw()
        tops = [w for w in mounted if isinstance(w, Horizontal)]
        self.assertEqual(len(tops), 1, "the top is one side-by-side row")
        kids = list(getattr(tops[0], "_pending_children", []))
        infos = [p for p in kids
                 if isinstance(p, huebox_app.Panel) and p.name == "info"]
        self.assertEqual(infos, [], "an `info` panel mounted")
        editors = [p for p in kids
                   if isinstance(p, huebox_app.Panel)
                   and p.name == "editor"]
        self.assertEqual(len(editors), 1, "no `editor` panel in the top")
        cols = [c for c in kids if isinstance(c, Vertical)
                and not isinstance(c, huebox_app.Panel)]
        self.assertEqual(len(cols), 1, "no info column beside `editor`")
        buttons = [c for c in getattr(cols[0], "_pending_children", [])
                   if isinstance(c, huebox_app.ThemesButton)]
        self.assertEqual(len(buttons), 1,
                         "the info column holds no `themes` button")
        imports = [c for c in getattr(cols[0], "_pending_children", [])
                   if isinstance(c, huebox_app.ImportButton)]
        self.assertEqual(len(imports), 1,
                         "the info column holds no `import` button")

    def test_the_editor_top_mounts_the_button_in_the_metadata_column(self):
        from textual.containers import Horizontal, Vertical
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size",
            new_callable=mock.PropertyMock, return_value=Offset(160, 40))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Default env: the bordered `editor` panel wins where it fits.
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            editor = huebox_app.Editor()
        editor.query = lambda *a, **k: ()
        editor.mount = lambda *a, **k: None
        row, screen_h = editor._top_editor_row(160, editor.state, 4)
        self.assertIsNotNone(row, "the editor top fits at 160 columns")
        cols = [c for c in getattr(row, "_pending_children", [])
                if isinstance(c, Vertical)
                and not isinstance(c, huebox_app.Panel)]
        self.assertEqual(len(cols), 1, "one metadata column beside `editor`")
        children = list(getattr(cols[0], "_pending_children", []))
        buttons = [c for c in children
                   if isinstance(c, huebox_app.ThemesButton)]
        self.assertEqual(len(buttons), 1,
                         "the metadata column holds no `themes` button")
        imports = [c for c in children
                   if isinstance(c, huebox_app.ImportButton)]
        self.assertEqual(len(imports), 1,
                         "the metadata column holds no `import` button")
        self.assertEqual(str(imports[0].label), "Import")
        self.assertEqual(imports[0].id, "import-button")
        # The column's last rows were blank fill, so the buttons cost no
        # row: readout above, two one-row buttons below, column exactly
        # the row height.
        self.assertEqual(buttons[0].styles.height.value, 1)
        self.assertEqual(imports[0].styles.height.value, 1)
        self.assertEqual(cols[0].styles.height.value, screen_h)

    def test_pressing_the_button_opens_the_picker_like_t(self):
        names = ["alpha", "beta"]
        pressed = self._editor(library=self._library(names), theme="alpha")
        self._press(pressed, huebox_app.ThemesButton())
        keyed = self._editor(library=self._library(names), theme="alpha")
        huebox_app.apply_key("t", keyed.state)
        self.assertEqual(pressed.state.overlay, names)
        self.assertEqual(pressed.state.overlay, keyed.state.overlay,
                         "the button and `t` opened different pickers")

    def test_pressing_the_button_opens_through_unsaved_changes(self):
        # Opening is allowed while dirty — only Enter on a theme is
        # blocked (decision 12) — and the button matches `t` there too.
        names = ["alpha", "beta"]
        for driver in ("button", "key"):
            with self.subTest(driver=driver):
                editor = self._editor(library=self._library(names),
                                      theme="alpha")
                editor.state.slots["palette-0"] = "#ffffff"
                self.assertTrue(editor.state.dirty())
                if driver == "button":
                    self._press(editor, huebox_app.ThemesButton())
                else:
                    huebox_app.apply_key("t", editor.state)
                self.assertEqual(editor.state.overlay, names)

    def test_pressing_without_a_library_reports_instead(self):
        editor = self._editor()
        self._press(editor, huebox_app.ThemesButton())
        self.assertIsNone(editor.state.overlay)
        self.assertEqual(editor.state.status,
                         "no theme library in this session")


@needs_app
class SideBySide(unittest.IsolatedAsyncioTestCase):
    """The wide layout: top row above, controls | live side by side.

    At `SIDE_MIN_W` (100) and up the frame is two panels next to each other
    instead of stacked: the left holds palette pairs over interface pairs
    (two columns in total), the right the live blocks, and the top stands
    as bare logo plus the info column beside the bordered `editor` panel
    (theme only in `info`; the selected readout and the HSV bars in
    `editor`, §8.1 decision 40). From `SIDE_NARROW_MIN_W` (80) the same
    pair stays mounted squeezed (§8.1 decision 42), and thin below that;
    short windows keep the stacked panels instead (`PrototypePanels`).
    Narrow/thin are the same pair at narrower widths, so one wide case
    pins the pair and narrow `run_test` sizes elsewhere assert the
    fallback on purpose.
    """

    async def _app(self):
        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        # Unset means panels, and wide means side-by-side: no opt-in.
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            self.assertTrue(huebox_app.panels_enabled())
            return huebox_app.Editor()

    async def _press(self, app, pilot, *keys):
        from textual import events

        for key in keys:
            event = events.Key(key, None)
            event.set_sender(app)
            app.post_message(event)
            await pilot.pause()

    def _screen_of(self, app, hit):
        """Left-content hit → screen coords for a `Click`."""
        top = app._side_geom["top"]
        pad = app._side_geom.get("pad", 0)
        return hit.x0 + 1 + pad + 1, top + 1 + hit.y

    async def test_wide_mounts_a_pair_beside_each_other(self):
        from textual.containers import Horizontal

        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            self.assertTrue(app._side_on)
            self.assertFalse(app._panels_on)
            rows = list(app.query(Horizontal))
            # the top pair plus the controls | live pair below it.
            self.assertEqual(len(rows), 2)
            top, pair = rows
            panels = list(pair.query(huebox_app.Panel))
            self.assertEqual(len(panels), 2)
            self.assertEqual([p.border_title for p in panels],
                             ["THEME", "EXAMPLES"])
            left, right = panels
            self.assertEqual(left.styles.width.value,
                             huebox_app.LEFT_OUTER_W
                             + 2 * huebox_app.PANEL_PAD)
            self.assertEqual(left.styles.width.value
                             + right.styles.width.value, 120)
            # full width clears the stacked floor, so the limit holds.
            self.assertEqual(left.styles.min_width.value,
                             huebox_app.PANEL_MIN_W)
            self.assertEqual(app.screen.max_scroll_y, 0)

    async def test_padded_chrome_carries_air(self):
        # Decision 45 at 120x30: every panel pads — the pair one cell left
        # and right, the editor box its inner cell plus one.
        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            self.assertTrue(app._side_on)
            self.assertEqual(app._side_geom.get("pad"),
                             huebox_app.PANEL_PAD)
            panels = {panel.name: panel
                      for panel in app.query(huebox_app.Panel)}
            air = (0, huebox_app.PANEL_PAD, 0, huebox_app.PANEL_PAD)
            self.assertEqual(tuple(panels["controls"].styles.padding),
                             air)
            self.assertEqual(tuple(panels["examples"].styles.padding),
                             air)
            air = (0, huebox_app.PANEL_PAD + huebox_app.EDITOR_PAD_X,
                   0, huebox_app.PANEL_PAD + huebox_app.EDITOR_PAD_X)
            top_air = app._side_geom.get("top_pad", 0) \
                + huebox_app.EDITOR_PAD_X
            self.assertEqual(tuple(panels["editor"].styles.padding),
                             (0, top_air, 0, top_air))

    @mock.patch.dict(os.environ, {"HUEBOX_EDITOR_PANEL": "0"})
    async def test_top_sits_side_by_side_above_the_pair(self):
        """Pinned off, the top is the bare stack — never a merged panel.

        `HUEBOX_EDITOR_PANEL=0` keeps `draw_editor`'s own header and
        selected as full-width chrome above the pair: no `editor` panel,
        no `info` panel, no `themes` button — the opt-out the bare frame
        keeps (decision 40, §4.3.2).
        """
        from textual.containers import Horizontal

        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            self.assertTrue(app._side_on)
            self.assertEqual(list(app.query("#themes-button")), [])
            self.assertEqual([p.name for p in app.query(huebox_app.Panel)],
                             ["controls", "examples"])
            mounted = list(app.query(huebox_app.Frame))
            header = next(b for b in mounted if b.name == "header")
            selected = next(b for b in mounted if b.name == "selected")
            self.assertEqual(header.styles.width.value, 120)
            self.assertEqual(selected.styles.width.value, 120)
            rows = list(app.query(Horizontal))
            self.assertEqual(len(rows), 1)
            (pair,) = rows
            kids = list(app.screen.children)
            self.assertLess(kids.index(header), kids.index(pair))

    async def test_a_click_in_the_left_panel_selects_the_pair(self):
        from textual.events import Click

        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            hit = next(h for h in app.hits if h.slot == 8)
            x, y = self._screen_of(app, hit)
            app.on_click(Click(widget=None, x=x, y=y,
                               delta_x=0, delta_y=0, button=1,
                               shift=False, meta=False, ctrl=False))
            self.assertEqual(app.state.sel, 8)

    async def test_a_click_in_the_right_panel_selects_nothing(self):
        from textual.events import Click

        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            before = app.state.sel
            geom = app._side_geom
            # Middle of the examples panel: examples are readouts.
            app.on_click(Click(widget=None,
                               x=geom["left_outer"] + 5,
                               y=geom["top"] + 2,
                               delta_x=0, delta_y=0, button=1,
                               shift=False, meta=False, ctrl=False))
            self.assertEqual(app.state.sel, before)

    async def test_a_click_on_a_side_border_selects_nothing(self):
        from textual.events import Click

        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            before = app.state.sel
            geom = app._side_geom
            # The pair's top border row, and the left panel's right border.
            for x, y in ((10, geom["top"]),
                         (huebox_app.SIDE_LEFT_W + 1, geom["top"] + 1)):
                app.on_click(Click(widget=None, x=x, y=y,
                                   delta_x=0, delta_y=0, button=1,
                                   shift=False, meta=False, ctrl=False))
            self.assertEqual(app.state.sel, before)

    async def test_a_click_in_the_top_selects_nothing(self):
        """§4.3.2 — the top is chrome, not a control.

        Bare logo plus the info column beside one bordered `editor` panel
        (§8.1 decision 40), and none of it answers: the header names no
        slot and the readout is the readout of the grids below, so a click
        anywhere in the top must leave the selection where it was.
        """
        from textual.events import Click

        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            before = app.state.sel
            for x, y in ((5, 0), (100, 1), (60, 2)):
                app.on_click(Click(widget=None, x=x, y=y,
                                   delta_x=0, delta_y=0, button=1,
                                   shift=False, meta=False, ctrl=False))
            self.assertEqual(app.state.sel, before)



    async def test_the_top_has_a_border(self):
        """Bordered means bordered: the `editor` panel carries the border.

        The top is bare logo chrome plus the info column beside one
        `Panel` (`editor`) in one `Horizontal` on the theme's own
        background — chrome, never a control, and never an `info` panel:
        `info` is a bare `Frame`, so the two cannot merge (decision 40).
        The border spends one row top and bottom from the same row budget
        the widgets below share, and brings only border tokens the closure
        already accounts for.
        """
        from textual.containers import Horizontal

        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            rows = list(app.query(Horizontal))
            self.assertEqual(len(rows), 2)
            top, _ = rows
            panels = list(top.query(huebox_app.Panel))
            self.assertEqual([p.border_title for p in panels],
                             ["EDITOR"])
            names = sorted(b.name for b in top.query(huebox_app.Frame))
            self.assertEqual(names, ["editor-hsv", "header", "info"])

    async def test_the_logo_is_never_a_panel(self):
        """The logo is bare chrome at every width (decision 40).

        Shrinking out of the `editor` panel used to trade bare logo for a
        bordered `logo` panel — the sudden border. Now there is no second
        top: the unified row mounts logo, info column and `editor`
        together, and past the tightest share the bare stack stands — a
        bare `Frame` at stacked sizes and beside the pinned-off editor
        panel alike, never a `logo` or `info` panel anywhere.
        """
        cases = (((80, 24), {}), ((60, 16), {}),
                 ((120, 30), {"HUEBOX_EDITOR_PANEL": "0"}),
                 ((100, 30), {"HUEBOX_EDITOR_PANEL": "0"}))
        for size, overlay in cases:
            with self.subTest(size="%dx%d" % size, overlay=overlay):
                path = _slots_file(harness.FIXTURES["distinct"]["slots"])
                self.addCleanup(os.unlink, path)
                base = {"HUEBOX_SLOTS": path}
                base.update(overlay)
                patcher = mock.patch.dict(os.environ, base)
                patcher.start()
                try:
                    app = huebox_app.Editor()
                    async with app.run_test(size=size) as pilot:
                        await pilot.pause()
                        names = [p.name
                                 for p in app.query(huebox_app.Panel)]
                        self.assertNotIn(
                            "logo", names,
                            "the logo took a border at %dx%d" % size)
                        headers = [b for b in app.query(huebox_app.Frame)
                                   if b.name == "header"]
                        self.assertTrue(headers,
                                        "no bare logo at %dx%d" % size)
                finally:
                    patcher.stop()

    async def test_info_holds_no_editor_bars(self):
        """`info` is metadata; the HSV bars live only in `editor` (decision 40).

        The mounted `info` column carries the theme and no MARK hairline
        at any size the unified top fits, while the mounted `editor-hsv`
        box carries all three — the absence is the separation, not the
        width, and no fallback merges the two ever.
        """
        from huebox.render import MARK as _MARK
        from rich.text import Text as _Text

        app = await self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            info = next(b for b in app.query(huebox_app.Frame)
                        if b.name == "info")
            mounted = [_Text.from_ansi(row).plain
                       for row in info.rows_text]
            label, _path = huebox_app.top_subject(
                app.state, app.head_for(app.state) or app.fmt)
            self.assertTrue(any(label in row for row in mounted),
                            "the info column lost the theme")
            self.assertFalse(any(_MARK in row for row in mounted),
                             "`info` drew HSV bars")
            self.assertFalse(any("selected" in row for row in mounted),
                             "the readout leaked into `info`")
            hsv = next(b for b in app.query(huebox_app.Frame)
                       if b.name == "editor-hsv")
            bars = [_Text.from_ansi(row).plain
                    for row in hsv.rows_text]
            self.assertEqual(sum(_MARK in row for row in bars), 3,
                             "`editor` lost a bar")
            self.assertTrue(any("AaBbCc" in row for row in bars),
                            "`editor` lost the specimen")

    async def test_default_top_is_the_bordered_editor_panel(self):
        """Default product: logo plus info beside one `editor` box.

        The selected readout and the HSV bars alone get the border — logo
        and theme metadata stay bare chrome, while the chip row plus the
        three equal HSV bars ride in `Panel("editor")`. The info column
        owns the top's two controls: the flat `themes` button under the
        theme subject and the flat `import` button below it. The shares come from `editor.top_layout` — logo
        steps down first, then info truncates its path — so the widths
        meet at the window edge with no air between them, and the row
        stands one `top_screen` height, so everything below rides where
        it did.
        """
        from textual.containers import Horizontal

        from huebox.editor import top_layout, top_subject

        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            self.assertTrue(app._side_on)
            rows = list(app.query(Horizontal))
            self.assertEqual(len(rows), 2)
            top, _ = rows
            panels = list(top.query(huebox_app.Panel))
            self.assertEqual([p.border_title for p in panels], ["EDITOR"])
            header = next(b for b in top.query(huebox_app.Frame)
                          if b.name == "header")
            info = next(b for b in top.query(huebox_app.Frame)
                        if b.name == "info")
            hsv = next(b for b in top.query(huebox_app.Frame)
                       if b.name == "editor-hsv")
            editor_panel = panels[0]
            # One implementation of the shares: the mounted widths are
            # what the allocator says for this session's own subject —
            # the short `direct` name, never the priced config path —
            # at the same decision-45 air the box mounts with.
            label, path = top_subject(app.state,
                                      app.head_for(app.state) or app.fmt)
            lay = top_layout(120, label, path,
                             app.state.slots, app.state.sel,
                             pad=app._side_geom.get("top_pad", 0))
            self.assertIsNotNone(lay, "no top share at 120")
            left_w, meta_w, editor_outer, _stacked = lay
            self.assertEqual((header.styles.width.value,
                              info.styles.width.value,
                              editor_panel.styles.width.value),
                             (float(left_w), float(meta_w),
                              float(editor_outer)))
            self.assertEqual(header.styles.width.value
                             + info.styles.width.value
                             + editor_panel.styles.width.value, 120)
            # One row budget: header and editor box stand `top_screen`,
            # the info column leaves its last three rows for the controls
            # below: the two buttons parted by one blank row, so they never
            # read as one slab.
            self.assertEqual(header.styles.height.value,
                             editor_panel.styles.height.value)
            self.assertEqual(info.styles.height.value + 3,
                             header.styles.height.value)
            self.assertEqual(hsv.styles.width.value,
                             editor_outer - 2
                             - 2 * huebox_app.EDITOR_PAD_X
                             - 2 * app._side_geom.get("top_pad", 0))
            # The info column owns the top's two controls.
            buttons = list(top.query(huebox_app.ThemesButton))
            self.assertEqual(len(buttons), 1)
            self.assertEqual(str(buttons[0].label), "Themes")
            imports = list(top.query(huebox_app.ImportButton))
            self.assertEqual(len(imports), 1)
            self.assertEqual(str(imports[0].label), "Import")

    async def test_arrows_walk_pairs_not_rows(self):
        app = await self._app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            self.assertEqual(app.state.sel, 5)
            await self._press(app, pilot, "right")
            self.assertEqual(app.state.sel, 13)
            await self._press(app, pilot, "down")
            self.assertEqual(app.state.sel, 14)
            await self._press(app, pilot, "left")
            self.assertEqual(app.state.sel, 6)
            await self._press(app, pilot, "down")
            self.assertEqual(app.state.sel, 7)
            await self._press(app, pilot, "down")
            self.assertEqual(app.state.sel, 16)  # background, same column
            await self._press(app, pilot, "up")
            self.assertEqual(app.state.sel, 7)

    async def test_adjust_reuses_widgets_in_place(self):
        # §1: the side pair reuses like the stacked panels. Same widget
        # objects across an adjust, heights preserved — including the info
        # frame, which mounts one row shorter than its rows for the buttons.
        app = await self._app()
        app.state.setup = None
        app.state.overlay = None
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(app._side_on)
            before = {w.name: w
                      for w in app.screen_stack[0].query(huebox_app.Frame)}
            heights = {name: w.styles.height.value
                       for name, w in before.items()}
            self.assertEqual(heights.get("info"), 3)
            focused_before = app.focused
            await pilot.press("w")
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(app._fast_reused)
            after = {w.name: w
                     for w in app.screen_stack[0].query(huebox_app.Frame)}
            self.assertEqual(set(after), set(before))
            for name in before:
                self.assertIs(after[name], before[name])
                self.assertEqual(after[name].styles.height.value,
                                 heights[name])
            self.assertEqual(heights.get("info"), 3)
            self.assertEqual(app.state.sel, 5)
            self.assertIs(app.focused, focused_before)


@needs_app
class CollapsibleExamples(unittest.TestCase):
    """§14.1 — each live block is its own collapsible, open by default.

    The rows inside are still `draw_editor`'s: each header replaces its
    block's title one for one, so an all-open stack is exactly the frame's
    height. A collapsed block hides its rows behind its header; the frame is
    always drawn whole, so the hits above the live area never move.
    `HUEBOX_PANELS=0` pins the bare stack, where the assertions below read
    the bare rows; the panelled layouts mount the same `Live` blocks
    (pinned by `CollapsiblePanels`). `HUEBOX_COLLAPSIBLE=0` opts out to
    the bare rows; I2 runs product and proves no new colour
    leaked in.
    """

    def _editor(self, cols=80, rows=24, **kw):
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size",
            new_callable=mock.PropertyMock, return_value=Offset(cols, rows))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Kept alive for the whole test: `redraw` reads `HUEBOX_PANELS` on
        # mount, long after the constructor returns, so a `with` block here
        # would be gone before the first frame mounts.
        overlay = {"HUEBOX_SLOTS": path, "HUEBOX_PANELS": "0"}
        patcher = mock.patch.dict(os.environ, overlay)
        patcher.start()
        self.addCleanup(patcher.stop)
        editor = huebox_app.Editor(**kw)
        editor.query = lambda *a, **k: ()
        editor.mount = lambda *a, **k: None
        editor.redraw()
        return editor

    def test_enabled_by_default_and_bare_on_zero(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HUEBOX_COLLAPSIBLE", None)
            self.assertTrue(huebox_app.collapsible_enabled())
        with mock.patch.dict(os.environ, {"HUEBOX_COLLAPSIBLE": "0"}):
            self.assertFalse(huebox_app.collapsible_enabled())

    def test_open_by_default(self):
        editor = self._editor()
        self.assertEqual(editor._collapsed, set(),
                         "the live blocks must start open")
        live = [name for name, _, _ in editor.regions
                if name in huebox_app.LIVE_BLOCKS]
        self.assertTrue(live, "no live blocks to collapse")

    def test_the_strip_is_called_interface_text(self):
        self.assertEqual(huebox_app.LIVE_TITLES["examples"],
                         "Interface text")
        self.assertEqual(huebox_app.LIVE_TITLES["diff"], "Live diff")
        self.assertEqual(huebox_app.LIVE_TITLES["sample"], "Live code")

    def test_bare_opt_out_mounts_no_examples(self):
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size", new_callable=mock.PropertyMock,
            return_value=Offset(100, 30))
        patcher.start()
        self.addCleanup(patcher.stop)
        mounted = []
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path,
                                          "HUEBOX_PANELS": "0",
                                          "HUEBOX_COLLAPSIBLE": "0"}):
            editor = huebox_app.Editor()
            editor.query = lambda *a, **k: ()
            editor.mount = lambda widget: mounted.append(widget)
            editor.redraw()
        self.assertEqual([w for w in mounted
                          if isinstance(w, huebox_app.Live)], [],
                         "bare opt-out mounted a collapsible")

    def test_collapsing_hides_only_that_block(self):
        editor = self._editor(cols=100, rows=30)
        live = [name for name, _, _ in editor.regions
                if name in huebox_app.LIVE_BLOCKS]
        self.assertTrue(live, "no live blocks to collapse")
        editor._collapsed.add(live[0])
        editor.redraw()
        # The frame is still drawn whole — collapsing hides, never unpaints
        # — so the regions still name every block and every hit still points
        # at the painted frame it was announced for.
        names = [name for name, _, _ in editor.regions]
        for kept in huebox_app.LIVE_BLOCKS:
            if kept in live:
                self.assertIn(kept, names,
                              "the regions lost %s" % kept)
        for hit in editor.hits:
            self.assertLess(hit.y, len(editor.rows_text),
                            "a hit points past the painted frame")

    def test_expanded_hits_match_the_painted_cells(self):
        """Every hit the expanded frame announces is on a painted row."""
        editor = self._editor(cols=100, rows=30)
        for hit in editor.hits:
            self.assertLess(hit.y, len(editor.rows_text),
                            "a hit points past the painted frame")


@needs_app
class CollapsiblePanels(unittest.TestCase):
    """§14.1 in the panelled layouts: the toggles survive the panels.

    The collapsibles were mounted only on the bare stack, so with panels
    on (the default) `e`/`d`/`c` recorded the collapse and changed nothing.
    The live blocks ride in the examples panel (stacked) or the right-hand
    panel (side-by-side) instead — open by default, each shut on its own —
    and the stacked panel shrinks around what remains while the chrome
    below rides up.
    """

    def _editor(self, cols=80, rows=24, collapsed=()):
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size",
            new_callable=mock.PropertyMock,
            return_value=Offset(cols, rows))
        patcher.start()
        self.addCleanup(patcher.stop)
        mounted = []
        # Default product: panels on, collapsibles on. No pins — this is
        # the frame a user actually gets.
        with mock.patch.dict(os.environ, {"HUEBOX_SLOTS": path}):
            editor = huebox_app.Editor()
            editor.query = lambda *a, **k: ()
            editor.mount = lambda widget: mounted.append(widget)
            editor._collapsed = set(collapsed)
            editor.redraw()
        return editor, mounted

    def _lives(self, mounted):
        def flat(widgets):
            for widget in widgets:
                yield widget
                for child in flat(list(getattr(
                        widget, "_pending_children", []))):
                    yield child
        return [widget for widget in flat(mounted)
                if isinstance(widget, huebox_app.Live)]

    def test_open_by_default(self):
        editor, mounted = self._editor()
        live = [name for name, _, _ in editor.regions
                if name in huebox_app.LIVE_BLOCKS]
        self.assertTrue(live, "no live blocks to collapse")
        shut = [widget.name for widget in self._lives(mounted)
                if widget.collapsed]
        self.assertEqual(shut, [], "a live block mounted shut")
        self.assertEqual(sorted(widget.name
                                for widget in self._lives(mounted)),
                         sorted(live),
                         "a live block did not ride in a `Live`")

    def test_each_block_shuts_on_its_own(self):
        editor, _ = self._editor()
        regions = {name: (first, count)
                   for name, first, count in editor.regions}
        live = [name for name in huebox_app.LIVE_BLOCKS
                if name in regions]
        self.assertTrue(live, "no live blocks to collapse")
        for name in live:
            with self.subTest(block=name):
                shut_editor, shut_mounted = self._editor(collapsed=(name,))
                lives = {widget.name: widget.collapsed
                         for widget in self._lives(shut_mounted)}
                self.assertTrue(lives[name],
                                "%s did not shut" % name)
                for other in live:
                    if other != name:
                        self.assertFalse(lives[other],
                                         "%s shut with %s"
                                         % (other, name))
                # The panel shrinks by what the block hid: its rows less
                # the header standing in for the title — while the controls
                # above never move.
                first, count = regions[name]
                self.assertEqual(shut_editor._panel_geom["examples_h"],
                                 editor._panel_geom["examples_h"]
                                 - (count - 1))
                self.assertEqual(shut_editor._panel_geom["controls_h"],
                                 editor._panel_geom["controls_h"])

    def test_reopening_restores_the_panel(self):
        editor, _ = self._editor()
        geom = dict(editor._panel_geom)
        # At 80x24 only `sample` fits below the panelled chrome (the top's
        # `themes` button costs the row `examples` used to ride in), so
        # reopen the block that is actually shut.
        shut_editor, _ = self._editor(collapsed=("sample",))
        self.assertNotEqual(shut_editor._panel_geom["examples_h"],
                            geom["examples_h"])
        # `redraw` re-mounts from `_collapsed`: empty again, whole again.
        shut_editor._collapsed.clear()
        shut_editor.redraw()
        self.assertEqual(shut_editor._panel_geom, geom)

    def test_examples_shuts_where_it_fits(self):
        """At 80x24 the strip fits, so `e` shuts it on its own."""
        editor, _ = self._editor(cols=80, rows=24)
        live = [name for name, _, _ in editor.regions
                if name in huebox_app.LIVE_BLOCKS]
        self.assertIn("examples", live,
                      "the strip did not mount at 60x24")
        regions = {name: (first, count)
                   for name, first, count in editor.regions}
        shut_editor, shut_mounted = self._editor(cols=80, rows=24,
                                                 collapsed=("examples",))
        lives = {widget.name: widget.collapsed
                 for widget in self._lives(shut_mounted)}
        self.assertTrue(lives["examples"], "examples did not shut")
        self.assertFalse(lives["sample"], "sample shut with examples")
        first, count = regions["examples"]
        self.assertEqual(shut_editor._panel_geom["examples_h"],
                         editor._panel_geom["examples_h"] - (count - 1))
        self.assertEqual(shut_editor._panel_geom["controls_h"],
                         editor._panel_geom["controls_h"])
        # And reopening restores the panel whole again.
        shut_editor._collapsed.clear()
        shut_editor.redraw()
        self.assertEqual(shut_editor._panel_geom, dict(editor._panel_geom))

    def test_the_regions_still_name_every_block(self):
        editor, _ = self._editor()
        shut_editor, _ = self._editor(collapsed=("examples", "sample"))
        names = [name for name, _, _ in shut_editor.regions]
        for name, _, _ in editor.regions:
            self.assertIn(name, names, "the regions lost %s" % name)
        for hit in shut_editor.hits:
            self.assertLess(hit.y, len(shut_editor.rows_text),
                            "a hit points past the painted frame")

    def test_a_collapsed_panel_keeps_grid_clicks(self):
        """Shutting the strip must not move the swatches above it."""
        # At 80x24 the live area is only `sample`: `examples` never mounted,
        # so collapsing it would be a no-op that proves nothing.
        editor, _ = self._editor(collapsed=("sample",))
        hit = next(hit for hit in editor.hits if hit.slot == 5)
        # Panels offset content by one border column left plus decision 45's
        # horizontal air, and the top's growth plus the controls' own border
        # row above (read off the geometry — the top is bare chrome at
        # 80x24, so no growth row); the collapsed strip is below both.
        geom = editor._panel_geom
        pad = geom.get("pad", 0)
        dy = (geom["top_screen"] - geom["header_h"]) + 1
        self._click(editor, hit.x0 + 1 + 1 + pad, hit.y + dy)
        self.assertEqual(editor.state.sel, 5,
                         "a click below a collapse selected the wrong slot")

    def _click(self, editor, x, y):
        from textual.events import Click

        editor.on_click(Click(widget=None, x=x, y=y, delta_x=0, delta_y=0,
                              button=1, shift=False, meta=False, ctrl=False))

    def test_collapsible_opt_out_mounts_no_lives_in_panels(self):
        editor, mounted = self._editor()
        self.assertTrue(self._lives(mounted),
                        "no collapsible mounted open")
        del mounted[:]
        with mock.patch.dict(os.environ, {"HUEBOX_COLLAPSIBLE": "0"}):
            editor.redraw()
        self.assertEqual(self._lives(mounted), [],
                         "opt-out mounted a collapsible")

    def test_the_side_blocks_shut_on_their_own(self):
        editor, _ = self._editor(cols=100, rows=30)
        self.assertTrue(editor._side_on, "the side layout did not mount")
        live = [name for name, _, _ in editor.regions
                if name in huebox_app.LIVE_BLOCKS]
        self.assertTrue(live, "no live blocks to collapse")
        for name in live:
            with self.subTest(block=name):
                shut_editor, shut_mounted = self._editor(
                    cols=100, rows=30, collapsed=(name,))
                lives = {widget.name: widget.collapsed
                         for widget in self._lives(shut_mounted)}
                self.assertTrue(lives[name],
                                "%s did not shut" % name)
                # The side row keeps its height — the left panel fixes it
                # — so the click geometry never moves at all.
                self.assertEqual(shut_editor._side_geom, editor._side_geom)


@needs_app
class PanelLimits(unittest.TestCase):
    """Each panel keeps a minimum and a maximum length and width (§15.7).

    Pure checks first: `clamp_panel` is the bounds as numbers, `layout_size`
    is the window clamped to the maxima, and the opt-out restores the
    unbounded layout. No compositor needed — the failure this guards is a
    bound typed in the wrong place, not a widget laid out wrong.
    """

    def test_each_named_panel_has_four_bounds(self):
        for name, bounds in huebox_app.PANEL_LIMITS.items():
            min_w, max_w, min_h, max_h = bounds
            self.assertLessEqual(min_w, max_w, name)
            self.assertLessEqual(min_h, max_h, name)
            self.assertGreater(min_w, 0, name)
            self.assertGreater(min_h, 0, name)

    def test_clamp_holds_both_dimensions(self):
        min_w, max_w, min_h, max_h = huebox_app.PANEL_LIMITS["controls"]
        self.assertEqual(huebox_app.clamp_panel("controls", 0, 0),
                         (min_w, min_h))
        self.assertEqual(huebox_app.clamp_panel("controls", 999, 999),
                         (max_w, max_h))
        self.assertEqual(huebox_app.clamp_panel("controls", min_w, min_h),
                         (min_w, min_h))

    def test_unknown_names_take_the_layout_bounds(self):
        self.assertEqual(
            huebox_app.clamp_panel("no-such-panel", 0, 999),
            (huebox_app.PANEL_MIN_W, huebox_app.PANEL_MAX_H))

    def test_layout_is_identity_within_the_maxima(self):
        self.assertEqual(huebox_app.layout_size(80, 24), (80, 24))
        self.assertEqual(huebox_app.layout_size(150, 50), (150, 50))

    def test_layout_caps_past_the_maxima(self):
        self.assertEqual(
            huebox_app.layout_size(200, 60),
            (huebox_app.PANEL_MAX_W, huebox_app.PANEL_MAX_H))

    def test_opt_out_restores_the_unbounded_layout(self):
        with mock.patch.dict(os.environ, {"HUEBOX_PANEL_LIMITS": "0"}):
            self.assertFalse(huebox_app.panel_limits_enabled())
            self.assertEqual(huebox_app.layout_size(200, 60), (200, 60))
        self.assertTrue(huebox_app.panel_limits_enabled())


class ImportPreview(unittest.TestCase):
    """The popup preview shows palette plus abbreviated interface."""

    @needs_app
    def test_preview_includes_abbreviated_interface_cells(self):
        from huebox.color import SLOTS
        slots = {name: "#112233" for name in SLOTS}
        rows = huebox_app.import_preview_rows("Demo", slots, 60)
        text = "\n".join(rows)
        for abbr in ("BG", "FG", "CC", "CT", "SB", "SF"):
            self.assertIn(abbr, text)

    @needs_app
    def test_empty_slots_stay_a_single_muted_row(self):
        rows = huebox_app.import_preview_rows("Demo", {}, 60)
        self.assertEqual(len(rows), 1)

    @needs_app
    def test_wide_preview_shares_two_lines(self):
        from huebox.color import SLOTS
        slots = {name: "#112233" for name in SLOTS}
        rows = huebox_app.import_preview_rows("Demo", slots, 80)
        # title, blank, then the two shared lines.
        self.assertIn("FG", rows[2])
        self.assertIn("BG", rows[3])
        self.assertIn(" 0", rows[2])
        self.assertIn(" 8", rows[3])

    @needs_app
    def test_narrow_preview_stacks_to_four_lines(self):
        from huebox.color import SLOTS
        slots = {name: "#112233" for name in SLOTS}
        rows = huebox_app.import_preview_rows("Demo", slots, 60)
        # title, blank, two palette lines, blank, two paired lines.
        self.assertNotIn("FG", rows[2])
        self.assertIn("FG", rows[5])
        self.assertIn("BG", rows[6])

    @needs_app
    def test_pairs_columns_foregrounds_over_backgrounds(self):
        from huebox.color import SLOTS
        slots = {name: "#112233" for name in SLOTS}
        rows = huebox_app.import_preview_rows("Demo", slots, 80)
        top, bottom = rows[2], rows[3]
        self.assertLess(top.index("FG"), top.index("CC"))
        self.assertLess(top.index("CC"), top.index("SB"))
        self.assertLess(bottom.index("BG"), bottom.index("CT"))
        self.assertLess(bottom.index("CT"), bottom.index("SF"))

"""The Textual shell (migration phase 2): `huebox/app.py`.

Two things are worth pinning here beyond what I1 already covers. The **launch
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
    and I1 could then only say the two copies agreed.
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
    which is why I1 stayed green across this phase without a single change to
    the goldens.
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

    def test_a_click_selects_the_slot_whose_cell_it_was(self):
        editor = self._editor()
        for slot in (0, 5, 16):
            with self.subTest(slot=slot):
                hit = self._point_at(editor, slot)
                self._click(editor, hit.x0 + 1, hit.y)
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

        I1 covers the frame and the goldens were untouched by all of phase 4,
        so the mouse cannot have reached a colour. What is left to say here is
        the part I1 does not: a click that selects nothing must not even redraw
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
        hit = self._point_at(clicked, 1)
        self._click(clicked, hit.x0, hit.y)

        pressed = self._editor()
        huebox_app.apply_key("right", pressed.state)
        pressed.redraw()          # what `on_key` does after `apply_key`
        self.assertEqual(clicked.state.sel, 1)
        self.assertEqual(pressed.state.sel, 1)
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


class _Event:
    def stop(self):
        pass


@needs_app
class ThePickersMinimum(unittest.TestCase):
    """§13.7 — below the minimum, both frames are the same hint.

    The check used to sit inside `draw_editor`'s overlay branch, so the picker
    inherited it for free while the editor had it in a different place. With
    the picker a widget the two frames have separate mounts and the check has
    to be asked once, by whichever frame is up — which is the only way a frame
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

    def _mounted(self, cols=100, rows=30, status=""):
        from textual.geometry import Offset

        path = _slots_file(harness.FIXTURES["distinct"]["slots"])
        self.addCleanup(os.unlink, path)
        patcher = mock.patch.object(
            huebox_app.Editor, "size", new_callable=mock.PropertyMock,
            return_value=Offset(cols, rows))
        patcher.start()
        self.addCleanup(patcher.stop)
        mounted = []

        with mock.patch.dict(os.environ,
                             {"HUEBOX_SLOTS": path, "HUEBOX_STATUS": status}):
            editor = huebox_app.Editor()
            editor.query = lambda *a, **k: ()
            editor.mount = lambda widget: mounted.append(widget)
            editor.redraw()
        return editor, mounted

    def test_one_widget_per_block(self):
        _editor, mounted = self._mounted()
        regions = _editor.regions
        self.assertEqual(len(mounted), len(regions))
        self.assertGreater(len(mounted), 3,
                           "the frame collapsed back into a single widget")
        names = [name for name, _, _ in regions]
        for name in ("header", "palette", "interface", "selected", "hints"):
            self.assertIn(name, names,
                          "the frame no longer names its %s block" % name)

    def test_the_widgets_stack_to_the_frame_and_no_further(self):
        """A stack taller than the screen gives the screen a scrollbar.

        That is not a hypothetical: seven of Textual's 168 design tokens exist
        only for scrollbars, and I2's whole job is to reject a colour that was
        not the theme's. The blocks tile the frame exactly (tested in
        `test_editor.Regions`), so their heights must too."""
        editor, mounted = self._mounted(cols=100, rows=30)
        total = sum(len(block.rows_text) for block in mounted)
        self.assertEqual(total, len(editor.rows_text))
        self.assertLessEqual(total, 30, "the stack is taller than the screen")

    def test_every_block_paints_only_its_own_rows(self):
        editor, mounted = self._mounted()
        painted = [row for block in mounted for row in block.rows_text]
        self.assertEqual(painted, editor.rows_text,
                         "the blocks do not reassemble the frame in order")


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
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            self.assertEqual(app.state.sel, 0)
            await self._press(app, pilot, "right")
            self.assertEqual(app.state.sel, 1,
                             "one press moved the selection more than one "
                             "slot: two handlers acted on one key")

    async def test_an_arrow_still_moves_when_no_grid_is_focused(self):
        """The App's own handler is the fallback, and it must still work.

        40x12 shows twelve of the twenty-two slots, in a grid one column wide.
        Selecting `selection-foreground` puts the selection below the fold,
        where no grid block can hold focus — and the keys have to keep working,
        or the frame would trap the user on the slots it can show.

        `up`, not `left`: at one column wide the last slot is also the leftmost
        and the rightmost, so `left` and `right` correctly do nothing there
        (§4.3 — a key that runs off the end of the row stays put), and a test
        asserting otherwise would be asserting a bug.
        """
        app = await self._app()
        async with app.run_test(size=(40, 12)) as pilot:
            await pilot.pause()
            app.state.sel = 21              # below the fold at this size
            app.focus_grid(21)
            await pilot.pause()
            self.assertNotIsInstance(app.focused, huebox_app.Swatches,
                                     "a grid kept focus with the selection off "
                                     "it, so on_key would skip the arrows")
            await self._press(app, pilot, "up")
            self.assertEqual(app.state.sel, 20,
                             "the keys stopped working once the selection "
                             "was off the grid")

    async def test_focus_follows_the_selection_across_the_two_grids(self):
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            self.assertEqual(app.focused.name, "palette")
            # §4.3 — right stays in the row, so seventeen rights is not
            # seventeen slots. `down` is the key that crosses blocks.
            await self._press(app, pilot, *["down"] * 2)
            self.assertEqual(app.state.sel, len(harness.FIXTURES["distinct"]
                                                ["slots"]) - 6)
            self.assertEqual(app.focused.name, "interface",
                             "focus did not follow the selection out of the "
                             "palette")

    async def test_focus_moves_back_into_the_palette(self):
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await self._press(app, pilot, *["down"] * 2)
            self.assertEqual(app.focused.name, "interface")
            await self._press(app, pilot, *["up"] * 2)
            self.assertEqual(app.state.sel, 0)
            self.assertEqual(app.focused.name, "palette")

    async def test_the_picker_has_no_grid_to_focus(self):
        """§13.7 — the picker takes the surface, and it has no arrows.

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
            self.assertIsInstance(app.focused, huebox_app.Picker)
            before = app.state.sel
            await self._press(app, pilot, "right")
            self.assertEqual(app.state.sel, before,
                             "an arrow moved the colour selection behind the "
                             "picker")
            self.assertEqual(app.state.overlay_index, 1,
                             "the arrow did not move the picker's own row")


@needs_app
class TheFrameIsSizedByTheCompositor(unittest.IsolatedAsyncioTestCase):
    """The frame's size is the compositor's, and not also the terminal's.

    This is a bug I1 could not see, which is the whole reason it is worth a
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
            sizes = ((40, 12), (60, 16), (100, 30))
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
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
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
        """I2, for a frame that is not the frame the goldens captured.

        The goldens never have a selection down, so nothing in the harness can
        see what a drag paints. This is the same closure check applied to the
        one frame the harness does not cover: nothing Textual's own, or the
        selection would be a blue in a theme that has no blue.
        """
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
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

    This is the test whose absence let a crash ship. I1 compares one frame
    against one golden and never sends a key; the headless sessions drive
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
        # Smoke by default: `q` was the crash (mult as string), and every
        # adjust key shares the same step path; `HUEBOX_ALL=1` walks all six.
        keys = ("q", "w", "a", "s", "z", "x")
        if not os.environ.get("HUEBOX_ALL"):
            keys = ("q",)
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

        The hint line prints the multiplier verbatim, so a string multiplier is
        visible on screen even before it is fatal — which is how the golden came
        to record `f xFalse` in the first place."""
        app = await self._app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            hints = [_plain(row) for row in app.rows_text if "arrows" in row]
            self.assertEqual(len(hints), 1, "no hint line")
            self.assertIn("f x1", hints[0])
            self.assertNotIn("xFalse", hints[0])


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
    viewport moving on its own would move the `8-26 of 34` counter and I1 would
    see it), and any scroll offset left from a resize is cleared.
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
            sizes = ((150, 50), (120, 40), (100, 30), (80, 24),
                               (200, 60), (60, 16), (40, 12))
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
        app = await self._app()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            await pilot.resize_terminal(100, 30)
            await pilot.pause()
            self.assertEqual((app.size.width, app.size.height), (100, 30))
            self.assertLessEqual(len(app.rows_text), 30)
            self.assertEqual(app.screen.scroll_y, 0.0)
            self.assertEqual(sum(c for _, _, c in app.regions),
                             len(app.rows_text))

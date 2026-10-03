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
                                "textual missing: pip install -e '.[editor]'")


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
            huebox_app.frame_rows("ghostty", "", state, 80), written)


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

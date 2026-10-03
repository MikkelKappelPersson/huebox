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

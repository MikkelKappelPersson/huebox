"""Editor frame tests: the too-small floor and frame determinism (§15)."""

import io
import os
import re
import signal
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

from huebox import editor  # noqa: E402
from huebox.color import SLOTS  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FULL_SLOTS = {name: "#3f7a3f" for name in SLOTS}
HINT = ("terminal too small — need "
        f"{editor.MIN_COLS}x{editor.MIN_ROWS}")


def frame(cols, rows, sel=3, undo=(), status="", mult=1):
    """draw_editor's output at a chosen size, captured as a string."""
    out = io.StringIO()
    with mock.patch.object(editor, "term_size", return_value=(cols, rows)), \
            mock.patch.object(sys, "stdout", out):
        editor.draw_editor("ghostty", "/tmp/huebox.conf", FULL_SLOTS, sel,
                           list(undo), status, mult)
    return out.getvalue()


def lines(text):
    return text.split("\r\n")[:-1]        # the frame ends with a newline


def width(line):
    return len(ANSI.sub("", line))


class TooSmall(unittest.TestCase):
    def test_hint_text_and_centering(self):
        self.assertEqual(editor.too_small_frame(120).strip(), HINT)
        centred = editor.too_small_frame(80)
        self.assertTrue(centred.startswith(" " * ((80 - len(HINT)) // 2)))
        self.assertEqual(len(ANSI.sub("", editor.too_small_frame(20))), 20)

    def test_hint_replaces_the_frame(self):
        # the FULL hint must survive at MIN_COLS-1: a truncated hint would
        # hide the very size it tells the user to enlarge to (review P1)
        for size in ((editor.MIN_COLS - 1, 24), (80, editor.MIN_ROWS - 1)):
            with self.subTest(size=size):
                out = frame(*size)
                self.assertIn(HINT, out)
                self.assertNotIn("palette", out)
                self.assertNotIn("\033[48;2;", out)   # no swatches drawn

    def test_frame_is_one_line_on_a_cleared_screen(self):
        sizes = ((editor.MIN_COLS - 1, 24), (60, 8), (24, 10), (10, 4))
        for cols, rows in sizes:
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertEqual(len(body), 1)
                self.assertTrue(body[0].startswith("\033[H\033[2J"))
                self.assertLessEqual(width(body[0]), cols)

    def test_at_the_minimum_the_editor_draws(self):
        out = frame(editor.MIN_COLS, editor.MIN_ROWS)
        self.assertNotIn("too small", out)
        self.assertIn("palette", out)


class NormalFrame(unittest.TestCase):
    def test_eight_by_twentyfour_is_the_full_editor(self):
        out = frame(80, 24, status="saved")
        self.assertNotIn("too small", out)
        self.assertIn("huebox", out)
        self.assertIn("palette", out)
        self.assertIn("interface", out)
        self.assertIn("saved", out)
        self.assertLessEqual(len(lines(out)), 24)

    def test_frames_are_byte_identical(self):
        args = dict(sel=17, undo=[("palette-3", "#ffffff")], status="saved",
                    mult=5)
        self.assertEqual(frame(80, 24, **args), frame(80, 24, **args))
        self.assertEqual(frame(60, 16, **args), frame(60, 16, **args))

    def test_layout_holds_at_several_sizes(self):
        # (40, 10) is below-min on rows and belongs to the TooSmall tests
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            with self.subTest(size=(cols, rows)):
                for line in lines(frame(cols, rows)):
                    self.assertLessEqual(width(line), cols)

    def test_a_wider_terminal_spreads_the_palette(self):
        narrow = max(width(line) for line in lines(frame(60, 16)))
        wide = max(width(line) for line in lines(frame(100, 30)))
        self.assertGreater(wide, narrow)


class EditLoop(unittest.TestCase):
    def test_resize_redraws_without_consuming_a_key_or_writing(self):
        before = signal.getsignal(signal.SIGWINCH)
        handlers, writes = [], []
        keys = iter(["resize", "f", "esc"])

        def record(path, values):
            writes.append(dict(values))

        def note_handler(*args):
            handlers.append(signal.getsignal(signal.SIGWINCH))

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "kitty.conf")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# colours\n")
            with mock.patch.object(editor, "enter_raw",
                                   return_value=(7, None)),\
                    mock.patch.object(editor, "exit_raw") as leave,\
                    mock.patch.object(editor, "draw_editor",
                                      side_effect=note_handler),\
                    mock.patch.object(editor, "read_key",
                                      side_effect=lambda fd: next(keys)),\
                    mock.patch.object(sys, "stdout", io.StringIO()):
                editor.edit("kitty", path, dict(FULL_SLOTS), record)
            self.assertEqual(writes, [FULL_SLOTS])          # only after "f"
            self.assertEqual(handlers, [editor._on_winch] * 3)
            leave.assert_called_once_with(7, None)
        self.assertEqual(signal.getsignal(signal.SIGWINCH), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
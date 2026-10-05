"""Terminal I/O tests: the live size query, and nothing else.

`read_key`, the raw-mode pair, the SIGWINCH flag and `MIN_COLS`/`MIN_ROWS` used
to live in `tui.py` and had their cases here. All of it is gone: the editor is
the Textual shell, which owns input, resize and raw mode, and the minimum size
is layout policy now in `render.py`. What remains is the one function still
here, because `render.py` has to stay pure and cannot ask the terminal anything.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402
from huebox import render, tui  # noqa: E402


class Terminal(unittest.TestCase):
    def test_term_size_never_zero(self):
        self.assertTrue(all(v > 0 for v in huebox.term_size(default=(80, 24))))

    def test_term_size_falls_back_when_there_is_no_terminal(self):
        # A pipe or a closed stdout must not report 0x0: `cli show` sizes its
        # preview from this, and a zero width would blank the whole thing.
        import unittest.mock as mock

        with mock.patch.object(tui.os, "get_terminal_size",
                               side_effect=OSError):
            self.assertEqual(huebox.term_size(default=(80, 24)), (80, 24))
        with mock.patch.object(tui.os, "get_terminal_size",
                               return_value=mock.Mock(columns=0, lines=0)):
            self.assertEqual(huebox.term_size(default=(80, 24)), (80, 24))


class Minimum(unittest.TestCase):
    """§15.4 — the size below which nothing honest fits, now in `render.py`."""

    def test_the_minimum_lives_with_the_renderer(self):
        self.assertEqual((render.MIN_COLS, render.MIN_ROWS), (40, 12))

    def test_term_io_no_longer_owns_layout_policy(self):
        self.assertFalse(hasattr(tui, "MIN_COLS"),
                         "tui.py still holds the minimum; layout policy "
                         "belongs with the frame that has to fit")

    def test_term_io_no_longer_owns_input(self):
        for name in ("read_key", "enter_raw", "exit_raw", "_on_winch"):
            self.assertFalse(hasattr(tui, name),
                             f"tui.py still has {name}: Textual owns input and "
                             f"raw mode now, and a second copy is a second "
                             f"chance to get it wrong")


if __name__ == "__main__":
    unittest.main()

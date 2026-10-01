"""Terminal I/O tests: live size queries, the resize wake and key parsing."""

import os
import signal
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402
from huebox import tui  # noqa: E402

TICK = 0.01   # tests must not sleep a production tick


class Terminal(unittest.TestCase):
    def test_term_size_never_zero(self):
        self.assertTrue(all(v > 0 for v in huebox.term_size(default=(80, 24))))


class Pipe:
    """A real fd pair, so select() is exercised rather than mocked."""

    def __init__(self, test, data=b""):
        self.read_fd, self.write_fd = os.pipe()
        test.addCleanup(os.close, self.read_fd)
        test.addCleanup(os.close, self.write_fd)
        self.send(data)

    def send(self, data):
        os.write(self.write_fd, data)


class KeyReading(unittest.TestCase):
    def setUp(self):
        tui._resized = False
        self.addCleanup(setattr, tui, "_resized", False)

    def test_resize_wake_returns_resize(self):
        pipe = Pipe(self)                       # nothing to read
        tui._resized = True
        started = time.monotonic()
        self.assertEqual(tui.read_key(pipe.read_fd, tick=TICK), "resize")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(tui._resized)           # consumed by that one wake

    def test_input_wins_over_a_pending_resize(self):
        pipe = Pipe(self, b"\x1b[A")
        tui._resized = True
        self.assertEqual(tui.read_key(pipe.read_fd, tick=TICK), "up")
        self.assertTrue(tui._resized)            # still owed a redraw

    def test_waits_for_a_key_while_the_flag_is_clear(self):
        pipe = Pipe(self)

        def later():
            time.sleep(5 * TICK)
            pipe.send(b"z")

        writer = threading.Thread(target=later)
        writer.start()
        self.addCleanup(writer.join)
        self.assertEqual(tui.read_key(pipe.read_fd, tick=TICK), "z")

    def test_byte_and_escape_parsing_is_unchanged(self):
        pipe = Pipe(self, b"a")
        self.assertEqual(tui.read_key(pipe.read_fd, tick=TICK), "a")
        pipe.send(b"\033")                       # bare Escape, nothing follows
        self.assertEqual(tui.read_key(pipe.read_fd, tick=TICK), "esc")
        pipe.send(b"\033OB")                     # SS3 down
        self.assertEqual(tui.read_key(pipe.read_fd, tick=TICK), "down")
        pipe.send(b"\033[5~")                    # unmapped CSI
        self.assertEqual(tui.read_key(pipe.read_fd, tick=TICK), "esc")

    def test_signal_handler_only_raises_the_flag(self):
        tui._on_winch(signal.SIGWINCH, None)
        self.assertTrue(tui._resized)


if __name__ == "__main__":
    unittest.main(verbosity=2)

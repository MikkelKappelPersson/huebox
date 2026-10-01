"""Terminal I/O tests: live size queries."""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

import huebox  # noqa: E402


class Terminal(unittest.TestCase):
    def test_term_size_never_zero(self):
        self.assertTrue(all(v > 0 for v in huebox.term_size(default=(80, 24))))


if __name__ == "__main__":
    unittest.main(verbosity=2)

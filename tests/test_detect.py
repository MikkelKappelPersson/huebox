"""Detection tests: --config format inference (§7.1, plan 3)."""

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

from huebox.detect import infer_format  # noqa: E402

GHOSTTY = "background = #101014\n"
KITTY = "background  #101014\n"


class InferFormat(unittest.TestCase):
    """Name table first, file content second, tie -> None (§7.1)."""

    def _tmp(self, name, text):
        base = os.path.join(tempfile.mkdtemp(), name.lstrip("/"))
        self.addCleanup(shutil.rmtree, os.path.dirname(base),
                        ignore_errors=True)
        with open(base, "w", encoding="utf-8") as handle:
            handle.write(text)
        return base

    def test_a_known_name_wins_over_content(self):
        # kitty-syntax colours inside a bare `config` file: the name table
        # says ghostty, and the name wins
        path = self._tmp("/config", KITTY)
        self.assertEqual(infer_format(path), "ghostty")

    def test_content_decides_for_unknown_names(self):
        # kitty's mid is `\s*=?` so it reads `key = value` too — a ghostty
        # line counts for BOTH readers, which is why the tie test below
        # uses it. A kitty-syntax line (no `=`) is unambiguous.
        path = self._tmp("/theme-x", KITTY)
        self.assertEqual(infer_format(path), "kitty")

    def test_a_tie_is_not_inferable(self):
        # `background = #…` parses as ghostty AND kitty: one slot each,
        # no winner
        path = self._tmp("/theme-y", GHOSTTY)
        self.assertIsNone(infer_format(path))

    def test_an_empty_file_is_not_inferable(self):
        path = self._tmp("/theme-z", "")
        self.assertIsNone(infer_format(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
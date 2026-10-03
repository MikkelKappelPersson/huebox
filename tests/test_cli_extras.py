"""The editor's optional-dependency guard (textual-migration §8, phase 1).

`huebox edit` is the only command that will need Textual, so Textual is an
extra and not a dependency (§8). That makes a missing extra a user error: it has
to arrive as one stderr line and exit 1, never as a traceback from an import
(AGENTS.md).

The guard reads `editor.REQUIRES` rather than naming a package, because until
the migration's phase 3 lands the editor is still the stdlib one and `huebox
edit` has to keep working on a bare install. So these tests pin both halves:
quiet now, and a clean failure the moment the editor declares something.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`

from huebox import cli, editor  # noqa: E402

MISSING = "definitely_not_installed_huebox"


class TestEditorRequirements(unittest.TestCase):
    def _run_with(self, requires):
        """Run the editor entry point with `editor.REQUIRES` set to `requires`."""
        original = editor.REQUIRES
        editor.REQUIRES = requires
        stderr = io.StringIO()
        try:
            with redirect_stderr(stderr):
                code = cli._run_editor(None)
        finally:
            editor.REQUIRES = original
        return code, stderr.getvalue()

    def test_phase_1_needs_no_extra_at_all(self):
        # The actual state: the editor is still the stdlib one, so a bare
        # install can edit and this guard has nothing to say.
        self.assertEqual(editor.REQUIRES, ())
        self.assertEqual(cli._missing_extras(), [])

    def test_a_missing_extra_is_one_line_on_stderr_and_exit_1(self):
        code, err = self._run_with((MISSING,))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("huebox: "), err)
        self.assertIn(MISSING, err)
        self.assertIn("pipx install", err)
        # one line, and no traceback: that is the whole point of the guard
        self.assertNotIn("\n", err.strip(), err)
        self.assertNotIn("Traceback", err)

    def test_the_message_offers_the_command_that_needs_no_extra(self):
        _, err = self._run_with((MISSING,))
        self.assertIn("huebox show", err)

    def test_several_missing_extras_are_named_together(self):
        code, err = self._run_with(("nope_one_huebox", "nope_two_huebox"))
        self.assertEqual(code, 1)
        self.assertIn("nope_one_huebox,nope_two_huebox", err)

    def test_an_installed_requirement_is_silent(self):
        # The shape the phase 3 check will take for textual: importable means
        # the guard stays out of the way.
        original = editor.REQUIRES
        editor.REQUIRES = ("sys",)
        try:
            self.assertEqual(cli._missing_extras(), [])
        finally:
            editor.REQUIRES = original

    def test_cli_reads_the_editors_declaration_live(self):
        """`cli` must not hold its own copy of the list.

        A `from .editor import REQUIRES` would bind a second name at import
        time, and `editor.REQUIRES` changing later would silently leave the
        guard checking the wrong thing. Patching only `editor.REQUIRES` and
        seeing the guard react is what rules that out.
        """
        original = editor.REQUIRES
        editor.REQUIRES = (MISSING,)
        try:
            self.assertEqual(cli._missing_extras(), [MISSING])
        finally:
            editor.REQUIRES = original


if __name__ == "__main__":
    unittest.main()
"""The editor's optional-dependency guard (textual-migration §8, phase 1).

`huebox edit` is the only command that will need Textual, so Textual is an
extra and not a dependency (§8). That makes a missing extra a user error: it has
to arrive as one stderr line and exit 1, never as a traceback from an import
(AGENTS.md).

The guard reads `editor.REQUIRES` rather than naming a package, so `cli` never
holds its own copy of the list. The message it prints names something else
again — the *extra group* that provides those modules — and that distinction
is the whole subject of the tests below: `_missing_extras` reports modules
(`textual`, for `find_spec`), while `pipx install 'huebox[…]'` needs the
group (`editor`, from `pyproject.toml`). Joining module names into the
brackets once produced `huebox[textual]`, which nobody can install.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`

from huebox import cli, editor  # noqa: E402

MISSING = "definitely_not_installed_huebox"


class TTY(io.StringIO):
    """A StringIO that claims to be a terminal, and captures anyway."""

    def isatty(self):
        return True


class TestEditorRequirements(unittest.TestCase):
    def _run_with(self, requires):
        """Run the editor entry point with `editor.REQUIRES` set to `requires`.

        The tty test is patched on because `_run_editor` asks it first, and on
        purpose: `huebox edit | cat` should say what is wrong with a piped
        session rather than ask for an install it will never use. `TTY` rather
        than a bare `StringIO` because `_run_editor` reads `sys.stdout.isatty()`
        too — patching `sys.stdout` with something that says no would send it
        down the "not a terminal" path and the extra check would never run.
        """
        original = editor.REQUIRES
        editor.REQUIRES = requires
        out, err = TTY(), io.StringIO()
        target = cli.Target(None, "direct:/tmp/huebox.conf",
                            "/tmp/huebox.conf", {})
        try:
            with mock.patch.object(sys, "stdin", TTY()), \
                    mock.patch.object(sys, "stdout", out), \
                    redirect_stderr(err):
                code = cli._run_editor(target)
        finally:
            editor.REQUIRES = original
        return code, err.getvalue()

    def test_the_editor_names_the_extra_it_runs_on(self):
        # Phase 3: `huebox edit` is the Textual shell, so the editor says it
        # needs Textual and `cli` reports it missing rather than letting an
        # ImportError out. Naming an extra is never speculative here — it lands
        # in the same commit that makes the command use it.
        self.assertEqual(editor.REQUIRES, ("textual",))

    def test_a_missing_extra_is_one_line_on_stderr_and_exit_1(self):
        code, err = self._run_with((MISSING,))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("huebox: "), err)
        self.assertIn("pipx install", err)
        # one line, and no traceback: that is the whole point of the guard
        self.assertNotIn("\n", err.strip(), err)
        self.assertNotIn("Traceback", err)

    def test_the_message_names_the_extra_group_not_the_module(self):
        # The failure this guards against shipped: the message joined the
        # missing *module* names into `huebox[…]`, printing
        # `pipx install 'huebox[textual]'` — an extra that does not exist.
        # A user who follows that line installs nothing and gets the same
        # error back. The group is `editor`; the modules are what `find_spec`
        # needed, and they do not belong in brackets.
        _, err = self._run_with((MISSING,))
        self.assertIn("huebox[editor]", err)
        self.assertNotIn("huebox[%s]" % MISSING, err)

    def test_several_missing_modules_still_name_the_one_group(self):
        _, err = self._run_with(("nope_one_huebox", "nope_two_huebox"))
        self.assertIn("huebox[editor]", err)
        self.assertNotIn("nope_one_huebox", err)
        self.assertNotIn("nope_two_huebox", err)

    def test_the_named_extra_exists_in_pyproject(self):
        # The read-off-don't-name rule, applied to the message itself: every
        # `huebox[…]` the error offers must be a real key under
        # `[project.optional-dependencies]`, or the line it prints is one no
        # install command can honour. This is the test that would have caught
        # `huebox[textual]`.
        import re

        _, err = self._run_with((MISSING,))
        offered = re.findall(r"huebox\[([A-Za-z0-9_-]+)\]", err)
        self.assertTrue(offered, "the message offers no installable extra")
        with open(os.path.join(os.path.dirname(_HERE), "pyproject.toml"),
                  encoding="utf-8") as handle:
            project = handle.read()
        section = project.split("[project.optional-dependencies]", 1)[1]
        for extra in offered:
            self.assertRegex(section, r"(?m)^%s\s*=" % re.escape(extra),
                             "'%s' is not an extra in pyproject.toml" % extra)

    def test_the_message_offers_the_command_that_needs_no_extra(self):
        _, err = self._run_with((MISSING,))
        self.assertIn("huebox show", err)

    def test_an_installed_requirement_is_silent(self):
        # The shape the real check takes for textual: importable means the guard
        # stays out of the way, so a normal install never sees the message.
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
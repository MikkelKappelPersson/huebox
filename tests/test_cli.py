"""CLI tests: `python -m huebox` end to end as a subprocess."""

import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

from test_formats import GHOSTTY  # noqa: E402

REPO = os.path.dirname(_HERE)


class Cli(unittest.TestCase):
    def _run(self, *args):
        # an isolated config home: without it the subprocess reads the
        # real ~/.config/ghostty and the real theme library, so these
        # tests pass or fail depending on the machine they run on
        with tempfile.TemporaryDirectory() as home:
            env = {key: value for key, value in os.environ.items()
                   if not any(mark in key for mark in
                              ("GHOSTTY", "KITTY", "ALACRITTY", "WEZTERM",
                               "TERM_PROGRAM"))}
            env["HOME"] = home
            env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
            os.makedirs(env["XDG_CONFIG_HOME"], exist_ok=True)
            return subprocess.run([sys.executable, "-m", "huebox", *args],
                                  capture_output=True, text=True, cwd=REPO,
                                  env=env)

    def test_formats_listing(self):
        out = self._run("--formats")
        self.assertEqual(out.returncode, 0)
        self.assertIn("ghostty", out.stdout)

    def test_dump_from_explicit_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ghostty",
                                         delete=False) as handle:
            handle.write(GHOSTTY)
            path = handle.name
        out = self._run("--format", "ghostty", "--config", path, "--dump")
        os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("background=#0f0f1a", out.stdout)

    def test_missing_config_exits_nonzero(self):
        out = self._run("--format", "ghostty",
                        "--config", "/nonexistent/file")
        self.assertEqual(out.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

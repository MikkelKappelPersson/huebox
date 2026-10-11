"""pty closure harness for the Rust binary (004 Phase 6, spec section 7).

Drives ``rust/target/release/huebox`` (built via
``cargo build --release --manifest-path rust/Cargo.toml``) through the
parity session fixtures: same slots, same sizes, same bytes modulo
compositor. Keys go in over a pty, screens come back out through `pyte`,
and the harness asserts the same final slots/selection/status as
``tests/fixtures/parity_session.json`` plus the same pushed bytes as
Python for the save paths.

"Same bytes modulo compositor" is a semantic comparison, not a byte one:
Textual and Ratatui emit different escape sequences for identical cells
by design (alternate-screen enter, cursor addressing, diff granularity),
so comparing raw bytes would pin the framework rather than the theme.
What must agree is what the user can see and what the disk holds: the
theme file colours after each save, the selected slot and status on the
settled screen, and the terminal-config bytes a push writes.

Method notes (established by running, as in `candidate.py`):

* The session runs under an isolated `HOME`/`XDG_CONFIG_HOME` with the
  fixture's `initial_slots` seeded as theme `parity` (via `themes`, so
  the seed is the canonical writer's own layout) and `current = parity`.
  Editor runs use `--no-push`: push bytes are covered separately and
  deterministically by `TestPushBytes`, and `--no-push` keeps the replay
  hermetic on machines that own real terminal configs.
* Full-trace replay runs at 100x24 only — the fixture's own size. Its
  `down`/`up` pair walks the 8-wide palette ladder, and narrower sizes
  lay the palette out differently, so the same arrows select different
  slots there. The no-arrow prefix (keys up to the first save) replays
  at 100x24 and 80x24, proving size independence where the trace allows
  it.
* Prompts (`h`, `X`) suspend the compositor: the app leaves the
  alternate screen, prints the label on the main screen, and reads the
  line cooked. The harness waits for `new hex for` past its current
  offset before answering, so a slow frame can never eat the response.
  Main-screen interludes (`1049l`..`1049h`) are stripped before the
  `pyte` replay — `pyte` has no alternate buffer, and feeding it the
  prompt echo would paint the label over the settled frame.
* `HUEBOX_DEBUG` stays off during screen reads: the seam writes to the
  same pty a real terminal would draw over, so leaving it on garbles
  the grid under comparison. The bench enables it deliberately and
  never reads screens.
* Status comparison normalises the mode suffix: the oracle's headless
  writer returns bare `"saved"`, while both real CLIs report the mode
  (`saved parity (truth only)` here). The harness asserts the status
  names the save; buffer cleanliness is proven functionally — one `esc`
  quits with exit 0, which a dirty buffer refuses.
* The `f` multiplier cycle leaves no direct screen trace (the footer
  carries keys, not the step), but it steered every `e` after it: slots
  matching to the last colour transitively proves the cycle replayed.

Skips (only what genuinely needs a tty this machine lacks):

* `pyte` missing: screens cannot be read; install with `.[test]`.
* release binary missing: build it with the cargo command above.
* `pty.openpty` failing (no `/dev/ptmx`, e.g. a locked-down container):
  nothing here can run without a pty, so the file skips instead of
  failing green-meaningless comparisons against itself.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pty
import re
import select
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)  # repo root: `import huebox`
sys.path.insert(0, _HERE)  # tests dir: `import harness`

import harness  # noqa: E402
from huebox import themes  # noqa: E402
from huebox.color import SLOTS  # noqa: E402
from huebox.formats import FORMATS  # noqa: E402

try:
    import pyte  # noqa: E402
except ImportError:
    pyte = None

BIN = os.path.join(_ROOT, "rust", "target", "release", "huebox")
FIXTURE = os.path.join(_HERE, "fixtures", "parity_session.json")

#: Editor key name to the bytes a terminal sends (`translate` inverts
#: these back; any name missing here is a plain character answering
#: itself, the same rule the Rust table keeps).
KEY_BYTES = {
    "down": b"\x1b[B",
    "up": b"\x1b[A",
    "left": b"\x1b[D",
    "right": b"\x1b[C",
    "esc": b"\x1b",
}

#: The prompt label both shells print, the one string the harness waits
#: for before answering a suspended prompt.
PROMPT_MARK = b"new hex for"

#: Main-screen interludes a suspended prompt leaves behind: leave-alternate
#: to the next enter-alternate. Stripped before the `pyte` replay.
INTERLUDE = re.compile(rb"\x1b\[\?1049l.*?\x1b\[\?1049h", re.S)

#: One `stages` timing line from the `HUEBOX_DEBUG_LOG` seam.
STAGES = re.compile(
    r"stages key=([\d.]+)ms frame=([\d.]+)ms draw=([\d.]+)ms")

#: Worst-case redraw budget, spec section 6: 60fps, both sizes.
BUDGET_MS = 16.6


def needs_pty(test):
    """Skip the test when this machine cannot offer a pty at all."""
    def wrapper(self):
        try:
            master, slave = pty.openpty()
        except OSError as error:
            self.skipTest(f"no pty on this machine: {error}")
            return
        os.close(master)
        os.close(slave)
        return test(self)
    wrapper.__name__ = test.__name__
    return wrapper


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


class IsolatedHome:
    """A `HOME` + `XDG_CONFIG_HOME` holding the parity theme, then gone.

    `themes.*` reads the environment at call time, so the swap is scoped
    to seeding; the child gets the paths as explicit variables and the
    parent's environment is restored whatever happens.
    """

    def __init__(self, initial_slots):
        self.saved = (os.environ.get("HOME"), os.environ.get("XDG_CONFIG_HOME"))
        self.tmp = tempfile.mkdtemp(prefix="hb-pty-rust-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.config_home = os.path.join(self.home, ".config")
        os.makedirs(self.config_home, exist_ok=True)
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = self.config_home
        try:
            themes.create("parity", initial_slots)
            themes.set_current("parity")
        finally:
            self.restore()

    def restore(self):
        home, xdg = self.saved
        if home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = home
        if xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = xdg

    def theme_file(self):
        return os.path.join(self.config_home, "huebox", "themes", "parity.toml")

    def save_theme(self, name, slots):
        """Write truth through `themes` under the isolated home."""
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = self.config_home
        try:
            themes.save(name, slots)
        finally:
            self.restore()

    def child_env(self, debug_log=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("TEXTUAL") and key != "NO_COLOR"}
        env.update(harness.color_depth_env("truecolor"))
        env.update({"HOME": self.home, "XDG_CONFIG_HOME": self.config_home})
        env.pop("HUEBOX_DEBUG", None)
        env.pop("HUEBOX_DEBUG_LOG", None)
        if debug_log is not None:
            env["HUEBOX_DEBUG"] = "1"
            env["HUEBOX_DEBUG_LOG"] = debug_log
        return env

    def close(self):
        self.restore()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def theme_colors(path):
    """The `[colors]` of a theme file, slot-filtered (meta keys excluded)."""
    got = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            match = re.match(r'([A-Za-z0-9-]+)\s*=\s*"(.*)"', line.strip())
            if match and match.group(1) in SLOTS:
                got[match.group(1)] = match.group(2)
    return got


class PtyEditor:
    """The release binary's editor on a pty of the given size."""

    def __init__(self, home, cols, rows, argv, debug_log=None):
        self.home = home
        self.cols = cols
        self.rows = rows
        self.output = bytearray()
        master, slave = pty.openpty()
        with contextlib.suppress(OSError):
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        self.process = subprocess.Popen(
            argv, stdin=slave, stdout=slave, stderr=slave,
            env=home.child_env(debug_log), close_fds=True)
        os.close(slave)
        self.master = master

    def drain(self, timeout):
        """Read until `timeout` seconds quiet; False on EOF."""
        quiet_until = time.time() + timeout
        while time.time() < quiet_until:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    return False
                if not chunk:
                    return False
                self.output.extend(chunk)
                quiet_until = time.time() + timeout
        return True

    def feed(self, key):
        self.output_at = len(self.output)
        os.write(self.master, KEY_BYTES.get(key, key.encode("utf-8")))

    def answer_prompt(self, response, timeout=10.0):
        """Wait for the suspended prompt past the last feed, then answer."""
        mark = getattr(self, "output_at", 0)
        deadline = time.time() + timeout
        while bytes(self.output).find(PROMPT_MARK, mark) < 0:
            if time.time() > deadline:
                return False
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    return False
                if not chunk:
                    return False
                self.output.extend(chunk)
        time.sleep(0.3)
        self.drain(0.3)
        os.write(self.master, (response + "\n").encode("utf-8"))
        self.drain(0.8)
        return True

    def screen_text(self):
        """The settled editor screen: alt-stream only, replayed by `pyte`."""
        alt = INTERLUDE.sub(b"", bytes(self.output))
        screen = pyte.Screen(self.cols, self.rows)
        pyte.ByteStream(screen).feed(alt)
        lines = ["".join(screen.buffer[y][x].data for x in range(self.cols)).rstrip()
                 for y in range(self.rows)]
        return "\n".join(lines), lines

    def quit(self):
        """One `esc`: True when the session was clean enough to leave."""
        os.write(self.master, b"\x1b")
        self.drain(1.5)
        return self.process.poll() == 0

    def close(self):
        with contextlib.suppress(ProcessLookupError, OSError):
            self.process.send_signal(signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=5)
        if self.process.poll() is None:  # pragma: no cover
            self.process.kill()
            self.process.wait(timeout=5)
        with contextlib.suppress(OSError):
            os.close(self.master)
        return self.process.returncode


@unittest.skipUnless(pyte is not None, "pyte missing: pip install -e '.[test]'")
@unittest.skipUnless(os.path.isfile(BIN),
                     "release binary missing: "
                     "cargo build --release --manifest-path rust/Cargo.toml")
class TestParityReplay(unittest.TestCase):
    """The parity session, replayed key-for-key against the Rust binary."""

    def _run(self, keys, responses, cols, rows):
        data = load_fixture()
        home = IsolatedHome(data["initial_slots"])
        self.addCleanup(home.close)
        editor = PtyEditor(home, cols, rows,
                           [BIN, "edit", "parity", "--no-push"])
        self.addCleanup(editor.close)
        self.assertTrue(editor.drain(2.0), "binary produced no output")
        answers = list(responses)
        for key in keys:
            editor.feed(key)
            if key in ("h", "X"):
                self.assertTrue(answers, f"no prompt response left for {key!r}")
                self.assertTrue(editor.answer_prompt(answers.pop(0)),
                                f"suspended prompt never arrived after {key!r}")
            else:
                editor.drain(0.25)
        editor.drain(1.0)
        return data, home, editor

    @needs_pty
    def test_full_session_matches_final_state_100x24(self):
        data, home, editor = self._run_full(100, 24)
        final = data["final"]
        self.assertEqual(theme_colors(home.theme_file()), final["slots"],
                         "theme file after the full session is not the "
                         "fixture's final slots")
        self.assertEqual(theme_colors(home.theme_file()),
                         data["writes"][1]["slots"],
                         "theme file after the full session is not the "
                         "second save's bytes")
        text, _ = editor.screen_text()
        self.assertIn("saved", text,
                      "settled screen does not name the save")
        self.assertIn("palette-5", text,
                      "settled screen does not name the selected slot")
        self.assertIn("> 5", text,
                      "settled screen does not mark the selection")
        self.assertTrue(editor.quit(),
                        "one esc did not quit: the buffer is still dirty, "
                        "so the last save did not land")

    def _run_full(self, cols, rows):
        data = load_fixture()
        return self._run(data["keys"], data["prompt_responses"], cols, rows)

    @needs_pty
    def test_first_save_matches_at_100x24(self):
        data = load_fixture()
        _, home, editor = self._run(data["keys"][:18], [], 100, 24)
        self.assertEqual(theme_colors(home.theme_file()),
                         data["writes"][0]["slots"],
                         "theme file after the first save is not the "
                         "fixture's first write")
        text, _ = editor.screen_text()
        self.assertIn("saved", text)
        self.assertIn("> 5", text)
        self.assertTrue(editor.quit(),
                        "one esc did not quit after the first save")

    @needs_pty
    def test_first_save_matches_at_80x24(self):
        # The prefix holds no arrows that leave the slot (its down/up
        # pair round-trips on any palette ladder), so the save bytes must
        # hold at a second size too.
        data = load_fixture()
        _, home, editor = self._run(data["keys"][:18], [], 80, 24)
        self.assertEqual(theme_colors(home.theme_file()),
                         data["writes"][0]["slots"],
                         "first-save bytes moved with the terminal size")
        text, _ = editor.screen_text()
        self.assertIn("saved", text)
        self.assertIn("> 5", text)
        self.assertTrue(editor.quit(),
                        "one esc did not quit after the first save")


def _seed_config(fmt, path, slots):
    if fmt == "kitty":
        lines = [f"background {slots['background']}",
                 f"foreground {slots['foreground']}",
                 f"cursor {slots['cursor-color']}",
                 f"selection_background {slots['selection-background']}",
                 f"selection_foreground {slots['selection-foreground']}"]
        lines += [f"color{i} {slots[f'palette-{i}']}" for i in range(16)]
        body = "# seeded for the pty harness\n" + "\n".join(lines) + "\n"
    elif fmt == "ghostty":
        lines = [f"background = {slots['background']}",
                 f"foreground = {slots['foreground']}",
                 f"cursor-color = {slots['cursor-color']}",
                 f"cursor-text = {slots['cursor-text']}",
                 f"selection-background = {slots['selection-background']}",
                 f"selection-foreground = {slots['selection-foreground']}"]
        lines += [f"palette = {i}={slots[f'palette-{i}']}" for i in range(16)]
        body = "# seeded for the pty harness\n" + "\n".join(lines) + "\n"
    else:
        lines = [f"colors.primary.background = \"{slots['background']}\"",
                 f"colors.primary.foreground = \"{slots['foreground']}\"",
                 f"colors.cursor.cursor = \"{slots['cursor-color']}\"",
                 f"colors.cursor.text = \"{slots['cursor-text']}\"",
                 "colors.selection.background = "
                 f"\"{slots['selection-background']}\"",
                 "colors.selection.foreground = "
                 f"\"{slots['selection-foreground']}\""]
        lines += [f"colors.normal.{i} = \"{slots[f'palette-{i}']}\""
                  for i in range(16)]
        body = "# seeded for the pty harness\n" + "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return body


@unittest.skipUnless(os.path.isfile(BIN),
                     "release binary missing: "
                     "cargo build --release --manifest-path rust/Cargo.toml")
class TestPushBytes(unittest.TestCase):
    """`use --to <fmt>` writes byte-identical configs to the Python writer."""

    CONFIGS = {"kitty": "kitty/kitty.conf",
               "ghostty": "ghostty/config",
               "alacritty": "alacritty/alacritty.toml"}

    def test_push_matches_python_per_format(self):
        data = load_fixture()
        for fmt in ("kitty", "ghostty", "alacritty"):
            with self.subTest(fmt=fmt):
                home = IsolatedHome(data["initial_slots"])
                try:
                    path = os.path.join(home.config_home,
                                        self.CONFIGS[fmt])
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    seed = _seed_config(fmt, path, data["initial_slots"])
                    # The Python no-op write holds the seed byte-for-byte,
                    # so both writers start from the same file.
                    FORMATS[fmt]["write"](path, data["initial_slots"])
                    self.assertEqual(_read(path), seed,
                                     f"Python no-op write moved {fmt} bytes")
                    FORMATS[fmt]["write"](path, data["final"]["slots"])
                    expected = _read(path)
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(seed)
                    # Truth holds the final buffer: `use` pushes the file.
                    home.save_theme("parity", data["final"]["slots"])
                    env = home.child_env()
                    result = subprocess.run(
                        [BIN, "use", "parity", "--to", fmt, "--no-reload"],
                        env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0,
                                     f"rust use --to {fmt} failed: "
                                     f"{result.stderr.strip()[:200]}")
                    self.assertEqual(_read(path),
                                     expected,
                                     f"rust {fmt} push bytes differ from "
                                     "the Python writer")
                finally:
                    home.close()


@unittest.skipUnless(os.path.isfile(BIN),
                     "release binary missing: "
                     "cargo build --release --manifest-path rust/Cargo.toml")
class TestLiveBench(unittest.TestCase):
    """Spec section 6 gate: worst-case redraw p50 under 16.6ms, both sizes.

    The section 1 method: background selected (every row carries its
    fill, so each frame rewrites the whole grid), an `e` flood at
    10ms cadence (key repeat arrives every ~33ms; faster than repeat
    keeps every redraw back-pressured without coalescing the burst
    into one frame), the real compositor on a pty, timings from the
    file-logged `stages` seam. A reader thread drains the pty throughout:
    without it the output buffer fills and `draw` blocks on the write,
    which times the harness instead of the app.
    """

    NAV = [b"\x1b[D"] * 5 + [b"\x1b[B"] * 2  # 5 -> 0, 0 -> 8, 8 -> 16

    def _bench(self, cols, rows, nkeys=60, cadence=0.01):
        data = load_fixture()
        home = IsolatedHome(data["initial_slots"])
        self.addCleanup(home.close)
        debug_log = os.path.join(home.tmp, "stages.log")
        master, slave = pty.openpty()
        with contextlib.suppress(OSError):
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        process = subprocess.Popen(
            [BIN, "edit", "parity", "--no-push"],
            stdin=slave, stdout=slave, stderr=slave,
            env=home.child_env(debug_log), close_fds=True)
        os.close(slave)
        self.addCleanup(lambda: self._reap(process, master))
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                ready, _, _ = select.select([master], [], [], 0.05)
                if ready:
                    try:
                        os.read(master, 65536)
                    except OSError:
                        break

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            time.sleep(2.0)
            for key in self.NAV:
                os.write(master, key)
                time.sleep(0.25)
            time.sleep(0.5)
            with open(debug_log, encoding="utf-8") as handle:
                sels = re.findall(r"sel=(\d+)", handle.read())
            self.assertEqual(sels[-1] if sels else None, "16",
                             f"background not selected at {cols}x{rows}: "
                             f"nav walked {sels[-8:] if sels else sels}")
            with open(debug_log, "w", encoding="utf-8"):
                pass
            for _ in range(nkeys):
                os.write(master, b"e")
                time.sleep(cadence)
            time.sleep(2.5)
            os.write(master, b"\x1b")
            time.sleep(0.4)
            os.write(master, b"\x1b")
            time.sleep(1.0)
        finally:
            stop.set()
            thread.join(timeout=5)
        with open(debug_log, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        totals, keys, frames, draws = [], [], [], []
        for line in lines:
            match = STAGES.search(line)
            if match:
                key, frame, draw = (float(match.group(1)),
                                    float(match.group(2)),
                                    float(match.group(3)))
                keys.append(key)
                frames.append(frame)
                draws.append(draw)
                totals.append(key + frame + draw)
        return totals, keys, frames, draws

    @staticmethod
    def _reap(process, master):
        with contextlib.suppress(ProcessLookupError, OSError):
            process.send_signal(signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        if process.poll() is None:  # pragma: no cover
            process.kill()
            process.wait(timeout=5)
        with contextlib.suppress(OSError):
            os.close(master)

    def _gate(self, cols, rows):
        totals, keys, frames, draws = self._bench(cols, rows)
        self.assertGreaterEqual(len(totals), 20,
                                f"only {len(totals)} redraws at {cols}x{rows}: "
                                "the flood coalesced, the bench is broken")
        ordered = sorted(totals)
        count = len(ordered)
        p50 = ordered[count // 2]
        worst = ordered[-1]
        mean = statistics.mean(ordered)
        print(f"livebench {cols}x{rows}: n={count} p50={p50:.3f}ms "
              f"max={worst:.3f}ms mean={mean:.3f}ms "
              f"(key {statistics.mean(keys):.3f} / "
              f"frame {statistics.mean(frames):.3f} / "
              f"draw {statistics.mean(draws):.3f})")
        self.assertLess(
            p50, BUDGET_MS,
            f"worst-case redraw p50 {p50:.3f}ms exceeds the {BUDGET_MS}ms "
            f"budget at {cols}x{rows} (max {worst:.3f}, mean {mean:.3f}, "
            f"n={count}; stages mean key {statistics.mean(keys):.3f} / "
            f"frame {statistics.mean(frames):.3f} / "
            f"draw {statistics.mean(draws):.3f})")

    @needs_pty
    def test_worst_case_redraw_100x24(self):
        self._gate(100, 24)

    @needs_pty
    def test_worst_case_redraw_150x50(self):
        self._gate(150, 50)


if __name__ == "__main__":
    unittest.main()

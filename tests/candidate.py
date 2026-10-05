"""Capture the candidate editor's real bytes from a pty (migration phase 2).

`harness.candidate_bytes` returns the *reference's* bytes through phase 1, so I1
was green by construction. This replaces it with the thing I1 is for: the app
under `huebox/app.py`, launched as a subprocess in a real pty, its output read
back as bytes and parsed with `pyte`.

Two things here are not incidental.

**The environment is pinned, exhaustively** (migration spec §6.4). Textual
reads eight environment variables at *import* time and several of them rewrite
cells — `TEXTUAL_COLOR_SYSTEM` picks the colour depth, `NO_COLOR` installs a
monochrome filter, `TEXTUAL_FILTERS` can add a `dim` filter, `TEXTUAL_THEME`
moves every token. None can be corrected inside the running app. Leaving any of
them to the developer's shell would make I1 pass or fail depending on whose
machine ran it.

**The whole stream is replayed, not a window cut out of it.** An earlier
version cut the stream at every cursor-home and parsed the last region, on the
theory that setup chatter and half-drawn repaints had to be excluded. That was
re-implementing a terminal badly: Textual positions with `CSI row;col H`, not
bare `CSI H`, so the split never matched and the entire stream came out as one
region. `pyte` is a real emulator — handed everything the app wrote, it replays
it and lands on the settled screen, which is the whole of what I1 wants. No
regex, no region picking, nothing to keep in step with Textual's output format.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import harness  # noqa: E402

#: Variables that change what Textual puts on screen. Every one is removed from
#: the inherited environment before the app starts; the two that must be *set*
#: are in `color_depth_env`. Read at import time, so the launcher is the only
#: place they can be fixed (migration spec §6.4).
NEUTRALISE = (
    "TEXTUAL",                    # features: devtools, debug, headless
    "TEXTUAL_THEME",              # the base every token derives from
    "TEXTUAL_FILTERS",            # extra line filters; `dim` changes every cell
    "TEXTUAL_ANIMATIONS",
    "TEXTUAL_FPS",
    "TEXTUAL_DRIVER",
    "TEXTUAL_DEBUG",
    "TEXTUAL_DEVTOOLS_PORT",
    "TEXTUAL_DEVTOOLS_HOST",
    "TEXTUAL_LOG",
    "TEXTUAL_SCREENSHOT",
    "TEXTUAL_SCREENSHOT_LOCATION",
    "TEXTUAL_SCREENSHOT_FILENAME",
    "TEXTUAL_PRESS",
    "TEXTUAL_SHOW_RETURN",
    "NO_COLOR",                   # installs a Monochrome() filter
)

#: `COLORTERM`/`TERM` decide how Rich detects depth, and `FORCE_COLOR` forces
#: it. None of them may be inherited.
DETECTION = ("COLORTERM", "TERM", "FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE")

APP = [sys.executable, "-m", "huebox.app"]
SETTLE = 1.0                    # seconds to let the app settle and paint
DRAIN = 0.5                     # seconds more to read whatever is left

#: Captured runs, keyed by everything that can change one. A capture is
#: deterministic — same slots, same size, same pinned environment, same bytes —
#: and the suite asks for the same twelve a dozen times over between I1, I2 and
#: the backdrop check. Without this the suite spends 45 seconds launching
#: ~34 processes to answer ~22 distinct questions.
#:
#: It does mean a flaky app is only seen once per configuration rather than
#: once per assertion, which is the right trade: flakiness is a different test
#: with a different shape, and paying for it in every colour check is how colour
#: checks get deleted.
_CAPTURES: dict = {}


def capture_picker(fixture, cols, rows, picker="long", depth="truecolor",
                   status=None):
    """The candidate's picker bytes, memoised on the same terms as `capture`.

    `HUEBOX_PICKER` names a scenario in `harness.PICKERS`; the app opens it and
    paints the picker frame before its first draw, so the capture is a settled
    screen with the picker up rather than a keypress timed against a race.
    """
    # `HUEBOX_PANELS` and `HUEBOX_COLLAPSIBLE` both change the bytes
    # (borders / collapsible headers): same key without them would hand I1's
    # bare capture to I2's product check, or the reverse.
    panels = os.environ.get("HUEBOX_PANELS", "1")
    collapsible = os.environ.get("HUEBOX_COLLAPSIBLE", "1")
    key = ("picker", fixture, cols, rows, picker, depth, status, panels,
           collapsible)
    if key not in _CAPTURES:
        data, _ = run_in_pty(fixture, cols, rows, 0, depth,
                             picker=picker, status=status)
        _CAPTURES[key] = frame_bytes(data)
    return _CAPTURES[key]


def capture(fixture, cols, rows, sel=0, depth="truecolor"):
    """The candidate's output bytes, or `None` if it painted nothing.

    Memoised per `(fixture, cols, rows, sel, depth)`. Pass `fresh=True` to force
    a new process.
    """
    # `HUEBOX_PANELS` and `HUEBOX_COLLAPSIBLE` both change the bytes
    # (borders / headers): I1 pins bare (`0`) while I2 runs product, so the
    # key must tell them apart.
    panels = os.environ.get("HUEBOX_PANELS", "1")
    collapsible = os.environ.get("HUEBOX_COLLAPSIBLE", "1")
    key = (fixture, cols, rows, sel, depth, panels, collapsible)
    if key not in _CAPTURES:
        data, _ = run_in_pty(fixture, cols, rows, sel, depth)
        _CAPTURES[key] = frame_bytes(data)
    return _CAPTURES[key]


def uncached_capture(fixture, cols, rows, sel=0, depth="truecolor"):
    """One launch, no memo — for proving the launch itself works."""
    data, _ = run_in_pty(fixture, cols, rows, sel, depth)
    return frame_bytes(data)


def candidate_env(fixture, cols, rows, sel=0, depth="truecolor", slots_path=None,
                  picker=None, status=None):
    """The environment the candidate is launched with: pinned, then told.

    `HUEBOX_*` is the launch contract (`huebox/app.py`): the slots to render and
    where the frame says it is editing. The **size does not travel this way** —
    it goes through the pty's window size (`TIOCSWINSZ`), which is what
    `term_size()` and Textual both read, so setting `HUEBOX_COLS` here would be
    a variable nobody reads and a second source of truth for the same number.
    `HUEBOX_MULT` and `HUEBOX_STATUS` carry the reference's own values, from
    `harness`, so the two sides of I1 differ in the compositor and nothing else.
    """
    env = {key: value for key, value in os.environ.items()
           if key not in NEUTRALISE and key not in DETECTION}
    env.update(harness.color_depth_env(depth))
    env["TEXTUAL_COLOR_SYSTEM"] = "truecolor" if depth == "truecolor" else "256"
    env["HUEBOX_SLOTS"] = slots_path
    env["HUEBOX_SEL"] = str(sel)
    # `draw_editor`'s `head` replaces the format label in the header. The
    # reference is captured with `head=None`, so "" pins the candidate to the
    # same argument; the app reads "" as "no head" and an absent variable as
    # "derive it from the session". Both sides of I1 have to be given the same
    # arguments, or the header differs by a word and the diff reads as a colour
    # change rather than as the label it is.
    env["HUEBOX_HEAD"] = ""
    env["HUEBOX_MULT"] = str(harness.REFERENCE_MULT)
    env["HUEBOX_STATUS"] = str(harness.REFERENCE_STATUS)
    if picker is not None:
        env["HUEBOX_PICKER"] = picker
        env["HUEBOX_PICKER_NAMES"] = ",".join(harness.PICKERS[picker]["names"])
        env["HUEBOX_PICKER_INDEX"] = str(harness.PICKERS[picker]["index"])
        env["HUEBOX_PICKER_THEME"] = harness.CURRENT_THEME
    else:
        env.pop("HUEBOX_PICKER", None)
    if status is not None:
        env["HUEBOX_STATUS"] = status
    env["HUEBOX_FMT"] = "ghostty"
    env["HUEBOX_PATH"] = harness.FIXTURES[fixture]["path"]
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return env


def write_slots(fixture, directory):
    """The fixture's slots as a JSON file, which is how the app is handed them.

    Product code takes a buffer; the test fixtures live in `tests/`. The file is
    the seam between the two, and it is the same shape production supplies.
    """
    path = os.path.join(directory, "slots.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(harness.FIXTURES[fixture]["slots"], handle, indent=1)
    return path


def run_in_pty(fixture, cols, rows, sel=0, depth="truecolor", picker=None,
               status=None):
    """Launch the app in a pty of the given size; return everything it wrote.

    A pty rather than a pipe, because the app must believe it is on a terminal:
    `term_size()` reads TIOCGWINSZ, and Textual's driver decides its mode from
    whether stderr is a tty.
    """
    with tempfile.TemporaryDirectory() as directory:
        slots_path = write_slots(fixture, directory)
        env = candidate_env(fixture, cols, rows, sel, depth, slots_path,
                            picker, status)
        env["HOME"] = directory

        master, slave = pty.openpty()
        with contextlib.suppress(OSError):
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        # stderr, not stdout: Textual's Linux driver writes the UI to
        # `sys.__stderr__` (`textual/drivers/linux_driver.py:58`) precisely so
        # that `print()` in an app still reaches stdout without corrupting the
        # display. Capturing stdout here yields an empty stream and a frame of
        # pure background — which reads as "the colours are all wrong" rather
        # than "you read the wrong fd".
        process = subprocess.Popen(APP, stdin=slave, stdout=slave,
                                   stderr=slave, env=env, close_fds=True)
        os.close(slave)

        chunks = []
        deadline = time.time() + SETTLE + DRAIN
        quiet_until = time.time() + SETTLE
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                quiet_until = time.time() + DRAIN
            elif time.time() > quiet_until:
                break

        process.send_signal(signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        if process.poll() is None:                      # pragma: no cover
            process.kill()
            process.wait(timeout=5)
        os.close(master)
        return b"".join(chunks), ""


def frame_bytes(data: bytes):
    """Everything the app wrote, which `pyte` replays into the settled screen.

    `None` when the app produced no output at all — a crash, or a wrong fd. That
    is a failure to report rather than an empty frame to compare against,
    because an empty frame would otherwise read as "every colour is wrong".
    """
    return data or None

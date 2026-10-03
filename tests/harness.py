"""Phase 0 of the Textual migration: the colour-equivalence harness.

`docs/001-spec/textual-migration.md` §4 makes one promise — that moving the
frame onto Textual's compositor changes no cell's colour — and makes it
testable in two halves:

* **I1, equivalence.** The reference frame and the candidate frame are parsed
  as a terminal would parse them and compared cell for cell.
* **I2, closure.** Every colour in the frame is one the reference painted, and
  no cell inside the frame shows the terminal's background (§8.2).

Three things in here are load-bearing and were established by running them, not
by reading the source:

**The frame region is declared, not inferred.** `pyte`'s `Screen.buffer` is a
sparse `defaultdict`, and huebox's `\\033[2J` registers erased rows as
present-and-default. At 120x50 that puts rows 39-49 in the buffer, which a
"rows present" derivation mistakes for frame. Deriving it from the painted
extent instead would be circular — I2 checking its own premise. So the height
comes from `frame_rows()`, which encodes the layout rule measured across six
sizes, and `test_equivalence` asserts that rule still holds.

**Candidate is a seam, not a fact.** Today it returns the reference's own bytes,
so I1 is trivially green in phase 0. That is deliberate: phase 0 ends with a
green test that has never seen Textual, which is what makes the green tests in
later phases mean something. Phase 2 replaces this one function with a pty
capture of the migrated app and nothing else in this file changes.

**The closure is captured, never hand-listed.** The frame paints more than the
22 slots: Pygments' default style on the code sample, and the §8.3 bar sweeps
at any size where they fit (31 extra colours at 100x40). A hand-written list
would have been wrong at some size, so the reference records what it painted
and I2 holds the candidate to exactly that.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys

try:  # the test extra; `discover` must stay green without it
    import pyte
except ImportError:  # pragma: no cover - exercised by absence, not coverage
    pyte = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:                      # runnable as a script too
    sys.path.insert(0, _ROOT)                 #   `python3 tests/harness.py`
GOLDEN_ROOT = os.path.join(_HERE, "golden")

#: §10's four sizes. 100x30 is the one where the §8.3 bars fit, so it is the
#: fixture that keeps I2 honest; 80x24 is where they do not.
SIZES = ((100, 30), (80, 24), (60, 16), (40, 12))

#: The frame stops growing at this many rows. Measured, not assumed: the
#: painted height is `min(rows - 1, FRAME_ROWS_CAP)` at every size tried.
FRAME_ROWS_CAP = 39

DEFAULT = "default"          # pyte's sentinel for "the terminal's own colour"


def frame_rows(rows: int) -> int:
    """Rows of the frame, from the terminal's row count.

    Verified against the current renderer at 100x30, 80x24, 60x16, 40x12,
    100x40 and 120x50: painted rows are `min(rows - 1, 39)` every time. The
    terminal keeps the rest — §8.2 says the area outside the frame is the
    terminal's own and not the frame's to paint.
    """
    return max(0, min(rows - 1, FRAME_ROWS_CAP))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _slots(palette, **named):
    from huebox.color import MISSING, SLOTS
    out = {name: MISSING for name in SLOTS}
    out.update(palette)
    out.update(named)
    return out


#: Twelve distinct hues plus the six named slots, so no two slots collide and
#: a swapped pair is visible as a changed cell.
_DISTINCT_PALETTE = {
    "palette-0": "#1e1e2e", "palette-1": "#cdd6f4", "palette-2": "#f38ba8",
    "palette-3": "#a6e3a1", "palette-4": "#f9e2af", "palette-5": "#89b4fa",
    "palette-6": "#f5c2e7", "palette-7": "#94e2d5", "palette-8": "#eba0ac",
    "palette-9": "#7dc4e4", "palette-10": "#b4befe", "palette-11": "#cba6f7",
    "palette-12": "#89dceb", "palette-13": "#fab387", "palette-14": "#f5e0dc",
    "palette-15": "#45475a",
    "background": "#1e1e2e", "foreground": "#cdd6f4",
    "cursor-color": "#f9e2af", "cursor-text": "#1e1e2e",
    "selection-background": "#313244", "selection-foreground": "#cdd6f4",
}

#: Near-black on near-black. The worst case for a colour that leaked in from a
#: default: if Textual's own background ever shows through, a theme-slot check
#: will not notice, so this fixture is what makes one visible.
_DARK_PALETTE = dict(_DISTINCT_PALETTE, **{
    "background": "#010101", "foreground": "#030303",
    "selection-background": "#020202", "selection-foreground": "#040404",
    "cursor-color": "#050505", "cursor-text": "#060606",
})

FIXTURES = {
    "distinct": {"slots": _slots(_DISTINCT_PALETTE), "path": "/tmp/huebox.conf"},
    # A config path in brackets: Rich would read `[huebox]` as markup, so this
    # fixture is the markup guard (§6.3) as well as a colour one.
    "dark": {"slots": _slots(_DARK_PALETTE), "path": "/tmp/[huebox]/kitty.conf"},
    # MISSING is #808080, which is also palette-8's fallback — so this fixture
    # cannot distinguish slot identity, and catches a widget that reaches for
    # the wrong slot instead of the right colour.
    "missing": {"slots": _slots({}), "path": "/tmp/huebox.conf"},
}


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def capture_reference(fixture, cols, rows, sel=0):
    """The frame as the editor writes it today, with no framework involved.

    Returns `(raw, emitted_rows)`. `emitted_rows` is how many rows the renderer
    joined, which is not always how many it painted — at 80x24 it emits 24 and
    paints 23. The harness wants the painted count, from `frame_rows`.
    """
    import huebox.editor as editor

    spec = FIXTURES[fixture]
    original = editor.term_size
    editor.term_size = lambda default=(80, 24): (cols, rows)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            editor.draw_editor("ghostty", spec["path"], spec["slots"], sel,
                               [], "", False)
    finally:
        editor.term_size = original

    raw = buffer.getvalue()
    parts = raw.split("\r\n")
    if parts and parts[-1] == "":
        parts.pop()
    return raw.encode("utf-8"), len(parts)


def candidate_bytes(fixture, cols, rows, sel=0):
    """The candidate frame's bytes.

    Phase 0 has no candidate: this is the reference. Phase 2 swaps the body for
    a pty capture of the Textual app run under `color_depth_env` — which is why
    the parameter exists now and is unused, rather than being threaded through
    every test later.
    """
    return capture_reference(fixture, cols, rows, sel)[0]


def color_depth_env(depth):
    """Environment overrides that pin the terminal to a colour depth.

    §4.5. `pyte` normalises `38;5;196` and `38;2;255;0;0` to the same hex, so
    I1 cannot see a widget degrade to the 256-colour palette. Running the
    candidate at both depths and requiring both to match the golden is what
    catches it.
    """
    if depth == "truecolor":
        return {"COLORTERM": "truecolor", "TERM": "xterm-256color"}
    if depth == "256":
        return {"TERM": "xterm-256color", "COLORTERM": ""}
    raise ValueError(f"unknown depth: {depth}")


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def _attrs(cell):
    flags = ""
    for name in ("bold", "italics", "underscore", "strikethrough", "reverse",
                 "blink"):
        if getattr(cell, name):
            flags += name[0]
    return flags


def parse(raw, cols, rows, height=None):
    """Bytes to a cell grid, as a terminal would render it.

    `height` defaults to the declared frame height (§4.1). Only the frame is
    encoded — the terminal's own area outside it is not the frame's to paint and
    not part of the promise.

    Cells are stored flat, one `[char, fg, bg, attrs]` per cell. Run-length
    encoding was tried and **bought nothing**: measured against the real frame
    every cell is its own run, because a frame row is full of SGR 0s (§8.2
    reopens the fill after every one) so no two neighbouring cells ever share
    all four fields. Dropping the indirection is worth more than the runs would
    ever have saved, in the one file that has to be trusted.
    """
    if pyte is None:
        raise RuntimeError("pyte is not installed: pip install -e '.[test]'")
    height = frame_rows(rows) if height is None else height
    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(raw)

    return [[_cell(screen.buffer[y][x]) for x in range(cols)]
            for y in range(height)]


def _cell(cell):
    # `cell.data`, not `str(cell)`: pyte's Char is a NamedTuple, so str() gives
    # its whole repr — which made the goldens 85% padding and made diff() name
    # a repr instead of a character.
    return [cell.data, cell.fg, cell.bg, _attrs(cell)]


def painted_extent(raw, cols, rows):
    """The rows pyte shows carrying a background, as `(first, last, count)`.

    Used only to check that the candidate painted what it declared. I1 and I2
    must never derive the region from this — that would be circular (§4.1).
    """
    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(raw)
    painted = [y for y in range(rows)
               if any(screen.buffer[y][x].bg != DEFAULT for x in range(cols))]
    if not painted:
        return None
    return painted[0], painted[-1], len(painted)


def closure(raw, cols, rows, height=None):
    """Every colour the bytes put on screen, hex-normalised, excluding
    `'default'`. The reference's recorded answer to 'what may be in the frame'."""
    if pyte is None:
        raise RuntimeError("pyte is not installed: pip install -e '.[test]'")
    height = frame_rows(rows) if height is None else height
    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(raw)
    found = set()
    for y in range(height):
        for x in range(cols):
            cell = screen.buffer[y][x]
            if cell.fg != DEFAULT:
                found.add(cell.fg)
            if cell.bg != DEFAULT:
                found.add(cell.bg)
    return sorted(found)


def unbacked(raw, cols, rows, height=None):
    """Cells inside the frame whose background is still the terminal's.

    §8.2 said this in words. Here it is a list of coordinates.
    """
    if pyte is None:
        raise RuntimeError("pyte is not installed: pip install -e '.[test]'")
    height = frame_rows(rows) if height is None else height
    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(raw)
    return [(y, x) for y in range(height) for x in range(cols)
            if screen.buffer[y][x].bg == DEFAULT]


def diff(expected, actual, cols):
    """Where two grids disagree, as readable `(row, col, was, now)`.

    `was` and `now` are `[char, fg, bg, attrs]` or `None` past the end. This is
    the golden diff that makes a frame change reviewable (§4.4), so it has to
    name cells and colours rather than say "not equal".
    """
    out = []
    for y in range(max(len(expected), len(actual))):
        row_e = expected[y] if y < len(expected) else []
        row_a = actual[y] if y < len(actual) else []
        for x in range(cols):
            was = row_e[x] if x < len(row_e) else None
            now = row_a[x] if x < len(row_a) else None
            if was != now:
                out.append((y, x, was, now))
    return out


# --------------------------------------------------------------------------
# goldens
# --------------------------------------------------------------------------

def golden_path(fixture, cols, rows):
    return os.path.join(GOLDEN_ROOT, "%dx%d" % (cols, rows), fixture + ".json")


def record(fixture, cols, rows, sel=0):
    """Capture the reference frame as a golden. Phase 0's one write (§4.4)."""
    raw, emitted = capture_reference(fixture, cols, rows, sel)
    height = frame_rows(rows)
    return {
        "fixture": fixture,
        "cols": cols,
        "rows": rows,
        "sel": sel,
        "frame_rows": height,
        "emitted_rows": emitted,
        "closure": closure(raw, cols, rows, height),
        "cells": parse(raw, cols, rows, height),
    }


def load(fixture, cols, rows, sel=0):
    """The recorded golden, or None. Never regenerates: a missing golden is a
    test failure, not a silent re-record (§4.4)."""
    path = golden_path(fixture, cols, rows)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write(golden):
    path = golden_path(golden["fixture"], golden["cols"], golden["rows"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(golden, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
    return path


def _main(argv):
    if argv[:1] != ["--record"]:
        sys.stderr.write("usage: python3 tests/harness.py --record [fixture]\n")
        return 2
    only = argv[1:2]
    for cols, rows in SIZES:
        for name in sorted(FIXTURES):
            if only and name not in only:
                continue
            golden = record(name, cols, rows)
            path = write(golden)
            print("%-9s %-8s frame_rows=%-3d emitted=%-3d colours=%-3d %s"
                  % ("%dx%d" % (cols, rows), name, golden["frame_rows"],
                     golden["emitted_rows"], len(golden["closure"]),
                     os.path.relpath(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
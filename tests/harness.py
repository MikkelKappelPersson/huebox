"""The colour-closure harness (migration phases 0–5, I1 removed).

`docs/001-spec/textual-migration.md` §4 makes one promise — that moving the
frame onto Textual's compositor introduces no colour the theme did not paint —
and makes it testable:

* **I2, closure.** Every colour in the frame is one the reference painted, and
  no cell inside the frame shows the terminal's background (§8.2).

The reference is captured live from `editor.draw_editor` on every run; nothing
is recorded to disk and nothing is compared cell for cell. The candidate is the
migrated app launched in a pty (`candidate.py`, migration spec §6.4), read back
as the bytes the terminal would have received.

Three things in here are load-bearing and were established by running them, not
by reading the source:

**The frame region is declared, not inferred.** `pyte`'s `Screen.buffer` is a
sparse `defaultdict`, and huebox's `\\033[2J` registers erased rows as
present-and-default. At 120x50 that puts rows 39-49 in the buffer, which a
"rows present" derivation mistakes for frame. Deriving it from the painted
extent instead would be circular — I2 checking its own premise. So the height
comes from the reference's own row count (`emitted_rows`), which is
independent of both grids under comparison, so I2 stays falsifiable. The
harness separately asserts that the candidate's painted extent equals the
declared region, which is what catches a migrated app that paints short.

**Candidate is a seam, not a fact.** `candidate_bytes` launches the app in a pty
with the whole environment pinned and returns what it wrote. The seam is kept
because it is the one place the migration's claim is cashed.

**The closure is captured, never hand-listed.** The frame paints more than the
22 slots: Pygments' default style on the code sample, and the §8.3 bar sweeps
at any size where they fit (31 extra colours at 100x40). A hand-written list
would have been wrong at some size, so the reference reports what it painted
and I2 holds the candidate to exactly that.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys

try:  # the test extra; `discover` must stay green without it
    import pyte
except ImportError:  # pragma: no cover - exercised by absence, not coverage
    pyte = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:                      # runnable as a script too
    sys.path.insert(0, _ROOT)

#: §10's four sizes. 100x30 is the one where the §8.3 bars fit, so it is the
#: fixture that keeps I2 honest; 80x24 is where they do not.
SIZES = ((100, 30), (80, 24), (60, 16), (40, 12))

DEFAULT = "default"          # pyte's sentinel for "the terminal's own colour"

#: The step size both sides are given. The hint line prints `x{mult}`
#: verbatim, so this is a label as much as a value — the reference takes
#: whatever `capture_reference` passes and the candidate takes the same string
#: through `HUEBOX_MULT`. One constant, so the hint row cannot drift apart by a
#: word and be mistaken for a colour difference.
REFERENCE_MULT = 1          # MULT_STEPS[0] — the value a real session starts on
REFERENCE_STATUS = ""
REFERENCE_UNDO: list = []


def emitted_rows(text: str) -> int:
    """Rows the renderer wrote, from its own output.

    **The frame's height is content-dependent, not a function of the terminal
    size.** It is `len(body + extra + tail)` after the layout has decided what
    fits, so it lands on 24 in a 24-row terminal and 15 in a 16-row one. An
    earlier version of this file asserted `min(rows - 1, 39)`; that formula was
    an artefact of the scroll defect (§4.8) and was wrong at four of the six
    sizes measured. Height is therefore read from the reference, never guessed.

    The trailing `""` that a frame ending in a newline leaves behind is dropped,
    which makes the count correct whether or not the newline was written.
    """
    parts = text.split("\r\n")
    if parts and parts[-1] == "":
        parts.pop()
    return len(parts)


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
#: a swapped pair is a visible changed cell.
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


#: The picker's own scenarios (§13.7).
PICKERS = {
    # Three shapes, and the sizes make the difference between them visible:
    # `short` never scrolls anywhere; `edge` fills 80x24 to the row, which is
    # where the trailing-newline bug lived; `long` overflows at all four sizes,
    # so a widget that scrolled and one that never scrolled cannot agree.
    "short": {"names": ["ember", "paper", "dusk"], "index": 1},
    "edge": {"names": ["a%d" % i for i in range(20)], "index": 0},
    "long": {"names": ["t%02d" % i for i in range(34)], "index": 25},
}



# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def capture_reference(fixture, cols, rows, sel=0):
    """The frame as the editor writes it today, with no framework involved.

    Returns `(raw, written_rows)`.

    The arguments are a **direct-mode session**'s, which is what a harness run
    is: `head=None` so the header names the format, and `path=""` because
    `editor.session_path()` returns nothing for a session with no theme (§13.7 —
    `direct:<path>` already carries the path, so printing it twice reads like two
    files). The candidate derives both the same way, so the two sides differ in
    the compositor and nothing else.

    An earlier version pinned `path` to the fixture's config file, which no real
    session ever passes, and the header's path came back as sixteen differing
    cells. Pinning an argument nothing produces is not a stronger test.
    """
    import huebox.editor as editor

    spec = FIXTURES[fixture]
    original = editor.term_size
    editor.term_size = lambda default=(80, 24): (cols, rows)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            editor.draw_editor("ghostty", "", spec["slots"], sel,
                               REFERENCE_UNDO, REFERENCE_STATUS,
                               REFERENCE_MULT)
    finally:
        editor.term_size = original

    raw = buffer.getvalue()
    return raw.encode("utf-8"), emitted_rows(raw)


def capture_reference_picker(fixture, cols, rows, picker="long", status=None):
    """The picker as the direct-mode editor wrote it, no framework involved.

    The two steps below are what `draw_editor`'s overlay branch did and what
    `app.Picker` does now: `theme_lines` for the rows, `backdrop` to fill each
    one out to the last column in the buffer's own background (§8.2).

    Returns `(raw, written_rows)`, the same shape as `capture_reference`.
    """
    from huebox.editor import backdrop, theme_lines

    spec = FIXTURES[fixture]
    scene = PICKERS[picker]
    lines = [backdrop(line, spec["slots"], cols)
             for line in theme_lines(scene["names"], scene["index"],
                                     CURRENT_THEME, cols, rows, status,
                                     spec["slots"])]
    # §8.2, and the same rule `draw_editor` follows: the trailing CRLF is
    # withheld when the rows fill the screen, or it scrolls the top row off.
    raw = "\r\n".join(lines) + ("\r\n" if len(lines) < rows else "")
    return raw.encode("utf-8"), emitted_rows(raw)



#: The theme the picker is "looking at". It is in `PICKERS["long"]` at `t20`
#: while the selection sits on `t25`, so the current row and the selected row
#: are *different* rows — the case where a scrolling widget is most likely to
#: conflate "selected" with "current" and quietly load the wrong theme.
CURRENT_THEME = "t20"


def candidate_available() -> bool:
    """Whether the Textual shell can be launched from this interpreter.

    The closure tests skip without it rather than quietly falling back to
    comparing the reference with itself, which would be green and meaningless.
    """
    try:
        import textual  # noqa: F401
    except ImportError:
        return False
    return True


def candidate_bytes(fixture, cols, rows, sel=0, depth="truecolor"):
    """The candidate frame's bytes.

    `huebox/app.py` launched in a pty, with the whole environment pinned
    (`candidate.py`, migration spec §6.4), read back as the bytes the terminal
    would have received.
    """
    import candidate

    data = candidate.capture(fixture, cols, rows, sel, depth)
    if data is None:
        raise RuntimeError(
            "the candidate produced no output: `python -m huebox.app` under a "
            "pty wrote nothing (wrong fd, or it exited before painting)")
    return data


def candidate_picker_bytes(fixture, cols, rows, picker="long",
                           depth="truecolor", status=None):
    """The candidate's picker bytes — the same seam as `candidate_bytes`."""
    import candidate

    data = candidate.capture_picker(fixture, cols, rows, picker, depth,
                                    status)
    if data is None:
        raise RuntimeError(
            "the candidate painted no picker: `python -m huebox.app` under a "
            "pty wrote nothing (wrong fd, or it exited before painting)")
    return data


def color_depth_env(depth):
    """Environment overrides that pin the terminal to a colour depth.

    §4.5. `pyte` normalises `38;5;196` and `38;2;255;0;0` to the same hex, so
    a widget degrading to the 256-colour palette is hard to see. Running the
    candidate at a pinned depth is what keeps that honest.
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


def parse(raw, cols, rows, height):
    """Bytes to a cell grid, as a terminal would render it.

    `height` is the declared frame height — the reference's own row
    count. Only the frame is encoded: the terminal's own area outside it is not
    the frame's to paint and not part of the promise.

    Cells are stored flat, one `[char, fg, bg, attrs]` per cell.
    """
    if pyte is None:
        raise RuntimeError("pyte is not installed: pip install -e '.[test]'")
    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(raw)

    return [[_cell(screen.buffer[y][x]) for x in range(cols)]
            for y in range(height)]


def _cell(cell):
    # `cell.data`, not `str(cell)`: pyte's Char is a NamedTuple, so str() gives
    # its whole repr instead of the character.
    return [cell.data, cell.fg, cell.bg, _attrs(cell)]


def painted_extent(raw, cols, rows):
    """The rows pyte shows carrying a background, as `(first, last, count)`.

    Used only to check that the candidate painted what it declared.
    """
    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(raw)
    painted = [y for y in range(rows)
               if any(screen.buffer[y][x].bg != DEFAULT for x in range(cols))]
    if not painted:
        return None
    return painted[0], painted[-1], len(painted)


def closure(raw, cols, rows, height):
    """Every colour the bytes put on screen, hex-normalised, excluding
    `'default'`. The reference's answer to 'what may be in the frame'."""
    if pyte is None:
        raise RuntimeError("pyte is not installed: pip install -e '.[test]'")
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


def unbacked(raw, cols, rows, height):
    """Cells inside the frame whose background is still the terminal's.

    §8.2 said this in words. Here it is a list of coordinates.
    """
    if pyte is None:
        raise RuntimeError("pyte is not installed: pip install -e '.[test]'")
    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(raw)
    return [(y, x) for y in range(height) for x in range(cols)
            if screen.buffer[y][x].bg == DEFAULT]

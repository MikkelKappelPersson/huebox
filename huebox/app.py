"""The editor's Textual shell (migration phase 2).

§4.3, §14, and `docs/001-spec/textual-migration.md` §5.5. Phase 2 moves the
*compositor* under the frame and changes nothing about the frame: the rows
`draw_editor` has always written are captured and handed to Textual as a
`Strip`, so the SGR huebox emits is the SGR the terminal sees.

**The rows come from `draw_editor` itself, not from a re-implementation.**
That is the point of the phase. A second copy of the frame's construction
would be a second chance to get it wrong, and I1 could then only tell that the
two copies agreed — not that either matched what huebox used to do. Capturing
the real writer makes "the frame is unchanged" true by construction, and I1
measures the compositor underneath rather than a rewrite beside it.

**Slots arrive as a file.** `HUEBOX_SLOTS` names a JSON object of the 22 slots:
the same contract production has, where the editor is handed its buffer, and it
keeps the test fixtures in `tests/` where they belong.

Not stdout: Textual owns the screen. Anything this module says goes to stderr
under `HUEBOX_DEBUG`, so a pty capture sees only the frame.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys

from rich.console import Console
from rich.segment import Segment
from rich.text import Text
from textual.app import App, ComposeResult
from textual.strip import Strip
from textual.widget import Widget

from .color import MISSING, SLOTS, step_hsv
from .editor import MULT_STEPS, draw_editor, grid_geometry, move_slot, too_small_frame
from .render import visible
from .tui import MIN_COLS, MIN_ROWS

#: The Textual design tokens this app binds, so no built-in surface draws in
#: Textual's own colours (migration spec §6.2). §5.1 of that spec's decision
#: list is emphatic that this is all 168 tokens in phase A, not the handful
#: today's CSS happens to name; phase 2 binds the surface the frame actually
#: has, and phase 3 widens it. `$text` is the one that defaults to
#: `ansi_default` — the terminal's own foreground — so it is bound first.
TOKEN_SLOTS = {
    "background": "background",
    "surface": "background",
    "panel": "background",
    "boost": "background",
    "primary": "foreground",
    "secondary": "foreground",
    "accent": "foreground",
    "text": "foreground",
    "text-muted": "palette-8",
    "text-disabled": "palette-8",
    "foreground": "foreground",
    "foreground-muted": "palette-8",
    "scrollbar": "palette-8",
    "scrollbar-hover": "palette-8",
    "scrollbar-active": "foreground",
    "scrollbar-background": "background",
    "scrollbar-background-hover": "background",
    "scrollbar-background-active": "background",
    "scrollbar-corner-color": "background",
}


def _debug(message: str) -> None:
    if os.environ.get("HUEBOX_DEBUG"):
        print(f"huebox.app: {message}", file=sys.stderr, flush=True)


def load_slots() -> dict:
    """The buffer to render, from the JSON file `HUEBOX_SLOTS` names.

    A slot the file omits keeps `MISSING`, the same way a slot a terminal
    config omits does: the frame is honest about what it does not know instead
    of inventing a colour for it.
    """
    path = os.environ.get("HUEBOX_SLOTS")
    given = {}
    if path:
        with open(path, encoding="utf-8") as handle:
            given = json.load(handle)
    return {name: given.get(name, MISSING) for name in SLOTS}


def frame_rows(fmt, path, slots, sel, cols=None, rows=None, undo=None,
               status="", mult=1, head=None, overlay=None):
    """The frame as a list of rows, captured from `draw_editor`.

    Returns the rows *without* the trailing newline decision, which belongs to
    whoever writes them: `draw_editor` keeps it (and withholds it when the frame
    fills the screen, §4.8), Textual positions cells itself.

    `cols`/`rows` are ignored when given — `draw_editor` reads the terminal,
    which under a pty is the size the harness asked for. They are accepted so
    the signature reads like the thing it replaced.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        draw_editor(fmt, path, slots, sel, list(undo or []), status, mult,
                    head=head, overlay=overlay)
    text = buffer.getvalue()
    rows_out = text.split("\r\n")
    if rows_out and rows_out[-1] == "":
        rows_out.pop()
    return rows_out


class Frame(Widget):
    """The whole frame, one row per line `draw_editor` wrote.

    `render_line` is the only thing Textual asks for and it is handed the row's
    screen coordinate. The rows are parsed once here rather than per frame: the
    strings are exactly what `render.py` emitted, so `Text.from_ansi` is a
    re-encoding rather than an interpretation.
    """

    DEFAULT_CSS = """
    Frame { background: $background; color: $foreground; }
    """

    def __init__(self, rows_text, width, **kwargs):
        super().__init__(**kwargs)
        self.text_rows = rows_text
        self.console = Console(file=io.StringIO(), force_terminal=True,
                               color_system="truecolor", legacy_windows=False,
                               markup=False, highlight=False)
        self._cache = [Text.from_ansi(row) for row in rows_text]

    def on_mount(self) -> None:
        self.can_focus = True
        self.focus()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0:
            return Strip([])
        if y >= len(self._cache):
            return Strip([Segment(" " * width)], width)
        segments = list(self._cache[y].render(self.console, end=""))
        filled = sum(segment.cell_length for segment in segments)
        if filled < width:
            # §8.2 — the row reaches the edge in the buffer's own fill, so no
            # column shows the terminal's background
            segments.append(Segment(" " * (width - filled)))
        return Strip(segments, width)


class Editor(App):
    """The frame, under Textual's compositor.

    Phase 2 wires the shell: the arrows walk the grid the frame was drawn for
    (§4.3.1), and a resize re-derives the frame at the new width. The save
    path, the picker and the prompts are phase 3 — they still live in
    `editor.py` behind the injected seams, and I1 does not look at them.
    """

    BINDINGS = [
        ("up", "slot('up')", "up"),
        ("down", "slot('down')", "down"),
        ("left", "slot('left')", "left"),
        ("right", "slot('right')", "right"),
        ("q", "nudge('h', -1)", "hue-"),
        ("w", "nudge('h', 1)", "hue+"),
        ("a", "nudge('s', -1)", "sat-"),
        ("s", "nudge('s', 1)", "sat+"),
        ("z", "nudge('v', -1)", "val-"),
        ("x", "nudge('v', 1)", "val+"),
    ]

    def __init__(self, fmt="ghostty", path="/tmp/huebox.conf", **kwargs):
        # Before `super()`: App.__init__ calls get_css_variables() to build
        # the stylesheet, so the buffer has to exist by then or the first frame
        # is painted against MISSING for every slot.
        self.fmt = fmt
        self.path = path
        self.slots = load_slots()
        self.sel = int(os.environ.get("HUEBOX_SEL", "0"))
        # Passed through verbatim: the hint line renders `x{mult}` literally, so
        # this is a label as much as a step size. Production's default is
        # `MULT_STEPS[0]`; the harness supplies its own value and both sides of
        # I1 must be given the same one, or the hint row differs by a word.
        self.mult = os.environ.get("HUEBOX_MULT", str(MULT_STEPS[0]))
        self.status = os.environ.get("HUEBOX_STATUS", "")
        self.undo = []
        self.head = os.environ.get("HUEBOX_HEAD") or None
        self.rows_text = []
        super().__init__(**kwargs)

    # -- the frame ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Nothing here: the frame's width comes from the size Textual hands us
        # at mount, and a `ScrollView` would bring a border and a scrollbar
        # that the frame has no room for. Scrolling is phase 4, and it is
        # gated on I1 (migration spec §5.6) like everything else that changes
        # what reaches the screen.
        return iter(())

    def get_css_variables(self):
        """Every colour Textual draws with, bound to a theme slot.

        §6.2: 168 tokens exist and this is the subset the shell touches in
        phase 2. Anything unbound falls through to Textual's own palette, and
        `$text` in particular falls through to `ansi_default` — the terminal's
        own foreground — which I2 exists to catch.
        """
        variables = dict(super().get_css_variables())
        for token, slot in TOKEN_SLOTS.items():
            variables[token] = self.slots.get(slot, MISSING)
        return variables

    def redraw(self) -> None:
        """Re-derive the frame at the current size and hand it to the widget."""
        width, height = self.size
        too_small = width < MIN_COLS or height < MIN_ROWS
        if too_small:
            rows_text = [too_small_frame(width)]
        else:
            rows_text = frame_rows(self.fmt, self.path, self.slots, self.sel,
                                   undo=self.undo, status=self.status,
                                   mult=self.mult, head=self.head)
        self.rows_text = rows_text

        for child in list(self.query(Frame)):
            child.remove()
        frame = Frame(rows_text, width)
        frame.styles.width = width
        frame.styles.height = len(rows_text)
        frame.styles.padding = 0
        frame.styles.margin = 0
        self.mount(frame)
        _debug(f"redraw {width}x{height}: {len(rows_text)} rows, "
               f"sel={self.sel}, widest={max((visible(r) for r in rows_text), default=0)}")

    def on_mount(self) -> None:
        self.title = "huebox"
        self.redraw()

    def on_resize(self, event) -> None:
        _debug(f"resize to {event.size}")
        self.redraw()

    # -- input -------------------------------------------------------------

    def action_slot(self, direction: str) -> None:
        """Walk the grid the frame was drawn for (§4.3.1)."""
        width = self.size.width
        if width < MIN_COLS:
            return
        target = move_slot(self.sel, direction, grid_geometry(width))
        if target != self.sel:
            self.sel = target
            self.redraw()

    def action_nudge(self, axis: str, step: int) -> None:
        """Step the selected slot along one axis (§4.3, `ADJUST`).

        Through `color.step_hsv`, the same arithmetic `editor._adjust` uses, so
        a key press moves the colour by the same amount under Textual as it
        always did. The shell had its own 0.01 steps for a moment, which would
        have made `q/w` and `a/s` feel wrong in a way no test would have caught.
        """
        mult = self.mult
        if not isinstance(mult, (int, float)) or isinstance(mult, bool):
            # `HUEBOX_MULT` is a label the hint line prints as well as a step,
            # so the harness may pass something like "False"; fall back to the
            # real default rather than multiplying by nonsense.
            mult = MULT_STEPS[0]
        name = SLOTS[self.sel]
        self.slots[name] = step_hsv(self.slots[name], axis, step, mult)
        self.redraw()


def main() -> int:
    """`python -m huebox.app` — the shell, driven by the environment."""
    fmt = os.environ.get("HUEBOX_FMT", "ghostty")
    path = os.environ.get("HUEBOX_PATH", "/tmp/huebox.conf")
    Editor(fmt=fmt, path=path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

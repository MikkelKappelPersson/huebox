"""The editor: the frame, under Textual's compositor (migration phases 2–3).

§4.3, §13.7, §14, and `docs/001-spec/textual-migration.md` §5.5. Textual owns
the screen; `render.py` still owns the frame. The rows here are the ones
`draw_editor` wrote, captured rather than re-rendered, so "the frame is
unchanged" is true by construction and I1 measures the compositor underneath
rather than a rewrite beside it.

**The key surface is not reimplemented.** `apply_key` and `EditorState` already
are the whole of huebox's editing behaviour — selection, adjust, undo, revert,
step size, the picker overlay, the save, the two-armed Esc. This module owns
only what a compositor needs to own: getting a key in, redrawing, and taking the
terminal back for the two prompts. So there is one implementation of "what `u`
does" and it is the one the 400-odd existing tests cover.

**Three Textual details this exists to get right.**

*Key names.* Textual says `escape`, `ctrl+c`, `ctrl+s`; `apply_key` was written
against huebox's own reader and says `esc`, `\x03`, `\x13`. `translate` is the
whole of the difference, and it is the only place the two vocabularies meet.

*The prompts.* `apply_key` calls `st.prompt_hex(label)` synchronously, so the
answer has to be available when it returns. `App.suspend()` hands the terminal
back exactly as it was before the app started — cooked mode, no alternate
screen — which is precisely the drop-out-of-raw-mode pattern `edit()` used, and
keeps the seam synchronous instead of turning the whole key surface async.

*Nothing is bound.* Textual's own bindings would swallow keys `apply_key` wants.
`BINDINGS` is empty and every key arrives at `on_key`; the command palette is
off for the same reason.

Not stdout: Textual writes the UI to `sys.__stderr__` on purpose, so a pty
capture sees the frame and nothing else. Diagnostics go to stderr under
`HUEBOX_DEBUG`.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from functools import partial

from rich.console import Console
from rich.segment import Segment
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.strip import Strip
from textual.style import Style
from textual.widget import Widget
from textual.widgets import Collapsible

from .color import MISSING, SLOTS
from .editor import (MULT_STEPS, SIDE_LEFT_ROWS, SIDE_LEFT_W, EditorState,
                     apply_key, backdrop, draw_editor, grid_geometry,
                     head_label, report_session, session_path, side_grid,
                     side_left_rows, side_live_rows, slot_at, theme_lines,
                     too_small_frame)
from .render import MIN_COLS, MIN_ROWS, visible

#: Textual's key vocabulary → huebox's. The only seam between them.
#: The blocks a click and an arrow both act on — the two grids. They are one
#: logical control split across two rows of the frame, which is why focus moves
#: between them rather than sitting in one.
GRID_BLOCKS = ("palette", "interface")

#: The keys the grid binds for itself. `Editor.on_key` steps over them when a
#: grid has focus, because Textual does not guarantee the two run in one order:
#: the App's key handler and the focused widget's binding both see every key,
#: and with both acting on an arrow the selection moved *two* slots for one
#: press. One handler per key is the invariant, and
#: `test_an_arrow_moves_one_slot_when_the_grid_has_focus` is the guard.
GRID_KEYS = ("left", "right", "up", "down")


def _mult_step(raw) -> int:
    """`HUEBOX_MULT` as one of `MULT_STEPS`, whatever shape it arrives in.

    `str(MULT_STEPS[0])` used to be the default here, and that is a string:
    `step_hsv` multiplies the step by it, so `q`, `w`, `a`, `s`, `z` and `x`
    all died with `can't multiply sequence by non-int of type float`, and `f`
    died on `MULT_STEPS.index("1")`. Every one of those keys is the editor's
    main verb.

    The harness did not catch it because `REFERENCE_MULT` was `False`, so
    `HUEBOX_MULT` was the string `"False"` — a *different* wrong type that
    rendered as `f xFalse` in the golden and `f xFalse` in the candidate, and
    the two wrongs matched. §6.2's lesson in a new place: pinning an argument
    no real session passes buys an I1 that cannot fail.
    """
    if raw is not None:
        for step in MULT_STEPS:
            if raw == str(step):
                return step
    return MULT_STEPS[0]


KEYS = {
    "escape": "esc",
    "ctrl+c": "\x03",
    "ctrl+s": "\x13",
    "ctrl+d": "\x04",
}

#: The Textual design tokens this shell binds, so no built-in surface draws in
#: Textual's own colours (migration spec §6.2). 168 tokens exist; this is the
#: subset the shell touches, and phase A's decomposition widens it. `$text` is
#: the one that matters most — it is generated as `ansi_default`, the terminal's
#: own foreground, so anything falling through to it paints a colour that is not
#: the theme's. I2 is what proves none did.
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
    # Panels: borders are compositor chrome, so they need theme-closed
    # tokens like everything else. Muted `palette-8`, the same slot the
    # frame's own furniture wears (§8.1).
    "border": "palette-8",
    "border-blurred": "palette-8",
    # §6.2 — the text selection. `Selectable` blocks (the code sample and the
    # diff) are selectable, and Textual composites the selection on the screen
    # in a `.screen--selection` overlay painted with these two. They are the
    # theme's own selection slots, which is the same pair the picker marks a
    # theme with, so a selection reads as part of the theme rather than as a
    # blue someone else's palette brought with it. `input-selection-*` is bound
    # beside them for the same reason: nothing uses an input today, and the day
    # a prompt is a real widget rather than a suspended terminal this is where
    # its selection colour will come from.
    "screen-selection-background": "selection-background",
    "screen-selection-foreground": "selection-foreground",
    "input-selection-background": "selection-background",
    "input-selection-foreground": "selection-foreground",
}


def translate(key: str) -> str:
    """Textual's key name → the one `apply_key` expects."""
    return KEYS.get(key, key)


def _debug(message: str) -> None:
    if os.environ.get("HUEBOX_DEBUG"):
        import sys
        print(f"huebox.app: {message}", file=sys.stderr, flush=True)


def load_slots() -> dict:
    """The buffer to render, from the JSON file `HUEBOX_SLOTS` names.

    A slot the file omits keeps `MISSING`, the same way a slot a terminal config
    omits does: the frame is honest about what it does not know instead of
    inventing a colour for it. Production passes the buffer in rather than a
    path — this is the harness's way in, and the one shape both understand.
    """
    path = os.environ.get("HUEBOX_SLOTS")
    given = {}
    if path:
        with open(path, encoding="utf-8") as handle:
            given = json.load(handle)
    return {name: given.get(name, MISSING) for name in SLOTS}


def frame_rows(fmt, path, state, cols, rows, head=None, hits=None,
               regions=None):
    """The frame as a list of rows, captured from `draw_editor`.

    Returns the rows without the trailing-newline decision, which belongs to
    whoever writes them: `draw_editor` keeps that (and withholds the newline
    when the frame fills the screen, §4.8), Textual positions cells itself.

    `rows` is passed to `draw_editor` as its `size`, and that is not tidiness.
    The frame's layout is a function of both dimensions — the row count decides
    which widgets get rows at all — and the compositor's size is the only one
    that is right. Asking the terminal as well means two numbers for the same
    quantity, and when they disagree the frame is laid out for a window that
    is not on screen: a 24-row frame in a 12-row terminal, scrollbar and all.

    `hits` is filled with the clickable cells the frame painted, and `regions`
    with the frame's blocks as `(name, first row, rows)`. Both are asked of the
    drawing code rather than recomputed here, so a click cannot land a row away
    from the swatch the user aimed at and a widget cannot claim a row the frame
    did not draw.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        draw_editor(fmt, path, state.slots, state.sel, state.undo,
                    state.status, state.mult, head=head, hits=hits,
                    regions=regions, size=(cols, rows))
    text = buffer.getvalue()
    rows = text.split("\r\n")
    if rows and rows[-1] == "":
        rows.pop()
    return rows


class Frame(Widget):
    """The whole frame, one row per line `draw_editor` wrote.

    `render_line` is the only thing Textual asks for, and it is handed the row's
    screen coordinate. The rows are parsed once here rather than per frame: the
    strings are exactly what `render.py` emitted, so `Text.from_ansi` is a
    re-encoding rather than an interpretation.
    """

    # Textual makes *every* widget selectable by default, and the screen checks
    # the app's `ALLOW_SELECT` to decide whether a drag selects text. So a
    # plain block — a swatch, a header, a hint — would begin a text selection
    # when clicked, and clicking a swatch is how a colour is selected. Off
    # here, on for the two blocks whose text is meant to be taken away.
    # `test_the_palette_grid_is_not_selectable` guards it, because the failure
    # is silent: the selection looks like nothing happened.
    ALLOW_SELECT = False

    DEFAULT_CSS = """
    Frame { background: $background; color: $foreground; }
    """

    def __init__(self, rows_text, width, **kwargs):
        super().__init__(**kwargs)
        self.console = Console(file=io.StringIO(), force_terminal=True,
                               color_system="truecolor", legacy_windows=False,
                               markup=False, highlight=False)
        self.rows_text = rows_text
        self._cache = [Text.from_ansi(row) for row in rows_text]

    def selection_for(self, y: int):
        """The theme's selection style over this row's selected span, or None.

        §6.2 — Textual paints a selection inside `Visual.to_strips`, which is
        the path a widget with a `render()` takes. This widget has none: its
        rows are already-parsed `Text`, and `render_line` is the whole story. So
        the selection has to be applied here, from the screen's
        `screen--selection` component styles — which is where the theme's
        `selection-background` and `selection-foreground` arrive. Skip this and
        a drag highlights nothing at all; use a colour of your own and it
        highlights in something that is not the theme, which I2 rejects.
        """
        if not self.ALLOW_SELECT:
            return None
        selection = self.text_selection
        if selection is None:
            return None
        span = selection.get_span(y)
        if span is None:
            return None
        # `Text.stylize` takes a *rich* Style, and Textual's `Style` is a
        # different thing wearing the same name; `rich_style` is the bridge.
        # Handing one to the other does not raise, it tries to parse the object
        # as a style definition and fails deep inside rich.
        style = Style.from_styles(
            self.screen.get_component_styles("screen--selection")).rich_style
        start, end = span
        return style, start, len(self._cache[y].plain) if end < 0 else end

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0:
            return Strip([])
        if y >= len(self._cache):
            return Strip([Segment(" " * width)], width)
        row = self._cache[y]
        marked = self.selection_for(y)
        if marked is not None:
            style, start, end = marked
            row = row.copy()
            row.stylize(style, start, end)
        segments = list(row.render(self.console, end=""))
        filled = sum(segment.cell_length for segment in segments)
        if filled < width:
            # §8.2 — the row reaches the edge in the buffer's own fill, so no
            # column shows the terminal's background
            segments.append(Segment(" " * (width - filled)))
        return Strip(segments, width)


class Swatches(Frame):
    """The palette and interface grids: focusable, and holding their own keys.

    Phase 5's first extraction gave every block its own widget but left the keys
    on the app, which meant the grid was a picture of a control rather than a
    control. Here it is the control: the arrows are *its* bindings, so they are
    routed to it by Textual and never reach the app's `on_key` — one key
    surface, asked in one place, instead of the app deciding on every keypress
    which of its widgets wanted it.

    The grid's navigation is not reimplemented. `apply_key` still does it, for
    three reasons worth keeping: `move_slot` is §4.3's contract and is tested
    there; the arrow keys, the mouse and the click handler must agree about
    where `sel` moves to; and a session driven headlessly (`tests/session.py`)
    has no compositor at all, so a binding here would be a second, untested
    implementation of the same walk.

    Focus follows the selection rather than sitting still. The two grids are one
    logical widget split across two rows of the frame, so which of them holds the
    selected cell decides which one has focus — and when the arrows walk out of
    the palette into the interface, focus moves with them.
    """

    can_focus = True

    BINDINGS = [Binding(key, "slot('%s')" % key, key, show=False)
                for key in GRID_KEYS]

    def action_slot(self, direction: str) -> None:
        app = self.app
        apply_key(direction, app.state)
        app.redraw()
        app.focus_grid(app.state.sel)

    def on_click(self, event) -> None:
        # The app handles every click itself, from the frame's announced cells —
        # one place answers "what did you point at", rather than each widget
        # working out whether the point was its own business.
        event.stop()
        self.app.on_click(event)


class Selectable(Frame):
    """A block of the frame the user can select text out of.

    The code sample and the live diff are the two blocks whose whole point is
    that their text can leave the editor: sample a colour somewhere, copy the
    hex, paste the new value. As a painted row they were inert — the glyphs were
    right and there was no way to reach them.

    `ALLOW_SELECT` is the entire mechanism. Textual composites the selection on
    the *screen*, in a `.screen--selection` overlay, so `Frame.render_line` is
    untouched and I1 does not move: a capture never has a selection down, and a
    frame with one down is the same frame plus an overlay.

    Which is also the risk. The overlay's two colours are design tokens like any
    other, and Textual would otherwise supply its own — so `TOKEN_SLOTS` binds
    them to the theme's `selection-background` and `selection-foreground`, the
    slots the picker already shows itself. I2 is what proves the binding holds;
    without it, a user selecting a line would get Textual's blue.
    """

    ALLOW_SELECT = True

    def get_selection(self, selection):
        """The selected text, read from the rows the widget painted.

        `Widget.get_selection` asks the widget to `render()` a Visual and selects
        from that. This widget has no `render()` — its content is the rows
        `draw_editor` wrote, already parsed — so the default finds nothing and a
        drag selects an empty string: the highlight would paint and the
        clipboard would be blank, which is the worst of both.

        `Text.plain` is the same parse `render_line` already did, so the text
        handed to a selection is the text on screen, escape codes and all the
        SGR in the row included.
        """
        return selection.extract("\n".join(row.plain
                                            for row in self._cache)), "\n"


class Sample(Selectable):
    """The live code sample: Zig, pygments-highlighted, in truecolor (§14)."""


class Diff(Selectable):
    """The live diff — the hunk that appeared since the last save (§14)."""


class Picker(Frame):
    """The theme picker as a widget of its own (§13.7, migration §5.6).

    Its first version is exactly `theme_lines`' output through the same
    compositor as everything else — the rows are `render.py`'s, parsed the same
    way — so extraction is a rearrangement with no visible consequence, and I1
    says so cell for cell.

    What it buys is the thing phase 5 is for. The picker used to be a parameter
    of the editor frame (`draw_editor(overlay=...)`), which meant it could never
    have its own focus, its own bindings or its own hit-testing, and every
    change to either surface had to go through a function whose whole job was
    to be both. As a widget it is mounted instead of composed, and the editor
    frame goes back to having one job.

    **It scrolls by selection, and that is not a simplification.** The window is
    a function of `overlay_index` — `theme_lines` centres it — and the footer
    prints `8-26 of 34` from it, so a viewport that scrolled on its own would
    move that counter and I1 would see it. What a `ScrollView` would add here
    is a scrollbar: seven of Textual's 168 design tokens exist only for it, and
    I2's whole job is to reject exactly that. The wheel moves the selection,
    which moves the window, which is what a user pressing a wheel key means.

    Focusable, because §13.7 says the picker owns the surface while it is up:
    focus left on a grid underneath would let an arrow move the *colour*
    selection behind a list the user is reading.
    """

    can_focus = True


#: Which widget draws which block. Everything not named here is a plain `Frame`:
#: a picture of a thing, with nothing to press. The two grids and the two text
#: blocks are the ones that are controls — and the distinction is worth making
#: explicitly, because "a widget per block" sounds like they are all the same
#: and only these four answer to the user.
BLOCK_WIDGETS = {"palette": Swatches, "interface": Swatches,
                 "sample": Sample, "diff": Diff}


#: Panels: stacked bordered groups, like the reference screenshot's
#: `Data Catalog | Query Editor / Query Results`. Panel 1 is the controls
#: (`palette` + `interface` + the `selected` readout, which is the readout
#: *of* the selected control); panel 2 is everything live (`examples` +
#: `diff` + `sample`). `header` / `hints` / `status` stay full-width chrome
#: outside both, like the screenshot's bottom `CTRL+Q Quit` bar. Widths
#: below MIN+border fall back to the unpanelled frame, because a bordered
#: panel narrower than the frame's own floor has nothing honest to draw
#: (§15.4).
PANEL_CONTROLS = ("palette", "interface", "selected")
PANEL_EXAMPLES = ("examples", "diff", "sample")
PANEL_TITLES = {"controls": "palette / interface", "examples": "examples"}

#: The side-by-side layout: `selected` full-width above, the controls and
#: the live blocks in two panels next to each other below. The left panel
#: is a fixed content width (`SIDE_LEFT_W` + two border columns); the right
#: takes the rest, so side-by-side starts where both stay usable (W>=100)
#: and narrower windows keep the stacked panels. `selected` is bare chrome
#: above the pair, not a third panel: a two-row readout needs no border.
SIDE_MIN_W = 100
LEFT_OUTER_W = SIDE_LEFT_W + 2


def panels_enabled() -> bool:
    """Whether the panel layout is on.

    Default on: the frame is two bordered panels, not a bare stack.
    `HUEBOX_PANELS=0` opts out back to the frameless stack (tests and the
    harness use it where they assert the bare rows, never as product).
    """
    return os.environ.get("HUEBOX_PANELS", "1") != "0"


class Panel(Vertical):
    """One bordered group of the frame's blocks (prototype).

    A `Vertical` that owns nothing but chrome: the border and its title.
    The rows inside are still `draw_editor`'s, captured rather than
    re-rendered, so the panel cannot disagree with the frame about what
    a block says — it only puts a themed border around it. Never
    focusable itself; focus stays on the `Swatches` grids inside, and a
    click on the border is chrome and selects nothing (§4.3.2).
    """

    can_focus = False
    can_focus_children = True

    DEFAULT_CSS = """
    Panel {
        border: round $border;
        background: $background;
        border-title-color: $foreground;
        border-title-background: $background;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self, title: str, *children: Widget, name=None, **kwargs):
        super().__init__(*children, name=name, **kwargs)
        self.border_title = title


#: The live blocks, each collapsible on its own (§14.1): the strip, the
#: hunk and the code sample. Three blocks, three toggles — a collapsed strip
#: never takes the diff and the sample with it.
LIVE_BLOCKS = ("examples", "diff", "sample")

#: What the compositor calls the live blocks. The strip demonstrates the
#: interface text pairs (background, selection, cursor), so that is what its
#: header says; the bare rows keep `draw_editor`'s own "examples" title, and
#: I1 pins those — the header already differs by its toggle mark, so the
#: label is product chrome of the same kind.
LIVE_TITLES = {"examples": "interface text", "diff": "live diff",
                "sample": "live code"}

#: One key per live block. All three are free in `apply_key`, so the toggles
#: never steal a colour key; the picker owns the surface while it is up, so
#: behind it they stay editor keys (no-ops) rather than collapsing the frame
#: the user is reading.
COLLAPSE_KEYS = {"e": "examples", "d": "diff", "c": "sample"}


def collapsible_enabled() -> bool:
    """Whether the live collapsibles are on.

    Default on: each live block rides in an open `Live` rather than as bare
    rows. `HUEBOX_COLLAPSIBLE=0` opts out back to the frameless stack (tests
    and the harness use it where they assert the bare rows, never as
    product). I1 pins bare (`0`) while I2 runs product, so the key must tell
    them apart — the same rule as the panels prototype that came before it.
    """
    return os.environ.get("HUEBOX_COLLAPSIBLE", "1") != "0"


class Live(Collapsible):
    """One live block as a collapsible, open by default (§14.1).

    The rows inside are still `draw_editor`'s, captured rather than
    re-rendered, so the collapsible cannot disagree with the frame about what
    its block says — it only puts a toggle around it. Never focusable itself;
    focus stays on the `Swatches` grids, and the title answers to click and
    Enter through `Collapsible`'s own toggle.

    Theme-closed by construction: the CSS below uses only `$background` and
    `$foreground` — the tokens `TOKEN_SLOTS` already binds — and no tint, no
    hover wash, no focus ring that would derive a colour the reference never
    painted. I2 is what proves it.
    """

    can_focus = False
    can_focus_children = True

    DEFAULT_CSS = """
    Live {
        background: $background;
        border: none;
        padding: 0;
        margin: 0;
    }
    Live Contents {
        background: $background;
        padding: 0;
        margin: 0;
    }
    Live CollapsibleTitle {
        background: $background;
        color: $foreground;
        padding: 0;
        margin: 0;
        text-style: bold;
    }
    Live CollapsibleTitle:hover {
        background: $background;
        color: $foreground;
    }
    Live CollapsibleTitle:focus {
        background: $background;
        color: $foreground;
        text-style: bold;
    }
    Live:focus-within {
        background: $background;
    }
    """

    def __init__(self, title: str, *children: Widget, collapsed=False,
                 name=None, **kwargs):
        super().__init__(*children, title=title, collapsed=collapsed,
                         name=name, **kwargs)


class Editor(App):
    """One editing session, under Textual's compositor.

    `write` is the session's one save path and `library` the picker's seam onto
    the theme store — the same four injected seams `edit()` took, unchanged, so
    `cli` builds them once and both the tests and this shell consume them.
    """

    ENABLE_COMMAND_PALETTE = False
    # Textual's `Screen` is `overflow-y: auto`, so any frame whose content is a
    # row taller than the window gets a scrollbar and a wheel that scrolls it.
    # Nothing in huebox's frame should ever scroll: §15 lays the blocks out to
    # fill the window exactly, and the picker scrolls by *selection* — the
    # window in `theme_lines` is a function of `overlay_index`, because a
    # viewport that moved on its own would move the `8-26 of 34` counter and I1
    # would see it.
    #
    # So the screen is told so explicitly. "Fits exactly" is a property of the
    # layout; "therefore cannot scroll" is a consequence, and a consequence the
    # framework will not infer. Left implicit it is also invisible: a wheel
    # scroll moves the frame a row and the cells it exposes were painted for a
    # window that no longer exists, which reads as a stale header rather than as
    # a scroll.
    CSS = """
    Screen { overflow: hidden; }
    CollapsibleTitle {
        background: $background;
        color: $foreground;
        padding: 0;
        margin: 0;
        text-style: bold;
    }
    CollapsibleTitle:hover {
        background: $background;
        color: $foreground;
    }
    CollapsibleTitle:focus {
        background: $background;
        color: $foreground;
        text-style: bold;
    }
    Collapsible Contents {
        background: $background;
        padding: 0;
        margin: 0;
    }
    """

    BINDINGS = []

    def __init__(self, fmt="ghostty", path="/tmp/huebox.conf", slots=None,
                 write=None, backup_path=None, theme=None, library=None,
                 head_override=None, **kwargs):
        # Before `super()`: App.__init__ calls get_css_variables() to build the
        # stylesheet, so the buffer must exist by then or the first frame is
        # painted against MISSING for every slot.
        self.fmt = fmt
        self.path = path
        self.slots = load_slots() if slots is None else dict(slots)
        self.write = write
        self.backup_path = backup_path
        self.theme_name = theme
        self.library = library
        # `None` means "derive it from the session"; `""` means "none", which is
        # how the harness pins the header to the reference's arguments.
        self.head_override = head_override
        self.state = None
        self.hits: list = []
        self.rows_text: list = []
        # Prototype panels: screen→frame translation for clicks. Empty
        # (panels off) means identity: `slot_at` on the event as-is.
        self._panels_on = False
        self._panel_geom: dict = {}
        # The live collapsibles, all open by default (§14.1): the set of
        # block names standing collapsed. Compositor state, not buffer state
        # — the frame is always drawn whole and the collapsed rows are hidden,
        # never unpainted, so a headless session never collapses and I1 pins
        # the bare rows it always did.
        self._collapsed: set = set()
        super().__init__(**kwargs)
        # After `super()`, and in the constructor rather than `on_mount`: Textual
        # delivers a `Resize` during start-up, before `on_mount` runs, and
        # `on_resize` redraws. A session built in `on_mount` would still be None
        # then, and the frame would be drawn from nothing.
        self.build_state()

    # -- the session -------------------------------------------------------

    def build_state(self):
        """The session, seeded from the environment the harness supplies.

        `HUEBOX_SEL`, `HUEBOX_MULT` and `HUEBOX_STATUS` exist so the reference
        and the candidate can be given the *same* session rather than each
        choosing its own defaults — `mult` is printed verbatim by the hint line,
        so `False` against `1` is a visible cell difference that is nothing to
        do with colour.
        """
        state = EditorState(self.slots, None, self.prompt_text,
                            self.backup_path, theme=self.theme_name,
                            fmt=self.fmt, library=self.library,
                            path=self.path)
        state.prompt_name = self.prompt_text
        if self.write is not None:
            # bound to the state, not to this call's arguments: both the theme
            # and the path can change while the session runs (§13.7)
            state.write = lambda values: self.write(state.theme, state.path,
                                                    values)
        state.sel = int(os.environ.get("HUEBOX_SEL", "0"))
        state.mult = _mult_step(os.environ.get("HUEBOX_MULT"))
        state.status = os.environ.get("HUEBOX_STATUS", "")
        scene = os.environ.get("HUEBOX_PICKER")
        if scene:
            # The harness needs the picker up on the *first* paint: a capture
            # that had to send a keypress would be racing the frame it is
            # measuring. The names come from the launch contract, not from a
            # theme directory, so a capture reads the same list whatever
            # happens to be in the user's ~/.local/share/huebox/themes.
            names = os.environ.get("HUEBOX_PICKER_NAMES", "").split(",")
            names = [name for name in names if name]
            if not names:
                names = [os.environ.get("HUEBOX_PICKER_THEME", "theme")]
            state.overlay = names
            state.overlay_index = min(int(os.environ.get("HUEBOX_PICKER_INDEX",
                                                         "0")), len(names) - 1)
        self.state = state
        return state

    def compose(self) -> ComposeResult:
        # Nothing here: the frame's width comes from the size Textual hands us
        # at mount, and a `ScrollView` would bring a border and a scrollbar the
        # frame has no room for. Scrolling is phase 4, gated on I1 like
        # everything else that changes what reaches the screen.
        return iter(())

    def get_css_variables(self):
        variables = dict(super().get_css_variables())
        for token, slot in TOKEN_SLOTS.items():
            variables[token] = self.slots.get(slot, MISSING)
        return variables

    def head_for(self, state):
        """The header's subject: pinned by the harness, derived otherwise."""
        if self.head_override is not None:
            return self.head_override or None
        return head_label(state)

    def redraw(self) -> None:
        """Re-derive the frame at the current size and hand it to the widget."""
        width, height = self.size
        state = self.state
        # §15 — one geometry per frame, read by the frame and by the arrows,
        # which move through the grid the user can see (§4.3)
        state.grid = grid_geometry(width)

        self.hits = []
        self.regions = []
        self._panels_on = False
        self._panel_geom = {}
        self._side_on = False
        self._side_geom = {}
        # §13.7 — the picker owns the surface while it is up, and it shares the
        # editor's minimum size, so the too-small check covers both frames and
        # is asked once, here, rather than twice inside `draw_editor`.
        picker = state.picker_frame()
        if width < MIN_COLS or height < MIN_ROWS:
            rows_text = [too_small_frame(width)]
        elif picker is not None:
            # §8.2 — `backdrop` is what fills each row out to the last column
            # in the buffer's own colour, so no cell of the picker shows the
            # terminal's background. It was the overlay branch's job in
            # `draw_editor` and it is this widget's job now; dropping it is
            # invisible in a diff of the text and enormous in a diff of the
            # cells, since every background becomes "never painted".
            rows_text = [backdrop(line, state.slots, width)
                         for line in theme_lines(*picker, width, height,
                                                 state.status, state.slots,
                                                 hits=self.hits)]
        elif (panels_enabled() and width >= SIDE_MIN_W
                and self._try_side(width, height, state)):
            # Side-by-side mounted everything: chrome above, two panels
            # below. Both locals are what the debug line counts.
            named = list(self.regions)
            rows_text = self.rows_text
        elif (panels_enabled()
                and width >= MIN_COLS + 2 and height >= MIN_ROWS + 2):
            # Prototype panels: the same rows, laid out for the inner width.
            # `header` / `hints` are laid out narrow too and padded on display
            # — the pad is the theme's own background, so it reads as fill.
            # Two passes, so a frame with no examples only pays for one panel:
            # first at H-2, and only when live blocks showed up re-lay at H-4
            # for both borders. Always reserving four would trim the hints
            # into the controls at small sizes (60x16), where they belong
            # outside the panel, not in it.
            inner_w = width - 2
            state.grid = grid_geometry(inner_w)
            trial_hits, trial_regions = [], []
            trial = frame_rows(self.fmt, session_path(state), state,
                               inner_w, height - 2,
                               head=self.head_for(state),
                               hits=trial_hits, regions=trial_regions)
            names = {name for name, _, _ in trial_regions}
            two = bool(names & set(PANEL_EXAMPLES))
            inner_h = height - 4 if two else height - 2
            if two and height < MIN_ROWS + 4:
                # Room for one panel but not two: fall back to the bare frame
                # rather than trimming widgets to buy borders.
                rows_text = frame_rows(self.fmt, session_path(state), state,
                                       width, height,
                                       head=self.head_for(state),
                                       hits=self.hits, regions=self.regions)
            else:
                rows_text = (trial if (inner_h == height - 2) else frame_rows(
                    self.fmt, session_path(state), state, inner_w, inner_h,
                    head=self.head_for(state),
                    hits=self.hits, regions=self.regions))
                if inner_h == height - 2:
                    self.hits = trial_hits
                    self.regions = trial_regions
                self.rows_text = rows_text
                named = list(self.regions)
                self._mount_panels(width, rows_text, named)
                self._panels_on = True
        else:
            rows_text = frame_rows(self.fmt, session_path(state), state,
                                   width, height, head=self.head_for(state),
                                   hits=self.hits, regions=self.regions)
        if not self._panels_on and not self._side_on:
            self.rows_text = rows_text
            for child in list(self.query(Panel)):
                child.remove()
            for child in list(self.query(Frame)):
                child.remove()
            for child in list(self.query(Live)):
                child.remove()
            for child in list(self.query(Horizontal)):
                child.remove()
            # §5.6 — one widget per block of the frame. The blocks are the rows
            # `draw_editor` reported, in order and without gaps, so the widgets stack
            # to exactly the frame's height and not one row more: a stack taller than
            # the screen would give the screen a scrollbar, which is seven of
            # Textual's 168 design tokens arriving in the frame's first paint.
            #
            # With the collapsibles on, each live block rides inside its own
            # open `Live` instead: the header replaces the block's title row
            # one for one, so an all-open stack is still exactly the frame's
            # height. A collapsed block hides its rows behind its header; what
            # is below it rides up, and the empty rows at the bottom stand on
            # the screen's own background. Panels and side-by-side do their own
            # grouping, so the collapsibles only apply to the bare stack.
            named = ([("picker", 0, len(rows_text))] if picker is not None
                     else list(self.regions)
                     or [("frame", 0, len(rows_text))])
            if (collapsible_enabled() and picker is None
                    and not (width < MIN_COLS or height < MIN_ROWS)
                    and any(entry[0] in LIVE_BLOCKS for entry in named)):
                self._mount_collapsible(width, rows_text, named)
            else:
                self._mount_bare(width, rows_text, named)
        # `mount` is a request, not a fact: the widgets do not exist yet, so
        # focus has to wait for the next refresh. Done inline it would query an
        # empty tree and quietly leave the focus wherever it was — which, with
        # seven blocks each asking for it on mount, was the last block in the
        # frame. Every block used to steal focus in `on_mount`; now only the two
        # grids can take it, and only the one holding the selection does.
        settle = partial(self.place_focus, picker is not None, state.sel)
        if self.is_running:
            self.call_after_refresh(settle)
        else:
            settle()
        _debug("redraw %dx%d: %d rows, %d blocks, sel=%d, widest=%d"
               % (width, height, len(rows_text), len(named), state.sel,
                  max((visible(row) for row in rows_text), default=0)))

    def _mount_panels(self, width: int, rows_text: list, named: list) -> None:
        """Stack the frame's blocks into two bordered panels.

        `named` is `draw_editor`'s own `(name, first, count)` map at the inner
        size, so the groups cannot drift from what the frame painted: the
        same rule as `hits`. Chrome (the top block, then `hints` / `status`)
        stays full-width outside; controls and examples each get a `Panel`
        with a themed border and title. Inner blocks keep the inner width; chrome
        blocks are re-backed to the full width, because a row backed to the
        inner width and padded by the widget would leave two columns on the
        terminal's own background (§8.2) — the pad has no style of its own.
        """
        for child in list(self.query(Panel)):
            child.remove()
        for child in list(self.query(Frame)):
            child.remove()
        for child in list(self.query(Live)):
            child.remove()
        inner_w = width - 2
        by_name = {name: (first, count) for name, first, count in named}

        def rows_for(names):
            # Frame order, not group order: `named` tiles the frame, so
            # filtering it keeps every group in the order the frame
            # painted — a block the frame moved (the top block put
            # `selected` above the palette) cannot mount stale.
            wanted = set(names)
            return [(name, first, count) for name, first, count in named
                    if name in wanted]

        # §8.1 (decision 36) — the side top block is one block: where the
        # frame put `selected` above the palette grid it is the header's
        # right column, not a control, so it rides with the header chrome
        # above the panels. Inside the controls panel it would sit below
        # the grids it reads, splitting the block across a border — and
        # the widgets would no longer reassemble the frame in order.
        top = ["header"]
        if ("selected" in by_name and "palette" in by_name
                and by_name["selected"][0] < by_name["palette"][0]):
            top.append("selected")
        header = rows_for(top)
        controls = rows_for(name for name in PANEL_CONTROLS
                            if name not in top)
        examples = rows_for(PANEL_EXAMPLES)
        hints = rows_for(("hints", "status"))
        # Any block `draw_editor` reported that is in none of the groups
        # (a future widget) stays chrome rather than vanishing: mount it
        # full-width in order. Prototype must not drop rows it does not know.
        known = {"header", *PANEL_CONTROLS, *PANEL_EXAMPLES, "hints", "status"}
        extra = [(n, f, c) for n, f, c in named if n not in known]

        def mount_block(name, first, count, block_width):
            rows_here = rows_text[first:first + count]
            if block_width > inner_w:
                # Chrome at full width: re-back the inner rows out to the
                # edge in the buffer's own background, so no column shows
                # the terminal's (§8.2). Inner blocks skip this: they are
                # already backed to exactly the width they are mounted at.
                slots = self.state.slots
                rows_here = [backdrop(row, slots, block_width)
                             for row in rows_here]
            kind = BLOCK_WIDGETS.get(name, Frame)
            block = kind(rows_here, block_width, name=name)
            block.styles.width = block_width
            block.styles.height = len(rows_here)
            block.styles.padding = 0
            block.styles.margin = 0
            return block

        header_h = sum(c for _, _, c in header)
        controls_h = sum(c for _, _, c in controls)
        examples_h = sum(c for _, _, c in examples)
        # Screen geometry for clicks: borders are single rows. Header is
        # bare; each panel adds a top and a bottom border row.
        self._panel_geom = {
            "inner_w": inner_w,
            "header_h": header_h,
            "controls_h": controls_h,
            "examples_h": examples_h,
            "has_examples": bool(examples),
        }
        for name, first, count in header + extra:
            # `extra` unknown rows sit with the header chrome: full-width,
            # never inside a panel whose title would misname them.
            self.mount(mount_block(name, first, count, width))
        if controls:
            inners = [mount_block(n, f, c, inner_w) for n, f, c in controls]
            panel = Panel(PANEL_TITLES["controls"], *inners, name="controls")
            panel.styles.width = width
            panel.styles.height = controls_h + 2
            panel.styles.padding = 0
            panel.styles.margin = 0
            self.mount(panel)
        if examples:
            inners = [mount_block(n, f, c, inner_w) for n, f, c in examples]
            panel = Panel(PANEL_TITLES["examples"], *inners, name="examples")
            panel.styles.width = width
            panel.styles.height = examples_h + 2
            panel.styles.padding = 0
            panel.styles.margin = 0
            self.mount(panel)
        for name, first, count in hints:
            self.mount(mount_block(name, first, count, width))
        for child in list(self.query(Horizontal)):
            child.remove()

    def _try_side(self, width: int, height: int, state) -> bool:
        """The side-by-side layout, or `False` to keep the stacked one.

        `selected` full-width above; the controls (palette pairs over
        interface pairs) and the live blocks in two panels next to each
        other below. The chrome rows come from a full-width `draw_editor`
        run — captured, like everywhere — while the panels' contents are
        `side_left_rows` / `side_live_rows` at their own widths. Anything
        that does not fit (trimmed chrome, a short middle) returns `False`
        before mounting anything, and the stacked layout runs instead.
        """
        full_hits: list = []
        full_regions: list = []
        full = frame_rows(self.fmt, session_path(state), state,
                          width, height, head=self.head_for(state),
                          hits=full_hits, regions=full_regions)
        by_name = {name: (first, count)
                   for name, first, count in full_regions}
        for need in ("header", "selected", "hints"):
            if need not in by_name:
                return False        # a trimmed frame: chrome must be whole
        header_h = by_name["header"][1]
        sel_h = by_name["selected"][1]
        hints_h = by_name["hints"][1]
        status_h = by_name["status"][1] if "status" in by_name else 0
        bottom_h = hints_h + status_h
        content_h = height - header_h - sel_h - bottom_h - 2
        if content_h < SIDE_LEFT_ROWS:
            return False
        right_outer = width - LEFT_OUTER_W
        right_inner = right_outer - 2
        right_regions: list = []
        right = side_live_rows(state.slots, right_inner, content_h,
                               regions=right_regions)
        if right is None:
            return False
        left_hits: list = []
        left = side_left_rows(state.slots, state.sel, hits=left_hits)
        # Success past this point: grid, mount, publish.
        state.grid = side_grid()

        def block(name, rows_here, block_width):
            kind = BLOCK_WIDGETS.get(name, Frame)
            widget = kind(rows_here, block_width, name=name)
            widget.styles.width = block_width
            widget.styles.height = len(rows_here)
            widget.styles.padding = 0
            widget.styles.margin = 0
            return widget

        for child in list(self.query(Panel)):
            child.remove()
        for child in list(self.query(Frame)):
            child.remove()
        for child in list(self.query(Live)):
            child.remove()
        for child in list(self.query(Horizontal)):
            child.remove()
        slots = state.slots
        for name in ("header", "selected"):
            first, count = by_name[name]
            self.mount(block(name, full[first:first + count], width))
        left_children = [block("palette", left[0:9], SIDE_LEFT_W),
                         block("interface", left[9:SIDE_LEFT_ROWS],
                               SIDE_LEFT_W)]
        pad = content_h - SIDE_LEFT_ROWS
        if pad:
            left_children.append(block(
                "left-pad", [backdrop("", slots, SIDE_LEFT_W)] * pad,
                SIDE_LEFT_W))
        left_panel = Panel(PANEL_TITLES["controls"], *left_children,
                           name="controls")
        left_panel.styles.width = LEFT_OUTER_W
        left_panel.styles.height = content_h + 2
        left_panel.styles.padding = 0
        left_panel.styles.margin = 0
        right_children = [block(name, right[first:first + count], right_inner)
                          for name, first, count in right_regions]
        right_panel = Panel(PANEL_TITLES["examples"], *right_children,
                            name="examples")
        right_panel.styles.width = right_outer
        right_panel.styles.height = content_h + 2
        right_panel.styles.padding = 0
        right_panel.styles.margin = 0
        row = Horizontal(left_panel, right_panel)
        row.styles.width = width
        row.styles.height = content_h + 2
        row.styles.padding = 0
        row.styles.margin = 0
        self.mount(row)
        bottom0 = header_h + sel_h + content_h + 2
        for name in ("hints", "status"):
            if name in by_name:
                first, count = by_name[name]
                self.mount(block(name, full[first:first + count], width))
        # Coordinate spaces, because there are two panels now: chrome blocks
        # are full-frame rows, `palette` / `interface` are left-content rows
        # (what `self.hits` is announced in), the live blocks right-content
        # rows. `focus_grid` only ever reads the middle two, against the
        # hits, so the three spaces never meet — but they are named here so
        # the next reader does not assume one frame.
        self.rows_text = full
        self.hits = left_hits
        self.regions = [("header", 0, header_h),
                        ("selected", header_h, sel_h),
                        ("palette", 0, 9),
                        ("interface", 9, SIDE_LEFT_ROWS - 9)]
        self.regions.extend(right_regions)
        self.regions.append(("hints", bottom0, hints_h))
        if status_h:
            self.regions.append(("status", bottom0 + hints_h, status_h))
        self._side_on = True
        self._side_geom = {"top": header_h + sel_h,
                           "content_h": content_h,
                           "left_outer": LEFT_OUTER_W}
        return True

    def _side_frame_coords(self, sx: int, sy: int):
        """Screen → left-content coords, or `None` for chrome.

        Only the left panel holds controls: the right panel's examples are
        readouts, the `selected` strip above is a readout, and every border
        row and column is chrome. A click anywhere else selects nothing.
        """
        geom = self._side_geom
        y = sy - geom["top"]
        if y == 0 or y == geom["content_h"] + 1:
            return None             # the pair's top / bottom borders
        if not 1 <= y <= geom["content_h"]:
            return None             # chrome above or below the pair
        if sx >= geom["left_outer"]:
            return None             # the examples panel
        if sx == 0 or sx >= SIDE_LEFT_W + 1:
            return None             # the controls panel's own borders
        return sx - 1, y - 1

    def _panel_frame_coords(self, sx: int, sy: int):
        """Screen → frame coords inside prototype panels, or None on chrome.

        Borders are chrome: a click on any border row or border column is
        not an error and moves nothing (§4.3.2). Inner blocks are offset by
        one column (left border) and by the border rows above them.
        """
        geom = self._panel_geom
        if not geom:
            return sx, sy
        inner_w = geom["inner_w"]
        header_h = geom["header_h"]
        controls_h = geom["controls_h"]
        examples_h = geom["examples_h"]
        has_examples = geom["has_examples"]
        # Header chrome: full-width, no offset. Columns past the inner width
        # are pad and hit nothing, which `slot_at` already says as None.
        if sy < header_h:
            return sx, sy
        y = sy - header_h
        # Controls panel: top border, inner, bottom border.
        if y == 0:
            return None
        if 1 <= y <= controls_h:
            if sx == 0 or sx > inner_w:
                return None
            # Inner frame Y includes the header rows before it.
            return sx - 1, header_h + (y - 1)
        if y == controls_h + 1:
            return None
        y -= controls_h + 2
        if has_examples:
            if y == 0:
                return None
            if 1 <= y <= examples_h:
                if sx == 0 or sx > inner_w:
                    return None
                return sx - 1, header_h + controls_h + (y - 1)
            if y == examples_h + 1:
                return None
            y -= examples_h + 2
        # Hints chrome below both panels: two border rows per panel above.
        panels = 2 if has_examples else 1
        # `y` is now relative to the hints zone; inner Y adds back everything
        # above it. Borders above = 2 per panel.
        return sx, sy - 2 * panels

    def _mount_bare(self, width: int, rows_text: list, named: list) -> None:
        """Mount every block as its own widget, with no collapsible."""
        for name, first, count in named:
            rows_here = rows_text[first:first + count]
            kind = BLOCK_WIDGETS.get(name, Frame) if name != "picker" \
                else Picker
            # `name` is a constructor argument, not a settable property — which
            # is Textual saying the name is part of a widget's identity.
            block = kind(rows_here, width, name=name)
            block.styles.width = width
            block.styles.height = len(rows_here)
            block.styles.padding = 0
            block.styles.margin = 0
            self.mount(block)

    def _mount_collapsible(self, width: int, rows_text: list,
                           named: list) -> None:
        """Mount the frame with each live block inside its own `Live`.

        Each header replaces its block's title row one for one, so an
        all-open stack is still exactly the frame's height: every body row
        keeps its row and only the three title rows are the collapsibles'
        own. A collapsed block hides its rows behind its header; what is
        below it rides up, and the hits never notice — the grids are above
        the live area and the hints name no slot.
        """
        by_name = {name: (first, count) for name, first, count in named}

        def mount_block(name, rows_here):
            kind = BLOCK_WIDGETS.get(name, Frame)
            block = kind(rows_here, width, name=name)
            block.styles.width = width
            block.styles.height = len(rows_here)
            block.styles.padding = 0
            block.styles.margin = 0
            return block

        def mount_live(name):
            first, count = by_name[name]
            # The header stands in for the title row: the body keeps its
            # rows, so open or shut the stack below never shifts by more
            # than what this block hid.
            body = rows_text[first + 1:first + count]
            kind = BLOCK_WIDGETS.get(name, Frame)
            child = kind(body, width, name=name)
            child.styles.width = width
            child.styles.height = len(body)
            child.styles.padding = 0
            child.styles.margin = 0
            self.mount(Live(LIVE_TITLES[name], child,
                            collapsed=name in self._collapsed, name=name))

        order = [name for name, _, _ in named]
        live_at = min((order.index(name) for name in LIVE_BLOCKS
                       if name in by_name), default=len(order))
        for name, first, count in named:
            if name in LIVE_BLOCKS:
                continue
            if order.index(name) < live_at:
                self.mount(mount_block(
                    name, rows_text[first:first + count]))
        for name in LIVE_BLOCKS:
            if name in by_name:
                mount_live(name)
        seen_live = False
        for name, first, count in named:
            if name in LIVE_BLOCKS:
                seen_live = True
                continue
            if seen_live:
                self.mount(mount_block(
                    name, rows_text[first:first + count]))

    def place_focus(self, picker_up: bool, sel: int) -> None:
        """Hand focus to whichever block owns it, once the tree exists.

        §13.7 — the picker owns the surface while it is up, so it takes focus.
        Focus left on a grid underneath would let an arrow move the *colour*
        selection behind a list the user is reading, which is the one thing
        §13.7 says cannot happen.
        """
        # `focused` and `set_focus` both reach for a screen, which an app that
        # has never run does not have. A frame drawn outside Textual — the
        # headless suites, and `tests/session.py` — has no focus to place.
        if not self.is_running:
            return
        # The frame is the window, so any scroll offset on the screen is left
        # over from a moment when it was not — a resize mid-frame, or a frame
        # laid out one row taller than the window it is in. Leaving it there is
        # what makes a scroll reveal a stale header: the cells the scroll exposes
        # were painted for a window that no longer exists, and nothing repaints
        # them because nothing thinks they changed.
        self.screen.scroll_home(animate=False)
        if picker_up:
            for block in self.query(Picker):
                block.focus()
                return
        self.focus_grid(sel)

    def focus_grid(self, sel: int) -> None:
        """Give focus to the grid block that holds the selected cell.

        `sel` is a slot, not a row, so the row it is drawn on is looked up in
        the frame's own hit map rather than computed from the grid — the same
        rule as the click, and for the same reason.

        A short frame can leave the selected slot in no block at all: 40x12 shows
        twelve of the twenty-two slots, and selecting the twenty-second puts the
        selection below the fold. Then *focus is dropped*, rather than left
        where it was — because `on_key` steps over the arrows while a grid holds
        focus, and a grid still holding focus with the selection off it would
        leave the arrows moving a selection the user cannot see, or not moving
        at all. Dropping it hands the keys back to the app.
        """
        target = next((hit for hit in self.hits if hit.slot == sel), None)
        for name, first, count in self.regions:
            if name in GRID_BLOCKS and target is not None \
                    and first <= target.y < first + count:
                for block in self.query(Swatches):
                    if block.name == name:
                        block.focus()
                        return
        if isinstance(self.focused, Swatches):
            self.set_focus(None)

    def on_mount(self) -> None:
        self.title = "huebox"
        self.redraw()

    def on_resize(self, event) -> None:
        # The frame is laid out from `self.size`, and during `on_resize` that is
        # still the *old* size: Textual applies the new one in the layout pass
        # that follows. Redrawing here drew the frame for the window the user
        # just left — a 24-row frame in a 12-row terminal, one resize behind,
        # forever. `call_after_refresh` is the first moment both agree.
        _debug("resize to %s" % (event.size,))
        if self.state is not None:
            self.call_after_refresh(self.redraw)

    # -- input -------------------------------------------------------------

    def on_key(self, event) -> None:
        # Every key, unhandled by Textual, straight to the one key surface —
        # except the four the grid binds for itself while it has focus, which
        # are already on their way to `Swatches.action_slot`, and the three
        # live-block toggles below. Each of those is free in `apply_key`, so
        # no toggle ever steals a colour key; the picker owns the surface
        # while it is up, so behind it they stay editor keys (no-ops) rather
        # than collapsing the frame the user is reading.
        event.stop()
        if isinstance(self.focused, Swatches) and event.key in GRID_KEYS:
            return
        if (event.key in COLLAPSE_KEYS and self.state.overlay is None
                and collapsible_enabled()):
            block = COLLAPSE_KEYS[event.key]
            if block in self._collapsed:
                self._collapsed.discard(block)
            else:
                self._collapsed.add(block)
            self.redraw()
            return
        apply_key(translate(event.key), self.state)
        if self.state.quit:
            self.exit()
        else:
            self.redraw()

    def on_collapsible_collapsed(self, event) -> None:
        """A header toggled shut (click or Enter): stay shut on redraw."""
        # Pending toggles can land after the screen is gone (shutdown flush):
        # there is nothing to redraw into then. A toggle that changes nothing
        # (an `Expanded` posted by a fresh mount) must not redraw either, or
        # every mount schedules another mount.
        name = getattr(getattr(event, "collapsible", None), "name", None)
        if not self.is_running or name not in LIVE_BLOCKS \
                or name in self._collapsed:
            return
        self._collapsed.add(name)
        self.redraw()

    def on_collapsible_expanded(self, event) -> None:
        """A header toggled back open: stay open on redraw."""
        name = getattr(getattr(event, "collapsible", None), "name", None)
        if not self.is_running or name not in LIVE_BLOCKS \
                or name not in self._collapsed:
            return
        self._collapsed.discard(name)
        self.redraw()

    def action_slot(self, direction: str) -> None:      # pragma: no cover
        """Unused: `on_key` covers the arrows, so nothing is bound."""

    # -- the mouse (§4.3.1) ------------------------------------------------

    def on_click(self, event) -> None:
        """Click a swatch or an interface cell to select it.

        The frame widget starts at the screen origin, so a click's screen row is
        its row in the frame; `slot_at` maps a column to a slot or to None for
        the chrome. Clicking nothing is not an error and does not move the
        selection — the header, the readings and the empty air are not controls,
        and pretending otherwise would make the frame feel like a form.

        Both frames answer from `self.hits`, the cells the frame that is up
        announced as it painted them — so the picker needs no branch here at
        all. Its cells carry the *row in the library* in the same field the
        editor's carry the slot in, because both are "the number this row means"
        and neither is anything a click has to translate.
        """
        event.stop()
        # Screen coords survive nesting; `offset` does not. A synthetic
        # `Click(widget=None)` carries its point in both; a real click on a
        # nested block carries the widget point in `offset` and the frame
        # point on the screen. `None` falls back to `offset`: the harness
        # pins `HUEBOX_HEAD` and the size, never the pointer.
        fx = getattr(event, "screen_x", None)
        fy = getattr(event, "screen_y", None)
        if fx is None or fy is None:
            fx, fy = event.offset.x, event.offset.y
        # Side-by-side first: only the left panel answers, in its own
        # content coords against its own hits. Everywhere else is chrome.
        if self._side_on:
            coords = self._side_frame_coords(fx, fy)
            if coords is None:
                return
            target = slot_at(self.hits, *coords)
            if target is None:
                return
            if target != self.state.sel:
                self.state.sel = target
            self.redraw()
            return
        # Screen coords survive nesting; `offset` does not: inside a panel
        # the offset is relative to the inner block, while the hit map is
        # frame-relative. Panels translate back; the bare frame is identity,
        # and a `Live` block's inner widget gives the frame point on the
        # screen, which is what the hit map is announced in.
        if self._panels_on:
            coords = self._panel_frame_coords(fx, fy)
            if coords is None:
                return
            fx, fy = coords
        target = slot_at(self.hits, fx, fy)
        if target is None:
            return
        if self.state.overlay is None:
            # a swatch or an interface cell
            if target != self.state.sel:
                self.state.sel = target
            self.redraw()
            return
        # A row of the library. Move the selection onto it, then let apply_key
        # do what it does for Enter — so a click and Enter cannot diverge, and a
        # click cannot open a theme the keyboard would have refused.
        if target != self.state.overlay_index:
            self.state.overlay_index = target
            self.redraw()
        apply_key("enter", self.state)
        self.redraw()

    def on_mouse_scroll_up(self, event) -> None:
        self._scroll_picker("up", event)

    def on_mouse_scroll_down(self, event) -> None:
        self._scroll_picker("down", event)

    def _scroll_picker(self, direction: str, event) -> None:
        """Wheel the picker's selection, which is how a long library is walked.

        Only in the picker: the editor frame itself does not scroll, so a wheel
        notch does nothing a user could misread as having moved the colours.
        """
        event.stop()
        if self.state.overlay is None:
            return
        apply_key(direction, self.state)
        self.redraw()

    def prompt_text(self, label: str):
        """One line, with the terminal handed over for it (§4.3).

        `App.suspend()` restores the terminal to what it was before the app
        started — cooked mode, no alternate screen — which is exactly the
        drop-out-of-raw-mode pattern `edit()` used, and keeps `apply_key`'s
        call synchronous. Ctrl+C or EOF cancels the prompt and returns to the
        editor rather than ending it.
        """
        with self.suspend():
            _debug("prompt %r" % (label,))
            try:
                return input(label).strip()
            except (EOFError, KeyboardInterrupt):
                return None

    def action_nudge(self, axis: str, step: int) -> None:   # pragma: no cover
        """Unused: `on_key` covers the adjust keys, so nothing is bound."""


def run(fmt, path, slots, write, backup_path=None, theme=None, library=None,
        report=None, notes=None):
    """Run one session to completion, then say what it has to say.

    What `cli` calls. `report_session` runs here rather than in `cli` because the
    session state lives on the `Editor`, and the wording of what a user reads on
    exit is huebox's, not the driver's.
    """
    editor = Editor(fmt=fmt, path=path, slots=slots, write=write,
                    backup_path=backup_path, theme=theme, library=library)
    try:
        editor.run()
    finally:
        report_session(editor.state, report, notes)
    return editor


def main() -> int:
    """`python -m huebox.app` — driven by the environment, for the harness."""
    head = os.environ.get("HUEBOX_HEAD")
    Editor(fmt=os.environ.get("HUEBOX_FMT", "ghostty"),
           path=os.environ.get("HUEBOX_PATH", "/tmp/huebox.conf"),
           head_override=head,
           theme=os.environ.get("HUEBOX_PICKER_THEME")).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

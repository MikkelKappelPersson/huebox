"""The editor: the frame, under Textual's compositor (migration phases 2–3).

§4.3, §13.7, §14, and `docs/001-spec/textual-migration.md` §5.5. Textual owns
the screen; `render.py` still owns the frame. The rows here are the ones
`draw_editor` wrote, captured rather than re-rendered, so "the frame is
unchanged" is true by construction: the compositor underneath is measured
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
from textual.widgets import Button, Collapsible

from .color import MISSING, SLOTS
from .editor import (BANNER_LEFT_W, MINI_LEFT_W, MULT_STEPS, SIDE_LEFT_ROWS, SIDE_LEFT_W, TOP_LEFT_W, EditorState,
                     apply_key, backdrop, draw_editor, grid_geometry,
                     head_label, report_session, session_path, side_grid,
                     side_left_rows, side_live_rows, slot_at, theme_lines,
                     too_small_frame, top_editor_meta, top_left_rows, top_meta_rows, top_side_panels)
from .render import HSV_FIELD, MIN_COLS, MIN_ROWS, visible, hsv_axis

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
    rendered as `f xFalse` in the reference and `f xFalse` in the candidate, and
    the two wrongs matched. §6.2's lesson in a new place: pinning an argument
    no real session passes buys a check that cannot fail.
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
    untouched and the frame does not move: a capture never has a selection down, and a
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
    way — so extraction is a rearrangement with no visible consequence.

    What it buys is the thing phase 5 is for. The picker used to be a parameter
    of the editor frame (`draw_editor(overlay=...)`), which meant it could never
    have its own focus, its own bindings or its own hit-testing, and every
    change to either surface had to go through a function whose whole job was
    to be both. As a widget it is mounted instead of composed, and the editor
    frame goes back to having one job.

    **It scrolls by selection, and that is not a simplification.** The window is
    a function of `overlay_index` — `theme_lines` centres it — and the footer
    prints `8-26 of 34` from it, so a viewport that scrolled on its own would
    move that counter. What a `ScrollView` would add here
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
PANEL_TITLES = {"logo": "logo", "info": "info",
                "controls": "palette / interface", "examples": "examples"}

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


TOP_TITLE = "theme / selected"


EDITOR_TITLE = "editor"
#: Horizontal breathing room inside the editor panel's border (prototype).
#: Vertical stays 0 so the box costs no extra rows; the bars give up two
#: cells of sweep for one cell of air on each side.
EDITOR_PAD_X = 1


def editor_panel_enabled() -> bool:
    """Whether the HSV selectors ride in their own bordered panel.

    Default on: the top is header + metadata (bare chrome) beside one
    bordered `Panel("editor")` holding only the three HSV bars.
    `HUEBOX_EDITOR_PANEL=0` opts back out to the logo/info top, and
    `HUEBOX_TOP=0` pins the stacked header past both; tests pin whichever
    top they assert.
    """
    return os.environ.get("HUEBOX_EDITOR_PANEL", "1") != "0"


def top_bordered_enabled() -> bool:
    """Whether the top rides in one bordered panel (prototype).

    Default off: header + selected stay two unbordered columns (decision 37).
    `HUEBOX_TOP_BORDER=1` merges them into a single bordered `Panel` so the
    selected readout shares the same chrome as the controls/examples below.
    Tests.
    """
    return os.environ.get("HUEBOX_TOP_BORDER", "0") == "1"


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


class ThemesButton(Button):
    """The `themes` button: a mouse mirror of `t` (§4.3.2).

    A flat `Button` labelled `themes`, and the one control the top owns —
    everything else up there is chrome and answers to no click. It rides
    under the readout in the `info` panel, or under the theme subject in
    the editor top's metadata column. The flat *look* is the CSS below
    (no border, no wash); the variant stays default on purpose: the flat
    variant's `color: auto 90%` reroutes the label through the auto contrast
    *after* the explicit colour (`visual_style` applies it last), so the
    label would paint white whatever the theme said — and `auto-color` is
    not a CSS property, so no rule can switch it back. Pressing it
    does exactly what `t` does, through the same `apply_key` call the
    keyboard takes, so the two cannot disagree about what the picker is or
    about what happens to a dirty buffer. Never focusable: focus follows the
    selection between the two grids (or takes the picker while it is up), and
    a button that kept focus would leave the arrows talking to a control
    with no arrows. `ALLOW_SELECT` stays off (inherited), so a drag across it
    can never begin a text selection the way the sample and the diff invite.

    Theme-closed by construction: the CSS below names only the selection
    pair — `$screen-selection-background` and `$screen-selection-foreground`,
    the tokens `TOKEN_SLOTS` already binds to the theme's own
    `selection-background` / `selection-foreground` — with no `auto`
    ink, no derived wash and no tint, so a button never captured
    still paints nothing I2 can object to. The pair is the picker's own mark
    (the `>` row wears it), so the button reads as part of the theme rather
    than as a colour of its own. The hover is an underline, not a colour:
    an attribute says "clickable" where a wash would spend a slot.
    """

    can_focus = False

    DEFAULT_CSS = """
    ThemesButton {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        text-style: bold;
        width: auto;
        height: 1;
        min-width: 16;
        margin: 0;
        padding: 0;
        content-align: center middle;
    }
    ThemesButton:hover {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        text-style: bold underline;
    }
    ThemesButton:focus {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        text-style: bold underline;
    }
    ThemesButton.-active {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        tint: transparent !important;
    }
    ThemesButton:disabled {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
    }
    """

    def __init__(self, **kwargs):
        # Default variant, flat look from the CSS above: `flat=True` would
        # take the `-style-flat` class whose `color: auto 90%` reroutes the
        # label (see the class docstring), so the variant stays off and the
        # borderlessness carries the flatness instead.
        super().__init__("themes", id="themes-button",
                         name="themes", **kwargs)


#: The live blocks, each collapsible on its own (§14.1): the strip, the
#: hunk and the code sample. Three blocks, three toggles — a collapsed strip
#: never takes the diff and the sample with it.
LIVE_BLOCKS = ("examples", "diff", "sample")

#: What the compositor calls the live blocks. The strip demonstrates the
#: interface text pairs (background, selection, cursor), so that is what its
#: header says; the bare rows keep `draw_editor`'s own "examples" title, and
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
    product). The key must tell bare (`0`) from product — the same rule as the panels prototype that came before it.
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
    # viewport that moved on its own would move the `8-26 of 34` counter.
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
        # never unpainted, so a headless session never collapses and
        # the bare rows are what a capture always sees.
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
        # frame has no room for. Scrolling is phase 4, like
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
                and width >= MIN_COLS + 2 and height >= MIN_ROWS + 5):
            # Bordered panels: the same rows, laid out for the inner width.
            # `header` / `hints` are laid out narrow too and padded on display
            # — the pad is the theme's own background, so it reads as fill.
            # Two passes, so a frame with no examples only pays for top +
            # controls (+5: the top's two borders plus its button row, and the
            # controls' two): first at H-5, and only when live blocks showed
            # up re-lay at H-7 for top + both borders. Always reserving seven
            # would trim the hints into the controls at small sizes (60x16),
            # where they belong outside the panel, not in it — below what fits
            # the borders the frame falls back to the bare stack instead.
            inner_w = width - 2
            state.grid = grid_geometry(inner_w)
            trial_hits, trial_regions = [], []
            trial = frame_rows(self.fmt, session_path(state), state,
                               inner_w, height - 5,
                               head=self.head_for(state),
                               hits=trial_hits, regions=trial_regions)
            names = {name for name, _, _ in trial_regions}
            two = bool(names & set(PANEL_EXAMPLES))
            inner_h = height - 7 if two else height - 5
            if inner_h < MIN_ROWS:
                # Room for one panel but not all: fall back to the bare frame
                # rather than trimming widgets to buy borders.
                rows_text = frame_rows(self.fmt, session_path(state), state,
                                       width, height,
                                       head=self.head_for(state),
                                       hits=self.hits, regions=self.regions)
            else:
                rows_text = (trial if (inner_h == height - 5) else frame_rows(
                    self.fmt, session_path(state), state, inner_w, inner_h,
                    head=self.head_for(state),
                    hits=self.hits, regions=self.regions))
                if inner_h == height - 5:
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

    def _top_editor_row(self, width: int, state, top_h: int):
        """Header + metadata beside one bordered `editor` panel, or `None`.

        The hue selectors alone get the
        border — header logo and theme metadata stay bare chrome, while the
        chip row plus the three equal HSV bars ride in `Panel("editor")`
        with one cell of inner padding on each side (`EDITOR_PAD_X`). The
        duplicated exact numbers are dropped. The metadata column owns the
        top's one control: the flat `themes` button under the theme subject,
        a mouse mirror of `t` through the same `apply_key` call — the
        column's last row is blank fill anyway, so the button costs no row
        and the row budget never moves. Returns `(widget, screen_h)`;
        `None` where the columns do not fit.
        """
        slots = state.slots
        sel = state.sel
        label = self.head_for(state) or self.fmt
        path = session_path(state)
        try:
            meta_rows, head, meta_w = top_editor_meta(label, path, slots,
                                                     sel)
            head_w = max(visible(row) for row in head)
        except Exception:
            return None
        value = slots.get(SLOTS[sel], MISSING)
        single_chrome = len("hue") + 1 + HSV_FIELD + 2 + 1
        for left_w in (BANNER_LEFT_W, MINI_LEFT_W, TOP_LEFT_W):
            editor_outer = width - left_w - meta_w
            if editor_outer < single_chrome + 7 + 2 + 2 * EDITOR_PAD_X:
                continue
            left_raw = top_left_rows(slots, left_w)
            if visible(left_raw[0]) > left_w and len(left_raw) > 1:
                continue
            if left_w == TOP_LEFT_W and len(left_raw) != 1:
                continue
            if left_w != TOP_LEFT_W and len(left_raw) == 1:
                continue
            content_w = editor_outer - 2 - 2 * EDITOR_PAD_X
            if content_w < head_w:
                continue
            bar_w = content_w - single_chrome
            if bar_w < 7:
                continue
            try:
                hsv_rows = [hsv_axis(slots, value, axis, bar_w)
                            for axis in range(3)]
            except IndexError:
                continue
            if any(not row for row in hsv_rows):
                continue
            editor_rows = head + hsv_rows
            top_screen = max(top_h, 7, len(left_raw))
            inner_h = top_screen - 2
            if inner_h < len(editor_rows):
                continue
            header = [backdrop(line, slots, left_w) for line in left_raw]
            header += [backdrop("", slots, left_w)] * max(0, top_screen - len(header))
            header = header[:top_screen]
            meta = [backdrop(line, slots, meta_w) for line in meta_rows]
            meta += [backdrop("", slots, meta_w)] * max(0, top_screen - 1 - len(meta))
            meta = meta[:top_screen - 1]
            hsv = [backdrop(line, slots, content_w)
                   for line in editor_rows]
            hsv += [backdrop("", slots, content_w)] * max(0, inner_h - len(hsv))
            hsv = hsv[:inner_h]
            header_frame = Frame(header, left_w, name="header")
            header_frame.styles.width = left_w
            header_frame.styles.height = top_screen
            header_frame.styles.padding = 0
            header_frame.styles.margin = 0
            meta_frame = Frame(meta, meta_w, name="selected")
            meta_frame.styles.width = meta_w
            meta_frame.styles.height = top_screen - 1
            meta_frame.styles.padding = 0
            meta_frame.styles.margin = 0
            # The top's one control in this arrangement: the same flat
            # `themes` button the `info` panel owns, under the theme
            # subject in the metadata column. Never focusable, theme-closed
            # by the same CSS, and exactly what `t` does through the same
            # `apply_key` call — see `ThemesButton` and `on_button_pressed`.
            button = ThemesButton()
            button.styles.width = meta_w
            button.styles.height = 1
            button.styles.margin = 0
            button.styles.padding = 0
            meta_col = Vertical(meta_frame, button)
            meta_col.styles.width = meta_w
            meta_col.styles.height = top_screen
            meta_col.styles.padding = 0
            meta_col.styles.margin = 0
            hsv_frame = Frame(hsv, content_w, name="editor-hsv")
            hsv_frame.styles.width = content_w
            hsv_frame.styles.height = inner_h
            hsv_frame.styles.padding = 0
            hsv_frame.styles.margin = 0
            editor_panel = Panel(EDITOR_TITLE, hsv_frame, name="editor")
            editor_panel.styles.width = editor_outer
            editor_panel.styles.height = top_screen
            editor_panel.styles.padding = (0, EDITOR_PAD_X)
            editor_panel.styles.margin = 0
            row = Horizontal(header_frame, meta_col, editor_panel)
            row.styles.width = width
            row.styles.height = top_screen
            row.styles.padding = 0
            row.styles.margin = 0
            return row, top_screen
        return None

    def _top_screen_height(self, width: int, state, top_h: int) -> int:
        """Screen rows the top will occupy, without building it.

        Mirrors `_top_side_row`'s viability ladder so `_try_side` can size
        the middle before mounting anything.
        """
        if (editor_panel_enabled()
                and os.environ.get("HUEBOX_TOP", "1") != "0"):
            try:
                label = self.head_for(state) or self.fmt
                _meta, head, meta_w = top_editor_meta(label, session_path(state),
                                                      state.slots, state.sel)
                core_w = max(visible(row) for row in head)
            except Exception:
                _meta, meta_w, core_w = None, 10 ** 9, 10 ** 9
            single_chrome = len("hue") + 1 + HSV_FIELD + 2 + 1
            for left_w in (BANNER_LEFT_W, MINI_LEFT_W, TOP_LEFT_W):
                editor_outer = width - left_w - meta_w
                if editor_outer < single_chrome + 7 + 2 + 2 * EDITOR_PAD_X:
                    continue
                if editor_outer - 2 - 2 * EDITOR_PAD_X < core_w:
                    continue
                try:
                    left_raw = top_left_rows(state.slots, left_w)
                except Exception:
                    continue
                if visible(left_raw[0]) > left_w and len(left_raw) > 1:
                    continue
                if left_w == TOP_LEFT_W and len(left_raw) != 1:
                    continue
                if left_w != TOP_LEFT_W and len(left_raw) == 1:
                    continue
                top_screen = max(top_h, 7, len(left_raw))
                if top_screen - 2 < 5:
                    continue
                return top_screen
        if top_bordered_enabled():
            try:
                viable = top_side_panels(state.slots,
                                         self.head_for(state) or self.fmt,
                                         session_path(state), state.sel,
                                         width - 2)
            except Exception:
                viable = None
            if viable is not None:
                return top_h + 2
        try:
            viable = top_side_panels(state.slots,
                                     self.head_for(state) or self.fmt,
                                     session_path(state), state.sel,
                                     width - 4)
        except Exception:
            viable = None
        if viable is not None:
            return top_h + 3
        return top_h

    def _top_side_row(self, width: int, state, top_h: int):
        """The top as two bordered panels side by side, or `None` (§8.1).

        Logo left, theme subject plus the selected readout right (`info`) —
        the same rows `draw_editor` paints, asked at the panels' own widths
        through `top_side_panels` rather than re-rendered, so a click and a
        swatch cannot disagree. Both panels
        are padded in the buffer's own background to `top_h` (the height the
        frame laid out). Bordered: two `Panel`s in a `Horizontal` — `logo`
        and `info` — chrome, never controls (§4.3.2), except the one control
        the top owns: a flat `themes` button riding under the readout in the
        info panel, a mouse mirror of `t` through the same `apply_key` call.
        Asked at `width - 4` so the pair fits inside both borders; the pair
        costs three rows (two borders, one button).

        Prototype (`HUEBOX_TOP_BORDER=1`): the same `Horizontal` wrapped in
        one bordered `Panel(TOP_TITLE)`, so header + selected share the
        same chrome as the controls/examples below. Asked at `width - 2`
        so the pair fits inside the border; the panel costs two rows.

        Default (see `editor_panel_enabled`): header + metadata stay bare and
        only the three HSV bars ride in `Panel("editor")` beside them.
        `HUEBOX_TOP=0` pins the stacked header past it. Falls back to the
        logo/info pair below where the columns do not fit.
        Returns `(widget, screen_h)`; `(None, top_h)` where no top fits.
        """
        if (editor_panel_enabled()
                and os.environ.get("HUEBOX_TOP", "1") != "0"):
            built = self._top_editor_row(width, state, top_h)
            if built is not None:
                return built
        if top_bordered_enabled():
            inner = width - 2
            top = top_side_panels(state.slots,
                                  self.head_for(state) or self.fmt,
                                  session_path(state), state.sel, inner)
            if top is None:
                return None, top_h
            left_w, right_w, left_raw, right_raw = top
            slots = state.slots
            left = [backdrop(line, slots, left_w) for line in left_raw]
            right = [backdrop(line, slots, right_w) for line in right_raw]
            left += [backdrop("", slots, left_w)] * max(0, top_h - len(left))
            right += [backdrop("", slots, right_w)] * max(0, top_h - len(right))
            left, right = left[:top_h], right[:top_h]
            left_frame = Frame(left, left_w, name="header")
            left_frame.styles.width = left_w
            left_frame.styles.height = top_h
            left_frame.styles.padding = 0
            left_frame.styles.margin = 0
            right_frame = Frame(right, right_w, name="selected")
            right_frame.styles.width = right_w
            right_frame.styles.height = top_h
            right_frame.styles.padding = 0
            right_frame.styles.margin = 0
            row = Horizontal(left_frame, right_frame)
            row.styles.width = inner
            row.styles.height = top_h
            row.styles.padding = 0
            row.styles.margin = 0
            panel = Panel(TOP_TITLE, row, name="top")
            panel.styles.width = width
            panel.styles.height = top_h + 2
            panel.styles.padding = 0
            panel.styles.margin = 0
            return panel, top_h + 2
        top = top_side_panels(state.slots, self.head_for(state) or self.fmt,
                              session_path(state), state.sel, width - 4)
        if top is None:
            return None, top_h
        left_w, right_w, left_raw, right_raw = top
        slots = state.slots
        left = [backdrop(line, slots, left_w) for line in left_raw]
        right = [backdrop(line, slots, right_w) for line in right_raw]
        left += [backdrop("", slots, left_w)] * max(0, top_h - len(left))
        right += [backdrop("", slots, right_w)] * max(0, top_h - len(right))
        left, right = left[:top_h], right[:top_h]
        # The button's row: one blank of the buffer's own background under the
        # logo, so both panels stand the same height and the pair below rides
        # where the frame laid it out.
        left_full = left + [backdrop("", slots, left_w)]
        left_frame = Frame(left_full, left_w, name="header")
        left_frame.styles.width = left_w
        left_frame.styles.height = top_h + 1
        left_frame.styles.padding = 0
        left_frame.styles.margin = 0
        right_frame = Frame(right, right_w, name="selected")
        right_frame.styles.width = right_w
        right_frame.styles.height = top_h
        right_frame.styles.padding = 0
        right_frame.styles.margin = 0
        button = ThemesButton()
        button.styles.height = 1
        button.styles.margin = 0
        button.styles.padding = 0
        logo_panel = Panel(PANEL_TITLES["logo"], left_frame, name="logo")
        logo_panel.styles.width = left_w + 2
        logo_panel.styles.height = top_h + 3
        logo_panel.styles.padding = 0
        logo_panel.styles.margin = 0
        info_panel = Panel(PANEL_TITLES["info"], right_frame, button,
                           name="info")
        info_panel.styles.width = right_w + 2
        info_panel.styles.height = top_h + 3
        info_panel.styles.padding = 0
        info_panel.styles.margin = 0
        row = Horizontal(logo_panel, info_panel)
        row.styles.width = width
        row.styles.height = top_h + 3
        row.styles.padding = 0
        row.styles.margin = 0
        return row, top_h + 3

    def _mount_panels(self, width: int, rows_text: list, named: list) -> None:
        """Stack the frame's blocks into bordered panels.

        `named` is `draw_editor`'s own `(name, first, count)` map at the inner
        size, so the groups cannot drift from what the frame painted: the
        same rule as `hits`. The top rides as `logo` + `info` side by side;
        controls and examples each get a `Panel` with a themed border and
        title. Inner blocks keep the inner width; chrome blocks are re-backed
        to the full width, because a row backed to the inner width and padded
        by the widget would leave two columns on the terminal's own background
        (§8.2) — the pad has no style of its own.
        """
        for child in list(self.query(Panel)):
            child.remove()
        for child in list(self.query(Frame)):
            child.remove()
        for child in list(self.query(Live)):
            child.remove()
        for child in list(self.query(Horizontal)):
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

        def mount_inner(name, first, count):
            """One block inside a panel, collapsible where the bare stack is.

            A live block rides in a `Live` whose header stands in for the
            title row — the same construction as `_mount_collapsible`, so
            an open block costs exactly its rows and a shut one its header.
            The shut rows are all below the controls, and the live area
            names no slot, so the click map below cannot land anywhere new.
            """
            if name in LIVE_BLOCKS and collapsible_enabled():
                body = rows_text[first + 1:first + count]
                kind = BLOCK_WIDGETS.get(name, Frame)
                child = kind(body, inner_w, name=name)
                child.styles.width = inner_w
                child.styles.height = len(body)
                child.styles.padding = 0
                child.styles.margin = 0
                shut = name in self._collapsed
                return (Live(LIVE_TITLES[name], child, collapsed=shut,
                              name=name),
                        1 if shut else count)
            return mount_block(name, first, count, inner_w), count

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
        # Panel heights follow what mounted, not what the frame drew: a
        # shut live block costs its header, so the panel shrinks and the
        # chrome below rides up to the rows that remain.
        control_inners = [mount_inner(n, f, c) for n, f, c in controls]
        example_inners = [mount_inner(n, f, c) for n, f, c in examples]
        controls_h = sum(h for _, h in control_inners)
        examples_h = sum(h for _, h in example_inners)
        # Screen geometry for clicks: borders are single rows. The top is two
        # bordered panels sharing one row budget (`top_screen`: two borders
        # plus the `themes` button's row); each panel below adds a top and a
        # bottom border row.
        top_row, top_screen = self._top_side_row(width, self.state,
                                                 header_h)
        self._panel_geom = {
            "inner_w": inner_w,
            "header_h": header_h,
            "controls_h": controls_h,
            "examples_h": examples_h,
            "has_examples": bool(examples),
            "top_bordered": top_bordered_enabled(),
            "top_screen": top_screen if top_row is not None else header_h,
        }
        if top_row is not None:
            # §8.1 (decision 38) — the logo and the theme plus selected
            # readout stand side by side in two bordered panels: `logo`
            # left, `info` right, padded to the height the frame laid out so
            # everything below rides where it did and the click map never
            # moves. A click in the top selects nothing, because the top
            # names no slot (§4.3.2).
            self.mount(top_row)
        else:
            for name, first, count in header:
                self.mount(mount_block(name, first, count, width))
        for name, first, count in extra:
            # `extra` unknown rows sit with the header chrome: full-width,
            # never inside a panel whose title would misname them.
            self.mount(mount_block(name, first, count, width))
        if control_inners:
            inners = [widget for widget, _ in control_inners]
            panel = Panel(PANEL_TITLES["controls"], *inners, name="controls")
            panel.styles.width = width
            panel.styles.height = controls_h + 2
            panel.styles.padding = 0
            panel.styles.margin = 0
            self.mount(panel)
        if example_inners:
            inners = [widget for widget, _ in example_inners]
            panel = Panel(PANEL_TITLES["examples"], *inners, name="examples")
            panel.styles.width = width
            panel.styles.height = examples_h + 2
            panel.styles.padding = 0
            panel.styles.margin = 0
            self.mount(panel)
        for name, first, count in hints:
            self.mount(mount_block(name, first, count, width))

    def _try_side(self, width: int, height: int, state) -> bool:
        """The side-by-side layout, or `False` to keep the stacked one.

        The top stands side by side in two bordered panels (`logo` left,
        theme plus `selected` right as `info`, §8.1 decision 38); the controls
        (palette pairs over interface pairs) and the live blocks in two
        bordered panels next to each other below. The chrome rows come from a
        full-width `draw_editor` run — captured, like everywhere — while the
        panels' contents are `side_left_rows` / `side_live_rows` at their own
        widths. Anything that does not fit (trimmed chrome, a short middle)
        returns `False` before mounting anything, and the stacked layout runs
        instead.
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
        top_h = header_h + sel_h
        top_screen = self._top_screen_height(width, state, top_h)
        content_h = height - top_screen - bottom_h - 2
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
        top_h = header_h + sel_h
        top_row, top_screen = self._top_side_row(width, state, top_h)
        if top_row is not None:
            self.mount(top_row)
        else:
            top_screen = top_h
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
        def right_block(name, first, count):
            # The side panel keeps its height: a shut live block hides
            # its rows behind its header and the panel holds the air, on
            # its own themed background. The left panel fixes the row, so
            # nothing below can ride up the way it does in the stacked
            # panels — and the click geometry above never moves at all.
            rows_here = right[first:first + count]
            if name in LIVE_BLOCKS and collapsible_enabled():
                child = block(name, rows_here[1:], right_inner)
                return Live(LIVE_TITLES[name], child,
                            collapsed=name in self._collapsed, name=name)
            return block(name, rows_here, right_inner)
        right_children = [right_block(name, first, count)
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
        bottom0 = top_screen + content_h + 2
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
        self._side_geom = {"top": top_screen,
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
        top_screen = geom.get("top_screen", header_h)
        if top_screen != header_h or geom.get("top_bordered"):
            # Bordered top or editor panel: the whole top zone is chrome
            # (clicks there select nothing), then everything below rides
            # `top_screen - header_h` rows lower than the bare header.
            if sy < top_screen:
                return None
            y = sy - top_screen
        else:
            # Header chrome: full-width, no offset. Columns past the inner
            # width are pad and hit nothing, which `slot_at` says as None.
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
        # above it. Borders above = 2 per panel, plus the top's own extra.
        extra = geom.get("top_screen", header_h) - header_h
        return sx, sy - 2 * panels - extra

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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """The `themes` button: exactly what `t` does, through the same call.

        A click is a keypress (§4.3.2): the button resolves to the key surface
        rather than re-implementing the picker — open, blocked-while-dirty,
        or the no-library status — and `redraw` hands focus to the picker
        when one opened, the same as `on_key` does after `apply_key`.
        """
        if getattr(event.button, "id", None) != "themes-button":
            return
        event.stop()
        apply_key("t", self.state)
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

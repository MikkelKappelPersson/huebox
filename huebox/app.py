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
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.style import Style
from textual.widget import Widget
from textual.widgets import Button, Collapsible, SelectionList, Static

from . import import_state
from .color import MISSING, SLOTS
from .editor import (EDITOR_PAD_X, INITIAL_SEL, MULT_STEPS, SIDE_LEFT_NARROW_ROWS,
                     SIDE_LEFT_NARROW_W, SIDE_LEFT_ROWS, SIDE_LEFT_THIN_W,
                     SIDE_LEFT_W, EditorState,
                     apply_key, backdrop, draw_editor, enter_setup, grid_geometry,
                     head_label, report_session, session_path, setup_lines, side_grid,
                     side_left_rows, side_live_rows, slot_at, theme_lines,
                     too_small_frame, top_editor_meta, top_layout, top_left_rows,
                     top_subject)
from .preview import (example_lines, interface_pair_rows,
                       palette_interface_rows, palette_rows, sample_lines)
from .render import (CHROME_MUTED, HSV_FIELD, MIN_COLS, MIN_ROWS, chrome,
                     hint_line, hsv_axis, title, visible)

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
               regions=None, indent="  "):
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
    did not draw. `indent` is the content rows' own air (decision 46): the
    stacked panels capture with `""` so the panel's padding is the only
    edge air; the bare frame keeps the default.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        draw_editor(fmt, path, state.slots, state.sel, state.undo,
                    state.status, state.mult, head=head, hits=hits,
                    regions=regions, size=(cols, rows), indent=indent)
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

    def update_rows(self, rows_text, width=None):
        """Repaint this block in place: new rows, same widget.

        The import popup's preview and footer repaint on every cursor move
        and toggle; removing and remounting a widget per repaint would churn
        focus and scroll, so the rows (and their parse) are swapped under the
        mounted widget instead. Editor blocks never call this — their rows
        are rebuilt by `redraw`.
        """
        self.rows_text = rows_text
        self._cache = [Text.from_ansi(row) for row in rows_text]
        self.styles.height = len(rows_text)
        if width is not None:
            self.styles.width = width
        self.refresh()

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


class SetupScreen(ModalScreen):
    """The first-run choice as a modal popup over the editor (empty library).

    The dialog's rows are `editor.setup_lines` at the dialog width — the same
    rows the headless session draws full-frame, so the two cannot disagree
    about what the choice says. Behaviour stays in `EditorState` + `apply_key`
    (the `import_state`/`ImportScreen` split, repeated): keys arrive already
    translated from `Editor.on_key` and go straight to the one key surface,
    and every outcome already lives on the state (`quit`, `import_pending`,
    or the choice simply closing on create). The dialog is positioned with
    explicit margins rather than `align`, so its origin is exactly what
    `click_at` recomputes — no stored geometry to drift. Import opens only
    after this dismisses, so modals never stack; a popup closed still empty
    puts the choice back up (the app's `_setup_closed` re-opens it).
    """

    #: The dialog's content width: holds the longest choice row with the hint
    #: footer folded to two lines. Clamped to narrow screens in `_fill`.
    DIALOG_W = 44

    #: The dialog content's narrowest honest width: below this even the
    #: choice rows clip, so the dialog never shrinks past it.
    DIALOG_MIN_W = 24

    DEFAULT_CSS = """
    SetupScreen {
        /* No background of its own: the modal dim (`$background` at 60%)
        stays, so the editor frame shows through behind the dialog.
        Painting this opaque would hide the session the choice belongs
        to — which is exactly what the first version did. */
        padding: 0;
    }
    SetupScreen .setup-dialog {
        background: $background;
        border: round $border;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._dlg_w = 0               # content width from the last `_fill`
        self._dlg_h = 0               # content height from the last `_fill`
        self._hits: list = []         # their clickable cells, content coords
        self._body = None             # the `Frame` carrying the rows
        self._dialog = None           # the bordered container

    def compose(self) -> ComposeResult:
        # Nothing here: the dialog's width comes from the compositor, and
        # the size it reports decides the clamp. Shells mount in `_fill`
        # once the tree exists — the same shape as `Editor.compose`.
        return iter(())

    def on_mount(self) -> None:
        self.call_after_refresh(self._fill)

    def on_resize(self, event) -> None:
        # The rows are fixed-width, but the clamp and the centering are
        # not: re-lay rather than repaint.
        if not self.is_running:
            return
        self.call_after_refresh(self._fill)

    def _origin(self):
        """The dialog's top-left, in screen coords.

        The dialog is the screen's only child and carries its centering as
        explicit margins (set in `_fill`), so this recomputes to exactly
        where it was mounted — the click map never drifts from the paint.
        """
        width, height = self.size
        return ((width - (self._dlg_w + 2)) // 2,
                (height - (self._dlg_h + 2)) // 2)

    def _fill(self) -> None:
        """Mount the dialog at the current size, centered with margins."""
        for child in list(self.children):
            child.remove()
        st = self.app.state
        width, _height = self.size
        dlg_w = max(self.DIALOG_MIN_W,
                    min(self.DIALOG_W, width - 2))
        self._hits = []
        rows = [backdrop(line, st.slots, dlg_w)
                for line in setup_lines(st.setup or 0, dlg_w, 99,
                                        st.status, st.slots, hits=self._hits,
                                        name=st.setup_name)]
        self._dlg_w = dlg_w
        self._dlg_h = len(rows)
        body = Frame(rows, dlg_w, name="setup-body")
        body.styles.width = dlg_w
        body.styles.height = len(rows)
        body.styles.padding = 0
        body.styles.margin = 0
        dialog = Vertical(body, classes="setup-dialog")
        dialog.styles.width = dlg_w + 2
        dialog.styles.height = len(rows) + 2
        dialog.styles.padding = 0
        ox, oy = self._origin()
        dialog.styles.margin = (oy, 0, 0, ox)
        self.mount(dialog)
        self._body = body
        self._dialog = dialog

    def _repaint(self) -> None:
        """Repaint the dialog in place: new rows, same widgets.

        The choice never moves rows (only the `>` mark and the status line),
        so the dialog keeps its size and its margins — a repaint cannot
        misplace a click.
        """
        if self._body is None or not self._body.is_mounted:
            return              # mounts still queued; `_fill` paints them
        st = self.app.state
        self._hits = []
        rows = [backdrop(line, st.slots, self._dlg_w)
                for line in setup_lines(st.setup or 0, self._dlg_w, 99,
                                        st.status, st.slots, hits=self._hits,
                                        name=st.setup_name)]
        self._body.update_rows(rows, self._dlg_w)

    def setup_key(self, key: str) -> None:
        """One keypress while this screen is top, from `Editor.on_key`.

        The key arrives translated; everything routes to the one key
        surface. Terminal outcomes (`quit`, `import_pending`, or the choice
        closing on create) dismiss — the app routes from the state — and
        anything else repaints the dialog in place.
        """
        st = self.app.state
        apply_key(key, st)
        if st.quit or st.import_pending or st.setup is None:
            self.dismiss(None)
        else:
            self._repaint()

    def click_at(self, fx: int, fy: int) -> None:
        """A click while this screen is top: a choice row confirms.

        The origin is derived live from the screen size, so resizes need no
        stored geometry. A click on the border or outside the dialog is
        chrome and selects nothing — like every other frame, clicking
        nothing is not an error.
        """
        ox, oy = self._origin()
        lx, ly = fx - ox - 1, fy - oy - 1
        if not (0 <= lx < self._dlg_w and 0 <= ly < self._dlg_h):
            return
        target = slot_at(self._hits, lx, ly)
        if target is None:
            return
        st = self.app.state
        st.setup = target
        self.setup_key("\r")


class ThemesScreen(ModalScreen):
    """The theme picker as a modal popup over the editor (same kind as setup).

    The dialog's rows are `editor.theme_lines` at the dialog width — the same
    rows the headless session draws full-frame, so the two cannot disagree
    about what the list says. Behaviour stays in `EditorState` + `apply_key`
    (the `SetupScreen` split, repeated): keys arrive already translated from
    `Editor.on_key` and go straight to the one key surface, and closing is
    the state going `overlay is None` — the app repaints the editor behind
    it. The dialog is positioned with explicit margins rather than `align`,
    so its origin is exactly what `click_at` recomputes — no stored geometry
    to drift. Takeovers never stack: the setup choice and the import popup
    each own the surface their own way, and opening here re-checks both.
    """

    #: The dialog's content width: holds a theme row with the hint footer
    #: folded to three lines. Clamped to narrow screens in `_fill` — the
    #: same clamp as `SetupScreen`, so the two popups read as one kind.
    DIALOG_W = 44

    #: The dialog content's narrowest honest width: below this even the
    #: choice rows clip, so the dialog never shrinks past it.
    DIALOG_MIN_W = 24

    #: The dialog content's tallest honest height: past this the list scrolls
    #: by selection (`theme_lines` windows it) instead of growing the dialog
    #: — the popup stays over the UI with the dim visible, rather than
    #: filling the screen and reading as a takeover. Clamped to short screens
    #: in `_fill`.
    DIALOG_MAX_H = 18

    DEFAULT_CSS = """
    ThemesScreen {
        /* No background of its own: the modal dim (`$background` at 60%)
        stays, so the editor frame shows through behind the dialog.
        Painting this opaque would hide the session the list belongs
        to — the same rule as `SetupScreen`. */
        padding: 0;
    }
    ThemesScreen .themes-dialog {
        background: $background;
        border: round $border;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._dlg_w = 0               # content width from the last `_fill`
        self._dlg_h = 0               # content height from the last `_fill`
        self._max_h = self.DIALOG_MAX_H  # list budget from the last `_fill`
        self._hits: list = []         # their clickable cells, content coords
        self._body = None             # the `Frame` carrying the rows
        self._dialog = None           # the bordered container

    def compose(self) -> ComposeResult:
        # Nothing here: the dialog's width comes from the compositor, and
        # the size it reports decides the clamp. Shells mount in `_fill`
        # once the tree exists — the same shape as `Editor.compose`.
        return iter(())

    def on_mount(self) -> None:
        self.call_after_refresh(self._fill)

    def on_resize(self, event) -> None:
        # The rows are fixed-width, but the clamp and the centering are
        # not: re-lay rather than repaint.
        if not self.is_running:
            return
        self.call_after_refresh(self._fill)

    def _origin(self):
        """The dialog's top-left, in screen coords.

        The dialog is the screen's only child and carries its centering as
        explicit margins (set in `_fill`), so this recomputes to exactly
        where it was mounted — the click map never drifts from the paint.
        """
        width, height = self.size
        return ((width - (self._dlg_w + 2)) // 2,
                (height - (self._dlg_h + 2)) // 2)

    def _fill(self) -> None:
        """Mount the dialog at the current size, centered with margins."""
        for child in list(self.children):
            child.remove()
        st = self.app.state
        width, height = self.size
        dlg_w = max(self.DIALOG_MIN_W,
                    min(self.DIALOG_W, width - 2))
        max_h = min(self.DIALOG_MAX_H, max(6, height - 2))
        self._max_h = max_h
        picker = st.picker_frame()
        if picker is None:
            names, index, current = [], 0, ""
        else:
            names, index, current = picker
        self._hits = []
        rows = [backdrop(line, st.slots, dlg_w)
                for line in theme_lines(names, index, current, dlg_w,
                                         max_h, st.status, st.slots,
                                         hits=self._hits)]
        self._dlg_w = dlg_w
        self._dlg_h = len(rows)
        body = Frame(rows, dlg_w, name="themes-body")
        body.styles.width = dlg_w
        body.styles.height = len(rows)
        body.styles.padding = 0
        body.styles.margin = 0
        dialog = Vertical(body, classes="themes-dialog")
        dialog.styles.width = dlg_w + 2
        dialog.styles.height = len(rows) + 2
        dialog.styles.padding = 0
        ox, oy = self._origin()
        dialog.styles.margin = (oy, 0, 0, ox)
        self.mount(dialog)
        self._body = body
        self._dialog = dialog

    def _repaint(self) -> None:
        """Repaint the dialog in place: new rows, same widgets.

        The list window moves with the selection (`theme_lines` owns it),
        so the dialog keeps its width and its budget — only the height
        follows the rows (a status line arriving or leaving), with the
        margins re-centered so the click map never drifts.
        """
        if self._body is None or not self._body.is_mounted:
            return              # mounts still queued; `_fill` paints them
        st = self.app.state
        picker = st.picker_frame()
        if picker is None:
            return              # closed underneath; dismiss owns it
        names, index, current = picker
        self._hits = []
        rows = [backdrop(line, st.slots, self._dlg_w)
                for line in theme_lines(names, index, current,
                                         self._dlg_w, self._max_h,
                                         st.status, st.slots,
                                         hits=self._hits)]
        self._body.update_rows(rows, self._dlg_w)
        if len(rows) != self._dlg_h:
            self._dlg_h = len(rows)
            if self._dialog is not None:
                self._dialog.styles.height = len(rows) + 2
                ox, oy = self._origin()
                self._dialog.styles.margin = (oy, 0, 0, ox)

    def themes_key(self, key: str) -> None:
        """One keypress while this screen is top, from `Editor.on_key`.

        The key arrives translated; everything routes to the one key
        surface. Closing is the state going `overlay is None` (Enter picked
        a theme, Esc/`t`/`Q` put the editor back) — the app repaints from
        the state — and anything else repaints the dialog in place.
        """
        st = self.app.state
        apply_key(key, st)
        if st.quit or st.overlay is None:
            self.dismiss(None)
        else:
            self._repaint()

    def click_at(self, fx: int, fy: int) -> None:
        """A click while this screen is top: a theme row opens it.

        The origin is derived live from the screen size, so resizes need no
        stored geometry. A click on the border or outside the dialog is
        chrome and selects nothing — like every other frame, clicking
        nothing is not an error. A row moves the selection onto it and
        confirms through the one key surface, so a click and Enter cannot
        diverge.
        """
        ox, oy = self._origin()
        lx, ly = fx - ox - 1, fy - oy - 1
        if not (0 <= lx < self._dlg_w and 0 <= ly < self._dlg_h):
            return
        target = slot_at(self._hits, lx, ly)
        if target is None:
            return
        st = self.app.state
        if st.overlay is None:
            return
        st.overlay_index = target
        self.themes_key("\r")


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
PANEL_TITLES = {"controls": "THEME", "examples": "EXAMPLES"}

#: The side-by-side layout: `selected` full-width above, the controls and
#: the live blocks in two panels next to each other below. The left panel
#: is `SIDE_LEFT_W` of content plus two border columns and the right takes
#: the rest, so the full pair starts where both stay usable (W>=100). Past
#: that the squeezed pair (§8.1, decision 42) keeps the layout mounted:
#: the same pairs in abbreviated cells (`SIDE_LEFT_NARROW_W` + two border
#: columns), and the right keeps the 48 columns it has at 100 — so the pair
#: stands down to W>=80 and narrower windows keep the stacked panels.
#: `selected` is bare chrome above the pair, not a third panel: a two-row
#: readout needs no border.
SIDE_MIN_W = 100
LEFT_OUTER_W = SIDE_LEFT_W + 2
#: The squeezed pair stands at 80 and up, where the narrow left still
#: leaves the right past the 48 columns it must hold. The thin pair
#: (decision 43: narrow rows, hex hidden) stands wherever the window
#: itself stands: every right-panel row is clipped to its width by
#: construction (`backdrop`, `pack`/`clip`), so the live blocks fold
#: instead of overflowing and there is no width worth falling back for.
SIDE_NARROW_MIN_W = 80
LEFT_NARROW_OUTER_W = SIDE_LEFT_NARROW_W + 2
SIDE_THIN_MIN_W = MIN_COLS
LEFT_THIN_OUTER_W = SIDE_LEFT_THIN_W + 2

#: Panel size bounds (§15.7). Each bordered panel keeps a minimum and a
#: maximum width and height, so the frame stops reflowing once the window
#: leaves the useful range: below a minimum the frame falls back to the bare
#: stack (or the too-small hint), above a maximum the extra columns and rows
#: stay the buffer's own background fill instead of stretching the content.
#: The globals bound the whole layout (`redraw` lays out at
#: `min(size, max)`); `PANEL_LIMITS` bounds each named panel, enforced
#: through Textual's own `min-width` / `max-width` / `min-height` /
#: `max-height` so the compositor holds them on resize too. Values stay
#: clear of the tested sizes (up to 150x50) so capping is a no-op there:
#: 150 columns still fill, 200 do not, and the scroll guarantees keep
#: holding either way.
PANEL_MIN_W = MIN_COLS + 2              # 42: borders around the 40-col floor
PANEL_MAX_W = 160                     # past this extra columns are fill
PANEL_MIN_H = 3                       # borders around a single row
PANEL_MAX_H = 50                      # past this extra rows are fill
PANEL_LIMITS = {
    # name: (min_w, max_w, min_h, max_h). Stacked panels share the
    # layout bounds; the top pair splits the same row budget, so their
    # maxima are generous upper bounds — the row total is what caps them.
    "controls": (PANEL_MIN_W, PANEL_MAX_W, 5, PANEL_MAX_H),
    "examples": (PANEL_MIN_W, PANEL_MAX_W, 4, PANEL_MAX_H),
    "editor": (30, 120, 5, 12),
    "top": (PANEL_MIN_W, PANEL_MAX_W, 4, 12),
}


def panel_limits_enabled() -> bool:
    """Whether panel min/max bounds apply (§15.7).

    Default on: each `Panel` carries its `PANEL_LIMITS` entry as Textual
    `min-*` / `max-*` styles and the layout is computed at
    `min(size, max)` so content stops reflowing past the maxima.
    `HUEBOX_PANEL_LIMITS=0` opts out back to the unbounded layout (tests
    use it where they assert full-window fill past the maxima).
    """
    return os.environ.get("HUEBOX_PANEL_LIMITS", "1") != "0"


def clamp_panel(name: str, width: int, height: int) -> tuple:
    """Clamp a panel's `(width, height)` to its `PANEL_LIMITS` entry.

    Pure: the same bounds `Panel` enforces through styles, as numbers a
    headless test can assert without a compositor. Unknown names take the
    generic layout bounds.
    """
    min_w, max_w, min_h, max_h = PANEL_LIMITS.get(
        name, (PANEL_MIN_W, PANEL_MAX_W, PANEL_MIN_H, PANEL_MAX_H))
    return (min(max(width, min_w), max_w),
            min(max(height, min_h), max_h))


def layout_size(width: int, height: int) -> tuple:
    """The size the frame is laid out at: `min(size, max)` (§15.7).

    Within the maxima this is identity, so every tested size lays out
    exactly as before; past them the grid, the folds and the row budget
    stop moving and the extra screen stays fill. Disabled with the panels'
    own opt-out so `HUEBOX_PANEL_LIMITS=0` restores the unbounded layout.
    """
    if not panel_limits_enabled():
        return width, height
    return min(width, PANEL_MAX_W), min(height, PANEL_MAX_H)


def panels_enabled() -> bool:
    """Whether the panel layout is on.

    Default on: the frame is two bordered panels, not a bare stack.
    `HUEBOX_PANELS=0` opts out back to the frameless stack (tests and the
    harness use it where they assert the bare rows, never as product).
    """
    return os.environ.get("HUEBOX_PANELS", "1") != "0"


#: Breathing room inside every bordered panel (§8.1, decision 45): one cell
#: of air on the left and right, so content never touches a border — the
#: frame's own rows carry a two-column indent on the left and nothing on the
#: right, which reads as padding on one side only. Vertical air would come
#: 1:1 out of the widgets, so panels take horizontal air only. Padding is
#: the first thing the layout spends when the window shrinks: panels pad iff
#: the padded layout still mounts everything the unpadded one does, else
#: they mount unpadded, else the bare stack stands.
PANEL_PAD = 1
#: A padded panel needs the draw floor plus its own air: borders plus
#: `PANEL_PAD` air on each side, so the content still holds `MIN_COLS`.
PANEL_PAD_MIN_W = MIN_COLS + 2 + 2 * PANEL_PAD


def panel_pad_enabled() -> bool:
    """Whether panels pad their content (decision 45).

    Default on: every `Panel` carries `PANEL_PAD` on the left and right.
    `HUEBOX_PANEL_PAD=0` keeps the unpadded panels at any size — the same
    rows, touching the borders; tests pin whichever chrome they assert.
    """
    return os.environ.get("HUEBOX_PANEL_PAD", "1") != "0"


EDITOR_TITLE = "EDITOR"


def editor_panel_enabled() -> bool:
    """Whether the compositor top is laid out, or left bare (§8.1).

    Default on: the top is bare logo beside the flexible info column and
    one bordered `Panel("EDITOR")` holding the selected readout plus the
    three HSV bars (`editor.top_layout` shares the widths, so info shrinks
    with the window instead of holding air while the editor squeezes).
    `HUEBOX_EDITOR_PANEL=0` keeps the bare stack top at any width — the
    same rows `draw_editor` paints, with no compositor chrome and no
    `themes` button; tests pin whichever top they assert.
    """
    return os.environ.get("HUEBOX_EDITOR_PANEL", "1") != "0"


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
        border-title-style: bold;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self, title: str, *children: Widget, name=None, **kwargs):
        super().__init__(*children, name=name, **kwargs)
        self.border_title = title
        if panel_limits_enabled():
            # Each panel carries its own minimum and maximum length and
            # width (§15.7): the compositor holds them on resize, so a
            # panel never squeezes below usable nor stretches past useful.
            # Explicit `styles.width` / `height` set at mount stay the
            # layout's ask; these are the bounds around it.
            min_w, max_w, min_h, max_h = PANEL_LIMITS.get(
                name or "", (PANEL_MIN_W, PANEL_MAX_W,
                               PANEL_MIN_H, PANEL_MAX_H))
            self.styles.min_width = min_w
            self.styles.max_width = max_w
            self.styles.min_height = min_h
            self.styles.max_height = max_h


class ThemesButton(Button):
    """The `Themes` button: a mouse mirror of `t` (§4.3.2).

    A flat `Button` labelled `Themes`, and the one control the top owns —
    everything else up there is chrome and answers to no click. It rides
    under the theme subject in the top's info column. The flat *look*
    is the CSS below
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
        super().__init__("Themes", id="themes-button",
                         name="themes", **kwargs)


class ImportButton(Button):
    """The `Import` button: a mouse mirror of `i` (003 spec §4.1).

    The info column's second control, directly below `Themes`: the same flat
    contract (never focusable, so the arrows stay on the grids; painted only
    in the theme's own `selection-background` / `selection-foreground`, hover
    as underline) and the same blank-fill row discipline (it rides the
    column's existing fill, so the row budget never moves). Pressing it does
    exactly what `i` does, through the same `Editor.open_import` call — the
    button and the key cannot disagree about what the popup is, the same rule
    `Themes`/`t` follows. Bare-stack mode (`HUEBOX_EDITOR_PANEL=0`) mounts no
    top and keeps no button; `i` still works.
    """

    can_focus = False

    DEFAULT_CSS = """
    ImportButton {
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
    ImportButton:hover {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        text-style: bold underline;
    }
    ImportButton:focus {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        text-style: bold underline;
    }
    ImportButton.-active {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        tint: transparent !important;
    }
    ImportButton:disabled {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
    }
    """

    def __init__(self, **kwargs):
        # Default variant, flat look from the CSS above — the same reason as
        # `ThemesButton`: the flat variant's `color: auto 90%` reroutes the
        # label past the explicit colour, and `auto-color` is not a property
        # any rule can switch back.
        super().__init__("Import", id="import-button",
                         name="import", **kwargs)


class ImportFooterButton(Button):
    """The popup's confirm/cancel: click only, never focus (003 spec §4.7).

    The footer answers to its keys without holding focus, so tab-trapping is
    impossible by construction: `Enter`/`Esc` arrive through the screen-level
    bindings `ImportScreen` owns, and these buttons answer to clicks alone.
    Theme-closed like `ThemesButton`: the default variant's base states name
    tokens `TOKEN_SLOTS` never mapped (`$button-foreground`, derived
    `$surface-*` washes), so every state carries the selection pair with
    `!important`, the same armor — hover as underline, never a wash.
    """

    can_focus = False

    DEFAULT_CSS = """
    ImportFooterButton {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        text-style: bold;
        width: auto;
        height: 1;
        min-width: 12;
        margin: 0 1;
        padding: 0 1;
        content-align: center middle;
    }
    ImportFooterButton:hover {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        text-style: bold underline;
    }
    ImportFooterButton:focus {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        text-style: bold;
    }
    ImportFooterButton.-active {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
        tint: transparent !important;
    }
    ImportFooterButton:disabled {
        background: $screen-selection-background !important;
        color: $screen-selection-foreground !important;
        border: none !important;
    }
    """


#: The live blocks, each collapsible on its own (§14.1): the strip, the
#: hunk and the code sample. Three blocks, three toggles — a collapsed strip
#: never takes the diff and the sample with it.
LIVE_BLOCKS = ("examples", "diff", "sample")

#: What the compositor calls the live blocks. The strip demonstrates the
#: interface text pairs (background, selection, cursor), so that is what its
#: header says; the bare rows keep `draw_editor`'s own "examples" title, and
#: label is product chrome of the same kind.
LIVE_TITLES = {"examples": "Interface text", "diff": "Live diff",
                "sample": "Live code"}

#: One key per live block. All three are free in `apply_key`, so the toggles
#: never steal a colour key; the picker owns the surface while it is up, so
#: behind it they stay editor keys (no-ops) rather than collapsing the frame
#: the user is reading. Shifted, because the bare letters colour: `w/e`,
#: `s/d` and `x/c` adjust hue, saturation and value.
COLLAPSE_KEYS = {"E": "examples", "D": "diff", "C": "sample"}


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


#: The import popup's list column: wide enough for provider titles plus theme
#: names, narrow enough to leave the preview its palette at `MIN_COLS`.
#: The `#import-left` rule below carries the same number — one width, one
#: place it is derived.
IMPORT_LEFT_W = 34

#: Keys the popup answers itself (screen-level priority bindings) or that a
#: focused `SelectionList` binds natively. `Editor.on_key` steps over all of
#: them while the popup is top — one handler per key. `up`/`down`/`space`
#: need the priority binding (a focused list starves plain screen bindings —
#: reproduced for `enter` in P0, same mechanism); `home`/`end`/`pagedown` /
#: `pageup` stay native within their list, adopted through `highlighted`.
IMPORT_WIDGET_KEYS = ("up", "down", "home", "end", "pagedown",
                      "pageup", "space")

#: The popup footer's hint pairs, painted through `hint_line` like every
#: footer huebox draws.
IMPORT_HINTS = [("space", "toggle"), ("a", "all visible"),
                ("n", "clear"), ("arrows", "move"),
                ("Enter", "import"), ("Esc", "cancel")]


class ImportList(SelectionList):
    """One provider's theme list (003/P4: candidate A, P0 verdict).

    A stock `SelectionList` with one behaviour added: the wheel moves the
    highlight ±1 per detent instead of scrolling the viewport (native scroll
    never moves the highlight — reproduced in the P0 spike), and the event is
    stopped so the list column does not scroll underneath the cursor. The
    highlight change routes through the state (`ImportScreen.scroll_import`),
    so wheel, arrows and clicks share one cursor like the picker does.
    Toggle marks, `space` toggling and the `SelectedChanged` stream stay
    stock: the screen reconciles the state's toggled set from them.
    """

    def on_mouse_scroll_up(self, event) -> None:
        event.stop()
        self.screen.scroll_import(-1)

    def on_mouse_scroll_down(self, event) -> None:
        event.stop()
        self.screen.scroll_import(+1)


def import_preview_rows(name, slots, width):
    """The popup's right column for the highlighted theme (003 spec §4.5).

    Pure like every preview unit: the theme name as a bold-`foreground`
    header, palette plus paired interface on the same two lines where they
    fit (`palette_interface_rows`: `0-7` beside `FG CC SB`, `8-15` beside
    `BG CT SF`), else stacked (`palette_rows` over `interface_pair_rows`)
    — same cells either way, abbreviated with no hex and read-only
    (`sel=-1`) — then the interface-text examples strip (`example_lines`)
    and the live code sample (`sample_lines`). The same units the editor
    frame calls, so the two cannot disagree about what a theme looks like.
    Every row stands on the previewed theme's own `background` (`backdrop`,
    §8.2), never the terminal's. Shedding is tail-first by construction:
    the sample is last, so a short column clips it before the strip, which
    keeps its floor. `{}` slots preview as one muted row rather than the
    last theme's.
    """
    if not slots:
        note = (title(name, slots) if name
                else chrome("no themes found", CHROME_MUTED, slots or {}))
        return [backdrop(note, slots or {}, width)]
    rows = [title(name, slots), ""]
    combined = palette_interface_rows(slots, sel=-1)
    if combined and visible(combined[0]) <= width:
        rows.extend(combined)
    else:
        rows.extend(palette_rows(slots, sel=-1))
        rows.append("")
        rows.extend(interface_pair_rows(slots, sel=-1))
    rows.append("")
    rows.extend(example_lines(slots, cols=width, indent=""))
    code = [line for line, _ in sample_lines(slots)]
    if code and not visible(code[-1]):
        code.pop()               # the lexer's trailing newline, not a line
    rows.append("")
    rows.extend(code)
    return [backdrop(line, slots, width) for line in rows]


class ImportScreen(ModalScreen):
    """The theme-import popup: list left, preview right, confirm footer.

    003 spec §§4–5, P0 verdict followed without re-litigating: one
    `ModalScreen`, pushed with `push_screen` and closed with
    `dismiss(result)` — the themes popup path is untouched alongside it, and
    the app routes popup keys here by guarding on this screen's type (it
    still sees keys under a modal). Inside: one `Collapsible` per provider
    with themes (title `Name (count)`, collapsed set from the state's
    `expanded`; empty providers are omitted, never a `Name (0)` group)
    each holding one `ImportList` of `(display, display)` rows, the preview column
    (`import_preview_rows` in a `Frame` carrier), and a footer of hints plus
    a live count plus confirm/cancel buttons.

    The state (`import_state.ImportState`) owns the behaviour — cursor over
    open groups, expanded set, toggled set, confirm mapping — and this screen
    is a thin shell over it: widget messages reconcile into the state, state
    transitions mirror back out, and exactly one list holds focus, moving
    with the cursor. `Enter` confirms the whole set through the priority
    binding below (a focused list starves plain `enter` — reproduced in P0 —
    and toggle stays `space`'s job alone, so confirm never double-fires).
    """

    BINDINGS = [Binding("enter", "confirm_import", "Import", show=False,
                        priority=True),
                # The cursor keys, preempted: a focused list binds them too,
                # and `Editor.on_key` runs before the widget binding would —
                # so routing them through the app would double-move (native
                # wrap plus state step on one press). Screen-priority bindings
                # run first instead: the state walks the continuous cursor
                # across open groups (single-row edges included — native
                # no-ops there, posting nothing) and the mirror sets
                # `highlighted` + focus together on the landing list.
                Binding("up", "import_up", "", show=False,
                        priority=True),
                Binding("down", "import_down", "", show=False,
                        priority=True),
                Binding("space", "import_toggle", "", show=False,
                        priority=True)]

    # Theme-closure: `$background`/`$foreground` plus the selection pair the
    # picker already wears — every token here is one `TOKEN_SLOTS` maps, no
    # `auto` ink, no derived washes. The modal dim is overridden to the
    # theme's own `background` (Textual's overlay colour never shows), and
    # all four `selection-list--button*` component classes bind theme slots:
    # the box marks the toggled set alone — muted until picked, bright once
    # picked — and the cursor never repaints it: a cursor row keeps the muted
    # box unless toggled, the selection-pair wash carries the cursor.
    DEFAULT_CSS = """
    ImportScreen {
        background: $background;
        overflow: hidden;
    }
    ImportScreen #import-body {
        height: 1fr;
        background: $background;
    }
    ImportScreen #import-left {
        width: 34;
        height: 1fr;
        background: $background;
        padding: 0;
        margin: 0;
    }
    ImportScreen #import-right {
        height: 1fr;
        background: $background;
        padding: 0;
        margin: 0;
    }
    ImportScreen #import-footer {
        height: auto;
        background: $background;
    }
    ImportScreen #import-buttons {
        height: auto;
        background: $background;
    }
    ImportScreen SelectionList {
        background: $background;
        color: $foreground;
        border: none;
        padding: 0;
        margin: 0;
    }
    ImportScreen SelectionList > .selection-list--button {
        background: $background;
        color: $text-muted;
    }
    ImportScreen SelectionList > .selection-list--button-selected {
        background: $background;
        color: $foreground;
        text-style: bold;
    }
    ImportScreen SelectionList > .selection-list--button-highlighted {
        background: $screen-selection-background;
        color: $text-muted;
    }
    ImportScreen SelectionList > .selection-list--button-selected-highlighted {
        background: $screen-selection-background;
        color: $screen-selection-foreground;
        text-style: bold;
    }
    """

    def __init__(self, istate, **kwargs):
        super().__init__(**kwargs)
        self._istate = istate
        # While set, widget messages are echoes of a programmatic mirror,
        # not user input: handlers return early. Every echo is idempotent
        # anyway (handlers read the live widget state and compare against
        # the state), so this is belt, not braces.
        self._syncing = False
        self._lists: dict = {}          # provider -> ImportList
        self._collapsibles: dict = {}   # provider -> Collapsible
        # Last accounted-for highlight per provider: a mounted list posts
        # `SelectionHighlighted` for its initial highlight, and every mirror
        # below posts one per change — all echoes, not user moves. The
        # handler ignores a highlight equalling the echo and adopts anything
        # else, so the cursor can never drift to a list nobody touched.
        self._echo: dict = {}
        self._left = None
        self._right = None
        self._footer = None
        self._hints_frame = None
        self._count_frame = None
        self._preview_pane = None
        self._full = True               # False while the too-small line
                                        # stands in for the popup

    # -- mount ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Nothing here: the popup's width comes from the compositor, and the
        # too-small check needs the laid-out size. Shells mount in `_fill`
        # once the tree exists — the same shape as `Editor.compose`.
        return iter(())

    def on_mount(self) -> None:
        self.call_after_refresh(self._after_mount)

    def _after_mount(self) -> None:
        # Focus after mount, like every other focus this shell places: `mount`
        # is a request, so focusing inline would query an empty tree. The
        # fill mounts first, the sync runs on the refresh after it.
        self._fill()
        self.call_after_refresh(self._sync_all)

    def on_resize(self, event) -> None:
        # The laid-out size arrives a refresh late (the `Editor.on_resize`
        # rule), so re-evaluate on the first moment sizes agree.
        self.call_after_refresh(self._relayout)

    def _editor_slots(self):
        """The live buffer's slots, for popup chrome (frame-typography rule).

        Chrome (titles, hints, counts) is painted from the buffer through
        `chrome()`/`title()` like every frame; the preview column paints
        from the highlighted theme's own slots instead (§4.5).
        """
        return getattr(getattr(self.app, "state", None), "slots", {})

    def _fill(self) -> None:
        """Mount the popup at the current size: full, or the too-small line.

        Below `MIN_COLS`×`MIN_ROWS` the popup keeps the editor's own fallback
        — the `terminal too small` line instead of a squeezed modal, same
        floor, no second constant. Widget state is never carried: lists,
        collapsed flags and selections re-sync from the state afterwards.
        """
        for child in list(self.children):
            child.remove()
        self._lists = {}
        self._collapsibles = {}
        self._echo = {}
        self._left = self._right = self._footer = None
        self._hints_frame = self._count_frame = self._preview_pane = None
        width, height = self.size
        if width < MIN_COLS or height < MIN_ROWS:
            self._full = False
            small = Frame([too_small_frame(width)], width,
                            name="import-small")
            small.styles.width = width
            small.styles.height = 1
            self.mount(small)
            return
        self._full = True
        st = self._istate
        slots = self._editor_slots()
        groups = []
        for provider in st.provider_order:
            ids = st.theme_ids.get(provider, [])
            if not ids:
                continue      # empty providers never appear as groups
            title_text = f"{provider} ({len(ids)})"
            lst = ImportList(*[(st.display[tid], st.display[tid])
                               for tid in ids],
                             id=f"import-list-{provider}")
            self._lists[provider] = lst
            coll = Collapsible(lst, title=title_text,
                               collapsed=(provider not in st.expanded),
                               id=f"import-group-{provider}")
            coll.can_focus = False
            groups.append(coll)
            self._collapsibles[provider] = coll
            self._echo[provider] = lst.highlighted
        if not groups:
            # No provider has themes: one muted row instead of bare air.
            groups = [Static(Text.from_ansi(
                chrome("no themes found", CHROME_MUTED, slots)))]
        left = VerticalScroll(*groups, id="import-left")
        right = Vertical(id="import-right")
        body = Horizontal(left, right, id="import-body")
        hints = Frame(self._hint_rows(width), width, name="import-hints")
        hints.styles.width = width
        hints.styles.height = len(hints.rows_text)
        count = Frame(self._count_rows(width), width, name="import-count")
        count.styles.width = width
        count.styles.height = 1
        confirm = ImportFooterButton("Import", id="import-confirm")
        cancel = ImportFooterButton("Cancel", id="import-cancel")
        buttons = Horizontal(confirm, cancel, id="import-buttons")
        footer = Vertical(hints, count, buttons, id="import-footer")
        # One mount call: everything above is constructor-composed (a widget
        # accepts `mount` only once it is mounted itself, so nesting through
        # constructors is the only synchronous shape).
        self.mount(body, footer)
        self._left, self._right, self._footer = left, right, footer
        self._hints_frame, self._count_frame = hints, count
        self._preview_pane = None

    def _tree_mounted(self) -> bool:
        """Whether the filled tree finished mounting (mounts are requests)."""
        return (self._full and self._left is not None
                and self._left.is_mounted and self._right is not None
                and self._right.is_mounted)

    def _relayout(self) -> None:
        """Re-evaluate the size after a resize: swap or repaint."""
        if not self.is_running:
            return
        width, height = self.size
        fits = width >= MIN_COLS and height >= MIN_ROWS
        if fits != self._full:
            self._fill()
            self._sync_all()
        elif fits and self._tree_mounted():
            self._repaint_preview()
            self._refresh_footer()

    # -- rows ----------------------------------------------------------

    def _hint_rows(self, width):
        slots = self._editor_slots()
        return ["  " + line
                for line in hint_line(slots, IMPORT_HINTS, width - 2)]

    def _count_rows(self, width):
        """The live count: `N selected — Enter imports, Esc cancels`."""
        st = self._istate
        slots = self._editor_slots()
        line = (chrome(f"{len(st.selected)} selected", "foreground",
                        slots, bold=True)
                + chrome(" — Enter imports, Esc cancels",
                         CHROME_MUTED, slots))
        if st.note:
            line += "  " + chrome(st.note, CHROME_MUTED, slots)
        return [backdrop(line, slots, width)]

    def _refresh_footer(self) -> None:
        if self._footer is None or not self._full:
            return
        width, _ = self.size
        if getattr(self, "_hints_frame", None) is not None:
            self._hints_frame.update_rows(self._hint_rows(width), width)
        if getattr(self, "_count_frame", None) is not None:
            self._count_frame.update_rows(self._count_rows(width), width)

    def _repaint_preview(self) -> None:
        """Repaint the preview from the cursor theme's slots (read-once).

        `cursor_slots` hits the injected reader on first highlight and the
        cache after that; `{}` (nowhere, or unreadable) paints the muted row
        rather than the last theme's. Read-only carriers: plain `Frame`s,
        selection off, never focusable (spec §4.5).
        """
        if self._right is None or not self._full:
            return
        if not self._right.is_mounted:
            return              # mounts still queued; `_sync_all` repaints
        st = self._istate
        tid = import_state.cursor_id(st)
        slots = import_state.cursor_slots(st) if tid is not None else {}
        name = st.display.get(tid, "") if tid is not None else ""
        width = max(8, self.size.width - IMPORT_LEFT_W)
        rows = import_preview_rows(name, slots, width)
        if getattr(self, "_preview_pane", None) is None:
            pane = Frame(rows, width, name="import-preview")
            pane.styles.width = width
            pane.styles.height = len(rows)
            pane.styles.padding = 0
            pane.styles.margin = 0
            self._right.mount(pane)
            self._preview_pane = pane
        else:
            self._preview_pane.update_rows(rows, width)

    # -- state → widgets -----------------------------------------------

    def _sync_all(self) -> None:
        if self._full and not self._tree_mounted():
            if not self.is_running:
                return          # dismissed mid-mount: nothing to sync into
            self.call_after_refresh(self._sync_all)
            return
        self._sync_collapsed()
        self._sync_selection()
        self._sync_cursor()

    def _sync_collapsed(self) -> None:
        st = self._istate
        self._syncing = True
        try:
            for provider, coll in self._collapsibles.items():
                want = provider not in st.expanded
                if coll.collapsed != want:
                    coll.collapsed = want
        finally:
            self._syncing = False

    def _sync_selection(self) -> None:
        """Mirror the toggled set onto the lists, exactly.

        Only ids the state knows are ever selected — a stale value can
        never enter the toggled set.
        """
        st = self._istate
        self._syncing = True
        try:
            for provider, lst in self._lists.items():
                ids = st.theme_ids.get(provider, [])
                want = {st.display[tid] for tid in ids
                        if tid in st.selected}
                have = set(lst.selected)
                for value in have - want:
                    lst.deselect(value)
                for value in want - have:
                    lst.select(value)
        finally:
            self._syncing = False
        self._refresh_footer()

    def _sync_cursor(self) -> None:
        """Focus-follows-cursor: the cursor's list holds the highlight and
        the focus, every other list holds neither — never split-brain."""
        st = self._istate
        cursor_provider, cursor_index = st.cursor
        self._syncing = True
        try:
            for provider, lst in self._lists.items():
                ids = st.theme_ids.get(provider, [])
                if (provider == cursor_provider
                        and 0 <= cursor_index < len(ids)):
                    if lst.highlighted != cursor_index:
                        lst.highlighted = cursor_index
                elif lst.highlighted is not None:
                    lst.highlighted = None
                self._echo[provider] = lst.highlighted
            target = self._lists.get(cursor_provider)
            if target is None or not st.theme_ids.get(cursor_provider):
                target = next(iter(self._lists.values()), None)
            if target is not None:
                target.focus()
                if target.highlighted is not None:
                    target.scroll_to_highlight()
        finally:
            self._syncing = False
        self._repaint_preview()

    # -- keys ----------------------------------------------------------

    def import_key(self, key: str) -> None:
        """One keypress while this screen is top, from `Editor.on_key`.

        `up`/`down`/`space`/`enter` belong to the screen-level priority
        bindings above (which run before any widget binding could), and
        `home`/`end`/`pagedown`/`pageup` to the focused list itself — all
        stepped over here. `left` / `right` / `a` / `n` route to
        `import_state.handle_key` and mirror back out; close spellings dismiss
        with nothing written. Editor colour keys have no branch — inert by
        construction, like the picker's `t`.
        """
        st = self._istate
        if key in IMPORT_WIDGET_KEYS or key == "enter":
            return                    # the screen binding, or the list
        if key in ("left", "right", "a", "n"):
            action = import_state.handle_key(st, key)
            if action in ("collapsed", "expanded"):
                self._sync_collapsed()
                self._sync_cursor()
            elif action in ("selected-all", "cleared"):
                self._sync_selection()
            return
        if key in ("esc", "escape", "i", "I", "\x03"):
            if import_state.handle_key(st, key) == "close":
                self.dismiss(None)
            return
        # Anything else (`w/e/s/d/x/c/f/u/r/t/N/…`, `ctrl+s`, …) is an
        # editor key behind a popup that owns the surface: ignore it.

    def scroll_import(self, delta: int) -> None:
        """A wheel detent over a list: the highlight follows, ±1."""
        import_state.wheel(self._istate, delta)
        self._sync_cursor()

    # -- messages ------------------------------------------------------

    def _list_provider(self, lst):
        for provider, owned in self._lists.items():
            if owned is lst:
                return provider
        return None

    def on_selection_list_selection_highlighted(self, event) -> None:
        """Cursor move: adopt the live highlight, repaint the preview.

        Reads `selection_list.highlighted` — `SelectionHighlighted` carries
        no `index` attr (P0). A highlight equalling the per-provider echo is
        a mount- or mirror-time message, not a move: hold still. Anything
        else (clicks, native `home`/`end`/page jumps) adopts the cursor and
        repaints — `up`/`down` never arrive here, the screen bindings own
        them before any widget binding could wrap.
        """
        if self._syncing:
            return
        provider = self._list_provider(event.selection_list)
        if provider is None:
            return
        highlighted = event.selection_list.highlighted
        if highlighted is None:
            return
        if highlighted == self._echo.get(provider):
            return                    # mount or mirror echo, not a move
        self._echo[provider] = highlighted
        st = self._istate
        if (provider, highlighted) == (st.cursor[0], st.cursor[1]):
            return
        st.cursor = (provider, highlighted)
        self._repaint_preview()

    def on_selection_list_selected_changed(self, event) -> None:
        """Toggle: reconcile the provider's ids from the list's `selected`.

        Only ids the state knows survive — a muted or stale value can never
        enter the toggled set. The footer count is the proof; the preview
        does not follow selection.
        """
        if self._syncing:
            return
        provider = self._list_provider(event.selection_list)
        if provider is None:
            return
        st = self._istate
        ids = set(st.theme_ids.get(provider, []))
        now = {(provider, value) for value in event.selection_list.selected
               if (provider, value) in ids}
        st.selected = (st.selected - ids) | now
        self._refresh_footer()

    def on_collapsible_collapsed(self, event) -> None:
        """A group title toggled shut (click or Enter): persist the set.

        Same pattern as `Live`: the widget owns the toggle, the screen owns
        the expanded set across redraws. A cursor left inside a closed group
        re-homes flat-preservingly (the `_collapse` rule); selections stay
        selected — hiding is not deselecting. (`Collapsible.Toggled` itself
        is never posted — only its `Collapsed`/`Expanded` specialisations —
        so there is one handler per posted message, not one for the base.)
        """
        if self._syncing:
            return
        self._group_toggled(event.collapsible, True)

    def on_collapsible_expanded(self, event) -> None:
        """A group title toggled back open: persist the set."""
        if self._syncing:
            return
        self._group_toggled(event.collapsible, False)

    def _group_toggled(self, collapsible, is_collapsed: bool) -> None:
        provider = next((name for name, owned
                         in self._collapsibles.items()
                         if owned is collapsible), None)
        if provider is None:
            return
        self._adopt_collapsed(provider, is_collapsed)
        self._sync_cursor()

    def _adopt_collapsed(self, provider: str, is_collapsed: bool) -> None:
        st = self._istate
        rows = import_state.visible_rows(st)
        buddy = import_state.cursor_id(st)
        flat = (next((i for i, (_, _, rid) in enumerate(rows)
                      if rid == buddy), None)
                if buddy is not None else None)
        if is_collapsed:
            st.expanded.discard(provider)
        else:
            st.expanded.add(provider)
        rows = import_state.visible_rows(st)
        if buddy is not None and any(rid == buddy for _, _, rid in rows):
            return                    # still standing on a row: unmoved
        if not rows:
            st.cursor = (provider, 0)
            return
        flat = 0 if flat is None else min(flat, len(rows) - 1)
        st.cursor = (rows[flat][0], rows[flat][1])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = getattr(event.button, "id", None)
        if button_id == "import-confirm":
            event.stop()
            self.confirm()
        elif button_id == "import-cancel":
            event.stop()
            self.dismiss(None)

    def action_confirm_import(self) -> None:
        self.confirm()

    def action_import_up(self) -> None:
        import_state.handle_key(self._istate, "up")
        self._sync_cursor()

    def action_import_down(self) -> None:
        import_state.handle_key(self._istate, "down")
        self._sync_cursor()

    def action_import_toggle(self) -> None:
        import_state.handle_key(self._istate, "space")
        self._sync_selection()
        self._sync_cursor()

    def confirm(self) -> None:
        """`Enter`: import the selected themes and close (spec §4.4).

        The toggled set maps through `plan_confirm` (slugified unique names,
        library clashes skipped, unreadable sources noted) into a plain plan
        payload; `_import_closed` writes each plan through the injected
        writer (`themes.create` per plan, truth files only). An
        empty confirm stays open on the footer note — never an error and
        never a close.
        """
        st = self._istate
        library = getattr(self.app, "library", None)
        existing = library.names() if library is not None else []
        plans, skips, failures = import_state.plan_confirm(st, existing)
        if not plans and not skips and not failures:
            self._refresh_footer()   # `nothing selected`; stay open
            return
        self.dismiss((plans, skips, failures))


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
                 head_override=None, import_library=None,
                 import_writer=None, **kwargs):
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
        # 003/P4 — the import popup's injected seam (`ImportLibrary`: listing
        # + reader + formats, built by the `cli` composition root in phase 5
        # like the picker's `Library`). `None` lists nothing, so a bare popup
        # renders empty groups and never crashes; full wiring lands in P5.
        self.import_library = import_library
        # 003/P5 — the confirm path's write seam: a `cli`-built
        # `themes.create` closure (truth files only — no push, no
        # `set_current`, no buffer). `None` writes nothing; the root
        # builds it, never an import here.
        self.import_writer = import_writer
        self.import_result = None     # the last confirm's plan payload
        self.import_notes = []        # per-theme failures, for P5's report
        self._import_state = None     # the open popup's state, if any
        # `None` means "derive it from the session"; `""` means "none", which is
        # how the harness pins the header to the reference's arguments.
        self.head_override = head_override
        self.state = None
        self.hits: list = []
        self.rows_text: list = []
        # §15.7 — the laid-out size (`min(window, maxima)`); clicks past it
        # are fill and select nothing. Set on every `redraw`.
        self._layout_w = None
        self._layout_h = None
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
        state.sel = int(os.environ.get("HUEBOX_SEL", str(INITIAL_SEL)))
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
        if state.overlay is None:
            # An empty library opens on the first-run choice instead of an
            # editor with nothing to save to: import or name-and-create,
            # chosen before a single colour key can fire.
            enter_setup(state)
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
        # §15.7 — the layout size is the window clamped to the panel maxima:
        # within them this is identity and every size lays out as before;
        # past them the grid, the folds and the row budget stop moving and
        # the extra screen stays the buffer's own background fill. The
        # too-small check below still reads the window itself, and clicks
        # past the layout read as fill (§4.3.2).
        layout_w, layout_h = layout_size(width, height)
        self._layout_w, self._layout_h = layout_w, layout_h
        # §15 — one geometry per frame, read by the frame and by the arrows,
        # which move through the grid the user can see (§4.3)
        state.grid = grid_geometry(layout_w)

        self.hits = []
        self.regions = []
        self._panels_on = False
        self._panel_geom = {}
        self._side_on = False
        self._side_geom = {}
        # §13.7 — the picker is a modal popup over this frame (ThemesScreen),
        # so the too-small check covers the editor behind it: the dialog
        # clamps itself, and this frame is what dims behind it.
        picker = state.picker_frame()
        if width < MIN_COLS or height < MIN_ROWS:
            rows_text = [too_small_frame(width)]
        elif (panels_enabled() and layout_w >= SIDE_THIN_MIN_W
                and self._try_side(layout_w, layout_h, state)):
            # Side-by-side mounted everything: chrome above, two panels
            # below. Both locals are what the debug line counts.
            named = list(self.regions)
            rows_text = self.rows_text
        elif (panels_enabled()
                and width >= MIN_COLS + 2 and height >= MIN_ROWS + 5):
            # Bordered panels: the same rows, laid out for the inner width.
            # `header` / `hints` are laid out narrow too and padded on display
            # — the pad is the theme's own background, so it reads as fill.
            # The budget is exact, not fixed: the top costs whatever
            # `top_layout` shares (`_top_screen_height` minus the bare
            # header it replaces — the unified logo plus info beside
            # `editor`, or nothing where no share fits and the bare header
            # stays), plus two border rows per panel row below it, plus
            # decision 45's air on both sides of every panel while padded.
            # First at H-5, and only when live blocks showed up re-lay for
            # top + both borders; always reserving both would trim the hints
            # into the controls at small sizes (60x16), where they belong
            # outside the panel, not in it — below what fits the borders the
            # frame falls back to the bare stack instead. At most three lays:
            # the top's own share can grow the bill after the trial (a 7-row
            # `editor` top where a 6-row fallback used to be), and the re-lay
            # pays it out of decoration rather than scrolling.
            # Decision 45 — padding is the first thing spent when the window
            # shrinks: the padded panels where they show everything the
            # unpadded ones do, the unpadded panels where they would not, the
            # bare stack past that. The width gate is structural: a padded
            # panel needs the draw floor plus its own air (`PANEL_PAD_MIN_W`),
            # so below it only the unpadded layout is attempted.
            rows_text = None
            lay = self._lay_stacked(layout_w, layout_h, state, pad=0)
            if lay is not None:
                choice = lay
                if (panel_pad_enabled()
                        and layout_w >= PANEL_PAD_MIN_W):
                    airy = self._lay_stacked(layout_w, layout_h, state,
                                             pad=PANEL_PAD)
                    if (airy is not None
                            and self._pad_keeps_content(lay, airy)):
                        choice = airy
                rows_text, self.hits, self.regions = choice[:3]
                self.rows_text = rows_text
                state.grid = grid_geometry(layout_w - 2 - 2 * choice[5], "")
                named = list(self.regions)
                self._mount_panels(layout_w, rows_text, named, choice[5],
                                     choice[6])
                self._panels_on = True
            if rows_text is None:
                # Room for one panel but not all: fall back to the bare frame
                # rather than trimming widgets to buy borders. The grid keeps
                # the inner width, which is the inline loop's own rule.
                state.grid = grid_geometry(layout_w - 2)
                rows_text = frame_rows(self.fmt, session_path(state), state,
                                       layout_w, layout_h,
                                       head=self.head_for(state),
                                       hits=self.hits, regions=self.regions)
        else:
            rows_text = frame_rows(self.fmt, session_path(state), state,
                                   layout_w, layout_h,
                                   head=self.head_for(state),
                                   hits=self.hits, regions=self.regions)
        if not self._panels_on and not self._side_on:
            self.rows_text = rows_text
            surface = self._editor_screen()
            for child in list(surface.query(Panel)):
                child.remove()
            for child in list(surface.query(Frame)):
                child.remove()
            for child in list(surface.query(Live)):
                child.remove()
            for child in list(surface.query(Horizontal)):
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
            # The themes picker never takes this frame over — it is a modal
            # popup (ThemesScreen) over it — so there is no picker branch here.
            named = (list(self.regions)
                     or [("frame", 0, len(rows_text))])
            if (collapsible_enabled()
                    and not (width < MIN_COLS or height < MIN_ROWS)
                    and any(entry[0] in LIVE_BLOCKS for entry in named)):
                self._mount_collapsible(layout_w, rows_text, named)
            else:
                self._mount_bare(layout_w, rows_text, named)
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

    def _top_editor_row(self, width: int, state, top_h: int, pad: int = 0):
        """Logo plus info beside one bordered `editor` panel, or `None`.

        The selected readout and the HSV bars alone get the border — logo
        and theme metadata stay bare chrome, while the chip row (plus the
        specimen, stacked onto its own row where narrow) and the three
        equal HSV bars ride in `Panel("EDITOR")` with one cell of inner
        padding on each side (`EDITOR_PAD_X`) plus decision 45's horizontal
        air (`pad` on the left and right, so the padded box costs two columns
        and no rows). The info column owns the
        top's one control: the flat `themes` button under the theme
        subject, a mouse mirror of `t` through the same `apply_key` call
        — the column's last row is blank fill anyway, so the button costs
        no row and the row budget never moves. The shares come from
        `editor.top_layout` (logo steps down first, then info truncates
        its path, then the head stacks), so info shrinks with the window
        instead of holding air while the editor squeezes. Returns
        `(widget, screen_h)`; `None` where the columns do not fit.
        """
        slots = state.slots
        sel = state.sel
        label, path = top_subject(state, self.head_for(state) or self.fmt)
        # One arrangement at every size it fits (`editor.top_layout` owns
        # the shares): logo bare, info metadata only, the readout and the
        # bars in `editor`. No merge step below this — past the tightest
        # share the caller keeps the bare stack, so `info` never carries
        # the editor's own controls.
        try:
            lay = top_layout(width, label, path, slots, sel, pad=pad)
        except Exception:
            return None
        if lay is None:
            return None
        left_w, meta_w, editor_outer, stacked = lay
        try:
            meta_rows, head, _ = top_editor_meta(label, path, slots, sel,
                                                 meta_w=meta_w,
                                                 stacked=stacked)
            head_w = max(visible(row) for row in head)
        except Exception:
            return None
        value = slots.get(SLOTS[sel], MISSING)
        single_chrome = len("hue") + 1 + HSV_FIELD + 2
        left_raw = top_left_rows(slots, left_w)
        content_w = editor_outer - 2 - 2 * EDITOR_PAD_X - 2 * pad
        if content_w < head_w:
            return None
        bar_w = content_w - single_chrome
        if bar_w < 7:
            return None
        try:
            hsv_rows = [hsv_axis(slots, value, axis, bar_w)
                        for axis in range(3)]
        except IndexError:
            return None
        if any(not row for row in hsv_rows):
            return None
        editor_rows = head + hsv_rows
        # Six, not seven: the head is one row (`top_editor_meta` dropped
        # its blank), so the box holds 1 + 3 bars behind two borders —
        # the six rows the redraw budget already pays for the top. The
        # content rows never move for padding: the box narrows around them.
        top_screen = max(top_h, 6, len(left_raw))
        inner_h = top_screen - 2
        if inner_h < len(editor_rows):
            return None
        header = [backdrop(line, slots, left_w) for line in left_raw]
        header += [backdrop("", slots, left_w)] * max(0, top_screen - len(header))
        header = header[:top_screen]
        meta = [backdrop(line, slots, meta_w) for line in meta_rows]
        meta += [backdrop("", slots, meta_w)] * max(0, top_screen - 2 - len(meta))
        meta = meta[:top_screen - 2]
        hsv = [backdrop(line, slots, content_w)
               for line in editor_rows]
        hsv += [backdrop("", slots, content_w)] * max(0, inner_h - len(hsv))
        hsv = hsv[:inner_h]
        header_frame = Frame(header, left_w, name="header")
        header_frame.styles.width = left_w
        header_frame.styles.height = top_screen
        header_frame.styles.padding = 0
        header_frame.styles.margin = 0
        meta_frame = Frame(meta, meta_w, name="info")
        meta_frame.styles.width = meta_w
        meta_frame.styles.height = top_screen - 3
        meta_frame.styles.padding = 0
        meta_frame.styles.margin = 0
        # The top's controls in this arrangement: the same flat
        # `themes` button, under the theme subject in the metadata
        # column, and below it the same flat `import` button (003/P4) —
        # never focusable, theme-closed by the same CSS, each exactly what
        # its key does through the same call (`t`/`apply_key`, `i` /
        # `open_import` — see `ThemesButton`, `ImportButton` and
        # `on_button_pressed`). Their floor follows the
        # column down: past the label's own 16 the buttons squeeze with
        # it rather than overflowing the share the allocator gave them.
        button = ThemesButton()
        button_air = 1 if meta_w > 2 else 0
        button.styles.width = meta_w - 2 * button_air
        button.styles.min_width = min(16, meta_w - 2 * button_air)
        button.styles.height = 1
        button.styles.margin = (0, button_air)
        button.styles.padding = 0
        # The top's second control (003/P4): the same flat `import` button,
        # directly below `themes` in the metadata column. Never focusable,
        # theme-closed by the same CSS, and exactly what `i` does through the
        # same `open_import` call. It costs no row, and neither does the one
        # blank row parting it from `themes`: the column's last rows were
        # blank fill, and the metadata frame above gives two back.
        import_button = ImportButton()
        import_button.styles.width = meta_w - 2 * button_air
        import_button.styles.min_width = min(16, meta_w - 2 * button_air)
        import_button.styles.height = 1
        import_button.styles.margin = (1, button_air, 0, button_air)
        import_button.styles.padding = 0
        meta_col = Vertical(meta_frame, button, import_button)
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
        editor_panel.styles.padding = (0, EDITOR_PAD_X + pad)
        editor_panel.styles.margin = 0
        row = Horizontal(header_frame, meta_col, editor_panel)
        row.styles.width = width
        row.styles.height = top_screen
        row.styles.padding = 0
        row.styles.margin = 0
        return row, top_screen

    def _top_box_pad(self, width: int, state, top_h: int) -> int:
        """The editor box's own air: padded iff the padded box mounts.

        Decision 45 — the top decides its air for itself, independent of the
        content panels below it: a tight readout (an editor box with no slack
        behind its head) keeps today's unpadded box rather than collapsing
        into a bare readout, and that never vetoes air elsewhere. Narrower
        cannot mount where wider fails, so a padded mount implies the
        unpadded one mounts too and the two can only agree.
        """
        if not panel_pad_enabled():
            return 0
        if self._top_screen_height(width, state, top_h, PANEL_PAD) != top_h:
            return PANEL_PAD
        return 0

    def _top_screen_height(self, width: int, state, top_h: int,
                           pad: int = 0) -> int:
        """Screen rows the top will occupy, without building it.

        Asks `editor.top_layout` — the same shares `_top_editor_row`
        mounts — so `_try_side` sizes the middle from the identical answer
        rather than a second copy of the ladder. `pad` is decision 45's
        horizontal air per side, reserved out of the info column's share
        by the allocator: the content width is what the unpadded box had,
        so a padded top mounts wherever the unpadded one does and the
        caller only keeps the bare stack where no top fits at all.
        """
        if (editor_panel_enabled()
                and os.environ.get("HUEBOX_TOP", "1") != "0"):
            try:
                label, path = top_subject(state,
                                          self.head_for(state) or self.fmt)
                lay = top_layout(width, label, path,
                                 state.slots, state.sel, pad=pad)
            except Exception:
                lay = None
            if lay is not None:
                try:
                    left_raw = top_left_rows(state.slots, lay[0])
                    _meta, head, _w = top_editor_meta(
                        label, path, state.slots,
                        state.sel, meta_w=lay[1], stacked=lay[3])
                    head_w = max(visible(row) for row in head)
                except Exception:
                    return top_h
                top_screen = max(top_h, 6, len(left_raw))
                # The same viability `_top_editor_row` mounts: the inner
                # must hold the head plus all three bars at a content width
                # that holds the head and a usable bar, else the caller
                # keeps the bare stack and the budget loop pays nothing.
                content_w = lay[2] - 2 - 2 * EDITOR_PAD_X - 2 * pad
                bar_w = content_w - (len("hue") + 1 + HSV_FIELD + 2)
                if (top_screen - 2 >= len(head) + 3
                        and content_w >= head_w and bar_w >= 7):
                    return top_screen
        # No layout: the caller keeps the bare stack top (`top_h`). There
        # is no info-only fallback — `info` never carries the editor's
        # controls, so below the tightest share the frame paints its own
        # header and selected instead of a merged panel.
        return top_h

    def _top_side_row(self, width: int, state, top_h: int, pad: int = 0):
        """The compositor top, or `(None, top_h)` for the bare stack (§8.1).

        One arrangement and no merge step: bare logo, the flexible info
        column (theme only), and the bordered `Panel("EDITOR")` holding
        the selected readout plus the three HSV bars — the shares from
        `editor.top_layout`, mounted by `_top_editor_row`. `info` and
        `editor` stay separate boxes at every size they fit; past the
        tightest share this returns `None` and the caller keeps the bare
        stack top `draw_editor` painted, so `info` never carries the
        editor's own controls. `HUEBOX_TOP=0` pins that stack past both;
        `HUEBOX_EDITOR_PANEL=0` pins it too. Returns `(widget, screen_h)`;
        `(None, top_h)` where no top fits.
        """
        if (editor_panel_enabled()
                and os.environ.get("HUEBOX_TOP", "1") != "0"):
            built = self._top_editor_row(width, state, top_h, pad)
            if built is not None:
                return built
        return None, top_h

    def _lay_stacked(self, layout_w: int, layout_h: int, state,
                       pad: int):
        """Lay the stacked panels out at `pad` air per side, or `None`.

        The budget loop `redraw` always ran, lifted whole: the frame is laid
        out for the content width (borders plus `pad` air on each side come
        off the window), the top costs its screen rows minus the bare header
        it replaces, and each panel row below costs its two borders — air
        costs columns only, never rows, so the budget heights never move for
        padding. Pure: nothing is mounted and `self` is untouched, so the
        caller can lay both airs and keep the padded one only where it shows
        everything the unpadded one does (decision 45). Returns `(rows,
        hits, regions, top_screen, header_here, pad)`.
        """
        inner_w = layout_w - 2 - 2 * pad
        inner_h = layout_h - 5
        for _ in range(3):
            if inner_h < MIN_ROWS:
                return None
            lay_hits, lay_regions = [], []
            lay = frame_rows(self.fmt, session_path(state), state,
                             inner_w, inner_h,
                             head=self.head_for(state),
                             hits=lay_hits, regions=lay_regions,
                             indent="")
            by_name = {name: (first, count)
                       for name, first, count in lay_regions}
            # The bare header `_mount_panels` replaces: `header`, plus
            # `selected` only where the frame put it above the palette
            # (the side top block) — the same rule as `rows_for(top)`.
            header_names = ["header"]
            if ("selected" in by_name and "palette" in by_name
                    and by_name["selected"][0]
                    < by_name["palette"][0]):
                header_names.append("selected")
            header_here = sum(by_name[_name][1]
                              for _name in header_names
                              if _name in by_name)
            top_pad_here = self._top_box_pad(layout_w, state, header_here)
            top_screen_here = self._top_screen_height(layout_w, state,
                                                      header_here,
                                                      top_pad_here)
            panels_here = 2 if set(by_name) & set(PANEL_EXAMPLES) else 1
            need = ((top_screen_here - header_here)
                    + panels_here * 2)
            if inner_h + need <= layout_h:
                return (lay, lay_hits, lay_regions,
                        top_screen_here, header_here, pad, top_pad_here)
            inner_h = layout_h - need
        return None

    @staticmethod
    def _pad_keeps_content(ref, cand) -> bool:
        """Whether the padded lay shows everything the unpadded one does.

        Decision 45 — air must never cost content: the same top (a padded
        editor box must not collapse into a bare readout), the same blocks,
        and no block shows fewer rows. Narrower folds may show *more* rows
        for the same widgets, which is fine.
        """
        (_, _, ref_regions, ref_top, ref_head, _, _) = ref
        (_, _, cand_regions, cand_top, cand_head, _, _) = cand
        if (cand_top != cand_head) != (ref_top != ref_head):
            return False                    # the top changed shape
        ref_counts = {name: count for name, _, count in ref_regions}
        cand_counts = {name: count for name, _, count in cand_regions}
        if set(cand_counts) != set(ref_counts):
            return False                    # a block came or went
        return all(cand_counts[name] >= ref_counts[name]
                   for name in ref_counts)

    def _mount_panels(self, width: int, rows_text: list, named: list,
                        pad: int = 0, top_pad: int = 0) -> None:
        """Stack the frame's blocks into bordered panels.

        `named` is `draw_editor`'s own `(name, first, count)` map at the inner
        size, so the groups cannot drift from what the frame painted: the
        same rule as `hits`. The top rides as bare logo plus the info
        column beside the bordered `editor` panel, or as bare chrome where
        no share fits; controls and examples each get a `Panel` with a
        themed border and title. Inner blocks keep the inner width; chrome blocks are re-backed
        to the full width, because a row backed to the inner width and padded
        by the widget would leave two columns on the terminal's own background
        (§8.2) — the pad has no style of its own.
        """
        surface = self._editor_screen()
        for child in list(surface.query(Panel)):
            child.remove()
        for child in list(surface.query(Frame)):
            child.remove()
        for child in list(surface.query(Live)):
            child.remove()
        for child in list(surface.query(Horizontal)):
            child.remove()
        inner_w = width - 2 - 2 * pad
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
        # Screen geometry for clicks: borders are single rows. The top is
        # bare logo plus the flexible info column beside one bordered
        # `editor` panel (`top_screen`: two borders, button costs no row);
        # each panel below adds a top and a bottom border row.
        top_row, top_screen = self._top_side_row(width, self.state,
                                                 header_h, top_pad)
        self._panel_geom = {
            "pad": pad,
            "top_pad": top_pad,
            "inner_w": inner_w,
            "header_h": header_h,
            "controls_h": controls_h,
            "examples_h": examples_h,
            "has_examples": bool(examples),
            "top_screen": top_screen if top_row is not None else header_h,
        }
        if top_row is not None:
            # §8.1 (decision 40) — the bare logo, the info column and the
            # bordered `editor` panel stay separate boxes at every size
            # they fit, padded to the height the frame laid out so
            # everything below rides where it did and the click map never
            # moves. A click in the top selects nothing, because the top
            # names no slot (§4.3.2).
            surface.mount(top_row)
        else:
            for name, first, count in header:
                surface.mount(mount_block(name, first, count, width))
        for name, first, count in extra:
            # `extra` unknown rows sit with the header chrome: full-width,
            # never inside a panel whose title would misname them.
            surface.mount(mount_block(name, first, count, width))
        if control_inners:
            inners = [widget for widget, _ in control_inners]
            panel = Panel(PANEL_TITLES["controls"], *inners, name="controls")
            panel.styles.width = width
            panel.styles.height = controls_h + 2
            panel.styles.padding = (0, pad)
            panel.styles.margin = 0
            surface.mount(panel)
        if example_inners:
            inners = [widget for widget, _ in example_inners]
            panel = Panel(PANEL_TITLES["examples"], *inners, name="examples")
            panel.styles.width = width
            panel.styles.height = examples_h + 2
            panel.styles.padding = (0, pad)
            panel.styles.margin = 0
            surface.mount(panel)
        for name, first, count in hints:
            surface.mount(mount_block(name, first, count, width))

    def _try_side(self, width: int, height: int, state) -> bool:
        """The side-by-side layout, or `False` to keep the stacked one.

        The top stands as bare logo plus the info column beside the
        bordered `editor` panel (§8.1 decision 40); the controls
        (palette pairs over interface pairs) and the live blocks in two
        bordered panels next to each other below. The chrome rows come from a
        full-width `draw_editor` run — captured, like everywhere — while the
        panels' contents are `side_left_rows` / `side_live_rows` at their own
        widths. Anything that does not fit (trimmed chrome, a short middle)
        returns `False` before mounting anything, and the stacked layout runs
        instead. Below `SIDE_MIN_W` the left panel is the squeezed pair
        (§8.1, decision 42) — the same pairs in abbreviated cells — down
        to `SIDE_NARROW_MIN_W`, and past that the thin pair (decision 43:
        narrow rows, hex hidden) down to `SIDE_THIN_MIN_W`, past which the
        stacked layout runs too. Decision 45's content air is decided from
        the blocks alone: the right column folds gracefully at any width,
        so the pair pads iff it still holds a row. The top box decides its
        own air in `_top_box_pad` and never vetoes the pair's.
        """
        if width >= SIDE_MIN_W:
            narrow, hexes = False, True
        elif width >= SIDE_NARROW_MIN_W:
            narrow, hexes = True, True
        elif width >= SIDE_THIN_MIN_W:
            narrow, hexes = True, False
        else:
            return False
        left_rows = SIDE_LEFT_NARROW_ROWS if narrow else SIDE_LEFT_ROWS
        # The thin pair shares the narrow rows; only its cells narrow
        # further, so the height bar above covers all three widths.
        if not narrow:
            left_w, left_outer = SIDE_LEFT_W, LEFT_OUTER_W
        elif hexes:
            left_w, left_outer = SIDE_LEFT_NARROW_W, LEFT_NARROW_OUTER_W
        else:
            left_w, left_outer = SIDE_LEFT_THIN_W, LEFT_THIN_OUTER_W
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
        top_pad = self._top_box_pad(width, state, top_h)
        top_screen = self._top_screen_height(width, state, top_h, top_pad)
        content_h = height - top_screen - bottom_h - 2
        # One height bar for both widths: short windows keep the stacked
        # panels (full-width sample) whatever the width, and the narrower
        # content pads like the full one does past it.
        if content_h < SIDE_LEFT_ROWS:
            return False
        pad = 0
        if (panel_pad_enabled()
                and width - left_outer - 2 * PANEL_PAD - 2 - 2 * PANEL_PAD >= 1):
            # The padded right column still holds a row. Its folds are
            # graceful at any width and the left pairs are fixed, so the
            # blocks never move for air here — only the width gate matters.
            pad = PANEL_PAD
        right_outer = width - left_outer - 2 * pad
        right_inner = right_outer - 2 - 2 * pad
        if right_inner < 1:
            return False        # the air costs more than the window holds
        right_regions: list = []
        right = side_live_rows(state.slots, right_inner, content_h,
                               regions=right_regions)
        if right is None:
            return False
        left_hits: list = []
        left = side_left_rows(state.slots, state.sel, hits=left_hits,
                              narrow=narrow, show_hex=hexes)
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

        surface = self._editor_screen()
        for child in list(surface.query(Panel)):
            child.remove()
        for child in list(surface.query(Frame)):
            child.remove()
        for child in list(surface.query(Live)):
            child.remove()
        for child in list(surface.query(Horizontal)):
            child.remove()
        slots = state.slots
        top_h = header_h + sel_h
        top_row, top_screen = self._top_side_row(width, state, top_h,
                                                 top_pad)
        if top_row is not None:
            surface.mount(top_row)
        else:
            top_screen = top_h
            for name in ("header", "selected"):
                first, count = by_name[name]
                surface.mount(block(name, full[first:first + count], width))
        # The palette is always the title plus its 8 pair-rows; the
        # interface is whatever rows the variant draws after the blank.
        left_children = [block("palette", left[0:9], left_w),
                         block("interface", left[9:left_rows], left_w)]
        fill = content_h - left_rows
        if fill:
            left_children.append(block(
                "left-pad", [backdrop("", slots, left_w)] * fill,
                left_w))
        left_panel = Panel(PANEL_TITLES["controls"], *left_children,
                           name="controls")
        left_panel.styles.width = left_outer + 2 * pad
        if panel_limits_enabled() and left_outer + 2 * pad < PANEL_MIN_W:
            # The stacked minimum would clamp the squeezed pair wider
            # than its content and fill the rest with air: below it the
            # floor follows the variant instead. The stacked panels never
            # take this path, so §15.7 keeps holding them as pinned.
            left_panel.styles.min_width = left_outer + 2 * pad
        left_panel.styles.height = content_h + 2
        left_panel.styles.padding = (0, pad)
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
        if panel_limits_enabled() and right_outer < PANEL_MIN_W:
            # The mirror of the left floor: below 55 columns the stacked
            # minimum would hold the right panel at 42 while it asks for
            # less, and the pair would overflow the window by the
            # difference — the row's right border column cut off screen.
            right_panel.styles.min_width = right_outer
        right_panel.styles.height = content_h + 2
        right_panel.styles.padding = (0, pad)
        right_panel.styles.margin = 0
        row = Horizontal(left_panel, right_panel)
        row.styles.width = width
        row.styles.height = content_h + 2
        row.styles.padding = 0
        row.styles.margin = 0
        surface.mount(row)
        bottom0 = top_screen + content_h + 2
        for name in ("hints", "status"):
            if name in by_name:
                first, count = by_name[name]
                surface.mount(block(name, full[first:first + count], width))
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
                        ("interface", 9, left_rows - 9)]
        self.regions.extend(right_regions)
        self.regions.append(("hints", bottom0, hints_h))
        if status_h:
            self.regions.append(("status", bottom0 + hints_h, status_h))
        self._side_on = True
        self._side_geom = {"pad": pad,
                           "top_pad": top_pad,
                           "top": top_screen,
                           "content_h": content_h,
                           "left_outer": left_outer + 2 * pad,
                           "left_w": left_w,
                           "narrow": narrow}
        return True

    def _side_frame_coords(self, sx: int, sy: int):
        """Screen → left-content coords, or `None` for chrome.

        Only the left panel holds controls: the right panel's examples are
        readouts, the `selected` strip above is a readout, and every border
        row and column is chrome. Decision 45's horizontal air reads as
        chrome too: a click into it selects nothing, like a click on a
        border.
        """
        geom = self._side_geom
        pad = geom.get("pad", 0)
        content_h = geom["content_h"]
        y = sy - geom["top"]
        if y == 0 or y == content_h + 1:
            return None             # the pair's top / bottom borders
        if not 1 <= y <= content_h:
            return None             # chrome above or below the pair
        if sx >= geom["left_outer"]:
            return None             # the examples panel
        if sx <= pad or sx > pad + geom["left_w"]:
            return None             # the controls panel's border or its air
        return sx - 1 - pad, y - 1

    def _panel_frame_coords(self, sx: int, sy: int):
        """Screen → frame coords inside prototype panels, or None on chrome.

        Borders are chrome: a click on any border row or border column is
        not an error and moves nothing (§4.3.2). Inner blocks are offset by
        one column (left border) plus decision 45's horizontal air, and by
        the border rows above them; a click into the air itself is chrome
        too.
        """
        geom = self._panel_geom
        if not geom:
            return sx, sy
        pad = geom.get("pad", 0)
        inner_w = geom["inner_w"]
        header_h = geom["header_h"]
        controls_h = geom["controls_h"]
        examples_h = geom["examples_h"]
        has_examples = geom["has_examples"]
        top_screen = geom.get("top_screen", header_h)
        if top_screen != header_h:
            # Compositor top (logo plus info beside `editor`): the whole
            # top zone is chrome (clicks there select nothing), then
            # everything below rides `top_screen - header_h` rows lower
            # than the bare header.
            if sy < top_screen:
                return None
            y = sy - top_screen
        else:
            # Header chrome: full-width, no offset. Columns past the inner
            # width are pad and hit nothing, which `slot_at` says as None.
            if sy < header_h:
                return sx, sy
            y = sy - header_h

        def inner_hit(y, base):
            # One panel's content rows — border, content, border — against
            # the frame rows past the panels above it (`base`). The air is
            # horizontal only (decision 45), so rows map straight past the
            # border while columns shift past it. A tuple is the frame
            # coords; `None` is chrome (a border row, or a column past the
            # content); `False` is past the panel, and only then does the
            # caller walk on to the next one.
            if y <= base + 1:
                if y == 0 or y == base + 1:
                    return None          # either border row
                if sx <= pad or sx > pad + inner_w:
                    return None          # a border column or the air
                return sx - 1 - pad, y - 1
            return False

        # Controls panel: top border, air, inner, air, bottom border.
        hit = inner_hit(y, controls_h)
        if hit is None:
            return None
        if hit is not False:
            return hit[0], header_h + hit[1]
        y -= controls_h + 2
        if has_examples:
            hit = inner_hit(y, examples_h)
            if hit is None:
                return None
            if hit is not False:
                return hit[0], header_h + controls_h + hit[1]
            y -= examples_h + 2
        # Hints chrome below both panels: two border rows per panel above.
        panels = 2 if has_examples else 1
        # `y` is now relative to the hints zone; inner Y adds back everything
        # above it. Borders above = 2 per panel, plus the top's own extra.
        extra = geom.get("top_screen", header_h) - header_h
        return sx, sy - 2 * panels - extra

    def _mount_bare(self, width: int, rows_text: list, named: list) -> None:
        """Mount every block as its own widget, with no collapsible."""
        surface = self._editor_screen()
        for name, first, count in named:
            rows_here = rows_text[first:first + count]
            kind = BLOCK_WIDGETS.get(name, Frame)
            # `name` is a constructor argument, not a settable property — which
            # is Textual saying the name is part of a widget's identity.
            block = kind(rows_here, width, name=name)
            block.styles.width = width
            block.styles.height = len(rows_here)
            block.styles.padding = 0
            block.styles.margin = 0
            surface.mount(block)

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
        surface = self._editor_screen()

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
            surface.mount(Live(LIVE_TITLES[name], child,
                                collapsed=name in self._collapsed, name=name))

        order = [name for name, _, _ in named]
        live_at = min((order.index(name) for name in LIVE_BLOCKS
                       if name in by_name), default=len(order))
        for name, first, count in named:
            if name in LIVE_BLOCKS:
                continue
            if order.index(name) < live_at:
                surface.mount(mount_block(
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
                surface.mount(mount_block(
                    name, rows_text[first:first + count]))

    def place_focus(self, picker_up: bool, sel: int) -> None:
        """Hand focus to whichever block owns it, once the tree exists.

        §13.7 — the picker owns the surface while it is up, as a modal popup
        over the editor. Focus stays off the grids underneath, so an arrow
        cannot move the *colour* selection behind a list the user is reading
        — the one thing §13.7 says cannot happen.
        """
        # `focused` and `set_focus` both reach for a screen, which an app that
        # has never run does not have. A frame drawn outside Textual — the
        # headless suites, and `tests/session.py` — has no focus to place.
        if not self.is_running:
            return
        # 003/P4 — the import popup owns focus while it is up; handing a
        # grid the focus would let an arrow move the colour selection behind
        # the list the user is reading. The popup focuses its own cursor list
        # and `redraw` hands the grid back once it closes.
        if self._import_screen() is not None:
            return
        # The themes popup owns the surface the same way: it has no focusable
        # of its own (keys arrive via `on_key`), so no grid behind it may
        # keep the arrows.
        if self._themes_screen() is not None:
            return
        if picker_up:
            # The list is up but its dialog is not top yet (the push lands a
            # refresh late): hold focus off the grids until it is.
            if isinstance(self.focused, Swatches):
                self.set_focus(None)
            return
        # The frame is the window, so any scroll offset on the screen is left
        # over from a moment when it was not — a resize mid-frame, or a frame
        # laid out one row taller than the window it is in. Leaving it there is
        # what makes a scroll reveal a stale header: the cells the scroll exposes
        # were painted for a window that no longer exists, and nothing repaints
        # them because nothing thinks they changed.
        self.screen.scroll_home(animate=False)
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
        if self.state.setup is not None:
            # Pushing a screen needs a running app, so the first-run popup
            # waits for the refresh after mount — the same shape as every
            # other focus this shell places.
            self.call_after_refresh(self.open_setup)
        elif self.state.overlay is not None:
            # The harness pins the picker up on the first paint the same way:
            # the list opens as a popup over the editor, never as a takeover.
            self.call_after_refresh(self.open_themes)

    def on_resize(self, event) -> None:
        # The frame is laid out from `self.size`, and during `on_resize` that is
        # still the *old* size: Textual applies the new one in the layout pass
        # that follows. Redrawing here drew the frame for the window the user
        # just left — a 24-row frame in a 12-row terminal, one resize behind,
        # forever. `call_after_refresh` is the first moment both agree.
        _debug("resize to %s" % (event.size,))
        if self._import_screen() is not None:
            return          # the popup owns the surface: it re-lays itself
                            # out, and the frame behind it redraws on close
        if self._setup_screen() is not None:
            return          # the choice dialog re-lays itself the same way
        if self._themes_screen() is not None:
            return          # the themes dialog re-lays itself the same way
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
        # 003/P4 — the import popup owns the surface while it is up. The
        # modal does not starve this handler (P0), so the guard reads the
        # screen type: popup keys route to the import state, and editor
        # colour keys are inert — no branch reaches `apply_key` underneath it.
        if self._import_screen() is not None:
            event.stop()
            self.screen.import_key(translate(event.key))
            return
        if self._setup_screen() is not None:
            # The first-run popup owns the surface: every key goes to the
            # one key surface through the screen, which routes outcomes
            # (repaint, import, quit) from the state.
            event.stop()
            self.screen.setup_key(translate(event.key))
            return
        if self._themes_screen() is not None:
            # The themes popup owns the surface the same way: every key goes
            # to the one key surface through the screen, which dismisses on
            # close and repaints the dialog otherwise.
            event.stop()
            self.screen.themes_key(translate(event.key))
            return
        event.stop()
        if isinstance(self.focused, Swatches) and event.key in GRID_KEYS:
            return
        # 003/P4 — `i` (or `I`) opens the import popup, through the one call the
        # `Import` button takes. While the picker owns the surface it has
        # no branch (popups never stack); `open_import` re-checks both.
        if translate(event.key) in ("i", "I"):
            if self.state.overlay is not None:
                return
            self.open_import()
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
            if self.state.overlay is not None:
                # `t` opened the list: the popup over the editor, never a
                # takeover — the frame behind stays as it was.
                self.open_themes()
            self.redraw()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """The `themes` button: exactly what `t` does, through the same call.

        A click is a keypress (§4.3.2): the button resolves to the key surface
        rather than re-implementing the picker — open, blocked-while-dirty,
        or the no-library status — and the list opens as a popup over the
        editor when one opened, the same as `on_key` does after `apply_key`.
        The `import` button (003/P4) resolves the same way to `open_import`,
        the identical call the `i`/`I` keys take.
        """
        button_id = getattr(event.button, "id", None)
        if button_id == "import-button":
            event.stop()
            self.open_import()
            return
        if button_id != "themes-button":
            return
        event.stop()
        apply_key("t", self.state)
        if self.state.quit:
            self.exit()
        else:
            if self.state.overlay is not None:
                self.open_themes()
            self.redraw()

    def _setup_screen(self):
        """The first-run popup, if it is the top screen — else `None`.

        One guard for every setup check in this shell, mirroring
        `_import_screen`. `is_running` comes first for the same reason: a
        frame drawn outside Textual has no screen stack at all.
        """
        if not self.is_running:
            return None
        screen = self.screen
        return screen if isinstance(screen, SetupScreen) else None

    def _themes_screen(self):
        """The themes popup, if it is the top screen — else `None`.

        One guard for every themes check in this shell, mirroring
        `_setup_screen` and `_import_screen`.
        """
        if not self.is_running:
            return None
        screen = self.screen
        return screen if isinstance(screen, ThemesScreen) else None

    def open_themes(self) -> None:
        """Push the themes popup: the list over the dimmed editor.

        Called after `apply_key` opens the list (`t`, the `Themes` button,
        or the harnessed first paint) — never from `build_state`, since
        pushing a screen needs a running app. Popups never stack: the
        setup choice and the import popup each own the surface their own
        way, and a second open while the dialog is up is a no-op.
        """
        if self.state.overlay is None:
            return
        if self.state.setup is not None:
            return
        if self._setup_screen() is not None:
            return
        if self._import_screen() is not None:
            return
        if self._themes_screen() is not None:
            return
        if not self.is_running:
            return              # headless (no compositor): the list stays on
                                # the state for `apply_key` to own; pushing
                                # needs a running app like `open_setup` does
        self.push_screen(ThemesScreen(), self._themes_closed)

    def _themes_closed(self, result) -> None:
        """The popup dismissed: paint the editor it stood over.

        `result` is always `None` — closing is the state going
        `overlay is None` (Enter picked a theme, Esc/`t` put the editor
        back), so this only routes: quit exits, anything else repaints the
        frame behind it. A dialog dismissed with the list still up (which
        no key does) puts the popup back up, so the surface never drops.
        """
        if not self.is_running:
            return
        if self.state.quit:
            self.exit()
        elif self.state.overlay is not None:
            self.open_themes()
        else:
            self.redraw()

    def open_setup(self) -> None:
        """Push the first-run popup: import or name-and-create.

        Called once the session is up (never from `build_state` — pushing a
        screen needs a running app, so `on_mount` defers here), and again
        when the import popup closes still empty. Popups never stack:
        the themes list and the import popup each own the surface their own
        way, and a second open while a dialog is up is a no-op.
        """
        if self.state.setup is None:
            return
        if self.state.overlay is not None:
            return
        if self._setup_screen() is not None:
            return
        if self._import_screen() is not None:
            return
        if self._themes_screen() is not None:
            return
        self.push_screen(SetupScreen(), self._setup_closed)

    def _setup_closed(self, result) -> None:
        """The popup dismissed: route the outcome the state already holds.

        `result` is always `None` — the choice writes its outcomes onto the
        state before dismissing, so this only routes: quit exits, an import
        choice opens the import popup (after this one is gone, so modals
        never stack), and anything else — a create, or a dialog that only
        ever repainted — paints the editor behind it.
        """
        if not self.is_running:
            return
        if self.state.quit:
            self.exit()
        elif self.state.import_pending:
            self.state.import_pending = False
            self.open_import()
        else:
            self.redraw()

    def _editor_screen(self):
        """The screen the editor frame lives on: the stack bottom.

        `redraw` can run while a modal is top (a resize scheduled before the
        push lands after it): `App.query` spans every screen while `App.mount`
        targets the active one, so an unscoped redraw tears the editor's
        widgets out of the default screen and mounts them into the modal —
        which is how the choice dialog once stood over a blank frame.
        Every query and mount below goes through this; headless sessions
        (never running, mocks for `query`/`mount`) keep today's behaviour.
        """
        if self.is_running:
            return self.screen_stack[0]
        return self

    def _import_screen(self):
        """The import popup, if it is the top screen — else `None`.

        One guard for every popup check in this shell. `is_running` comes
        first because a frame drawn outside Textual (the headless suites,
        `tests/session.py`) has no screen stack at all — `self.screen`
        raises there instead of answering.
        """
        if not self.is_running:
            return None
        screen = self.screen
        return screen if isinstance(screen, ImportScreen) else None

    def open_import(self) -> None:
        """`i`/`I` and the `Import` button: one call for both (003 spec §4.1).

        Opening is never blocked by a dirty buffer, and neither open nor
        close retargets the session: importing adds library files, it never
        touches the buffer the way the picker does. Popups never stack —
        the themes list owns the surface while it is up, and a second `i`
        while a popup is up is a no-op. The first-run choice owns the surface
        the same way: `i` behind it is swallowed by the choice (its own `i`
        branch asks for the popup instead), so opening from here re-checks
        both. The provider order comes from the
        injected library's formats (P5 builds it; `None` lists nothing, so
        a bare popup renders the `no themes found` row and never crashes).
        """
        if self.state.overlay is not None or self.state.setup is not None:
            return
        if self._import_screen() is not None:
            return
        if self._themes_screen() is not None:
            return
        library = self.import_library
        order = list(library.formats) if library is not None else []
        istate = import_state.ImportState(order, library)
        import_state.open_import(istate)
        self._import_state = istate
        self.push_screen(ImportScreen(istate), self._import_closed)

    def _write_imports(self, plans):
        """Write each confirmed plan through the injected writer.

        Returns `(imported, problems)`: the names that landed and the
        per-theme problems that did not. Truth files only, by construction
        — the writer is `cli`'s `themes.create` closure (no push, no
        `set_current`), so writing never retargets the session, never
        touches the current theme, and never reaches the edit buffer;
        only `state.status` below is written, never slots, sel or theme.
        A session with no writer (harness, bare `Editor`) reports the
        plan and writes nothing.
        """
        imported, problems = [], []
        writer = self.import_writer
        if writer is None:
            return [plan.name for plan in plans], problems
        for plan in plans:
            problem = writer(plan.name, plan.slots, plan.source)
            if problem:
                problems.append(f"{plan.name}: {problem}")
            else:
                imported.append(plan.name)
        return imported, problems

    def _import_closed(self, result) -> None:
        """The popup dismissed: write the plan, report, frame returns.

        `result` is `None` on abandon (Esc/`i`/cancel — buffer and status
        untouched) or the `(plans, skips, failures)` payload from `confirm`.
        Each plan lands through the injected writer (`themes.create` per
        plan, truth files only); successes and skips fold into the status
        line (`imported N themes: …` / `already in library: …`), per-theme
        failures join `import_notes` for the exit report (the picker-notes
        rule — no modal in the popup). Closing never touches the buffer:
        no branch here assigns slots, sel, theme or path.
        """
        self._import_state = None
        if not self.is_running:
            return
        if result is not None:
            plans, skips, failures = result
            self.import_result = result
            imported, problems = self._write_imports(plans)
            self.import_notes = list(failures) + problems
            if imported:
                names = ", ".join(imported)
                plural = "" if len(imported) == 1 else "s"
                parts = [f"imported {len(imported)} "
                         f"theme{plural}: {names}"]
                parts.extend(skips)
                self.state.status = "; ".join(parts)
            elif skips:
                self.state.status = (skips[0] if len(skips) == 1
                                     else f"{len(skips)} themes already "
                                          "in library")
            elif self.import_notes:
                self.state.status = "nothing imported - see session notes"
        if (self.state.setup is None and self.library is not None
                and not self.library.names()):
            # Still nothing to edit: the choice the popup was opened from
            # — or the empty session `i` was pressed in — is owed again.
            # A library that landed themes stays in the editor, where `t`
            # opens them.
            enter_setup(self.state)
        if self.state.setup is not None:
            # The popup, not the frame: the editor behind stays as the
            # import found it, and the choice opens over it.
            self.open_setup()
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

        003/P4 — while the import popup is top this returns without stopping:
        the list rows and group titles already answered the click below, and
        answering here as well would move the *colour* selection behind the
        popup the user is reading.

        The frame widget starts at the screen origin, so a click's screen row is
        its row in the frame; `slot_at` maps a column to a slot or to None for
        the chrome. Clicking nothing is not an error and does not move the
        selection — the header, the readings and the empty air are not controls,
        and pretending otherwise would make the frame feel like a form.

        The themes list answers from its dialog's own cells (`ThemesScreen`
        owns its hits, like `SetupScreen`): a click while it is top never
        reaches the editor behind it.
        """
        if self._import_screen() is not None:
            return
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
        if self._setup_screen() is not None:
            # The popup answers its own clicks against its own cells — the
            # editor's panels and layout maxima are behind it, not under it.
            self.screen.click_at(fx, fy)
            return
        if self._themes_screen() is not None:
            # The themes dialog answers its own clicks against its own cells.
            self.screen.click_at(fx, fy)
            return
        # §15.7 — past the layout maxima the screen is fill, not a panel:
        # a click out there names no slot, like any other chrome click.
        layout_w = getattr(self, "_layout_w", None)
        layout_h = getattr(self, "_layout_h", None)
        if layout_w is not None and (fx >= layout_w or fy >= layout_h):
            return
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
        # A swatch or an interface cell. The themes list never reaches here:
        # its dialog routes clicks to its own cells above, so this is always
        # the editor behind any popup.
        if target != self.state.sel:
            self.state.sel = target
        self.redraw()

    def on_mouse_scroll_up(self, event) -> None:
        if self._import_screen() is not None:
            return          # the popup's lists stop their own wheel events;
                            # gaps bubble to the list column, not here — so
                            # do not stop what is already past this shell
        self._scroll_picker("up", event)

    def on_mouse_scroll_down(self, event) -> None:
        if self._import_screen() is not None:
            return
        self._scroll_picker("down", event)

    def _scroll_picker(self, direction: str, event) -> None:
        """Wheel the picker's selection, which is how a long library is walked.

        Only in the picker: the editor frame itself does not scroll, so a wheel
        notch does nothing a user could misread as having moved the colours.
        Either popup owns the wheel while it is up — the import lists stop
        their own events, and the two-row choice has nothing to walk. The
        themes dialog repaints in place; the editor behind it never scrolls.
        """
        if self._setup_screen() is not None:
            return
        if self._themes_screen() is not None:
            event.stop()
            if self.state.overlay is None:
                return
            apply_key(direction, self.state)
            if self.state.overlay is None:
                return              # a wheel never closes, but be total
            self.screen._repaint()
            return
        event.stop()
        if self.state.overlay is None:
            return
        apply_key(direction, self.state)
        if self.state.overlay is None:
            return
        # Headless or between `t` and its push: no dialog to repaint yet —
        # the next `redraw` paints the editor behind and the push follows.
        if self._themes_screen() is not None:
            self.screen._repaint()
        else:
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
        report=None, notes=None, import_library=None, import_writer=None):
    """Run one session to completion, then say what it has to say.

    What `cli` calls. `report_session` runs here rather than in `cli` because the
    session state lives on the `Editor`, and the wording of what a user reads on
    exit is huebox's, not the driver's. `import_library` / `import_writer` are
    the import popup's injected seams (built by `cli`); `None` lists nothing
    and writes nothing. The popup's per-theme failures join the session notes
    printed at exit — the picker-notes rule, no modal in the popup.
    """
    editor = Editor(fmt=fmt, path=path, slots=slots, write=write,
                    backup_path=backup_path, theme=theme, library=library,
                    import_library=import_library,
                    import_writer=import_writer)
    try:
        editor.run()
    finally:
        report_session(editor.state, report,
                       list(notes or []) + list(editor.import_notes))
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

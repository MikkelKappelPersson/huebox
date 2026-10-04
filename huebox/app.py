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
from textual.strip import Strip
from textual.style import Style
from textual.widget import Widget

from .color import MISSING, SLOTS
from .editor import (MULT_STEPS, EditorState, apply_key, backdrop,
                     draw_editor, grid_geometry, head_label, report_session,
                     session_path, slot_at, theme_lines, too_small_frame)
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


class Editor(App):
    """One editing session, under Textual's compositor.

    `write` is the session's one save path and `library` the picker's seam onto
    the theme store — the same four injected seams `edit()` took, unchanged, so
    `cli` builds them once and both the tests and this shell consume them.
    """

    ENABLE_COMMAND_PALETTE = False
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
        else:
            rows_text = frame_rows(self.fmt, session_path(state), state,
                                   width, height, head=self.head_for(state),
                                   hits=self.hits, regions=self.regions)
        self.rows_text = rows_text

        for child in list(self.query(Frame)):
            child.remove()
        # §5.6 — one widget per block of the frame. The blocks are the rows
        # `draw_editor` reported, in order and without gaps, so the widgets stack
        # to exactly the frame's height and not one row more: a stack taller than
        # the screen would give the screen a scrollbar, which is seven of
        # Textual's 168 design tokens arriving in the frame's first paint.
        named = ([("picker", 0, len(rows_text))] if picker is not None
                 else list(self.regions)
                 or [("frame", 0, len(rows_text))])
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
        # are already on their way to `Swatches.action_slot`.
        event.stop()
        if isinstance(self.focused, Swatches) and event.key in GRID_KEYS:
            return
        apply_key(translate(event.key), self.state)
        if self.state.quit:
            self.exit()
        else:
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
        target = slot_at(self.hits, event.offset.x, event.offset.y)
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

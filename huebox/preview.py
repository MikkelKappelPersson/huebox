"""Shared preview units: one rendering of a theme, asked from two layouts.

Spec `docs/003-import-menu/spec.md` §6: the editor frame and the import
popup preview must call the same code — the examples strip, the code
sample and the palette swatch cells, plus the compact 16-slot palette
form the popup needs. This module is that code.

Pure like `render.py`: slots in, ANSI rows out, colours read per call, no
stored theme, no Textual/widget imports. Dependency direction is
`preview` → `render` + `color` only — `render` stays `color`-only, and
`editor`/`app` import this module without cycles.

What lives where: `example_lines` / `sample_lines` stay defined in
`render.py` (the strip and the sample never lived anywhere else) and are
re-exported here so the popup imports one module. The swatch-cell family
(`swatch_cell`, `named_cell`, `named_cell_width`, `NAMED_ABBR`) moved here
from `editor.py`, which re-exports them for compat. `palette_rows` /
`interface_rows` are the row assemblers both layouts share: the editor
frame calls them with its live `Grid` shares, the popup with its own
compact shares.

Compact form (defined once, here): the palette as a grid of swatch cells
— row 0 the base half, row 1 the bright half at `per_row=8`, mirroring the
`0-7 base / 8-15 bright` reading — with the hex hidden (`show_hex=False`)
where the caller is narrow. All 16 slots stay readable at once; nothing
here clips, pads to a frame, or paints a background: indent, `clip` and
`backdrop` are the caller's layout job, so the same rows mount in the bare
frame and in a popup `Static` alike.

Paired interface (defined once, here): the 6 interface slots as two rows
of three abbreviated cells — foregrounds above backgrounds
(`FG CC SB` / `BG CT SF`), so each column is one pair. Same cells as
`interface_rows` (`abbrev=True`, no hex), only the grouping differs; the
popup asks `palette_interface_rows` for both blocks on the same two lines
where they fit, else `palette_rows` plus `interface_pair_rows` stacked.

`TOKEN_SLOTS` note (for the P5 docs pass): no new entries. Every colour
these units emit is read out of a theme slot (`palette-*`, `background`,
`foreground`, …) on the call, so the I2 colour-closure property the popup
inherits is structural — a row cannot name a colour its slots did not give
it. If a future preview block ever needs a token `TOKEN_SLOTS` does not
map, that entry must map a theme slot only.
"""

from __future__ import annotations

from .color import MISSING, NAMED, SLOTS, readable_fg
from .render import BOLD, RESET, bg, example_lines, fg, sample_lines

__all__ = [
    "CELL_FULL",
    "CELL_MIN",
    "INTERFACE_PAIRS",
    "NAMED_ABBR",
    "NAMED_ABBR_W",
    "NAMED_COL_W",
    "NAMED_MIN_W",
    "example_lines",
    "interface_pair_rows",
    "interface_rows",
    "named_cell",
    "named_cell_width",
    "palette_interface_rows",
    "palette_rows",
    "sample_lines",
    "swatch_cell",
]

# A palette swatch, with and without its hex value; the frame drops the hex
# before it drops a swatch, and gives up cells before width (§15.2).
# Canonical home of the widths `editor.py` used to own: `draw_editor` and
# `render_preview`'s ladder agreed on 13/6 by coincidence, and a third
# caller must not mint a third pair.
CELL_FULL, CELL_MIN = 13, 6
# The width of one interface cell: `mark name hex`, the name padded to 21.
NAMED_COL_W = 32
#: Two-letter abbreviations for the interface slots, for the narrow rungs
#: of the width ladder (§15.2): the full names are what make a bare cell 32
#: wide, so below 68 columns the frame holds two cells abreast by printing
#: these instead — `background` is back+ground, `foreground` fore+ground,
#: the rest read off the words. The hex goes last, past what any frame at
#: or above `MIN_COLS` reaches.
NAMED_ABBR = {"background": "BG", "foreground": "FG",
              "cursor-color": "CC", "cursor-text": "CT",
              "selection-background": "SB",
              "selection-foreground": "SF"}
#: An abbreviated interface cell with its hex (`mark abbr hex`), and
#: without: the floor rung, past the narrowest frame the editor draws.
NAMED_ABBR_W, NAMED_MIN_W = 13, 4


def swatch_cell(slots, index, sel, show_hex, width=None):
    """One palette swatch: `> 4 #rrggbb` in its own colours.

    The same expression `draw_editor` always painted, hoisted so the
    side-by-side left panel — and now the import preview — cannot drift
    from it: one implementation of what a swatch says, asked from every
    layout. `width` pads the swatch past its own cell (the pad rides inside
    the painted span, so the colour field grows): the side layout passes
    the interface cell width so both grids stand in the same two columns.
    The text stays left-aligned in the field, so the `>` marks line up down
    the column. `sel` outside 0-15 marks nothing (the read-only preview
    passes -1).
    """
    value = slots.get(f"palette-{index}", MISSING)
    mark = ">" if sel == index else " "
    cellw = width or (CELL_FULL if show_hex else CELL_MIN)
    label = (f" {mark}{index:>2} {value} " if show_hex
             else f" {mark}{index:>2}")
    return (f"{bg(value)}{fg(readable_fg(value))}"
            f"{BOLD if sel == index else ''}{label.ljust(cellw)}{RESET}")


def named_cell_width(abbrev=False, show_hex=True):
    """The painted width of one interface cell (§15.2)."""
    if not abbrev:
        return NAMED_COL_W
    return NAMED_ABBR_W if show_hex else NAMED_MIN_W


def named_cell(slots, key, sel, abbrev=False, show_hex=True):
    """One interface cell: `mark name hex`, the name padded to 21.

    Same hoist as `swatch_cell`: the side layout's `background/foreground`
    rows are these cells, not a second rendering of them. `abbrev` prints
    the two-letter `NAMED_ABBR` form and `show_hex` the value — the narrow
    rungs of the width ladder, asked from the grid, never re-derived here.
    """
    index = SLOTS.index(key)
    value = slots.get(key, MISSING)
    mark = ">" if sel == index else " "
    style = BOLD if sel == index else ""
    name = NAMED_ABBR[key] if abbrev else f"{key:<21}"
    label = (f" {mark}{name} {value} " if show_hex
             else f" {mark}{name}")
    return (f"{bg(value)}{fg(readable_fg(value))}{style}"
            f"{label.ljust(named_cell_width(abbrev, show_hex))}{RESET}")


def palette_rows(slots, sel=-1, per_row=8, show_hex=False, width=None):
    """The 16 palette slots as rows of swatch cells — the compact form.

    One row per `per_row` slots, each row the plain join of its cells
    (unstripped of nothing but what `swatch_cell` pads: callers add their
    own indent and `rstrip`, so the bare frame's `(indent + row).rstrip()`
    and the popup's `backdrop` share the cells, not the chrome). The
    default is the compact popup share: eight cells a row, hex hidden, two
    rows holding all 16 at once in 48 columns. `sel=-1` marks nothing.
    """
    rows = []
    for start in range(0, 16, per_row):
        rows.append("".join(swatch_cell(slots, i, sel, show_hex, width)
                            for i in range(start, min(start + per_row, 16))))
    return rows


def interface_rows(slots, sel=-1, per=2, abbrev=False, show_hex=True):
    """The interface slots as rows of named cells, two-up by default.

    Same contract as `palette_rows`: cells joined with two spaces, no
    indent, no strip — the caller owns the row's edges. The default share
    is the full two-abreast form; narrow callers ask `abbrev=True` down the
    same ladder `grid_geometry` walks.
    """
    rows = []
    for start in range(0, len(NAMED), per):
        rows.append("  ".join(named_cell(slots, key, sel, abbrev, show_hex)
                              for key in NAMED[start:start + per]))
    return rows


#: Paired columns for the popup: each column is one pair — foregrounds on
#: the top row, backgrounds below — so `FG` stands over `BG`, `CC` over
#: `CT`, `SB` over `SF`.
INTERFACE_PAIRS = (("foreground", "cursor-color",
                    "selection-background"),
                   ("background", "cursor-text",
                    "selection-foreground"))


def interface_pair_rows(slots, sel=-1):
    """The 6 interface slots as two paired rows: `FG CC SB` / `BG CT SF`.

    Same cells as `interface_rows` with `abbrev=True, show_hex=False` —
    the compact share the popup stacks under the palette where the two
    blocks do not fit side by side. `sel=-1` marks nothing.
    """
    return ["  ".join(named_cell(slots, key, sel, abbrev=True,
                                  show_hex=False)
                       for key in pair)
            for pair in INTERFACE_PAIRS]


def palette_interface_rows(slots, sel=-1, sep="  "):
    """Palette plus paired interface on the same two lines.

    Row 0 is palette `0-7` beside `FG CC SB`, row 1 palette `8-15` beside
    `BG CT SF` — base/bright on the left, one interface pair per column on
    the right, so the two blocks line up instead of stacking to four rows.
    Same cells as `palette_rows` plus `interface_pair_rows`, joined with
    `sep`; like both, no indent, no clip, no background — the caller owns
    the edges and checks the fit before asking.
    """
    pal = palette_rows(slots, sel=sel)
    pairs = interface_pair_rows(slots, sel=sel)
    return [left + sep + right for left, right in zip(pal, pairs)]

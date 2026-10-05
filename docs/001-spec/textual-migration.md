# huebox — Textual migration

Status: **phases 0–5 landed; the picker, the frame's blocks and the grids extracted; two launch-contract defects fixed; no TODOs open** · Plans spec:
`spec.md` §5, §8.2, §14.1, §15, §9, §10 · Conventions: `AGENTS.md`

A plan-class document, like `plan.md`, not a format version. `spec.md` moves to
`docs/002-spec/` only when the colour model or the file contract changes in a
way that breaks existing configs; this migration changes neither — the 22
slots (§5) and the line-level write contract (§6.2) are untouched — so it lives
here and is cited by section.

The deal this document makes: **Textual may own the screen, and not one cell
of the frame may change colour.** Everything below exists to make that claim
testable rather than aspirational, because it is the one promise worth
breaking huebox over, and the one a migration breaks silently if left
unwatched.

**Where the research stands.** Every claim about Textual in here is measured
against **8.2.8**, not recalled: the 168 tokens (§6.2), the `get_css_variables`
override (§6.2), the eight environment variables that can rewrite its output
(§6.4), the phase-A mechanism reproduced end to end through a real compositor
and a real VT emulator (§5.5), and the 256-colour degradation confirmed with
numbers (§4.5). Three findings changed the document: the colour system is read
from the environment at import time and cannot be corrected in-process; huebox
had a pre-existing one-line scroll defect that cost it the wordmark row at 80x24
(§4.8, now fixed); and the frame's height turned out to be content-dependent,
not the formula the harness had fitted to the broken frame.

---

## 1. Problem

The editor's frame is correct and pretty. Its *machinery* is the cost:

- `tui.py` is 112 lines of hand-rolled `termios`/`select`/`SIGWINCH`, including
  a hand-written SS3 escape parser with a 50 ms ambiguity window (`tui.py:93`).
- `editor.py` is 917 lines, of which the draw loop, the raw-mode prompt dance
  (`editor.py:834`) and the modal picker state machine are all hand-rolled.
- Clicking is impossible; there is no mouse tracking, no hit-testing, no focus.
- Scrolling is impossible; the picker recomputes a window by hand.

Textual gives mouse, focus, scroll, resize, layout and polish for free, and is
the only serious option whose colour model fits (§6). What it costs is the
compositor — and the compositor is the risk, not the widgets.

## 2. Goals

1. **Colour equivalence.** For every fixture and size, the migrated editor's
   cell grid equals today's, cell for cell (§4.1).
2. **Closure.** No colour appears in the frame that is not one of the theme's
   own 22 slots (§4.2).
3. Mouse: click a swatch to select it, click a picker row to open it, scroll a
   list longer than the screen.
4. Polished layout: panels, borders, focus rings — all painted in theme slots.
5. `render.py` stays pure and its existing tests keep passing untouched.
6. Non-interactive commands (`show`, `list`, `detect`, `use`) gain **no**
   dependency.

## 3. Non-goals

- New config keys, new formats, new CLI surface.
- Changing the colour model (§5), the canonical theme file, or the write rules
  (§6.2). Byte-identical no-op writes stay byte-identical.
- Improving `render.py`'s *visual* output. Any frame change is a separate
  change, landed after this, and must be deliberate.
- Reflowing the layout. Textual's CSS layout is deliberately **not** used to
  lay out the frame in phase A; see §5.5.

## 4. The invariant — colours 1:1

The whole migration is governed by one harness. If it is not understood and
built first, nothing else in this document is safe to attempt.

### 4.1 I1 — equivalence

For a fixture `(slots, cols, rows, sel, mult, head, status)`, two cell grids
are produced and compared:

- **Reference.** Today's code: `render` rows → the exact SGR string they emit
  today → fed to a `pyte.Screen`. No framework, no terminal, no Textual.
- **Candidate.** The migrated app, run headless in a pty, its captured byte
  stream → `pyte.Screen`.

Both grids are compared cell for cell over `char`, `fg`, `bg`, `bold`,
`reverse`. `pyte.screens.Char` is a NamedTuple with exactly these fields and
normalises every colour to a 6-hex-digit string or the sentinel `'default'`,
so the comparison is apples-to-apples across encoding.

`I1 holds` ⟺ the two grids are equal across the frame region.

**The frame region is declared, never inferred.** Two traps, both found by
running the harness before writing it:

- `pyte`'s `Screen.buffer` is a sparse `defaultdict` keyed by row, and huebox's
  `\033[2J` (`editor.py:276`) **registers erased rows as present-and-default**.
  At 120x50 that put rows 39–49 in the buffer, which a "rows present" derivation
  would have mistaken for frame and failed on 1320 cells that are correctly the
  terminal's own.
- Deriving the region from the *painted extent* instead would be circular — I2
  would be checking its own premise.

So the harness takes the region from the **reference side's own geometry**: the
number of rows `render.py` emitted (the length of the row list behind
`"\r\n".join(...)`) times the fixture's `cols`. That is independent of both
grids under comparison, so I2 stays falsifiable. The harness separately
asserts that the candidate's painted extent equals the declared region, which
is what catches a migrated app that paints short.

Measured frame height is `min(rows - 1, 39)` — it fills the screen at 80x24
(23 rows) and 100x40 (39 rows), and stops at 39 in a 50-row terminal, leaving
rows 39–49 to the terminal per §8.2.

### 4.2 I2 — closure

`I1` alone cannot see a colour that Textual introduced in a place today's frame
happens to agree with, nor one that only differs in a widget the fixture does
not reach. So a second, independent invariant:

> **I2.** No cell in the frame region has a `bg` of `'default'`, and every
> non-`'default'` `fg` and `bg` in it is a member of the **declared closure
> set** below.

The closure set is **not** just the 22 slots. Measured against today's editor
at 80x24 (§4.7), the frame legitimately paints **39 distinct colours**, of
which 27 are outside §5, from three sources:

1. **The 22 slots** (§5).
2. **Pygments' default token style** — `render.py:451` calls `lex()` with no
   `style=`, so the code sample wears Pygments' built-in palette: `ffffff` on
   `000000`, 314 cells of it. The sample is *not* theme-coloured today.
3. **The §8.3 bar sweeps** — `_chip` (`render.py:298`) interpolates along the
   axis with `rgb_to_hex(colour(step))`, so each bar paints a run of derived
   colours no slot holds. 31 of them at 100x40 (`00002e`, `06062e`,
   `0c0c2e`, …). Computable from the fixture, not literal.

**Source 3 is size-dependent, and that is the more surprising finding.** At
80x24 the frame is 16 colours and only 2 stray — the bars do not fit and never
paint. At 100x40 it is 47 colours and 33 stray. So a closure check written
against 80x24 alone would pass while the bars paint thirty-one unslot colours
at any size where they are visible. The closure set must therefore be computed
from what `render.py` actually emits for the fixture, not hand-listed.

I2 is stated over that union, and its job is to catch a **fourth** source:
Textual's scrollbar thumbs, focus rings, border fallbacks, dimmed `Footer`
text, any CSS rule that fell through to a default token. If a built-in widget
cannot be brought inside the closure, the answer is to not use it.

The `bg == 'default'` half is §8.2 stated mechanically. It holds at every
measured size with **zero** violations, which makes it a free regression guard
for the property the migration is most likely to break.

Together: I1 says *we did not change*; I2 says *nothing new leaked in*. A
migration that passes both has not altered the frame and has not smuggled a
fourth colour source past it.

### 4.3 Where `I1` is asserted

`tests/test_equivalence.py`, against fixtures in `tests/harness.py`. Three, each
for a reason rather than for coverage:

- **`distinct`** — twelve distinct hues plus the six named slots, so no two
  slots collide and a swapped pair is a visible changed cell.
- **`dark`** — near-black on near-black (`#010101` background, `#030303`–`#060606`
  foregrounds). The worst case for a colour that leaked in from a default: if
  Textual's own background ever shows through, a slot-membership check will not
  notice. Its config path is also `/tmp/[huebox]/kitty.conf`, so this fixture is
  the markup guard (§6.3) as well as a colour one.
- **`missing`** — every slot `MISSING` (`#808080`), which is also `palette-8`'s
  value. The frame cannot tell one from another, so it catches a widget that
  reaches for the wrong *slot* rather than a merely wrong colour.

At the four §10 sizes, and with the selection walked to `sel=0` and `sel=21` —
the first and last of the 22 slots (§5), the two moves that would repaint the
wrong cell.

### 4.4 Goldens — capture before touching anything

The reference side must be **frozen**, or the two sides can drift together and
the comparison stays green while the frame changes underneath it.

Phase 0 therefore commits `tests/golden/<size>/<fixture>.json` — the reference
grid, cell for cell — generated by today's code before a single line of the
migration exists. From then on I1 asserts *candidate == golden*, not
*candidate == reference*. Regenerating a golden is a spec change, reviewed as
one, and the diff of the golden is the review artefact: a frame change shows up
as a literal list of changed cells with their old and new colours.

This is the mechanism that makes the migration reviewable. Without it, "the
tests pass" is not evidence of anything.

### 4.5 Depth probe — the 256-colour blind spot

`pyte` normalises `38;5;196` and `38;2;255;0;0` to the same hex. So a Textual
widget that quietly degrades to the 256-colour palette is **invisible** to I1
and I2 — on a truecolor terminal the two are indistinguishable, and the frame
looks correct until someone runs over SSH on a 256-colour host.

**Confirmed, with the mechanism, against Textual 8.2.8.** The same phase-A app
(`/tmp/probe` shape of §5.5) run twice, changing only the terminal's colour
system:

| `TEXTUAL_COLOR_SYSTEM` | `#1e1e2e` becomes | `#7dc4e4` becomes |
| --- | --- | --- |
| `truecolor` | `1e1e2e` | `7dc4e4` |
| `256` | **`000000`** | **`87d7d7`** |

Every cell differed, and nothing in the frame said so. The setting is read from
the environment **at import time** (`textual.constants.COLOR_SYSTEM`, default
`auto`), so it cannot be fixed inside the running app — it has to be pinned in
the candidate's environment, and `auto` must never be what the harness sets.

So every fixture runs **twice**, the candidate launched once at each depth, and
both grids must equal the golden. The depth is set by the env, not by
`COLORTERM` alone: `TEXTUAL_COLOR_SYSTEM` plus `TERM`/`COLORTERM` together.

### 4.6 What the harness cannot see

Stated plainly, so it is not over-trusted:

- Wide-glyph and combining-mark **placement** — `pyte` reports a wide char as
  one cell with a trailing empty cell; huebox's `visible()`/`clip()` arithmetic
  (§8.1) is still the source of truth for width and keeps its own unit tests.
- Anything outside the frame region. §8.2 leaves the terminal's own colours
  there by definition; the alt-screen change (§7.1) is asserted separately, not
  by I1.
- Terminals whose own truecolor handling differs. Out of scope.

### 4.7 The measured baseline

Phase 0 records these from the editor, so a later regression is a diff against
a known value rather than a sense that something looks off. Recorded after the
§4.8 fix, via `pyte` 0.8.2, `sel=0`:

| Size | Frame rows | `bg == 'default'` in frame | Colours: `distinct` / `dark` / `missing` |
| --- | --- | --- | --- |
| 100x30 | 0–28 (29) | **0** | 50 / 55 / 17 |
| 80x24 | 0–23 (24) | **0** | 19 / 24 / 2 |
| 60x16 | 0–14 (15) | **0** | 19 / 24 / 2 |
| 40x12 | 0–11 (12) | **0** | 18 / 20 / 2 |

Three things to read off it. The `0` column is the load-bearing one: §8.2 says
no column of the frame shows the terminal's background, and here it is a number
rather than an aspiration. The colour count is **not** stable across sizes —
19 at 80x24 against 50 at 100x30 — because the bars only appear once they fit,
so a closure check written from the smallest fixture alone would be checking
almost nothing. And `missing` collapses to 2 colours at three of the four
sizes, which is the trap working as intended: one grey, plus Pygments.

**Frame height is not a function of the terminal size.** 80x24 fills all 24
rows; 60x16 stops at 15. It is `len(body + extra + tail)` after the layout has
decided what fits, so it depends on the content as much as the room. An earlier
version of this document put `min(rows - 1, 39)` in the spec and the harness
carried a function by that name — and both were wrong at four of six sizes,
because the formula had been fitted to a frame that had already lost a row to
the §4.8 scroll. The height is now read from the reference's own output, and
the test asserts *no rows lost* rather than a formula.

Goldens are flat per-cell records, ~44 KB each and 524 KB in total for all
twelve. Run-length encoding was tried and dropped: measured against the real
frame it compressed nothing, because a row is full of SGR 0s and so no two
neighbouring cells ever share all four fields.

### 4.8 A defect the harness found in huebox, not in Textual — fixed

Phase 0's goldens came out off by one row at 80x24, and the harness turned out
to be right and the frame wrong.

At any size where the renderer emitted as many rows as the terminal had, the
frame **scrolled up by one line**: the top row was lost, and the terminal's own
background showed at the bottom. Measured:

| Size | Rows emitted | Terminal rows | Frame survived |
| --- | --- | --- | --- |
| 100x30 | 29 | 30 | yes |
| **80x24** | **24** | **24** | **no — scrolled** |
| 60x16 | 15 | 16 | yes |
| **40x12** | **12** | **12** | **no — scrolled** |
| **100x40** | **40** | **40** | **no — scrolled** |

The mechanism was in `draw_editor`: the frame was written as
`"\r\n".join(backdrop(...) for line in out) + "\r\n"`. When `len(out)` equalled
the terminal's row count the last row landed on the bottom line and the
**trailing newline scrolled the screen** — one row gone at the top. At 80x24 the
row that disappeared was the `huebox` wordmark.

Pre-existing, unrelated to Textual, and it hit 80x24 — the commonest terminal
size there is. Invisible until something parsed the output as a terminal rather
than as a string, which is all the harness did.

**Fixed.** The trailing CRLF is now withheld exactly when the frame fills the
screen:

```python
sys.stdout.write("\r\n".join(backdrop(line, slots, cols) for line in out)
                 + ("\r\n" if len(out) < rows else ""))
```

`out` never exceeds `rows`, so `len(out) == rows` is the only scrolling case; a
row short of the bottom keeps the newline, which is harmless there and keeps the
cursor off the frame's last line. All four sizes now show the wordmark on row 0
with painted rows contiguous from 0.

Two things the fix had to drag along with it, both found by the suite rather
than by reading:

- `tests/test_editor.py`'s `lines()` did `split("\r\n")[:-1]` with the comment
  *"the frame ends with a newline"* — now conditional on the trailing element
  being empty. An unconditional `[:-1]` would have silently dropped a real row.
- The goldens are **re-recorded**, because the phase-0 set encoded the scrolled
  frame. Left alone, I1 would have *enforced* the bug: a migrated editor would
  have had to reproduce the missing wordmark to pass.

## 5. Architecture

### 5.1 Untouched — and this is most of the codebase

| Module | Lines | Why it is untouched |
| --- | --- | --- |
| `color.py` | 52 | It *is* the colour model (§5) |
| `formats/` | 177 | Line-level write contract (§6.2) |
| `detect.py` | 369 | Probe/env/include logic (§7) |
| `themes.py` | 860 | Storage, push, native export (§13) |
| `render.py` | 679 | Pure; consumed, not rewritten (§5.2) |
| `cli.py` | 562 | Argparse and dispatch (§4) |

Roughly 2,680 lines and every spec-critical write guarantee sit outside this
migration. The §6.2 guarantees — compare first, write second; a no-op is
byte-identical *and* never opens the file — are the last things anyone should
risk, and they are not at risk.

### 5.2 `render.py` — same bytes, new interface

`render.py` keeps emitting SGR strings, unchanged. Two interface adjustments:

1. **Per-row.** Today `draw_editor()` returns one string with cursor moves
   embedded (`editor.py:276`). Under Textual the compositor positions cells, so
   the frame must be consumable row by row. The painted bytes of each row are
   **unchanged** — that is what I1 pins.
2. **`MIN_COLS`/`MIN_ROWS` move here** from `tui.py:12`. They are layout policy
   and `render.py` is the surviving pure module; the too-small hint
   (`editor.py:178`) is a rendering decision, not a terminal-I/O one.

The existing 60 tests in `tests/test_render.py` keep passing as written. That is
a deliberate gate: if the migration forces changes to those tests, the migration
is wrong.

### 5.3 `tui.py` retired

`term_size`, `enter_raw`, `exit_raw`, `read_key`, the `SIGWINCH` handler and
`_resized` are all Textual's. Deleted. Nothing else imports it except
`editor.py` and `cli.py`'s size checks.

### 5.4 `editor.py` → `app.py` + widgets

The state machine survives verbatim — `move_slot` (§4.3.1), the undo stack,
picker state, blocked-while-dirty, new-from-buffer, save-as-new (§13.7). Only
its *drivers* change: `apply_key` becomes key bindings, the draw loop becomes
`refresh()` on a reactive buffer, and `_prompt`/`prompt_hex`/`prompt_name`
become Textual input widgets (§4.3's leave-and-reenter-raw-mode is then
moot).

The four injected seams stay (§ AGENTS.md): save callback, `prompt_hex`,
`prompt_name`, `Library`. They are what make the app swappable, and this
migration is the payoff for having drawn that line.

### 5.5 Phase A — one custom widget

Phase A renders the entire frame in **one** custom `Widget` whose
`render_line(y)` returns a `Strip` of segments built from the row `render.py`
emitted, parsed with `rich.text.Text.from_ansi`. Verified working on Rich
15.0.0:

```
>>> Text.from_ansi('\033[38;2;255;0;0mred\033[48;2;0;0;255mon blue\033[0m tail').spans
[Span(0, 3, '#ff0000'), Span(3, 10, '#ff0000 on #0000ff')]
```

**And verified end to end, which is the reason phase A is not a hope.** A probe
app — one widget, `render_line` returning `Strip(segments, width)`, huebox's own
rows as its input — was run in a pty at 80x24 and its bytes parsed back through
`pyte` (§4.1). Every colour and every attribute matched: foreground `7dc4e4`,
background `1e1e2e`, bold flag set, per cell. The only differences were the
probe's own bugs, not Textual's. `render.py` needs no rewrite at all — the SGR
it already emits is consumed unchanged, through a real compositor, to exact
24-bit output.

Mouse is added by hit-testing the click against the grid geometry
`editor.py:90` already computes. Scrolling is a `ScrollView` wrapper.

This is deliberately the *least* Textual-shaped version, and it is the right
first step: it moves the compositor underneath the frame while keeping the
frame's construction identical, so I1 has one variable at a time.

### 5.6 Phase B — decomposition, gated

Only after A is byte-identical is the frame split into real widgets: palette
grid, interface grid, code sample, examples strip, picker list. Each extraction
lands as its own commit and must re-pass I1 and I2 before the next. A widget
that cannot be made theme-closed is not extracted.

**I1 covered one frame of two.** Phase 4 found the gap the hard way: the
picker's own frame is drawn by `theme_lines`, has its own windowing, and had
been free to change without anything noticing. So before any widget work the
picker was pinned the same way the frame was — 36 goldens, 3 scenarios × 3
fixtures × 4 sizes, and I2's closure over them (`--record-picker`). The three
scenarios are chosen by what they can catch:

| Scenario | Library | What it is for |
| --- | --- | --- |
| `short` | 3 themes, 7 rows | A picker **shorter** than the screen. The case a `ScrollView` gets wrong: a scrolling container fills its viewport, so the natural widget version paints sixteen rows of background where the golden has seven. Visually identical, and seven cells I1 will not forgive. |
| `edge` | 20 themes, exactly 24 rows at 80x24 | Fills the screen to the row — where a trailing newline scrolls. |
| `long` | 34 themes, selection at `t25` | Overflows at every size, with the window partway down the list. A golden pinned to the top cannot tell a scroll from a no-scroll. The current theme is `t20` and the selection `t25`, so `*` and `>` are **different rows** — the case where a scrolling widget conflates "selected" with "current" and quietly opens the wrong theme. |

Pinning it found a defect immediately. The overlay branch of `draw_editor`
wrote its trailing CRLF unconditionally, so a picker that filled the screen
scrolled the terminal and lost its top row — the `huebox  themes` header, at
60x16 with 34 themes. Same defect as the editor frame's (phase 0, `4b239fe`),
same fix: withhold the newline when the rows fill the screen. The picker
goldens were recorded once before the fix, which captured the bug, and
re-recorded after — the one legitimate §4.4 re-record, and it is why
`test_the_header_survives_a_library_that_fills_the_screen` exists.

**What B actually buys, said honestly.** The frame is an absolute, static
layout: nothing in it scrolls, and the only interactive thing in it is the
palette grid, which the arrows already walk. So decomposition is not buying
focus rings or smooth scrolling — it is buying *structure*: one widget per
block, each owning its own rows, so a change to the frame no longer means
changing a 200-line function that writes to stdout. The honest summary is that
B is a refactor with a test harness attached, not a feature.

Three extractions have landed:

| Extraction | Commit | What changed |
| --- | --- | --- |
| The picker | `9ecd8a9` | `draw_editor`'s `overlay=` mode is gone; `draw_editor` has one caller shape again |
| The frame's blocks | `d70c2af` | Nine `Frame`s stacked, from a map `draw_editor` reports |
| The palette/interface grids | `ff7dc07` | `Swatches`: focusable, holding the arrows as its own bindings |
| The code sample and the diff | this one | `Sample`, `Diff`: selectable, so their text can be copied |

**Focus is where decomposition starts paying, and it is not free.** Giving the
grids their own bindings immediately exposed a fact about Textual that a unit
test cannot reach: the app's `on_key` and the focused widget's binding *both*
see every key, and with both acting on an arrow the selection moved two slots
per press. Both handlers were "correct"; nothing had promised an order. The fix
is `GRID_KEYS` — an explicit list the app steps over — and the guard is
`test_an_arrow_moves_one_slot_when_the_grid_has_focus`, which can only be a
pilot test, because the whole question is what happens between the terminal and
`apply_key`. `run_test` costs 0.13s, which is affordable; a real terminal would
not have been.

Two more things only a running app could find:

- **Focus must be dropped, not left.** `on_key` steps over the arrows while a
  grid holds focus. A short frame puts the selected slot below the fold — 40x12
  shows twelve of twenty-two — so with focus left where it was, the arrows
  either moved a selection the user cannot see or did nothing at all.
- **The picker takes focus.** Focus left on a grid underneath it let an arrow
  move the *colour* selection behind a list the user is reading, which is the
  one thing §13.7 says cannot happen.

**The last extraction is the first one a user would notice.** The code sample
and the live diff are blocks whose whole point is that their text *leaves* the
editor — sample a colour, copy the hex — and as painted rows they were inert.
`ALLOW_SELECT` is the entire mechanism, and it needed two halves that no
screenshot shows:

- **The text.** `Widget.get_selection` asks the widget to `render()` and selects
  out of that Visual. These widgets have no `render()` — their rows are
  already-parsed `Text` — so the default found nothing: the screen highlighted
  while the clipboard stayed blank.
- **The paint.** Textual applies the selection style inside
  `Visual.to_strips`, the path a widget *with* a `render()` takes.
  `render_line` is the whole story here, so the style had to be applied by
  hand from the screen's `screen--selection` component styles. Skip it and a
  drag shows nothing at all.

And the colour is the theme's, because `TOKEN_SLOTS` now binds
`screen-selection-*` to `selection-background` / `selection-foreground` — the
same pair the picker marks a theme with. A selection in a theme editor that is
not the theme's colour is the whole subject of §6.2, and I2 cannot see it: the
goldens never have a selection down.

**One thing `ALLOW_SELECT = True` would have broken.** Textual makes *every*
widget selectable by default and the screen checks the *app's* `ALLOW_SELECT`,
so a plain block — a swatch, a header, a hint — would start a text selection
when clicked, and clicking a swatch is how a colour is selected. `Frame` turns
it off and `Selectable` turns it on. The failure would have been silent: the
selection looks like nothing happened.

**And then the harness itself lied, twice.** See §7: I1 compares one frame
against one golden and never sends a key, so a session built by the app — as
opposed to one built by `EditorState`, which is what the headless suites drive
— was never exercised at all. That gap hid a crash in every HSL key.

**And a bug I1 was structurally unable to see.** `redraw` read the frame's
height from the compositor and `draw_editor` read it from the terminal: one
number, two sources. The harness sets a pty's window size *before* launching,
so the two always agreed there and no golden could disagree. On a resize they
did not — and `on_resize` fires *before* Textual applies the new size, so the
frame was drawn for the window the user had just left, one resize behind,
forever. Three separate mistakes stacked in one line: two sources for one
number, no `size=` passed down, and a redraw in the wrong moment of the
refresh. `TheFrameIsSizedByTheCompositor` drives real resizes through
`pilot.resize_terminal` — a posted `Resize` event would have passed against the
very bug it is for, since it changes nothing the compositor agrees with.

**The map is the load-bearing part, and it is easy to get wrong.** A block's
first row is not knowable while the frame is being built: §15 spends decoration
rows before it spends widget rows, and dropping one moves everything below it
up. So the map is reported *after* the frame has given up its decoration and
trimmed itself — the same trap the hit map fell into in phase 4, and the same
answer (ask the rows that draw the thing, never recompute).

The first version kept a dropped checkpoint in the list instead of removing
it. That is invisible for every block *after* it and catastrophic for the one
before: a checkpoint past the bottom is also every later block's *end*, so
keeping it gave the block below a height reaching past the frame. At 40x12 that
was the difference between six widgets and ten, four of them hanging off the
bottom of the screen. `test_the_blocks_tile_the_frame` is the guard.

Two traps found on the way, both silent:

- The blocks must tile the frame **exactly**. One row more and the screen is
  taller than its viewport, which gives it a scrollbar — and seven of
  Textual's 168 design tokens exist only for scrollbars. I2 would have caught
  it; the assertion that catches it earlier is
  `test_the_widgets_stack_to_the_frame_and_no_further`.
- A test that watched `draw_editor` to see what each frame said stopped seeing
  picker frames once the picker had its own path, and the blocked-switch
  status is *reported on a picker frame*. A test asserting that message existed
  passed for the wrong reason. Second time a hook that could not see a thing
  has produced a green test that meant nothing; worth a standing rule.

## 6. Colour model → Textual

### 6.1 The 22 slots as CSS variables

`get_css_variables()` returns all 22 (§5): `palette-0`…`palette-15`,
`background`, `foreground`, `cursor-color`, `cursor-text`,
`selection-background`, `selection-foreground`, as `--huebox-<slot>`. Every
widget rule reads those and nothing else.

### 6.2 No Textual default may reach a cell

Textual's colour system is **168 named tokens**, not the handful of design
tokens one might guess at — `textual.design.ColorSystem(...).generate()` returns
all of them. They cover the scrollbar (seven: `scrollbar`, `scrollbar-active`,
`scrollbar-background`, `scrollbar-background-active`, `scrollbar-background-hover`,
`scrollbar-hover`, `scrollbar-corner-color`), the footer, buttons, inputs,
links, markdown headings, and the block cursor. Each also carries
`-lighten-1..3`, `-darken-1..3` and `-muted` variants.

Several line up with huebox's slots closely enough to bind directly:

| Textual token | huebox slot |
| --- | --- |
| `input-selection-background` / `-foreground` | `selection-background` / `selection-foreground` |
| `block-cursor-foreground` / `-background` / `-text-style` | `cursor-color` / `cursor-text` |
| `text` / `text-muted` / `text-disabled` | `foreground` / a muted chrome slot |
| `background`, `surface`, `panel`, `boost` | `background`, and the frame's own fill |
| `screen-selection-background` / `-foreground` | the selection pair again |

**The override mechanism is verified.** `App.get_css_variables()` returns a
mapping that is fed to the `Stylesheet` as-is, so returning token *names* works:

```python
class Probe(App):
    CSS = "Static { background: $panel; color: $text; }"
    def get_css_variables(self):
        return {**super().get_css_variables(),
                "panel": "#112233", "text": "#aabbcc"}
# -> w.styles.background == Color(17, 34, 51)
# -> w.styles.color        == Color(170, 187, 204)
```

So phase A binds **all 168** to theme slots — not the handful that happen to
appear in today's CSS. Any token a built-in surface reaches for then draws in
theme colour, and I2 is what proves it.

**One default is a live hazard.** `$text` is generated as `ansi_default` — the
terminal's own foreground, not a colour huebox chose. Any widget that falls
through to `$text` paints a colour outside the closure set, which is precisely
what I2 exists to catch, and precisely the sort of thing that looks fine on the
author's terminal.

> **Resolved.** The TODO that used to sit here — token names and the binding
> API — is answered above, against Textual 8.2.8.

### 6.3 Markup escaping

Rich interprets `[...]`. huebox displays file paths, theme names and hex
readouts, so a config named `[dracula].toml` would be swallowed or mis-styled.
Every dynamic string goes in with markup disabled or escaped. Add a fixture
named for it (§4.3) — a theme called `[x]` and a config path containing
brackets.

### 6.4 Determinism

The original worry here was a user config file. **There is none** — searching
Textual 8.2.8 finds no `~/.textual` theme or `.tcss` discovery. The real hazard
is the environment, and it is worse than a config file because it is invisible:

| Variable | Default | Effect on the frame's bytes |
| --- | --- | --- |
| `TEXTUAL_COLOR_SYSTEM` | `auto` | **colour depth — the §4.5 blind spot** |
| `TEXTUAL_THEME` | `textual-dark` | the base every token derives from |
| `TEXTUAL_FILTERS` | `""` | adds line filters; `dim` changes every cell |
| `NO_COLOR` | unset | installs a `Monochrome()` filter — kills all colour |
| `TEXTUAL_ANIMATIONS` | `FULL` | output cadence |
| `TEXTUAL` | `""` | features: `devtools`, `debug`, **`headless`** |
| `TEXTUAL_DEBUG`, `TEXTUAL_FPS`, `TEXTUAL_DRIVER` | — | logging, cadence, driver |

Textual also installs an `ANSIToTruecolor` line filter that converts ANSI
colours against the app's *own* ansi theme, and honours `NO_COLOR` with a
`Monochrome()` filter — both of which rewrite cells.

Every one of these is read at **import time** into `textual.constants`, so none
can be corrected inside the running app. The candidate must be launched as a
subprocess with all of them pinned (absent or explicit), plus `TERM`,
`COLORTERM`, `FORCE_COLOR`, `HOME` and `XDG_CONFIG_HOME`. Phase 0's
`color_depth_env()` is the seam; phase 2 widens it into the full pinned
environment. `auto` is never an acceptable value.

> **Resolved.** The TODO that used to sit here is answered above. The
> mechanism is not a config file at all, which is why the original wording was
> wrong.

## 7. User-visible behaviour changes

Called out because they are real and should be accepted deliberately.

### 7.1 Alternate screen

Today the editor writes to the normal screen (`\033[H\033[2J`,
`editor.py:276`), so frames land in scrollback. A full-screen Textual app uses
the alternate buffer and restores on exit. **Better** — no scrollback pollution —
but it is a change. Non-interactive commands are unaffected.

### 7.2 Mouse capture

Mouse support means SGR mouse reporting while the editor runs, so the user's
terminal stops passing clicks through and text cannot be selected with the
mouse mid-session. Expected for a full-screen app; worth a line in the README.
Click-to-close and focus-follows-mouse are the compensating polish.

**It is toggleable, and that closes the question.** `App.run(mouse=False)`
disables reporting for the session, and the driver honours it
(`LinuxDriver(..., mouse=False)` never emits the enable sequences). So a
`--no-mouse` flag is a one-line pass-through rather than a feature. It is not
built here — the editor is single-purpose — but the cost of adding it later is
one argument, and the README can say so honestly.

### 7.3 Render cadence

Textual runs a ~60 fps refresh loop with damage diffing, against today's
redraw-on-keystroke. Visually identical, slightly busier. If battery matters,
`App.CSS`/refresh throttling can cap it.

## 8. Dependencies and packaging

```toml
dependencies = ["Pygments>=2.0"]

[project.optional-dependencies]
editor = ["textual>=8"]
test   = ["pyte>=0.8"]
```

- **`textual` is an extra, not a dependency.** This is what keeps goal 6: `huebox
  show`, `list`, `detect` and `use` install with exactly one dependency, as
  §9 requires. `huebox edit` without the extra prints the install line and
  exits 1 — a clean error, not a `Traceback` (AGENTS.md).
- **`pyte` is a test-only extra.** It is never imported by `huebox/`.
- Textual bundles Pygments; huebox's own pin stays, since `render.py` uses it
  directly and independently.
- §9's "one third-party dependency" sentence is rewritten to: *one at runtime;
  the editor adds Textual, the suite adds pyte.* This is the amendment §9
  needs to accept the migration.
- Textual 8.2.8 requires `>=3.9`, so the §9 Python floor survives. (It does not
  survive prompt_toolkit 3.0.53, which is `>=3.10` — noted only because that
  ruled the alternative out.)

## 9. Testing

Unchanged and still mandatory: `python3 -m unittest discover -s tests -W always`,
with the same zero-warning contract (AGENTS.md).

Added:

| Suite | Asserts |
| --- | --- |
| `test_equivalence.py` | I1 at all four sizes × all fixtures, both depths (§4.5) |
| `test_closure.py` | I2 — no cell outside the 22 slots |
| `test_markup.py` | §6.3 fixtures |
| `test_render.py` | **unchanged** — 60 existing tests, as the gate on §5.2 |

The existing `tests/test_editor.py` (1,682 lines) is rewritten against Textual's
`run_test()` / Pilot, one flow at a time. Its flows are the regression suite
and their names should survive the rewrite so the diff shows behaviour, not
renames.

## 10. Rollout order

0. **Harness first, no product code.** Golden capture (§4.4), the pyte driver,
   the depth probe, I1 wired against *today's* editor. Phase 0 is done when
   I1 passes for the un-migrated editor — the test is green before it can
   possibly be useful, which is how you know it is measuring something.
   **Landed:** `tests/harness.py`, `tests/test_equivalence.py`,
   `tests/test_closure.py`, `tests/golden/`, and the `test` extra in
   `pyproject.toml`. 389 tests green under `-W always`, and green again with
   pyte absent (14 skipped), because pyte is an extra and not a dependency.
1. Packaging: the two extras, the `edit` guard, §9 amendment. No behaviour
   change. **Landed:** the `editor` and `test` groups in `pyproject.toml`, the
   `editor.REQUIRES` declaration with `cli`'s clean-failure guard, and §9's
   extras table. 395 tests green under `-W always`.
2. Textual shell around the existing draw: App, keys, resize, raw mode.
   `render.py` untouched; frame still painted by huebox. I1 green. **Landed:**
   `huebox/app.py` (one `Frame` widget over `draw_editor`'s captured rows,
   arrow bindings through `move_slot`, `on_resize` re-deriving at the new
   width), `tests/candidate.py` (the pty launcher with its environment pinned
   per §6.4), and `tests/test_app.py` for the launch contract and the token
   binding. `editor.REQUIRES` stays empty — `cli` still opens the stdlib
   session until phase 3, so naming `textual` now would break `huebox edit`
   for a module the command does not yet run. I1 and I2 both green at every
   size and fixture; §4.5's probe confirmed live against the app.
3. Phase A: the single custom widget, `render.py` per-row, `tui.py` retired,
   `MIN_COLS`/`MIN_ROWS` relocated. I1 + I2 green. **Partly landed.** The single
   custom widget was phase 2; this phase made the app the *session* — it drives
   `EditorState` and `apply_key` rather than reimplementing either, so selection,
   adjust, undo, revert, step size, the picker overlay, the save and the two-armed
   Esc are all the implementations the 400 existing tests already cover. `cli`
   hands it the four injected seams unchanged, `editor.REQUIRES` is
   `("textual",)`, `MIN_COLS`/`MIN_ROWS` moved to `render.py`, and the post-session
   report is `editor.report_session` so the wording stayed huebox's.
   `_run_editor` names its `driver`, which is what lets the wiring suites drive
   the session without a compositor.
   **Landed (the rest).** `tui.py` is `term_size` and nothing else: `read_key`,
   `enter_raw`/`exit_raw`, the SIGWINCH flag and the minimum size went with the
   code that used them, because Textual owns input, resize and raw mode and a
   second copy would be a second source of truth. `MIN_COLS`/`MIN_ROWS` moved to
   `render.py`, which keeps the purity that makes §14.1 testable at four sizes
   without a tty. `editor.edit` — the raw-mode loop — is deleted; its
   `report_session` half survived as a function so the wording of what a user
   reads on exit is still huebox's.
   The suites that drove it use `tests/session.py`, the same loop without the
   raw-mode half, since a key *list* needs no terminal. `test_editor`'s
   `RawMode` class is gone: it asserted an `enter_raw`/`exit_raw` pairing that
   no longer exists, and what replaces it (`TestTerminalHygiene`) asserts the
   promise that survives — a prompt hands the terminal back and cancels
   cleanly.
4. Mouse: hit-testing against the existing grid geometry. I1 unaffected —
   mouse changes input, not output. **Landed.** `draw_editor` and
   `theme_lines` now announce their own clickable cells (`hits=`), and
   `frame_hits` / `theme_hits` / `slot_at` are thin readers of that. Clicking a
   swatch or an interface cell sets the selection; clicking a picker row
   moves the picker's selection and lets `apply_key` do what Enter does, so the
   two cannot diverge; the wheel walks the picker and does nothing in the frame.
   I1 needed no change — not one golden moved — which is the phase's whole
   claim, asserted as a test (`a click that changes nothing paints nothing`,
   byte for byte).

   **Why the cells are announced rather than computed.** A second description of
   the layout is a second chance to point a click at the wrong cell, and the
   failure is silent — you select a colour you are not looking at and the log
   says nothing. So the row that draws a swatch records it, `test_editor`
   cross-checks every hit against the painted frame, and two cases that would
   otherwise have shipped wrong are the reason: the hit map was built at the
   *terminal's* width rather than the frame's, and a short frame's indices were
   taken before the decoration row was deleted.
5. Phase B: decomposition, one widget per commit, I1 + I2 before each next.
6. Polish: panels, focus, borders, scrollbar styling — theme-closed or not
   used (§6.2).
7. Docs: README mouse/scroll/alt-screen notes, AGENTS.md dependency rule,
   this document's TODOs closed.

Each phase keeps the suite green. **No phase starts before the previous one's
I1 is green.**

## 11. Risks

| Risk | Detected by |
| --- | --- |
| Silent colour drift | I1 + goldens (§4.4) |
| Textual chrome leaking a colour | I2 (§4.2) |
| Assuming the frame is only 22 colours | Closure set enumerated (§4.2) — it is 39 today |
| Palette degradation on a 256-colour host | Depth probe (§4.5) |
| Bracket injection from paths/theme names | `test_markup.py` (§6.3) |
| Developer-local Textual config skewing results | `HOME` redirect (§6.4) |
| Loss of pure-render testability | `test_render.py` unchanged (§5.2) |
| Colour system degrading in-process | Pinned env (§6.4); read at import time, so only the launcher can fix it |
| Scope creep into config writing | §5.1 — `formats/`, `themes.py` untouched |

## 12. Decisions

1. **Textual, not prompt_toolkit.** Textual has mouse, focus, polish and
   arbitrary `#rrggbb`; prompt_toolkit 3.0.53 requires `>=3.10`, breaking §9's
   floor. Both own a compositor; Textual's widget model is the better fit.
2. **`textual` is an optional extra.** Goal 6 — non-interactive commands keep
   one dependency (§8).
3. **pyte is the oracle.** It is a real VT emulator, so the comparison is
   against what a terminal would actually display, not against a model of it.
4. **Goldens are committed before the migration begins** (§4.4). Without a
   frozen reference the comparison can be green and wrong.
5. **Two invariants, not one.** I2 catches what I1 structurally cannot (§4.2).
6. **Every fixture runs at both colour depths** (§4.5). `pyte` normalisation
   hides palette degradation otherwise.
7. **Phase A before Phase B** (§5.5, §5.6). One variable at a time; B is
   optional, A is shippable.
8. **The alternate screen is accepted** (§7.1) — a net improvement, and the
   behaviour change is recorded rather than discovered.
9. **Frame layout stays hand-built.** Textual's CSS layout is not used to
   position frame content in this migration; reflow is a separate change.
10. **This document lives in `docs/001-spec/`.** The colour model (§5) and the
    file contract (§6.2) do not change, so `spec.md`'s own rule does not move it
    to `docs/002-spec/`.
11. **Fix the one-line scroll defect, then re-record goldens** (§4.8).
    Frame height is content-dependent, so at 80x24, 40x12 and 100x40 the frame
    filled the screen and the trailing newline scrolled it: the wordmark row was
    lost. The goldens captured in phase 0 encoded that loss, and I1 would
    otherwise *enforce* it — a migrated editor would have to reproduce the bug
    to pass. **Done**, with the goldens re-recorded and the tests asserting no
    rows lost. Pre-existing and unrelated to Textual; found by parsing output as
    a terminal instead of as a string.
12. **Bind all 168 tokens, not the ones today's CSS happens to use** (§6.2).
    `$text` alone is generated as `ansi_default`, so a single unbound token is
    enough to leak the terminal's own foreground into the frame.

## 13. Open questions

Everything this document used to leave open is now answered by measurement
against Textual 8.2.8. What is left is what is genuinely a decision for the
next person, not a fact to go look up.

1. ~~Exact Textual design-token names and the API that binds them~~ —
   **answered, §6.2.** 168 tokens; `get_css_variables()` overrides them by
   name; `$text` defaults to `ansi_default` and must be bound.
2. ~~Textual's user-config discovery path~~ — **answered, §6.4.** There is no
   user config file; the hazard is eight environment variables read at import
   time, listed and pinned.
3. Whether the theme picker becomes a real focusable list in phase B or stays a
   phase-A widget with key bindings. Still open, and deliberately per
   extraction: the answer depends on whether the list's rows survive I1 as
   separate widgets, which is only knowable once there is something to extract.
4. ~~Whether mouse support should be toggleable~~ — **answered, §7.2.**
   `App.run(mouse=False)`. Not building it; the cost of adding it is one
   argument.
5. Whether the diff widget (§14.4) is extractable under I2, or whether its
   paired-colour rows make it a permanent phase-A widget. Still open, for the
   same reason as 3 — but note that `diff_lines` emits `DIFF_ADDED`/`DIFF_REMOVED`
   in theme slots, so the closure set already covers it and I2 has nothing extra
   to say about it.
6. ~~The frame scrolls a line at 80x24, 40x12 and 100x40~~ — **answered and
   fixed, §4.8.** The trailing CRLF is withheld when the frame fills the
   screen; the goldens are re-recorded and `TestFrameGeometry` asserts no rows
   lost rather than a height formula, since the height turns out to be
   content-dependent.
## 14. What phase 2 actually found

Four things, none of which were guessable from the spec and two of which would
have been silent.

**The Linux driver writes the UI to `sys.__stderr__`**
(`textual/drivers/linux_driver.py:58`), on purpose, so that `print()` in an app
still reaches stdout without corrupting the display. A harness that captures
stdout gets an empty stream and reports *every colour as wrong* — which reads
as a catastrophic failure rather than as the wrong file descriptor.

**`pyte` replays the whole stream; no window needs cutting.** The plan was to
split at cursor-home and parse the last region, to exclude Textual's setup
chatter and its many damage repaints. Textual positions with `CSI row;col H`,
not bare `CSI H`, so the split never matched and the entire stream came out as
one region. Feeding everything to a real emulator lands on the settled screen
with no regex and nothing to keep in step with Textual's output format.

**The frame is not worth re-rendering.** `app.py` captures what
`draw_editor` wrote rather than reimplementing the construction. That makes
"the frame is unchanged" true by construction instead of by agreement, and it
is why phase 2 could be assembled in one sitting from a proven mechanism.

**Two values had to be shared or the hint row lies.** `draw_editor`'s `mult` is
printed verbatim by the hint line, so the reference's `False` and the app's
default `1` produced `f xFalse` against `f x1` — a genuine cell difference,
reported as bold and colour rather than as the label it was. Both sides now
take one constant from `harness`. The same review turned up the shell nudging
HSV by `0.01` where huebox nudges by `1/360` and `0.02`: no test would have
caught that, because it only shows up in a key press. The arithmetic now lives
once in `color.step_hsv`, used by `editor._adjust` and by the shell.

**Result.** I1 and I2 hold at all four sizes and all three fixtures: 0 of 1920
cells differing at 80x24, 0 new colours anywhere, 0 unbacked cells. §4.5's
probe, run against the real app rather than described, diverges in all 1920
cells at 256-colour — so the comparison can still fail, which is the only thing
that makes it mean anything.

Cost: the suite goes from 4 seconds to about 17, all of it pty launches, cut
from 34 to 22 by memoising captures. Without them the equivalence tests skip
rather than compare the reference with itself, which would be green and
meaningless.

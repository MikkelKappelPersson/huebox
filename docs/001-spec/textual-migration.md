# huebox — Textual migration

Status: **phases 0–1 landed, no TODOs open** · Plans spec: `spec.md` §5, §8.2,
§14.1, §15, §9, §10 · Conventions: `AGENTS.md`

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
numbers (§4.5). Two findings changed the document: the colour system is read
from the environment at import time and cannot be corrected in-process, and
huebox has a pre-existing one-line scroll defect that the harness found (§4.8).

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

Phase 0 records these from today's editor, so a later regression is a diff
against a known value rather than a sense that something looks off. Recorded at
`fd9439a`, `sel=0`, via `pyte` 0.8.2. **Frame height is `min(rows - 1, 39)`**,
and the renderer does not always emit as many rows as it paints:

| Size | Frame rows | Rows emitted | `bg == 'default'` in frame | Colours: `distinct` / `dark` / `missing` |
| --- | --- | --- | --- | --- |
| 100x30 | 0–28 (29) | 29 | **0** | 50 / 55 / 17 |
| 80x24 | 0–22 (23) | 24 | **0** | 19 / 24 / 2 |
| 60x16 | 0–14 (15) | 15 | **0** | 19 / 24 / 2 |
| 40x12 | 0–10 (11) | 12 | **0** | 18 / 20 / 2 |

Three things to read off it. The `0` column is the load-bearing one: §8.2 says
no column of the frame shows the terminal's background, and here it is a number
rather than an aspiration. The colour count is **not** stable across sizes —
19 at 80x24 against 50 at 100x30 — because the bars only appear once they fit,
so a closure check written from the smallest fixture alone would be checking
almost nothing. And `missing` collapses to 2 colours at three of the four
sizes, which is the trap working as intended: one grey, plus Pygments.

Goldens are flat per-cell records, ~44 KB each and 524 KB in total for all
twelve. Run-length encoding was tried and dropped: measured against the real
frame it compressed nothing, because a row is full of SGR 0s and so no two
neighbouring cells ever share all four fields.

### 4.8 A defect the harness found in huebox, not in Textual

Phase 0's goldens are off by one row at 80x24, and the harness turned out to be
right and the frame wrong.

At any size where the renderer emits as many rows as the terminal has, the frame
**scrolls up by one line**: the top row of the frame is lost, and the terminal's
own background shows at the bottom. Measured across the four sizes:

| Size | Rows emitted | Terminal rows | Frame survives |
| --- | --- | --- | --- |
| 100x30 | 29 | 30 | yes |
| **80x24** | **24** | **24** | **no — scrolled** |
| 60x16 | 15 | 16 | yes |
| **40x12** | **12** | **12** | **no — scrolled** |

The mechanism is in `draw_editor` (`editor.py:450`): the frame is written as
`"\r\n".join(backdrop(...) for line in out) + "\r\n"`. When `len(out)` equals
the terminal's row count, the last row lands on the bottom line and the
**trailing newline then scrolls the screen** — one row lost at the top, blank
line at the bottom. At 80x24 the row that disappears is the `huebox` wordmark.

This is pre-existing, unrelated to the migration, and hits 80x24 — the most
common terminal size there is. It was invisible until something parsed the
output as a terminal rather than as a string.

**Consequence for this migration, and it is a decision not an oversight:** the
goldens recorded in phase 0 encode the scrolled frame. Left alone, I1 would
*enforce* the bug — a migrated editor would have to reproduce the missing
wordmark to pass. So the goldens must be re-recorded after the defect is fixed,
and the fix lands first. That is decision 11.

> Not fixed in the phase-0/phase-1 commits, deliberately: it changes what the
> frame looks like, and §4.4 says a frame change is reviewed as a spec change.
> It is one line of `editor.py` plus a regression test; it just should not ride
> in on a migration commit.

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

The payoff of B is the polish — real focus, real scrolling, real hit targets —
but it is strictly optional. If B stalls, A is a shippable huebox with mouse
and scrolling. That is the point of the ordering.

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
   `render.py` untouched; frame still painted by huebox. I1 green. **The
   mechanism is already proven** (§5.5), so this phase is assembly rather than
   discovery — with the caveat from §6.4 that the candidate's whole environment
   has to be pinned before a single byte is trusted.
3. Phase A: the single custom widget, `render.py` per-row, `tui.py` retired,
   `MIN_COLS`/`MIN_ROWS` relocated. I1 + I2 green.
4. Mouse: hit-testing against the existing grid geometry. I1 unaffected —
   mouse changes input, not output.
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
11. **Fix the one-line scroll defect before re-recording goldens** (§4.8).
    Frame height is `min(rows - 1, 39)`, so at 80x24, 40x12 and 100x40 the
    frame fills the screen and the trailing newline scrolls it: the wordmark
    row is lost. The goldens captured in phase 0 encode that loss, and I1 would
    otherwise *enforce* it — a migrated editor would have to reproduce the bug
    to pass. Pre-existing and unrelated to Textual; found by parsing output as
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
6. **New, and the only one that blocks:** the frame scrolls a line at 80x24,
   40x12 and 100x40 (§4.8). Fix the defect and re-record the goldens, or accept
   it and let I1 enforce the missing wordmark? The spec says fix (§4.8,
   decision 11), and the fix is one line — but it changes what every golden
   contains, so it is worth saying out loud rather than slipping into a
   migration commit.
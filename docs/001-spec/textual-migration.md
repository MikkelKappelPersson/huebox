# huebox — Textual migration

Status: **proposed** · Plans spec: `spec.md` §5, §8.2, §14.1, §15, §9, §10 ·
Conventions: `AGENTS.md`

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

So every fixture runs **twice**: the candidate app is launched once with
`COLORTERM=truecolor` and once with `COLORTERM` unset and
`TERM=xterm-256color`, and both grids must equal the golden. Textual emits
`38;2` in the first and `38;5` in the second; if the region differs, the app
is not colour-faithful and the test says so.

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
`render_line(y)` returns the row from `render.py`, parsed with
`rich.text.Text.from_ansi`. Verified working on Rich 15.0.0:

```
>>> Text.from_ansi('\033[38;2;255;0;0mred\033[48;2;0;0;255mon blue\033[0m tail').spans
[Span(0, 3, '#ff0000'), Span(3, 10, '#ff0000 on #0000ff')]
```

`render.py` therefore needs no rewrite at all — the SGR it already emits is
consumed unchanged. Mouse is added by hit-testing the click against the grid
geometry `editor.py:90` already computes. Scrolling is a `ScrollView` wrapper.

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

Textual's own design tokens (`$primary`, `$surface`, `$panel`, `$boost`,
`$warning`, and the scrollbar/focus/border roles) must all be **bound** to
theme slots in phase A, so that any built-in surface that reaches the frame
draws in theme colour. I2 is what proves it did not.

Phase 0 has already shown what I2 will be doing. The `dark` fixture paints
**55** colours at 100x30 against `distinct`'s **50** on the same layout — the
near-black frame is where a leaked default is least visible and most worth
asserting on, which is why it is a fixture and not a note.

> **TODO** — confirm the exact token names and the binding API against the
> installed Textual before phase A; record the answer here. Not guessed at in
> this document.

### 6.3 Markup escaping

Rich interprets `[...]`. huebox displays file paths, theme names and hex
readouts, so a config named `[dracula].toml` would be swallowed or mis-styled.
Every dynamic string goes in with markup disabled or escaped. Add a fixture
named for it (§4.3) — a theme called `[x]` and a config path containing
brackets.

### 6.4 Determinism

Textual reads user configuration (a `~/.textual` theme/CSS file) and its own
themes. Under the harness that would make the candidate depend on the
developer's home directory and silently break I1 for everyone else. The
harness must neutralise it — `HOME` redirected to a fixture directory, and the
app's own theme set programmatically — and the app must never consult user
config for colour.

> **TODO** — confirm the config-discovery path and the override.

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
   change.
2. Textual shell around the existing draw: App, keys, resize, raw mode.
   `render.py` untouched; frame still painted by huebox. I1 green.
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

## 13. Open questions

1. Exact Textual design-token names and the API that binds them (§6.2).
2. Textual's user-config discovery path and how the harness neutralises it
   (§6.4).
3. Whether the Theme picker should become a real focusable list in phase B or
   stay a phase-A widget with key bindings — decided per extraction, not now.
4. Whether mouse support should be toggleable (§7.2).
5. Whether the diff widget (§14.4) is extractable under I2, or whether its
   paired-colour rows make it a permanent phase-A widget.
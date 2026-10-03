# huebox — specification

Status: **living draft** · Format version: **1** · Last updated: 2026-10-02

This is the working spec for huebox. It is a single living document: edit it in
place as the tool changes, and move it to `docs/002-spec/spec.md` only when the
colour model or the file contract changes in a way that breaks existing configs.

§§1–12 describe v1 as built. §§13–16 are the plan: the theme library, staged
editing with live examples, and a responsive layout.

Everything marked `TODO` is a decision we have not made yet — unless a `>` note
directly below it records the answer, which is how a closed one stays closed
and still shows what was asked. Everything else is either a rule the code
already enforces or a promise we intend to keep.

---

## 1. Problem

Changing a terminal theme normally means knowing the exact config key, the exact
colour format, and the exact reload shortcut for your terminal. The workflow is
edit-config-by-hand → get the syntax wrong → restart or reload → find out. Three
terminals with three different config dialects, and none of them agree on
naming.

## 2. Goals

- Edit every colour of a terminal theme without touching hex codes.
- Show the theme truthfully, including on non-TTY output like pipes and CI logs.
- Never damage a config file. Comments, ordering, alignment and unrelated
  settings survive a round-trip untouched.
- Work for Ghostty, kitty and Alacritty through one mental model.

## 3. Non-goals

- Not a general config editor. huebox owns the colour slots and nothing else.
- Not a theme store, gallery or sharing service.
- Not a screenshot or export tool.
- Not a terminal emulator; it never spawns one.

## 4. User-visible surface

> Since §13–§15: this surface grew theme commands (§13), save semantics
> changed (§14) and the layout follows the terminal (§15). The theme commands
> of §13.5 and the picker of §13.7 are live — `new`, `list`, `use`, `import`,
> `[name]` on `edit` / `show` / `--dump`, and `t` inside the editor — and a
> save or a `use` writes the theme file and then pushes it to the terminal
> (§13.6).

### 4.1 Commands

| Invocation | Behaviour |
| --- | --- |
| `huebox` | TUI editor when stdin **and** stdout are TTYs, otherwise a static preview |
| `huebox show` | Force the static preview |
| `huebox edit` | Force the interactive editor |
| `huebox --dump` | Print resolved colours as `# <format> <path>` + `key=value` lines, then exit |
| `huebox --formats` | List supported format names, one per line, then exit |

### 4.2 Flags

| Flag | Meaning |
| --- | --- |
| `-f`, `--format` | Force a format instead of detecting one. One of the `--formats` values |
| `-c`, `--config` | Use an explicit config path instead of the detected one |
| `--to` | Push targets for a theme save or `use`: a comma list of formats (§13.6). Default: the terminal you are in |
| `--ghostty-native` | Always push Ghostty as a native theme file and point its config at it (§13.6). Adds the `theme =` line where there is none. Ghostty targets only |
| `--ghostty-in-place` | Never export a Ghostty theme file: edit the file the colours already live in, as kitty and alacritty always are (§13.6) |
| `--no-reload` | Do not ask the terminal to re-read its config after a push; the report keeps naming the key to press (§13.6) |
| `--no-push` | Write the theme file only; the terminal config is not touched (§13.6) |
| `--version` | Print `huebox <semver>` and exit |
| `--help` | argparse default |

(`--force` and `--from` belong to the theme commands of §13.5.)

Exit codes: `0` success, `1` every failure (no colours found, unreadable config,
unresolvable format, missing config). Errors go to stderr and are prefixed
`huebox: `.

### 4.3 Keys (editor)

| Key | Action |
| --- | --- |
| arrows | move between slots — along a row, or up/down a row, in the grid as drawn (§4.3.1) |
| `q` / `w` | hue −/+ |
| `a` / `s` | saturation −/+ |
| `z` / `x` | lightness −/+ |
| `f` | cycle step multiplier ×1 → ×5 → ×20 |
| `i` | type a hex value directly |
| `Ctrl+S` | save — the session's only write (§14.2) |
| `u` / `r` | undo / revert to the last save |
| `t` | theme picker — arrows, `Enter` opens **and pushes it**, `n` new from the buffer, `Esc` back (§13.7) |
| `N` | save the buffer as a new theme, then save it like any other (§13.7) |
| `Esc` | quit — twice if the buffer is dirty |

Since §14, keystrokes do not write the file. They mutate an in-memory buffer
that renders live; `Ctrl+S` is the save point, `r` reverts to the last save
(not to session start), undo survives saves, and Esc on a dirty buffer arms
`unsaved changes — Esc again to discard` instead of dropping the edits. `Q`
and `Ctrl+C` take the same path as Esc; `Ctrl+C` inside a prompt cancels the
prompt instead of quitting. Since §14 only the HSL keys and hex entry no-op
while the selected slot has no value — save, undo, revert and navigation work
regardless (v1 blocked every action on it). The `<config>.huebox.bak` is
taken at the first save of a session, not at editor open (§14.2).

### 4.3.1 The arrows move the way the frame reads

The frame is two stacked grids — the palette (eight swatches to a row at the
default width) and the six interface cells (two to a row) — and an arrow key
moves one cell *in that geometry*: left/right along a row, up/down a row in
the same column. No key uses a fixed slot stride, so what a key does depends
on the width the frame was drawn at: at 40 columns the palette is four to a
row and one arrow down is the swatch below the selection, not the one two
rows down. A vertical key that runs off a grid crosses to the other one in
the same column — the palette's bottom row is the row directly above the
interface's first — while `palette-0` and `selection-foreground`, the outer
ends of the frame, stay put; a row's edge is an edge, so `right` on the last
cell of a row stays rather than wrapping into the next row.

The grid is computed once per frame from the live width and handed to both
the frame and the keys (§15.2), so what is drawn and what the arrows step
through cannot disagree — including across a resize, where the selection
follows the new layout.

**Raw-mode guarantee.** A session enters raw mode exactly once, in `edit()`,
before its draw loop, and leaves it exactly once, in the `finally` that wraps
that loop. The paths out of raw mode are therefore all the same path:

1. the loop's `finally` on a clean quit (`Esc`, `Q`, `Ctrl+C`);
2. the same, after an armed dirty quit discarded the buffer;
3. a prompt — hex entry (`i` / `X`), the picker's name prompt (`n`, `N`) and
   its exists-confirm follow-up — each of which drops out of raw mode for one
   line and re-enters it in its own `finally`;
4. any exception out of the draw, the key reader or a handler.

The theme picker never leaves raw mode: it is drawn and read inside the same
loop, so opening it is not a fourth path.

`exit_raw` in that `finally` restores the termios state saved by the *most
recent* `enter_raw`, which matters because every prompt has closed and reopened
the pair since the session started. A prompt cancelled with `Ctrl+C` or EOF
returns to the editor rather than stranding the session, and each exit is
paired with an enter. The `SIGWINCH` handler (§15.1) is installed after
entering raw mode and restored in the same `finally`, in a nested block that
runs even if restoring the terminal itself fails — no path leaves a user with a
raw shell or with huebox's resize handler still installed. `Ctrl+C` needs no
signal handling at all: raw mode clears `ISIG`, so it arrives as byte `0x03`
and takes the Esc path. Only `SIGKILL`, or a terminal that goes away
underneath the process, can leave a shell without echo.

## 5. Colour model

The canonical model is deliberately small and is what every format is mapped
into and out of.

- **Palette**: 16 slots, `palette-0` … `palette-15`.
- **Named**: `background`, `foreground`, `cursor-color`, `cursor-text`,
  `selection-background`, `selection-foreground`.
- **Total**: 22 slots. Order is fixed as palette-then-named and is the display
  order everywhere.
- **Value form**: `#rrggbb`, lowercase, always with the `#`.
- **Missing value**: `#808080` (`MISSING`) — a mid grey that reads as "unset"
  in a preview without being confused for a real choice.

### 5.1 Maths

- `hex_to_rgb` / `rgb_to_hex` with clamping to `0..255` and rounding on write.
- `rgb_to_hsv` / `hsv_to_rgb` normalised to `0..1` for the hue/sat/light keys.
- `luminance` = `0.2126R + 0.7152G + 0.0722B` on 0–255 values.
- `readable_fg` returns `#000000` when `luminance > 140`, else `#ffffff`. The
  140 threshold is what keeps the swatch grid legible at both extremes.

**TODO — is 140 right?** Recalibrate if the sample text or grid ever looks
washed out on mid-tones. Record the reason when it changes.

> Re-checked at 0.2: kept. `#808080` MISSING and every ramp slot render
> legibly at it, and `pack`/`clip` guarantee the swatch grid never has to
> rely on a threshold to fit.

**TODO — rounding.** `rgb_to_hsv` then back introduces drift. Confirm whether
repeated hue nudges on the same slot are acceptable, or whether edits should
accumulate in a higher-precision space.

> Answered at 0.2, and it is the reason this stays a note rather than a
> queue: the buffer's storage form is hex (§5), so every nudge is
> hex → hsv → hex through the slot value and nothing accumulates between
> keystrokes. The visible cost is quantisation — a ×1 nudge on a value that
> is already at the step's floor is a no-op — not runaway drift. Accumulating
> in float is the change to make if that ever becomes a complaint.

## 6. Format support

A format is a name plus four things: `read`, `write`, `defaults` (candidate
paths, in priority order) and `env` (env vars that redirect those paths).

| Format | Dialect | Candidate paths |
| --- | --- | --- |
| `ghostty` | flat `key = value` | `$XDG_CONFIG_HOME/ghostty/config.ghostty`, `~/.config/ghostty/config.ghostty`, `~/.config/ghostty/config` |
| `kitty` | flat `key value` | `$XDG_CONFIG_HOME/kitty/kitty.conf`, `~/.config/kitty/kitty.conf`, `~/.kitty.conf` |
| `alacritty` | TOML | `$XDG_CONFIG_HOME/alacritty/alacritty.toml`, `~/.config/alacritty/alacritty.toml`, `$XDG_CONFIG_HOME/alacritty.toml`, `~/.config/alacritty.toml`, `~/.config/alacritty/alacritty.yml`, `~/.alacritty.toml` |

The candidate list is upstream's own search order (plan appendix A), in each
terminal's terms, with two huebox extras kept for files that are still out
there: alacritty's pre-TOML `alacritty.yml`, and Ghostty's extensionless
`config`. `~/.config/...` entries are rewritten to `$XDG_CONFIG_HOME` at load
time, which is why both spellings appear above.

**Env overrides.** Only what upstream documents is honoured (§7.2, decision
23): `KITTY_CONFIG_DIRECTORY` for kitty. Alacritty documents no env var for
its config path, so there is none to honour and `--config` is the only
override.

### 6.1 Parsing rules

- Ghostty and kitty are **flat**: a regex rule per slot pair, resolving
  `slot` → config key(s). Ghostty supports `config-file` includes and
  `theme = Name` indirection into a separate theme file.
- Alacritty is **structured**: dotted keys (`colors.primary.background`),
  `[section]` tables and inline tables (`colors.primary = { background = "…" }`)
  all resolve to the same path. Reads normalise every path form; writes go back
  to the form already present in the file.

### 6.2 Write contract

This is the safety promise, and it is the strictest thing in this document.

1. Writes are **line-level**: only colour tokens are replaced.
2. Comments, blank lines, ordering, indentation, alignment and every unrelated
   key survive byte-for-byte.
3. A write that changes nothing is **byte-identical** to the input — no
   reformatting, no trailing-newline repair, no whitespace tidying.
4. A **no-op write must not rewrite the file at all** (mtime untouched). The
   writers decide this before opening the file for writing, so an unchanged
   save does not bump the mtime a backup job, an editor or a config manager
   watches.

**TODO — the first-run backup.** The README promises a `<config>.huebox.bak`
holding pre-huebox state. Specify precisely when it is written, when it is
refreshed, and whether it is ever overwritten.

> Decided in §14.2 (landed in phase 2): the backup is taken before the
> session's *first* save, only if no backup file is already there, and is
> never refreshed or overwritten for the rest of that session. Theme files —
> files huebox owns — get none.

> Since §13–§14: a save is two writes — the truth theme file, then a
> push to the terminal config. The backup moves to first-save-of-session,
> and theme files get none (huebox owns them). The four rules above hold
> unchanged for both writes: the push goes through the same line-level
> writers, so a terminal config is still never re-serialised.

## 7. Detection and resolution

1. If `--format` is given, use it. If `--config` is given without `--format`,
   infer the format from the path: a known config file name first
   (`kitty.conf`, `alacritty.toml`, `config.ghostty`), then whichever
   reader finds colours in the file. A path that says neither is not
   guessable and is an error, not a coin toss.
2. Otherwise probe: environment variables, then candidate paths, in the order
   above, XDG-aware (`XDG_CONFIG_HOME` wins over `~/.config`). The env vars
   are the ones upstream documents (plan appendix A): kitty's
   `KITTY_CONFIG_DIRECTORY`, and nothing at all for alacritty. `KITTY_CONFIG_DIR`
   was a huebox invention and is probed second as a **deprecated** spelling for
   one release after 0.2 (decision 23); it is removed with the next minor bump.
3. **Only offer a terminal whose config actually contains colours.** A stale
   `ALACRITTY_SOCKET` must never hijack a working Ghostty config. The one
   state that is not "no colours" is a `theme =` line naming a file that is
   not on disk: that chain is broken rather than colourless, Ghostty reports
   it as a configuration error on reload, and a save may repair it by
   exporting the theme it points at (§13.6).
4. Ghostty `config-file` includes and `theme = Name` are followed to the file
   that actually holds the colours; that file is what gets written. Both are
   read the way Ghostty reads them: the value may be quoted or bare, a bare
   one stops at the first `#`, and `?path` resolves against the config's own
   directory. A trailing comment on either line is a comment, not part of the
   path.

**The other direction.** Reading a config follows `theme =`; an export
*writes* it. `ghostty_main_config()` is the file that holds the pointer line
(the first Ghostty candidate that exists, or the explicit `--config`), which
is deliberately not the file `resolve()` returns, and
`ghostty_theme_name()` is the same reader without the "does that file exist"
step — the one question a push asks to decide where a save belongs (§13.6).
`ensure_theme_pointer()` changes that one line and nothing else.
Because of the split, "does this config have colours" is
asked of the *chain* — `config_holds_colours()` follows the same includes and
`theme =` a detection would — so a main config that holds nothing but a
pointer is still a terminal, while one that points at nothing is not (§7.3).

**TODO — multi-format UX.** When two or more formats resolve, what does
`huebox` do with no `--format`? Pick one silently, or prompt? What if the
config path is ambiguous between formats?

> Decided at 0.2 (decision 25): the first format that *resolves* wins, in
> probe order — per-window env vars first, then `TERM_PROGRAM`, then the
> format table — and nothing prompts. A terminal you are inside is the
> subject; a machine with several configs is a machine whose user passes
> `--format` or `--config`. The ambiguity case is already an error rather
> than a coin toss (§7.1).

## 8. Preview and editor rendering

- The static preview and the editor draw from the same slot data.
- The code sample inside the editor is rendered in **truecolor from the values
  currently being edited**, so it updates before any terminal reload.
- Pygments is a required dependency (§9) and the only one; the sample is the
  reason, and huebox degrades to no code sample rather than crashing without
  it.
- Layout must hold in narrow terminals; `pack()` and `clip()` guarantee nothing
  overflows and nothing wraps badly.

**How much of the palette the sample shows.** The sample is a sample, not a
swatch grid: it paints what a zig lexer can actually tell apart, and the
editor's palette grid is where all sixteen slots are read at once. The sample is
written to spend that whole vocabulary, and the mapping splits the palette in
half — the base half for syntax, the bright half for what syntax alone cannot
say:

| token | slot | | token | slot |
| --- | --- | --- | --- | --- |
| `Keyword` / `Operator` | `palette-5` | | `Comment` (bright black) | `palette-8` |
| `Keyword.Type` | `palette-6` | | `String.Escape` | `palette-11` |
| `String` | `palette-2` | | name in call position | `palette-12` |
| `Number` | `palette-3` | | `Name.Builtin` (`@import`) | `palette-14` |

Everything else — identifiers, punctuation, whitespace — is `foreground`. Eight
palette slots and one named slot, which is the honest ceiling for one source
file: the other eight have no token class to wear (`palette-0` is the
background, and red, white and bright magenta carry no syntax meaning this
lexer emits). A call position is recognised by huebox rather than by the lexer:
the zig lexer emits a bare `Name` for a declaration, a field, a module path and
a call alike, so a name followed by `(` and not by `fn` is a call. Tokens are
lexed and mapped to slots once per session; only the hex behind a slot is read
per frame (§14.1).

**What spends the rest.** The two slots the sample cannot spend — red and
green, a diff's whole vocabulary — are spent by the diff widget of §14.4,
which is a separate block for the same reason the sample is: a zig lexer has
no `+` and `-` tokens, so a diff *inside* the sample would either cost the
sample half its vocabulary or put the two languages in one block where neither
reads.

**Minimum width.** Defined in §15.4 — `MIN_COLS`/`MIN_ROWS` in `tui.py`;
below them the editor renders one centered `terminal too small — need WxH`
line instead of a garbled frame. Settled in phase 5, when the picker landed.

### 8.1 Frame typography — the frame says itself in the theme

The frame's own text is drawn from the buffer like everything else (§14.1), so
no colour in it comes from a terminal attribute the theme cannot change. Three
roles, three slots, all of them slots the code sample already spends, so the
bright half is read twice in one frame — once as syntax, once as chrome:

| role | slot | carries |
| --- | --- | --- |
| key | `palette-11` | `arrows`, `q/w`, `^S`, `Esc` — the half the reader is hunting for, so it wears the brightest thing in the line |
| label | `palette-14` | the label beside a key: `arrows` **move** |
| muted | `palette-8` | the furniture: a path, a hex, hue/sat/val, a counter, a parenthetical, the `0-7 base` legend |

**A header is not a role.** `palette`, `interface`, `selected`, `examples`,
`live diff`, `live code` and `themes` wear the theme's own `foreground` in
bold, and no palette slot moves them: `foreground` is the one colour the user
chose for text, and a header that took a colour of its own would compete with
the widget it introduces. The status line is bold and nothing else is, because
it is the one sentence in the frame that must be read first.

**The wordmark is the one ornament.** `huebox`, six letters, the six bright
hues in palette order (`palette-9` … `palette-14`), bold, at the top where it
is read once per frame — the header line and the static preview alike. It is
the only rainbow huebox draws, and it is live like everything else: editing
`palette-11` recolours the *u*.

**Hints are `(key, what)` pairs, not strings.** The hint line paints a key and
its label separately, so `pack` folds between *whole hints* and never between a
key and the label it belongs to — which is why `pack` measures display columns
with the escapes left out (`visible()`), the same width `clip` cuts at. Folding
on raw string length would count every colour as columns and cut the row short
of the edge. Pairing changes no width: the painted hint is the same text as the
plain string it replaced, so no frame grows or loses a row to it.

> Planned: the example area becomes a full live gallery (§14) and the layout
> follows terminal resizes (§15).

### 8.2 The frame's own floor

Every row the editor draws is painted on the buffer's own `background`, from
the first column to `cols`, and ends in a reset. The editor is not a preview
*beside* the theme being edited — it is a sample of it: the ground under the
palette grid, the air between the widgets and the columns after the last hint
are all the colour a save would write to the terminal. Nothing is left to the
terminal's own background for the user to imagine, so moving `background`
repaints the whole surface on the same frame it moves the text on (§14.1).

The floor costs no row and no column of content: a row is `clip`ped to `cols`
and then padded in the fill to the same width, measured with `visible()` — the
same width `clip` cuts at — so `clip`/`pack` stay the only width-sensitive
primitives (§15.3). A row that already reaches the edge is unaffected: the
examples strip pads itself in the same slot (§14.1), so its documented padding
and the frame's floor agree by construction rather than by luck.

**A reset reopens the fill.** SGR 0 clears the background as well as the
foreground, and a frame row is full of resets — one at the end of every chrome
run, and the wordmark is one run per letter. A fill painted only at the row's
head therefore leaves every run after the first reset on the terminal's own
background: the rest of the wordmark, the parenthetical beside a header, the
gap between two hints, the blank line inside the code block. So the floor is
repainted after *every* reset inside a row, and the property that follows is
the one the frame owes the reader: **no column of the frame shows the
terminal's background**. A run that wants a background of its own paints it
immediately after the reopen, so nothing is painted over — swatches, the
examples strip and the §8.3 bars are untouched. The area outside the frame
(below `rows`, right of `cols`) is the terminal's own and is not the frame's
to paint.

Two rows are deliberately outside it. The too-small fallback (§15.4) is left
unpainted — it is about the window, not the theme, and it is drawn precisely
when the frame cannot fit anything else. And `show`'s static preview keeps the
terminal's background (§15.5): its output is meant to be piped, and a
full-width block of colour in a pager's output is worse than the alternative.

### 8.3 The selected slot's readout — the value in its own bar

The `selected` row ends in a reading of the slot's own colour: hue, saturation
and value. Numbers alone make every edit blind — `q` moves the hue and the only
thing that changes is a digit — so where the row has room, the reading is three
bars and the numbers live *on* them.

**A bar is a sweep of its whole axis.** The hue bar is the wheel at the slot's
own saturation and value, so `a`/`s` and `z`/`x` repaint every one of its cells
at once, and a colour with no saturation shows as the grey bar it is. The
saturation bar runs grey → colour and the value bar black → colour, each
painted at the slot's own hue. The bar is a legend of what its axis means; the
reading is what the number on it says, and the exact reading is the row below.

**The value is printed centred on its bar**, one cell at a time. The bars are
odd widths so the number can sit on the middle cell, which keeps the row's
optical balance — three bars of different lengths, each with its own reading
sitting in the same place on it.

**A number takes one ink for the whole number.** The ground it is read against
is the cells it covers, so the ink is `readable_fg` of their average — the rule
the palette cells' own labels already follow (§8.1). Choosing it per character
turns a sweep into two numbers: the digits go light over the dark half of the
gradient and dark over the light half, and the number stops reading as one
thing. It is the one place in the frame where the ink is chosen from computed
colours rather than from a slot, and it has to be: the gradient is computed,
and no slot holds one.

**The exact reading has its own line, under the bars.** A number on a bar is
rounded to a degree and a percent, which is the wrong precision for a frame
you are steering a colour with — so `hue 207.0  sat 59.4%  val 93.7%` sits on
the specimen row below, in the same muted the labels wear. The two readings
answer different questions and neither gives up a row for the other: the bars
are the glance, the numbers are the truth. Where the row below cannot hold
them they go, and what is left is the hex — which is the value either way.

The bars keep a ladder, because the row is one row and §15's budget is a
contract: `HSV_FULL` (hue 15, saturation 9, value 9) where the row has the
columns, `HSV_COMPACT` (11, 7, 7) where it has nearly enough, and nothing
where even the compact bars do not fit. The ladder is measured on the strings
this slot produces, so a rung is never chosen and then clipped, and the
subject — the slot's name and hex — is never the thing that gets cut. Nothing
here costs a row: the readout replaces the string that was on a row the frame
already drew.

## 9. Non-functional requirements

- Python ≥ 3.9.
- **One third-party dependency: Pygments**, and only for the editor's live
  code sample — it is a declared dependency, not an extra, because the
  sample is half the show. Everything else is stdlib.
- A small package (§17), installable via `pipx` or `uv tool`.
- No network access at runtime, for any command.
- No writes outside the resolved config and its backup.

## 10. Testing

`python3 -m unittest discover -s tests` (post-§17 layout; v1: `test_huebox -v`) must cover, at minimum:

- reading every slot, per format
- round-trip without drift
- only the intended lines change
- a no-op write is byte-identical (asserted for every format)
- layout holds in narrow terminals
- editor frames at 100x30, 80x24, 60x16 and 40x12 — reflow, clipping, the
  too-small fallback below the minimum, and two identical draws producing a
  byte-identical frame (§15)
- the arrow keys walk the grid the frame was drawn for, at every width:
  left/right along a row, up/down a row, the crossing between the palette and
  the interface grid, the outer edges, and a narrow frame whose rows are four
  or one wide (§4.3.1)
- the diff block at the sizes where it is whole and where it is at its
  floor, that a frame too short for it is byte-identical to the frame without
  it, that a cut hunk never shows half a pair, and that one buffer edit moves
  both sides of it (§14.4)
- the theme picker frame at the same sizes, including a library larger than
  the screen (it scrolls), and the picker flows at the key level: open,
  move, open, blocked-while-dirty, new-from-buffer, save-as-new (§13.7)

**TODO — the gaps.** Add explicit cases for: `config-file` includes, `theme =`
indirection, inline Alacritty tables, malformed hex input, and a read-only or
unwritable config.

> Closed at 0.2: includes (§7.4, quoted / bare / commented / `?`-relative and
> the missing-file case), `theme =` indirection (§13.6), inline
> Alacritty tables, a malformed theme file (binary garbage loads as
> MISSING with a warning) and an unwritable config (a write that raises is
> reported and fails the push; truth is never rolled back). Still thin:
> malformed *hex* inside a terminal config is covered only by "the rule does
> not match, the line is left alone".

## 11. Open questions

Collected, unsorted. Theme-library, staged-save and resize questions live with
their sections (§§13–15); this list is v1-only:

1. Raw-mode restoration and signal handling (§4.3) — **decided**: the
   guarantee in §4.3 (one enter/exit pair per session, prompts close and
   reopen it, the resize handler is restored in the same `finally`).
2. Accumulated drift from HSV round-trips (§5.1) — **answered at 0.2**: the
   buffer stores hex, so nothing accumulates between keystrokes; the residue
   is quantisation, not drift.
3. The 140 luminance threshold (§5.1) — **kept at 0.2**, re-checked against
   MISSING and the fallback ramp.
4. When the `.huebox.bak` is written and refreshed (§6.2) — **decided**:
   first save of a session, once, never overwritten (§14.2).
5. Behaviour when several formats resolve at once (§7) — **decided**: the
   first to resolve in probe order wins; `--format` / `--config` say
   otherwise (decision 25).
6. Minimum supported terminal width (§8) — **decided**: 40x12 (§15.4).
7. Test coverage for includes, indirection and unwritable configs (§10) —
   **closed at 0.2** for all three; malformed hex inside a terminal config
   is the one thin spot left.

## 12. Decisions

Append-only. Newest last. One line per decision, with the reason.

| # | Decision | Why |
| --- | --- | --- |
| 1 | 22-slot canonical model (16 palette + 6 named) | The common denominator of all three terminals |
| 2 | Line-level writes, never full re-serialisation | A theme editor must not be able to mangle a config |
| 3 | Require colours present before offering a format | Stale env vars must not hijack detection |
| 4 | Truecolor sample rendered from live edit values | Shows the change before the terminal reloads |
| 5 | Pygments stays optional | Keeps huebox dependency-free |
| 6 | Truth lives in `$XDG_CONFIG_HOME/huebox/themes/<name>.toml` | User-authored, dotfile-friendly, next to the configs it drives |
| 7 | Save writes truth first, then pushes; push failures never roll back truth | The library outlives any one terminal config |
| 8 | Staged editing: buffer renders live, files change only on Ctrl+S | Live-everywhere preview without churning the config per keystroke |
| 9 | Push reuses the line-level writers, safety contract unchanged | One write path, one guarantee |
| 10 | A dirty Esc needs a second Esc to discard | No silent loss, no modal prompt inside raw mode |
| 11 | Resize = SIGWINCH flag + select-timeout wake + full redraw from live size | Follows the terminal without polling or restructuring input parsing |
| 12 | Switching themes is blocked while the buffer is dirty | Choosing a theme must never silently drop edits |
| 13 | `huebox.py` becomes package `huebox/`, one module per spec area (§17) | The theme library needs somewhere maintainable to live; split first, behaviour-neutral |
| 14 | AGENTS.md owns architecture + guidelines; the spec owns behaviour | Keeps “what” and “how” in the doc each reader reaches for |
| 15 | No autosave: the buffer is written only when the user presses Ctrl+S | Staging exists so edits are deliberate; a timer would write on every idle and make the save key meaningless |
| 16 | The fallback ramp is a neutral dark base, a readable foreground, an inverting cursor, and muted hues with bright siblings — the plan's values, unchanged | It has to be legible the moment `new` opens the editor on an empty machine, and one dict with a comment is easier to argue about than a tuning session |
| 17 | No `huebox rm` / `mv`: the filesystem manages the library and the state file tolerates a dangling pointer | huebox would be offering to delete a user's dotfile; a deleted theme already warns and falls back to direct mode (§13.4) |
| 18 | Push is report-only for keys the target config does not carry | Inserting a key is the one thing that would break the §6.2 line-level contract; saying `not carried by this config: …` tells the user what to add, and the next push fills it in (§13.6, open question 1 stays open) |
| 19 | The picker takes over the frame while it is open, instead of insetting a box over the editor | One frame means one layout budget, and the picker's own footer folds through `pack` — a centred box would have needed a wider floor than §15.4 promises for no extra information |
| 20 | `n` / `N` adopt the new theme as the session's subject, and `N` then runs the ordinary save | One writer, one save pipeline: a theme made in the editor is pushed like any other (§13.6), and the status bar names the theme the next `Ctrl+S` will write |
| 21 | An already-taken name is confirmed in text (`y` overwrites, another name is used), never with a modal | Same reasoning as decision 10: no modal inside raw mode, and the answer is a word rather than a keystroke that could land on the wrong widget |
| 22 | ~~Ghostty native export is opt-in (`--ghostty-native`), not the default push~~ — **superseded by decision 26** | The reasoning said phase 1 only ever edits the file that already holds the colours, so it cannot cross a theme's name. That was the load-bearing claim and it was false: the file that holds the colours is *another theme's* file whenever the config is organised by theme, and a save that puts one theme's colours under another theme's name is worse than a `theme =` line nobody asked for |
| 23 | Env probes follow upstream's documented spellings: `KITTY_CONFIG_DIRECTORY` (primary), `KITTY_CONFIG_DIR` kept second as deprecated for one release; alacritty's invented `ALACRITTY_CONFIG_DIR` / `ALACRITTY_CONFIG` removed | An override named after a variable the terminal does not read is a wrong answer, not a helpful one (plan appendix A). Dropping the alacritty pair outright would have been a silent regression for anyone who set it, so they are removed loudly instead; keeping the kitty one deprecated costs nothing and saves a real user |
| 24 | A no-op write skips the write instead of writing identical bytes | §6.2 rule 4 is about the file, not the bytes: rewriting it bumps the mtime, which is exactly what a backup job, a config manager or an open editor watches. The writers now decide "did anything change?" before opening the file |
| 25 | Several configs at once resolve to the first that resolves, in probe order; nothing prompts | A picker is not an editor: huebox's subject is the terminal you are in, and the user who wants a different one has `--format` and `--config`. An ambiguous `--config` was already an error (§7.1), so the coin toss was only ever on the no-flag path |
| 26 | A Ghostty save is exported as that theme's own file whenever the config is organised by theme; the invariant is *a theme's colours never land in a file that belongs to another theme*, and `--ghostty-in-place` cannot break it. A `theme =` naming a file that is missing is a broken chain, so a save repairs it by exporting that theme | Decision 22 assumed the in-place path was name-blind but harmless. It is neither: with `theme = Nightspice` in the config, saving theme `test` wrote `test`'s colours into `themes/Nightspice`, so the terminal changed and the theme's name became a lie, and the next save of the real Nightspice overwrote it. The one line a `theme =` swap moves is visible, reversible and reported; silently re-badging somebody else's theme file is not. Where the colours are inline or in an include there is no theme name in play, so the edit stays in place and nobody's layout changes. The dangling case follows from the same reasoning: a pointer to a file that is not there is not a colourless config, it is a config in the state Ghostty itself calls an error |
| 27 | A push that succeeded ends with the terminal reloading its config, and opening a theme in the picker is a save — so choosing a theme is choosing it for the terminal too | A push that has to be followed by a keypress is a half-finished action: the user asked for the colours to change, and the report saying "now press ctrl+shift+," is huebox telling them to finish its work. The reload is last, best effort and never load-bearing, and each terminal is asked through the interface it actually has (ghostty: the `SIGUSR2` its own application handles; kitty: `kitty @ load-config`), so there is nothing to configure. Opening a theme in the picker already writes `state.toml` — "opening is choosing" (§13.4) — so the push belongs in the same gesture; a theme you picked and then had to press `Ctrl+S` for was a half-picked theme |
| 28 | The git diff is its own live widget, not a second language inside the code sample, and it is drawn only out of rows the sample did not need | The sample's job is to spend the zig lexer's whole vocabulary (§8); a diff has no lexer, so folding one in would cost the sample half of what it demonstrates. Standing alone it spends the two slots nothing else could — the red and green — and it fills spare rows rather than taking them: the sample is the widget the editor exists to show, and a frame too short for both is exactly the frame that was there before the diff existed |

---

## 13. Theme library — the plan

v1 edits one terminal config in place. The plan turns huebox into a two-way
theme system: the huebox folder is the **truth**, terminal configs are **push
targets**. Read a terminal into a theme (import), edit the truth, push it back
out (save / use). A theme edited once applies to every terminal you use.

This changes §4 (new commands), §6 (writes split in two) and §8 (gallery).

### 13.1 Home

- Home is `$XDG_CONFIG_HOME/huebox`, falling back to `~/.config/huebox`.
- `themes/<name>.toml` — one file per theme. This directory is the truth.
- `state.toml` — `current = "<name>"`, the theme you are working on.
- huebox creates the home lazily on the first `new` / `import`.

Config home, not data home: themes are user-authored, belong in dotfiles, and
sit next to the terminal configs they drive.

### 13.2 Theme file format

Canonical TOML, fully owned by huebox:

```toml
# owned by huebox — values are yours, structure is ours
[theme]
name = "ember"
created = "2026-10-01T12:00:00"
modified = "2026-10-01T12:34:56"
source = "ghostty:/home/you/.config/ghostty/config"  # import origin, informational

[colors]
palette-0 = "#15161e"
# ... palette-1 through palette-15, then the six named slots: all 22, in
# that order
background = "#1a1b26"
foreground = "#c0caf5"
cursor-color = "#c0caf5"
cursor-text = "#1a1b26"
selection-background = "#33467c"
selection-foreground = "#c0caf5"
```

- huebox always writes all 22 slots, palette-then-named (§5 order).
- Writing is atomic — a sibling `.tmp` renamed over the file — so a save is
  all-or-nothing. Terminal configs keep the in-place line-level writer
  instead (§6.2); a rename over one would break that contract.
- Reading tolerates gaps: a missing slot loads as `MISSING` grey with a
  status-bar warning, and is filled in on the next save.
- Hand edits to *values* are respected. Unknown keys or sections are dropped
  on save, with a load-time status warning (once per session).
- Timestamps are local ISO-8601, no timezone. `created` is preserved across
  saves; `modified` is the time of the last write.

### 13.3 Names

`^[A-Za-z0-9][A-Za-z0-9_-]*$`, max 64 chars, file `<name>.toml`,
case-sensitive. Anything else is `huebox: invalid theme name`, exit 1.

### 13.4 Current theme

`edit` with no name opens the current theme from `state.toml`. With no current
theme and no themes at all, `edit` behaves exactly as v1 (direct config edit)
— the `N` key (§13.7) is the migration path out of that mode. If `state.toml`
points at a deleted file, huebox says so and falls back to direct mode; `rm`
and `mv` on theme files keep working because the state tolerates them.

Opening a theme in the picker is what makes it current: `state.toml` is
rewritten at that moment (§13.4, decision 27), and since decision 27 the same
gesture also saves and pushes it, so "current" and "what the terminal is
showing" cannot drift apart. `huebox use <name>` keeps its order — current
first, then the push, and the truth is never rolled back.

### 13.5 CLI (additions; existing commands keep working and gain `[name]`)

| Command | Behaviour |
| --- | --- |
| `huebox new <name>` | Seed from the detected terminal's colours if it has any, else a built-in fallback ramp (the `RAMP` dict in `themes.py`, decision 16). Sets current, opens the editor |
| `huebox edit [name]` | Edit a theme (default: current). Every save writes truth + pushes (§13.6) |
| `huebox show [name]` | Static preview of a theme file instead of a terminal config |
| `huebox --dump [name]` | Dump a theme file instead of a terminal config |
| `huebox list` | List themes: current marked, source terminal and modified date each |
| `huebox use <name>` | Set current + push to the active terminal, no editor |
| `huebox import <name>` | Snapshot the detected terminal into a theme file. Refuses to overwrite without `--force` |

Flags: `--to <fmt,…>` chooses push targets; `--no-push` writes truth only;
`--from <fmt>` chooses the import source (combines with `--config`, and
also seeds `new`).

`use` sets the current theme first and pushes second: a failed push is stderr
plus exit 1, never a rolled-back truth.

> Landed so far: every row above works, and the push half is real — a save
> writes the theme file and then the terminal config, `use` sets current and
> pushes, `--to` / `--no-push` choose and suppress the targets, and the
> status bar reads `saved ember → ghostty`. `edit` with no name opens the
> current theme, and falls back to the v1 direct mode when there is nothing
> to open (no current theme, or one whose file has been deleted). Bare
> `huebox` resolves the same way — the current theme, edited on a TTY and
> previewed off one — because that is the subject; an explicit `show`
> without a name stays the terminal config. Inside the editor, `t` opens the
> picker and `N` is the way out of direct mode (§13.7).

### 13.6 Push (truth → terminal)

- Save = write the truth file, then push the buffer to the active terminal.
  No separate apply step — being in Ghostty means Ghostty follows.
- Push reuses the existing line-level writers, so the §6.2 contract holds
  unchanged for every byte written to a terminal config.
- Default target is today's `resolve()`: the terminal you are in. `--to`
  resolves each named format independently; per-target errors are reported
  and any failure exits 1.
- A terminal whose config holds no colours is never a push target (same rule
  as detection), reported on stderr.
- Keys absent from the target config are updated-if-present; missing keys are
  reported as "not carried by this config". Push never inserts keys — that
  would break the line-level contract (open question 1).
- The **in-place write** pushes through the resolved path — inline config,
  include, or a Ghostty `theme =` file — splicing the 22 slots with that
  format's own line-level writer.
- The **export**, Ghostty only: emit `~/.config/ghostty/themes/<name>` (same
  flat syntax, same writer) and point the main config at it — replace the
  `theme =` value or append the line. Other theme files are left untouched.
- **Which of the two a Ghostty save uses is the config's own answer**
  (decision 26). When the config is organised by theme — its `theme =` line
  names the file the colours live in — the save is exported under the theme's
  own name and the pointer is repointed, because a theme's colours in
  another theme's file is the one write huebox must never make. When the
  colours are inline or in an include, no theme name is in play, so they are
  edited where they are and the config's layout is left alone.
- `--ghostty-native` forces the export even where the colours are inline
  (adding the pointer), `--ghostty-in-place` forces the edit, and the two
  together are refused as contradictory. Neither flag changes the
  invariant: a refused crossing is a report and exit 1, the truth file
  still stands, and the other themes' files are untouched.
- kitty and Alacritty have no theme-file indirection, so they are always
  pushed in place.

As built: a push returns report lines, not an exit code, and the caller
routes them. The editor prints them on stderr after the session (never
inside the raw-mode loop) and the status bar carries the one-line verdict,
`saved ember → ghostty`; `use` prints them on stderr and exits 1 if any
target failed. A push never rolls the truth file back, so a failed push
leaves a saved theme and a failing exit code — fix the target and press
`Ctrl+S` again.

**The reload.** A push that succeeded asks each terminal it reached to
re-read its config, so a save ends with the terminal already showing the
colours (decision 27). It is the last step, it is best effort, and it can
never fail a save: the bytes are on disk before it is attempted.

- **`reload_terminal(fmt)`** returns the report line, or `""` for a
  terminal that cannot be told. Ghostty has no CLI reload action in 1.3
  (`+reload_config` is a *keybind* action and the desktop file offers only
  `new-window`), so it is asked with the signal its own application
  handles: `ghostty_app_pid()` walks `/proc` for a process named `ghostty`
  whose command line carries `--gtk-single-instance` — the application,
  which owns the handler and tells its surfaces — and that process gets
  `SIGUSR2`, the same re-read `ctrl+shift+,` performs. A build with
  per-window surfaces is safe: they are not signalled. kitty is asked
  through its own remote control, `kitty @ load-config`, with all three
  streams on `DEVNULL` (a reload that stole a keystroke or printed into
  the editor's frame would be worse than no reload) and a five second
  timeout.
- **A terminal that cannot be told** — a format with no interface, a
  process that is not running, a signal that lands nowhere, a command that
  is not installed or exits non-zero — returns `""` and the report keeps
  the advice line (`reload your terminal to see it`). The formats that
  *were* reloaded come back in `PushResult.reloaded`, which is how the
  caller knows whether to add that advice at all; a target that failed is
  not in `pushed` and so is never reloaded.
- **`--no-reload`** turns the whole step off, and `push(reload=…)`
  defaults to off so a programmatic call never reaches for a signal or a
  subprocess nobody asked about. Only a command line that leaves the flag
  alone reloads.

The export as built:

- **`export_ghostty_native(name, slots)`** writes
  `$XDG_CONFIG_HOME/ghostty/themes/<name>` — all 22 slots, palette-then-named,
  in Ghostty's own `palette = 0=#…` spelling. The template is written to a
  sibling tmp and the *same* line-level writer a push uses (`write_flat` +
  `GHOSTTY_RULES`) splices the values in before the tmp is renamed over the
  target, so the file is round-trip-safe by construction and never half
  written. The name must pass `valid_name` (§13.3) — it becomes a file name
  in a directory huebox does not own — and a slot with no value is written
  as the same `MISSING` grey a push sends.
- **`ensure_theme_pointer(config_path, name)`** is the only line that moves
  in the user's config: an existing `theme =` keeps its spacing, its quotes
  and its trailing comment and only the value is swapped; a config with none
  gets the line appended; a config already pointing at `name` is not
  rewritten at all, so its mtime survives. Line endings and every byte of
  every other line survive (§6.2). It returns `unchanged` / `rewritten` /
  `appended`, which is what the report says.
- **Which file gets the pointer** is the *main* config — the one holding the
  `theme =` line — not the file `resolve()` follows to the colours
  (`ghostty_main_config()`). So §7.4 is read through that pointer and a main
  config with no colours of its own is still a legitimate target; a config
  with no colours *anywhere* in its chain is still refused (§7.3), and so is
  a machine with no Ghostty config to point at.
- **Which path is taken** is `ghostty_theme_name(main)` against the file in
  front of the push: the colours belong to a theme when that file is the
  theme the pointer names — the usual case, since `resolve()` follows the
  pointer — or the config that carries the pointer, which is what `--config`
  on a main config hands back verbatim (§7.1) even though its colours are
  behind the pointer. No name to export under (a legacy direct session) or
  no pointer at all means the in-place write, which is what those sessions
  did anyway.
- **Nothing is deleted to make room for a theme.** A main config that
  carried colours of its own keeps them, and because the pointer is
  appended at the end the theme file is the last word on colours for as
  long as it is there; the report says those colours are now shadowed, so a
  colour cannot disappear quietly.
- **A dangling `theme =` is a target, not a refusal.** When the main
  config's pointer names a file that is not on disk and the chain has no
  other colours, a save with a theme name exports that theme and repoints
  the config: the file did not exist, so nothing is overwritten, and the
  report says the file was missing. Without a name to export under, or
  under `--ghostty-in-place`, the push is refused — but it names the
  missing theme and what would fix it instead of the bare "no colours
  found". The repair is ghostty's and only for a ghostty target; another
  format's missing config is still that format's missing config.
- **Scope**: the export is ghostty-scoped. `--to ghostty,kitty` exports for
  ghostty and pushes kitty the ordinary way; `--no-push` is honoured with
  every flag combination, because it asks for no push at all rather than for
  a contradictory one. The export is a whole file of ours, so there is no
  "not carried by this config" report on that path — all 22 keys are there
  by construction.
- Other theme files are untouched: the one the config pointed at before the
  push stays exactly as it was, which is the invariant decision 26 is about.
  The themes directory itself is shared with Ghostty's own themes, though —
  an export overwrites any same-name file there (the report says
  `over an existing file` when it did), and a same-named file is why
  `valid_name` guards the name before anything is written.
- **The two paths stay one story.** After an export, `resolve()` follows
  the pointer to the exported file, so a later save of *that* theme is the
  same export again (its name is the file's name) and a save of any other
  theme is a new file plus one pointer line. Nothing about a config's
  history changes which path a save takes — only the pointer does, and the
  pointer is what huebox keeps correct.

### 13.7 TUI theme switching

- `t` opens a theme overlay: arrows + Enter to open, `n` for new (name via
  the same prompt trick as `i`), `Esc` back.
- **Enter is a save, not a peek** (decision 27): opening a theme runs the
  ordinary save path, so the truth file is written, the theme is pushed and
  the terminal is reloaded. Opening already wrote `state.toml` (§13.4), so
  the picker has always meant "this is the theme" — the push is the same
  gesture finished. The buffer comes up clean, so there is nothing left to
  press; `Ctrl+S` remains for the buffer's own edits. The status line says
  what happened (`saved dusk → ghostty`) and the push report lands after
  the session like any other.
- `N` in the editor saves the buffer as a new theme (prompts a name) — the
  way out of legacy direct-config sessions.
- `n` in the picker makes a theme from the buffer and adopts it, and does
  *not* save: creating is not choosing (decision 20's shape), and the
  picker stays open on the new row.
- Switching while dirty is blocked: status reads
  `save (Ctrl+S) or revert (r) first`. No silent loss, no modal.
- The status bar shows `<theme> <fmt>` (or `direct:<path>` in legacy
  mode); a dirty dot `●` appears between theme and target when the buffer
  differs from last save.

As built:

- **The picker owns the frame while it is up** (decision 19) and takes the
  whole key surface: arrows move the selection, `Enter` opens, `n` creates
  from the buffer, `Esc` / `t` / `Q` / `Ctrl+C` all just put the editor back.
  No colour edit, no save and no quit can happen behind a list the user is
  reading.
- **Opening a theme resets the session around it**: the buffer becomes the
  loaded colours, `saved` is that same snapshot (so the new theme is clean),
  the undo log is empty, the selection starts at slot 0, and the pending-discard
  arm is dropped — a fresh theme must never inherit a half-armed Esc. Status
  reads `opened <name>`.
- **`n` creates and adopts.** The prompt is one line (`new theme name:`); a
  name that is taken gets a second one (`<name> exists - y overwrites it, or
  type another name:`) and nothing is written without an answer (decision 21).
  The new theme becomes the session's subject *and* the library's current, so
  the next `Ctrl+S` writes it, and the picker stays open with the new row
  selected. Creation is not switching, so a dirty buffer is fine — the buffer
  is exactly what gets written.
- **`N` is the same, plus the save.** Prompt, `create`, `set_current`, then the
  ordinary save pipeline, so the new theme is pushed like any other truth file
  (§13.6) and the status bar shows `saved dusk → ghostty`. This is the
  migration path out of a legacy direct-mode session (§13.4): the subject
  becomes a theme, the `.huebox.bak` rule goes with it (§13.2), and everything
  after it is an ordinary theme session.
- **One writer for the whole session.** The session's save callback is handed
  the theme name and path every time, not closed over one: a switch mid-session
  has to retarget `Ctrl+S`, and the caller's writer is what decides
  truth-then-push versus the v1 direct-config write.
- **`<fmt>` is the push target the command line named** (`-f`, or the first of
  `--to`). With neither there is no target to name until a save resolves one,
  and the save's own status line names every format it pushed. `●` appears
  between the theme and the target while the buffer differs from the last save;
  a legacy direct-mode session shows `direct:<path>` instead, because that is
  the file its save writes.
- **What the picker cannot say in one line is reported after the session**: a
  theme that would not open, a state file that could not be written, a
  hand-written theme's dropped keys and grey gaps (§13.2), a creation whose
  theme file or state write failed, and a `--to` named in a legacy direct
  session where it has no push target. All of it is stderr,
  after raw mode is over, exactly like the push report (§13.6).

### 13.8 Open questions (§13)

1. Should push insert keys the target config lacks, or stay report-only?
   — **report-only so far** (decision 18): the config tells huebox which
   keys it has, and huebox never invents a line. Still open whether a
   future phase should offer to add them.
2. Ghostty native theme-file export: default or opt-in? — **decided: the
   default wherever the config is organised by theme** (decision 26,
   superseding decision 22). The in-place path is not name-blind but it is
   not name-safe: it writes a theme's colours into whichever theme file the
   config points at. So the export is the default there, in-place stays the
   default for inline colours and includes, and `--ghostty-in-place` is
   refused rather than obeyed when it would cross two themes' names. The
   same reasoning makes a dangling `theme =` a repairable target rather than
   a refusal.
3. Exact values of the built-in fallback ramp for `new` with no colours
   found — **decided** (decision 16): the `RAMP` dict in `huebox/themes.py`,
   plan 3.3's values unchanged — background `#101014`, foreground `#e6e6ea`,
   cursor `#e6e6ea` on `#101014`, selection `#2a2a34` / `#e6e6ea`, palette
   0-7 `#101014 #a83232 #3f7a3f #a88a3f #3f6a8a #8a3f6a #3f8a8a #b0b0b8`,
   8-15 `#d0d0d8 #e06c6c #6cc06c #e0c06c #6c9ce0 #e06c9c #6cc0c0 #f0f0f8`.
4. Machine-readable `list --porcelain` for scripting — now or later?
5. Delete / rename commands, or is `rm` / `mv` enough for v1 of the library?
   — **decided: `rm` / `mv` for v1** (decision 17). The state file is only a
   pointer: a deleted or renamed theme warns once and drops `edit` back to
   direct mode (§13.4), which is exactly the degradation `rm` should have.
6. kitty `include` / Alacritty `import` indirection as push alternatives?

## 14. Staged editing + live examples — the plan

Today every keystroke rewrites the config. New model: keystrokes mutate an
in-memory buffer; nothing touches disk until save. The buffer is what renders,
so the whole editor becomes a live preview of unsaved state.

### 14.1 The live-everything property

Every preview element re-renders from the buffer each frame — background fill
(including the floor the whole frame stands on, §8.2), foreground text,
selection-highlight sample, cursor/caret block, palette grid, interface cells,
the `AaBbCc` readout and the code sample. No colour is cached
between frames; draw reads `slots` and nothing else. Changing one slot visibly
moves the background, the highlight, the text under it and the cursor together,
on the same frame.

Concretely, next to the existing palette / interface / code widgets the editor
gains an examples strip: three rows over one sample sentence, each labelled
with the *pair* of slots it demonstrates — `background/foreground`,
`selection-background/foreground`, `cursor-color/text`, the shared prefix
printed once — and with no hex column: the colour *is* the readout. The
background row sets the whole sentence in foreground on background; the
selection row carries a run of words in selection-foreground on
selection-background, sitting in the sentence where a selection would; the
cursor row covers exactly one character, drawn in cursor-text on
cursor-color, the way a block cursor sits over the character under it.
Each row updates per keystroke. Below `PAIR_MIN_COLS` (58 columns: two of
padding, the 31-column label, a phrase worth showing) the rows fall back to
the plain slot name — the sentence gets those columns back, because a label
that crowds out the sample demonstrates nothing. The rest of each row is
padded in the buffer's `background`, never in a placeholder and never in the
colour the row demonstrates: a missing slot renders as `MISSING` grey *in
place*, and nothing paints MISSING by accident. **The demonstrated colour is
exactly the span that demonstrates it** — `selection-background` covers the
selected words and stops at the last one, where padding the row out to the
edge of the frame in that colour would read as a selection running on past
the text. The three rows are one sentence drawn three times, each with one
span emphasised; that is the whole comparison.

### 14.2 Save, quit, undo

- Ctrl+S writes the truth theme file, then pushes (§13.6); status confirms
  both, e.g. `saved ember → ghostty`. In legacy direct mode it writes the
  config as today.
- Save checkpoints the buffer: `u` keeps working across saves, `r` reverts to
  the last save (v1's revert-to-session-start goes away with per-keystroke
  writes — record the semantic change here so it is deliberate).
- The `<path>.huebox.bak` moves from editor-open to first-save-of-session:
  snapshot the pre-save file, never overwrite an existing backup. Theme files
  get no `.bak` — huebox owns them (history/versioning is an open question).
- Esc with a clean buffer quits. Esc with a dirty buffer arms
  `unsaved changes — Esc again to discard`; the second Esc discards. Ctrl+C
  follows the Esc path; during a prompt it cancels the prompt (raw-mode
  restoration is the guarantee in §4.3). A write failure surfaces as
  `write failed: …` in the status bar and leaves the buffer dirty — `saved`
  is never snapshot on a failed write.
- `--dump` and `show` read saved files only. The buffer lives and dies inside
  the editor process.

### 14.3 Open questions (§14)

1. Autosave timer (save N seconds after the last keystroke) — **decided: no**
   (decision 15). Explicit save is the point of staging; a timer would write
   the config on every idle, which is exactly the churn the buffer removes.
2. Theme file history / versions inside `~/.config/huebox` — later?

### 14.4 The live diff

A third live widget, under the examples strip and above the code sample: a
git hunk over *that* sample, so the frame reads as one story — the program
you are looking at, and the change you would commit.

    live diff (git-style: + added, - removed)
      @@ -6,2 +6,2 @@ pub fn main() !void {
      -var count: u32 = 42;   // your palette
      +var count: u32 = 0x2A;  // your palette
      -std.debug.print("{d} colours\n", .{count});
      +std.debug.print("{d} slots\n", .{count});

A diff has no lexer behind it: `@@` and two signs are the whole vocabulary,
so the mapping is four slots and no Pygments.

| part | slot | |
| --- | --- | --- |
| `+` line | `palette-2` | added, green |
| `-` line | `palette-1` | removed, red |
| `@@` | `palette-6` | the marks that open a hunk |
| the rest of the header, and any unchanged line | `palette-8` | muted, the way a comment is |

The base red and green are the ones spent: §8 gives the bright half to what a
lexer cannot say, and here the lexer says nothing at all. The block is live
like every other (§14.1): it reads `slots` on the call, one keystroke repaints
both sides of the hunk, and a missing slot paints `MISSING` in place.

**The hunk is cut between pairs.** Its rows are the `@@` line plus whole
removed/added pairs, so a short frame loses a pair and never shows half of one
— a lone `-` with no `+` under it is noise, not a diff. `DIFF_ROWS` (whole:
header + `@@` + two pairs) and `DIFF_FLOOR` (header + `@@` + one pair) live
in `editor.py` beside the strip's and the sample's, and §15 decides when the
frame spends them.

**The diff is illustrative, not your buffer.** It shows what a green and a red
slot look like next to each other; it is not a diff of your unsaved edits.
Rendering the real thing would mean diffing the staged buffer against the file
on disk through each format's dialect, which is a config editor wearing a
diff's clothes (§3, non-goals).

## 15. Responsive layout — the plan

What exists: `term_size()` already queries the live size every call and the
editor already full-redraws every keypress. What is missing: while blocked in
`read_key` a resize produces a stale frame until the next keypress, and there
is no small-size story.

1. SIGWINCH sets a dirty flag; `read_key` is restructured around `select()`
   with a short timeout (~0.1 s) so the loop wakes, sees the flag and redraws
   — resize follows within a frame even with no input. Byte-at-a-time escape
   parsing semantics are preserved. A SIGWINCH landing *mid-sequence* raises
   the flag but does not break the sequence: PEP 475 retries the interrupted
   `os.read` automatically, so parsing continues and the redraw happens at
   the next idle tick.
2. Layout is computed from the current (cols, rows) every frame. No cached
   coordinates survive across frames. The frame's grid — how many swatches
   and interface cells fit to a row — is part of that per-frame computation
   and is handed to the arrow keys along with the draw (§4.3.1), so the
   selection and the layout are one calculation, not two that can drift.
3. `pack()` / `clip()` remain the only width-sensitive primitives; every new
   widget (examples strip, theme overlay) must go through them. Both measure
   display columns with SGR escapes left out (`visible()`, §8.1), so a widget
   may hand `pack` painted items and the fold still lands between them.
4. Below a minimum size, render a centered
   `terminal too small — need WxH` screen instead of garbling.
   **`MIN_COLS = 40`, `MIN_ROWS = 12`** — the constants live in `tui.py`
   beside `term_size()`. Phase 1 proposed these numbers and left them
   tunable "until the theme picker lands"; phase 5 kept them, because the
   picker is not the wide widget it was feared to be: it replaces the frame
   rather than insetting a box (decision 19), its widest row is a name that
   `clip` truncates, and its hint footer folds through `pack` into two or
   three rows inside the same floor. The floor is a promise, not a
   proposal: everything at or above it renders, and is tested at 100x30,
   80x24, 60x16 and 40x12. One consequence is recorded in the editor: the
   key-hint line packs its items two spaces apart, so adding the two picker
   keys did not cost the examples strip a row at 60x24.
5. Static `show` is unchanged: one-shot render at the current size.
6. Tests: layout cases at several sizes including below-minimum (§10 grows
   one line: `pack`/`clip`/overlay rendering at 100x30, 80x24, 60x16, 40x10).

**What a short frame spends, in order.** `rows` is a budget and the three live
widgets of §§14.1/14.4 — the examples strip, the diff and the code sample —
are what flex inside it. The order is the contract, not the sizes:

1. **The frame sheds decoration before it sheds a widget.** The `0-7 base
   8-15 bright` legend goes first (the grid is numbered anyway), then the
   blank separators, nearest the widgets first, so the top of the frame keeps
   its air.
2. **The strip gives up rows before the block gives up a line.** The strip is
   whole at `EXAMPLES_ROWS` (header + three rows), shrinks to `EXAMPLES_FLOOR`
   (header + one row), and below that it goes.
3. **The block keeps `SAMPLE_FLOOR`** (header + three lines) because it is the
   widget the editor exists to show (§9), and above that it grows to its whole
   self as the frame allows.
4. **The diff is drawn out of what the block did not need, and never takes a
   row from it.** It is the last widget the frame fills and the first thing it
   stops drawing: whole at `DIFF_ROWS` (header + `@@` + two pairs), at
   `DIFF_FLOOR` (header + `@@` + one pair) once only a pair fits, and absent
   below that. So a frame too short for both is *identical* to the frame
   before the diff existed — no hunk half-drawn where it does not belong —
   and the sample keeps every line it had. Where the hunk does appear it
   grows above the sample, between the strip and the block.
5. **A truncated block drops its least useful lines**: the leading comment
   (the label above already says what the block is), the blank inside it, and
   the closing brace. In a short frame a row that shows nothing is the most
   expensive row there is.
6. **The blank after a block is the last row given up**, after the widget it
   follows. The blanks that separate the widget blocks — the one above the
   hunk and the one under it — are decoration in the same sense: the hunk is
   drawn first, out of what the sample did not need, and only the rows it
   left over become air. So a hunk with two spare rows gets both, one spare
   row gets the blank below it (the row nearest the widget it follows is the
   last one given up), and a hunk that fills its budget gets neither. No
   blank is ever bought with a diff row.
7. **Below the floors the widgets go**, and the frame is the palette grid, the
   interface rows, the selected readout and the hints. A tall frame is never
   padded out to `rows`.

The constants live beside the editor's other layout constants
(`EXAMPLES_ROWS`, `EXAMPLES_FLOOR`, `DIFF_ROWS`, `DIFF_FLOOR`,
`SAMPLE_FLOOR` in `editor.py`), the ladder is tested as exact numbers at
100x30, 80x24, 60x24 and the short end (80x20, 80x18, 80x16, 60x20) — the
diff absent at every one of them — and at the tall end (120x40 whole, 110x36
at its floor). No size from 80x14 up overflows its rows.

## 16. Rollout order

0. §17 project structure + AGENTS.md — the split lands before anything else.
1. §15 resize reactivity — small, independent, can land any time.
2. §14 staged saves + live gallery — editor-internal, unlocks the rest.
3. §13 storage + `new` / `list` / `use` / `import` + `edit [name]` on truth.
4. Push-on-save + `--to` / `--no-push` / `--from`.
5. TUI picker + save-as-new.
6. Ghostty native theme-file export.
7. Live diff widget (§14.4) — the last widget added, and the first the frame
   spends when rows run short.

Each phase keeps `python3 -m unittest` green and the v1 commands working.

## 17. Project structure (step 0)

Before any of §§13–16 lands, the single `huebox.py` is split into a package
so the new code has somewhere maintainable to live. Target layout:

```
huebox/                the package (replaces huebox.py)
  __init__.py          version + re-exports of the v1 public names
  __main__.py          `python -m huebox`
  cli.py               §4
  color.py             §5
  formats/             §6 — base.py, ghostty.py, kitty.py, alacritty.py
  detect.py            §7
  themes.py            §13 (created with the library)
  render.py            §8 + §14 gallery
  tui.py               §15
  editor.py            §4.3 + §14
tests/                 test_<module>.py per module, via `unittest discover`
AGENTS.md              architecture + guidelines (repo root, living doc)
```

Rules for the split:

1. No behaviour change: the suite passes before and after with identical
   output. The split is one commit, one review unit.
2. `huebox/__init__.py` re-exports the v1 public names (`SLOTS`, `FORMATS`,
   `clip`, `term_size`, `render_preview`, …) so `import huebox` keeps working
   for existing tests and callers.
3. `pyproject.toml` moves `py-modules = ["huebox"]` to the package and the
   console script to `huebox.cli:main`.
4. `test_huebox.py` divides per module into `tests/`; the invocation becomes
   `python3 -m unittest discover -s tests`.
5. AGENTS.md is updated in the same commit as any structural change — it
   describes the code as it is, not as it was.

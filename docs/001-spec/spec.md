# huebox — specification

Status: **living draft** · Format version: **1** · Last updated: 2026-10-01

This is the working spec for huebox. It is a single living document: edit it in
place as the tool changes, and move it to `docs/002-spec/spec.md` only when the
colour model or the file contract changes in a way that breaks existing configs.

§§1–12 describe v1 as built. §§13–16 are the plan: the theme library, staged
editing with live examples, and a responsive layout.

Everything marked `TODO` is a decision we have not made yet. Everything else is
either a rule the code already enforces or a promise we intend to keep.

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

> Planned: this surface grows theme commands (§13) and save semantics change
> (§14). Until then, everything below is current behaviour.

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
| `--version` | Print `huebox <semver>` and exit |
| `--help` | argparse default |

Exit codes: `0` success, `1` every failure (no colours found, unreadable config,
unresolvable format, missing config). Errors go to stderr and are prefixed
`huebox: `.

### 4.3 Keys (editor)

| Key | Action |
| --- | --- |
| arrows | move between slots |
| `q` / `w` | hue −/+ |
| `a` / `s` | saturation −/+ |
| `z` / `x` | lightness −/+ |
| `f` | cycle step multiplier ×1 → ×5 → ×20 |
| `i` | type a hex value directly |
| `Ctrl+S` | save (changes are already written live) |
| `u` / `r` | undo / revert everything |
| `Esc` | quit |

> Planned (§14): keystrokes stop writing the file. They mutate an in-memory
> buffer that renders live; Ctrl+S becomes the real save point and Esc
discards a dirty buffer only on a second press.

**TODO — raw-mode cleanup.** On `Esc`, an interrupt or a crash while in raw mode,
the terminal must be restored. Specify the exact restoration sequence and the
signal handling required, so `huebox` can never leave a user's shell without
echo.

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

**TODO — rounding.** `rgb_to_hsv` then back introduces drift. Confirm whether
repeated hue nudges on the same slot are acceptable, or whether edits should
accumulate in a higher-precision space.

## 6. Format support

A format is a name plus four things: `read`, `write`, `defaults` (candidate
paths, in priority order) and `env` (env vars that redirect those paths).

| Format | Dialect | Candidate paths |
| --- | --- | --- |
| `ghostty` | flat `key = value` | `~/.config/ghostty/config.ghostty`, `~/.config/ghostty/config` |
| `kitty` | flat `key value` | `~/.config/kitty/kitty.conf`, `~/.kitty.conf` |
| `alacritty` | TOML | `~/.config/alacritty/alacritty.toml`, `…/alacritty.yml`, `~/.alacritty.toml` |

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
4. A **no-op write must not rewrite the file at all** (mtime untouched).

**TODO — the first-run backup.** The README promises a `<config>.huebox.bak`
holding pre-huebox state. Specify precisely when it is written, when it is
refreshed, and whether it is ever overwritten.

> Planned (§§13–14): every save splits into truth-write + push to the active
> terminal. The backup moves to first-save-of-session. The four rules above
> apply to both writes unchanged.

## 7. Detection and resolution

1. If `--format` is given, use it. If `--config` is given without `--format`,
   infer the format from the path.
2. Otherwise probe: environment variables, then candidate paths, in the order
   above, XDG-aware (`XDG_CONFIG_HOME` wins over `~/.config`).
3. **Only offer a terminal whose config actually contains colours.** A stale
   `ALACRITTY_SOCKET` must never hijack a working Ghostty config.
4. Ghostty `config-file` includes and `theme = Name` are followed to the file
   that actually holds the colours; that file is what gets written.

**TODO — multi-format UX.** When two or more formats resolve, what does
`huebox` do with no `--format`? Pick one silently, or prompt? What if the
config path is ambiguous between formats?

## 8. Preview and editor rendering

- The static preview and the editor draw from the same slot data.
- The code sample inside the editor is rendered in **truecolor from the values
  currently being edited**, so it updates before any terminal reload.
- Pygments is optional; without it the sample is plain, and huebox must not
  degrade or crash.
- Layout must hold in narrow terminals; `pack()` and `clip()` guarantee nothing
  overflows and nothing wraps badly.

**Minimum width.** Defined in §15.4 — `MIN_COLS`/`MIN_ROWS` in `tui.py`;
below them the editor renders one centered `terminal too small — enlarge to at
least WxH` line instead of a garbled frame. Still a proposal until the picker
lands.

> Planned: the example area becomes a full live gallery (§14) and the layout
> follows terminal resizes (§15).

## 9. Non-functional requirements

- Python ≥ 3.9.
- **Zero required third-party dependencies.** Pygments is optional and only
  affects the sample.
- Single module, stdlib only, installable via `pipx`.
- No network access at runtime, for any command.
- No writes outside the resolved config and its backup.

## 10. Testing

`python3 -m unittest discover -s tests` (post-§17 layout; v1: `test_huebox -v`) must cover, at minimum:

- reading every slot, per format
- round-trip without drift
- only the intended lines change
- a no-op write is byte-identical (asserted for every format)
- layout holds in narrow terminals
- editor frames at 100x30, 80x24, 60x16 and 40x10 — reflow, clipping, the
  too-small fallback below the minimum, and two identical draws producing a
  byte-identical frame (§15)

**TODO — the gaps.** Add explicit cases for: `config-file` includes, `theme =`
indirection, inline Alacritty tables, malformed hex input, and a read-only or
unwritable config.

## 11. Open questions

Collected, unsorted, all still live. Theme-library, staged-save and resize
questions live with their sections (§§13–15); this list stays v1-only:

1. Raw-mode restoration and signal handling (§4.3).
2. Accumulated drift from HSV round-trips (§5.1).
3. The 140 luminance threshold (§5.1).
4. When the `.huebox.bak` is written and refreshed (§6.2).
5. Behaviour when several formats resolve at once (§7).
6. Minimum supported terminal width (§8).
7. Test coverage for includes, indirection and unwritable configs (§10).

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
background = "#1a1b26"
foreground = "#c0caf5"
cursor-color = "#c0caf5"
cursor-text = "#1a1b26"
selection-background = "#33467c"
selection-foreground = "#c0caf5"
palette-0 = "#15161e"
# ... palette-1 through palette-15, all 22 slots present
```

- huebox always writes all 22 slots, palette-then-named.
- Reading tolerates gaps: a missing slot loads as `MISSING` grey with a
  status-bar warning, and is filled in on the next save.
- Hand edits to *values* are respected. Unknown keys or sections are dropped
  on save, with a load-time status warning (once per session).
- Timestamps are local ISO-8601, no timezone.

### 13.3 Names

`^[A-Za-z0-9][A-Za-z0-9_-]*$`, max 64 chars, file `<name>.toml`,
case-sensitive. Anything else is `huebox: invalid theme name`, exit 1.

### 13.4 Current theme

`edit` with no name opens the current theme from `state.toml`. With no current
theme and no themes at all, `edit` behaves exactly as v1 (direct config edit)
— the `N` key (§13.7) is the migration path out of that mode. If `state.toml`
points at a deleted file, huebox says so and falls back to direct mode; `rm`
and `mv` on theme files keep working because the state tolerates them.

### 13.5 CLI (additions; existing commands keep working and gain `[name]`)

| Command | Behaviour |
| --- | --- |
| `huebox new <name>` | Seed from the detected terminal's colours if it has any, else a built-in fallback ramp (TODO: define the ramp). Sets current, opens the editor |
| `huebox edit [name]` | Edit a theme (default: current). Every save writes truth + pushes (§13.6) |
| `huebox show [name]` | Static preview of a theme file instead of a terminal config |
| `huebox --dump [name]` | Dump a theme file instead of a terminal config |
| `huebox list` | List themes: current marked, source terminal and modified date each |
| `huebox use <name>` | Set current + push to the active terminal, no editor |
| `huebox import <name>` | Snapshot the detected terminal into a theme file. Refuses to overwrite without `--force` |

Flags: `--to <fmt,…>` chooses push targets; `--no-push` writes truth only;
`--from <fmt>` chooses the import source (combines with `--config`).

`use` sets the current theme first and pushes second: a failed push is stderr
plus exit 1, never a rolled-back truth.

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
- Phase 1 pushes through the resolved path: inline config, include, or the
  Ghostty `theme =` file — wherever the colours live today.
- Phase 2, Ghostty only: emit a native `~/.config/ghostty/themes/<name>`
  (same flat syntax, same writer) and point the main config at it — replace
  the `theme =` value or append the line. Other theme files are left
  untouched. Open whether this is the default or opt-in.

### 13.7 TUI theme switching

- `t` opens a theme overlay: arrows + Enter to open, `n` for new (name via
  the same prompt trick as `i`), `Esc` back.
- `N` in the editor saves the buffer as a new theme (prompts a name) — the
  way out of legacy direct-config sessions.
- Switching while dirty is blocked: status reads
  `save (Ctrl+S) or revert (r) first`. No silent loss, no modal.
- The status bar always shows `<theme> ● <fmt>` (or `direct:<path>` in
  legacy mode) plus a dirty dot `●` when the buffer differs from last save.

### 13.8 Open questions (§13)

1. Should push insert keys the target config lacks, or stay report-only?
2. Ghostty native theme-file export: default or opt-in?
3. Exact values of the built-in fallback ramp for `new` with no colours found.
4. Machine-readable `list --porcelain` for scripting — now or later?
5. Delete / rename commands, or is `rm` / `mv` enough for v1 of the library?
6. kitty `include` / Alacritty `import` indirection as push alternatives?

## 14. Staged editing + live examples — the plan

Today every keystroke rewrites the config. New model: keystrokes mutate an
in-memory buffer; nothing touches disk until save. The buffer is what renders,
so the whole editor becomes a live preview of unsaved state.

### 14.1 The live-everything property

Every preview element re-renders from the buffer each frame — background fill,
foreground text, selection-highlight sample, cursor/caret block, palette grid,
interface cells, the `AaBbCc` readout and the code sample. No colour is cached
between frames; draw reads `slots` and nothing else. Changing one slot visibly
moves the background, the highlight, the text under it and the cursor together,
on the same frame.

Concretely, next to the existing palette / interface / code widgets the editor
gains an examples strip: a background swatch with readable foreground text on
it, a selection-background sample with selection-foreground text, and a cursor
block in cursor-color carrying cursor-text — each labelled with its slot name
and hex, each updating per keystroke.

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
  follows the Esc path (raw-mode restoration, §4.3 TODO, still required).
- `--dump` and `show` read saved files only. The buffer lives and dies inside
  the editor process.

### 14.3 Open questions (§14)

1. Autosave timer (save N seconds after the last keystroke) — yes or heresy?
   Proposal: no. Explicit save is the point of staging.
2. Theme file history / versions inside `~/.config/huebox` — later?

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
   coordinates survive across frames.
3. `pack()` / `clip()` remain the only width-sensitive primitives; every new
   widget (examples strip, theme overlay) must go through them.
4. Below a minimum size, render a centered
   `terminal too small — need WxH` screen instead of garbling.
   **Proposal: `MIN_COLS = 40`, `MIN_ROWS = 12`** — the constants live in
   `tui.py` beside `term_size()`, and the numbers stay tunable until the theme
   picker (§13.7) and gallery (§14) land, since the picker is the widest
   widget. The floor is a proposal, not a promise: everything at or above it
   still has to render (and is tested at 100x30, 80x24, 60x16, 40x12).
5. Static `show` is unchanged: one-shot render at the current size.
6. Tests: layout cases at several sizes including below-minimum (§10 grows
   one line: `pack`/`clip`/overlay rendering at 100x30, 80x24, 60x16, 40x10).

## 16. Rollout order

0. §17 project structure + AGENTS.md — the split lands before anything else.
1. §15 resize reactivity — small, independent, can land any time.
2. §14 staged saves + live gallery — editor-internal, unlocks the rest.
3. §13 storage + `new` / `list` / `use` / `import` + `edit [name]` on truth.
4. Push-on-save + `--to` / `--no-push` / `--from`.
5. TUI picker + save-as-new.
6. Ghostty native theme-file export.

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

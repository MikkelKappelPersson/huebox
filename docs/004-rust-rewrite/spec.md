# huebox 004 — Rust rewrite spec

Status: **draft** · Previous: `docs/001-spec/spec.md` (behavioral contract, unchanged)
· Conventions: `AGENTS.md` + `docs/dev/code-guidelines.md`
· Next: `plan.md`, then `tasks.md`

This spec says *what* the Rust rewrite must preserve and how fast it must be.
Behavioral authority stays with `docs/001-spec/spec.md` §§1–17 plus the Python
suite (516 tests) as the parity oracle. Nothing here changes the colour model,
the file contract, or the CLI surface.

## 1. Problem

Measured, worst case (background selected, adjust-key held, every row dirty):

```text
live redraw, real compositor, 40-key flood:
100x24: total min=45.28 p50=119.64 max=149.98 mean=99.66ms
  css reparse: max=74.46 mean=32.35ms
  layout+mount_panels: max=112.74 mean=65.25ms
  bare mount+footer: max=6.88 mean=2.06ms
  update_rows parse: ~5ms/redraw
150x50: total max=158.18 mean=106.01ms
headless frame build (no compositor): 9.25ms at 100x24, 20.06ms at 150x50
paint bytes: ~13KB/redraw at 100x24, ~32KB at 150x50 (small; paint is innocent)
```

Frame budget is 16.6ms (60fps); key repeat arrives every ~33ms. Six coalesced
redraws drain a 40-key burst in ~600ms while new keys keep arriving — backlog
grows, stutter felt. Root causes are Textual overhead (stylesheet reparse per
keystroke on token-bound slots, 2–3 lays plus widget rebuilds per frame), not
colour maths (`apply_key` costs 0.006ms) and not terminal paint.

## 2. Goals

- Worst-case held-key redraw p50 under 16.6ms at 100x24 and 150x50,
  measured by the same pty bench that produced §1 (`/tmp/livebench.py` method:
  background selected, `e` flood, real compositor, file-logged timings).
- Behavioural parity: every editing behaviour in `EditorState` + `apply_key`
  ports exactly (selection, adjust, undo, revert, step size, picker, setup,
  overrides warning, save/apply, two-armed Esc). The Python suite stays green
  during migration and every ported case lands in `cargo test`.
- Same truth contract: in-memory buffer every frame, disk on Ctrl+S only,
  preview push on Ctrl+A only, truth-then-push with no rollback, byte-identical
  no-op writes, line-level terminal-config writes only.
- Same CLI surface and exit codes: all commands, flags, `huebox: ` stderr
  prefix with exit 1, no tracebacks for user errors.
- Single static binary, fast startup, no runtime language dependency.

## 3. Non-goals

- No new features, no new terminals, no config keys beyond colour slots.
- Not a config editor, not a theme store (§1 of the 001 spec still binds).
- No visual redesign: the frame, typography and layout policy (§8, §14, §15)
  port as-is; sizes below `MIN_COLS`×`MIN_ROWS` keep the too-small hint.

## 4. Stack

- **crossterm** owns the terminal: raw mode, key/mouse/resize input, alternate
  screen, output. It is the backend, not the UI.
- **Ratatui** owns the UI: immediate-mode buffer, built-in diff so unchanged
  cells cost nothing, layout primitives. It replaces Textual, not the frame:
  `render` rows port cell-for-cell, Ratatui only composites them.
- **syntect** replaces Pygments for the Zig code sample (grammar data
  committed; token-to-slot mapping in §14 ports by table, verified by test).
- **clap** for the CLI surface; **serde/toml** for themes/state files.

## 5. Architecture (mirrors the Python modules, same dependency rule)

```text
huebox-core:   color (22-slot model, hex/rgb/hsv, steps) — imports nothing
huebox-format: base rule machinery + ghostty/kitty/alacritty + FORMATS registry — imports core only
huebox-lib:    providers (PROVIDERS registry) + detect (resolve()) + themes
               (save/push/create/overrides/reload) — facades, never bypassed
huebox-render: clip/pack/visible (unicode-width), chrome/title/wordmark,
               banner/mini, samples, examples strip, diff, hsv bars — pure,
               reads slots on every call (live-everything, §14.1)
huebox-editor: EditorState + apply_key (ALL behaviour) + draw_editor +
               theme/setup/overrides lines + hits — no I/O, no compositor;
               save/apply/delete/Library/import seams injected by cli
huebox-app:    Ratatui shell — keys in, redraw, two prompts; thin by rule
huebox-cli:    clap, dispatch, composition root (builds all injected seams)
```

Rules carried over: no cycles; editor never imports themes/detect; callers use
`FORMATS` / `PROVIDERS` / `themes.save|push|create` / `detect.resolve()`;
clicks resolve through row-announced hits via `apply_key`, never directly;
popups never stack; footer owns hints+status, frame lays out above it.

## 6. Performance budgets (worst case, background hold)

| Stage | Python now (100x24) | Rust budget |
| --- | --- | --- |
| state (`apply_key`) | 0.006ms | <0.1ms |
| frame build (layout+rows) | 3.6ms headless / 65ms live | <3ms |
| style/chrome rebind | 52ms reparse spikes | 0ms (no stylesheet engine; colours are cell values) |
| parse/re-encode (`from_ansi`) | 4.4ms | 0ms (cells built styled, never serialised+reparsed) |
| diff+write (~13KB) | inside 100ms total | <2ms |
| redraw total | p50 120ms | p50 <16.6ms |

The win comes from deleting the stylesheet engine, the widget DOM and the
ANSI round-trip — not from faster colour maths. Any phase failing its budget
stops the rewrite: fix the design, not the language.

## 7. Parity requirements

- Colour maths ports identically, not approximately: same `HUE_STEP`
  (1/360) and `CHANNEL_STEP` (0.02), hue wraps while sat/val clamp,
  `rgb_to_hex` rounds half-even and clamps to 0–255, `luminance` keeps its
  coefficients and the `readable_fg` 140 boundary, hex parsing and
  normalisation match. Hex/int outputs compare byte-for-byte;
  hsv/luminance floats within 1e-9 absolute.
- Before any Rust behaviour phase lands, a golden oracle lands first in
  Python: `tests/test_parity.py` with committed `tests/fixtures/` vectors —
  every colour function on fixed inputs including the edges above, plus one
  scripted session (all adjust axes both ways, multiplier cycle, undo,
  revert, hex entry, save) as a key-by-key trace of slots, selection,
  multiplier and status. The Rust state machine must replay both fixtures
  exactly; the Python suite stays the oracle until it does.
- Ported `cargo test` suite mirrors the Python modules; new behaviour needs a
  case, new format needs round-trip plus byte-identical no-op cases.
- pty closure harness (same method as `tests/candidate.py` + `pyte`): same
  slots, same sizes, same bytes modulo compositor — port it to drive the Rust
  binary before any phase lands UI.
- Profiling seam ports too: `HUEBOX_DEBUG` file-logged per-stage timings
  (`HUEBOX_DEBUG_LOG`) exist in Rust from the first UI phase, same line
  shapes, so benches compare apples to apples.
- Mouse, resize, wheel, text selection in sample/diff, and all four popups
  (setup/themes/overrides/import) behave per 001 §§13–14.

## 8. Migration strategy

Python stays the reference implementation until the Rust binary passes parity
plus budgets; no big-bang cutover. Interop order: core → formats →
themes/detect → render → editor state machine (headless-tested, no UI) →
app/popups → cli → packaging. Each step ships with its ported tests green and
the Python suite still green. The Python tree is retired only when the Rust
binary owns every command and the pty harness agrees on all fixtures.

## 9. Risks

- Zig highlighting parity (syntect vs Pygments token stream) — table-test every
  sample token before UI lands.
- Wide-glyph/combining parity in `clip`/`visible` — property-test against the
  Python pair on adversarial strings.
- Key-encoding differences (crossterm vs Textual `translate` table) — port the
  table by test, especially Esc/Ctrl/Enter/modifiers.
- Reload signalling and Ghostty include/`theme =` handling must behave
  identically — covered by themes parity tests, verified on all three
  terminals.
- Scope creep into redesign — §3 binds; any visual change needs its own spec.

# huebox 004 — implementation plan

Status: **draft** · Plans spec: `spec.md` §§1–9 · Research: `research.md`
· Conventions: `AGENTS.md` + `docs/dev/code-guidelines.md` · Next: `tasks.md`

This turns the rewrite spec into build order. The spec says *what*; this says
*how*, crate by crate, phase by phase. Python stays the reference until §8's
cutover; every phase keeps `python3 -m unittest discover -s tests` green
*and* `cargo test` green from P2 on. No task list lives here — `tasks.md`
comes afterwards.

| Phase | Delivers | Spec | Touches |
| --- | --- | --- | --- |
| 0 | Toolchain + perf spike (go/no-go gate) | §6 | new `rust/` tree, throwaway bench |
| 1 | Parity oracle in Python (golden fixtures) | §7 | new `tests/test_parity.py` + `tests/fixtures/` |
| 2 | Core + format + lib crates, ported unit tests | §§5, 7 | `rust/huebox-core`, `huebox-format`, `huebox-lib` |
| 3 | Render + preview as pure functions, cell-grid comparison | §§3, 5, 7 | `rust/huebox-render` |
| 4 | Editor state machine headless + transcript replay | §§2, 5, 7 | `rust/huebox-editor` |
| 5 | Ratatui shell: frame, popups, prompts, mouse | §§2, 4, 5, 8 | `rust/huebox-app` |
| 6 | CLI + pty parity harness + packaging + bench gate | §§2, 6, 8 | `rust/huebox-cli`, harness, docs |

## Phase 0 — toolchain + spike (throwaway, decides everything)

Goal: prove the budgets before the rewrite earns another phase. `yay -S
rustup`, stable toolchain, `rust/` workspace skeleton. Then a spike that draws
the worst case (background-hold frame at 100x24 and 150x50: palette grids,
three HSV bars, sample, strip, diff) through Ratatui's test backend with the
§2 loop, and times build+diff+write per frame.

Gate: p50 frame under 8ms at both sizes (half the §6 budget, leaving headroom
for compositor and paint) or the rewrite stops here and the Python fixes from
the bench (debounced style sync, single lay) ship instead. Spike is deleted
once the numbers are recorded in the plan.

## Phase 1 — parity oracle (Python, ships first)

Goal: the cross-language answer key, committed before any Rust behaviour.
`tests/test_parity.py` plus `tests/fixtures/parity_color.json` (every colour
function on fixed inputs including wrap/clamp/rounding/140-boundary edges)
and `parity_session.json` (scripted session as key-by-key trace of slots,
selection, multiplier, status, plus final state and writes).

Gate: suite green, clean under `-W always`; fixtures regenerated only via
`HUEBOX_REGENERATE=1` with the change in the diff. Until this lands, later
phases have nothing to assert against.

## Phase 2 — core + format + lib

Goal: colour maths, config I/O and theme store with no UI. Crates
`huebox-core` (slots, hex/rgb/hsv, steps, luminance — colorsys verbatim,
banker's rounding per research §7), `huebox-format` (base rules + three
terminals + `FORMATS` registry), `huebox-lib` (`PROVIDERS`, `detect.resolve`,
`themes` save/push/create/overrides/reload).

Gate: `cargo test` mirrors the Python format/theme/detect cases
(round-trip plus byte-identical no-op writes); parity fixture's colour
vectors replay exactly (hex/int byte-equal, floats within 1e-9). Line-level
write discipline and truth-then-push semantics port by test, not by comment.

## Phase 3 — render + preview (pure)

Goal: every frame row as styled cells, no compositor. Port `clip`/`pack`/
`visible` (unicode-width + adversarial property tests), chrome/title/
wordmark, banner/mini, palette/interface cells, examples strip, diff, HSV
bars, and the grid/panel/side/bare ladder with the Python layout tests beside
them.

Gate: cell-grid comparison — a Python helper dumps each fixture frame as
(char, fg, bg) triples and the Rust tests assert identical grids. No ANSI
strings cross the boundary in either direction (research §4 binds).

## Phase 4 — editor state machine (headless)

Goal: `EditorState` + `apply_key` with all injected seams (write/apply/
delete/prompts/Library/import), zero I/O, zero Ratatui. Selection, adjust,
undo, revert, steps, picker, setup, overrides, save/apply, two-armed Esc.

Gate: `parity_session.json` replays to identical trace and final state;
ported editor unit tests green. This crate must stay UI-free — a Ratatui
import here fails the phase.

## Phase 5 — Ratatui shell

Goal: the thin compositor from research §§2–5, 8. Keys in through the one
translation table, coalesced redraw, footer bar, four popups over a dimmed
base (never stacked, never opaque), suspend-prompts, mouse through hits,
`HUEBOX_DEBUG`/`HUEBOX_DEBUG_LOG` timing seam from the first UI commit.

Gate: pty harness drives the binary through the parity session fixtures;
`cargo test` plus clippy clean; manual background-hold feels instant before
instruments say so.

## Phase 6 — CLI, harness, packaging, bench gate

Goal: clap surface identical to today (commands, flags, `huebox: ` errors,
exit codes), composition root wiring all seams, pty closure harness ported
from `tests/candidate.py` (same slots/sizes/bytes comparison), single binary
via `cargo install`, README/CONTRIBUTING install docs updated.

Gate (all three, no exceptions): full pty harness agrees on every fixture;
live pty bench (§1's method) shows worst-case p50 under 16.6ms at both sizes;
Python tree retired only after both hold. Then 001 spec, guidelines and tasks
close the rewrite out.

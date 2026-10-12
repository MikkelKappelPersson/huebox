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
| 8 | crates.io + binaries + wheels + AUR + Homebrew from one tag | §10 | `rust/*/Cargo.toml`, `.github/workflows/publish.yml`, PKGBUILD/formula |

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

Result 2026-10-10: GO. Worst-case spike (dev profile, 300 frames/size + 10
warmup, TestBackend, every row dirty): 100x24 p50 1.43ms / max 1.58 /
mean 1.43ms; 150x50 p50 4.26ms / max 4.84 / mean 4.27ms. Release headroom:
0.15ms / 0.44ms. Single `ratatui 0.30` + `crossterm`-feature dep, styled
cells, no ANSI round-trip. Spike deleted; bare `rust/` workspace remains.

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

Result: ALL GREEN. Harness 6/6 (`tests/test_pty_rust.py` on the release
binary: parity replay, save bytes, per-format push bytes). Live bench
(real compositor, `e` flood on slot 16): post-panels 100x24 p50 1.74 /
max 1.88 / mean 1.70ms; 150x50 p50 1.63 / max 1.89 / mean 1.56ms —
~10x under the 16.6ms budget (pre-panels: 0.66/1.26ms). `cargo install --path rust/huebox-cli` proven
(2.7MB `huebox 0.4.1`). Retirement left as a human morning decision
(see tasks.md); Python suite (525) stays the reference until then.

## Phase 8 — release distribution

Goal: one tag ships all five targets through a reworked `publish.yml`.
Prep first (crates.io refuses the workspace as-is): every `Cargo.toml`
gains `description` + `license` (+ `repository`, `readme`, `keywords`),
and every path dependency gains `version = "0.5.0"` beside its `path`
— registry installs cannot see path deps, so versioned path deps are
what makes `cargo install huebox` resolvable. Publish order inside the
workflow is dependency order: core → format, render → lib → editor →
app → huebox, via crates.io Trusted Publishing (OIDC, no tokens), each
preceded by `cargo publish --dry-run` at implementation time.

Chain on `v*` tags: Rust gates (`cargo test --workspace`, clippy
`-D warnings`, `fmt --check`, tag == all 7 `Cargo.toml` versions) →
crates.io publish → GitHub Release with a `huebox-0.5.0-x86_64-linux`
tarball + sha256 (aarch64 stretch) → PyPI wheels (hand-rolled:
prebuilt binary + console entry point, `py3-none-linux_x86_64`;
aarch64 stretch) → AUR (PKGBUILD builds with cargo from the crates
source, `python-*` deps gone, `.SRCINFO` regenerated) → Homebrew tap
(formula off the GitHub tarball). The `workflow_dispatch` TestPyPI
dry run stays: wheels prove themselves there before any tag.

Gate: dry-run dispatch green, then tag `v0.5.0` and verify every
target installs and reports 0.5.0 (`cargo install huebox`,
`uv tool install huebox`, `yay -S huebox`, `brew install`).

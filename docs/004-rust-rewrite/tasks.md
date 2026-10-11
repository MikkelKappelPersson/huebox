# huebox 004 — tasks

Status: **draft** · Plan: `plan.md` · Spec: `spec.md` · Research: `research.md`
· Rules: `AGENTS.md` + `docs/dev/code-guidelines.md`

Execution order is top to bottom unless marked `PARALLEL`. Phases are
checkboxes, tasks are `- [ ]`. Every phase ends with its gate green and the
relevant spec/plan lines updated.

Per-phase loop: implementer lands the phase → reviewer audits the diff →
fixes → gate green → one phase-commit. Delegation briefs end with the
completion ritual: agents finish their report with `ship and done` on its own
line, then call `shepherd_done` with the summary; the orchestrator closes
every agent as soon as its task is closed.

Agents: `worker` (full capabilities, isolated context) implements;
`reviewer` (read-only) audits. No other agents on this rewrite.

## P-1 — overnight setup (human, before sleep) — done 2026-10-10 (cargo 1.99.0, stable 1.99.0, 516 tests green via `uv run`)

The tree must be build-ready or nothing below runs unattended:

- [x] Install the toolchain: `yay -S rustup`, then
  `rustup toolchain install stable`, then `cargo --version` prints a version
  (rustup 1.29.1, cargo 1.99.0)
- [x] Commit the working tree: the `HUEBOX_DEBUG`/`HUEBOX_DEBUG_LOG` timing
  seam in `huebox/app.py` stays (spec §7 mandates it) — tree verified clean,
  no commit needed
- [x] Network for crates.io (first `cargo build` fetches ~200MB); machine
  stays on, no sleep/suspend
- [x] Confirm `python3 -W always -m unittest discover -s tests` is green on
  the committed tree (baseline for every later comparison) — 516 tests OK

## P0 — toolchain verify + perf spike (gate: go/no-go) — done, GO (1.43ms / 4.26ms dev)

- [x] `worker-a`: verify `cargo` stable, create `rust/` workspace skeleton
  (`Cargo.toml` workspace + empty `huebox-spike`), pin `ratatui 0.30`
  with the `crossterm` feature per research §1 (cargo 1.99.0)
- [x] `worker-a`: worst-case spike — background-hold frame (palette grids,
  three HSV bars, sample, strip, diff) at 100x24 and 150x50 through the
  test backend with the research §2 loop; print per-frame build+diff+write
- [x] Gate: p50 under 8ms both sizes or STOP (ship Python fixes instead);
  on go, record numbers in `plan.md` and delete the spike — GO, numbers in plan
- [x] `reviewer`: audit `rust/Cargo.*` + spike removal — reviewer timed out;
  orchestrator self-audited (single ratatui dep, no from_ansi, §2 loop) and landed bare workspace

## P1 — parity oracle, Python (PARALLEL with P0, no cargo needed) — done (115 color cases, 23-key trace, 519 green)

- [x] `worker-b`: `tests/test_parity.py` + `tests/fixtures/parity_color.json`
  (every colour function on fixed inputs incl. wrap/clamp/rounding/140 edges)
  with `HUEBOX_REGENERATE=1` regen rule per spec §7
- [x] `worker-b`: `tests/fixtures/parity_session.json` — scripted session
  (all axes both ways, mult cycle, undo, revert, hex entry, save) as
  key-by-key trace plus final state, via `tests/session.py`
- [x] Gate: suite green, clean under `-W always`; fixtures committed
- [x] `reviewer`: audit vectors for edge coverage (one missing edge fails it) — pass, no Critical

## P2 — core + format + lib crates (workers PARALLEL after API handshake) — done (175 cargo green, clippy clean)

- [x] All: 30-minute handshake — froze `huebox-core` public API by dictate
  (`Slots`, `hex/rgb/hsv`, `step_hsv`, `luminance`, `readable_fg`) from
  research §7, then split and do not block on each other
- [x] `worker-a`: `huebox-core` — colorsys verbatim, banker's rounding,
  `((x % 1.0) + 1.0) % 1.0` hue normalisation; colour vectors replay exactly
  (14 tests; landed `3e6d203`)
- [x] `worker-b`: `huebox-format` — base rules + ghostty/kitty/alacritty +
  `FORMATS` registry; round-trip + byte-identical no-op tests ported
  (4 unit + 20 integration; review pass, no Critical)
- [x] `worker-c`: `huebox-lib` — `PROVIDERS`, `detect.resolve`, `themes`
  save/push/create/overrides/reload; truth-then-push + line-level writes
  ported by test (57 tests; review found 3 Criticals, all fixed at merge:
  exact-case listing, clear-error propagation, per-line Ghostty predicate
  exposed as `ghostty_is_color_line`; `RealBackend` wires FormatStub→format
  with FORMAT_NAMES validation so unknown fmts become `huebox:` errors)
- [x] Gate: `cargo test` green for all three; colour vectors byte-exact
  (hex/int) / 1e-9 (floats)
- [x] `reviewer`: audit all three crates (dependency rule: no cycles,
  editor-ward seams untouched)
- Known gaps (deferred to P6 hardening): UTC-vs-local `created` stamp,
  lossy non-UTF8 edge, `{value}` vs `{value!r}` warnings, Linux SIGUSR
  consts, hex-helper 3-way duplication (core/format/lib fold pass)

## P3 — render + preview, pure (PARALLEL with P2-C, needs core only) — done (80 cargo green)

- [x] `worker-c`: port `clip`/`pack`/`visible` (unicode-width) +
  adversarial property tests; chrome/title/wordmark, banner/mini,
  palette/interface cells, examples strip, diff, HSV bars
- [x] `worker-c`: grid/panel/side/bare ladder + ported layout tests
- [x] Gate: cell-grid comparison harness — Python dumps (char, fg, bg)
  triples per fixture frame, Rust asserts identical grids; no ANSI strings
  cross the boundary (zero `from_ansi`; `tests/dump_grid.py` replays real
  draw_editor stdout, 10 frame fixtures + corpora)
- [x] `reviewer`: audit purity (no I/O, no Ratatui in this crate) — pass,
  no phase-failing defect

## P4 — editor state machine, headless (starts when core API frozen) — done (126 cargo green, replay IDENTICAL)

- [x] `worker-a`: `EditorState` + `apply_key` + draw/lines/hits + all
  injected seams; Ratatui import here fails the phase (grep empty)
- [x] Gate: `parity_session.json` replays to identical trace + final;
  ported editor unit tests green (2 parity + 93 editor + 31 import)
- [x] `reviewer`: audit against 001 §§13–14 behaviours one by one — pass,
  1 warning (is_name_char exotic-space approx, behaviour-preserving)

## P5 — Ratatui shell (sequential: needs P3 + P4)

- [ ] `worker-a`: §2 loop + one translation table (table-tested) + footer +
  `HUEBOX_DEBUG`/`HUEBOX_DEBUG_LOG` seam from the first UI commit
- [ ] `worker-b`: four popups over dimmed base (never stacked/opaque) +
  suspend-prompts + mouse-through-hits (PARALLEL with A after router shape
  freezes — same handshake pattern as P2)
- [ ] Gate: pty harness drives the binary through parity fixtures; clippy
  clean; background-hold feels instant before instruments confirm
- [ ] `reviewer`: audit input routing (one handler per key) + popup rules

## P6 — CLI, harness, packaging, bench gate (sequential: needs everything)

- [ ] `worker-a`: clap surface identical (commands, flags, `huebox: ` +
  exit 1, no tracebacks); composition root wires all seams
- [ ] `worker-b`: pty closure harness ported from `tests/candidate.py`;
  `cargo install` single binary; README/CONTRIBUTING updated
  (PARALLEL with A after CLI shape freezes)
- [ ] Gate (all three, no exceptions): harness agrees on every fixture;
  live pty bench shows worst-case p50 under 16.6ms both sizes; Python tree
  retires only after both hold
- [ ] `reviewer`: final audit; close-out updates to 001 spec, guidelines,
  tasks

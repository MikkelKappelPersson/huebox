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

## P-1 — overnight setup (human, before sleep)

The tree must be build-ready or nothing below runs unattended:

- [ ] Install the toolchain: `yay -S rustup`, then
  `rustup toolchain install stable`, then `cargo --version` prints a version
- [ ] Commit the working tree: the `HUEBOX_DEBUG`/`HUEBOX_DEBUG_LOG` timing
  seam in `huebox/app.py` stays (spec §7 mandates it) — commit it as
  `chore: file-logged debug timing seam`, so overnight starts clean
- [ ] Network for crates.io (first `cargo build` fetches ~200MB); machine
  stays on, no sleep/suspend
- [ ] Confirm `python3 -W always -m unittest discover -s tests` is green on
  the committed tree (baseline for every later comparison)

## P0 — toolchain verify + perf spike (gate: go/no-go)

- [ ] `worker-a`: verify `cargo` stable, create `rust/` workspace skeleton
  (`Cargo.toml` workspace + empty `huebox-spike`), pin `ratatui 0.30`
  with the `crossterm` feature per research §1
- [ ] `worker-a`: worst-case spike — background-hold frame (palette grids,
  three HSV bars, sample, strip, diff) at 100x24 and 150x50 through the
  test backend with the research §2 loop; print per-frame build+diff+write
- [ ] Gate: p50 under 8ms both sizes or STOP (ship Python fixes instead);
  on go, record numbers in `plan.md` and delete the spike
- [ ] `reviewer`: audit `rust/Cargo.*` + spike removal

## P1 — parity oracle, Python (PARALLEL with P0, no cargo needed)

- [ ] `worker-b`: `tests/test_parity.py` + `tests/fixtures/parity_color.json`
  (every colour function on fixed inputs incl. wrap/clamp/rounding/140 edges)
  with `HUEBOX_REGENERATE=1` regen rule per spec §7
- [ ] `worker-b`: `tests/fixtures/parity_session.json` — scripted session
  (all axes both ways, mult cycle, undo, revert, hex entry, save) as
  key-by-key trace plus final state, via `tests/session.py`
- [ ] Gate: suite green, clean under `-W always`; fixtures committed
- [ ] `reviewer`: audit vectors for edge coverage (one missing edge fails it)

## P2 — core + format + lib crates (workers PARALLEL after API handshake)

- [ ] All: 30-minute handshake — freeze `huebox-core` public API
  (`Slots`, `hex/rgb/hsv`, `step_hsv`, `luminance`, `readable_fg`) from
  research §7, then split and do not block on each other
- [ ] `worker-a`: `huebox-core` — colorsys verbatim, banker's rounding,
  `((x % 1.0) + 1.0) % 1.0` hue normalisation; colour vectors replay exactly
- [ ] `worker-b`: `huebox-format` — base rules + ghostty/kitty/alacritty +
  `FORMATS` registry; round-trip + byte-identical no-op tests ported
- [ ] `worker-c`: `huebox-lib` — `PROVIDERS`, `detect.resolve`, `themes`
  save/push/create/overrides/reload; truth-then-push + line-level writes
  ported by test (PARALLEL with A/B against the frozen registry shape)
- [ ] Gate: `cargo test` green for all three; colour vectors byte-exact
  (hex/int) / 1e-9 (floats)
- [ ] `reviewer`: audit all three crates (dependency rule: no cycles,
  editor-ward seams untouched)

## P3 — render + preview, pure (PARALLEL with P2-C, needs core only)

- [ ] `worker-c`: port `clip`/`pack`/`visible` (unicode-width) +
  adversarial property tests; chrome/title/wordmark, banner/mini,
  palette/interface cells, examples strip, diff, HSV bars
- [ ] `worker-c`: grid/panel/side/bare ladder + ported layout tests
- [ ] Gate: cell-grid comparison harness — Python dumps (char, fg, bg)
  triples per fixture frame, Rust asserts identical grids; no ANSI strings
  cross the boundary (finding `from_ansi` fails the phase)
- [ ] `reviewer`: audit purity (no I/O, no Ratatui in this crate)

## P4 — editor state machine, headless (starts when core API frozen)

- [ ] `worker-a`: `EditorState` + `apply_key` + draw/lines/hits + all
  injected seams; Ratatui import here fails the phase
- [ ] Gate: `parity_session.json` replays to identical trace + final;
  ported editor unit tests green
- [ ] `reviewer`: audit against 001 §§13–14 behaviours one by one

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

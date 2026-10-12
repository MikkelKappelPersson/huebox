# huebox 004 — tasks

Status: **done** (0.5.0: Python retired, Rust-only) · Plan: `plan.md` · Spec: `spec.md` · Research: `research.md`
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
- Gaps ledger (final): UTC-vs-local stamps FIXED via `chrono::Local`
  (`system_now` + cli `local_stamp`; `list` verified byte-equal);
  `{value}` vs `{value!r}` verified IDENTICAL (both parsers yield strings
  only — not a gap); SIGUSR consts documented (live path shells symbolic
  `-USR1`/`-USR2` flags); lossy non-UTF8 edge accepted (Python reads
  `errors="replace"` too); hex-helper 3-way duplication kept as
  post-retirement fold work (all three match Python today)

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

## P5 — Ratatui shell (sequential: needs P3 + P4) — done, single worker (52 tests) + 3 review Criticals fixed at merge

- [x] `worker-a`: §2 loop + one translation table (table-tested) + footer +
  `HUEBOX_DEBUG`/`HUEBOX_DEBUG_LOG` seam from the first UI commit
- [x] `worker-b`: folded into one P5 worker (loop/popups/prompts/mouse
  cohere around one `App`; parallel split would serialise on it anyway)
- [x] Gate: pty harness drives the binary through parity fixtures; clippy
  clean; background-hold feels instant before instruments confirm
  (pty smoke passed in-worktree; full harness = P6)
- [x] `reviewer`: audit input routing (one handler per key) + popup rules —
  3 Criticals, all fixed at merge: unknown-key sentinel `"?"`→`""`
  (was typable into setup-name field), Ctrl+C-in-prompt now cancels via
  `signal-hook` EINTR→None (001 §4.3; `fill_buf` loop — `read_line`
  retries `Interrupted` forever), `Cell.bold` restores every Python `BOLD`
  (titles, wordmark-adjacent headers, `>` marks, selected rows/cells,
  banner faces, theme labels; harness still compares triples)

## P6 — CLI, harness, packaging, bench gate (sequential: needs everything)

- [x] `worker-a`: clap surface identical (commands, flags, `huebox: ` +
  exit 1, no tracebacks); composition root wires all seams (15 tests;
  review: no Criticals; merge fixes: piped-edit exit→0 to match cli.py
  despite review Accept of 1 — oracle binds; `show` banner wired to
  byte-identical incl. separate-`\x1b[1m` run shape; direct-apply
  garbage-fmt branch fixed to save via `direct_fmt`)
- [x] `worker-b`: pty closure harness ported from `tests/candidate.py`;
  `cargo install` single binary; README/CONTRIBUTING updated
  (`tests/test_pty_rust.py`, 6 tests; install proof 2.7MB `huebox 0.4.1`;
  docs install sections only)
- [x] Gate (all three, no exceptions): harness agrees on every fixture
  (parity replay + save bytes + per-format push bytes, all green);
  live pty bench shows worst-case p50 under 16.6ms both sizes —
  post-panels re-measured 100x24 p50 1.74/max 1.88/mean 1.70ms,
  150x50 p50 1.63/max 1.89/mean 1.56ms (release binary, real
  compositor, `e` flood on slot 16; panels cost ~1ms/frame, still
  ~10x under budget — was 0.66/1.26ms pre-panels);
  Python tree retirement stays a HUMAN morning decision (see below)
- [x] `reviewer`: final audit — NOGO on ledger/uncommitted evidence only;
  code verdict clean (dep rule, no from_ansi/todo/dbg, zero bare
  unwrap/panic in cli, counts check out, harness asserts real parity).
  This commit lands the ledger + evidence; hardening gaps tracked under
  P2 as before.

## Morning decision (human): Python retirement

Both P6 gates hold (harness green, bench ~13–25x under budget), so spec
§8 permits retiring the Python tree. Suggested cutover (NOT run
unattended): `git grep -l` the last Python-only consumers, then remove
`huebox/` + the Python-only `tests/test_*.py` (keeping
`tests/fixtures/` + `tests/test_pty_rust.py` + `tests/dump_grid.py` as
the Rust harness inputs), and update README/CONTRIBUTING to the single
binary. Until then the Python suite stays the reference: 525 green.

## Phase 7 — full screen/popup parity sweep (then done)

- [x] P5b LANDED (`0719512`): bordered panels + buttons restore
  (`rust/huebox-app/src/panel.rs`, click routing, env toggles) plus
  `Live` collapsibles, `╭─ TITLE ─` dashes, Import margin row,
  truncating main footer — orchestrator-finished after the worker's
  `shepherd_done` (handles died with the old session)
- [x] Main-layout sweep DONE (orchestrator, pty, `parity` theme):
  76/90/100x24 IDENTICAL (0 differing lines); 80 + 110x24 differ in 2
  lines only — squeezed `Themes`/`Import` label tails (`Th`/`Im` at 80,
  `Theme`/`Impor` at 110) where Textual's flex-squeezed `Button` clips
  its label inside an identical box/row/click rect. ACCEPTED deviation:
  same buttons, same rows, same clicks; only the clipped tail differs.
  Fixed en route: `╭─ TITLE ─` dashes, Import top-margin blank row,
  single-row truncating main footer (status scrolls out, as in Python),
  `Live` collapsibles (`▼/▶ Interface text/Live diff/Live code`,
  `E`/`D`/`C`, header-click toggle, `HUEBOX_COLLAPSIBLE`).
- [x] P7 capture DONE (3 parallel read-only workers, reports
  `/tmp/p7-setup-save.md` + `/tmp/p7-themes-overrides.md` +
  `/tmp/p7-import-prompts.md`): Setup missing, import small-box vs
  full-screen modal, square corners, flat dim, dialog-docked footer,
  status pad, too-small footer row, sample/fill/footer ink gaps.
- [x] P7 fix LANDED (`260483d`, single sequential worker, reviewer
  sign-off, no criticals): round `dialog_block`, 3/5 `dim_buffer`
  blend (backdrop cells byte-identical incl. colors), editor hints
  stay docked under dialogs, overrides status pad, Python-true
  too-small frame, slot-fg sample, last-ink code fill, `112233`
  footer ink + label-colored `/` gaps, full SetupScreen port
  (welcome/import/new, key gating, Esc), full-screen Import rework
  (no title, no group header, `▐X▌` rows, `0178d4` cursor wash,
  live cursor-theme preview, one-row hints, `N selected` line,
  flat ` Import ` + ` Cancel ` buttons — button row byte-identical
  incl. colors), mouse-teardown disables. Verified pty IDENTICAL
  (text+fg/bg) for base/themes/overrides/setup/import/too-small at
  100x24 + 80x24. Gates: 394 cargo + 525 Python green, clippy 0,
  fmt clean. Reviewer residuals filed as follow-ups (non-blocking):
  `#808080`-background empty-preview sentinel, Reset-fg dim path,
  `import_column` 3x-per-frame rebuild, facade accessor suggestion.
- [x] ACCEPTED deviations (user-approved or strictly-better, kept):
  share-spanning top buttons (user request; Python tight 8-col),
  explicit `f0f0f0` chip fg (terminal-independent; Python default),
  80-col squeezed label tails (`Them`/`Impo`; Python `Th`/`Im`),
  `[<u` absence (Python-only kitty-query leakage).
- [x] Gate: `release: retire python, rust-only 0.5.0` lands the retirement (Python tree + packaging removed, harness on a frozen oracle, docs + guidelines rebound, versions 0.5.0); closes the rewrite.

## Phase 8 — release distribution (0.5.0)

- [x] Crate metadata: single `huebox` crate (workspace merged — seven modules, same boundaries as module discipline); `description` + `license` (+ `repository`, `readme`, `keywords`) in `rust/Cargo.toml`; `cargo package --list` clean, `cargo publish --dry-run` green. The 5 half-published 0.5.0 workspace crates (core/format/render/lib/editor) are abandoned on the registry — yank them via web UI when convenient, nothing depends on them.
- [x] `publish.yml` rework: tag-gated chain — Rust gates (test/clippy/fmt + tag == Cargo.toml) → crates.io publish, single crate (Trusted Publishing, re-runs safe) → GitHub Release (`huebox-0.5.0-x86_64-linux` tarball + sha256) → PyPI wheels (hand-rolled binary wheel via `.github/pack-wheel.py`, linux x86_64; proven end-to-end: fresh-venv `pip install` + `huebox --version`) → AUR (cargo-build PKGBUILD from the tag archive, no python deps) → Homebrew tap (tag archive). TestPyPI dispatch dry-run kept. AUR/formula jobs fail fast until the one-time cargo rewrite lands.
- [x] AUR PKGBUILD cargo rewrite: the workflow bootstraps it itself on first run (no local AUR key needed); `.SRCINFO` regenerated in-job, 0.5.0. Homebrew formula rewritten + pushed (cargo build, resources gone).
- [x] Human: crates.io Trusted Publisher configured on the `huebox` crate; `AUR_SSH_PRIVATE_KEY` + `TAP_TOKEN` secrets verified
- [x] Gate: tag `v0.5.0` → all green except crates-job OIDC (`cargo publish` found no token; 0.5.0 was already on the registry from the laptop publish, so nothing missing). Verified: `cargo install huebox` → 0.5.0, `uv tool install huebox` → 0.5.0, AUR → 0.5.0-1, tap pushed, GitHub Release attached. crates job tries OIDC first, falls back to the `CARGO_REGISTRY_TOKEN` repo secret on `no token found` — add a scoped publish token there before 0.5.1. Local `yay -S` install needs a sudo terminal. Then done.
- [ ] 0.5.1: four-way binaries — IMPLEMENTED (matrix: linux x86_64 + aarch64, mac arm64 + Intel; flat tarballs, one + sha256 each), proves on the 0.5.1 tag run. Binary-by-default everywhere: AUR ships the x86_64 tarball (CI-bootstrapped), Homebrew pours all four (CI-bootstrapped), README leads with the binary.
- [ ] Later: Windows binary — needs a portability audit first (`detect`/`themes` assume Unix home/config paths; line endings, reload signals, and which terminal the push targets are all open questions). Spike before promising.

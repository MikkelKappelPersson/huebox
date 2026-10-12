# P5 fieldnote — `huebox-app` (thin Ratatui shell)

Date: 2026-10-11 · Worktree: `/home/mikkelkp/Projects/huebox-p5` (base b1e44bb, uncommitted)

## What landed

`rust/huebox-app/` — keys in, redraw, two prompts over the headless
editor. Direct deps: `huebox-core` + `huebox-render` + `huebox-editor` +
`ratatui 0.30 (crossterm feature)` + `std` only (`cargo tree` proves it).
Crossterm event types import ONLY via `ratatui::crossterm::event`
(research §1 binds); zero `from_ansi` (grep proves it); no test touches a
real tty.

- `src/keys.rs` — `translate(&KeyEvent) -> &'static str`: the ONE table
  porting `app.py`'s `KEYS` (`esc`, `\x03\x13\x01\x04`, arrows, `\r`,
  `space`, `backspace`, plain chars). Ctrl maps `c/s/a/d` ONLY (either
  case, Shift riding along); any other Ctrl, any Alt/Super/Hyper/Meta, and
  Tab/Ins/Del/Home/End/paging/function/media/non-ASCII answer `"?"`.
  Plain ASCII resolves through a 95-entry static table (no leak, no panic).
  Press+Repeat vs Release stays caller-side (`run` filters kinds).
- `src/router.rs` — one input router per screen mirroring `on_key`'s guard
  order: import → setup → overrides → picker → editor. Editor colour keys
  behind a popup are inert (no branch reaches `apply_key` underneath);
  `i` behind any popup never stacks. Closing reads off the state
  (`overlay`/`overrides`/`setup` none, `quit`, `import_pending` opening
  import after dismiss). Confirm folds through `confirm_import` + the
  injected writer (None writer = noop, the `_write_imports` None shape);
  empty confirm leaves the line untouched and stays open (P4 seam note).
  After close, `setup_after_import` re-arms the choice when still empty.
- `src/app.rs` — `Shell{state, import, import_open, import_writer,
  import_notes, cols/rows, hits, cached lines, popup}`. `refresh` rebuilds
  grid + body (`frame(..., footer=false)`) + folded bar + dialog layout
  every turn; short frames pad with buffer-bg fill (the floor holds even
  when the frame returns fewer rows than the area). `render` paints body,
  docked bar, dim base, centered bordered dialog. `run` is the research §2
  loop (block-first-key, Press+Repeat, `poll(ZERO)` drain, draw-once, quit
  breaks) inside `ratatui::run` (panic-safe restore); `MouseCapture` Drop
  guard disables capture on exit AND unwind (`run` doesn't do it).
  `snapshot(w, h)` renders into `TestBackend` for headless tests.
- `src/cells.rs` — `Cell{ch,fg,bg}` → `Span::styled`/`Line`: `fg ""`
  paints `Reset` (terminal default, as in Python — never an unset style,
  which Ratatui would skip and leave stale cells showing through),
  `bg ""` resolves to the buffer fill. Bold restored by position for the
  wordmark only (six consecutive cells wearing `palette-9…14` in order);
  status bold lives in the bar. NO DIM anywhere (test scans every span).
- `src/footer.rs` — `_fold_footer` ported verbatim (`fold_footer`: one row
  if it fits, else two, status last never dropped) + `bar_lines` paint
  (keys `palette-11` bold, labels `palette-14`, status foreground bold,
  full-width bg fill). Editor bar pairs from `editor_hints`.
- `src/dialog.rs` — centered `popup_rect`, `dim_buffer` (every backdrop
  cell three-fifths toward the buffer background — the measured modal
  factor; `Clear` deliberately NOT used, it would expose the terminal bg
  and break §8.2), `dialog_block` (round border, muted `palette-8`,
  buffer bg — never an opaque foreign fill). Width clamps mirror
  `DIALOG_W` (44/44/60/100, max 28 for import, min 24). The import list
  lives here (behaviour stays in `import.rs`): `import_column` titles
  plus `▐X▌` theme rows, `import_body` windows it, `import_preview`
  paints the cursor theme (palette/interface/strip/code on its own bg),
  `import_count_text` the live count, `import_scrollbar` the overlay bar.
  `ids` + `groups` click maps announced per row.
- `src/mouse.rs` — click → `slot_at(hits)` → `click_slot` (editor) or
  `click_theme/setup/overrides` (which confirm through `apply_key` —
  never direct); import clicks toggle rows via `click_row` and titles via
  `toggle_group`, footer buttons mirror `Enter`/`Esc`; chrome clicks
  select nothing. Wheel → `up`/`down` in picker/import only; swallowed
  everywhere else.
- `src/prompts.rs` — `read_prompt_line` pure over injected buffers (EOF/
  error → `None`, else trimmed); `suspending_prompter()` restores the
  terminal (raw off, alt-screen left, mouse dropped), reads on real
  stdio, re-enters. Both seams stay `PromptFn`, so tests inject fakes.
- `src/debug.rs` — `HUEBOX_DEBUG`/`HUEBOX_DEBUG_LOG` from the first commit:
  `redraw WxH: …` / `resize to …` / `prompt '…'` byte-identical to
  `app.py`, plus `stages key=… frame=… draw=…` for the §6 budget table.
  File errors never fail a frame. Env-mutating tests serialize on a mutex.
- `tests/app.rs` — TestBackend snapshots (no-terminal-bg floor at
  100x30/80x24/60x20, too-small hint, footer hints+bold status, bold
  wordmark, centered theme-closed popups, import list+preview), fake
  prompter (write/cancel/invalid-hex), headless key-drive to save.

## Deltas from Python, all deliberate

- Collapse keys (`E`/`D`/`C`) are plain `apply_key` no-ops: the Rust
  `frame()` has no collapse parameter, so there is no honest row to hide.
- Home/End/PageUp/PageDown are named (`home`/`end`/`pageup`/`pagedown`):
  the import list jumps first/last on them, like the native list it
  mirrors; everywhere else they are inert.
- Non-ASCII chars answer `"?"`: theme names and hex are ASCII-only.
- Bold restores for status + wordmark only (brief scope); headers, `>`
  marks and selected rows stay unbolded — cells carry no bold (P4 notes).
- Ctrl+C inside a suspend-prompt terminates (cooked-mode SIGINT), as for
  any cooked tool; EOF cancels. Documented, not guarded.
- The import preview is the cursor theme's own palette/interface/strip/
  code on its background, like the Python side-by-side pane; text
  selection in sample/diff is P6 work (immediate mode has no selection
  overlay yet).

## Proof

- `cargo test -p huebox-app`: 44 lib + 8 integration = 52, green.
- `cargo clippy -p huebox-app --all-targets`: clean. `cargo fmt --check`: clean.
- `cargo test --workspace`: all green (P2–P4 untouched, still green).
- pty smoke (temp example, since deleted): `run` on a real pty via
  `pty.fork` — alt-screen + mouse-capture init bytes, `wwQ` drive, exit
  code out, restore bytes (`?1000l…?1049l?25h`) captured. Terminal recovery
  verified byte-for-byte; no brick path.
- Only `rust/Cargo.toml` (member), `rust/Cargo.lock`, `rust/huebox-app/`
  touched. Nothing outside `rust/`.

## For P6 (`cli`)

- `Shell::new(state, import, writer)` + `run(shell) -> io::Result<Shell>`
  is the whole integration surface. Build seams per the P4 contract
  (`WriteFn`/`ApplyFn`/`DeleteFn`/`BackupFn`/`PromptFn` via
  `suspending_prompter()`, `Library`, `ImportLibrary` + `ImportWriter`),
  then print `report_session` lines after `run` returns (elements keep
  trailing `\n`, `huebox: ` stderr prefix — P4 `SessionReport`).
- Wire `HUEBOX_DEBUG_LOG` through for the live bench; the `stages` line is
  the §6 comparison point.

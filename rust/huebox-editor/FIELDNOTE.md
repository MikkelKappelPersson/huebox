# P4 fieldnote — `huebox-editor` (headless state machine)

Date: 2026-10-10 · Worktree: `/home/mikkelkp/Projects/huebox-p4` (HEAD 680341e, uncommitted)

## What landed

`rust/huebox-editor/` — `EditorState` + `apply_key` with every behaviour,
zero UI, zero file I/O. Direct deps: `huebox-core` + `huebox-render` +
`std` only (`cargo tree` proves it; `unicode-width` arrives via render).

- `src/state.rs` — `EditorState` (all 24 Python fields, same names),
  `present` mask for slots a file omits (production readers fill
  `MISSING`, so adjust/hex stay inert on absent slots exactly like the
  `value is None` return), `head_label` / `session_path` / `top_subject`,
  `needs_setup` / `enter_setup`, click helpers, `report_session` (returns
  `SessionReport{stdout, stderr}` — the shell `println!`s each stdout line;
  elements keep their trailing `\n`, byte-identical to the Python print).
- `src/keys.rs` — `apply_key` (setup → overrides → overlay → editor
  precedence; two-armed Esc; `f` cycle; undo/revert; staged save;
  preview-apply never moving `saved`; direct-mode Ctrl+A saves; `h`/`X`
  hex entry; `t`/`N`; absent-slot inert; unknown keys disarm + clear
  status), `save_state`, `apply_state`, picker / setup / overrides
  sub-handlers, `setup_after_import` (the `_import_closed` tail: re-arm the
  choice when an import lands nothing).
- `src/dialogs.rs` — `theme_lines` / `setup_lines` / `overrides_lines` as
  cell rows + row-announced `Hit`s (`theme_hits` / `setup_hits` /
  `overrides_hits`), same windows/marks/counters/footers. Bold does not
  survive (cells carry no bold — selection reads via `>`); status rows are
  plain text.
- `src/import.rs` — `ImportState` + free functions mirroring
  `import_state.py` verbatim (`visible_rows`, `cursor_id`, `move_up/down`,
  `wheel`, `collapse/expand_cursor`, `toggle_group`, `toggle`,
  `select_all_visible`, `clear_selection`, `click_row/group`,
  `preview_slots` read-once cache, `cursor_slots`, `open/close_import`,
  `abandon`, `handle_key` returning the same ten action strings,
  `plan_confirm`, `confirm_import`), plus local `slugify` /
  `unique_slug` / `batch_unique` (editor never imports providers).
- `src/seams.rs` — the injected contract (table in `lib.rs` docs).

## Verbatim seam names for P5/P6

`EditorState`, `apply_key`, `save_state`, `apply_state`, `enter_setup`,
`needs_setup`, `setup_after_import`, `report_session`, `SessionReport`,
`theme_lines`, `setup_lines`, `overrides_lines`, `theme_hits`,
`setup_hits`, `overrides_hits`, `click_slot`, `click_theme`,
`click_setup`, `click_overrides`, `WriteOutcome{status, overrides}`,
`OverridesReport{main, keys}`, `WriteFn`, `ApplyFn`, `DeleteFn`,
`BackupFn`, `PromptFn`, `Library{names, load, create}`,
`ImportLibrary{entries, read, format_of}`, `ImportWriter`, `ImportState`,
`handle_key`, `open_import`, `close_import`, `abandon`,
`import_may_open`, `plan_confirm`, `ImportPlan`, `ConfirmOutcome`,
`confirm_import`, `NEW_THEME_LABEL`, `DIRTY_MARK`, `INITIAL_SEL`,
`MULT_STEPS`, `QUIT_KEYS`, `ENTER_KEYS`, `SAVE_KEY`, `APPLY_KEY`.

Rust adaptations (all documented at the seam): `write`/`apply` return
`Result<WriteOutcome, String>` (`Err` → `write/apply failed: {msg}`,
`saved` untouched); `delete` returns `Result<String, String>`; `create`
returns `Result<String, String>` (`Ok(path)`/`Err(problem)`); import
`read` returns `Option<Slots>` (`None` = no colours); `backup` is an
extra injected seam (`FnMut(&str)->bool`, once per session) since the
crate does no I/O; `confirm_import` with empty selection returns empty
`status` (shell leaves the line untouched, stays open); `is_name_char`
uses `!is_control()` (≈ `isprintable`, diverges only on exotic unicode
spaces); `handle_key` returns `&'static str`.

Shell routing (P5): keys go to `handle_key(import)` while `is_open`, else
`apply_key`; `i` opens only when `import_may_open(&editor)` AND no import
open; frame via render `frame()` + `slot_at`, dialog clicks via
`click_*`; after import close with no themes landed, call
`setup_after_import`.

## Proof

- `parity_session.json` replays to IDENTICAL trace (24 entries: sel,
  slot, mult, status, all 22 slots per key) + final state + 2 writes.
- `parity_color.json` `is_hex`/`normalize_hex` vectors replay exactly.
- Tests: 93 editor + 31 import + 2 parity = 126, all green; workspace
  `cargo test` green (all crates); `cargo clippy --all-targets` clean;
  `cargo fmt --check` clean; UI-framework grep over the crate empty;
  only `rust/Cargo.toml` (member), `rust/Cargo.lock`, `rust/huebox-editor/`
  touched.

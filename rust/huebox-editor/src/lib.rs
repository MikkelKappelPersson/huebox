//! The editing session: buffer, key surface, picker, and what it saves.
//!
//! Rust port of `huebox/editor.py` (`EditorState` + `apply_key` own ALL
//! editing behaviour) and `huebox/import_state.py` (the import popup's
//! cursor, selection and confirm mapping). Headless by rule: this crate
//! depends only on [`huebox_core`] + [`huebox_render`] + `std` — no
//! compositor, no file I/O, and no UI framework import anywhere in this
//! crate (a UI dependency here fails the phase, by design).
//!
//! # The seam contract (P6 wires the real ones in `cli`)
//!
//! `editor` reaches the world only through injected seams, never imports
//! of themes/detect. Shapes, verbatim:
//!
//! | Seam | Shape | Contract |
//! | --- | --- | --- |
//! | save | [`WriteFn`]: `FnMut(Option<&str> theme, &str path, &Slots) -> Result<WriteOutcome, String>` | `Ok` marks the buffer clean (`saved` moves, `written` sets); `Err(msg)` leaves `saved` untouched with status `write failed: {msg}`. `WriteOutcome.status` empty reads `"saved"`. `WriteOutcome.overrides` (`main`, `keys`) arms the Ghostty warning. Handed the subject every save — a picker switch retargets mid-session. |
//! | preview | [`ApplyFn`]: same shape as `WriteFn` | `Ok` never moves `saved` (buffer stays dirty, Esc keeps guarding); `Err(msg)` gives `apply failed: {msg}`. `None` seam reads `"nothing to apply"`. In direct sessions (`theme` none) Ctrl+A saves instead — there is nothing to preview past the file. |
//! | overrides delete | [`DeleteFn`]: `FnMut(&str main) -> Result<String, String>` | `Ok(msg)` closes with `msg` (empty reads `removed {n} override(s) from {main} - theme now shows`); `Err(msg)` closes with `delete failed: {msg}`. Buffer untouched either way. |
//! | backup snapshot | [`BackupFn`]: `FnMut(&str path) -> bool` | Fires once per session before the first save when `backup_path` is set; `true` sets `backup_made`. `None` skips (theme files: huebox owns them, no `.bak`). |
//! | prompts | [`PromptFn`]: `FnMut(&str label) -> Option<String>` | `prompt_hex` (`"  new hex for {slot}: "`) and `prompt_name`; `None` cancels with nothing written. One prompt pattern, shared. |
//! | picker library | [`Library`] trait: `names() -> Vec<String>`, `load(&str) -> Option<(Slots, String)>`, `create(&str, &Slots, bool) -> Result<String, String>` | `load` gives `(slots, path)` or `None`; `create` gives `Ok(path)` or `Err(problem)` — the problem lands on the status line, never a panic into the key loop. |
//! | import library | [`ImportLibrary`] trait: `entries(&str) -> Vec<(String, String)>`, `read(&str, &str) -> Option<Slots>`, `format_of(&str) -> String` | `entries` is `[(display, path)]` in list order; `read` is `None` for no colours/unreadable; `format_of` maps provider to the `source` format. |
//! | import writer | [`ImportWriter`]: `FnMut(&str name, &Slots, &str source) -> Result<(), String>` | One call per confirmed plan; `Err(problem)` folds into the session notes as `{name}: {problem}`. Truth files only — never retargets the session. |
//!
//! Cells, not ANSI: dialog rows ([`dialogs`]) are [`huebox_render`] cell
//! rows plus row-announced hits, the same split as `frame`. Clicks resolve
//! through [`slot_at`](huebox_render::frame::slot_at) then go through
//! [`apply_key`] (dialog clicks set the index then confirm with `"\r"`),
//! never directly. Popups never stack: [`apply_key`] serves setup, then
//! overrides, then picker; [`import_may_open`](import::import_may_open)
//! gates the import popup on all three being closed.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod dialogs;
pub mod import;
pub mod keys;
pub mod seams;
pub mod state;

pub use dialogs::{
    overrides_hits, overrides_lines, setup_hits, setup_lines, theme_hits, theme_lines, DialogFrame,
    OVERRIDES_CHOICES, OVERRIDES_HINTS, OVERRIDES_KEYS, OVERRIDES_LABELS, SETUP_CHOICES,
    SETUP_HINTS, SETUP_KEYS, SETUP_LABELS, SETUP_NAME_HINTS, SETUP_NAME_MAX, THEME_HINTS,
};
pub use huebox_render::frame::{editor_hints, slot_at, Hit};
pub use import::{
    abandon, batch_unique, clear_selection, click_group, click_row, close_import, collapse_cursor,
    confirm_import, cursor_id, cursor_slots, expand_cursor, handle_key, import_may_open, move_down,
    move_first, move_last, move_up, open_import, plan_confirm, preview_slots, select_all_visible,
    slugify, toggle, toggle_group, unique_slug, visible_rows, wheel, ConfirmOutcome, ImportPlan,
    ImportState,
};
pub use keys::{apply_key, apply_state, save_state, setup_after_import, NEW_THEME_LABEL};
pub use seams::{
    ApplyFn, BackupFn, DeleteFn, ImportLibrary, ImportWriter, Library, OverridesReport, PromptFn,
    WriteFn, WriteOutcome,
};
pub use state::{
    adjust_of, click_overrides, click_setup, click_slot, click_theme, enter_setup, head_label,
    needs_setup, session_path, slot_index, slot_name, top_subject, EditorState, OverridesState,
};
pub use state::{
    is_hex, normalize_hex, report_session, SessionReport, APPLY_KEY, ARROWS, DIRTY_MARK,
    ENTER_KEYS, INITIAL_SEL, MULT_STEPS, QUIT_KEYS, SAVE_KEY,
};

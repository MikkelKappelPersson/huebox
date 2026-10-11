//! The Ratatui shell: keys in, redraw, two prompts.
//!
//! Rust port of `huebox/app.py`. Thin by rule: [`huebox_editor`] owns ALL
//! editing behaviour (`EditorState` + `apply_key`), [`huebox_render`]
//! owns every frame row, and this crate owns only what a compositor must —
//! getting a key in ([`keys`]), redrawing ([`app`]), and taking the
//! terminal back for the two prompts ([`prompts`]).
//!
//! # The rules this shell keeps
//!
//! * One translation table ([`keys::translate`]) is the only place the
//!   crossterm and editor key vocabularies meet. Unknown keys answer
//!   `"?"` (a no-op in `apply_key`), never a panic.
//! * One input router per screen ([`router`]) mirrors `on_key`'s guards:
//!   import, then setup, then overrides, then picker, then editor. Popups
//!   never stack.
//! * A click resolves through row-announced hits and goes through the
//!   `apply_key` path ([`mouse`]) — it never touches a colour directly.
//!   Chrome clicks select nothing. The wheel moves the picker and the
//!   import list, never the editor frame.
//! * Popups are centered areas over a dimmed base ([`dialog`]): clear plus
//!   the buffer's own background, never an opaque foreign fill, never
//!   stacked. The footer bar ([`footer`]) carries the same hint pairs plus
//!   the status last, folded to two lines in dialogs.
//! * Cells map to Ratatui spans directly ([`cells`]) — there is no
//!   `from_ansi` anywhere in this crate. Bold survives only where the
//!   brief restores it (status, wordmark); there is no DIM anywhere.
//! * [`debug`] ports the profiling seam from the first commit:
//!   `HUEBOX_DEBUG` file-logged per-stage timings (`HUEBOX_DEBUG_LOG`),
//!   same line shapes as `app.py`.
//!
//! # UI-framework boundary
//!
//! `ratatui` (with the `crossterm` feature) is the single backend
//! dependency. Crossterm event types are imported ONLY via
//! `ratatui::crossterm::event` (research §1 binds: one backend dep, or two
//! crossterm versions mismatch and nothing compiles). No test in this
//! crate touches a real tty: frames render into `TestBackend`, prompts
//! read from injected buffers.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod app;
pub mod cells;
pub mod debug;
pub mod dialog;
pub mod footer;
pub mod keys;
pub mod mouse;
pub mod panel;
pub mod prompts;
pub mod router;

pub use app::{run, BodyMode, PopupKind, PopupLayout, Shell, SideCache, StackedCache, TopCache};
pub use cells::{hex_color, line_for, render_rows, span_for};
pub use dialog::{popup_rect, IMPORT_HINTS};
pub use footer::{bar_line_single, bar_lines, fold_footer, FooterFold};
pub use keys::translate;
pub use mouse::{click_at, wheel};
pub use prompts::{read_prompt_line, suspending_prompter};
pub use router::{route_key, Outcome, Screen};

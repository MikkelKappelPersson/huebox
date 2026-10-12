//! Injected seams: the only way this crate touches the world.
//!
//! `cli` (P6) builds the real ones; tests hand in lambdas. The editor
//! never imports themes/detect — the dependency rule — so the mirror
//! structs below (`status` + optional `(main, keys)`) live here, not in
//! `huebox-lib`.

use crate::core::Slots;

/// Ghostty inline colours shadowing the exported theme file: the config
/// holding them plus the colour keys found in it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OverridesReport {
    /// The main config file holding the inline colours.
    pub main: String,
    /// Colour keys shadowing the export.
    pub keys: Vec<String>,
}

/// A save/apply seam's answer: the status line plus, for Ghostty-aware
/// writers, the overrides that arm the delete-or-keep popup.
///
/// Mirrors the Python dual shape — a bare status string (or `None`) and
/// the `(message, (main, keys) | None)` tuple — as one struct: empty
/// `status` reads `"saved"` / `"applied"`, `None` overrides arms nothing.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WriteOutcome {
    /// Status line; empty means the default (`"saved"` / `"applied"`).
    pub status: String,
    /// Ghostty overrides shadowing this write, if any.
    pub overrides: Option<OverridesReport>,
}

impl WriteOutcome {
    /// A plain status with no overrides.
    pub fn status(text: &str) -> Self {
        Self {
            status: text.to_owned(),
            overrides: None,
        }
    }

    /// The default status (`"saved"` / `"applied"`) with no overrides.
    pub fn ok() -> Self {
        Self::status("")
    }
}

/// Save callback: `write(theme, path, slots)`.
///
/// Handed the subject every save — a picker switch retargets mid-session.
/// `Ok` marks the buffer clean; `Err(msg)` leaves `saved` untouched with
/// status `write failed: {msg}`.
pub type WriteFn = Box<dyn FnMut(Option<&str>, &str, &Slots) -> Result<WriteOutcome, String>>;

/// Preview callback: `apply(theme, path, slots)`.
///
/// Same shape as [`WriteFn`]; `Ok` never moves `saved`.
pub type ApplyFn = Box<dyn FnMut(Option<&str>, &str, &Slots) -> Result<WriteOutcome, String>>;

/// Ghostty overrides deleter: `delete_overrides(main)`.
///
/// `Ok(msg)` closes the warning with `msg` (empty reads the `removed …`
/// default); `Err(msg)` closes it with `delete failed: {msg}`.
pub type DeleteFn = Box<dyn FnMut(&str) -> Result<String, String>>;

/// Pre-save snapshot: `<path>.huebox.bak`, once per session.
///
/// Fires before the first save when `backup_path` is set; `true` sets
/// `backup_made`. Best-effort: `false` never blocks a save.
pub type BackupFn = Box<dyn FnMut(&str) -> bool>;

/// One line of input: `prompt_hex` and `prompt_name` share the pattern.
///
/// `None` cancels with the buffer and the library exactly as they were.
pub type PromptFn = Box<dyn FnMut(&str) -> Option<String>>;

/// The three things the picker may ask of the theme library.
///
/// Injected, never imported. Failure is by return value, never a panic
/// into the key loop: `names` is a (possibly empty) list, `load` gives
/// `(slots, path)` or `None`, `create` gives `Ok(path)` or `Err(problem)`
/// with the problem landing on the status line. Methods take `&self` so
/// read-only doubles stay trivial; recording doubles use a `RefCell` log.
pub trait Library {
    /// Theme names in list order (`[]` when the library is unreachable).
    fn names(&self) -> Vec<String>;
    /// `(slots, path)` for `name`, or `None` when it will not open.
    fn load(&self, name: &str) -> Option<(Slots, String)>;
    /// Write the buffer as `name` (`force` overwrites a taken name):
    /// `Ok(path)` or `Err(problem)`.
    fn create(&self, name: &str, slots: &Slots, force: bool) -> Result<String, String>;
}

/// The two things the import popup may ask of provider themes, plus the
/// provider-to-format mapping for the `source` (`fmt:path`) the confirm
/// plan records.
///
/// Mirrors the picker's [`Library`]: failure is by return value — a dead
/// listing reads as the empty group, a dead read as no colours.
pub trait ImportLibrary {
    /// `[(display, path)]` in list order (`[]` when unreachable).
    fn entries(&self, provider: &str) -> Vec<(String, String)>;
    /// The theme's slots, or `None` when it holds no colours.
    fn read(&self, provider: &str, path: &str) -> Option<Slots>;
    /// The `source` format for `provider` (its own name when unmapped).
    fn format_of(&self, provider: &str) -> String;
}

/// The import confirm writer: one call per plan, truth files only.
///
/// `Ok(())` lands the theme; `Err(problem)` folds into the session notes
/// as `{name}: {problem}`. Never retargets the session, never touches
/// the edit buffer.
pub type ImportWriter = Box<dyn FnMut(&str, &Slots, &str) -> Result<(), String>>;

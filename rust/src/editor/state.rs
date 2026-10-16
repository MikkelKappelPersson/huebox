//! `EditorState`: everything one editor session mutates.
//!
//! The buffer renders every frame and reaches disk only via the injected
//! [`WriteFn`](crate::editor::WriteFn), called from the Ctrl+S branch of
//! [`apply_key`](crate::editor::apply_key); the injected apply pushes the buffer
//! without touching disk, called from Ctrl+A. `saved` is the last-save
//! snapshot, so [`dirty`](EditorState::dirty) means "the buffer differs
//! from what is on disk" — that is what arms Esc, and what blocks a theme
//! switch. An apply never moves `saved`.

use crate::core::Slots;
use crate::render::grid::{grid_geometry, Grid};
use crate::render::palette::SLOTS;

use crate::editor::seams::{
    ApplyFn, BackupFn, DeleteFn, EnsureTargetFn, Library, PromptFn, WriteFn,
};

/// The slot selected on open and after a theme switch (`palette-5`).
pub const INITIAL_SEL: usize = 5;
/// The step multiplier ladder `f` cycles.
pub const MULT_STEPS: [u32; 3] = [1, 5, 20];
/// Save key (Ctrl+S).
pub const SAVE_KEY: &str = "\x13";
/// Apply key (Ctrl+A); the compositor spelling `"ctrl+a"` works too.
pub const APPLY_KEY: &str = "\x01";
/// `import_state` keeps the same three: the headless tests speak `\r`,
/// the compositor speaks `enter`, and both arrive at the same branch.
pub const ENTER_KEYS: [&str; 3] = ["\r", "\n", "enter"];
/// Arrow spellings.
pub const ARROWS: [&str; 4] = ["up", "down", "left", "right"];
/// Quit spellings: a clean Esc quits at once, a dirty one arms.
/// `escape` is the headless spelling (`enter` in [`ENTER_KEYS`] is the
/// same arrangement); the compositor speaks `esc`.
pub const QUIT_KEYS: [&str; 4] = ["esc", "escape", "Q", "\x03"];
/// The spec's own dirty mark, not an ASCII stand-in.
pub const DIRTY_MARK: &str = "●";
/// A state starts at this width; the draw loop replaces it with the real
/// geometry on every frame.
pub const FALLBACK_COLS: usize = 80;

/// The Ghostty overrides warning while it is up: the config holding the
/// inline colours plus the shadowing keys.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct OverridesState {
    /// Main config file holding the inline colours.
    pub main: String,
    /// Colour keys shadowing the export.
    pub keys: Vec<String>,
}

/// Everything one editor session mutates.
///
/// `theme` is the theme being edited (`None` in a legacy direct-mode
/// session); `library` is the injected seam the picker asks for themes.
/// Each can change mid-session, which is why the writer is bound to the
/// state and not to the caller's arguments. `grid` is the frame's shape:
/// the draw loop replaces it with the live geometry on every frame,
/// because the arrow keys move through the grid the user can see.
///
/// `present` marks which of the 22 slots the buffer holds. Production
/// readers fill gaps with `MISSING` grey, so all 22 are present there; a
/// slot a file omits keeps `MISSING` (painted in place, like the Python
/// `slots.get(name, MISSING)`), while adjust and hex entry stay inert on
/// it — the `value is None` early return, kept exact.
pub struct EditorState {
    /// The edit buffer: renders every frame, saved on Ctrl+S only.
    pub slots: Slots,
    /// The last-save snapshot; [`dirty`](EditorState::dirty) compares it.
    pub saved: Slots,
    /// Which slots the buffer holds (all 22 in production).
    pub present: [bool; 22],
    /// Selected slot index into the 22 canonical slots.
    pub sel: usize,
    /// `(slot, previous)` per adjust/hex edit; a save never clears it.
    pub undo: Vec<(String, String)>,
    /// Status line (last tail row / dialog bar).
    pub status: String,
    /// Step multiplier from [`MULT_STEPS`].
    pub mult: u32,
    /// Second-Esc pending (a dirty first Esc arms, any other key disarms).
    pub armed: bool,
    /// Any disk write this session.
    pub written: bool,
    /// Config to snapshot as `<path>.huebox.bak` (`None`: huebox owns the
    /// file — theme files — so no backup).
    pub backup_path: Option<String>,
    /// Whether the pre-save snapshot was created.
    pub backup_made: bool,
    /// Save seam: `write(theme, path, slots)`.
    pub write: WriteFn,
    /// Preview seam: `apply(theme, path, slots)` (`None`: nothing to apply).
    pub apply: Option<ApplyFn>,
    /// Overrides deleter behind the Ghostty warning.
    pub delete_overrides: Option<DeleteFn>,
    /// Pre-save snapshot seam (see [`BackupFn`](crate::editor::BackupFn)).
    pub backup: Option<BackupFn>,
    /// Hex-entry prompt (`h` / `X`).
    pub prompt_hex: Option<PromptFn>,
    /// Theme-name prompt (`n` / `N` in and out of the picker).
    pub prompt_name: Option<PromptFn>,
    /// The session is done.
    pub quit: bool,
    /// Theme being edited (`None`: direct mode, the config is the truth).
    pub theme: Option<String>,
    /// Push target label.
    pub fmt: String,
    /// The file a save writes.
    pub path: String,
    /// Picker seam (`None`: no theme library in this session).
    pub library: Option<Box<dyn Library>>,
    /// Name created via `n`/`N`/setup this session (for the exit report).
    pub created: Option<String>,
    /// Theme names while the picker is up (`None` closes it).
    pub overlay: Option<Vec<String>>,
    /// Picker selection.
    pub overlay_index: usize,
    /// First-run choice while up (`None` closes it).
    pub setup: Option<usize>,
    /// Typed name while naming in the setup popup (`None`: choosing).
    pub setup_name: Option<String>,
    /// Setup chose import: the shell opens the popup after dismiss, so
    /// modals never stack.
    pub import_pending: bool,
    /// Ghostty warning while up (`None` closes it).
    pub overrides: Option<OverridesState>,
    /// Warning selection (`1` = keep: the safe default).
    pub overrides_index: usize,
    /// No-target session: the terminal holds no colours, so the save/apply
    /// path ensures the config before the existing push runs.
    pub no_target: bool,
    /// `no theme detected` choice while up (`None` closes it).
    pub no_theme: Option<usize>,
    /// Bundled picker cursor while listing bundled themes (`None`: choices).
    pub no_theme_bundled: Option<usize>,
    /// Bundled themes preloaded by the composition root: `(name, slots)`.
    /// Data, not a seam — the bundle is static, so tests set it directly.
    pub bundled: Vec<(String, Slots)>,
    /// Ensure-target seam behind save/apply in no-target sessions.
    pub ensure_target: Option<EnsureTargetFn>,
    /// The frame's shape, refreshed every frame.
    pub grid: Grid,
}

impl EditorState {
    /// A session over `slots` (all 22 present) with its save seam.
    pub fn new(slots: Slots, write: WriteFn) -> Self {
        let saved = slots.clone();
        Self {
            slots,
            saved,
            present: [true; 22],
            sel: INITIAL_SEL,
            undo: Vec::new(),
            status: String::new(),
            mult: MULT_STEPS[0],
            armed: false,
            written: false,
            backup_path: None,
            backup_made: false,
            write,
            apply: None,
            delete_overrides: None,
            backup: None,
            prompt_hex: None,
            prompt_name: None,
            quit: false,
            theme: None,
            fmt: String::new(),
            path: String::new(),
            library: None,
            created: None,
            overlay: None,
            overlay_index: 0,
            setup: None,
            setup_name: None,
            import_pending: false,
            overrides: None,
            overrides_index: 1,
            no_target: false,
            no_theme: None,
            no_theme_bundled: None,
            bundled: Vec::new(),
            ensure_target: None,
            grid: grid_geometry(FALLBACK_COLS, "  "),
        }
    }

    /// A session holding only `pairs` (for files omitting slots); every
    /// other slot reads `MISSING` and refuses adjust/hex entry.
    ///
    /// Panics on an unknown slot name — the 22 canonical names are the
    /// whole model.
    pub fn from_pairs(pairs: &[(&str, &str)], write: WriteFn) -> Self {
        let mut slots = Slots::default();
        let mut present = [false; 22];
        for (name, hex) in pairs {
            slots.set(name, hex);
            present[slot_index(name)] = true;
        }
        let saved = slots.clone();
        let mut st = Self::new(slots, write);
        st.saved = saved;
        st.present = present;
        st
    }

    /// Whether the buffer differs from the last save — what arms Esc and
    /// blocks a theme switch.
    pub fn dirty(&self) -> bool {
        self.slots != self.saved
    }

    /// The buffer's hex for `name`, or `None` for a slot the file omits.
    pub fn slot_value(&self, name: &str) -> Option<&str> {
        if self.present[slot_index(name)] {
            Some(self.slots.get(name))
        } else {
            None
        }
    }

    /// `(names, index, current)` for the draw call, or `None` when closed.
    ///
    /// `current` is the theme the buffer holds, so it is only marked
    /// while it is one of the rows on screen.
    pub fn picker_frame(&self) -> Option<(Vec<String>, usize, String)> {
        let overlay = self.overlay.as_ref()?;
        let current = match &self.theme {
            Some(theme) if overlay.iter().any(|name| name == theme) => theme.clone(),
            _ => String::new(),
        };
        Some((overlay.clone(), self.overlay_index, current))
    }
}

impl std::fmt::Debug for EditorState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EditorState")
            .field("slots", &self.slots)
            .field("saved", &self.saved)
            .field("sel", &self.sel)
            .field("undo", &self.undo)
            .field("status", &self.status)
            .field("mult", &self.mult)
            .field("armed", &self.armed)
            .field("written", &self.written)
            .field("quit", &self.quit)
            .field("theme", &self.theme)
            .field("fmt", &self.fmt)
            .field("path", &self.path)
            .field("created", &self.created)
            .field("overlay", &self.overlay)
            .field("overlay_index", &self.overlay_index)
            .field("setup", &self.setup)
            .field("setup_name", &self.setup_name)
            .field("import_pending", &self.import_pending)
            .field("overrides", &self.overrides)
            .field("overrides_index", &self.overrides_index)
            .finish_non_exhaustive()
    }
}

/// Canonical index of `slot`.
///
/// Panics on an unknown name.
pub fn slot_index(slot: &str) -> usize {
    SLOTS
        .iter()
        .position(|name| *name == slot)
        .unwrap_or_else(|| panic!("huebox-editor: unknown slot {slot:?}"))
}

/// Canonical name of `sel`, or `None` past the 22 slots.
pub fn slot_name(sel: usize) -> Option<&'static str> {
    SLOTS.get(sel).copied()
}

/// Whether `key` is an adjust key: `(channel, direction)`.
///
/// No vim quartet: `h` takes hex entry and `i` opens the import popup, so
/// `h`/`j`/`k`/`l` stay unbound — `w`/`e`, `s`/`d` and `x`/`c` already
/// cover every axis.
pub fn adjust_of(key: &str) -> Option<(char, i32)> {
    match key {
        "w" => Some(('h', -1)),
        "e" => Some(('h', 1)),
        "s" => Some(('s', -1)),
        "d" => Some(('s', 1)),
        "x" => Some(('v', -1)),
        "c" => Some(('v', 1)),
        "H" => Some(('s', -1)),
        "L" => Some(('s', 1)),
        _ => None,
    }
}

/// `color.is_hex`: an optional `#` plus exactly six hex digits, padded
/// with ignorable whitespace.
pub fn is_hex(value: &str) -> bool {
    let body = value.trim().strip_prefix('#').unwrap_or(value.trim());
    body.len() == 6 && body.bytes().all(|b| b.is_ascii_hexdigit())
}

/// `color.normalize_hex`: stripped, lowercased, one leading `#`.
pub fn normalize_hex(value: &str) -> String {
    format!("#{}", value.trim().trim_start_matches('#').to_lowercase())
}

/// The status bar's subject: `<theme> ● <fmt>`, or `direct:<path>`.
///
/// A theme session names the theme plus the push target; the `●` appears
/// only while the buffer differs from the last save. A direct-mode session
/// names the config instead, because that is the file its Ctrl+S writes.
pub fn head_label(st: &EditorState) -> String {
    match &st.theme {
        None => format!("direct:{}", st.path),
        Some(theme) => {
            let mut parts = vec![theme.clone()];
            if st.dirty() {
                parts.push(DIRTY_MARK.to_owned());
            }
            if !st.fmt.is_empty() {
                parts.push(st.fmt.clone());
            }
            parts.join(" ")
        }
    }
}

/// The dim path after the subject — theme sessions only.
///
/// `direct:<path>` already carries the config's path; printing it twice
/// reads like two files.
pub fn session_path(st: &EditorState) -> String {
    match &st.theme {
        Some(_) => st.path.clone(),
        None => String::new(),
    }
}

/// The compositor top's `(label, path)`: name and file, separately.
///
/// Direct sessions arrive as one long `direct:<path>` label, which prices
/// the config path's length into the logo ladder — splitting hands the
/// ladder the six-letter name (and the meta the truncatable path, like
/// every theme file), so both sessions lay out alike.
pub fn top_subject(st: &EditorState, head: &str) -> (String, String) {
    if st.theme.is_none() && head.starts_with("direct:") {
        return ("direct".to_owned(), head["direct:".len()..].to_owned());
    }
    (head.to_owned(), session_path(st))
}

/// Whether the first-run choice is owed: a library with no themes.
///
/// No library means nothing to create through, so nothing to choose with:
/// the session stays a plain editor, exactly as before.
pub fn needs_setup(st: &EditorState) -> bool {
    match &st.library {
        None => false,
        Some(library) => library.names().is_empty(),
    }
}

/// Put the first-run choice up, when the library is empty.
///
/// Returns whether it opened. Never over an open no-theme choice, picker
/// or overrides warning; idempotent while the choice is up.
pub fn enter_setup(st: &mut EditorState) -> bool {
    if st.setup.is_some() || st.no_theme.is_some() || st.overlay.is_some() || st.overrides.is_some()
    {
        return st.setup.is_some();
    }
    if !needs_setup(st) {
        return false;
    }
    st.setup = Some(0);
    st.status.clear();
    true
}

/// Click a swatch or interface cell: selection follows the click.
///
/// Chrome clicks never reach here — the shell resolves through the hits
/// first and only a slot calls this — so this only ever names a slot.
/// Selection, not a colour edit: no undo, no status, no arm.
pub fn click_slot(st: &mut EditorState, slot: usize) {
    if slot < SLOTS.len() && slot != st.sel {
        st.sel = slot;
    }
}

/// Click a picker row: the selection moves onto it and confirms through
/// the one key surface, so a click and Enter cannot diverge.
pub fn click_theme(st: &mut EditorState, row: usize) {
    if let Some(overlay) = &st.overlay {
        if row < overlay.len() {
            st.overlay_index = row;
            crate::editor::keys::apply_key("\r", st);
        }
    }
}

/// Click a first-run choice row: choose through the one key surface.
pub fn click_setup(st: &mut EditorState, row: usize) {
    if st.setup.is_some() && row < crate::editor::dialogs::SETUP_CHOICES.len() {
        st.setup = Some(row);
        crate::editor::keys::apply_key("\r", st);
    }
}

/// Click an overrides row: choose through the one key surface.
pub fn click_overrides(st: &mut EditorState, row: usize) {
    if st.overrides.is_some() && row < crate::editor::dialogs::OVERRIDES_CHOICES.len() {
        st.overrides_index = row;
        crate::editor::keys::apply_key("\r", st);
    }
}

/// Whether the `no theme detected` dialog is owed: a no-target session
/// whose buffer holds no library theme yet.
///
/// Normal direct sessions (`no_target` false) never owe it — a `theme`
/// of `None` there is the legacy direct mode, not an empty terminal.
pub fn needs_no_theme(st: &EditorState) -> bool {
    st.no_target && st.theme.is_none()
}

/// Put the `no theme detected` choice up, when a no-target session owes it.
///
/// Returns whether it opened. Never over an open no-theme choice, setup,
/// picker or overrides warning; idempotent while the choice is up.
pub fn enter_no_theme(st: &mut EditorState) -> bool {
    if st.no_theme.is_some() || st.setup.is_some() || st.overlay.is_some() || st.overrides.is_some()
    {
        return st.no_theme.is_some();
    }
    if !needs_no_theme(st) {
        return false;
    }
    st.no_theme = Some(0);
    st.no_theme_bundled = None;
    st.status.clear();
    true
}

/// Re-arm the `no theme detected` dialog after the import popup closes
/// still themeless.
///
/// The shell calls this when an import lands nothing in a no-target
/// session: a session that grew a theme stays in the editor, where `t`
/// opens it.
pub fn no_theme_after_import(st: &mut EditorState) {
    if st.no_theme.is_none() && needs_no_theme(st) {
        enter_no_theme(st);
    }
}

/// Click a `no theme detected` choice row: choose through the one key surface.
pub fn click_no_theme(st: &mut EditorState, row: usize) {
    if st.no_theme.is_some()
        && st.no_theme_bundled.is_none()
        && row < crate::editor::dialogs::NO_THEME_CHOICES.len()
    {
        st.no_theme = Some(row);
        crate::editor::keys::apply_key("\r", st);
    }
}

/// Click a bundled theme row: move the cursor there then confirm through
/// the one key surface, so a click and Enter cannot diverge.
pub fn click_no_theme_bundled(st: &mut EditorState, row: usize) {
    if st.no_theme.is_some() && st.no_theme_bundled.is_some() && row < st.bundled.len() {
        st.no_theme_bundled = Some(row);
        crate::editor::keys::apply_key("\r", st);
    }
}

/// What the session has to say once the frame is done.
///
/// A function rather than a driver, because the wording is huebox's and
/// not the compositor's: the shell runs the session and prints these
/// afterwards — `stdout` lines with `println!` each (an element holding a
/// trailing `\n` prints its own blank, exactly as the Python `print`
/// did), `stderr` lines as `huebox: {line}`. The report's wording is the
/// caller's: it knows what it pushed and what it could not read.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct SessionReport {
    /// Lines for stdout, one `println!` each.
    pub stdout: Vec<String>,
    /// Lines for stderr, one `huebox: {line}` each.
    pub stderr: Vec<String>,
}

/// Build the exit report for `st` (`report` + `notes` go to stderr).
pub fn report_session(st: &EditorState, report: &[String], notes: &[String]) -> SessionReport {
    let mut stdout = Vec::new();
    if st.written {
        match &st.theme {
            Some(theme) => {
                stdout.push(format!("  saved theme {theme}  {}", st.path));
                stdout.push(String::new());
            }
            None => {
                stdout.push(format!("  saved {}", st.path));
                if st.backup_made {
                    if let Some(backup) = &st.backup_path {
                        stdout.push(format!(
                            "  backup of the pre-save state: {backup}.huebox.bak"
                        ));
                    }
                }
                stdout.push("  reload your terminal to see the change\n".to_owned());
            }
        }
    } else if st.dirty() {
        stdout.push("  nothing saved - the buffer was discarded\n".to_owned());
    } else if let Some(created) = &st.created {
        stdout.push(format!("  created theme {created}  {}", st.path));
        stdout.push("  Ctrl+S saves it to the terminal\n".to_owned());
    } else {
        stdout.push("  no changes\n".to_owned());
    }
    let stderr = report
        .iter()
        .chain(notes.iter())
        .map(|line| format!("huebox: {line}"))
        .collect();
    SessionReport { stdout, stderr }
}

//! `apply_key`: one keypress against the buffer or the popup on top.
//!
//! Pure apart from disk and prompts: the injected `write` fires on Ctrl+S
//! (the first save also snapshots `<path>.huebox.bak` through the
//! injected backup seam, and the injected `ensure_target` ensures the
//! terminal config first in no-target sessions), the injected `apply`
//! fires on Ctrl+A, and `prompt_hex` / `prompt_name` answer one line each
//! — so the key surface itself is testable without a terminal. Sets
//! `st.quit` when the session is done: a clean Esc (or Ctrl+C) quits at
//! once, a dirty one arms and takes a second press. An open `no theme
//! detected` choice takes the whole surface first, then the first-run
//! choice, the Ghostty warning, and the picker before the editor — so
//! quitting and colour edits cannot happen behind any of them.

use crate::core::{hex_to_rgb, hsv_to_rgb, rgb_to_hex, rgb_to_hsv, step_hsv};
use crate::render::grid::move_slot;
use crate::render::palette::SLOTS;

use crate::editor::state::{
    adjust_of, enter_setup, is_hex, needs_setup, normalize_hex, EditorState, OverridesState,
    APPLY_KEY, ARROWS, ENTER_KEYS, INITIAL_SEL, MULT_STEPS, QUIT_KEYS, SAVE_KEY,
};

/// The prompt for a new theme name.
pub const NEW_THEME_LABEL: &str = "  new theme name: ";

/// One keypress against the buffer or the popup on top.
///
/// Key spellings are the translated names the compositor speaks (`up`,
/// `esc`, `\x13`, `\r`, …); anything unbound disarms a pending discard
/// and clears the status, and nothing else.
pub fn apply_key(key: &str, st: &mut EditorState) {
    if st.no_theme.is_some() {
        no_theme_key(key, st);
        return;
    }
    if st.setup.is_some() {
        setup_key(key, st);
        return;
    }
    if st.overrides.is_some() {
        overrides_key(key, st);
        return;
    }
    if st.overlay.is_some() {
        overlay_key(key, st);
        return;
    }

    if QUIT_KEYS.contains(&key) {
        if st.dirty() && !st.armed {
            st.armed = true;
            st.status = "unsaved changes - Esc again to discard".to_owned();
            return;
        }
        st.quit = true;
        return;
    }

    st.armed = false; // any other key disarms a pending discard
    st.status.clear();

    if ARROWS.contains(&key) {
        st.sel = move_slot(st.sel, key, st.grid);
    } else if key == "f" {
        let at = MULT_STEPS.iter().position(|m| *m == st.mult).unwrap_or(0);
        st.mult = MULT_STEPS[(at + 1) % MULT_STEPS.len()];
        st.status = format!("step size x{}", st.mult);
    } else if key == SAVE_KEY {
        save_state(st);
    } else if key == APPLY_KEY || key == "ctrl+a" {
        if st.theme.is_none() {
            // Direct mode: the config is the truth, so a preview is a save.
            save_state(st);
        } else {
            apply_state(st);
        }
    } else if key == "u" {
        if let Some((slot, previous)) = st.undo.pop() {
            st.slots.set(&slot, &previous);
            st.status = format!("undid {slot}");
        }
    } else if key == "r" {
        st.undo.clear();
        st.slots = st.saved.clone();
        st.status = if st.written {
            "reverted to last save".to_owned()
        } else {
            "reverted to start".to_owned()
        };
    } else if key == "t" {
        open_overlay(st);
    } else if key == "N" {
        save_as_new(st);
    } else if st.slot_value(SLOTS[st.sel]).is_none() {
        // slot absent from this config: nothing to do
    } else if let Some((channel, direction)) = adjust_of(key) {
        adjust(st, channel, direction);
    } else if key == "h" || key == "X" {
        prompt(st);
    }
}

/// Ctrl+S — the one path from buffer to disk.
///
/// A failed write leaves `saved` untouched: the buffer stays dirty and
/// Esc keeps guarding it. The backup is best-effort and never blocks a
/// save. A write reporting Ghostty overrides arms the delete-or-keep
/// popup; the buffer is already clean, so delete/keep only ever touch the
/// terminal config.
pub fn save_state(st: &mut EditorState) {
    if let Some(path) = st.backup_path.clone() {
        if !st.written {
            st.backup_made = match &mut st.backup {
                Some(backup) => backup(&path),
                None => false,
            };
        }
    }
    let theme = st.theme.clone();
    let fmt = st.fmt.clone();
    let path = st.path.clone();
    if let Some(ensure) = st.ensure_target.as_mut() {
        if let Err(error) = ensure(theme.as_deref(), &fmt, &path) {
            st.status = format!("write failed: {error}");
            return;
        }
    }
    let slots = st.slots.clone();
    match (st.write)(theme.as_deref(), &path, &slots) {
        Err(error) => {
            st.status = format!("write failed: {error}");
        }
        Ok(outcome) => {
            st.saved = st.slots.clone();
            st.written = true;
            st.status = if outcome.status.is_empty() {
                "saved".to_owned()
            } else {
                outcome.status
            };
            if let Some(over) = outcome.overrides {
                open_overrides(st, &over.main, &over.keys);
            }
        }
    }
}

/// Ctrl+A — push the buffer without touching truth.
///
/// `saved` stays where it was: the buffer stays dirty and Esc keeps
/// guarding it. A failed push leaves the buffer alone the same way. A
/// push reporting Ghostty overrides arms the delete-or-keep popup; the
/// buffer stays dirty either way.
pub fn apply_state(st: &mut EditorState) {
    let theme = st.theme.clone();
    let fmt = st.fmt.clone();
    let path = st.path.clone();
    if let Some(ensure) = st.ensure_target.as_mut() {
        if let Err(error) = ensure(theme.as_deref(), &fmt, &path) {
            st.status = format!("apply failed: {error}");
            return;
        }
    }
    let Some(apply) = &mut st.apply else {
        st.status = "nothing to apply".to_owned();
        return;
    };
    let slots = st.slots.clone();
    match apply(theme.as_deref(), &path, &slots) {
        Err(error) => {
            st.status = format!("apply failed: {error}");
        }
        Ok(outcome) => {
            st.status = if outcome.status.is_empty() {
                "applied".to_owned()
            } else {
                outcome.status
            };
            if let Some(over) = outcome.overrides {
                open_overrides(st, &over.main, &over.keys);
            }
        }
    }
}

/// Nudge the selected slot one step along `channel`.
fn adjust(st: &mut EditorState, channel: char, direction: i32) {
    let name = SLOTS[st.sel];
    let Some(before) = st.slot_value(name).map(str::to_owned) else {
        return;
    };
    let (r, g, b) = hex_to_rgb(&before);
    let (h, s, v) = rgb_to_hsv(r, g, b);
    let (h2, s2, v2) = step_hsv(h, s, v, channel, direction, st.mult as f64);
    let (r2, g2, b2) = hsv_to_rgb(h2, s2, v2);
    st.undo.push((name.to_owned(), before));
    st.slots.set(name, &rgb_to_hex(r2, g2, b2));
}

/// `h` / `X` — hex entry, re-injected so `apply_key` stays testable.
fn prompt(st: &mut EditorState) {
    let name = SLOTS[st.sel];
    let Some(prompt_hex) = &mut st.prompt_hex else {
        return;
    };
    let typed = prompt_hex(&format!("  new hex for {name}: "));
    let Some(typed) = typed else {
        return; // cancelled: leave the buffer be
    };
    let typed = typed.trim().to_owned();
    if is_hex(&typed) {
        let hex = normalize_hex(&typed);
        if let Some(before) = st.slot_value(name).map(str::to_owned) {
            st.undo.push((name.to_owned(), before));
            st.slots.set(name, &hex);
            st.status = format!("{name} = {hex}");
        }
    } else {
        st.status = "not a valid 6-digit hex - ignored".to_owned();
    }
}

// --------------------------------------------------------------------------
// the theme picker
// --------------------------------------------------------------------------

/// `t` — the picker, with the session's own theme selected.
///
/// Blocked behind the overrides warning: popups never stack.
fn open_overlay(st: &mut EditorState) {
    if st.overrides.is_some() {
        return;
    }
    let Some(library) = &st.library else {
        st.status = "no theme library in this session".to_owned();
        return;
    };
    let names = library.names();
    if names.is_empty() {
        st.status = "no themes yet - N makes one from this buffer".to_owned();
        return;
    }
    st.overlay_index = match &st.theme {
        Some(theme) => names.iter().position(|name| name == theme).unwrap_or(0),
        None => 0,
    };
    st.overlay = Some(names);
    st.status.clear();
}

/// Enter in the picker: open the selected theme, and make it live.
///
/// Choosing a theme is choosing it for the terminal too, so the switch
/// runs the ordinary save path: the truth file is written, the theme is
/// pushed and the status line says which target took it. `Ctrl+S` still
/// saves the *buffer*; after a switch there is simply nothing left to
/// press, because the theme picked is already what the terminal shows.
fn open_selected(st: &mut EditorState) {
    if st.dirty() {
        st.status = "save (Ctrl+S) or revert (r) first".to_owned();
        return;
    }
    let Some(overlay) = &st.overlay else {
        return;
    };
    if overlay.is_empty() {
        st.status = "no themes yet - N makes one from this buffer".to_owned();
        return;
    }
    let name = overlay[st.overlay_index.min(overlay.len().saturating_sub(1))].clone();
    let loaded = st.library.as_ref().and_then(|library| library.load(&name));
    let Some((slots, path)) = loaded else {
        st.status = format!("{name} could not be opened");
        return;
    };
    adopt(st, &name, &slots, &path);
    st.overlay = None;
    save_state(st);
}

/// `n` in the picker: the buffer becomes a new theme.
///
/// Creating is not switching — the buffer is exactly what gets written,
/// so a dirty one is fine — but the new theme becomes the session's
/// subject, which is what makes this a way out of a legacy direct-mode
/// session. The picker stays up with the new row selected.
fn picker_new(st: &mut EditorState) {
    let Some((name, path)) = create_theme(st, NEW_THEME_LABEL) else {
        return;
    };
    let slots = st.slots.clone();
    adopt(st, &name, &slots, &path);
    st.created = Some(name.clone());
    open_overlay(st); // re-read the library, land on the new row
    st.status = format!("created {name} - current now");
}

/// `N` in the picker: the buffer becomes a new theme, and then it is saved.
///
/// The picker's half of [`save_as_new`]: one prompt, `create` + adopt,
/// then the ordinary save pipeline, so a theme made here is pushed
/// exactly like one saved all session. The picker stays open on the new
/// row, like [`picker_new`] — choosing among themes is still the list's
/// job, and a dirty buffer is fine, because the buffer is what gets
/// written. A save behind the open picker cannot arm the overrides
/// warning (popups never stack), so the save's own status line is all
/// the warning there is.
fn picker_save_new(st: &mut EditorState) {
    let Some((name, path)) = create_theme(st, NEW_THEME_LABEL) else {
        return;
    };
    let slots = st.slots.clone();
    adopt(st, &name, &slots, &path);
    st.created = Some(name.clone());
    save_state(st);
    let status = st.status.clone();
    open_overlay(st); // re-read the library, land on the new row
    st.status = status;
}

/// Keys while the picker is up: it owns the surface.
///
/// Nothing else can fire — no colour changes, no save, no quit — so a key
/// meant for the editor cannot do damage behind a list the user is
/// reading. `Esc`, `t`, `Q` and Ctrl+C all just put the editor back.
fn overlay_key(key: &str, st: &mut EditorState) {
    st.armed = false;
    st.status.clear();
    let len = st.overlay.as_ref().map(Vec::len).unwrap_or(0);
    if key == "up" {
        st.overlay_index = st.overlay_index.saturating_sub(1);
    } else if key == "down" {
        st.overlay_index = (st.overlay_index + 1).min(len.saturating_sub(1));
    } else if ENTER_KEYS.contains(&key) {
        open_selected(st);
    } else if key == "n" {
        picker_new(st);
    } else if key == "N" {
        picker_save_new(st);
    } else if key == "t" || QUIT_KEYS.contains(&key) {
        st.overlay = None;
    }
}

/// One line of input, or `""` when the prompt was cancelled.
///
/// The prompt is the same injected closure hex entry uses, so a cancelled
/// read returns to the editor with the buffer and the library exactly as
/// they were.
fn ask(st: &mut EditorState, label: &str) -> String {
    match &mut st.prompt_name {
        None => String::new(),
        Some(prompt_name) => prompt_name(label).unwrap_or_default().trim().to_owned(),
    }
}

/// Is this name in the library? Case-folded, as `create` compares.
fn taken(st: &EditorState, name: &str) -> bool {
    match &st.library {
        None => false,
        Some(library) => library
            .names()
            .iter()
            .any(|row| row.to_lowercase() == name.to_lowercase()),
    }
}

/// Make `name` the subject of the session.
///
/// A different theme means a different buffer, a different file to save,
/// and no history to carry over: `saved` is what was loaded, the undo log
/// is empty, the selection starts at `INITIAL_SEL`, and the pending-discard
/// arm is dropped — a fresh theme must never inherit a half-armed Esc.
fn adopt(st: &mut EditorState, name: &str, slots: &crate::core::Slots, path: &str) {
    st.slots = slots.clone();
    st.saved = slots.clone();
    st.present = [true; 22];
    st.undo.clear();
    st.sel = INITIAL_SEL;
    st.armed = false;
    st.theme = Some(name.to_owned());
    st.path = path.to_owned();
    st.backup_path = None; // huebox owns theme files: no .bak
}

/// Ask for a name and write the buffer as that theme.
///
/// A name that is taken gets one more line saying so, where `y`
/// overwrites it and any other name is used instead. Returns
/// `(name, path)`, or `None` with the reason on `st.status` and nothing
/// written.
fn create_theme(st: &mut EditorState, label: &str) -> Option<(String, String)> {
    if st.library.is_none() {
        st.status = "no theme library in this session".to_owned();
        return None;
    }
    let mut name = ask(st, label);
    if name.is_empty() {
        st.status = "cancelled - no theme created".to_owned();
        return None;
    }
    let mut force = false;
    if taken(st, &name) {
        let answer = ask(
            st,
            &format!("  {name} exists - y overwrites it, or type another name: "),
        );
        if answer.is_empty() {
            st.status = format!("cancelled - {name} is untouched");
            return None;
        }
        if answer.to_lowercase() == "y" || answer.to_lowercase() == "yes" {
            force = true;
        } else {
            name = answer;
            if taken(st, &name) {
                st.status = format!("{name} exists too - nothing created");
                return None;
            }
        }
    }
    let slots = st.slots.clone();
    // `library` is present — checked above — and `create` has the last word.
    match st
        .library
        .as_ref()
        .expect("library checked")
        .create(&name, &slots, force)
    {
        Err(problem) => {
            st.status = problem;
            None
        }
        Ok(path) => Some((name, path)),
    }
}

/// `N` — the buffer becomes a new theme, and then it is saved.
///
/// The way out of a legacy direct-config session: one prompt, `create` +
/// adopt, then the ordinary save pipeline, so a theme made here is pushed
/// exactly like one saved all session.
fn save_as_new(st: &mut EditorState) {
    let Some((name, path)) = create_theme(st, NEW_THEME_LABEL) else {
        return;
    };
    let slots = st.slots.clone();
    adopt(st, &name, &slots, &path);
    save_state(st);
}

// --------------------------------------------------------------------------
// the Ghostty overrides warning
// --------------------------------------------------------------------------

/// Arm the Ghostty overrides warning after a save or an apply.
///
/// Popups never stack: over an open choice or picker this is a no-op and
/// the save's own status line is all the warning there is. Otherwise the
/// warning owns the surface until delete or keep closes it, starting on
/// `keep` — the safe default.
fn open_overrides(st: &mut EditorState, main: &str, keys: &[String]) {
    let keys = keys.to_vec();
    if main.is_empty() || keys.is_empty() {
        return;
    }
    if st.setup.is_some() || st.overlay.is_some() || st.overrides.is_some() {
        return;
    }
    st.overrides = Some(OverridesState {
        main: main.to_owned(),
        keys,
    });
    st.overrides_index = 1;
    st.armed = false;
}

/// Delete the warned-about lines through the injected seam.
///
/// The buffer is untouched — overrides live in the terminal config, not in
/// truth — so `saved`/`dirty`/`written` stay as the save left them. A
/// missing seam or a failed delete keeps the reason on the status line and
/// still closes: the save itself succeeded, and a stuck popup would hold
/// the session hostage over a best-effort cleanup.
fn overrides_delete(st: &mut EditorState) {
    let main = st
        .overrides
        .as_ref()
        .map(|over| over.main.clone())
        .unwrap_or_default();
    let count = st
        .overrides
        .as_ref()
        .map(|over| over.keys.len())
        .unwrap_or(0);
    let Some(delete) = &mut st.delete_overrides else {
        st.status = "cannot delete overrides here".to_owned();
        st.overrides = None;
        return;
    };
    match delete(&main) {
        Err(error) => {
            st.status = format!("delete failed: {error}");
        }
        Ok(message) => {
            st.status = if message.is_empty() {
                format!("removed {count} override(s) from {main} - theme now shows")
            } else {
                message
            };
        }
    }
    st.overrides = None;
}

/// Keep the inline colours: the theme stays shadowed, by choice.
fn overrides_keep(st: &mut EditorState) {
    let main = st
        .overrides
        .as_ref()
        .map(|over| over.main.clone())
        .unwrap_or_default();
    let count = st
        .overrides
        .as_ref()
        .map(|over| over.keys.len())
        .unwrap_or(0);
    st.status = if main.is_empty() {
        "kept overrides".to_owned()
    } else {
        format!("kept {count} override(s) in {main} - theme stays shadowed")
    };
    st.overrides = None;
}

/// Keys while the overrides warning is up: it owns the surface.
///
/// Nothing else can fire — no colour changes, no save, no quit — so a key
/// meant for the editor cannot do damage behind a warning the user is
/// reading. `Esc`, `Q` and Ctrl+C keep (the safe default), never quit.
fn overrides_key(key: &str, st: &mut EditorState) {
    use crate::editor::dialogs::OVERRIDES_CHOICES;
    st.armed = false;
    if key == "up" || key == "left" {
        st.overrides_index = st.overrides_index.saturating_sub(1);
    } else if key == "down" || key == "right" {
        st.overrides_index = (st.overrides_index + 1).min(OVERRIDES_CHOICES.len() - 1);
    } else if ENTER_KEYS.contains(&key) {
        if OVERRIDES_CHOICES[st.overrides_index.min(OVERRIDES_CHOICES.len() - 1)] == "delete" {
            overrides_delete(st);
        } else {
            overrides_keep(st);
        }
    } else if key == "y" || key == "Y" {
        overrides_delete(st);
    } else if ["n", "N", "esc", "escape", "Q", "\x03", "t"].contains(&key) {
        overrides_keep(st);
    } else {
        // Anything unbound clears the status like every other overlay
        // (decision 60) — the warning stays open, the line resets.
        st.status.clear();
    }
}

// --------------------------------------------------------------------------
// the first-run setup choice
// --------------------------------------------------------------------------

/// Choose on the first-run choice: import, or start naming.
///
/// Import closes the choice and raises `import_pending` for the shell —
/// the popup is the compositor's, so the state only asks for it, the same
/// split the `i` key keeps. New opens the inline naming field instead of a
/// prompt, so the name is typed in the popup and the terminal is never
/// handed back mid-session.
fn setup_confirm(st: &mut EditorState) {
    if st.setup == Some(1) {
        st.setup_name = Some(String::new());
        st.status.clear();
        return;
    }
    st.setup = None;
    st.import_pending = true;
    st.status.clear();
}

/// Enter in naming mode: create the buffer under the typed name.
///
/// No prompts — the name arrives typed, so this is the prompt-free half of
/// `create_theme`: empty goes back to the choices, a taken name stays in
/// the field with the reason on the status line, and only a written theme
/// adopts and closes. A refusal from the library keeps the typed name, so
/// fixing a character does not mean retyping the rest.
fn setup_create(st: &mut EditorState) {
    let name = st.setup_name.as_deref().unwrap_or("").trim().to_owned();
    if st.library.is_none() {
        st.setup_name = None;
        st.status = "no theme library in this session".to_owned();
        return;
    }
    if name.is_empty() {
        st.setup_name = None;
        st.status = "cancelled - no theme created".to_owned();
        return;
    }
    if taken(st, &name) {
        st.status = format!("{name} exists - type another name");
        return;
    }
    let slots = st.slots.clone();
    match st
        .library
        .as_ref()
        .expect("library checked")
        .create(&name, &slots, false)
    {
        Err(problem) => {
            st.status = problem;
        }
        Ok(path) => {
            adopt(st, &name, &slots, &path);
            st.created = Some(name.clone());
            st.setup = None;
            st.setup_name = None;
            st.status = format!("created {name} - current now");
        }
    }
}

/// Keys while naming the new theme: a one-line field, typed inline.
///
/// Single characters (and `space`, which the compositor names rather than
/// sends) append up to the library's length rule; `backspace` deletes;
/// Enter creates; Esc backs out to the choices. Everything else — arrows,
/// colour keys, quit keys — is swallowed: the field has no cursor to move
/// and no edit to guard, and quitting stays two Escapes away.
fn setup_name_key(key: &str, st: &mut EditorState) {
    use crate::editor::dialogs::SETUP_NAME_MAX;
    if ENTER_KEYS.contains(&key) {
        setup_create(st);
    } else if key == "esc" || key == "escape" {
        st.setup_name = None;
        st.status.clear();
    } else if key == "backspace" {
        if let Some(name) = &mut st.setup_name {
            name.pop();
        }
        st.status.clear();
    } else if key == "space" || (key.chars().count() == 1 && is_name_char(key)) {
        let field = st.setup_name.get_or_insert_with(String::new);
        if field.chars().count() < SETUP_NAME_MAX {
            field.push(if key == "space" {
                ' '
            } else {
                key.chars().next().expect("one char")
            });
            st.status.clear();
        }
    }
}

/// A name character: printable, never a control code (`\x13` from Ctrl+S
/// included is not a name character).
fn is_name_char(key: &str) -> bool {
    match key.chars().next() {
        Some(ch) => !ch.is_control(),
        None => false,
    }
}

/// Keys while the first-run choice is up: it owns the surface.
///
/// Nothing else can fire — no colour changes, no save, no picker, no quit
/// behind the choice — so a key meant for the editor cannot do damage
/// before the session has a theme. `Esc`, `Q` and Ctrl+C quit outright:
/// there is no editor behind the choice to return to. While naming (the
/// inline field), keys go to the field instead.
fn setup_key(key: &str, st: &mut EditorState) {
    use crate::editor::dialogs::SETUP_CHOICES;
    st.armed = false;
    if st.setup_name.is_some() {
        setup_name_key(key, st);
        return;
    }
    st.status.clear();
    if key == "up" || key == "left" {
        st.setup = Some(st.setup.unwrap_or(0).saturating_sub(1));
    } else if key == "down" || key == "right" {
        st.setup = Some((st.setup.unwrap_or(0) + 1).min(SETUP_CHOICES.len() - 1));
    } else if ENTER_KEYS.contains(&key) {
        setup_confirm(st);
    } else if key == "i" || key == "I" {
        st.setup = Some(0);
        setup_confirm(st);
    } else if key == "n" || key == "N" {
        st.setup = Some(1);
        setup_confirm(st);
    } else if QUIT_KEYS.contains(&key) {
        st.setup = None;
        st.quit = true;
    }
}

/// Re-arm the first-run choice after the import popup closes still empty.
///
/// The shell calls this when an import lands nothing: a library that grew
/// themes stays in the editor, where `t` opens them.
pub fn setup_after_import(st: &mut EditorState) {
    if st.setup.is_none() && needs_setup(st) {
        enter_setup(st);
    }
}

// --------------------------------------------------------------------------
// the `no theme detected` choice
// --------------------------------------------------------------------------

/// Choose on the `no theme detected` dialog: create lists bundled themes,
/// import raises a pending import for the shell.
///
/// Import closes the choice and raises `import_pending` — the popup is the
/// compositor's, so the state only asks for it, the same split the `i` key
/// keeps. Create opens the inline bundled list instead of a prompt, so the
/// theme is picked in the popup and the terminal is never handed back.
fn no_theme_confirm(st: &mut EditorState) {
    use crate::editor::dialogs::NO_THEME_CHOICES;
    let choices = NO_THEME_CHOICES;
    let at = st.no_theme.unwrap_or(0).min(choices.len() - 1);
    if choices[at] == "import" {
        st.no_theme = None;
        st.no_theme_bundled = None;
        st.import_pending = true;
        st.status.clear();
        return;
    }
    if st.bundled.is_empty() {
        st.status = "no bundled themes".to_owned();
        return;
    }
    st.no_theme_bundled = Some(0);
    st.status.clear();
}

/// Enter on the bundled list: copy the highlighted bundle into the library
/// and load it into the buffer.
///
/// No prompts — the name arrives picked, so this is the prompt-free half of
/// `create_theme`: a taken name loads the existing theme instead of
/// failing, and only a loaded theme adopts and closes. A refusal from the
/// library stays in the list with the reason on the status line.
fn no_theme_create(st: &mut EditorState) {
    let at = st.no_theme_bundled.unwrap_or(0);
    let Some((name, slots)) = st.bundled.get(at).cloned() else {
        st.status = "no bundled themes".to_owned();
        return;
    };
    let Some(library) = &st.library else {
        st.status = "no theme library in this session".to_owned();
        return;
    };
    match library.create(&name, &slots, false) {
        Ok(path) => {
            adopt(st, &name, &slots, &path);
            st.created = Some(name.clone());
            st.no_theme = None;
            st.no_theme_bundled = None;
            st.status = format!("created {name} - current now");
        }
        Err(problem) if problem.contains("already exists") => match library.load(&name) {
            Some((loaded, path)) => {
                adopt(st, &name, &loaded, &path);
                st.no_theme = None;
                st.no_theme_bundled = None;
                st.status = format!("{name} already in library - current now");
            }
            None => {
                st.status = problem;
            }
        },
        Err(problem) => {
            st.status = problem;
        }
    }
}

/// Quit or close the `no theme detected` dialog.
///
/// Closing quits the session when the library is still empty — there is no
/// editor behind the choice to return to — and otherwise puts the editor
/// back, where `t` opens the library.
fn no_theme_quit(st: &mut EditorState) {
    let empty = match &st.library {
        None => true,
        Some(library) => library.names().is_empty(),
    };
    st.no_theme = None;
    st.no_theme_bundled = None;
    if empty {
        st.quit = true;
    }
    st.status.clear();
}

/// Keys while picking a bundled theme: a three-row list, typed nowhere.
///
/// Arrows move, Enter copies, Esc backs out to the two choices. Everything
/// else is swallowed: the list has no cursor to move and no edit to guard.
fn no_theme_bundled_key(key: &str, st: &mut EditorState) {
    let len = st.bundled.len();
    st.status.clear();
    if key == "up" || key == "left" {
        st.no_theme_bundled = Some(st.no_theme_bundled.unwrap_or(0).saturating_sub(1));
    } else if key == "down" || key == "right" {
        st.no_theme_bundled =
            Some((st.no_theme_bundled.unwrap_or(0) + 1).min(len.saturating_sub(1)));
    } else if ENTER_KEYS.contains(&key) {
        no_theme_create(st);
    } else if key == "esc" || key == "escape" {
        st.no_theme_bundled = None;
    }
}

/// Keys while the `no theme detected` choice is up: it owns the surface.
///
/// Nothing else can fire — no colour changes, no save, no picker, no quit
/// behind the choice — so a key meant for the editor cannot do damage
/// before the session has a theme. While picking (the bundled list), keys
/// go to the list instead.
fn no_theme_key(key: &str, st: &mut EditorState) {
    use crate::editor::dialogs::NO_THEME_CHOICES;
    st.armed = false;
    if st.no_theme_bundled.is_some() {
        no_theme_bundled_key(key, st);
        return;
    }
    st.status.clear();
    if key == "up" || key == "left" {
        st.no_theme = Some(st.no_theme.unwrap_or(0).saturating_sub(1));
    } else if key == "down" || key == "right" {
        st.no_theme = Some((st.no_theme.unwrap_or(0) + 1).min(NO_THEME_CHOICES.len() - 1));
    } else if ENTER_KEYS.contains(&key) {
        no_theme_confirm(st);
    } else if key == "c" || key == "C" {
        st.no_theme = Some(0);
        no_theme_confirm(st);
    } else if key == "i" || key == "I" {
        st.no_theme = Some(1);
        no_theme_confirm(st);
    } else if QUIT_KEYS.contains(&key) {
        no_theme_quit(st);
    }
}

//! One input router per screen, mirroring `on_key`'s guards.
//!
//! Rust port of `Editor.on_key`'s routing half (the paint half lives in
//! [`crate::app`]): while the import popup is up every key goes to the
//! import state; else the first-run choice, the Ghostty warning, the
//! picker, each owning the whole surface through the one key surface;
//! else the editor. Editor colour keys behind a popup are inert by
//! construction — no branch reaches `apply_key` underneath one — and
//! popups never stack (`i` while any popup owns the surface is a no-op).
//!
//! Closing is always read off the state, as in the Python screens: the
//! picker closing is `overlay is None`, the warning `overrides is None`,
//! the choice `setup is None` (or `quit`, or `import_pending`, which opens
//! the import popup after dismiss so modals never stack).

use huebox_editor::{
    apply_key, confirm_import, handle_key, import_may_open, open_import, setup_after_import,
    ConfirmOutcome,
};

use crate::app::Shell;

/// What one routed key asks of the event loop.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Repaint and keep going.
    Continue,
    /// Break the loop; the terminal restores on the way down.
    Quit,
}

/// Which surface owns the keys right now — the guard order, as a value.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Screen {
    /// The import popup (`is_open` interlock).
    Import,
    /// The first-run choice (`setup.is_some()`).
    Setup,
    /// The Ghostty warning (`overrides.is_some()`).
    Overrides,
    /// The theme picker (`overlay.is_some()`).
    Themes,
    /// The editor frame.
    Editor,
}

/// Post-input routing shared by keys and clicks: quit exits, and a setup
/// import choice opens the import popup after dismiss — never stacked.
pub fn settle(shell: &mut Shell) -> Outcome {
    if shell.state.quit {
        return Outcome::Quit;
    }
    if shell.state.import_pending {
        shell.state.import_pending = false;
        open_popup(shell);
    }
    Outcome::Continue
}

/// Take the import surface (the shell's half of `open_import`).
fn open_popup(shell: &mut Shell) {
    open_import(&mut shell.import);
    shell.import_open = true;
    // A fresh mount re-seeds the viewport past the title.
    shell.import_scroll = None;
}

/// One translated key against whichever screen is top.
pub fn route_key(shell: &mut Shell, key: &str) -> Outcome {
    if shell.import_open {
        return import_key(shell, key);
    }
    // Live collapsibles: `E`/`D`/`C` toggle their block wherever the
    // picker and the warning do not own the surface (never stacked over
    // a popup, never through the import list). The setup screen does not
    // gate them — the same rule as the Python `on_key`.
    if let Some(block) = crate::panel::collapse_target(key) {
        if shell.state.overlay.is_none()
            && shell.state.overrides.is_none()
            && crate::panel::collapsible_enabled()
        {
            if !shell.collapsed.remove(block) {
                shell.collapsed.insert(block.to_owned());
            }
            return settle(shell);
        }
    }
    if shell.state.setup.is_some() {
        // The first-run popup owns the surface: every key goes to the one
        // key surface, which routes outcomes from the state.
        apply_key(key, &mut shell.state);
        return settle(shell);
    }
    if shell.state.overrides.is_some() {
        // The Ghostty warning owns the surface the same way.
        apply_key(key, &mut shell.state);
        return settle(shell);
    }
    if shell.state.overlay.is_some() {
        // The picker owns the surface: `i` behind it is swallowed (a
        // second popup never stacks), everything else is the list's.
        if key == "i" || key == "I" {
            return Outcome::Continue;
        }
        apply_key(key, &mut shell.state);
        return settle(shell);
    }
    // The editor. `i` opens the import popup through the one call —
    // never blocked by a dirty buffer, never stacked over a popup.
    if key == "i" || key == "I" {
        if !shell.import_open && import_may_open(&shell.state) {
            open_popup(shell);
        }
        return Outcome::Continue;
    }
    apply_key(key, &mut shell.state);
    settle(shell)
}

/// One key while the import popup is top: the import state owns it.
fn import_key(shell: &mut Shell, key: &str) -> Outcome {
    match handle_key(&mut shell.import, key) {
        "close" => {
            // Abandoned (Esc/`i`/Ctrl+C): buffer and status untouched.
            // `handle_key` already closed the state; the shell drops the
            // interlock and re-arms the first-run choice when the library
            // is still empty — a library that landed themes stays in the
            // editor, where `t` opens them.
            shell.import_open = false;
            shell.import_scroll = None;
            setup_after_import(&mut shell.state);
            Outcome::Continue
        }
        "confirm" => confirm_popup(shell),
        // moved/toggled/collapsed/expanded/selected-all/cleared/ignored:
        // the popup repaints, and every editor colour key is inert.
        _ => Outcome::Continue,
    }
}

/// Confirm the popup: each plan through the injected writer.
///
/// Successes and skips fold into the status line; per-theme failures join
/// the session notes for the exit report (the picker-notes rule — no
/// modal in the popup). Closing never touches the buffer. An empty
/// confirm (nothing selected) returns an empty status — the shell leaves
/// the status line untouched and stays open.
fn confirm_popup(shell: &mut Shell) -> Outcome {
    let existing: Vec<String> = shell
        .state
        .library
        .as_ref()
        .map(|library| library.names())
        .unwrap_or_default();
    let lower: Vec<&str> = existing.iter().map(String::as_str).collect();
    let outcome: ConfirmOutcome = match shell.import_writer.as_mut() {
        Some(writer) => confirm_import(&mut shell.import, &lower, &mut **writer),
        // No writer (harness, bare shell): the plans report as landed
        // and nothing is written — the `_write_imports` None shape.
        None => {
            let mut noop = |_: &str, _: &huebox_core::Slots, _: &str| Ok(());
            confirm_import(&mut shell.import, &lower, &mut noop)
        }
    };
    if outcome.imported.is_empty() && outcome.status.is_empty() {
        return Outcome::Continue;
    }
    if !outcome.status.is_empty() {
        shell.state.status = outcome.status;
    }
    shell.import_notes.extend(outcome.notes);
    shell.import_open = false;
    shell.import_scroll = None;
    setup_after_import(&mut shell.state);
    Outcome::Continue
}

/// Apply keys in order; the last outcome wins. Test helper only.
#[allow(dead_code)]
pub fn drive(shell: &mut Shell, keys: &[&str]) -> Outcome {
    let mut outcome = Outcome::Continue;
    for key in keys {
        outcome = route_key(shell, key);
        if outcome == Outcome::Quit {
            break;
        }
    }
    outcome
}

#[cfg(test)]
mod tests {
    use super::*;
    use huebox_core::Slots;
    use huebox_editor::{ImportLibrary, Library, WriteOutcome};
    use std::cell::RefCell;
    use std::rc::Rc;

    /// A library that remembers what it created: `names` reflects writes,
    /// so the first-run choice stays down once an import lands.
    struct MemLibrary {
        names: Rc<RefCell<Vec<String>>>,
    }

    impl Library for MemLibrary {
        fn names(&self) -> Vec<String> {
            self.names.borrow().clone()
        }
        fn load(&self, _name: &str) -> Option<(Slots, String)> {
            None
        }
        fn create(&self, name: &str, _slots: &Slots, _force: bool) -> Result<String, String> {
            self.names.borrow_mut().push(name.to_owned());
            Ok(format!("/tmp/{name}.toml"))
        }
    }

    struct FakeImportLibrary {
        entries: Vec<(String, String)>,
    }

    impl ImportLibrary for FakeImportLibrary {
        fn entries(&self, _provider: &str) -> Vec<(String, String)> {
            self.entries.clone()
        }
        fn read(&self, _provider: &str, path: &str) -> Option<Slots> {
            let mut slots = Slots::default();
            slots.set("background", "#101014");
            let _ = path;
            Some(slots)
        }
        fn format_of(&self, provider: &str) -> String {
            provider.to_owned()
        }
    }

    fn test_shell() -> Shell {
        let write = || -> huebox_editor::WriteFn { Box::new(|_, _, _| Ok(WriteOutcome::ok())) };
        let state = huebox_editor::EditorState::new(Slots::default(), write());
        let import = huebox_editor::ImportState::new(
            vec!["ghostty".to_owned()],
            Some(Box::new(FakeImportLibrary {
                entries: vec![("Ember".to_owned(), "/tmp/ember".to_owned())],
            })),
        );
        Shell::new(state, import, None)
    }

    #[test]
    fn editor_keys_reach_apply_key() {
        let mut shell = test_shell();
        assert_eq!(shell.screen(), Screen::Editor);
        let sel = shell.state.sel;
        route_key(&mut shell, "right");
        assert_ne!(shell.state.sel, sel);
        route_key(&mut shell, "f");
        assert_eq!(shell.state.status, "step size x5");
    }

    #[test]
    fn quit_path_is_two_armed_when_dirty() {
        let mut shell = test_shell();
        route_key(&mut shell, "x"); // value step moves MISSING grey: dirty
        assert!(shell.state.dirty());
        assert_eq!(route_key(&mut shell, "esc"), Outcome::Continue);
        assert!(shell.state.armed);
        assert_eq!(route_key(&mut shell, "esc"), Outcome::Quit);
    }

    #[test]
    fn clean_esc_quits_at_once() {
        let mut shell = test_shell();
        assert_eq!(route_key(&mut shell, "esc"), Outcome::Quit);
        assert!(shell.state.quit);
    }

    #[test]
    fn i_opens_import_from_editor() {
        let mut shell = test_shell();
        route_key(&mut shell, "i");
        assert!(shell.import_open);
        assert_eq!(shell.screen(), Screen::Import);
    }

    #[test]
    fn editor_colour_keys_inert_behind_import() {
        let mut shell = test_shell();
        route_key(&mut shell, "i");
        let before = shell.state.slots.clone();
        route_key(&mut shell, "w");
        route_key(&mut shell, "\x13");
        assert_eq!(shell.state.slots, before);
        assert!(shell.import_open);
    }

    #[test]
    fn import_toggle_and_confirm_flow() {
        let mut shell = test_shell();
        // Writer and library share the listing: a landed theme keeps the
        // first-run choice down and the status line standing.
        let names = Rc::new(RefCell::new(Vec::new()));
        shell.state.library = Some(Box::new(MemLibrary {
            names: names.clone(),
        }));
        let landed = names.clone();
        shell.import_writer = Some(Box::new(
            move |name: &str, _: &Slots, _: &str| -> Result<(), String> {
                landed.borrow_mut().push(name.to_owned());
                Ok(())
            },
        ));
        route_key(&mut shell, "i");
        // Cursor parks on the first row; space toggles it.
        assert_eq!(route_key(&mut shell, "space"), Outcome::Continue);
        assert_eq!(route_key(&mut shell, "\r"), Outcome::Continue);
        assert!(!shell.import_open);
        assert!(shell.state.setup.is_none());
        assert!(shell.state.status.contains("imported 1 theme"));
        assert_eq!(shell.import_notes, Vec::<String>::new());
    }

    #[test]
    fn import_esc_abandons_with_status_untouched() {
        let mut shell = test_shell();
        route_key(&mut shell, "i");
        route_key(&mut shell, "space");
        shell.state.status = "keep me".to_owned();
        route_key(&mut shell, "esc");
        assert!(!shell.import_open);
        assert_eq!(shell.state.status, "keep me");
    }

    #[test]
    fn empty_confirm_stays_open() {
        let mut shell = test_shell();
        route_key(&mut shell, "i");
        route_key(&mut shell, "\r"); // nothing selected
        assert!(shell.import_open);
        assert!(shell.state.status.is_empty());
    }

    #[test]
    fn setup_owns_surface_and_i_routes_through_it() {
        let mut shell = test_shell();
        shell.state.setup = Some(0);
        assert_eq!(shell.screen(), Screen::Setup);
        let before = shell.state.slots.clone();
        route_key(&mut shell, "w");
        assert_eq!(shell.state.slots, before);
        // `n` chooses the naming field, not a colour edit.
        route_key(&mut shell, "n");
        assert!(shell.state.setup_name.is_some());
    }

    #[test]
    fn setup_import_choice_opens_popup_after_dismiss() {
        let mut shell = test_shell();
        shell.state.setup = Some(0);
        route_key(&mut shell, "\r"); // import choice
        assert!(shell.state.setup.is_none());
        assert!(shell.import_open);
        assert_eq!(shell.screen(), Screen::Import);
    }

    #[test]
    fn picker_guards_quit_and_colour_keys() {
        let mut shell = test_shell();
        shell.state.overlay = Some(vec!["a".to_owned(), "b".to_owned()]);
        shell.state.overlay_index = 0;
        assert_eq!(shell.screen(), Screen::Themes);
        let before = shell.state.slots.clone();
        route_key(&mut shell, "w");
        assert_eq!(shell.state.slots, before);
        // `i` behind the picker never stacks a popup.
        route_key(&mut shell, "i");
        assert!(!shell.import_open);
        route_key(&mut shell, "down");
        assert_eq!(shell.state.overlay_index, 1);
        route_key(&mut shell, "esc");
        assert!(shell.state.overlay.is_none());
    }

    #[test]
    fn overrides_owns_surface() {
        let mut shell = test_shell();
        shell.state.overrides = Some(huebox_editor::OverridesState {
            main: "/tmp/ghostty".to_owned(),
            keys: vec!["palette".to_owned()],
        });
        shell.state.overrides_index = 1;
        assert_eq!(shell.screen(), Screen::Overrides);
        let before = shell.state.slots.clone();
        route_key(&mut shell, "w");
        assert_eq!(shell.state.slots, before);
        route_key(&mut shell, "n"); // keep
        assert!(shell.state.overrides.is_none());
    }

    #[test]
    fn precedence_is_import_setup_overrides_picker() {
        let mut shell = test_shell();
        shell.state.setup = Some(0);
        shell.state.overlay = Some(vec!["a".to_owned()]);
        shell.import_open = true;
        assert_eq!(shell.screen(), Screen::Import);
        shell.import_open = false;
        assert_eq!(shell.screen(), Screen::Setup);
        shell.state.setup = None;
        shell.state.overrides = Some(huebox_editor::OverridesState {
            main: "m".to_owned(),
            keys: vec!["k".to_owned()],
        });
        assert_eq!(shell.screen(), Screen::Overrides);
        shell.state.overrides = None;
        assert_eq!(shell.screen(), Screen::Themes);
        shell.state.overlay = None;
        assert_eq!(shell.screen(), Screen::Editor);
    }
}

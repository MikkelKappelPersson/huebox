//! The mouse: an alternative way to point at something on the frame.
//!
//! Ports `on_click` / `_scroll_picker` (§4.3.2): a click resolves through
//! the same row-announced hits plus `slot_at`, then goes through the
//! `apply_key` path — never directly at a colour or a frame. Three things
//! are deliberately absent, as in the Python shell:
//!
//! * Chrome clicks do nothing: clicking nothing is not an error and does
//!   not move the selection.
//! * The wheel does nothing in the editor: only the picker and the import
//!   list scroll, because only they have a window.
//! * Nothing hovers or highlights on its own: the `>` mark already says
//!   where the selection is.
//!
//! Coordinates are frame-relative; dialog origins recompute live from the
//! cached layout, exactly as the popup screens recompute theirs — no
//! stored geometry drifts from the paint.

use huebox_editor::{
    click_overrides, click_setup, click_slot, click_theme, slot_at, wheel as import_wheel,
};

use crate::app::{PopupKind, Shell};
use crate::router::{route_key, settle, Outcome, Screen};

/// A left click at frame-relative (`x`, `y`).
pub fn click_at(shell: &mut Shell, x: usize, y: usize) -> Outcome {
    match shell.screen() {
        Screen::Import => click_import(shell, x, y),
        Screen::Setup | Screen::Themes | Screen::Overrides => click_dialog(shell, x, y),
        Screen::Editor => {
            // A swatch or an interface cell: selection, not an edit —
            // no undo, no status, no arm. Chrome: not an error.
            if let Some(slot) = slot_at(&shell.hits, x, y) {
                click_slot(&mut shell.state, slot);
            }
            Outcome::Continue
        }
    }
}

/// A click while the import popup is top: a theme row toggles, a group
/// title collapses/expands — the mouse spellings of space and arrows.
fn click_import(shell: &mut Shell, x: usize, y: usize) -> Outcome {
    let Some(layout) = shell.popup.as_ref() else {
        return Outcome::Continue;
    };
    if layout.kind != PopupKind::Import {
        return Outcome::Continue;
    }
    let (ox, oy) = (layout.ox, layout.oy);
    let (Some(lx), Some(ly)) = (x.checked_sub(ox + 1), y.checked_sub(oy + 1)) else {
        return Outcome::Continue;
    };
    if lx >= layout.w {
        return Outcome::Continue;
    }
    let id = layout.import_ids.get(ly).and_then(|id| id.clone());
    // A theme row: the mouse spelling of cursor-move + toggle. Group
    // and chrome rows hold still — a group toggles only through its key.
    if let Some((provider, display)) = id {
        if let Some(index) = shell
            .import
            .theme_ids
            .get(&provider)
            .and_then(|ids| ids.iter().position(|id| id.1 == display))
        {
            huebox_editor::click_row(&mut shell.import, &provider, index);
        }
    }
    Outcome::Continue
}

/// A click while a small dialog is top: a choice row confirms through the
/// one key surface, so a click and Enter cannot diverge.
fn click_dialog(shell: &mut Shell, x: usize, y: usize) -> Outcome {
    let Some(layout) = shell.popup.clone() else {
        return Outcome::Continue;
    };
    let (Some(lx), Some(ly)) = (x.checked_sub(layout.ox + 1), y.checked_sub(layout.oy + 1)) else {
        return Outcome::Continue;
    };
    if lx >= layout.w {
        return Outcome::Continue;
    }
    let target = slot_at(&layout.hits, lx, ly);
    let Some(target) = target else {
        return Outcome::Continue; // border/chrome: selects nothing
    };
    match layout.kind {
        PopupKind::Setup => click_setup(&mut shell.state, target),
        PopupKind::Themes => click_theme(&mut shell.state, target),
        PopupKind::Overrides => click_overrides(&mut shell.state, target),
        PopupKind::Import => {}
    }
    // `click_*` already confirmed through `apply_key`; settle routes the
    // outcome (quit, or the import popup after a setup choice).
    settle(shell)
}

/// A wheel detent: `delta > 0` scrolls down, `< 0` scrolls up.
///
/// Only the picker and the import list move — the editor frame itself
/// does not scroll, so a wheel notch never reads as having moved the
/// colours. Either small dialog swallows the wheel; the editor with no
/// picker up holds still.
pub fn wheel(shell: &mut Shell, delta: i32) -> Outcome {
    match shell.screen() {
        Screen::Import => {
            import_wheel(&mut shell.import, delta);
            Outcome::Continue
        }
        Screen::Themes => {
            let key = if delta > 0 { "down" } else { "up" };
            route_key(shell, key)
        }
        Screen::Editor | Screen::Setup | Screen::Overrides => Outcome::Continue,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use huebox_core::Slots;
    use huebox_editor::{ImportLibrary, WriteOutcome};

    struct FakeImportLibrary;

    impl ImportLibrary for FakeImportLibrary {
        fn entries(&self, provider: &str) -> Vec<(String, String)> {
            if provider == "ghostty" {
                vec![("Ember".to_owned(), "/tmp/ember".to_owned())]
            } else {
                vec![]
            }
        }
        fn read(&self, _provider: &str, _path: &str) -> Option<Slots> {
            None
        }
        fn format_of(&self, provider: &str) -> String {
            provider.to_owned()
        }
    }

    fn test_shell() -> Shell {
        let mut slots = Slots::default();
        slots.set("background", "#101014");
        slots.set("foreground", "#e6e6ea");
        let write = || -> huebox_editor::WriteFn { Box::new(|_, _, _| Ok(WriteOutcome::ok())) };
        let state = huebox_editor::EditorState::new(slots, write());
        let import = huebox_editor::ImportState::new(
            vec!["ghostty".to_owned()],
            Some(Box::new(FakeImportLibrary)),
        );
        let mut shell = Shell::new(state, import, None);
        shell.set_size(100, 30);
        shell.refresh();
        shell
    }

    #[test]
    fn click_selects_slot_through_hits() {
        let mut shell = test_shell();
        assert!(!shell.hits.is_empty());
        let hit = shell.hits[0];
        let before = shell.state.sel;
        click_at(&mut shell, hit.x0, hit.y);
        assert_eq!(shell.state.sel, hit.slot);
        // Clicking the same slot holds still.
        click_at(&mut shell, hit.x0, hit.y);
        assert_eq!(shell.state.sel, hit.slot);
        let _ = before;
    }

    #[test]
    fn chrome_click_selects_nothing() {
        let mut shell = test_shell();
        let before = shell.state.sel;
        click_at(&mut shell, 0, 0);
        assert_eq!(shell.state.sel, before);
    }

    #[test]
    fn wheel_ignored_in_editor_moves_picker() {
        let mut shell = test_shell();
        let before = shell.state.sel;
        wheel(&mut shell, 1);
        wheel(&mut shell, -1);
        assert_eq!(shell.state.sel, before);
        shell.state.overlay = Some(vec!["a".to_owned(), "b".to_owned(), "c".to_owned()]);
        shell.refresh();
        wheel(&mut shell, 1);
        assert_eq!(shell.state.overlay_index, 1);
        wheel(&mut shell, -1);
        assert_eq!(shell.state.overlay_index, 0);
    }

    #[test]
    fn dialog_click_confirms_choice() {
        let mut shell = test_shell();
        shell.state.setup = Some(1);
        shell.refresh();
        let layout = shell.popup.clone().expect("setup dialog");
        assert_eq!(layout.kind, PopupKind::Setup);
        // `setup_hits` slot 0 is the first choice row.
        let hit = layout
            .hits
            .iter()
            .find(|hit| hit.slot == 0)
            .expect("choice hit");
        click_at(&mut shell, layout.ox + 1 + hit.x0, layout.oy + 1 + hit.y);
        // Chose import: the popup opens after dismiss, modals never stack.
        assert!(shell.state.setup.is_none());
        assert!(shell.import_open);
    }

    #[test]
    fn import_click_toggles_row() {
        let mut shell = test_shell();
        shell.import_open = true;
        shell.refresh();
        let layout = shell.popup.clone().expect("import dialog");
        let row = layout
            .import_ids
            .iter()
            .position(|id| id.is_some())
            .expect("a theme row");
        assert!(shell.import.selected.is_empty());
        click_at(&mut shell, layout.ox + 1, layout.oy + 1 + row);
        assert_eq!(shell.import.selected.len(), 1);
    }
}

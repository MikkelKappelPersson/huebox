//! The mouse: an alternative way to point at something on the frame.
//!
//! Ports `on_click` / `_scroll_picker` plus the `Panel` / button clicks
//! (§4.3.2): a click resolves through the same row-announced hits plus
//! `slot_at`, then goes through the `apply_key` path — never directly at
//! a colour or a frame. Three things are deliberately absent, as in the
//! Python shell:
//!
//! * Chrome clicks do nothing: clicking nothing is not an error and does
//!   not move the selection. Panel borders, titles, air and every other
//!   top/frame row outside hits and buttons is chrome — a border click
//!   routes to no-op, never to a slot.
//! * The wheel does nothing in the editor: only the picker and the import
//!   list scroll, because only they have a window.
//! * Nothing hovers or highlights on its own: the `>` mark already says
//!   where the selection is.
//!
//! Buttons resolve through the same key surface as the keyboard, never
//! direct state edits: `Themes` is exactly what `t` does, `Import`
//! exactly what `i` does (both via [`route_key`]), and the import
//! dialog's `Import` / `Cancel` are click-only mirrors of its `Enter` /
//! `Esc` bindings through the same call.
//!
//! Coordinates are frame-relative; dialog origins recompute live from the
//! cached layout, exactly as the popup screens recompute theirs — no
//! stored geometry drifts from the paint.

use crate::editor::{
    click_no_theme, click_no_theme_bundled, click_overrides, click_setup, click_slot, click_theme,
    slot_at, wheel as import_wheel,
};

use crate::app::router::{route_key, settle, Outcome, Screen};
use crate::app::shell::{PopupKind, Shell};

/// A left click at frame-relative (`x`, `y`).
pub fn click_at(shell: &mut Shell, x: usize, y: usize) -> Outcome {
    match shell.screen() {
        Screen::Import => click_import(shell, x, y),
        Screen::NoTheme | Screen::Setup | Screen::Themes | Screen::Overrides => {
            click_dialog(shell, x, y)
        }
        Screen::Editor => click_editor(shell, x, y),
    }
}

/// A click on the editor frame: top buttons first, then slots, else chrome.
///
/// `Themes` is exactly what `t` does and `Import` exactly what `i` does
/// — both through [`route_key`], so the button and the key cannot disagree
/// about what the picker/popup is. Never a direct state edit. Border,
/// title, air and every other chrome cell selects nothing.
fn click_editor(shell: &mut Shell, x: usize, y: usize) -> Outcome {
    if let Some(top) = shell.top.clone() {
        if in_rect(&top.themes_btn, x, y) {
            return route_key(shell, "t");
        }
        if in_rect(&top.import_btn, x, y) {
            return route_key(shell, "i");
        }
    }
    // A live header toggles its block shut/open — the mouse spelling of
    // `E`/`D`/`C`, through the same key surface so click and key cannot
    // disagree. Never a direct state edit.
    if let Some(name) = shell
        .live_headers
        .iter()
        .find(|(hy, x0, x1, _)| *hy as usize == y && (*x0 as usize) <= x && x < (*x1 as usize))
        .map(|(_, _, _, name)| name.clone())
    {
        let key = crate::app::panel::COLLAPSE_KEYS
            .iter()
            .find(|(_, block)| *block == name)
            .map(|(key, _)| *key)
            .unwrap_or("E");
        return route_key(shell, key);
    }
    // A swatch or an interface cell: selection, not an edit —
    // no undo, no status, no arm. Chrome: not an error.
    if let Some(slot) = slot_at(&shell.hits, x, y) {
        click_slot(&mut shell.state, slot);
    }
    Outcome::Continue
}

/// A click while the import popup is top: footer buttons first (click-only
/// mirrors of `Enter` / `Esc` through the same key surface), then a theme
/// row toggles — the mouse spelling of cursor-move + toggle.
fn click_import(shell: &mut Shell, x: usize, y: usize) -> Outcome {
    if let Some(layout) = shell.popup.clone() {
        if layout.kind == PopupKind::Import {
            if let Some(rect) = layout.confirm_btn {
                if in_rect(&rect, x, y) {
                    return route_key(shell, "\r");
                }
            }
            if let Some(rect) = layout.cancel_btn {
                if in_rect(&rect, x, y) {
                    return route_key(shell, "esc");
                }
            }
        }
    }
    let Some(layout) = shell.popup.as_ref() else {
        return Outcome::Continue;
    };
    if layout.kind != PopupKind::Import {
        return Outcome::Continue;
    }
    // (Footer buttons already routed above; what follows is list rows.)
    let (ox, oy) = (layout.ox, layout.oy);
    let (Some(lx), Some(ly)) = (x.checked_sub(ox + 1), y.checked_sub(oy + 1)) else {
        return Outcome::Continue;
    };
    if lx >= layout.w {
        return Outcome::Continue;
    }
    let id = layout.import_ids.get(ly).and_then(|id| id.clone());
    // A group title: the mouse spelling of collapse/expand.
    if id.is_none() {
        if let Some(provider) = layout.import_groups.get(ly).and_then(|group| group.clone()) {
            crate::editor::toggle_group(&mut shell.import, &provider);
        }
        return Outcome::Continue;
    }
    // A theme row: the mouse spelling of cursor-move + toggle.
    if let Some((provider, display)) = id {
        if let Some(index) = shell
            .import
            .theme_ids
            .get(&provider)
            .and_then(|ids| ids.iter().position(|id| id.1 == display))
        {
            crate::editor::click_row(&mut shell.import, &provider, index);
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
        PopupKind::NoTheme => {
            if shell.state.no_theme_bundled.is_some() {
                click_no_theme_bundled(&mut shell.state, target);
            } else {
                click_no_theme(&mut shell.state, target);
            }
        }
        PopupKind::Setup => click_setup(&mut shell.state, target),
        PopupKind::Themes => click_theme(&mut shell.state, target),
        PopupKind::Overrides => click_overrides(&mut shell.state, target),
        PopupKind::Import => {}
    }
    // `click_*` already confirmed through `apply_key`; settle routes the
    // outcome (quit, or the import popup after a setup choice).
    settle(shell)
}

/// Whether frame-relative (`x`, `y`) lands inside `rect`.
fn in_rect(rect: &ratatui::layout::Rect, x: usize, y: usize) -> bool {
    let (rx, ry, rw, rh) = (
        rect.x as usize,
        rect.y as usize,
        rect.width as usize,
        rect.height as usize,
    );
    x >= rx && x < rx + rw && y >= ry && y < ry + rh
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
        Screen::Themes | Screen::NoTheme => {
            let key = if delta > 0 { "down" } else { "up" };
            route_key(shell, key)
        }
        Screen::Editor | Screen::Setup | Screen::Overrides => Outcome::Continue,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::Slots;
    use crate::editor::{ImportLibrary, WriteOutcome};

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
        let write = || -> crate::editor::WriteFn { Box::new(|_, _, _| Ok(WriteOutcome::ok())) };
        let state = crate::editor::EditorState::new(slots, write());
        let import = crate::editor::ImportState::new(
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

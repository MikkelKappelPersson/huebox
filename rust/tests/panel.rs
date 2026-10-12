//! Bordered panels + buttons: headless parity tests.
//!
//! No real tty anywhere: geometry in, buffer out. Panel mounts compose
//! already-built rows (the frame's own slices); these tests pin the
//! chrome — round-border corners, titles, button spans and their clicks —
//! never cell re-rendering. Env toggles run serialised behind one mutex:
//! the toggles are process-global and the harness runs tests in parallel.

use huebox::app::{click_at, route_key, BodyMode, Shell};
use huebox::core::Slots;
use huebox::editor::{EditorState, ImportLibrary, ImportState, Library, WriteOutcome};
use ratatui::style::{Color, Modifier};
use std::sync::Mutex;

static ENV_LOCK: Mutex<()> = Mutex::new(());

fn ramp() -> Slots {
    let mut slots = Slots::default();
    slots.set("background", "#101014");
    slots.set("foreground", "#e6e6ea");
    slots.set("cursor-color", "#e6e6ea");
    slots.set("cursor-text", "#101014");
    slots.set("selection-background", "#2a2a34");
    slots.set("selection-foreground", "#e6e6ea");
    let palette = [
        "#101014", "#a83232", "#3f7a3f", "#a88a3f", "#3f6a8a", "#8a3f6a", "#3f8a8a", "#b0b0b8",
        "#d0d0d8", "#e06c6c", "#6cc06c", "#e0c06c", "#6c9ce0", "#e06c9c", "#6cc0c0", "#f0f0f8",
    ];
    for (i, hex) in palette.iter().enumerate() {
        slots.set(&format!("palette-{i}"), hex);
    }
    slots
}

fn write_ok() -> huebox::editor::WriteFn {
    Box::new(|_, _, _| Ok(WriteOutcome::ok()))
}

struct FakeImportLibrary;

impl ImportLibrary for FakeImportLibrary {
    fn entries(&self, provider: &str) -> Vec<(String, String)> {
        if provider == "ghostty" {
            vec![
                ("Ember".to_owned(), "/tmp/ember".to_owned()),
                ("Dusk".to_owned(), "/tmp/dusk".to_owned()),
            ]
        } else {
            vec![]
        }
    }
    fn read(&self, _provider: &str, _path: &str) -> Option<Slots> {
        Some(ramp())
    }
    fn format_of(&self, provider: &str) -> String {
        provider.to_owned()
    }
}

/// A theme library with two names: `t` opens the picker through it.
struct TwoThemes;

impl Library for TwoThemes {
    fn names(&self) -> Vec<String> {
        vec!["ember".to_owned(), "dusk".to_owned()]
    }
    fn load(&self, _name: &str) -> Option<(Slots, String)> {
        None
    }
    fn create(&self, _name: &str, _slots: &Slots, _force: bool) -> Result<String, String> {
        Err("no".to_owned())
    }
}

fn fresh() -> Shell {
    let state = EditorState::new(ramp(), write_ok());
    let import = ImportState::new(
        vec!["ghostty".to_owned()],
        Some(Box::new(FakeImportLibrary)),
    );
    Shell::new(state, import, None)
}

fn shell_with_library() -> Shell {
    let mut shell = fresh();
    shell.state.library = Some(Box::new(TwoThemes));
    shell.refresh();
    shell
}

fn row_text(buffer: &ratatui::buffer::Buffer, y: u16, width: u16) -> String {
    (0..width)
        .map(|x| buffer[(x, y)].symbol().to_owned())
        .collect::<String>()
}

fn frame_text(buffer: &ratatui::buffer::Buffer, cols: u16, rows: u16) -> String {
    (0..rows)
        .map(|y| row_text(buffer, y, cols))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Stacked body: round-border corners plus `THEME` / `EXAMPLES` /
/// `EDITOR` titles at the rows the layout mounted.
#[test]
fn stacked_panels_show_borders_and_titles() {
    let _guard = ENV_LOCK.lock().unwrap();
    let mut shell = fresh();
    let buffer = shell.snapshot(80, 24);
    assert_eq!(shell.body_mode, BodyMode::Stacked);
    let top = shell.top.clone().expect("compositor top");
    assert_eq!(top.top_screen, 6);
    let stacked = shell.stacked.clone().expect("stacked panels");
    assert!(!stacked.controls.is_empty());
    assert!(!stacked.examples.is_empty());
    let body = frame_text(&buffer, 80, 24);
    for title in ["THEME", "EXAMPLES", "EDITOR"] {
        assert!(body.contains(title), "missing {title}\n{body}");
    }
    // The 80-column info share clips the button labels to its box
    // (Python's Textual squeeze clips harder — accepted deviation);
    // the click test below pins the spans that remain.
    for label in ["Them", "Impo"] {
        assert!(body.contains(label), "missing {label}\n{body}");
    }
    // Round corners, not square ones: the native `border: round`.
    assert!(body.contains('╭'), "{body:?}");
    assert!(body.contains('╮'), "{body:?}");
    assert!(body.contains('╰'), "{body:?}");
    assert!(body.contains('╯'), "{body:?}");
    assert!(!body.contains('┌'), "square corner leaked\n{body}");
    // The controls panel's top-left corner stands where mounted, in the
    // furniture slot on the buffer background.
    let corner = &buffer[(stacked.controls_rect.x, stacked.controls_rect.y)];
    assert_eq!(corner.symbol(), "╭");
    assert_eq!(corner.fg, Color::Rgb(0xd0, 0xd0, 0xd8));
    assert_eq!(corner.bg, Color::Rgb(0x10, 0x10, 0x14));
    // The title wears Textual's `─ TITLE ` shape: the dash is BORDER ink
    // (palette-8, unbolded), the ` THEME ` that follows bold foreground.
    let title_x = stacked.controls_rect.x as usize + 1;
    let title_y = stacked.controls_rect.y;
    let title_cells: Vec<&ratatui::buffer::Cell> = (0..8)
        .map(|i| &buffer[(title_x as u16 + i, title_y)])
        .collect();
    let title_text: String = title_cells.iter().map(|c| c.symbol()).collect::<String>();
    assert_eq!(title_text, "─ THEME ");
    assert_eq!(
        title_cells[0].fg,
        Color::Rgb(0xd0, 0xd0, 0xd8),
        "{:?}",
        title_cells[0]
    );
    assert!(
        !title_cells[0].modifier.contains(Modifier::BOLD),
        "{:?}",
        title_cells[0]
    );
    for cell in &title_cells[1..] {
        assert_eq!(cell.fg, Color::Rgb(0xe6, 0xe6, 0xea), "{cell:?}");
        assert_eq!(cell.bg, Color::Rgb(0x10, 0x10, 0x14), "{cell:?}");
        assert!(cell.modifier.contains(Modifier::BOLD), "{cell:?}");
    }
    let _ = top;
}

/// Side body: the pair stands in bordered panels at 100x30.
#[test]
fn side_panels_stand_bordered_at_100x30() {
    let _guard = ENV_LOCK.lock().unwrap();
    let mut shell = fresh();
    let buffer = shell.snapshot(100, 30);
    assert_eq!(shell.body_mode, BodyMode::Side);
    assert!(shell.top.is_some());
    let side = shell.side.clone().expect("side pair");
    assert!(!side.left.is_empty() && !side.right.is_empty());
    // Adjacent bordered panels: `╮╭` on top, `╯╰` below.
    let body = frame_text(&buffer, 100, 30);
    assert!(body.contains("THEME"), "{body:?}");
    assert!(body.contains("EXAMPLES"), "{body:?}");
    assert!(body.contains("EDITOR"), "{body:?}");
    let left_corner = &buffer[(side.left_rect.x, side.left_rect.y)];
    let right_corner = &buffer[(side.right_rect.x, side.right_rect.y)];
    assert_eq!(left_corner.symbol(), "╭");
    assert_eq!(right_corner.symbol(), "╭");
    assert_eq!(left_corner.fg, Color::Rgb(0xd0, 0xd0, 0xd8));
    assert_eq!(right_corner.fg, Color::Rgb(0xd0, 0xd0, 0xd8));
    // Palette pairs on the left, live blocks on the right — under
    // collapsible headers, whose titles stand in for the frame's own.
    assert!(body.contains("#d0d0d8"), "{body:?}");
    assert!(body.contains("▼ Live diff"), "{body:?}");
    assert!(body.contains("▼ Live code"), "{body:?}");
    assert!(body.contains("▼ Interface text"), "{body:?}");
}

/// Clicking `Themes` is exactly what `t` does, through the same call.
#[test]
fn themes_button_click_opens_picker_like_t() {
    let _guard = ENV_LOCK.lock().unwrap();
    let mut clicked = shell_with_library();
    let _ = clicked.snapshot(80, 24);
    let top = clicked.top.clone().expect("top with buttons");
    // Same action both ways: click == `t`, popup open either way.
    let mut via_key = shell_with_library();
    route_key(&mut via_key, "t");
    assert!(via_key.state.overlay.is_some());
    let (bx, by) = (
        top.themes_btn.x as usize + top.themes_btn.width as usize / 2,
        top.themes_btn.y as usize,
    );
    // The button span wears the selection pair, bold — pinned before the
    // click (the modal dim repaints the base the dialog stands over).
    let buffer = clicked.snapshot(80, 24);
    let cell = &buffer[(bx as u16, by as u16)];
    assert_eq!(cell.fg, Color::Rgb(0xe6, 0xe6, 0xea), "{cell:?}");
    assert_eq!(cell.bg, Color::Rgb(0x2a, 0x2a, 0x34), "{cell:?}");
    assert!(cell.modifier.contains(Modifier::BOLD), "{cell:?}");
    click_at(&mut clicked, bx, by);
    assert!(clicked.state.overlay.is_some());
    assert_eq!(
        clicked.state.overlay_index, via_key.state.overlay_index,
        "button and key disagree about the picker"
    );
}

/// Clicking `Import` is exactly what `i` does, through the same call.
#[test]
fn import_button_click_opens_popup_like_i() {
    let _guard = ENV_LOCK.lock().unwrap();
    let mut clicked = fresh();
    let _ = clicked.snapshot(80, 24);
    let top = clicked.top.clone().expect("top with buttons");
    let (bx, by) = (
        top.import_btn.x as usize + top.import_btn.width as usize / 2,
        top.import_btn.y as usize,
    );
    // Key path first, for the parity assertion.
    let mut via_key = fresh();
    route_key(&mut via_key, "i");
    assert!(via_key.import_open);
    click_at(&mut clicked, bx, by);
    assert!(clicked.import_open);
    assert_eq!(clicked.screen(), via_key.screen());
}

/// Border and title clicks select nothing: chrome is not a control.
#[test]
fn border_click_is_a_noop() {
    let _guard = ENV_LOCK.lock().unwrap();
    let mut shell = fresh();
    let _ = shell.snapshot(80, 24);
    let before = shell.state.sel;
    let stacked = shell.stacked.clone().expect("stacked panels");
    // Top-left corner of each bordered panel (border chrome).
    for rect in [stacked.controls_rect, stacked.examples_rect] {
        click_at(&mut shell, rect.x as usize, rect.y as usize);
        assert_eq!(shell.state.sel, before);
        // Bottom-right corner too.
        click_at(
            &mut shell,
            rect.x as usize + rect.width as usize - 1,
            rect.y as usize + rect.height as usize - 1,
        );
        assert_eq!(shell.state.sel, before);
        // The title text itself.
        click_at(&mut shell, rect.x as usize + 2, rect.y as usize);
        assert_eq!(shell.state.sel, before);
    }
    // The editor panel's border too.
    let top = shell.top.clone().expect("top");
    let editor = ratatui::layout::Rect::new(
        top.editor_x as u16,
        0,
        top.editor_outer as u16,
        top.top_screen as u16,
    );
    click_at(&mut shell, editor.x as usize, editor.y as usize);
    assert_eq!(shell.state.sel, before);
    // Sanity: a real swatch click still selects through the same map.
    assert!(!shell.hits.is_empty());
    let hit = shell.hits[0];
    click_at(&mut shell, hit.x0, hit.y);
    assert_eq!(shell.state.sel, hit.slot);
}

/// Import footer buttons: click-only mirrors of `Enter` / `Esc`.
#[test]
fn import_footer_buttons_mirror_enter_and_esc() {
    let _guard = ENV_LOCK.lock().unwrap();
    // Cancel: click == `Esc`, popup closes, status untouched.
    let mut shell = fresh();
    route_key(&mut shell, "i");
    assert!(shell.import_open);
    shell.state.status = "keep me".to_owned();
    let _ = shell.snapshot(100, 30);
    let layout = shell.popup.clone().expect("import dialog");
    let cancel = layout.cancel_btn.expect("cancel button");
    click_at(
        &mut shell,
        cancel.x as usize + cancel.width as usize / 2,
        cancel.y as usize,
    );
    assert!(!shell.import_open);
    assert_eq!(shell.state.status, "keep me");
    // Empty confirm: click == `Enter`, stays open on the note.
    let mut empty = fresh();
    route_key(&mut empty, "i");
    assert!(empty.import_open);
    let _ = empty.snapshot(100, 30);
    let layout = empty.popup.clone().expect("import dialog");
    let confirm = layout.confirm_btn.expect("confirm button");
    click_at(
        &mut empty,
        confirm.x as usize + confirm.width as usize / 2,
        confirm.y as usize,
    );
    assert!(empty.import_open);
    let mut via_key = fresh();
    route_key(&mut via_key, "i");
    route_key(&mut via_key, "\r");
    assert!(via_key.import_open);
    // Loaded confirm: toggle one theme, then click Import == `Enter`.
    let mut loaded = fresh();
    route_key(&mut loaded, "i");
    assert!(loaded.import_open);
    route_key(&mut loaded, "space");
    assert_eq!(loaded.import.selected.len(), 1);
    let _ = loaded.snapshot(100, 30);
    let layout = loaded.popup.clone().expect("import dialog");
    let confirm = layout.confirm_btn.expect("confirm button");
    click_at(
        &mut loaded,
        confirm.x as usize + confirm.width as usize / 2,
        confirm.y as usize,
    );
    assert!(!loaded.import_open);
    assert!(
        loaded.state.status.contains("imported"),
        "{:?}",
        loaded.state.status
    );
}

/// `HUEBOX_PANELS=0` restores the frameless stack (tests pin bare rows).
#[test]
fn panels_toggle_restores_bare_stack() {
    let _guard = ENV_LOCK.lock().unwrap();
    std::env::set_var("HUEBOX_PANELS", "0");
    let mut shell = fresh();
    let buffer = shell.snapshot(80, 24);
    assert_eq!(shell.body_mode, BodyMode::Bare);
    assert!(shell.top.is_none() && shell.stacked.is_none() && shell.side.is_none());
    let body = frame_text(&buffer, 80, 24);
    assert!(!body.contains("╭"), "panel border leaked\n{body}");
    assert!(!body.contains("THEME"), "{body:?}");
    // Bare titles still stand.
    assert!(body.contains("Palette"), "{body:?}");
    std::env::remove_var("HUEBOX_PANELS");
}

/// `HUEBOX_EDITOR_PANEL=0` / `HUEBOX_TOP=0` keep the bare stack top.
#[test]
fn editor_panel_toggle_keeps_bare_top() {
    let _guard = ENV_LOCK.lock().unwrap();
    for var in ["HUEBOX_EDITOR_PANEL", "HUEBOX_TOP"] {
        std::env::set_var(var, "0");
        let mut shell = fresh();
        let buffer = shell.snapshot(80, 24);
        assert!(shell.top.is_none(), "{var} still mounted a top");
        let body = frame_text(&buffer, 80, 24);
        assert!(!body.contains("EDITOR"), "{var} leaked\n{body}");
        assert!(!body.contains("Themes"), "{var} leaked\n{body}");
        std::env::remove_var(var);
    }
}

/// `HUEBOX_PANEL_PAD=0` mounts touching borders; limits toggle is exact.
#[test]
fn pad_and_limits_toggles_hold() {
    let _guard = ENV_LOCK.lock().unwrap();
    std::env::set_var("HUEBOX_PANEL_PAD", "0");
    let mut shell = fresh();
    let _ = shell.snapshot(80, 24);
    assert_eq!(shell.body_mode, BodyMode::Stacked);
    assert_eq!(shell.stacked.clone().expect("panels").pad, 0);
    std::env::remove_var("HUEBOX_PANEL_PAD");
    // Default on: padded where the window allows it.
    let mut shell = fresh();
    let _ = shell.snapshot(80, 24);
    assert_eq!(shell.stacked.clone().expect("panels").pad, 1);
    // Limits: default clamps to 160x50, `=0` is identity.
    assert_eq!(
        huebox::app::panel::layout_size(200, 60),
        (160, 50),
        "maxima bind by default"
    );
    assert_eq!(
        huebox::app::panel::clamp_panel("controls", 10, 100),
        (42, 50)
    );
    std::env::set_var("HUEBOX_PANEL_LIMITS", "0");
    assert_eq!(huebox::app::panel::layout_size(200, 60), (200, 60));
    std::env::remove_var("HUEBOX_PANEL_LIMITS");
}

/// `E` shuts the examples block: the header flips to `▶`, the body hides,
/// and a second `E` reopens it — the `Live` toggle through the key surface.
#[test]
fn e_collapses_and_reopens_examples() {
    let _guard = ENV_LOCK.lock().unwrap();
    std::env::remove_var("HUEBOX_COLLAPSIBLE");
    let mut shell = fresh();
    let before = frame_text(&shell.snapshot(80, 24), 80, 24);
    assert!(before.contains("▼ Interface text"), "{before:?}");
    assert!(before.contains("The quick brown fox"), "{before:?}");
    route_key(&mut shell, "E");
    assert!(shell.collapsed.contains("examples"));
    let shut = frame_text(&shell.snapshot(80, 24), 80, 24);
    assert!(shut.contains("▶ Interface text"), "{shut:?}");
    assert!(!shut.contains("The quick brown fox"), "{shut:?}");
    route_key(&mut shell, "E");
    assert!(!shell.collapsed.contains("examples"));
    let reopened = frame_text(&shell.snapshot(80, 24), 80, 24);
    assert!(reopened.contains("▼ Interface text"), "{reopened:?}");
    assert!(reopened.contains("The quick brown fox"), "{reopened:?}");
}

/// `D` and `C` shut the diff and the sample behind the same gate.
#[test]
fn d_and_c_collapse_diff_and_sample() {
    let _guard = ENV_LOCK.lock().unwrap();
    std::env::remove_var("HUEBOX_COLLAPSIBLE");
    let mut shell = fresh();
    let _ = shell.snapshot(100, 30);
    route_key(&mut shell, "D");
    route_key(&mut shell, "C");
    assert!(shell.collapsed.contains("diff"));
    assert!(shell.collapsed.contains("sample"));
    let body = frame_text(&shell.snapshot(100, 30), 100, 30);
    assert!(body.contains("▶ Live diff"), "{body:?}");
    assert!(body.contains("▶ Live code"), "{body:?}");
    assert!(body.contains("▼ Interface text"), "{body:?}");
}

/// `HUEBOX_COLLAPSIBLE=0` keeps the frame's bare rows, headers and all.
#[test]
fn collapsible_opt_out_keeps_frame_rows() {
    let _guard = ENV_LOCK.lock().unwrap();
    std::env::set_var("HUEBOX_COLLAPSIBLE", "0");
    let mut shell = fresh();
    let body = frame_text(&shell.snapshot(80, 24), 80, 24);
    assert!(!body.contains("▼ Interface text"), "{body:?}");
    assert!(body.contains("(live buffer:"), "{body:?}");
    route_key(&mut shell, "E");
    assert!(!shell.collapsed.contains("examples"));
    std::env::remove_var("HUEBOX_COLLAPSIBLE");
}

/// Clicking a live header toggles exactly like its key.
#[test]
fn live_header_click_toggles_like_key() {
    let _guard = ENV_LOCK.lock().unwrap();
    std::env::remove_var("HUEBOX_COLLAPSIBLE");
    let mut shell = fresh();
    let _ = shell.snapshot(80, 24);
    let (hy, x0, x1, name) = shell
        .live_headers
        .iter()
        .find(|(_, _, _, n)| n == "examples")
        .cloned()
        .expect("examples header");
    assert!(x0 < x1);
    click_at(&mut shell, (x0 + 1) as usize, hy as usize);
    assert!(shell.collapsed.contains("examples"), "{name}");
    let shut = frame_text(&shell.snapshot(80, 24), 80, 24);
    assert!(shut.contains("▶ Interface text"), "{shut:?}");
}

/// `E` behind the picker is an editor no-op: the picker owns the surface.
#[test]
fn collapse_key_inert_behind_picker() {
    let _guard = ENV_LOCK.lock().unwrap();
    std::env::remove_var("HUEBOX_COLLAPSIBLE");
    let mut shell = shell_with_library();
    route_key(&mut shell, "t");
    assert!(shell.state.overlay.is_some());
    route_key(&mut shell, "E");
    assert!(!shell.collapsed.contains("examples"));
    assert!(shell.state.overlay.is_some());
}

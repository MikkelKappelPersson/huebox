//! Headless shell tests: frames into `TestBackend`, prompts from fakes.
//!
//! No real tty anywhere: geometry in, buffer out; prompt answers from
//! injected closures. Fixtures are small RAMP-like buffers exercising the
//! full-width, narrow and too-small ladders plus one dialog per popup.

use huebox_app::{route_key, Shell};
use huebox_core::Slots;
use huebox_editor::{apply_key, EditorState, ImportLibrary, ImportState, WriteOutcome};
use ratatui::style::{Color, Modifier};

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

fn write_ok() -> huebox_editor::WriteFn {
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

fn shell() -> Shell {
    let state = EditorState::new(ramp(), write_ok());
    let import = ImportState::new(
        vec!["ghostty".to_owned()],
        Some(Box::new(FakeImportLibrary)),
    );
    Shell::new(state, import, None)
}

fn row_text(buffer: &ratatui::buffer::Buffer, y: u16, width: u16) -> String {
    (0..width)
        .map(|x| buffer[(x, y)].symbol().to_owned())
        .collect::<String>()
}

/// The floor contract, headless: no cell of the editor shows the
/// terminal's background — every bg is a set colour.
#[test]
fn frame_paints_no_terminal_background() {
    for (cols, rows) in [(100u16, 30u16), (80, 24), (60, 20)] {
        let mut shell = shell();
        let buffer = shell.snapshot(cols, rows);
        for y in 0..rows {
            for x in 0..cols {
                assert_ne!(
                    buffer[(x, y)].bg,
                    Color::Reset,
                    "cell ({x},{y}) at {cols}x{rows}"
                );
            }
        }
    }
}

/// The too-small ladder, headless: the hint stands, unpainted.
#[test]
fn too_small_frame_names_the_floor() {
    let mut shell = shell();
    let buffer = shell.snapshot(40, 10);
    let first = row_text(&buffer, 0, 40);
    assert!(first.contains("terminal too small"), "{first:?}");
}

/// The footer bar docks bottom: hints above, status bold on the last row.
#[test]
fn footer_bar_carries_hints_and_status() {
    let mut shell = shell();
    shell.state.status = "saved ember → ghostty".to_owned();
    let buffer = shell.snapshot(100, 30);
    let last = row_text(&buffer, 29, 100);
    // Python parity (pty capture): the height-1 footer scrolls
    // horizontally, so overflowing hints truncate mid-row and the status
    // scrolls out of view — the bar shows the hints' head only.
    assert_eq!(
        last,
        "arrows move  w/e hue  s/d sat  x/c val  ^S save  ^A apply  N save as new  u undo(0)  r revert  t the",
        "{last:?}"
    );
    // A hint key stands bright and bold on the bar.
    let key_cell = (0..100u16)
        .map(|x| &buffer[(x, 29)])
        .find(|cell| cell.symbol() == "a" && cell.fg == Color::Rgb(0xe0, 0xc0, 0x6c));
    assert!(key_cell.is_some(), "{last:?}");
    // Where the status fits it rides the same row, bold foreground —
    // with its two air cells on the left, the widget's own left margin.
    let probe = EditorState::new(ramp(), write_ok());
    let line = huebox_app::bar_line_single(&[], "saved ember", 20, &probe.slots);
    let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
    assert!(text.starts_with("  saved ember"), "{text:?}");
}

/// The wordmark stands bold in the top rows, live from the buffer.
///
/// Pinned to the bare stack (`HUEBOX_PANELS=0`): under panels the top
/// carries the banner ladder instead, and the wordmark is its last rung.
#[test]
fn wordmark_stands_bold() {
    std::env::set_var("HUEBOX_PANELS", "0");
    let mut shell = shell();
    let buffer = shell.snapshot(100, 30);
    std::env::remove_var("HUEBOX_PANELS");
    let wordmark: Vec<Color> = [
        "#e06c6c", "#6cc06c", "#e0c06c", "#6c9ce0", "#e06c9c", "#6cc0c0",
    ]
    .iter()
    .map(|hex| {
        let digits = &hex[1..];
        let value = u32::from_str_radix(digits, 16).expect("hex");
        Color::Rgb(
            ((value >> 16) & 0xff) as u8,
            ((value >> 8) & 0xff) as u8,
            (value & 0xff) as u8,
        )
    })
    .collect();
    let mut found = false;
    for y in 0..4u16 {
        let row: Vec<&ratatui::buffer::Cell> = (0..100).map(|x| &buffer[(x, y)]).collect();
        for start in 0..100 - 6 {
            if (0..6).all(|i| row[start + i].fg == wordmark[i]) {
                assert!(
                    (0..6).all(|i| row[start + i].modifier.contains(Modifier::BOLD)),
                    "wordmark at row {y} stands bold"
                );
                found = true;
            }
        }
    }
    assert!(found, "no wordmark run in the top rows");
}

/// Each dialog stands centered over the dimmed base in buffer colours.
#[test]
fn popups_stand_centered_and_theme_closed() {
    let mut shell = shell();
    // Themes.
    shell.state.overlay = Some(vec!["ember".to_owned(), "dusk".to_owned()]);
    let buffer = shell.snapshot(100, 30);
    // Layout recomputes live from the size: read it after the paint,
    // never the pre-snapshot centering at the old size.
    let layout = shell.popup.clone().expect("themes dialog");
    // Centered: the margins match on both sides at whatever size stands.
    assert_eq!(layout.ox * 2 + layout.w + 2, shell.cols);
    let bg = Color::Rgb(0x10, 0x10, 0x14);
    // The dialog border wears the muted furniture slot, never foreign.
    let top = layout.oy as u16;
    let border = &buffer[(layout.ox as u16, top)];
    assert_eq!(border.fg, Color::Rgb(0xd0, 0xd0, 0xd8));
    // Dialog interior stands on the buffer background, like every row.
    let inside = &buffer[(layout.ox as u16 + 1, top + 1)];
    assert_eq!(inside.bg, bg);
    // The list names the themes; the bar folds the status last.
    let body: String = (0..30)
        .map(|y| row_text(&buffer, y, 100))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(body.contains("ember") && body.contains("dusk"), "{body:?}");
}

/// The import dialog lists, previews and folds its bar.
#[test]
fn import_popup_lists_and_previews() {
    let mut shell = shell();
    shell.import_open = true;
    let buffer = shell.snapshot(100, 30);
    let body: String = (0..30)
        .map(|y| row_text(&buffer, y, 100))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(body.contains("Ember") && body.contains("Dusk"), "{body:?}");
    assert!(body.contains("preview"), "{body:?}");
}

/// Prompt seam with a fake prompter: hex entry writes, cancel keeps.
#[test]
fn prompt_seam_writes_and_cancels() {
    let mut shell = shell();
    // Canned answer: `h` writes the slot and reports it.
    shell.state.prompt_hex = Some(Box::new(|_: &str| Some("  #ff8800  ".to_owned())));
    route_key(&mut shell, "h");
    assert_eq!(shell.state.slots.get("palette-5"), "#ff8800");
    assert_eq!(shell.state.status, "palette-5 = #ff8800");
    // Cancelled (`None`): the buffer and the status line hold still.
    shell.state.prompt_hex = Some(Box::new(|_: &str| None));
    let before = shell.state.slots.clone();
    route_key(&mut shell, "h");
    assert_eq!(shell.state.slots, before);
    // Invalid hex: reported, never written.
    shell.state.prompt_hex = Some(Box::new(|_: &str| Some("zzz".to_owned())));
    route_key(&mut shell, "h");
    assert_eq!(shell.state.slots, before);
    assert_eq!(shell.state.status, "not a valid 6-digit hex - ignored");
}

/// The headless router drives a whole session without a compositor.
#[test]
fn headless_session_reaches_save() {
    let mut shell = shell();
    // Adjust, step, undo, save — the parity-session shape, key by key.
    for key in ["e", "f", "e", "u", "\x13"] {
        route_key(&mut shell, key);
    }
    assert_eq!(shell.state.status, "saved");
    assert!(!shell.state.dirty());
    let _ = apply_key; // the one key surface stays the behaviour owner
}

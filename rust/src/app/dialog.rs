//! Popups over a dimmed base: geometry, dim, and the import list.
//!
//! Ports the four `*Screen` popups' shared shape: a centered dialog area
//! over the editor, a real blend toward the buffer background — never an
//! opaque fill that hides the session the dialog belongs to, and never
//! stacked (the router owns the surface one screen at a time).
//!
//! The setup/themes/overrides bodies are [`editor`] cell rows
//! (`theme_lines` / `setup_lines` / `overrides_lines` with `footer=false`);
//! the import list's rows live here, since behaviour-only
//! [`crate::editor::import`] owns its cursor and selection but none of its
//! paint. Every dialog carries its folded bar in-flow (pairs plus status
//! last, via [`crate::app::footer`]).

use crate::core::Slots;
use crate::editor::import::ThemeId;
use crate::editor::{
    cursor_id, preview_slots, ImportState, OVERRIDES_HINTS, SETUP_HINTS, SETUP_NAME_HINTS,
    THEME_HINTS,
};
use crate::render::chrome::{chrome, chrome_bold, title, CHROME_MUTED};
use crate::render::layout::{clip_row, row_visible};
use crate::render::{slot_hex, Cell, Row};
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::widgets::{Block, BorderType, Borders};

use crate::app::cells::hex_color;

/// The import bar's pairs: cursor keys, not colour keys.
pub const IMPORT_HINTS: [(&str, &str); 6] = [
    ("space", "toggle"),
    ("a", "all visible"),
    ("n", "clear"),
    ("arrows", "move"),
    ("Enter", "import"),
    ("Esc", "cancel"),
];

/// Dialog content widths, mirroring the `*Screen.DIALOG_W` clamps.
pub const NO_THEME_DIALOG_W: usize = 48;
/// Dialog content widths, mirroring the `*Screen.DIALOG_W` clamps.
pub const SETUP_DIALOG_W: usize = 44;
/// Dialog content widths, mirroring the `*Screen.DIALOG_W` clamps.
pub const THEMES_DIALOG_W: usize = 44;
/// Dialog content widths, mirroring the `*Screen.DIALOG_W` clamps.
pub const OVERRIDES_DIALOG_W: usize = 60;
/// The import dialog is the full-screen kind: list left, live preview
/// right, footer inside one dialog (`ImportScreen.DIALOG_W`).
pub const IMPORT_DIALOG_W: usize = 100;
/// The import list's own column: provider titles plus theme names, narrow
/// enough to leave the preview its palette at the floor.
pub const IMPORT_LEFT_W: usize = 34;
/// The narrowest honest dialog: below this even choice rows clip.
pub const DIALOG_MIN_W: usize = 24;
/// The tallest honest list: past this the list windows instead of growing.
pub const THEMES_DIALOG_MAX_H: usize = 18;
/// The import dialog content's tallest honest height: body plus footer
/// inside one dialog (`ImportScreen.DIALOG_MAX_H`).
pub const IMPORT_DIALOG_MAX_H: usize = 28;

/// Clamp a dialog content width to the screen: never past the edges,
/// never below the honest minimum.
pub fn clamp_width(want: usize, cols: usize) -> usize {
    clamp_width_min(want, cols, DIALOG_MIN_W)
}

/// Clamp with an explicit floor (tests pin the shared shape).
pub fn clamp_width_min(want: usize, cols: usize, min: usize) -> usize {
    cols.saturating_sub(2).clamp(min.min(cols), want.min(cols))
}

/// A `w`×`h` dialog centered in `area` — the origin clicks recompute.
pub fn popup_rect(area: Rect, w: u16, h: u16) -> Rect {
    let x = area.x + area.width.saturating_sub(w) / 2;
    let y = area.y + area.height.saturating_sub(h) / 2;
    Rect {
        x,
        y,
        width: w.min(area.width),
        height: h.min(area.height),
    }
}

/// The modal dim, measured: Textual's `ModalScreen` keeps
/// `$background at 60%` over the frame, so every backdrop cell moves
/// three-fifths of the way toward the background slot — `out = (2*cell +
/// 3*background) / 5`, truncated per channel. Captures pin it: `ff0000`→
/// `6f090c`, `e6e6ea`→`656569`, `808080`→`3c3c3f`, `3f7a3f`→`223a25`.
fn blend_channel(cell: u8, toward: u8) -> u8 {
    ((cell as u32 * 2 + toward as u32 * 3) / 5) as u8
}

/// Parse `"#rrggbb"` into channels; `None` when unset/unparseable.
fn channels(hex: &str) -> Option<(u8, u8, u8)> {
    let digits = hex.trim_start_matches('#');
    if digits.len() == 6 {
        if let Ok(value) = u32::from_str_radix(digits, 16) {
            return Some((
                ((value >> 16) & 0xff) as u8,
                ((value >> 8) & 0xff) as u8,
                (value & 0xff) as u8,
            ));
        }
    }
    None
}

/// One dimmed colour: three-fifths toward the background slot.
fn dim_rgb(cell: (u8, u8, u8), bg: (u8, u8, u8)) -> ratatui::style::Color {
    ratatui::style::Color::Rgb(
        blend_channel(cell.0, bg.0),
        blend_channel(cell.1, bg.1),
        blend_channel(cell.2, bg.2),
    )
}

/// Dim the painted base in place: every cell of `area` moves toward the
/// buffer's background slot by the modal factor — fg and bg alike, so
/// swatches dim with their text and borders dim with the frame.
///
/// Cells with no background stay untouched (below the size floor the
/// hint row is deliberately unstyled); cells with no foreground take the
/// foreground slot whole — exactly what the compositor resolves `auto`
/// ink to under its translucent overlay. Bold and friends survive: only
/// the two colours move. The dialog paints after this, so its own chrome
/// stands full-bright above the dim.
pub fn dim_buffer(buf: &mut ratatui::buffer::Buffer, area: Rect, slots: &Slots) {
    let bg_hex = slot_hex(slots, "background");
    let fg_hex = slot_hex(slots, "foreground");
    let (Some(bg), Some(fg_fill)) = (channels(bg_hex), channels(fg_hex)) else {
        return;
    };
    for y in area.y..area.y.saturating_add(area.height) {
        for x in area.x..area.x.saturating_add(area.width) {
            let Some(cell) = buf.cell_mut((x, y)) else {
                continue;
            };
            let bg_reset = matches!(cell.bg, ratatui::style::Color::Reset);
            if bg_reset {
                continue;
            }
            if let ratatui::style::Color::Rgb(r, g, b) = cell.bg {
                cell.bg = dim_rgb((r, g, b), bg);
            }
            match cell.fg {
                ratatui::style::Color::Rgb(r, g, b) => {
                    cell.fg = dim_rgb((r, g, b), bg);
                }
                _ => {
                    cell.fg = ratatui::style::Color::Rgb(fg_fill.0, fg_fill.1, fg_fill.2);
                }
            }
        }
    }
}

/// The dialog chrome: a bordered box in the buffer background with a
/// muted border — the frame's own furniture slot, never a foreign fill.
/// Round borders: the Ratatui spelling of Textual's `border: round`.
pub fn dialog_block(slots: &Slots) -> Block<'static> {
    Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::new().fg(hex_color(slot_hex(slots, "palette-8"))))
        .style(Style::new().bg(hex_color(slot_hex(slots, "background"))))
}

/// One list-column row plus what stands behind it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ImportColRow {
    /// Painted cells for the row, at the list width.
    pub row: Row,
    /// The theme id under the row (`None` on titles).
    pub id: Option<ThemeId>,
    /// The provider a title row names (`None` on theme rows).
    pub group: Option<String>,
}

/// The import popup's list column: theme rows with per-row toggle boxes
/// (`▐X▌`) under their provider titles — windowed to the body height,
/// footer excluded.
pub struct ImportView {
    /// The window: exactly `body_h` rows at the list width.
    pub rows: Vec<Row>,
    /// Row-aligned ids for clicks (`None` on title rows).
    pub ids: Vec<Option<ThemeId>>,
    /// Row-aligned title providers (`None` on theme rows).
    pub groups: Vec<Option<String>>,
}

/// The cursor wash: the block-cursor pair, not the theme's selection
/// slots — fixed ink whatever the buffer holds.
pub const IMPORT_CURSOR_BG: &str = "#0178d4";
/// `auto 87%` over the wash: the cursor name's ink, equally fixed.
pub const IMPORT_CURSOR_FG: &str = "#ddedf9";

/// The whole list column, unwindowed: one title row per non-empty
/// provider, then its theme rows while expanded.
pub fn import_column(st: &ImportState) -> Vec<ImportColRow> {
    let mut column = vec![];
    for provider in &st.provider_order {
        let ids = st.theme_ids.get(provider).cloned().unwrap_or_default();
        if ids.is_empty() {
            continue;
        }
        column.push(ImportColRow {
            row: vec![],
            id: None,
            group: Some(provider.clone()),
        });
        if !st.expanded.contains(provider) {
            continue;
        }
        for id in &ids {
            column.push(ImportColRow {
                row: vec![],
                id: Some(id.clone()),
                group: None,
            });
        }
    }
    column
}

/// The list column's height: titles plus open rows.
pub fn import_column_height(st: &ImportState) -> usize {
    import_column(st).len()
}

/// The cursor's column row, or `None` standing nowhere (closed group).
pub fn import_cursor_y(st: &ImportState) -> Option<usize> {
    let cursor = cursor_id(st)?;
    import_column(st)
        .iter()
        .position(|item| item.id.as_ref() == Some(&cursor))
}

/// One title row: `▼/▶ provider (N)`, bold foreground on background.
fn import_title_row(provider: &str, count: usize, open: bool, slots: &Slots) -> Row {
    chrome_bold(
        &format!("{} {provider} ({count})", if open { '▼' } else { '▶' }),
        "foreground",
        slots,
    )
}

/// One theme row: the `▐X▌` toggle box, the name field, two tail cells.
///
/// The box marks the toggled set alone — muted until picked, bright once
/// picked — and the cursor never repaints it: a cursor row keeps the muted
/// box unless toggled, the fixed wash carries the cursor.
fn import_theme_row(
    display: &str,
    highlighted: bool,
    selected: bool,
    slots: &Slots,
    name_w: usize,
) -> Row {
    let bg = slot_hex(slots, "background").to_owned();
    let sel_bg = slot_hex(slots, "selection-background").to_owned();
    let sel_fg = slot_hex(slots, "selection-foreground").to_owned();
    let muted = slot_hex(slots, "palette-8").to_owned();
    let fg = slot_hex(slots, "foreground").to_owned();
    let mut row = vec![];
    if highlighted {
        row.push(Cell::new('▐', &sel_bg, IMPORT_CURSOR_BG));
        let mut x = Cell::new('X', if selected { &sel_fg } else { &muted }, &sel_bg);
        if selected {
            x = x.bolded();
        }
        row.push(x);
        row.push(Cell::new('▌', &sel_bg, IMPORT_CURSOR_BG));
    } else {
        row.push(Cell::new('▐', &bg, &bg));
        let mut x = Cell::new('X', if selected { &fg } else { &muted }, &bg);
        if selected {
            x = x.bolded();
        }
        row.push(x);
        row.push(Cell::new('▌', &bg, &bg));
    }
    // The name field: one air cell plus the label, clipped with an
    // ellipsis past the width, washed (bold) under the cursor.
    let label_w = name_w.saturating_sub(1);
    let shown: String = {
        let text: String = display.chars().take(label_w).collect();
        if display.chars().count() > label_w && label_w > 1 {
            let mut clipped: String = display.chars().take(label_w - 1).collect();
            clipped.push('…');
            clipped
        } else {
            text
        }
    };
    let mut field = format!(" {shown}");
    while field.chars().count() < name_w {
        field.push(' ');
    }
    if highlighted {
        for ch in field.chars() {
            row.push(Cell::new(ch, IMPORT_CURSOR_FG, IMPORT_CURSOR_BG).bolded());
        }
    } else {
        for ch in field.chars() {
            row.push(Cell::new(ch, &fg, &bg));
        }
    }
    // The tail wears the option ink; the viewport head's override
    // lives in `import_body`, shared with title rows.
    row.push(Cell::new(' ', &fg, &bg));
    row.push(Cell::new(' ', &fg, &bg));
    clip_row(&row, 3 + name_w + 2)
}

/// Build the list column at the list width, windowed to the body height.
///
/// `scroll` is the viewport head in column rows (titles counted); the
/// caller keeps it minimally scrolled to the cursor. Short columns pad
/// with blank dialog-background rows to the full height.
pub fn import_body(
    st: &ImportState,
    left_w: usize,
    scroll: usize,
    body_h: usize,
    slots: &Slots,
) -> ImportView {
    let bg = slot_hex(slots, "background").to_owned();
    let name_w = left_w.saturating_sub(5);
    let column = import_column(st);
    let cursor = cursor_id(st);
    let mut rows = vec![];
    let mut ids = vec![];
    let mut groups = vec![];
    for item in column.iter().skip(scroll).take(body_h) {
        match (&item.id, &item.group) {
            (None, Some(provider)) => {
                let ids_here = st.theme_ids.get(provider).cloned().unwrap_or_default();
                let mut row = import_title_row(
                    provider,
                    ids_here.len(),
                    st.expanded.contains(provider),
                    slots,
                );
                row = clip_row(&row, left_w);
                while row_visible(&row) < left_w {
                    row.push(Cell::new(' ', "", &bg));
                }
                rows.push(row);
                ids.push(None);
                groups.push(Some(provider.clone()));
            }
            (Some(id), None) => {
                let display = st.display.get(id).cloned().unwrap_or_default();
                rows.push(import_theme_row(
                    &display,
                    cursor.as_ref() == Some(id),
                    st.selected.contains(id),
                    slots,
                    name_w,
                ));
                ids.push(Some(id.clone()));
                groups.push(None);
            }
            _ => {}
        }
    }
    while rows.len() < body_h {
        rows.push(crate::render::floor::backdrop(&[], slots, left_w));
        ids.push(None);
        groups.push(None);
    }
    ImportView { rows, ids, groups }
}

/// One scrollbar thumb cell: the glyph plus whether it stands reversed.
///
/// Ports `ScrollBarRender.render_bar` (vertical): fractional head and
/// tail glyphs (`▁`…`▇`) for granularity, full reverse blanks between —
/// the track itself stays transparent, so list content shows around it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ImportThumb {
    /// The glyph (`' '` on full-thumb rows).
    pub ch: char,
    /// The tail (and full rows) stand reversed.
    pub reverse: bool,
}

/// Smooth thumb-end glyphs, head to tail.
const SCROLL_BARS: [char; 8] = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', ' '];

/// The list column's scrollbar overlay, one entry per body row: `None`
/// where the track stays transparent (or no overflow needs a bar).
///
/// `col_h` is the column height (titles counted), `scroll` its viewport
/// head; both come from the same numbers the shell scrolls with, so the
/// bar and the window cannot disagree about where the thumb stands.
pub fn import_scrollbar(col_h: usize, body_h: usize, scroll: usize) -> Vec<Option<ImportThumb>> {
    let mut out = vec![None; body_h];
    if body_h == 0 || col_h <= body_h {
        return out;
    }
    let size = body_h as f64;
    let virtual_size = col_h as f64;
    let window_size = body_h as f64;
    let bar_ratio = virtual_size / size;
    let thumb_size = 1.0f64.max(window_size / bar_ratio);
    let position = (size - thumb_size) * scroll as f64 / (virtual_size - window_size);
    let start = (position * 8.0) as usize;
    let end = start + (thumb_size * 8.0).ceil() as usize;
    let (start_index, start_bar) = (start / 8, start % 8);
    let (end_index, end_bar) = (end / 8, end % 8);
    for (i, slot) in out.iter_mut().enumerate() {
        if (start_index..end_index).contains(&i) {
            *slot = Some(ImportThumb {
                ch: ' ',
                reverse: true,
            });
        }
    }
    if start_index < body_h {
        let glyph = SCROLL_BARS[7 - start_bar];
        if glyph != ' ' {
            out[start_index] = Some(ImportThumb {
                ch: glyph,
                reverse: false,
            });
        }
    }
    if end_index < body_h {
        let glyph = SCROLL_BARS[7 - end_bar];
        if glyph != ' ' {
            out[end_index] = Some(ImportThumb {
                ch: glyph,
                reverse: true,
            });
        }
    }
    out
}

/// Paint one thumb over a content line's last two list cells.
///
/// Content lines arrive one span per cell, so the two cells are two
/// spans; anything else holds still. Ink is the theme's own scrollbar
/// pair — muted thumb on background track, like the widget it mirrors.
pub fn apply_import_thumb(
    line: &mut ratatui::text::Line<'static>,
    thumb: ImportThumb,
    left_w: usize,
    slots: &Slots,
) {
    use ratatui::style::Modifier;
    if line.spans.len() < left_w || left_w < 2 {
        return;
    }
    let fg = hex_color(slot_hex(slots, "palette-8"));
    let bg = hex_color(slot_hex(slots, "background"));
    for span in line.spans.iter_mut().skip(left_w - 2).take(2) {
        span.content = thumb.ch.to_string().into();
        span.style = ratatui::style::Style::new().fg(fg).bg(bg);
        if thumb.reverse {
            span.style = span.style.add_modifier(Modifier::REVERSED);
        }
    }
}

/// The live count: `N selected — Enter imports, Esc cancels`, the note
/// riding along behind it. Plain text for the bar, which paints itself.
pub fn import_count_text(st: &ImportState) -> String {
    let mut line = format!(
        "{} selected — Enter imports, Esc cancels",
        st.selected.len()
    );
    if !st.note.is_empty() {
        line.push_str("  ");
        line.push_str(&st.note);
    }
    line
}

/// The popup's right column for the cursor theme: name, palette plus
/// interface, examples strip, live code — every row on the previewed
/// theme's own background, never the terminal's. `{}` slots preview as
/// one muted row rather than the last theme's.
pub fn import_preview(
    name: &str,
    preview: &Slots,
    editor: &Slots,
    width: usize,
    body_h: usize,
) -> Vec<Row> {
    use crate::render::floor::backdrop;
    use crate::render::palette::palette_interface_rows;
    if slot_hex(preview, "background") == crate::render::MISSING {
        let note = if name.is_empty() {
            chrome("no themes found", CHROME_MUTED, preview)
        } else {
            title(name, preview, "")
        };
        let mut rows = vec![backdrop(&note, preview, width)];
        while rows.len() < body_h {
            rows.push(backdrop(&[], editor, width));
        }
        rows.truncate(body_h);
        return rows;
    }
    let mut rows = vec![title(name, preview, ""), vec![]];
    let combined = palette_interface_rows(preview, None, "  ");
    if !combined.is_empty() && row_visible(&combined[0]) <= width {
        rows.extend(combined);
    } else {
        rows.extend(crate::render::palette::palette_rows(
            preview, None, 8, false, None,
        ));
        rows.push(vec![]);
        rows.extend(crate::render::palette::interface_pair_rows(preview, None));
    }
    rows.push(vec![]);
    rows.extend(crate::render::examples::example_lines(
        preview,
        Some(width),
        "",
    ));
    let mut code = crate::render::sample::sample_lines(preview);
    if code.last().is_some_and(|line| row_visible(line) == 0) {
        code.pop();
    }
    rows.push(vec![]);
    let code_at = rows.len();
    rows.extend(code);
    let mut out: Vec<Row> = rows
        .into_iter()
        .enumerate()
        .map(|(i, line)| {
            if i >= code_at {
                crate::render::sample::pad_code_row(line, preview, width)
            } else {
                backdrop(&line, preview, width)
            }
        })
        .collect();
    while out.len() < body_h {
        out.push(backdrop(&[], preview, width));
    }
    out.truncate(body_h);
    out
}

/// The footer pairs for a setup dialog: naming keys while naming.
pub fn setup_pairs(naming: bool) -> &'static [(&'static str, &'static str)] {
    if naming {
        &SETUP_NAME_HINTS
    } else {
        &SETUP_HINTS
    }
}

/// The footer pairs for a `no theme detected` dialog: bundled keys while picking.
pub fn no_theme_pairs(picking: bool) -> &'static [(&'static str, &'static str)] {
    if picking {
        &crate::editor::NO_THEME_BUNDLED_HINTS
    } else {
        &crate::editor::NO_THEME_HINTS
    }
}

/// The themes dialog footer pairs.
pub fn themes_pairs() -> &'static [(&'static str, &'static str)] {
    &THEME_HINTS
}

/// The overrides dialog footer pairs.
pub fn overrides_pairs() -> &'static [(&'static str, &'static str)] {
    &OVERRIDES_HINTS
}

/// Read-once preview for one id, for tests that pin the cache shape.
pub fn preview_for(st: &mut ImportState, id: &ThemeId) -> Slots {
    preview_slots(st, id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::ImportLibrary;

    struct FakeLibrary {
        entries: Vec<(String, String)>,
    }

    impl ImportLibrary for FakeLibrary {
        fn entries(&self, _provider: &str) -> Vec<(String, String)> {
            self.entries.clone()
        }
        fn read(&self, _provider: &str, _path: &str) -> Option<Slots> {
            None
        }
        fn format_of(&self, provider: &str) -> String {
            provider.to_owned()
        }
    }

    fn slots() -> Slots {
        let mut slots = Slots::default();
        slots.set("background", "#101014");
        slots.set("foreground", "#e6e6ea");
        slots.set("palette-8", "#808080");
        slots.set("selection-background", "#202028");
        slots.set("selection-foreground", "#f0f0f0");
        for name in crate::render::palette::SLOTS.iter().take(16) {
            if *name != "palette-8" {
                slots.set(name, "#202020");
            }
        }
        slots
    }

    fn import_state() -> ImportState {
        ImportState::new(
            vec!["ghostty".to_owned(), "kitty".to_owned()],
            Some(Box::new(FakeLibrary {
                entries: vec![
                    ("Ember".to_owned(), "/tmp/ember".to_owned()),
                    ("Dusk".to_owned(), "/tmp/dusk".to_owned()),
                ],
            })),
        )
    }

    #[test]
    fn width_clamp_keeps_dialog_on_screen() {
        assert_eq!(clamp_width(44, 100), 44);
        assert_eq!(clamp_width(44, 30), 28);
        assert_eq!(clamp_width(60, 40), 38);
        assert_eq!(clamp_width(44, 10), 10);
    }

    #[test]
    fn popup_rect_centers() {
        let area = Rect::new(0, 0, 100, 30);
        let rect = popup_rect(area, 46, 12);
        assert_eq!(rect, Rect::new(27, 9, 46, 12));
        let tiny = Rect::new(0, 0, 20, 10);
        assert_eq!(popup_rect(tiny, 46, 12), Rect::new(0, 0, 20, 10));
    }

    #[test]
    fn import_column_titles_open_groups() {
        let st = import_state();
        // The first provider seeds expanded; the second stays shut.
        let col = import_column(&st);
        assert_eq!(col.len(), 4);
        assert_eq!(col[0].group.as_deref(), Some("ghostty"));
        assert!(col[0].id.is_none());
        assert_eq!(col[1].id.as_ref().map(|id| id.1.as_str()), Some("Ember"));
        assert_eq!(col[2].id.as_ref().map(|id| id.1.as_str()), Some("Dusk"));
        assert_eq!(col[3].group.as_deref(), Some("kitty"));
        assert_eq!(import_column_height(&st), 4);
        assert_eq!(import_cursor_y(&st), Some(1));
    }

    #[test]
    fn import_body_paints_toggle_box_and_wash() {
        let mut st = import_state();
        st.is_open = true;
        crate::editor::toggle(&mut st);
        let slots = slots();
        // No overflow at this height, so no scrollbar paints over tails.
        let view = import_body(&st, IMPORT_LEFT_W, 1, 4, &slots);
        assert_eq!(view.rows.len(), 4);
        assert_eq!(view.rows.len(), view.ids.len());
        assert_eq!(view.rows.len(), view.groups.len());
        let text: String = view.rows[0].iter().map(|cell| cell.ch).collect();
        assert!(text.starts_with("▐X▌ Ember"), "{text:?}");
        assert_eq!(view.rows[0][0].bg, IMPORT_CURSOR_BG);
        assert_eq!(view.rows[0][1].fg, "#f0f0f0");
        assert_eq!(view.rows[0][1].bg, "#202028");
        assert!(view.rows[0][1].bold);
        assert_eq!(view.rows[0][4].fg, IMPORT_CURSOR_FG);
        assert_eq!(view.rows[0][4].bg, IMPORT_CURSOR_BG);
        assert!(view.rows[0][4].bold);
        // The name field runs 29 wide with a two-cell option-ink tail.
        assert_eq!(view.rows[0][32].fg, "#e6e6ea");
        // Clicks resolve through the announced ids, never recomputed.
        assert_eq!(view.ids[0].as_ref().map(|id| id.1.as_str()), Some("Ember"));
        assert_eq!(view.groups[0], None);
        // A plain row wears the option ink with a muted box.
        let plain: String = view.rows[1].iter().map(|cell| cell.ch).collect();
        assert!(plain.starts_with("▐X▌ Dusk"), "{plain:?}");
        assert_eq!(view.rows[1][1].fg, "#808080");
        assert_eq!(view.rows[1][4].fg, "#e6e6ea");
        assert_eq!(view.rows[1][32].fg, "#e6e6ea");
    }

    #[test]
    fn import_scrollbar_pins_thumb_fractionally() {
        // Settled at the top: one reversed blank row, track transparent.
        let top = import_scrollbar(467, 19, 1);
        assert_eq!(top.len(), 19);
        assert_eq!(
            top[0],
            Some(ImportThumb {
                ch: ' ',
                reverse: true
            })
        );
        assert!(top[1..].iter().all(|slot| slot.is_none()));
        // Deep scroll: the one-row thumb splits into two `▁` ends.
        let deep = import_scrollbar(467, 19, 446);
        assert_eq!(deep.len(), 19);
        assert!(deep[..17].iter().all(|slot| slot.is_none()));
        assert_eq!(
            deep[17],
            Some(ImportThumb {
                ch: '▁',
                reverse: false
            })
        );
        assert_eq!(
            deep[18],
            Some(ImportThumb {
                ch: '▁',
                reverse: true
            })
        );
        // No overflow: no bar at all.
        assert!(import_scrollbar(4, 19, 0).iter().all(|slot| slot.is_none()));
    }

    #[test]
    fn import_body_title_row_announces_group() {
        let st = import_state();
        let view = import_body(&st, IMPORT_LEFT_W, 0, 2, &slots());
        let text: String = view.rows[0].iter().map(|cell| cell.ch).collect();
        assert!(text.starts_with("▼ ghostty (2)"), "{text:?}");
        assert!(view.rows[0][0].bold);
        assert_eq!(view.ids[0], None);
        assert_eq!(view.groups[0].as_deref(), Some("ghostty"));
    }

    #[test]
    fn import_count_text_counts_and_carries_note() {
        let mut st = import_state();
        assert_eq!(
            import_count_text(&st),
            "0 selected — Enter imports, Esc cancels"
        );
        crate::editor::toggle(&mut st);
        st.note = "boom".to_owned();
        assert_eq!(
            import_count_text(&st),
            "1 selected — Enter imports, Esc cancels  boom"
        );
    }

    #[test]
    fn import_preview_empty_says_no_themes() {
        let rows = import_preview("", &Slots::default(), &slots(), 20, 3);
        assert_eq!(rows.len(), 3);
        let text: String = rows[0].iter().map(|cell| cell.ch).collect();
        assert!(text.contains("no themes found"), "{text:?}");
        assert_eq!(rows[0][0].bg, crate::render::MISSING);
    }

    #[test]
    fn import_preview_paints_cursor_theme() {
        let slots = slots();
        let mut preview = Slots::default();
        preview.set("background", "#262427");
        preview.set("foreground", "#fcfcfa");
        let rows = import_preview("Ember", &preview, &slots, 40, 6);
        assert_eq!(rows.len(), 6);
        let title: String = rows[0].iter().map(|cell| cell.ch).collect();
        assert!(title.starts_with("Ember"), "{title:?}");
        assert_eq!(rows[0][0].fg, "#fcfcfa");
        assert_eq!(rows[0][0].bg, "#262427");
        assert!(rows[0][0].bold);
    }
}

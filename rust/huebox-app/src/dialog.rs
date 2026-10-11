//! Popups over a dimmed base: geometry, dim, and the import list.
//!
//! Ports the four `*Screen` popups' shared shape: a centered dialog area
//! over the editor, clear plus a dim style — never an opaque fill that
//! hides the session the dialog belongs to, and never stacked (the router
//! owns the surface one screen at a time).
//!
//! The setup/themes/overrides bodies are [`huebox_editor`] cell rows
//! (`theme_lines` / `setup_lines` / `overrides_lines` with `footer=false`);
//! the import list's rows live here, since behaviour-only
//! [`huebox_editor::import`] owns its cursor and selection but none of its
//! paint. Every dialog carries its folded bar in-flow (pairs plus status
//! last, via [`crate::footer`]).
//!
//! Dimming, Ratatui-flavoured: the base is repainted wall-to-wall in the
//! buffer's own background — the same fill every frame row already stands
//! on — so nothing foreign appears behind the dialog. `Clear` is
//! deliberately NOT used: it would expose the terminal's background and
//! break the §8.2 floor the frame owes the reader.

use huebox_core::Slots;
use huebox_editor::import::ThemeId;
use huebox_editor::{
    cursor_id, cursor_slots, preview_slots, visible_rows, ImportState, OVERRIDES_HINTS,
    SETUP_HINTS, SETUP_NAME_HINTS, THEME_HINTS,
};
use huebox_render::chrome::{chrome, title, wordmark_default, CHROME_MUTED};
use huebox_render::layout::clip_row;
use huebox_render::{plain, slot_hex, Row};
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::widgets::{Block, Borders};

use crate::cells::hex_color;

/// The import bar's pairs: cursor keys, not colour keys.
pub const IMPORT_HINTS: [(&str, &str); 6] = [
    ("arrows", "move"),
    ("space", "toggle"),
    ("a", "all"),
    ("n", "clear"),
    ("Enter", "import"),
    ("Esc", "back"),
];

/// Dialog content widths, mirroring the `*Screen.DIALOG_W` clamps.
pub const SETUP_DIALOG_W: usize = 44;
/// Dialog content widths, mirroring the `*Screen.DIALOG_W` clamps.
pub const THEMES_DIALOG_W: usize = 44;
/// Dialog content widths, mirroring the `*Screen.DIALOG_W` clamps.
pub const OVERRIDES_DIALOG_W: usize = 60;
/// The import dialog is the bigger kind: list plus preview plus footer.
pub const IMPORT_DIALOG_W: usize = 60;
/// The narrowest honest dialog: below this even choice rows clip.
pub const DIALOG_MIN_W: usize = 24;
/// The tallest honest list: past this the list windows instead of growing.
pub const THEMES_DIALOG_MAX_H: usize = 18;
/// The import list's own budget: title, groups, preview, counter, note.
pub const IMPORT_DIALOG_MAX_H: usize = 20;

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

/// The dimmed base: the whole area repainted in the buffer background.
///
/// Same fill the frame rows stand on, so the session shows through behind
/// the dialog exactly as the modal dim let it. Returns the block for the
/// caller to render — one place owns the dim.
pub fn dim_block(slots: &Slots) -> Block<'static> {
    Block::default().style(Style::new().bg(hex_color(slot_hex(slots, "background"))))
}

/// The dialog chrome: a bordered box in the buffer background with a
/// muted border — the frame's own furniture slot, never a foreign fill.
pub fn dialog_block(slots: &Slots) -> Block<'static> {
    Block::default()
        .borders(Borders::ALL)
        .border_style(Style::new().fg(hex_color(slot_hex(slots, "palette-8"))))
        .style(Style::new().bg(hex_color(slot_hex(slots, "background"))))
}

/// One import list row plus the theme it stands for (`None` = chrome).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ImportRow {
    /// Painted cells for the row.
    pub row: Row,
    /// The theme id under the cursor/selection, if any.
    pub id: Option<ThemeId>,
}

/// The import popup's content: header, groups, theme rows, preview,
/// counter and note — windowed to `max_rows`, footer excluded.
pub struct ImportView {
    /// Clipped content rows (header … note).
    pub rows: Vec<Row>,
    /// Row-aligned ids for clicks (`None` on group/chrome rows).
    pub ids: Vec<Option<ThemeId>>,
}

fn header_row(word: &str, slots: &Slots) -> Row {
    let mut row = plain("  ");
    row.extend(wordmark_default(slots));
    row.extend(plain("  "));
    row.extend(title(word, slots, ""));
    row
}

/// Build the import list at dialog width, windowed around the cursor.
///
/// Group rows carry the provider plus its selected count; theme rows the
/// `>` cursor and `*` selection marks; the tail carries the counter, the
/// read-once preview swatches for the cursor theme, and the note. Clicks
/// resolve through the row-aligned `ids`, announced here rather than
/// recomputed by the caller.
pub fn import_body(
    st: &mut ImportState,
    cols: usize,
    max_rows: usize,
    status: &str,
    slots: &Slots,
) -> ImportView {
    let mut head = vec![header_row("import", slots), vec![]];
    let cursor = cursor_id(st);
    let mut middle: Vec<ImportRow> = vec![];
    for provider in st.provider_order.clone() {
        let ids = st.theme_ids.get(&provider).cloned().unwrap_or_default();
        let open = st.expanded.contains(&provider);
        let picked = ids.iter().filter(|id| st.selected.contains(*id)).count();
        let mut grow = plain("  ");
        grow.extend(plain(if open { "v " } else { "> " }));
        grow.extend(chrome(&provider, "foreground", slots));
        grow.extend(plain("  "));
        grow.extend(chrome(
            &format!("{picked}/{}", ids.len()),
            CHROME_MUTED,
            slots,
        ));
        middle.push(ImportRow {
            row: grow,
            id: None,
        });
        if !open {
            continue;
        }
        for id in ids.iter() {
            let display = st.display.get(id).cloned().unwrap_or_default();
            let mut line = plain("    ");
            line.extend(plain(if cursor.as_ref() == Some(id) {
                "> "
            } else {
                "  "
            }));
            line.extend(plain(if st.selected.contains(id) { "* " } else { "  " }));
            line.extend(chrome(&display, "foreground", slots));
            middle.push(ImportRow {
                row: line,
                id: Some(id.clone()),
            });
        }
    }
    if middle.is_empty() {
        let mut row = plain("  ");
        row.extend(chrome("no themes found", CHROME_MUTED, slots));
        middle.push(ImportRow { row, id: None });
    }
    // Window the middle around the cursor; groups and chrome never trim.
    let room = max_rows.saturating_sub(head.len() + 3).max(1);
    let flat = visible_rows(st);
    let at = cursor
        .as_ref()
        .and_then(|id| flat.iter().position(|(_, _, row)| row == id))
        .unwrap_or(0);
    let start = at
        .saturating_sub(room / 2)
        .min(middle.len().saturating_sub(room));
    let end = (start + room).min(middle.len());
    let mut rows: Vec<Row> = std::mem::take(&mut head);
    let mut ids: Vec<Option<ThemeId>> = vec![None, None];
    for item in middle.iter().skip(start).take(end - start) {
        rows.push(item.row.clone());
        ids.push(item.id.clone());
    }
    // Counter, preview swatches, note: the tail the window never trims.
    let mut count = plain("  ");
    count.extend(chrome(
        &format!("{} themes", flat.len()),
        CHROME_MUTED,
        slots,
    ));
    rows.push(count);
    ids.push(None);
    let preview = cursor_slots(st);
    let mut prow = plain("  ");
    prow.extend(chrome("preview  ", CHROME_MUTED, slots));
    for name in huebox_render::palette::SLOTS.iter().take(16) {
        prow.push(huebox_render::Cell::new(' ', "", preview.get(name)));
    }
    rows.push(prow);
    ids.push(None);
    let note = if st.note.is_empty() {
        status.to_owned()
    } else {
        st.note.clone()
    };
    if !note.is_empty() {
        let mut nrow = plain("  ");
        nrow.extend(plain(&note));
        rows.push(nrow);
        ids.push(None);
    }
    let rows: Vec<Row> = rows
        .into_iter()
        .map(|row| {
            let mut padded = clip_row(&row, cols);
            let seen = huebox_render::layout::row_visible(&padded);
            let fill = slot_hex(slots, "background").to_owned();
            for cell in &mut padded {
                if cell.bg.is_empty() {
                    cell.bg = fill.clone();
                }
            }
            for _ in seen..cols {
                padded.push(huebox_render::Cell::new(' ', "", &fill));
            }
            padded
        })
        .collect();
    ImportView { rows, ids }
}

/// The footer pairs for a setup dialog: naming keys while naming.
pub fn setup_pairs(naming: bool) -> &'static [(&'static str, &'static str)] {
    if naming {
        &SETUP_NAME_HINTS
    } else {
        &SETUP_HINTS
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
    use huebox_editor::ImportLibrary;

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
        for name in huebox_render::palette::SLOTS.iter().take(16) {
            slots.set(name, "#202020");
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
    fn import_body_marks_cursor_and_selection() {
        let mut st = import_state();
        st.is_open = true;
        let slots = slots();
        huebox_editor::toggle(&mut st);
        let view = import_body(&mut st, 60, 20, "", &slots);
        let text: Vec<String> = view
            .rows
            .iter()
            .map(|row| row.iter().map(|cell| cell.ch).collect())
            .collect();
        assert!(text.iter().any(|line| line.contains("ghostty")));
        assert!(text
            .iter()
            .any(|line| line.contains("> * Ember") || line.contains("> *")));
        assert!(text.iter().any(|line| line.contains("preview")));
        assert_eq!(view.rows.len(), view.ids.len());
        // Clicks resolve through the announced ids, never recomputed.
        let ember = view
            .ids
            .iter()
            .find_map(|id| id.clone())
            .expect("a theme id");
        assert_eq!(ember.1, "Ember");
    }

    #[test]
    fn import_body_empty_library_says_so() {
        let mut st = ImportState::new(vec![], None);
        st.is_open = true;
        let view = import_body(&mut st, 60, 20, "", &slots());
        let text: Vec<String> = view
            .rows
            .iter()
            .map(|row| row.iter().map(|cell| cell.ch).collect())
            .collect();
        assert!(text.iter().any(|line| line.contains("no themes found")));
    }
}

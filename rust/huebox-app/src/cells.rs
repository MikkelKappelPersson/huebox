//! Cells, not ANSI: editor rows → Ratatui spans.
//!
//! Ports research §4: the frame arrives as [`huebox_render`] cell rows
//! (`Cell{ch, fg, bg}`) and this module maps them to `Span::styled` /
//! `Line` / `Paragraph` — built styled, never serialised and reparsed.
//! There is no `from_ansi` anywhere in this crate; a reviewer finding one
//! fails the phase.
//!
//! Paint rules:
//!
//! * `fg ""` leaves the terminal default in effect (exactly what the
//!   Python frame's unpainted runs leave); `bg ""` resolves to the
//!   buffer's own background fill (§8.2 — no column shows the terminal's
//!   background).
//! * Bold comes from the cells themselves ([`Cell::bold`][huebox_render::Cell]),
//!   set everywhere Python wore `BOLD`: titles, the wordmark-adjacent
//!   headers, `>` marks and selected rows/cells, banner faces. Position
//!   restoration remains for the two paints the cell model cannot see:
//!   the status line (built in [`crate::footer`]) and the `huebox`
//!   wordmark — six consecutive cells wearing `palette-9` … `palette-14`
//!   in order, the frame's one ornament.
//! * No DIM anywhere (§8.1 binds): no branch here sets `Modifier::DIM`,
//!   and the test below scans every produced span for it.

use huebox_core::Slots;
use huebox_render::chrome::WORDMARK_SLOTS;
use huebox_render::{slot_hex, Cell, Row};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};

/// `"#rrggbb"` → Ratatui colour; `""`/malformed → `Reset` (unset).
pub fn hex_color(hex: &str) -> Color {
    let digits = hex.trim_start_matches('#');
    if digits.len() == 6 {
        if let Ok(value) = u32::from_str_radix(digits, 16) {
            return Color::Rgb(
                ((value >> 16) & 0xff) as u8,
                ((value >> 8) & 0xff) as u8,
                (value & 0xff) as u8,
            );
        }
    }
    Color::Reset
}

/// One cell as a span. `bg_fill` is the buffer background for `bg ""`.
/// `bold` is positional emphasis (status/wordmark); the cell's own
/// [`Cell::bold`][huebox_render::Cell] flag covers everything else.
///
/// An unpainted foreground paints `Reset` (the terminal default) — never
/// an unset style, which Ratatui would skip and leave whatever stood on
/// the cell before (stale frame, or the modal dim) showing through.
pub fn span_for(cell: &Cell, bg_fill: &str, bold: bool) -> Span<'static> {
    let mut style = Style::new();
    style = style.fg(if cell.fg.is_empty() {
        Color::Reset
    } else {
        hex_color(&cell.fg)
    });
    style = style.bg(if cell.bg.is_empty() {
        hex_color(bg_fill)
    } else {
        hex_color(&cell.bg)
    });
    if bold || cell.bold {
        style = style.add_modifier(Modifier::BOLD);
    }
    Span::styled(cell.ch.to_string(), style)
}

/// One row as a line, with the wordmark run bolded where present.
pub fn line_for(row: &Row, slots: &Slots) -> Line<'static> {
    let bg_fill = slot_hex(slots, "background").to_owned();
    let marks: Vec<String> = WORDMARK_SLOTS
        .iter()
        .map(|slot| slot_hex(slots, slot).to_owned())
        .collect();
    let bolds = wordmark_run(row, &marks);
    let spans: Vec<Span<'static>> = row
        .iter()
        .enumerate()
        .map(|(i, cell)| span_for(cell, &bg_fill, bolds.contains(&i)))
        .collect();
    let mut line = Line::from(spans);
    line.style = Style::new().bg(hex_color(&bg_fill));
    line
}

/// Every body row as lines, owned (`'static`) for the frame cache.
pub fn render_rows(rows: &[Row], slots: &Slots) -> Vec<Line<'static>> {
    rows.iter().map(|row| line_for(row, slots)).collect()
}

/// Cell indexes of the `huebox` wordmark run in `row`, if one stands.
///
/// Six consecutive cells whose foregrounds are the six bright hues in
/// palette order — the one-letter-one-colour ornament, found by position
/// rather than stored emphasis.
fn wordmark_run(row: &Row, marks: &[String]) -> Vec<usize> {
    if row.len() < marks.len() {
        return vec![];
    }
    let mut out = vec![];
    for start in 0..=row.len() - marks.len() {
        if row[start..start + marks.len()]
            .iter()
            .zip(marks.iter())
            .all(|(cell, want)| cell.fg.eq_ignore_ascii_case(want))
        {
            out.extend(start..start + marks.len());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use huebox_core::Slots;

    fn slots() -> Slots {
        let mut slots = Slots::default();
        slots.set("background", "#101014");
        slots.set("foreground", "#e6e6ea");
        let bright = [
            "#e06c6c", "#e0c06c", "#6cc06c", "#6c9ce0", "#e06c9c", "#6cc0c0",
        ];
        for (slot, hex) in WORDMARK_SLOTS.iter().zip(bright.iter()) {
            slots.set(slot, hex);
        }
        slots
    }

    #[test]
    fn hex_shapes() {
        assert_eq!(hex_color("#ff8800"), Color::Rgb(0xff, 0x88, 0x00));
        assert_eq!(hex_color(""), Color::Reset);
        assert_eq!(hex_color("nope"), Color::Reset);
    }

    #[test]
    fn unset_bg_resolves_to_the_fill() {
        let slots = slots();
        let row: Row = huebox_render::plain("hi");
        let line = line_for(&row, &slots);
        for span in &line.spans {
            assert_eq!(span.style.bg, Some(Color::Rgb(0x10, 0x10, 0x14)));
        }
        // Unset fg paints Reset — the terminal default, as in Python.
        assert_eq!(line.spans[0].style.fg, Some(Color::Reset));
    }

    #[test]
    fn cell_bold_flag_drives_the_modifier() {
        let slots = slots();
        let cell = huebox_render::Cell::new('x', "#e6e6ea", "");
        let line = line_for(&vec![cell], &slots);
        assert_eq!(line.spans[0].style.add_modifier, Modifier::empty());
        let cell = huebox_render::Cell::new('x', "#e6e6ea", "").bolded();
        let line = line_for(&vec![cell], &slots);
        assert!(line.spans[0].style.add_modifier.contains(Modifier::BOLD));
    }

    #[test]
    fn wordmark_letters_stand_bold() {
        let slots = slots();
        let mut row = huebox_render::plain("  ");
        row.extend(huebox_render::chrome::wordmark_default(&slots));
        let line = line_for(&row, &slots);
        assert_eq!(line.spans.len(), 8);
        assert_eq!(line.spans[0].style.add_modifier, Modifier::empty());
        for span in &line.spans[2..] {
            assert!(span.style.add_modifier.contains(Modifier::BOLD), "{span:?}");
        }
    }

    #[test]
    fn no_dim_anywhere() {
        let slots = slots();
        let rows = vec![
            huebox_render::painted("key label", "#e0c06c", "#101014"),
            huebox_render::plain("chrome"),
        ];
        for line in render_rows(&rows, &slots) {
            for span in &line.spans {
                assert!(
                    !span.style.add_modifier.contains(Modifier::DIM)
                        && !span.style.sub_modifier.contains(Modifier::DIM),
                    "{span:?}"
                );
            }
        }
    }
}

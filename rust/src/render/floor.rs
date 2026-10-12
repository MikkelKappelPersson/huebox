//! The frame floor: `backdrop` and the too-small hint (§8.2, §15.4).
//!
//! Rust port of `backdrop` / `too_small_frame` in `huebox/render.py`.
//! The editor is a sample of the theme, not a preview beside it: every
//! row stands on the buffer's own background, so none of the terminal's
//! own background survives anywhere in the frame.

use crate::core::Slots;

use crate::render::layout::{clip_row, row_visible, MIN_COLS, MIN_ROWS};
use crate::render::{slot_hex, Cell, Row};

/// One frame row, standing on the buffer's own background (§8.2).
///
/// The row is clipped first and padded after it, measured in display
/// columns, so a row already full of colours reaches the edge exactly —
/// no wider, no short. Spans that paint no background of their own resolve
/// to the fill here; foregrounds are never touched (an unpainted run
/// leaves the terminal default in effect, exactly as the Python frame's
/// `RESET`-then-fill does). Pad cells are unpainted spaces on the fill —
/// the grid harness normalises trailing pad the same way on both sides.
pub fn backdrop(row: &[Cell], slots: &Slots, cols: usize) -> Row {
    let fill = slot_hex(slots, "background").to_owned();
    let mut out = clip_row(row, cols);
    for cell in &mut out {
        if cell.bg.is_empty() {
            cell.bg = fill.clone();
        }
    }
    let seen = row_visible(&out);
    for _ in seen..cols {
        out.push(Cell::new(' ', "", &fill));
    }
    out
}

/// The fallback frame (§15.4): one centred hint, clipped to the width.
///
/// The hint is 31 columns — it must stay shorter than `MIN_COLS`, or the
/// actionable size it names is clipped away exactly when it matters.
/// Unlike [`backdrop`] rows this one is unpainted: below the floor the
/// frame says so instead of drawing a garbled theme.
pub fn too_small_frame(cols: usize) -> Row {
    let hint = format!("terminal too small — need {MIN_COLS}x{MIN_ROWS}");
    let pad = " ".repeat(cols.saturating_sub(hint.chars().count()) / 2);
    let text = format!("{pad}{hint}");
    crate::render::layout::clip(&text, cols)
        .chars()
        .map(|ch| Cell::new(ch, "", ""))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::Slots;
    use crate::render::layout::visible;

    fn slots() -> Slots {
        let mut s = Slots::default();
        s.set("background", "#101014");
        s.set("foreground", "#e6e6ea");
        s
    }

    fn text(row: &[Cell]) -> String {
        row.iter().map(|c| c.ch).collect()
    }

    #[test]
    fn a_row_reaches_exactly_the_width() {
        let s = slots();
        for cells in [
            vec![],
            crate::render::plain("  palette"),
            crate::render::painted("aa", "#ff8800", ""),
        ] {
            for cols in [10, 40, 80, 120] {
                let row = backdrop(&cells, &s, cols);
                assert_eq!(row_visible(&row), cols);
            }
        }
    }

    #[test]
    fn floor_is_the_buffers_own_background() {
        let s = slots();
        let row = backdrop(&crate::render::plain("  palette"), &s, 40);
        assert!(row.iter().all(|c| c.bg == "#101014"));
        // a missing slot paints MISSING in place, like every other widget
        let painted = crate::render::painted("x", crate::render::MISSING, "");
        let row = backdrop(&painted, &Slots::default(), 10);
        assert!(row.iter().all(|c| c.bg == "#808080"));
        assert_eq!(row[0].fg, crate::render::MISSING);
    }

    #[test]
    fn background_is_read_per_call() {
        let dark = {
            let mut s = Slots::default();
            s.set("background", "#111111");
            s
        };
        let light = {
            let mut s = Slots::default();
            s.set("background", "#222222");
            s
        };
        assert_ne!(
            backdrop(&crate::render::plain("x"), &dark, 20),
            backdrop(&crate::render::plain("x"), &light, 20)
        );
    }

    #[test]
    fn blank_and_content_rows_share_the_floor() {
        let s = slots();
        let blank = backdrop(&[], &s, 40);
        assert_eq!(text(&blank), " ".repeat(40));
        assert!(blank.iter().all(|c| c.bg == "#101014" && c.fg.is_empty()));
        // a widget's blank line keeps its own (empty) paint on the floor
        let widget = backdrop(&crate::render::plain("    "), &s, 40);
        assert_eq!(&text(&widget)[..4], "    ");
        assert!(widget.iter().all(|c| c.bg == "#101014"));
    }

    #[test]
    fn widget_colours_never_reach_the_frames_own_columns() {
        // after the content the row is floor again: the padding to the
        // edge belongs to the buffer, not to the last thing painted
        let s = slots();
        let row = backdrop(
            &crate::render::chrome::chrome("aa", "foreground", &s),
            &s,
            30,
        );
        assert_eq!(text(&row).len(), 30);
        assert!(row[2..]
            .iter()
            .all(|c| c.bg == "#101014" && c.fg.is_empty()));
    }

    #[test]
    fn too_small_hint_is_centred_and_short() {
        let row = too_small_frame(80);
        let line = text(&row);
        assert!(line.contains("terminal too small"));
        assert!(visible(&line) <= 80);
        assert!(visible("terminal too small — need 40x12") < MIN_COLS);
        // the hint is centred, never padded: below the floor it is the
        // hint alone, clipped where it must be
        let row = too_small_frame(MIN_COLS - 1);
        assert!(visible(&text(&row)) < MIN_COLS);
        let row = too_small_frame(20);
        assert_eq!(visible(&text(&row)), 20);
    }
}

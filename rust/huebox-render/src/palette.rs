//! The shared swatch-cell family: palette and interface cells (§8).
//!
//! Rust port of `huebox/preview.py`'s cell family (`swatch_cell`,
//! `named_cell`, `palette_rows`, `interface_rows`, the paired forms).
//! One implementation of what a cell says, asked from every layout — the
//! bare frame calls these with its live [`Grid`](crate::grid::Grid)
//! shares, the popup with its own compact shares.

use huebox_core::{Slots, readable_fg};

use crate::{Row, painted, slot_hex};

/// A palette swatch with its hex; the frame drops the hex before it drops
/// a swatch, and gives up cells before width.
pub const CELL_FULL: usize = 13;
/// A palette swatch without its hex.
pub const CELL_MIN: usize = 6;

/// The width of one interface cell: `mark name hex`, the name padded to 21.
pub const NAMED_COL_W: usize = 32;
/// Two-letter abbreviations for the narrow rungs of the width ladder.
pub const NAMED_ABBR: [(&str, &str); 6] = [
    ("background", "BG"),
    ("foreground", "FG"),
    ("cursor-color", "CC"),
    ("cursor-text", "CT"),
    ("selection-background", "SB"),
    ("selection-foreground", "SF"),
];
/// An abbreviated interface cell with its hex, and without.
pub const NAMED_ABBR_W: usize = 13;
/// An abbreviated interface cell without its hex: the floor rung.
pub const NAMED_MIN_W: usize = 4;

/// Canonical slot order: 16 palette entries, then the 6 named slots.
pub const SLOTS: [&str; 22] = [
    "palette-0",
    "palette-1",
    "palette-2",
    "palette-3",
    "palette-4",
    "palette-5",
    "palette-6",
    "palette-7",
    "palette-8",
    "palette-9",
    "palette-10",
    "palette-11",
    "palette-12",
    "palette-13",
    "palette-14",
    "palette-15",
    "background",
    "foreground",
    "cursor-color",
    "cursor-text",
    "selection-background",
    "selection-foreground",
];

/// The 16 palette slots in order.
pub const PALETTE: [&str; 16] = [
    "palette-0",
    "palette-1",
    "palette-2",
    "palette-3",
    "palette-4",
    "palette-5",
    "palette-6",
    "palette-7",
    "palette-8",
    "palette-9",
    "palette-10",
    "palette-11",
    "palette-12",
    "palette-13",
    "palette-14",
    "palette-15",
];

/// The 6 interface slots in order.
pub const NAMED: [&str; 6] = [
    "background",
    "foreground",
    "cursor-color",
    "cursor-text",
    "selection-background",
    "selection-foreground",
];

/// Paired columns for the popup: each column is one pair — foregrounds on
/// the top row, backgrounds below — so `FG` stands over `BG`.
pub const INTERFACE_PAIRS: [[&str; 3]; 2] = [
    ["foreground", "cursor-color", "selection-background"],
    ["background", "cursor-text", "selection-foreground"],
];

/// Pad a label to `width` chars with spaces (left-aligned, like `ljust`).
fn ljust(text: &str, width: usize) -> String {
    let mut out = text.to_owned();
    while out.chars().count() < width {
        out.push(' ');
    }
    out
}

/// One palette swatch: `> 4 #rrggbb` in its own colours.
///
/// `width` pads the swatch past its own cell (the pad rides inside the
/// painted span, so the colour field grows). The text stays left-aligned
/// so the `>` marks line up down the column. `sel` outside 0–15 marks
/// nothing (a read-only preview passes `None`).
pub fn swatch_cell(
    slots: &Slots,
    index: usize,
    sel: Option<usize>,
    show_hex: bool,
    width: Option<usize>,
) -> Row {
    let value = slot_hex(slots, &format!("palette-{index}")).to_owned();
    let mark = if Some(index) == sel { ">" } else { " " };
    let cellw = width.unwrap_or(if show_hex { CELL_FULL } else { CELL_MIN });
    let label = if show_hex {
        format!(" {mark}{index:>2} {value} ")
    } else {
        format!(" {mark}{index:>2}")
    };
    painted(&ljust(&label, cellw), readable_fg(&value), &value)
}

/// The painted width of one interface cell.
pub fn named_cell_width(abbrev: bool, show_hex: bool) -> usize {
    if !abbrev {
        NAMED_COL_W
    } else if show_hex {
        NAMED_ABBR_W
    } else {
        NAMED_MIN_W
    }
}

/// Abbreviation for an interface slot.
pub fn named_abbr(key: &str) -> &str {
    NAMED_ABBR
        .iter()
        .find(|(k, _)| *k == key)
        .map(|(_, a)| *a)
        .unwrap_or(key)
}

/// One interface cell: `mark name hex`, the name padded to 21.
///
/// Same hoist as [`swatch_cell`]. `abbrev` prints the two-letter form and
/// `show_hex` the value — the narrow rungs of the width ladder, asked
/// from the grid, never re-derived here.
pub fn named_cell(
    slots: &Slots,
    key: &str,
    sel: Option<usize>,
    abbrev: bool,
    show_hex: bool,
) -> Row {
    let index = SLOTS.iter().position(|s| *s == key).unwrap_or(usize::MAX);
    let value = slot_hex(slots, key).to_owned();
    let mark = if Some(index) == sel { ">" } else { " " };
    let name = if abbrev {
        named_abbr(key).to_owned()
    } else {
        format!("{key:<21}")
    };
    let label = if show_hex {
        format!(" {mark}{name} {value} ")
    } else {
        format!(" {mark}{name}")
    };
    painted(
        &ljust(&label, named_cell_width(abbrev, show_hex)),
        readable_fg(&value),
        &value,
    )
}

/// The 16 palette slots as rows of swatch cells — the compact form.
///
/// One row per `per_row` slots, each row the plain join of its cells.
/// The default is the compact popup share: eight cells a row, hex hidden,
/// two rows holding all 16 at once in 48 columns. `sel = None` marks
/// nothing.
pub fn palette_rows(
    slots: &Slots,
    sel: Option<usize>,
    per_row: usize,
    show_hex: bool,
    width: Option<usize>,
) -> Vec<Row> {
    (0..16)
        .step_by(per_row.max(1))
        .map(|start| {
            (start..(start + per_row).min(16))
                .flat_map(|i| swatch_cell(slots, i, sel, show_hex, width))
                .collect()
        })
        .collect()
}

/// The interface slots as rows of named cells, two-up by default.
///
/// Cells joined with two spaces — same contract as [`palette_rows`]: no
/// indent, no strip, the caller owns the row's edges.
pub fn interface_rows(
    slots: &Slots,
    sel: Option<usize>,
    per: usize,
    abbrev: bool,
    show_hex: bool,
) -> Vec<Row> {
    (0..NAMED.len())
        .step_by(per.max(1))
        .map(|start| {
            let mut row = Row::new();
            for (n, key) in NAMED[start..(start + per).min(NAMED.len())].iter().enumerate() {
                if n > 0 {
                    row.extend(painted("  ", "", ""));
                }
                row.extend(named_cell(slots, key, sel, abbrev, show_hex));
            }
            row
        })
        .collect()
}

/// The 6 interface slots as two paired rows: `FG CC SB` / `BG CT SF`.
///
/// Same cells as [`interface_rows`] with `abbrev = true, show_hex = false`.
pub fn interface_pair_rows(slots: &Slots, sel: Option<usize>) -> Vec<Row> {
    INTERFACE_PAIRS
        .iter()
        .map(|pair| {
            let mut row = Row::new();
            for (n, key) in pair.iter().enumerate() {
                if n > 0 {
                    row.extend(painted("  ", "", ""));
                }
                row.extend(named_cell(slots, key, sel, true, false));
            }
            row
        })
        .collect()
}

/// Palette plus paired interface on the same two lines.
///
/// Row 0 is palette `0-7` beside `FG CC SB`, row 1 palette `8-15` beside
/// `BG CT SF` — base/bright on the left, one interface pair per column on
/// the right. Joined with `sep`; like both, no indent, no clip, no
/// background — the caller owns the edges and checks the fit.
pub fn palette_interface_rows(slots: &Slots, sel: Option<usize>, sep: &str) -> Vec<Row> {
    let pal = palette_rows(slots, sel, 8, false, None);
    let pairs = interface_pair_rows(slots, sel);
    pal.into_iter()
        .zip(pairs)
        .map(|(mut left, right)| {
            left.extend(painted(sep, "", ""));
            left.extend(right);
            left
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(row: &[Row]) -> Vec<String> {
        row.iter()
            .map(|r| r.iter().map(|c| c.ch).collect())
            .collect()
    }

    #[test]
    fn swatch_marks_selection_and_pads() {
        let s = Slots::default();
        let row = swatch_cell(&s, 4, Some(4), true, None);
        let t: String = row.iter().map(|c| c.ch).collect();
        assert_eq!(t.len(), CELL_FULL);
        assert!(t.starts_with(" > 4 "));
        assert!(row.iter().all(|c| c.bg == crate::MISSING));
        let row = swatch_cell(&s, 4, None, false, None);
        let t: String = row.iter().map(|c| c.ch).collect();
        assert_eq!(t.len(), CELL_MIN);
    }

    #[test]
    fn named_widths_follow_the_ladder() {
        assert_eq!(named_cell_width(false, true), NAMED_COL_W);
        assert_eq!(named_cell_width(true, true), NAMED_ABBR_W);
        assert_eq!(named_cell_width(true, false), NAMED_MIN_W);
    }

    #[test]
    fn compact_palette_holds_sixteen_in_two_rows() {
        let s = Slots::default();
        let rows = palette_rows(&s, None, 8, false, None);
        assert_eq!(rows.len(), 2);
        assert!(rows.iter().all(|r| r.len() == 8 * CELL_MIN));
        let _ = text(&rows);
    }

    #[test]
    fn paired_interface_stacks_foregrounds_over_backgrounds() {
        let s = Slots::default();
        let rows = interface_pair_rows(&s, None);
        assert_eq!(rows.len(), 2);
        let t = text(&rows);
        assert!(t[0].contains("FG") && t[0].contains("CC") && t[0].contains("SB"));
        assert!(t[1].contains("BG") && t[1].contains("CT") && t[1].contains("SF"));
    }
}

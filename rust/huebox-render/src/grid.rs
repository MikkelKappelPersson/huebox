//! Grids and layout shares: the bare grid, the side pair, the top.
//!
//! Rust port of the pure layout half of `huebox/editor.py`: [`Grid`] +
//! [`grid_geometry`] + [`move_slot`] (§4.3, §15.2), the side-by-side left
//! panel and right budget, and the unified top's shares. The compositor
//! mounts from these and never re-derives them.

use huebox_core::{hex_to_rgb, readable_fg, rgb_to_hsv};

use crate::chrome::{CHROME_MUTED, chrome, title};
use crate::diff::diff_lines;
use crate::examples::example_lines;
use crate::floor::backdrop;
use crate::hsv::{HSV_FIELD, hsv_axis, hsv_numbers, hsv_readout};
use crate::layout::{clip_row, row_visible, visible};
use crate::palette::{CELL_FULL, CELL_MIN, NAMED, NAMED_ABBR_W, NAMED_COL_W, NAMED_MIN_W, PALETTE, SLOTS, named_cell, swatch_cell};
use crate::sample::sample_lines;
use crate::{Cell, Row, painted, plain, slot_hex};

/// The two colour grids as a frame of `cols` columns draws them (§15).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Grid {
    /// Palette swatches per row.
    pub palette_cols: usize,
    /// Interface cells per row.
    pub named_cols: usize,
    /// A swatch is wide enough to print its hex value.
    pub show_hex: bool,
    /// Column-major pairs (the side layout's left panel): rows are
    /// `(i, i+8)`, not eight across. Bare frames never set it.
    pub vertical: bool,
    /// An interface cell prints BG/FG/… (§15.2).
    pub named_abbrev: bool,
    /// An interface cell prints its hex value.
    pub named_show_hex: bool,
}

/// The `Grid` a frame `cols` wide draws, and the arrows move in (§4.3).
///
/// One ladder for both (§15.2): the frame renders what this says and the
/// keys step through what this says, so a selection can never walk a row
/// the user cannot see. The interface sheds its name width before it sheds
/// a column, the way the palette sheds its hex before its cells. `indent`
/// is the content rows' own air: the rungs leave room for it, so bordered
/// panels ask with `""` and the grid fills the whole content width.
pub fn grid_geometry(cols: usize, indent: &str) -> Grid {
    let air = indent.chars().count();
    let (named_cols, abbrev, nhex) = if air + 2 * NAMED_COL_W + 2 <= cols {
        (2, false, true)
    } else if air + 2 * NAMED_ABBR_W + 2 <= cols {
        (2, true, true)
    } else if air + NAMED_ABBR_W <= cols {
        (1, true, true)
    } else {
        (1, true, false)
    };
    for (per_row, cellw) in [
        (8, CELL_FULL),
        (8, CELL_MIN),
        (4, CELL_FULL),
        (4, CELL_MIN),
        (2, CELL_FULL),
        (1, CELL_FULL),
    ] {
        if air + per_row * cellw <= cols {
            return Grid {
                palette_cols: per_row,
                named_cols,
                show_hex: cellw >= CELL_FULL,
                vertical: false,
                named_abbrev: abbrev,
                named_show_hex: nhex,
            };
        }
    }
    Grid {
        palette_cols: 1,
        named_cols: 1,
        show_hex: true,
        vertical: false,
        named_abbrev: abbrev,
        named_show_hex: nhex,
    }
}

/// A block's last row, for a column that may be wider than the block.
fn bottom_row(base: usize, size: usize, cols: usize, column: usize) -> usize {
    base + (size.div_ceil(cols) - 1) * cols + column.min(cols - 1)
}

/// The grid the side-by-side layout walks (§4.3).
///
/// Two columns throughout: palette pairs `(0, 8)` down to `(7, 15)`, then
/// the named slots two-up. One constructor so the compositor never
/// re-implements the pairing the left panel draws.
pub fn side_grid() -> Grid {
    Grid {
        palette_cols: 2,
        named_cols: 2,
        show_hex: true,
        vertical: true,
        named_abbrev: false,
        named_show_hex: true,
    }
}

/// Column-major pairs: left/right change the column, up/down the row. A
/// vertical key that runs off a block crosses to the other in the same
/// column, while the outer ends stay put.
fn move_vertical(sel: usize, key: &str) -> usize {
    if sel < PALETTE.len() {
        let col = if sel < 8 { 0 } else { 1 };
        let row = sel % 8;
        match key {
            "left" => if col == 1 { sel - 8 } else { sel },
            "right" => if col == 0 { sel + 8 } else { sel },
            "up" => if row > 0 { sel - 1 } else { sel },
            "down" => {
                if row < 7 {
                    sel + 1
                } else {
                    PALETTE.len() + col
                }
            }
            _ => sel,
        }
    } else {
        let pos = sel - PALETTE.len();
        let (col, row) = (pos % 2, pos / 2);
        match key {
            "left" => if col == 1 { sel - 1 } else { sel },
            "right" => if col == 0 { sel + 1 } else { sel },
            "up" => {
                if row > 0 {
                    sel - 2
                } else {
                    7 + col * 8
                }
            }
            "down" => if row < 2 { sel + 2 } else { sel },
            _ => sel,
        }
    }
}

/// Where an arrow lands, in the grid the frame was drawn in (§4.3).
///
/// Left and right stay in the row; up and down stay in the column. The
/// two blocks are stacked, so a vertical key that runs off a block
/// crosses to the other in the same column, while the outer ends of the
/// frame simply stay put.
pub fn move_slot(sel: usize, key: &str, grid: Grid) -> usize {
    if grid.vertical {
        return move_vertical(sel, key);
    }
    let palette = sel < PALETTE.len();
    let base = if palette { 0 } else { PALETTE.len() };
    let size = if palette { PALETTE.len() } else { NAMED.len() };
    let cols = if palette { grid.palette_cols } else { grid.named_cols };
    let (row, column) = ((sel - base) / cols, (sel - base) % cols);
    match key {
        "left" => {
            if column > 0 {
                base + row * cols + column - 1
            } else {
                sel
            }
        }
        "right" => {
            if column + 1 < cols {
                base + row * cols + column + 1
            } else {
                sel
            }
        }
        "up" => {
            if row > 0 {
                base + (row - 1) * cols + column
            } else if palette {
                sel
            } else {
                bottom_row(0, PALETTE.len(), grid.palette_cols, column)
            }
        }
        "down" => {
            if row + 1 < size.div_ceil(cols) {
                base + (row + 1) * cols + column
            } else if palette {
                PALETTE.len() + column.min(grid.named_cols - 1)
            } else {
                sel
            }
        }
        _ => sel,
    }
}

// --------------------------------------------------------------------------
// the side-by-side left panel
// --------------------------------------------------------------------------

/// One side-layout interface cell: `mark name` over the hex, both rows
/// painted in the slot's own colours. The names are what make the bare
/// frame's one-row cells 32 wide; stacking the hex below the name keeps
/// two cells abreast in barely more than one bare cell's width.
pub const SIDE_NAMED_W: usize = 1 + 1 + 20 + 1;
/// The side-by-side layout's left panel: palette pairs over interface
/// pairs, two columns throughout. No baked indent; the panel's padding is
/// the only edge air.
pub const SIDE_LEFT_W: usize = SIDE_NAMED_W + 2 + SIDE_NAMED_W;
/// Title + 8 pairs, a blank, title + 3 pairs over two rows each.
pub const SIDE_LEFT_ROWS: usize = 17;
/// The narrow side-by-side left panel: two abbreviated interface cells
/// abreast instead of two stacked full ones — 14 rows instead of 17.
/// Columns join with one space, not two.
pub const SIDE_LEFT_NARROW_W: usize = NAMED_ABBR_W + 1 + NAMED_ABBR_W;
/// Title + 8 pairs, a blank, title + 3 single-row pairs.
pub const SIDE_LEFT_NARROW_ROWS: usize = 14;
/// The thin left panel: the narrow rows with the hex hidden, so the pair
/// fits 11 columns and stands down to 61 wide.
pub const SIDE_LEFT_THIN_W: usize = NAMED_MIN_W + 1 + NAMED_MIN_W;
/// The right panel's fixed budget: examples title + 3 rows, diff title + 3.
pub const SIDE_EXAMPLES_ROWS: usize = 4;
/// See [`SIDE_EXAMPLES_ROWS`].
pub const SIDE_DIFF_ROWS: usize = 4;

/// One interface cell over two rows: the name, then its hex.
///
/// Both rows wear the slot's own background and foreground, so the cell
/// still reads as one swatch — only the hex moved down a row instead of
/// across 21 columns of padding.
pub fn side_named_cell(slots: &huebox_core::Slots, key: &str, sel: Option<usize>) -> [Row; 2] {
    let index = SLOTS.iter().position(|s| *s == key).unwrap_or(usize::MAX);
    let value = slot_hex(slots, key).to_owned();
    let mark = if Some(index) == sel { ">" } else { " " };
    let ink = readable_fg(&value);
    let namew = NAMED.iter().map(|k| k.len()).max().unwrap_or(0);
    let first = format!(" {mark}{key:<namew$} ");
    let mut second = format!("  {value}");
    while second.chars().count() < first.chars().count() {
        second.push(' ');
    }
    assert_eq!(first.chars().count(), SIDE_NAMED_W, "{first:?}");
    [painted(&first, ink, &value), painted(&second, ink, &value)]
}

/// The left panel's content: palette pairs, then interface pairs.
///
/// Rows are `(0, 8)` down to `(7, 15)`, then `(background, foreground)`
/// and friends — two columns in total. Every row is backed out to the
/// panel's width (§8.2), and `hits` is announced in content coords (`y0` =
/// the content's first row). `narrow` is the squeezed pair; `show_hex =
/// false` the thin pair.
pub fn side_left_rows(
    slots: &huebox_core::Slots,
    sel: Option<usize>,
    mut hits: Option<&mut Vec<crate::frame::Hit>>,
    y0: usize,
    narrow: bool,
    show_hex: bool,
) -> Vec<Row> {
    let (cellw, content_w) = if !narrow {
        (SIDE_NAMED_W, SIDE_LEFT_W)
    } else if show_hex {
        (NAMED_ABBR_W, SIDE_LEFT_NARROW_W)
    } else {
        (NAMED_MIN_W, SIDE_LEFT_THIN_W)
    };
    let content_h = if narrow { SIDE_LEFT_NARROW_ROWS } else { SIDE_LEFT_ROWS };
    let join = if narrow { " " } else { "  " };
    let mut out: Vec<Row> = Vec::new();
    out.push(title("Palette", slots, ""));
    for row in 0..8usize {
        let y = y0 + out.len();
        let cells = [row, row + 8];
        let mut line = Row::new();
        for (n, i) in cells.iter().enumerate() {
            if n > 0 {
                line.extend(painted(join, "", ""));
            }
            line.extend(swatch_cell(slots, *i, sel, show_hex, Some(cellw)));
        }
        out.push(line);
        if let Some(hits) = hits.as_mut() {
            for (column, index) in cells.iter().enumerate() {
                let x0 = column * (cellw + join.len());
                hits.push(crate::frame::Hit {
                    y,
                    x0,
                    x1: x0 + cellw - 1,
                    slot: *index,
                });
            }
        }
    }
    out.push(vec![]);
    out.push(title("Interface", slots, ""));
    for row in 0..3usize {
        let keys = &NAMED[2 * row..2 * row + 2];
        if narrow {
            let y = y0 + out.len();
            let mut line = Row::new();
            for (n, key) in keys.iter().enumerate() {
                if n > 0 {
                    line.extend(painted(join, "", ""));
                }
                line.extend(named_cell(slots, key, sel, true, show_hex));
            }
            out.push(line);
            if let Some(hits) = hits.as_mut() {
                for (column, key) in keys.iter().enumerate() {
                    let x0 = column * (cellw + join.len());
                    hits.push(crate::frame::Hit {
                        y,
                        x0,
                        x1: x0 + cellw - 1,
                        slot: SLOTS.iter().position(|s| s == key).unwrap_or(usize::MAX),
                    });
                }
            }
            continue;
        }
        let pair = [side_named_cell(slots, keys[0], sel), side_named_cell(slots, keys[1], sel)];
        for (first, second) in pair[0].iter().zip(pair[1].iter()) {
            let y = y0 + out.len();
            let mut line = Row::new();
            line.extend(first.clone());
            line.extend(painted("  ", "", ""));
            line.extend(second.clone());
            out.push(line);
            if let Some(hits) = hits.as_mut() {
                for (column, key) in keys.iter().enumerate() {
                    let x0 = column * (cellw + 2);
                    hits.push(crate::frame::Hit {
                        y,
                        x0,
                        x1: x0 + cellw - 1,
                        slot: SLOTS.iter().position(|s| s == key).unwrap_or(usize::MAX),
                    });
                }
            }
        }
    }
    assert_eq!(out.len(), content_h, "{out:?}");
    out.into_iter().map(|line| backdrop(&line, slots, content_w)).collect()
}

/// The right panel's content in exactly `height` rows, or `None`.
///
/// A fixed prototype budget: examples (title + 3), the diff (title + 3)
/// where `height` leaves the sample a title and a line, the sample with
/// the rest, blanks to fill. `None` when even examples + one sample line
/// do not fit — the caller falls back to the stacked layout rather than
/// trimming live blocks. `regions` takes the blocks as `(name, first,
/// count)` in content coords.
pub fn side_live_rows(
    slots: &huebox_core::Slots,
    cols: usize,
    height: usize,
    regions: Option<&mut Vec<(String, usize, usize)>>,
) -> Option<Vec<Row>> {
    if height < SIDE_EXAMPLES_ROWS + 2 {
        return None;
    }
    let mut rows: Vec<Row> = vec![title("examples", slots, "")];
    rows.extend(example_lines(slots, Some(cols), "").into_iter().take(3));
    let mut rest = height - rows.len();
    let mut diff_here = false;
    if rest >= SIDE_DIFF_ROWS + 3 {
        diff_here = true;
        rows.push(title("live diff", slots, ""));
        rows.extend(diff_lines(slots, Some(cols), "").into_iter().take(SIDE_DIFF_ROWS - 1));
        rest = height - rows.len();
    }
    let code = sample_lines(slots);
    // NOTE: no pop of a trailing blank — see `frame.rs`: the Python
    // `code.pop()` never fires, so the trailing newline stays a row.
    let take = (rest - 1).max(1);
    rows.push(title("live code", slots, ""));
    rows.extend(code.into_iter().take(take));
    rows.extend(std::iter::repeat_with(Vec::new).take(height - rows.len()));
    assert_eq!(rows.len(), height, "({}, {})", rows.len(), height);
    if let Some(regions) = regions {
        regions.push(("examples".to_owned(), 0, SIDE_EXAMPLES_ROWS));
        let mut at = SIDE_EXAMPLES_ROWS;
        if diff_here {
            regions.push(("diff".to_owned(), at, SIDE_DIFF_ROWS));
            at += SIDE_DIFF_ROWS;
        }
        regions.push(("sample".to_owned(), at, height - at));
    }
    Some(rows.into_iter().map(|line| backdrop(&line, slots, cols)).collect())
}

// --------------------------------------------------------------------------
// the unified top
// --------------------------------------------------------------------------

/// The side-by-side top block: the wordmark stands in a fixed left column
/// and the theme subject plus the selected readout in the right one.
pub const TOP_LEFT_W: usize = 12;
/// Below this the right column cannot hold even a bare readout, so the
/// frame keeps the stacked header instead.
pub const TOP_MIN_COLS: usize = 60;
/// The logo ladder widths: the logo plus a two-column gap of air.
pub const BANNER_LEFT_W: usize = 52;
/// See [`BANNER_LEFT_W`].
pub const MINI_LEFT_W: usize = 25;
/// The editor panel's own inner air, on top of the box padding: zero — the
/// box's content reaches its borders and the panel's padding is the only
/// edge air.
pub const EDITOR_PAD_X: usize = 0;
/// The selected readout's slot-name field: pinned to the longest slot
/// (`selection-foreground`), so switching the selection never moves the
/// hex, the bars or the specimen beside it.
pub const SELECTED_KEY_W: usize = 20;

/// The selected readout's subject: title, name field, hex.
///
/// Single spaces between the three, no trailing air — the gap before the
/// bars belongs to the bars, not the subject, so a bare row (no bars) is
/// exactly the indent plus the title plus the pinned name field plus the
/// hex: 40 columns stacked.
pub fn selected_core(
    slots: &huebox_core::Slots,
    key: &str,
    value: &str,
    indent: &str,
) -> Row {
    let mut row = plain(indent);
    row.extend(title("selected", slots, ""));
    row.extend(plain(" "));
    row.extend(chrome(&format!("{key:<width$}", width = SELECTED_KEY_W), "foreground", slots));
    row.extend(plain(" "));
    row.extend(chrome(value, CHROME_MUTED, slots));
    row
}

/// A 2-cell colour chip before the slot's name + hex.
///
/// The editor panel's first row: two empty cells in the slot's own
/// background, then the pinned name field and the hex as ordinary chrome.
/// Widths are pinned, so switching the selection never moves anything.
pub fn selected_swatch(slots: &huebox_core::Slots, key: &str, value: &str) -> Row {
    let mut row = painted("  ", "", value);
    row.extend(plain(" "));
    row.extend(chrome(&format!("{key:<width$}", width = SELECTED_KEY_W), "foreground", slots));
    row.extend(plain(" "));
    row.extend(chrome(value, CHROME_MUTED, slots));
    row
}

/// One left-column cell, padded out to [`TOP_LEFT_W`] visible columns.
///
/// The pad is plain air: the column holds the wordmark on the first top
/// row and nothing on the other two, so the theme subject beside it
/// starts in the same column on every row of the block.
pub fn pad_left(text_row: Row) -> Row {
    let mut row = text_row;
    while row_visible(&row) < TOP_LEFT_W {
        row.push(Cell::new(' ', "", ""));
    }
    row
}

/// Pad a row with air to `width` visible columns.
pub fn pad_visible(mut row: Row, width: usize) -> Row {
    while row_visible(&row) < width {
        row.push(Cell::new(' ', "", ""));
    }
    row
}

/// The right column's three rows: theme, selected, specimen.
///
/// The theme subject, the selected slot's prefix plus whatever rung of
/// the hsv ladder fits the right column, and the specimen with the exact
/// reading where it fits. `cols` is the full frame width, so the right
/// column is `cols - TOP_LEFT_W` wide.
pub fn top_right_rows(
    label: &str,
    path: &str,
    slots: &huebox_core::Slots,
    sel: usize,
    cols: usize,
) -> [Row; 3] {
    top_right_panel_rows(label, path, slots, sel, cols - TOP_LEFT_W)
}

/// The readout's metadata column (theme/core/spec).
///
/// Returns `([theme, core, spec], meta_w)` with each row padded to
/// `meta_w` visible columns. The theme keeps its filename (`…` truncates
/// the directory, never the tail).
pub fn top_meta_rows(
    label: &str,
    path: &str,
    slots: &huebox_core::Slots,
    sel: usize,
) -> (Vec<Row>, usize) {
    let key = SLOTS[sel.min(SLOTS.len() - 1)];
    let value = slot_hex(slots, key);
    let core = selected_core(slots, key, value, "");
    let mut spec = plain("  ");
    spec.extend(painted("AaBbCc 0123 ", value, ""));
    let (r, g, b) = hex_to_rgb(value);
    let (h, s, v) = rgb_to_hsv(r, g, b);
    let exact = hsv_numbers(h, s, v);
    spec.extend(plain("   "));
    spec.extend(chrome(&exact, CHROME_MUTED, slots));
    let meta_w = row_visible(&core).max(row_visible(&spec));
    let theme = if !path.is_empty()
        && label.chars().count() + 2 + path.chars().count() <= meta_w
    {
        let mut row = chrome(label, "foreground", slots);
        row.extend(plain("  "));
        row.extend(chrome(path, CHROME_MUTED, slots));
        row
    } else if !path.is_empty() {
        let keep = meta_w.saturating_sub(label.chars().count() + 2 + 1);
        let mut row = chrome(label, "foreground", slots);
        if keep > 0 {
            let tail: String = path.chars().rev().take(keep).collect::<String>().chars().rev().collect();
            row.extend(plain("  "));
            row.extend(chrome(&format!("…{tail}"), CHROME_MUTED, slots));
        }
        row
    } else {
        chrome(label, "foreground", slots)
    };
    let rows = vec![
        pad_visible(clip_row(&theme, meta_w), meta_w),
        pad_visible(core, meta_w),
        pad_visible(clip_row(&spec, meta_w), meta_w),
    ];
    (rows, meta_w)
}

/// Helper for the bordered `editor` panel.
///
/// The selected subject, its hex and the specimen all live in the editor
/// box; metadata keeps only the theme. Returns `(meta_rows, head,
/// meta_w)` where `meta_rows` is `[theme]` and `head` is `[combined]` —
/// chip + name + hex + specimen on one line. With `stacked` the head is
/// `[chip-row, specimen-row]` instead, so a narrow box keeps the readout
/// whole rather than clipping it.
pub fn top_editor_meta(
    label: &str,
    path: &str,
    slots: &huebox_core::Slots,
    sel: usize,
    meta_w: Option<usize>,
    stacked: bool,
) -> (Vec<Row>, Vec<Row>, usize) {
    let key = SLOTS[sel.min(SLOTS.len() - 1)];
    let value = slot_hex(slots, key);
    let core = selected_core(slots, key, value, "");
    let swatch = selected_swatch(slots, key, value);
    // stacked specimen: the head air is unpainted, the text painted
    let mut specimen = plain("  ");
    specimen.extend(painted("AaBbCc 0123", value, ""));
    let meta_w = meta_w.unwrap_or_else(|| row_visible(&core).max(row_visible(&specimen)));
    let theme = if !path.is_empty()
        && label.chars().count() + 2 + path.chars().count() <= meta_w
    {
        let mut row = chrome(label, "foreground", slots);
        row.extend(plain("  "));
        row.extend(chrome(path, CHROME_MUTED, slots));
        row
    } else if !path.is_empty() {
        let keep = meta_w.saturating_sub(label.chars().count() + 2 + 1);
        let mut row = chrome(label, "foreground", slots);
        if keep > 0 {
            let tail: String = path.chars().rev().take(keep).collect::<String>().chars().rev().collect();
            row.extend(plain("  "));
            row.extend(chrome(&format!("…{tail}"), CHROME_MUTED, slots));
        }
        row
    } else {
        chrome(label, "foreground", slots)
    };
    let rows = vec![pad_visible(clip_row(&theme, meta_w), meta_w)];
    if stacked {
        return (rows, vec![swatch, specimen], meta_w);
    }
    let mut combined = swatch;
    combined.extend(plain("   "));
    combined.extend(painted("AaBbCc 0123", "", value));
    (rows, vec![combined], meta_w)
}

/// Whether the readout splits metadata | one HSV axis per row.
///
/// Default on: each of the three rows is `[metadata | one HSV axis]`, so
/// the three bars share one width and the row count does not move.
/// `HUEBOX_TOP_NEW=0` opts back out to the single-line readout.
pub fn top_split_enabled() -> bool {
    std::env::var("HUEBOX_TOP_NEW").as_deref() != Ok("0")
}

/// The unified top's width shares, or `None` for the bare stack.
///
/// `(left_w, info_w, editor_outer, stacked)`: logo art, info column,
/// editor box. One arrangement at every size where it fits — wide head
/// before stacked, wider logo before narrower, and past the tightest
/// share the caller keeps the bare stack. `HUEBOX_TOP=0` pins that stack.
pub fn top_layout(
    width: usize,
    label: &str,
    path: &str,
    slots: &huebox_core::Slots,
    sel: usize,
    pad: usize,
) -> Option<(usize, usize, usize, bool)> {
    if std::env::var("HUEBOX_TOP").as_deref() == Ok("0") {
        return None;
    }
    let info_need = visible(&format!("{label}{}", if path.is_empty() { "" } else { "  " }))
        + if path.is_empty() { 0 } else { visible(path) };
    let info_min = visible(label).max(1);
    let (_, wide_head, _) = top_editor_meta(label, path, slots, sel, None, false);
    let (_, stacked_head, _) = top_editor_meta(label, path, slots, sel, None, true);
    let wide_need = wide_head.iter().map(|r| row_visible(r)).max().unwrap_or(0);
    let stacked_need = stacked_head.iter().map(|r| row_visible(r)).max().unwrap_or(0);
    for (stacked, content_need) in [(false, wide_need), (true, stacked_need)] {
        let outer_min = content_need + 2 + 2 * EDITOR_PAD_X + 2 * pad;
        for left_w in [BANNER_LEFT_W, MINI_LEFT_W, TOP_LEFT_W] {
            let rem = width.checked_sub(left_w)?;
            if rem < info_min + outer_min {
                continue;
            }
            let info_w = info_need.min(rem - outer_min);
            let left = top_left_rows(slots, left_w);
            if row_visible(&left[0]) > left_w && left.len() > 1 {
                continue;
            }
            if left_w == TOP_LEFT_W && left.len() != 1 {
                continue;
            }
            if left_w != TOP_LEFT_W && left.len() == 1 {
                continue;
            }
            return Some((left_w, info_w, rem - info_w, stacked));
        }
    }
    None
}

/// Metadata left, three equal HSV bars right.
///
/// Same three rows [`top_right_panel_rows`] paints, re-partitioned: each
/// row is `[metadata | one HSV axis]`. `None` where even a minimal bar
/// does not fit — the caller keeps the single-line readout.
pub fn top_right_panel_rows_split(
    label: &str,
    path: &str,
    slots: &huebox_core::Slots,
    sel: usize,
    right_w: usize,
) -> Option<[Row; 3]> {
    let key = SLOTS[sel.min(SLOTS.len() - 1)];
    let value = slot_hex(slots, key);
    let (meta, meta_w) = top_meta_rows(label, path, slots, sel);
    let gap = 2;
    let single_chrome = "hue".len() + 1 + HSV_FIELD + 2;
    let bar_w = right_w.checked_sub(meta_w + gap + single_chrome)?;
    if bar_w < 7 {
        return None;
    }
    let axes: Vec<Row> = (0..3).map(|a| hsv_axis(slots, value, a, bar_w)).collect();
    if axes.iter().any(|a| a.is_empty()) {
        return None;
    }
    let mut out: [Row; 3] = [vec![], vec![], vec![]];
    for (i, (left, axis)) in meta.into_iter().zip(axes).enumerate() {
        let mut row = left;
        row.extend(plain(&" ".repeat(gap)));
        row.extend(axis);
        out[i] = row;
    }
    Some(out)
}

/// The right panel's three rows at `right_w` wide.
///
/// The same three rows [`top_right_rows`] always painted, measured against
/// the panel's own width instead of the frame's minus [`TOP_LEFT_W`].
pub fn top_right_panel_rows(
    label: &str,
    path: &str,
    slots: &huebox_core::Slots,
    sel: usize,
    right_w: usize,
) -> [Row; 3] {
    if top_split_enabled() {
        if let Some(split) = top_right_panel_rows_split(label, path, slots, sel, right_w) {
            return split;
        }
    }
    let key = SLOTS[sel.min(SLOTS.len() - 1)];
    let value = slot_hex(slots, key);
    let mut right0 = chrome(label, "foreground", slots);
    if !path.is_empty() {
        let mut tail = plain("  ");
        tail.extend(chrome(path, CHROME_MUTED, slots));
        if row_visible(&right0) + row_visible(&tail) <= right_w {
            right0.extend(tail);
        }
    }
    let core = selected_core(slots, key, value, "");
    let bars = hsv_readout(slots, value, right_w.saturating_sub(row_visible(&core) + 3));
    let mut right1 = core;
    if !bars.is_empty() {
        right1.extend(plain("   "));
        right1.extend(bars);
    }
    // `  AaBbCc 0123 `: the head air is unpainted, the specimen painted
    let mut specimen = plain("  ");
    specimen.extend(painted("AaBbCc 0123 ", value, ""));
    let (r, g, b) = hex_to_rgb(value);
    let (h, s, v) = rgb_to_hsv(r, g, b);
    let exact = hsv_numbers(h, s, v);
    let mut suffix = plain("   ");
    suffix.extend(chrome(&exact, CHROME_MUTED, slots));
    let mut right2 = specimen.clone();
    if row_visible(&specimen) + row_visible(&suffix) <= right_w {
        right2.extend(suffix);
    }
    [right0, right1, right2]
}

/// The left panel's logo at `left_w` wide.
///
/// The same ladder the banner insertion walks — raster where it fits,
/// else mini, else the one-line wordmark — asked at the panel's own
/// width instead of the frame's.
pub fn top_left_rows(slots: &huebox_core::Slots, left_w: usize) -> Vec<Row> {
    let art = crate::banner::banner_lines(slots, Some(left_w), None);
    if !art.is_empty() {
        return art;
    }
    let mark = crate::banner::mini_banner_lines(slots, Some(left_w));
    if !mark.is_empty() {
        return mark;
    }
    vec![{
        let mut row = plain("  ");
        row.extend(crate::chrome::wordmark_default(slots));
        row
    }]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::row_visible;

    fn slots() -> huebox_core::Slots {
        let mut s = huebox_core::Slots::default();
        s.set("background", "#101014");
        s.set("foreground", "#e6e6ea");
        s
    }

    #[test]
    fn ladder_walks_eight_four_two_one() {
        assert_eq!(grid_geometry(120, "  ").palette_cols, 8);
        assert!(grid_geometry(120, "  ").show_hex);
        let narrow = grid_geometry(40, "  ");
        assert!(narrow.palette_cols <= 4);
        assert_eq!(grid_geometry(200, "  ").named_cols, 2);
    }

    #[test]
    fn interface_sheds_names_before_columns() {
        let full = grid_geometry(120, "  ");
        assert!(!full.named_abbrev && full.named_show_hex);
        // 68 columns: two full cells need 2+64+2
        let g68 = grid_geometry(68, "  ");
        assert_eq!((g68.named_cols, g68.named_abbrev, g68.named_show_hex), (2, false, true));
        let below = grid_geometry(67, "  ");
        assert_eq!((below.named_cols, below.named_abbrev), (2, true));
    }

    #[test]
    fn arrows_walk_the_visible_grid() {
        let grid = grid_geometry(120, "  ");
        assert_eq!(move_slot(0, "right", grid), 1);
        assert_eq!(move_slot(0, "left", grid), 0);
        assert_eq!(move_slot(0, "up", grid), 0);
        assert_eq!(move_slot(0, "down", grid), 8);
        assert_eq!(move_slot(15, "down", grid), 17);
        assert_eq!(move_slot(21, "down", grid), 21);
        assert_eq!(move_slot(16, "up", grid), 8);
    }

    #[test]
    fn vertical_pairs_cross_blocks() {
        let grid = side_grid();
        assert!(grid.vertical);
        assert_eq!(move_slot(0, "right", grid), 8);
        assert_eq!(move_slot(8, "left", grid), 0);
        assert_eq!(move_slot(7, "down", grid), 16);
        assert_eq!(move_slot(16, "up", grid), 7);
        assert_eq!(move_slot(21, "down", grid), 21);
    }

    #[test]
    fn side_left_panel_is_seventeen_or_fourteen_rows() {
        let s = slots();
        assert_eq!(side_left_rows(&s, Some(5), None, 0, false, true).len(), SIDE_LEFT_ROWS);
        assert_eq!(
            side_left_rows(&s, Some(5), None, 0, true, true).len(),
            SIDE_LEFT_NARROW_ROWS
        );
        assert_eq!(
            side_left_rows(&s, Some(5), None, 0, true, false).len(),
            SIDE_LEFT_NARROW_ROWS
        );
        for row in side_left_rows(&s, Some(5), None, 0, false, true) {
            assert_eq!(row_visible(&row), SIDE_LEFT_W);
        }
    }

    #[test]
    fn side_live_budget_is_fixed_or_nothing() {
        let s = slots();
        assert!(side_live_rows(&s, 40, SIDE_EXAMPLES_ROWS + 1, None).is_none());
        let rows = side_live_rows(&s, 40, 20, None).unwrap();
        assert_eq!(rows.len(), 20);
    }

    #[test]
    fn selected_subject_is_thirtynine_columns_bare() {
        // indent + title + pinned name field + hex: 39 stacked, which is
        // what keeps the hex on screen at MIN_COLS
        let s = slots();
        let row = selected_core(&s, "palette-5", "#c05c8c", "  ");
        assert_eq!(row_visible(&row), 39);
    }

    #[test]
    fn top_layout_walks_logo_then_head() {
        let s = slots();
        assert!(top_layout(200, "ghostty", "/tmp/x", &s, 5, 0).is_some());
        assert!(top_layout(30, "ghostty", "/tmp/x", &s, 5, 0).is_none());
    }
}

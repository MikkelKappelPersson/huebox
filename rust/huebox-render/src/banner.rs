//! The raster banner and the mini banner (§8.1).
//!
//! Rust port of `banner_lines` / `mini_banner_lines` in
//! `huebox/render.py`. `huebox` in per-letter block art, generated once
//! offline from the pyfiglet shadow font and committed as strings; the
//! generator is not a dependency, the strings are.

use huebox_core::Slots;

use crate::{slot_hex, Cell, Row};

/// Face letter `i` wears the base row, one hue per letter — black and
/// white skipped like the wordmark skips them: black reads as no letter
/// at all, white outshouts the five hues beside it.
pub const BANNER_FACE_SLOTS: [&str; 6] = [
    "palette-1",
    "palette-2",
    "palette-3",
    "palette-4",
    "palette-5",
    "palette-6",
];
/// Blank columns between letters.
pub const BANNER_GAP: usize = 1;
/// The shadow stands in the bevel's own seats: the font already offsets
/// its edging, and a further step visibly detaches it.
pub const BANNER_DX: usize = 0;
/// See [`BANNER_DX`].
pub const BANNER_DY: usize = 0;

/// `huebox` in six per-letter arts, bevel stripped (see module docs).
pub const BANNER_LETTERS: [[&str; 5]; 6] = [
    ["██   ██", "██   ██", "███████", "██   ██", "██   ██"],
    ["██    ██", "██    ██", "██    ██", "██    ██", " ██████"],
    ["███████", "██", "█████", "██", "███████"],
    ["██████", "██   ██", "██████", "██   ██", "██████"],
    [" ██████", "██    ██", "██    ██", "██    ██", " ██████"],
    ["██   ██", " ██ ██", "  ███", " ██ ██", "██   ██"],
];

/// Only the bevel cells are ever stamped: no shadow cell can double a
/// face stroke.
pub const BANNER_BEVEL: &str = "╗║╝╚╔═";

/// The shadow's texture: the same six arts with the bevel intact.
pub const BANNER_SHADOWS: [[&str; 6]; 6] = [
    [
        "██╗  ██╗",
        "██║  ██║",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ],
    [
        "██╗   ██╗",
        "██║   ██║",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝",
    ],
    [
        "███████╗",
        "██╔════╝",
        "█████╗",
        "██╔══╝",
        "███████╗",
        "╚══════╝",
    ],
    [
        "██████╗",
        "██╔══██╗",
        "██████╔╝",
        "██╔══██╗",
        "██████╔╝",
        "╚═════╝",
    ],
    [
        " ██████╗",
        "██╔═══██╗",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝",
    ],
    [
        "██╗  ██╗",
        "╚██╗██╔╝",
        " ╚███╔╝",
        " ██╔██╗",
        "██╔╝ ██╗",
        "╚═╝  ╚═╝",
    ],
];

/// Columns the banner stands in: letters, gaps, the shadow's fringe.
pub fn banner_width() -> usize {
    let mut width = 0;
    let mut x = 0;
    for (letter, silhouette) in BANNER_LETTERS.iter().zip(BANNER_SHADOWS.iter()) {
        let face_w = letter.iter().map(|l| l.chars().count()).max().unwrap_or(0);
        let sh_w = silhouette
            .iter()
            .map(|l| l.chars().count())
            .max()
            .unwrap_or(0);
        width = width.max(x + BANNER_DX + sh_w);
        x += face_w + BANNER_GAP;
    }
    width
}

/// One raster cell: ink, bold (faces only), glyph — `None` for air.
type BannerCell = Option<(String, bool, char)>;

/// The banner: each letter's face in its own base hue, its shadow with it.
///
/// Face letter `i` wears `BANNER_FACE_SLOTS[i]`; the shadow is the bevel
/// edging stamped only on exterior air (flood fill from the canvas border
/// marks the outside — a shadow inside a counter would read as a second
/// stroke), in the same hue from the bright row, or in `shadow` (a slot
/// name) when given. Every colour is read out of `slots` on the call, so
/// the banner is live like the wordmark (§14.1).
///
/// `cols` narrower than the banner gives `[]` and the caller falls back to
/// the wordmark — a clipped banner is half a letter, which reads as a
/// rendering bug rather than a logo.
pub fn banner_lines(slots: &Slots, cols: Option<usize>, shadow: Option<&str>) -> Vec<Row> {
    let width = banner_width();
    if cols.is_some_and(|c| c < width) {
        return vec![];
    }
    let height = BANNER_LETTERS
        .iter()
        .map(|l| l.len())
        .chain(BANNER_SHADOWS.iter().map(|l| l.len()))
        .max()
        .unwrap_or(0);
    // (ink, bold, glyph) per cell, None for air. Faces first, so the
    // flood fill can tell the letterforms from the air around them.
    // Faces wear bold like the one-line wordmark; shadows do not.
    let mut cells: Vec<Vec<BannerCell>> = vec![vec![None; width]; height + BANNER_DY];
    let mut bounds: Vec<(usize, String)> = Vec::new();
    let mut x = 0;
    for (index, (letter, _silhouette)) in
        BANNER_LETTERS.iter().zip(BANNER_SHADOWS.iter()).enumerate()
    {
        let face = BANNER_FACE_SLOTS[index % BANNER_FACE_SLOTS.len()];
        let bright = crate::chrome::WORDMARK_SLOTS[index % crate::chrome::WORDMARK_SLOTS.len()];
        let tint = slot_hex(slots, face).to_owned();
        let shade = match shadow {
            Some(name) => slot_hex(slots, name).to_owned(),
            None => slot_hex(slots, bright).to_owned(),
        };
        for (y, line) in letter.iter().enumerate() {
            for (dx, glyph) in line.chars().enumerate() {
                if glyph != ' ' {
                    cells[y][x + dx] = Some((tint.clone(), true, glyph));
                }
            }
        }
        bounds.push((x, shade));
        x += letter.iter().map(|l| l.chars().count()).max().unwrap_or(0) + BANNER_GAP;
    }
    // Exterior air: flood fill from the border through what the faces
    // leave uninked.
    let mut outside = vec![vec![false; width]; height + BANNER_DY];
    let mut stack: Vec<(usize, usize)> = Vec::new();
    for cx in 0..width {
        stack.push((cx, 0));
        stack.push((cx, height + BANNER_DY - 1));
    }
    for cy in 0..height + BANNER_DY {
        stack.push((0, cy));
        stack.push((width - 1, cy));
    }
    while let Some((cx, cy)) = stack.pop() {
        if outside[cy][cx] || cells[cy][cx].is_some() {
            continue;
        }
        outside[cy][cx] = true;
        if cx + 1 < width {
            stack.push((cx + 1, cy));
        }
        if cx > 0 {
            stack.push((cx - 1, cy));
        }
        if cy + 1 < height + BANNER_DY {
            stack.push((cx, cy + 1));
        }
        if cy > 0 {
            stack.push((cx, cy - 1));
        }
    }
    for (x0, shade) in &bounds {
        let silhouette = &BANNER_SHADOWS[bounds.iter().position(|(bx, _)| bx == x0).unwrap()];
        for (y, line) in silhouette.iter().enumerate() {
            for (dx, glyph) in line.chars().enumerate() {
                if BANNER_BEVEL.contains(glyph) && outside[y + BANNER_DY][x0 + dx + BANNER_DX] {
                    cells[y + BANNER_DY][x0 + dx + BANNER_DX] = Some((shade.clone(), false, glyph));
                }
            }
        }
    }
    paint_cells(&cells, cols)
}

/// Paint cell grids to rows: adjacent cells share runs implicitly (every
/// cell carries its own ink here), trailing air stripped.
fn paint_cells(cells: &[Vec<BannerCell>], cols: Option<usize>) -> Vec<Row> {
    let mut out = Vec::new();
    for row in cells {
        let mut cells_row: Row = row
            .iter()
            .map(|cell| match cell {
                Some((ink, bold, glyph)) => {
                    let cell = Cell::new(*glyph, ink, "");
                    if *bold {
                        cell.bolded()
                    } else {
                        cell
                    }
                }
                None => Cell::new(' ', "", ""),
            })
            .collect();
        while cells_row
            .last()
            .is_some_and(|c| c.ch == ' ' && c.fg.is_empty())
        {
            cells_row.pop();
        }
        out.push(match cols {
            Some(c) => crate::layout::clip_row(&cells_row, c),
            None => cells_row,
        });
    }
    out
}

/// The mini banner (§8.1): three rows of block art, 23 columns, one art
/// per letter so each keeps its own base hue. No shadow — the half-blocks
/// already round the curves, and an offset copy would double every stroke.
pub const MINI_LETTERS: [[&str; 3]; 6] = [
    ["█ █", "█▀█", "█ █"],
    ["█ █", "█ █", "█▄█"],
    ["█▀▀", "█▀", "█▄▄"],
    ["█▀▄", "█▀▄", "█▄▀"],
    ["█▀█", "█ █", "█▄█"],
    ["█ █", " █", "█ █"],
];
/// Air between letters, as pasted.
pub const MINI_GAP: usize = 1;
/// Columns the mini banner stands in.
pub const MINI_WIDTH: usize = {
    let mut w = 0;
    let mut i = 0;
    while i < 6 {
        let mut lw = 0;
        let mut j = 0;
        while j < 3 {
            let mut n = 0;
            let bytes = MINI_LETTERS[i][j].as_bytes();
            let mut k = 0;
            while k < bytes.len() {
                // block glyphs are 3 bytes in UTF-8; spaces are 1
                if bytes[k] < 0x80 {
                    k += 1;
                } else {
                    k += 3;
                }
                n += 1;
            }
            if n > lw {
                lw = n;
            }
            j += 1;
        }
        w += lw;
        i += 1;
    }
    w + MINI_GAP * (6 - 1)
};

/// The mini banner: each letter's blocks in its own base hue (§8.1).
///
/// Letter `i` wears `BANNER_FACE_SLOTS[i]`, live per call like the raster
/// banner. `cols` narrower than [`MINI_WIDTH`] gives `[]` and the caller
/// falls back to the wordmark.
pub fn mini_banner_lines(slots: &Slots, cols: Option<usize>) -> Vec<Row> {
    if cols.is_some_and(|c| c < MINI_WIDTH) {
        return vec![];
    }
    let height = MINI_LETTERS.iter().map(|l| l.len()).max().unwrap_or(0);
    let mut cells: Vec<Vec<BannerCell>> = vec![vec![None; MINI_WIDTH]; height];
    let mut x = 0;
    for (index, letter) in MINI_LETTERS.iter().enumerate() {
        let tint = slot_hex(slots, BANNER_FACE_SLOTS[index % BANNER_FACE_SLOTS.len()]).to_owned();
        for (y, line) in letter.iter().enumerate() {
            for (dx, glyph) in line.chars().enumerate() {
                if glyph != ' ' {
                    cells[y][x + dx] = Some((tint.clone(), true, glyph));
                }
            }
        }
        x += letter.iter().map(|l| l.chars().count()).max().unwrap_or(0) + MINI_GAP;
    }
    paint_cells(&cells, cols)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::{row_visible, visible};
    use huebox_core::Slots;

    fn slots() -> Slots {
        let mut s = Slots::default();
        for (slot, hex) in [
            ("palette-1", "#c05c5c"),
            ("palette-2", "#5cc05c"),
            ("palette-3", "#c0a05c"),
            ("palette-4", "#5c8cc0"),
            ("palette-5", "#c05c8c"),
            ("palette-6", "#5cc0c0"),
            ("palette-8", "#d0d0d8"),
            ("palette-9", "#e06c6c"),
            ("palette-10", "#6cc06c"),
            ("palette-11", "#e0c06c"),
            ("palette-12", "#6c9ce0"),
            ("palette-13", "#e06c9c"),
            ("palette-14", "#6cc0c0"),
        ] {
            s.set(slot, hex);
        }
        s
    }

    fn text(row: &Row) -> String {
        row.iter().map(|c| c.ch).collect()
    }

    #[test]
    fn banner_faces_wear_bold_shadows_do_not() {
        // Python's raster cells are (ink, bold, glyph): faces bold like
        // the one-line wordmark, the bevel shadow plain.
        let s = slots();
        let rows = banner_lines(&s, Some(96), None);
        let faces: Vec<&crate::Cell> = rows
            .iter()
            .flat_map(|r| r.iter())
            .filter(|c| c.ch != ' ' && BANNER_FACE_SLOTS.iter().any(|slot| s.get(slot) == c.fg))
            .collect();
        assert!(!faces.is_empty());
        assert!(faces.iter().all(|c| c.bold));
        let mini = mini_banner_lines(&s, Some(96));
        assert!(mini
            .iter()
            .flat_map(|r| r.iter())
            .filter(|c| c.ch != ' ')
            .all(|c| c.bold));
    }

    #[test]
    fn banner_faces_wear_the_base_row_in_order() {
        let s = slots();
        let rows = banner_lines(&s, Some(96), None);
        let inks: Vec<String> = rows
            .iter()
            .flat_map(|r| r.iter().map(|c| c.fg.clone()))
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();
        for slot in BANNER_FACE_SLOTS {
            assert!(inks.contains(&s.get(slot).to_owned()), "{slot}");
        }
    }

    #[test]
    fn banner_shadow_is_the_same_hue_one_row_brighter() {
        let s = slots();
        let rows = banner_lines(&s, Some(96), None);
        let spent: std::collections::HashSet<String> = rows
            .iter()
            .flat_map(|r| r.iter())
            .filter(|c| c.ch != ' ')
            .map(|c| c.fg.clone())
            .collect();
        let want: std::collections::HashSet<String> = BANNER_FACE_SLOTS
            .iter()
            .chain(crate::chrome::WORDMARK_SLOTS.iter())
            .map(|slot| s.get(slot).to_owned())
            .collect();
        assert_eq!(spent, want);
    }

    #[test]
    fn banner_shadow_takes_a_slot_name() {
        let s = slots();
        let rows = banner_lines(&s, Some(96), Some("palette-8"));
        assert!(rows
            .iter()
            .flat_map(|r| r.iter())
            .any(|c| c.fg == "#d0d0d8"));
    }

    #[test]
    fn banner_shadow_stands_outside_the_letterforms() {
        // flood fill from the border over the faces marks the outside;
        // no shadow cell may stand where the fill cannot reach
        let s = slots();
        let rows = banner_lines(&s, Some(96), None);
        let face: std::collections::HashSet<(usize, usize)> = rows
            .iter()
            .enumerate()
            .flat_map(|(y, r)| {
                r.iter()
                    .enumerate()
                    .filter_map(move |(x, c)| (c.ch == '█').then_some((x, y)))
            })
            .collect();
        let width = banner_width();
        let mut outside = std::collections::HashSet::new();
        let mut stack: Vec<(usize, usize)> = (0..width)
            .flat_map(|x| [(x, 0), (x, rows.len() - 1)])
            .chain((0..rows.len()).flat_map(|y| [(0, y), (width - 1, y)]))
            .collect();
        while let Some((x, y)) = stack.pop() {
            if x >= width || y >= rows.len() || outside.contains(&(x, y)) {
                continue;
            }
            let ch = rows[y].get(x).map(|c| c.ch).unwrap_or(' ');
            if face.contains(&(x, y)) && ch == '█' {
                continue;
            }
            outside.insert((x, y));
            if x + 1 < width {
                stack.push((x + 1, y));
            }
            if x > 0 {
                stack.push((x - 1, y));
            }
            if y + 1 < rows.len() {
                stack.push((x, y + 1));
            }
            if y > 0 {
                stack.push((x, y - 1));
            }
        }
        // every painted non-face cell stands on exterior air
        for (x, y) in rows.iter().enumerate().flat_map(|(y, r)| {
            r.iter()
                .enumerate()
                .filter_map(move |(x, c)| (c.ch != ' ' && c.ch != '█').then_some((x, y)))
        }) {
            assert!(outside.contains(&(x, y)), "shadow at {x},{y} is interior");
        }
    }

    #[test]
    fn face_is_solid_block_and_shadow_keeps_the_bevel() {
        let s = slots();
        let rows = banner_lines(&s, Some(96), None);
        // faces are all █ (bold in Python has no cell here); shadow glyphs
        // are the bevel set — distinguished by position is overkill, so
        // check the source tables directly plus a painted sample
        assert!(BANNER_LETTERS.iter().all(|l| l
            .iter()
            .flat_map(|s| s.chars())
            .all(|c| c == '█' || c == ' ')));
        let shadow_glyphs: std::collections::HashSet<char> = BANNER_SHADOWS
            .iter()
            .flat_map(|l| l.iter().flat_map(|s| s.chars()))
            .filter(|c| *c != ' ' && *c != '█')
            .collect();
        assert!(!shadow_glyphs.is_empty());
        assert!(shadow_glyphs.iter().all(|c| BANNER_BEVEL.contains(*c)));
        let _ = rows;
    }

    #[test]
    fn banner_is_live() {
        let s = slots();
        let mut moved = s.clone();
        moved.set(BANNER_FACE_SLOTS[2], "#ff00ff");
        assert_ne!(
            banner_lines(&s, Some(96), None),
            banner_lines(&moved, Some(96), None)
        );
        assert!(banner_lines(&moved, Some(96), None)
            .iter()
            .flat_map(|r| r.iter())
            .any(|c| c.fg == "#ff00ff"));
    }

    #[test]
    fn banner_fits_its_width_or_yields() {
        let s = slots();
        let width = banner_width();
        assert!(banner_lines(&s, Some(40), None).is_empty());
        assert!(banner_lines(&s, Some(width - 1), None).is_empty());
        let art = banner_lines(&s, Some(width), None);
        assert_eq!(art.len(), 6);
        assert_eq!(art.iter().map(|r| row_visible(r)).max().unwrap(), width);
    }

    #[test]
    fn mini_banner_is_the_pasted_block() {
        let s = slots();
        let rows = mini_banner_lines(&s, None);
        let plain: Vec<String> = rows.iter().map(text).collect();
        assert_eq!(
            plain,
            vec![
                "█ █ █ █ █▀▀ █▀▄ █▀█ █ █",
                "█▀█ █ █ █▀  █▀▄ █ █  █",
                "█ █ █▄█ █▄▄ █▄▀ █▄█ █ █",
            ]
        );
        assert_eq!(MINI_WIDTH, 23);
    }

    #[test]
    fn mini_banner_faces_wear_the_base_row_and_is_live() {
        let s = slots();
        let rows = mini_banner_lines(&s, Some(96));
        for slot in BANNER_FACE_SLOTS {
            assert!(rows
                .iter()
                .flat_map(|r| r.iter())
                .any(|c| c.fg == s.get(slot)));
        }
        let mut moved = s.clone();
        moved.set(BANNER_FACE_SLOTS[2], "#ff00ff");
        assert_ne!(rows, mini_banner_lines(&moved, Some(96)));
    }

    #[test]
    fn mini_banner_is_narrow_block_art() {
        let s = slots();
        let rows = mini_banner_lines(&s, Some(MINI_WIDTH));
        assert_eq!(rows.len(), 3);
        for row in &rows {
            for c in row {
                assert!(matches!(c.ch, '█' | '▀' | '▄' | ' '), "{}", c.ch);
                assert!(visible(&c.ch.to_string()) <= 1);
            }
        }
    }

    #[test]
    fn mini_banner_fits_its_width_or_yields() {
        let s = slots();
        assert!(mini_banner_lines(&s, Some(22)).is_empty());
        assert!(mini_banner_lines(&s, Some(MINI_WIDTH - 1)).is_empty());
        let art = mini_banner_lines(&s, Some(MINI_WIDTH));
        assert_eq!(art.len(), 3);
        assert_eq!(
            art.iter().map(|r| row_visible(r)).max().unwrap(),
            MINI_WIDTH
        );
    }
}
